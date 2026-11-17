"""Player-local observations and revisable inferences; never a source of game truth.

No default path, automatic import, game access or global-memory preload. Callers
choose a player SQLite and explicit profile. Source references are inert strings.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid

AUTHOR_ROLES = frozenset(('operator', 'tactics', 'interaction'))
LEGACY_AUTHOR_ROLE = 'legacy_unassigned'


def _author_role(value):
    """None preserves legacy callers; role ownership comes from a trusted host."""
    if value is None:
        return LEGACY_AUTHOR_ROLE
    if not isinstance(value, str) or value not in AUTHOR_ROLES:
        raise ValueError('invalid_experience_author_role')
    return value


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name + ' must be a nonempty string')
    return value


def _positive(value, name):
    if type(value) is not int or value <= 0:
        raise ValueError(name + ' must be a positive integer')
    return value


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False)


def _bytes(value):
    return len(_json(value).encode('utf-8'))


def _connect(store):
    """Borrow PanelKnowledge/connection, or own an explicitly named player path."""
    if isinstance(store, sqlite3.Connection):
        return store, False
    if isinstance(getattr(store, 'db', None), sqlite3.Connection):
        return store.db, False
    if isinstance(store, (str, Path)) and str(store):
        return sqlite3.connect(str(store)), True
    raise ValueError('explicit player SQLite path, connection or PanelKnowledge required')


def _now():
    return datetime.now(timezone.utc).isoformat()


class ExperienceStore:
    """All writes are append-only; latest revision is the default read result.

    Budgets are explicit caller choices, not silent truncation. read requires a
    selector (entry_id, keyword, or tags), and only loads the requested page.
    Confidence is the author's estimate, not API verification or a fact flag.
    """
    def __init__(self, store, *, profile_id):
        self.profile = _text(profile_id, 'profile_id')
        self.db, self._owns = _connect(store)
        # execute rather than executescript: do not implicitly commit borrowed
        # PanelKnowledge's pending transaction.
        for statement in (
            '''CREATE TABLE IF NOT EXISTS experience_entry_v1(
               seq INTEGER PRIMARY KEY, profile TEXT NOT NULL,
               entry_id TEXT NOT NULL, root_id TEXT NOT NULL,
               parent_id TEXT, kind TEXT NOT NULL, relation TEXT NOT NULL,
               content TEXT NOT NULL, payload TEXT NOT NULL,
               payload_bytes INTEGER NOT NULL, UNIQUE(profile,entry_id))''',
            '''CREATE INDEX IF NOT EXISTS experience_root_v1
               ON experience_entry_v1(profile,root_id,seq)''',
            '''CREATE TABLE IF NOT EXISTS experience_tag_v1(
               profile TEXT NOT NULL, entry_id TEXT NOT NULL, tag TEXT NOT NULL,
               PRIMARY KEY(profile,entry_id,tag))''',
            '''CREATE INDEX IF NOT EXISTS experience_tag_lookup_v1
               ON experience_tag_v1(profile,tag,entry_id)''',
        ):
            self.db.execute(statement)
        columns = {row[1] for row in self.db.execute('PRAGMA table_info(experience_entry_v1)')}
        if 'author_role' not in columns:
            # Retain old payload bytes and revision history; never infer an owner
            # from tags, content or provenance supplied by a model.
            self.db.execute("ALTER TABLE experience_entry_v1 ADD COLUMN author_role "
                            "TEXT NOT NULL DEFAULT 'legacy_unassigned'")
        self.db.execute('CREATE INDEX IF NOT EXISTS experience_role_v1 '
                        'ON experience_entry_v1(profile,author_role,seq)')

    def close(self):
        if self._owns:
            self.db.close()

    def record(self, *, kind, content, applicability, confidence, provenance,
               run_ref, tags, text_budget_bytes, author_role=None):
        return self._append(kind=kind, content=content, applicability=applicability,
            confidence=confidence, provenance=provenance, run_ref=run_ref,
            tags=tags, text_budget_bytes=text_budget_bytes,
            parent_id=None, relation='initial', author_role=author_role)

    def revise(self, entry_id, *, kind, content, applicability, confidence,
               provenance, run_ref, tags, text_budget_bytes, author_role=None):
        return self._append(kind=kind, content=content, applicability=applicability,
            confidence=confidence, provenance=provenance, run_ref=run_ref,
            tags=tags, text_budget_bytes=text_budget_bytes,
            parent_id=_text(entry_id, 'entry_id'), relation='revision', author_role=author_role)

    def refute(self, entry_id, *, content, applicability, confidence, provenance,
               run_ref, tags, text_budget_bytes, author_role=None):
        """Append the counterexample and a refuted status; retain old evidence."""
        return self._append(kind='observation', content=content,
            applicability=applicability, confidence=confidence,
            provenance=provenance, run_ref=run_ref, tags=tags,
            text_budget_bytes=text_budget_bytes,
            parent_id=_text(entry_id, 'entry_id'), relation='counterexample', author_role=author_role)

    def _append(self, *, kind, content, applicability, confidence, provenance,
                run_ref, tags, text_budget_bytes, parent_id, relation, author_role):
        owner = _author_role(author_role)
        _positive(text_budget_bytes, 'text_budget_bytes')
        if kind not in ('observation', 'inference'):
            raise ValueError('kind must be observation or inference')
        _text(content, 'content'); _text(applicability, 'applicability')
        _text(run_ref, 'run_ref')
        if type(confidence) not in (float, int) or not 0 <= confidence <= 1:
            raise ValueError('confidence must be finite and between 0 and 1')
        if not isinstance(provenance, list) or not provenance:
            raise ValueError('provenance must be a nonempty list of source references')
        for ref in provenance:
            _text(ref, 'provenance reference')
        if not isinstance(tags, list):
            raise ValueError('tags must be a list')
        tags = sorted(set(_text(t, 'tag').casefold() for t in tags))
        entry_id = uuid.uuid4().hex
        # Savepoint preserves the caller's transaction on rejection/failure.
        self.db.execute('SAVEPOINT experience_append')
        released = False
        try:
            root = entry_id
            if parent_id is not None:
                parent = self.db.execute('''SELECT root_id,author_role FROM experience_entry_v1
                    WHERE profile=? AND entry_id=?''',
                    (self.profile, parent_id)).fetchone()
                if parent is None:
                    raise ValueError('parent entry not found in this profile')
                if parent[1] != owner:
                    raise ValueError('experience_author_role_mismatch')
                root = parent[0]
                latest = self.db.execute('''SELECT entry_id FROM experience_entry_v1
                    WHERE profile=? AND root_id=? ORDER BY seq DESC LIMIT 1''',
                    (self.profile, root)).fetchone()[0]
                if latest != parent_id:
                    raise ValueError('stale revision: revise the current latest entry')
            payload = dict(entry_id=entry_id, root_id=root, parent_id=parent_id,
                kind=kind, relation=relation, content=content,
                applicability=applicability, confidence=float(confidence),
                provenance=list(provenance), run_ref=run_ref, tags=tags,
                recorded_at_utc=_now(),
                status='refuted' if relation == 'counterexample' else 'recorded',
                epistemic_status='player_authored_not_api_verified')
            if author_role is not None:
                payload['author_role'] = owner
            size = _bytes(payload)
            if size > text_budget_bytes:
                self.db.execute('ROLLBACK TO experience_append')
                self.db.execute('RELEASE experience_append')
                released = True
                return dict(status='budget_required', required_text_budget_bytes=size,
                            text_budget_bytes=text_budget_bytes, written=False)
            encoded = _json(payload)
            self.db.execute('''INSERT INTO experience_entry_v1
                (profile,entry_id,root_id,parent_id,kind,relation,content,payload,payload_bytes,author_role)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (self.profile, entry_id, root, parent_id, kind, relation,
                 content, encoded, size, owner))
            self.db.executemany('INSERT INTO experience_tag_v1 VALUES(?,?,?)',
                [(self.profile, entry_id, tag) for tag in tags])
            self.db.execute('RELEASE experience_append')
            released = True
            self.db.commit()
            return dict(status='recorded', entry_id=entry_id, root_id=root,
                        relation=relation, written=True, text_bytes=size)
        except BaseException:
            if not released:
                self.db.execute('ROLLBACK TO experience_append')
                self.db.execute('RELEASE experience_append')
            raise

    def read(self, *, limit, text_budget_bytes, entry_id=None, keyword=None,
             tags=None, cursor=None, history=False, author_role=None):
        owner = _author_role(author_role)
        _positive(limit, 'limit'); _positive(text_budget_bytes, 'text_budget_bytes')
        if type(history) is not bool:
            raise ValueError('history must be bool')
        if cursor is None:
            cursor = 0
        if type(cursor) is not int or cursor < 0:
            raise ValueError('cursor must be a nonnegative sequence')
        if entry_id is None and keyword is None and not tags and author_role is None:
            raise ValueError('select entry_id, keyword or tags; no automatic full preload')
        where = ['e.profile=?', 'e.seq>?']; args = [self.profile, cursor]
        if author_role is not None:
            where.append('e.author_role=?'); args.append(owner)
        if entry_id is not None:
            # An old revision ID selects its root, so current reads cannot quietly
            # return a superseded claim. history=True exposes the complete chain.
            _text(entry_id, 'entry_id')
            where.append('''e.root_id=(SELECT root_id FROM experience_entry_v1
                WHERE profile=? AND entry_id=?)''')
            args += [self.profile, entry_id]
        if not history:
            where.append('''NOT EXISTS(SELECT 1 FROM experience_entry_v1 n
                WHERE n.profile=e.profile AND n.root_id=e.root_id AND n.seq>e.seq)''')
        if keyword is not None:
            _text(keyword, 'keyword')
            where.append('instr(lower(e.content),lower(?))>0'); args.append(keyword)
        if tags is not None:
            if not isinstance(tags, list) or not tags:
                raise ValueError('tags must be a nonempty list when supplied')
            for tag in sorted(set(_text(t, 'tag').casefold() for t in tags)):
                where.append('''EXISTS(SELECT 1 FROM experience_tag_v1 t
                    WHERE t.profile=e.profile AND t.entry_id=e.entry_id AND t.tag=?)''')
                args.append(tag)
        clause = ' AND '.join(where)
        # Check stored byte lengths before materializing potentially large text.
        rows = self.db.execute('SELECT e.seq,e.payload_bytes,length(CAST(e.payload AS BLOB)) FROM experience_entry_v1 e WHERE '
                               + clause + ' ORDER BY e.seq LIMIT ?', args + [limit + 1]).fetchall()
        page, more = rows[:limit], len(rows) > limit
        for seq, expected_size, actual_size in page:
            if type(expected_size) is not int or expected_size < 2 or expected_size != actual_size:
                return dict(status='unavailable', reason='experience_data_corrupt',
                            corrupt_sequence=seq, entries=[], next_cursor=cursor,
                            has_more=True)
        # UTF-8 encoded compact JSON array, including [] and commas.
        required = 2 + sum(r[1] for r in page) + max(0, len(page) - 1)
        next_cursor = page[-1][0] if page else cursor
        if required > text_budget_bytes:
            return dict(status='budget_required', required_text_budget_bytes=required,
                        text_budget_bytes=text_budget_bytes, entries=[],
                        next_cursor=cursor, has_more=bool(rows))
        entries = []
        for seq, expected_size, actual_size in page:
            raw, entry_id, root_id, parent_id, kind, relation, content, stored_role = self.db.execute(
                '''SELECT payload,entry_id,root_id,parent_id,kind,relation,content,author_role
                   FROM experience_entry_v1 WHERE seq=?''', (seq,)).fetchone()
            try:
                item = json.loads(raw)
                if (not isinstance(item, dict) or _bytes(item) != expected_size
                        or item.get('kind') not in ('observation', 'inference')
                        or item.get('entry_id') != entry_id or item.get('root_id') != root_id
                        or item.get('parent_id') != parent_id or item.get('kind') != kind
                        or item.get('relation') != relation or item.get('content') != content
                        or item.get('author_role', LEGACY_AUTHOR_ROLE) != stored_role
                        or stored_role not in AUTHOR_ROLES | {LEGACY_AUTHOR_ROLE}
                        or item.get('epistemic_status') != 'player_authored_not_api_verified'
                        or type(item.get('confidence')) not in (int, float)
                        or not 0 <= item['confidence'] <= 1
                        or not isinstance(item.get('provenance'), list)
                        or not item['provenance'] or not isinstance(item.get('tags'), list)):
                    raise ValueError('invalid stored entry')
                for field in ('entry_id','root_id','content','applicability','run_ref','recorded_at_utc'):
                    _text(item.get(field), field)
                for value in item['provenance'] + item['tags']:
                    _text(value, 'stored reference or tag')
                entries.append(item)
            except (ValueError, TypeError, UnicodeError):
                return dict(status='unavailable', reason='experience_data_corrupt',
                            corrupt_sequence=seq, entries=[], next_cursor=cursor,
                            has_more=True)
        return dict(status='ok', entries=entries, next_cursor=next_cursor,
                    has_more=more, text_bytes=required)

    def list(self, *, author_role, limit=32, cursor=None, text_budget_bytes=65536):
        """Host-owned GUI page of this role's latest claims, in the same SQLite."""
        if author_role is None:
            raise ValueError('invalid_experience_author_role')
        _author_role(author_role)
        if type(limit) is not int or not 1 <= limit <= 64:
            raise ValueError('role experience limit must be 1..64')
        return self.read(author_role=author_role, limit=limit, cursor=cursor,
                         text_budget_bytes=text_budget_bytes)
