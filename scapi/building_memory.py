"""Durable, passive last-observed building facts for one verified game session.

Only the worker writes SQLite or consumes the source. Queries use a separate
read-only connection on their calling thread and never read the game. The
indexed insertion ordinal preserves existing offset cursors without retaining
all history in memory. Source acknowledgments are EXCLUSIVE:
ack(2) means sequence 1 is durable. Nothing expires, evicts, or infers death.
"""
import hashlib
import hmac
import json
import math
from pathlib import Path
import secrets
import sqlite3
import threading
import time


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), sort_keys=True)


def _integer(value, label, maximum=0x7FFFFFFFFFFFFFFF, minimum=0):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'invalid {label}')
    return value


class BuildingMemory:
    """One source consumer; the adapter owns its exclusive native lease.

    read_batch() may return None during a consistent-read retry. Sequence gaps,
    conflicting replays, epoch changes and malformed data halt acknowledgments.
    Transient SQLite failures retry without dropping the source backlog. Public
    results expose only historical facts, opaque tokens and collection status.
    """

    def __init__(self, source, path, session_key, *, poll_interval=.05):
        if type(session_key) is not str or not session_key:
            raise ValueError('verified nonempty session_key required')
        session_key.encode('utf-8')
        if not isinstance(poll_interval, (float, int)) or not math.isfinite(poll_interval) or poll_interval <= 0:
            raise ValueError('positive finite poll_interval required')
        self.source, self.path, self.session_key = source, str(path), session_key
        self.poll_interval = float(poll_interval)
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._ready = threading.Event()
        self._total = 0
        self._salt = None
        self._epoch = None
        self._next = 1
        self._write = 0
        self._ack = 1
        self._enabled = False
        self._fault = 0
        self._storage_error = None
        self._read_error = None
        self._source_error = None
        self._fatal = False
        self._closed = False
        self._cycle = 0
        self._last_logged = None
        # Keep named in-memory history alive after the worker closes, just as
        # the previous cache did. This connection is only created here, never
        # operated from another thread; its lifetime is the store's lifetime.
        self._memory_uri = (f'file:scapi-building-{secrets.token_hex(16)}?mode=memory&cache=shared'
                            if self.path == ':memory:' else None)
        self._memory_keeper = (sqlite3.connect(self._memory_uri, uri=True)
                               if self._memory_uri else None)
        self._thread = threading.Thread(target=self._run, name='scapi-building-memory', daemon=True)
        self._thread.start()
        self._ready.wait(2.0)

    def _report_error(self, category, exc, *, fatal=False):
        message = f'{type(exc).__name__}: {exc}'
        with self._condition:
            if category == 'read':
                self._read_error = message
            elif category == 'storage':
                self._storage_error = message
            else:
                self._source_error = message
            self._fatal |= fatal
            self._condition.notify_all()
        # A failed diagnostic write never clears the original visible error.
        diagnostic = (category, message)
        if diagnostic != self._last_logged and self.path != ':memory:':
            try:
                with open(self.path + '.errors.jsonl', 'a', encoding='utf-8') as stream:
                    stream.write(_json({'time_unix': time.time(), 'category': category,
                                        'error': message, 'fatal': fatal}) + '\n')
                self._last_logged = diagnostic
            except OSError as log_exc:
                with self._lock:
                    self._storage_error = (self._storage_error or message) + f'; diagnostic log: {log_exc}'

    def _open(self):
        if self.path != ':memory:':
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self._memory_uri or self.path, uri=bool(self._memory_uri),
                             timeout=.1, isolation_level=None)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('PRAGMA synchronous=FULL')
            db.executescript('''
              CREATE TABLE IF NOT EXISTS building_session_v1(
                session TEXT PRIMARY KEY, salt TEXT NOT NULL,
                epoch INTEGER, next_sequence INTEGER NOT NULL DEFAULT 1);
              CREATE TABLE IF NOT EXISTS building_event_v1(
                session TEXT NOT NULL, sequence INTEGER NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(session,sequence));
              CREATE TABLE IF NOT EXISTS building_current_v1(
                session TEXT NOT NULL, identity TEXT NOT NULL, first_sequence INTEGER NOT NULL,
                last_sequence INTEGER NOT NULL, payload TEXT NOT NULL,
                PRIMARY KEY(session,identity));
            ''')
            db.execute('INSERT OR IGNORE INTO building_session_v1(session,salt) VALUES(?,?)',
                       (self.session_key, secrets.token_hex(24)))
            salt, epoch, next_sequence = db.execute(
                'SELECT salt,epoch,next_sequence FROM building_session_v1 WHERE session=?',
                (self.session_key,)).fetchone()
            # Preserve offset cursors while allowing deep pages to seek directly.
            # Migration is entirely inside SQLite and commits atomically; ordinary
            # opens never fetch or decode historical payloads.
            db.execute('BEGIN IMMEDIATE')
            columns = {row[1] for row in db.execute('PRAGMA table_info(building_current_v1)')}
            if 'ordinal' not in columns:
                db.execute('ALTER TABLE building_current_v1 ADD COLUMN ordinal INTEGER')
                db.execute('''WITH ranked AS (
                    SELECT rowid AS rid, ROW_NUMBER() OVER (
                        PARTITION BY session ORDER BY first_sequence) - 1 AS position
                    FROM building_current_v1)
                    UPDATE building_current_v1 SET ordinal=(
                        SELECT position FROM ranked WHERE ranked.rid=building_current_v1.rowid)''')
            db.execute('''CREATE UNIQUE INDEX IF NOT EXISTS building_current_order_v1
                          ON building_current_v1(session,ordinal)''')
            db.execute('''CREATE INDEX IF NOT EXISTS building_current_changes_v1
                          ON building_current_v1(session,last_sequence)''')
            db.execute('''CREATE INDEX IF NOT EXISTS building_current_kind_v1
                          ON building_current_v1(session,json_extract(payload,'$.kind'),first_sequence)''')
            db.execute('COMMIT')
            total = db.execute('SELECT COALESCE(MAX(ordinal)+1,0) FROM building_current_v1 '
                               'WHERE session=?', (self.session_key,)).fetchone()[0]
            with self._lock:
                self._salt, self._epoch, self._next = salt, epoch, next_sequence
                self._total = total
                self._storage_error = None
            return db
        except BaseException:
            db.close()
            raise

    def _validate_batch(self, batch):
        if type(batch) is not dict:
            raise ValueError('source batch must be a dictionary')
        epoch = _integer(batch['epoch'], 'epoch')
        write = _integer(batch['write_seq'], 'write_seq')
        ack = _integer(batch['ack_seq'], 'ack_seq', minimum=1)
        if ack > write + 1:
            raise ValueError('source ack exceeds write sequence')
        if type(batch['enabled']) is not bool:
            raise ValueError('invalid enabled state')
        fault = _integer(batch['fault'], 'fault', 0xFFFFFFFF)
        records = batch['records']
        if type(records) is not list:
            raise ValueError('source records must be a list')
        with self._lock:
            self._write, self._ack = write, ack
            self._enabled, self._fault = batch['enabled'], fault
            epoch_before, next_sequence = self._epoch, self._next
        if epoch_before is not None and epoch_before != epoch:
            raise ValueError('session epoch changed; a new verified session_key is required')
        if ack > next_sequence:
            raise ValueError('source acknowledgment is ahead of durable history')
        clean = []
        previous = 0
        for record in records:
            if type(record) is not dict:
                raise ValueError('invalid building event')
            value = {field: _integer(record[field], field, maximum) for field, maximum in (
                ('sequence', 0x7FFFFFFFFFFFFFFE), ('identity', 0xFFFFFFFFFFFFFFFF),
                ('kind', 0xFFFF), ('owner', 0xFF), ('visual', 0xFFFFFFFF),
                ('x', 0xFFFFFFFF), ('y', 0xFFFFFFFF), ('last_seen_tick_ms', 0xFFFFFFFFFFFFFFFF))}
            if type(record['currently_observed']) is not bool:
                raise ValueError('invalid currently_observed')
            value['currently_observed'] = record['currently_observed']
            sequence = value['sequence']
            if sequence < 1 or sequence <= previous or sequence > write:
                raise ValueError('invalid event sequence order or bounds')
            previous = sequence
            clean.append(value)
        return epoch, clean

    def _persist(self, db, epoch, records):
        with self._lock:
            next_sequence = self._next
            total = self._total
            if not records and self._epoch == epoch:
                return next_sequence
        db.execute('BEGIN IMMEDIATE')
        try:
            for record in records:
                seq = record['sequence']
                payload = _json(record)
                if seq < next_sequence:
                    old = db.execute('SELECT payload FROM building_event_v1 WHERE session=? AND sequence=?',
                                     (self.session_key, seq)).fetchone()
                    if old is None or old[0] != payload:
                        raise ValueError('conflicting sequence replay')
                    continue
                if seq != next_sequence:
                    raise ValueError('source sequence gap; history cannot be silently skipped')
                identity = str(record['identity'])
                if not record['currently_observed']:
                    previous = db.execute('SELECT payload FROM building_current_v1 '
                                          'WHERE session=? AND identity=?',
                                          (self.session_key, identity)).fetchone()
                    old = json.loads(previous[0]) if previous is not None else None
                    fields = ('kind', 'owner', 'visual', 'x', 'y', 'last_seen_tick_ms')
                    if old is None or any(old[field] != record[field] for field in fields):
                        raise ValueError('unobserved event may only change observation state')
                db.execute('INSERT INTO building_event_v1 VALUES(?,?,?)',
                           (self.session_key, seq, payload))
                # Allocate an ordinal only for a newly observed identity. Updates
                # and replays preserve its original insertion position.
                inserted = db.execute('''INSERT OR IGNORE INTO building_current_v1
                    (session,identity,first_sequence,last_sequence,payload,ordinal)
                    VALUES(?,?,?,?,?,?)''',
                    (self.session_key, identity, seq, seq, payload, total)).rowcount
                if inserted:
                    total += 1
                else:
                    db.execute('UPDATE building_current_v1 SET last_sequence=?,payload=? '
                               'WHERE session=? AND identity=?',
                               (seq, payload, self.session_key, identity))
                next_sequence += 1
            db.execute('UPDATE building_session_v1 SET epoch=?,next_sequence=? WHERE session=?',
                       (epoch, next_sequence, self.session_key))
            db.execute('COMMIT')
        except BaseException:
            if db.in_transaction:
                db.execute('ROLLBACK')
            raise
        with self._condition:
            self._total = total
            self._next, self._epoch = next_sequence, epoch
            self._storage_error = None
            self._condition.notify_all()
        return next_sequence

    def _run(self):
        db = None
        try:
            while True:
                if self._stop.is_set():
                    break
                phase = 'storage'
                try:
                    if db is None:
                        db = self._open()
                        self._ready.set()
                    with self._lock:
                        fatal = self._fatal
                    if not fatal:
                        phase = 'source'
                        batch = self.source.read_batch()
                        if batch is not None:
                            epoch, records = self._validate_batch(batch)
                            phase = 'storage'
                            next_sequence = self._persist(db, epoch, records)
                            # Committed disk and metadata precede a source ack.
                            phase = 'source'
                            if batch['ack_seq'] < next_sequence:
                                self.source.ack(next_sequence)
                                with self._lock:
                                    self._ack = next_sequence
                            with self._lock:
                                self._source_error = None
                                self._cycle += 1
                except sqlite3.Error as exc:
                    self._report_error('storage', exc)
                    self._ready.set()
                except OSError as exc:
                    self._report_error(phase, exc)
                    self._ready.set()
                except (ValueError, KeyError, TypeError) as exc:
                    self._report_error('source', exc, fatal=True)
                    self._ready.set()
                except Exception as exc:
                    self._report_error('source', exc)
                    self._ready.set()
                finally:
                    with self._condition:
                        self._condition.notify_all()
                if self._stop.is_set():
                    break
                self._wake.wait(self.poll_interval)
                self._wake.clear()
        finally:
            try:
                if db is not None:
                    db.close()
            except sqlite3.Error as exc:
                self._report_error('storage', exc)
            with self._condition:
                self._closed = True
                self._condition.notify_all()

    def status(self):
        with self._lock:
            return {'pending': max(0, self._write - self._ack + 1), 'fault': self._fault,
                    'storage_error': self._storage_error or self._read_error,
                    'source_error': self._source_error,
                    'enabled': self._enabled, 'ready': self._salt is not None,
                    'closed': self._closed, 'fatal': self._fatal,
                    'persisted_next_sequence': self._next,
                    'ack_next_sequence': self._ack, 'write_sequence': self._write}

    def _cursor(self, offset):
        prefix = f'{offset:x}'
        signature = hmac.new(self._salt.encode(), ('cursor:' + prefix).encode(), hashlib.sha256).hexdigest()[:24]
        return prefix + '.' + signature

    def _offset(self, cursor, total=None):
        if cursor is None:
            return 0
        if type(cursor) is not str or len(cursor) > 100:
            raise ValueError('invalid cursor')
        try:
            prefix, _ = cursor.split('.')
            offset = int(prefix, 16)
        except (ValueError, TypeError):
            raise ValueError('invalid cursor') from None
        if total is None:
            total = self._total
        if offset < 0 or offset > total or not hmac.compare_digest(cursor, self._cursor(offset)):
            raise ValueError('invalid or foreign cursor')
        return offset

    def query(self, *, limit, text_budget_bytes, cursor=None, name_for_kind=None):
        """Bound the complete UTF-8 JSON response; never truncate a row or name.

        Names are resolved by the caller thread through its existing legitimate
        knowledge source. Pagination is stable insertion order, with latest facts
        at query time; appends and reobservations do not invalidate a cursor.
        """
        _integer(limit, 'positive limit', minimum=1)
        _integer(text_budget_bytes, 'positive text_budget_bytes', minimum=1)
        with self._lock:
            if self._salt is None:
                return {'status': 'rejected', 'reason': 'building memory is not ready',
                        'buildings': [], 'next_cursor': cursor, 'memory_status': self.status()}
            salt = self._salt
        db = None
        try:
            uri = self._memory_uri or (Path(self.path).resolve().as_uri() + '?mode=ro')
            db = sqlite3.connect(uri, uri=True, timeout=0, isolation_level=None)
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            total = db.execute('SELECT COALESCE(MAX(ordinal)+1,0) FROM building_current_v1 '
                               'WHERE session=?', (self.session_key,)).fetchone()[0]
            offset = self._offset(cursor, total)
            rows = db.execute('SELECT identity,payload FROM building_current_v1 '
                              'WHERE session=? AND ordinal>=? ORDER BY ordinal LIMIT ?',
                              (self.session_key, offset, limit))
            candidates = [(identity, json.loads(payload)) for identity, payload in rows]
            db.execute('COMMIT')
            db.close()
            db = None
        except (sqlite3.Error, OSError, json.JSONDecodeError) as exc:
            self._report_error('read', exc)
            return {'status': 'rejected', 'reason': 'building memory storage unavailable',
                    'buildings': [], 'next_cursor': cursor, 'memory_status': self.status()}
        except ValueError as exc:
            return {'status': 'rejected', 'reason': str(exc), 'buildings': [],
                    'next_cursor': cursor, 'memory_status': self.status()}
        finally:
            if db is not None:
                db.close()
        with self._lock:
            self._read_error = None
            memory_status = self.status()
        result = {'status': 'observed', 'buildings': [], 'total_records': total,
                  'next_cursor': None,
                  'memory_status': memory_status}
        # Count each row once. Re-encoding the accumulated page for every row
        # would make a caller-requested large page quadratic in size.
        base_bytes = len(_json(result).encode('utf-8'))
        rows_bytes = 0
        names = {}
        result['next_cursor'] = self._cursor(offset) if offset < total else None
        for identity, record in candidates:
            token = hmac.new(salt.encode(), ('record:' + identity).encode(), hashlib.sha256).hexdigest()[:32]
            row = {'record_token': 'building:' + token,
                   'appearance_token': f"sprite:{record['visual']}",
                   'last_seen_position': {'x': record['x'], 'y': record['y']},
                   'last_seen_tick_ms': record['last_seen_tick_ms'],
                   'currently_observed': record['currently_observed']}
            if name_for_kind is not None:
                kind = record['kind']
                if kind not in names:
                    names[kind] = name_for_kind(kind)
                name = names[kind]
                if name is not None:
                    if type(name) is not str:
                        raise ValueError('known building name must be a string or None')
                    row['name'] = name
            result['buildings'].append(row)
            next_offset = offset + len(result['buildings'])
            result['next_cursor'] = self._cursor(next_offset) if next_offset < total else None
            row_bytes = len(_json(row).encode('utf-8'))
            cursor_bytes = len(_json(result['next_cursor']).encode('utf-8')) - 4
            required = base_bytes + rows_bytes + row_bytes + len(result['buildings']) - 1 + cursor_bytes
            if required > text_budget_bytes:
                result['buildings'].pop()
                result['next_cursor'] = self._cursor(next_offset - 1)
                if not result['buildings']:
                    result.update(status='larger_text_budget_required', required_bytes=required,
                                  text_budget_bytes=text_budget_bytes)
                break
            rows_bytes += row_bytes
        if not candidates:
            required = len(_json(result).encode('utf-8'))
            if required > text_budget_bytes:
                result.update(status='larger_text_budget_required', required_bytes=required,
                              text_budget_bytes=text_budget_bytes)
        return result

    def query_changes(self, *, after_sequence, limit, text_budget_bytes, name_for_kind=None):
        """Host-only incremental current facts; no event log or native reads.

        A checkpoint means included current rows, not deletion or survival.
        Updates can repeat a token across pages; consumers upsert by token.
        Failed reads/budgets leave the checkpoint unchanged. Original public
        offset cursors and full historical query remain independent.
        """
        return self._query_updates(after_sequence=after_sequence,limit=limit,
                                   text_budget_bytes=text_budget_bytes,name_for_kind=name_for_kind)

    def query_kind(self, *, kind, after_sequence=0, limit, text_budget_bytes, name_for_kind=None):
        """Host-only selective name enrichment, ordered by first observation.

        Here checkpoint is the stable first_sequence, not the change sequence.
        A newly learned kind name can enrich its old hidden records without
        scanning unrelated kinds or retaining historical pages in the host.
        """
        _integer(kind, 'kind', maximum=0xFFFF)
        return self._query_updates(after_sequence=after_sequence,limit=limit,
                                   text_budget_bytes=text_budget_bytes,
                                   name_for_kind=name_for_kind,kind=kind)

    def _query_updates(self, *, after_sequence, limit, text_budget_bytes, name_for_kind, kind=None):
        _integer(after_sequence, 'after_sequence')
        _integer(limit, 'positive limit', minimum=1)
        _integer(text_budget_bytes, 'positive text_budget_bytes', minimum=1)
        with self._lock:
            salt = self._salt
        def unavailable(reason):
            return {'status':'rejected', 'reason':reason, 'buildings':[],
                    'checkpoint':after_sequence, 'has_more':False, 'memory_status':self.status()}
        if salt is None:
            return unavailable('building memory is not ready')
        db = None
        try:
            uri = self._memory_uri or (Path(self.path).resolve().as_uri() + '?mode=ro')
            db = sqlite3.connect(uri, uri=True, timeout=0, isolation_level=None)
            db.execute('PRAGMA query_only=ON')
            db.execute('BEGIN')
            row = db.execute('SELECT next_sequence FROM building_session_v1 WHERE session=?',
                             (self.session_key,)).fetchone()
            if row is None:
                return unavailable('building session missing')
            watermark = row[0] - 1
            if after_sequence > watermark:
                return unavailable('invalid or foreign building checkpoint')
            # These SQL fragments are fixed internal alternatives, never public
            # identifiers. Both have matching indexes, including the kind test.
            column = 'last_sequence' if kind is None else 'first_sequence'
            clause = 'session=?' if kind is None else "session=? AND json_extract(payload,'$.kind')=?"
            parameters = (self.session_key,) if kind is None else (self.session_key,kind)
            rows = db.execute(f'SELECT {column},identity,payload FROM building_current_v1 '
                              f'WHERE {clause} AND {column}>? ORDER BY {column} LIMIT ?',
                              parameters + (after_sequence, limit))
            candidates = [(sequence, identity, json.loads(payload)) for sequence,identity,payload in rows]
            page_end = candidates[-1][0] if candidates else watermark
            more = db.execute(f'SELECT 1 FROM building_current_v1 WHERE {clause} AND {column}>? LIMIT 1',
                              parameters + (page_end,)).fetchone() is not None
            db.execute('COMMIT')
            db.close()
            db = None
        except (sqlite3.Error, OSError, json.JSONDecodeError) as exc:
            self._report_error('read', exc)
            return unavailable('building memory storage unavailable')
        finally:
            if db is not None:
                db.close()
        with self._lock:
            self._read_error = None
            memory_status = self.status()
        result = {'status':'observed', 'buildings':[], 'checkpoint':after_sequence,
                  'has_more':True, 'memory_status':memory_status}
        base_bytes = len(_json(result).encode('utf-8'))
        rows_bytes = 0
        names = {}
        for sequence,identity,record in candidates:
            token = hmac.new(salt.encode(), ('record:' + identity).encode(), hashlib.sha256).hexdigest()[:32]
            item = {'record_token':'building:' + token,
                    'appearance_token':f"sprite:{record['visual']}",
                    'last_seen_position':{'x':record['x'],'y':record['y']},
                    'last_seen_tick_ms':record['last_seen_tick_ms'],
                    'currently_observed':record['currently_observed']}
            if name_for_kind is not None:
                kind = record['kind']
                if kind not in names:
                    names[kind] = name_for_kind(kind)
                name = names[kind]
                if name is not None:
                    if type(name) is not str:
                        raise ValueError('known building name must be a string or None')
                    item['name'] = name
            row_bytes = len(_json(item).encode('utf-8'))
            has_more = sequence != page_end or more
            required = (base_bytes + rows_bytes + row_bytes + len(result['buildings'])
                        + len(str(sequence)) - len(str(after_sequence)) + (0 if has_more else 1))
            if required > text_budget_bytes:
                if not result['buildings']:
                    result.update(status='larger_text_budget_required', required_bytes=required,
                                  text_budget_bytes=text_budget_bytes)
                break
            result['buildings'].append(item)
            result['checkpoint'] = sequence
            result['has_more'] = has_more
            rows_bytes += row_bytes
        if not candidates:
            result.update(checkpoint=watermark,has_more=False)
            required = len(_json(result).encode('utf-8'))
            if required > text_budget_bytes:
                result.update(status='larger_text_budget_required',checkpoint=after_sequence,
                              required_bytes=required,text_budget_bytes=text_budget_bytes)
        return result

    def flush(self, timeout=2.0):
        """Wait for a fresh source poll and durable ack; return explicit status."""
        if not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout < 0:
            raise ValueError('finite nonnegative timeout required')
        deadline = time.monotonic() + timeout
        with self._condition:
            cycle = self._cycle
            self._wake.set()
            while True:
                status = self.status()
                complete = (self._cycle > cycle and status['ready'] and status['pending'] == 0
                            and not status['fault'] and not status['storage_error'] and not status['source_error'])
                if complete or status['closed'] or status['fatal']:
                    return dict(status, flushed=complete)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return dict(status, flushed=False)
                self._condition.wait(remaining)

    def close(self, timeout=2.0):
        """Bounded drain, then stop; failures remain visible in the return value.

        Source ownership and close/release belong to the adapter, never this store.
        A source must provide bounded read_batch/ack calls for bounded shutdown.
        """
        started = time.monotonic()
        with self._lock:
            if self._closed:
                status = self.status()
                return dict(status, flushed=(not status['pending'] and not status['storage_error']
                                            and not status['fault'] and not status['source_error']))
        # Keep part of the same deadline for worker/SQLite shutdown even when a
        # persistent fault prevents drain completion. No extra source poll starts
        # after stop is requested.
        result = self.flush(timeout * .75)
        self._stop.set()
        self._wake.set()
        self._thread.join(max(0, timeout - (time.monotonic() - started)))
        return dict(self.status(), flushed=result['flushed'])

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
