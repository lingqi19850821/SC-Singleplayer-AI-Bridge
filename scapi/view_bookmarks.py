"""Three controller-owned camera bookmarks, isolated by verified live session.

The caller supplies an already verified process/creation/bridge/epoch key and
coordinates sampled by the native save-view action. This module never operates
the game, expires a bookmark, or exposes its coordinates in a public response.
Text budgets count UTF-8 payload bytes, not JSON framing. No text is truncated.
"""
from contextlib import contextmanager
import sqlite3


SLOTS = (2, 3, 4)


def _slot(slot):
    if type(slot) is not int or slot not in SLOTS:
        raise ValueError('view slot must be integer 2, 3 or 4 (F2/F3/F4)')


def _budget(value):
    if type(value) is not int or value < 1:
        raise ValueError('explicit positive integer text_budget_bytes required')


def _text_bytes(value):
    if type(value) is not str:
        raise ValueError('bookmark label and note must be strings')
    # Count even a rejected large caller value without making a second full copy.
    try:
        return sum(len(value[i:i + 4096].encode('utf-8'))
                   for i in range(0, len(value), 4096))
    except UnicodeEncodeError as exc:
        raise ValueError('bookmark text must be valid UTF-8') from exc


def _required(required, budget):
    if required > budget:
        return {'status': 'text_budget_required', 'required_bytes': required,
                'text_budget_bytes': budget}
    return None


def validate_save(slot, label, note, *, text_budget_bytes):
    """Validate metadata before the caller spends a native action allowance."""
    _slot(slot)
    _budget(text_budget_bytes)
    required = _text_bytes(label) + _text_bytes(note)
    return (_required(required, text_budget_bytes) or
            {'status': 'ready', 'required_bytes': required})


class BookmarkStore:
    """Durable slots for one verified session; storage errors remain visible.

    Same-session reopen preserves slots. A different key sees empty slots and
    cannot overwrite the old session. Save/load identity recovery is deliberately
    the caller's responsibility; map dimensions alone are not a session key.
    """

    def __init__(self, path, session_key):
        if type(session_key) is not str or not session_key:
            raise ValueError('verified nonempty session_key string required')
        _text_bytes(session_key)
        self.session_key = session_key
        self.db = sqlite3.connect(path, timeout=5.0, isolation_level=None)
        try:
            self.db.execute('PRAGMA journal_mode=WAL')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('''CREATE TABLE IF NOT EXISTS stage5_view_bookmark_v1(
                session_key TEXT NOT NULL,
                slot INTEGER NOT NULL CHECK(slot IN (2,3,4)),
                x INTEGER NOT NULL CHECK(x >= 0),
                y INTEGER NOT NULL CHECK(y >= 0),
                label TEXT NOT NULL,
                note TEXT NOT NULL,
                PRIMARY KEY(session_key, slot))''')
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    @contextmanager
    def _transaction(self, *, write=False):
        # Explicit read transactions keep length checks and subsequent text
        # reads on one snapshot if another controller reconnects concurrently.
        self.db.execute('BEGIN IMMEDIATE' if write else 'BEGIN')
        try:
            yield
            self.db.execute('COMMIT')
        except BaseException:
            if self.db.in_transaction:
                self.db.execute('ROLLBACK')
            raise

    def contains(self, slot):
        """Cheap occupancy check; it never fetches text or coordinates."""
        _slot(slot)
        return self.db.execute('''SELECT 1 FROM stage5_view_bookmark_v1
            WHERE session_key=? AND slot=?''', (self.session_key, slot)).fetchone() is not None

    def save(self, slot, x, y, label, note, *, text_budget_bytes, overwrite=False):
        validation = validate_save(slot, label, note, text_budget_bytes=text_budget_bytes)
        for coordinate in (x, y):
            if type(coordinate) is not int or not 0 <= coordinate < 2**32:
                raise ValueError('native camera coordinates must be unsigned 32-bit integers')
        if type(overwrite) is not bool:
            raise ValueError('overwrite must be an explicit boolean')
        if validation['status'] != 'ready':
            return validation
        with self._transaction(write=True):
            exists = self.contains(slot)
            if exists and not overwrite:
                return {'status': 'rejected', 'reason': 'slot_occupied', 'slot': slot}
            self.db.execute('''INSERT INTO stage5_view_bookmark_v1
                (session_key,slot,x,y,label,note) VALUES(?,?,?,?,?,?)
                ON CONFLICT(session_key,slot) DO UPDATE SET
                x=excluded.x,y=excluded.y,label=excluded.label,note=excluded.note''',
                (self.session_key, slot, x, y, label, note))
        return {'status': 'saved', 'slot': slot,
                'required_bytes': validation['required_bytes']}

    def list(self, *, text_budget_bytes):
        """Return all three slots and saved labels, never note bodies."""
        _budget(text_budget_bytes)
        with self._transaction():
            sizes = self.db.execute('''SELECT slot,typeof(label),typeof(note),
                length(CAST(label AS BLOB)),length(CAST(note AS BLOB))
                FROM stage5_view_bookmark_v1 WHERE session_key=? ORDER BY slot''',
                (self.session_key,)).fetchall()
            for slot, label_type, note_type, label_bytes, note_bytes in sizes:
                _slot(slot)
                if label_type != 'text' or note_type != 'text':
                    raise ValueError('invalid bookmark text in database')
            required = sum(row[3] for row in sizes)
            failure = _required(required, text_budget_bytes)
            if failure:
                return failure
            labels = dict(self.db.execute('''SELECT slot,label
                FROM stage5_view_bookmark_v1 WHERE session_key=? ORDER BY slot''',
                (self.session_key,)))
            note_sizes = {row[0]: row[4] for row in sizes}
        return {'status': 'ok', 'required_bytes': required, 'views': [
            {'slot': slot, 'saved': False} if slot not in labels else
            {'slot': slot, 'saved': True, 'label': labels[slot],
             'note_bytes': note_sizes[slot]} for slot in SLOTS]}

    def get(self, slot, *, text_budget_bytes):
        """Read one full label/note or return the required budget first."""
        _slot(slot)
        _budget(text_budget_bytes)
        with self._transaction():
            sizes = self.db.execute('''SELECT typeof(label),typeof(note),
                length(CAST(label AS BLOB)),length(CAST(note AS BLOB))
                FROM stage5_view_bookmark_v1 WHERE session_key=? AND slot=?''',
                (self.session_key, slot)).fetchone()
            if sizes is None:
                return {'status': 'rejected', 'reason': 'empty_slot', 'slot': slot}
            if sizes[0] != 'text' or sizes[1] != 'text':
                raise ValueError('invalid bookmark text in database')
            required = sizes[2] + sizes[3]
            failure = _required(required, text_budget_bytes)
            if failure:
                return failure
            label, note = self.db.execute('''SELECT label,note
                FROM stage5_view_bookmark_v1 WHERE session_key=? AND slot=?''',
                (self.session_key, slot)).fetchone()
        return {'status': 'ok', 'slot': slot, 'label': label, 'note': note,
                'required_bytes': required}

    def read_coords(self, slot):
        """Controller-only recall input; no labels or notes are materialized."""
        _slot(slot)
        row = self.db.execute('''SELECT x,y FROM stage5_view_bookmark_v1
            WHERE session_key=? AND slot=?''', (self.session_key, slot)).fetchone()
        if row is not None and any(type(n) is not int or not 0 <= n < 2**32 for n in row):
            raise ValueError('invalid bookmark coordinates in database')
        return row
