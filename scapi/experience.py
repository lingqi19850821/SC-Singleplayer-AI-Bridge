"""Player-local observations and revisable inferences; never a source of game truth.

No default path, automatic import, game access or global-memory preload. Callers
choose a player SQLite and explicit profile. Source references are inert strings.

An episode notebook may optionally be paired with a persistent global notebook.
The two scopes remain separate SQLite databases and separate profiles so one
role's notes are never visible to another role.
"""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import uuid

AUTHOR_ROLES = frozenset(('operator', 'tactics', 'interaction'))
LEGACY_AUTHOR_ROLE = 'legacy_unassigned'
EPISODE_CURSOR_BASE = 1_000_000_000
_MAX_SAFE_CURSOR = 2 ** 53 - 1
_WRITE_SCOPES = ('episode', 'global')
_READ_SCOPES = ('all', 'global', 'episode')
ROLE_EXPERIENCE_OPERATIONS = ('record', 'revise', 'refute', 'promote', 'delete', 'list')
_ROLE_EXPERIENCE_COMMON = frozenset(('role', 'operation', 'text_budget_bytes'))
_ROLE_EXPERIENCE_FIELDS = ('content', 'kind', 'applicability', 'confidence',
                           'provenance', 'tags')
_ROLE_EXPERIENCE_REJECTIONS = frozenset((
    'invalid_experience_author_role', 'experience_author_role_mismatch',
    'global_experience_disabled_in_isolated_scope', 'experience_scope_mismatch',
    'invalid_experience_scope', 'experience_already_global',
    'episode_note_requires_connected_game',
))
_ROLE_EXPERIENCE_REASON_DETAILS = {
    'invalid_experience_author_role':
        'role must be operator, tactics or interaction',
    'experience_author_role_mismatch':
        'entry belongs to a different role',
    'global_experience_disabled_in_isolated_scope':
        'global experience is disabled in isolated scope',
    'experience_scope_mismatch':
        'entry scope does not match the requested scope',
    'invalid_experience_scope':
        'scope must be episode or global',
    'experience_already_global':
        'entry already exists in global experience',
    'experience_entry_not_found':
        'entry_id was not found in this role notebook',
    'episode_note_requires_connected_game':
        "episode notes require a connected game; pass scope 'global' first",
}
_ROLE_EXPERIENCE_SAFE_MESSAGES = {
    'kind must be observation or inference': 'kind must be observation or inference',
    'confidence must be finite and between 0 and 1':
        'confidence must be a number between 0 and 1',
    'provenance must be a nonempty list of source references':
        'provenance must be a nonempty list of strings',
    'tags must be a list': 'tags must be a list of strings',
    'tag must be a nonempty string': 'tags must contain nonempty strings',
    'provenance reference must be a nonempty string':
        'provenance must contain nonempty strings',
    'content must be a nonempty string': 'content must be a nonempty string',
    'applicability must be a nonempty string':
        'applicability must be a nonempty string',
    'run_ref must be a nonempty string': 'run_ref must be a nonempty string',
    'text_budget_bytes must be a positive integer':
        'text_budget_bytes must be a positive integer',
    'entry_id must be a nonempty string': 'entry_id must be a nonempty string',
    'role experience limit must be 1..64':
        'limit must be an integer between 1 and 64',
    'cursor must be a nonnegative sequence':
        'cursor must be a nonnegative sequence',
    'stale revision: revise the current latest entry':
        'stale revision: revise the current latest entry',
    'parent entry not found in this profile':
        'entry_id was not found in this role notebook',
}


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
        # Owned notebooks wait for a competing writer instead of failing a
        # role-note request on a transient database lock.
        db = sqlite3.connect(str(store), timeout=5.0)
        try:
            db.execute('PRAGMA busy_timeout=5000')
        except BaseException:
            db.close()
            raise
        return db, True
    raise ValueError('explicit player SQLite path, connection or PanelKnowledge required')


def _write_scope(value):
    if value not in _WRITE_SCOPES:
        raise ValueError('invalid_experience_scope')
    return value


def _read_scope(value):
    if value not in _READ_SCOPES:
        raise ValueError('invalid_experience_scope')
    return value


def _now():
    return datetime.now(timezone.utc).isoformat()


def _initialize_schema(db, *, owned=False):
    """Create the same append-only schema in an episode or global database."""
    # execute rather than executescript: do not implicitly commit borrowed
    # PanelKnowledge's pending transaction.
    for statement in (
        '''CREATE TABLE IF NOT EXISTS experience_entry_v1(
           seq INTEGER PRIMARY KEY AUTOINCREMENT, profile TEXT NOT NULL,
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
        '''CREATE TABLE IF NOT EXISTS experience_promotion_v1(
           profile TEXT NOT NULL, source_root_id TEXT NOT NULL,
           global_entry_id TEXT NOT NULL, promoted_at_utc TEXT NOT NULL,
           PRIMARY KEY(profile, source_root_id))''',
    ):
        db.execute(statement)
    columns = {row[1] for row in db.execute('PRAGMA table_info(experience_entry_v1)')}
    if 'author_role' not in columns:
        # Retain old payload bytes and revision history; never infer an owner
        # from tags, content or provenance supplied by a model.
        db.execute("ALTER TABLE experience_entry_v1 ADD COLUMN author_role "
                   "TEXT NOT NULL DEFAULT 'legacy_unassigned'")
    db.execute('CREATE INDEX IF NOT EXISTS experience_role_v1 '
               'ON experience_entry_v1(profile,author_role,seq)')
    if owned:
        # An owned notebook must not hold a schema write transaction open or
        # roll the fresh DDL back when a read-only CLI closes without a write.
        db.commit()


class ExperienceStore:
    """All writes are append-only; latest revision is the default read result.

    An instance owns an episode notebook and may additionally borrow or own a
    persistent global notebook. Cursors use one band for episode-only stores;
    dual-scope stores place episode sequences above EPISODE_CURSOR_BASE.

    Budgets are explicit caller choices, not silent truncation. read requires a
    selector (entry_id, keyword, or tags), and only loads the requested page.
    Confidence is the author's estimate, not API verification or a fact flag.
    """
    def __init__(self, store, *, profile_id, shared_store=None, shared_profile_id=None):
        self.profile = _text(profile_id, 'profile_id')
        self.db, self._owns = _connect(store)
        self.shared_db = self._shared_owns = None
        self.shared_profile = None
        if shared_store is not None:
            if isinstance(shared_store, (str, Path)):
                if not str(shared_store):
                    raise ValueError(
                        'explicit player SQLite path, connection or PanelKnowledge required')
                Path(shared_store).parent.mkdir(parents=True, exist_ok=True)
            self.shared_db, self._shared_owns = _connect(shared_store)
            self.shared_profile = _text(shared_profile_id or 'official_standard',
                                        'shared_profile_id')
        _initialize_schema(self.db, owned=self._owns)
        if self.shared_db is not None:
            _initialize_schema(self.shared_db, owned=self._shared_owns)

    def close(self):
        if self._owns:
            self.db.close()
        if self._shared_owns and self.shared_db is not None:
            self.shared_db.close()

    def _target(self, scope):
        if scope == 'global':
            if self.shared_db is None:
                raise ValueError('global_experience_disabled_in_isolated_scope')
            return self.shared_db, self.shared_profile
        return self.db, self.profile

    def _locate(self, entry_id):
        """Return (scope, db, profile, root_id, author_role) for an entry id."""
        _text(entry_id, 'entry_id')
        row = self.db.execute('''SELECT root_id,author_role FROM experience_entry_v1
            WHERE profile=? AND entry_id=?''', (self.profile, entry_id)).fetchone()
        if row is not None:
            return 'episode', self.db, self.profile, row[0], row[1]
        if self.shared_db is not None:
            row = self.shared_db.execute('''SELECT root_id,author_role FROM experience_entry_v1
                WHERE profile=? AND entry_id=?''',
                (self.shared_profile, entry_id)).fetchone()
            if row is not None:
                return 'global', self.shared_db, self.shared_profile, row[0], row[1]
        return None

    def read_latest_owned_payload(self, entry_id, *, author_role):
        """Latest validated payload for entry_id's chain, only for its owner.

        Returns None when the entry is missing, owned by a different role, or
        the stored payload no longer passes byte/field integrity checks.
        """
        try:
            _text(entry_id, 'entry_id')
            owner = _author_role(author_role)
        except (ValueError, TypeError):
            return None
        target = None
        for scope, db, profile in (
                ('episode', self.db, self.profile),
                ('global', self.shared_db, self.shared_profile)):
            if db is None:
                continue
            row = db.execute('''SELECT root_id,author_role FROM experience_entry_v1
                WHERE profile=? AND entry_id=?''', (profile, entry_id)).fetchone()
            if row is not None:
                target = scope, db, profile, row[0], row[1]
                break
        if target is None:
            return None
        scope, db, profile, root_id, stored_role = target
        if stored_role != owner:
            return None
        latest = db.execute('''SELECT seq,payload_bytes,
            length(CAST(payload AS BLOB)),payload,entry_id,root_id,parent_id,
            kind,relation,content,author_role FROM experience_entry_v1
            WHERE profile=? AND root_id=? ORDER BY seq DESC LIMIT 1''',
            (profile, root_id)).fetchone()
        if latest is None or latest[10] != owner:
            return None
        item = self._load_entry(scope, latest)
        if item is None:
            return None
        if item.get('author_role', LEGACY_AUTHOR_ROLE) != owner:
            return None
        return item

    def record(self, *, kind, content, applicability, confidence, provenance,
               run_ref, tags, text_budget_bytes, author_role=None, scope='episode'):
        scope = _write_scope(scope)
        db, profile = self._target(scope)
        result = self._append(db, profile, kind=kind, content=content,
            applicability=applicability, confidence=confidence,
            provenance=provenance, run_ref=run_ref, tags=tags,
            text_budget_bytes=text_budget_bytes, parent_id=None,
            relation='initial', author_role=author_role, scope=scope)
        result['scope'] = scope
        return result

    def revise(self, entry_id, *, kind, content, applicability, confidence,
               provenance, run_ref, tags, text_budget_bytes, author_role=None,
               scope=None):
        return self._append_to_existing(entry_id, kind=kind, content=content,
            applicability=applicability, confidence=confidence,
            provenance=provenance, run_ref=run_ref, tags=tags,
            text_budget_bytes=text_budget_bytes, relation='revision',
            author_role=author_role, scope=scope)

    def refute(self, entry_id, *, content, applicability, confidence, provenance,
               run_ref, tags, text_budget_bytes, author_role=None, scope=None):
        """Append the counterexample and a refuted status; retain old evidence."""
        return self._append_to_existing(entry_id, kind='observation', content=content,
            applicability=applicability, confidence=confidence,
            provenance=provenance, run_ref=run_ref, tags=tags,
            text_budget_bytes=text_budget_bytes, relation='counterexample',
            author_role=author_role, scope=scope)

    def _append_to_existing(self, entry_id, *, kind, content, applicability,
                            confidence, provenance, run_ref, tags,
                            text_budget_bytes, relation, author_role, scope):
        if scope is not None:
            _write_scope(scope)
        _author_role(author_role)
        found = self._locate(entry_id)
        if found is None:
            raise ValueError('parent entry not found in this profile')
        found_scope, db, profile, _root, _stored_role = found
        if scope is not None and scope != found_scope:
            raise ValueError('experience_scope_mismatch')
        # Both CLI and session writers may race for the same parent revision.
        # Acquire the write lock before the latest-revision check/insert so the
        # loser waits, observes the committed child, and rejects as stale
        # instead of forking two sibling revisions.
        started = False
        if not db.in_transaction:
            db.execute('BEGIN IMMEDIATE')
            started = True
        try:
            result = self._append(db, profile, kind=kind, content=content,
                applicability=applicability, confidence=confidence,
                provenance=provenance, run_ref=run_ref, tags=tags,
                text_budget_bytes=text_budget_bytes, parent_id=entry_id,
                relation=relation, author_role=author_role, scope=found_scope)
        except BaseException:
            if started and db.in_transaction:
                db.rollback()
            raise
        if started and db.in_transaction:
            # budget_required keeps the episode copy but leaves no write.
            db.rollback()
        result['scope'] = found_scope
        return result

    def _append(self, db, profile, *, kind, content, applicability, confidence,
                provenance, run_ref, tags, text_budget_bytes, parent_id,
                relation, author_role, scope, entry_id=None):
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
        if entry_id is None:
            entry_id = uuid.uuid4().hex
        else:
            # Promotion pre-generates the global entry id so its durable
            # source-root mapping can commit in the same transaction.
            _text(entry_id, 'entry_id')
        # Savepoint preserves the caller's transaction on rejection/failure.
        db.execute('SAVEPOINT experience_append')
        released = False
        try:
            root = entry_id
            if parent_id is not None:
                parent = db.execute('''SELECT root_id,author_role FROM experience_entry_v1
                    WHERE profile=? AND entry_id=?''',
                    (profile, parent_id)).fetchone()
                if parent is None:
                    raise ValueError('parent entry not found in this profile')
                if parent[1] != owner:
                    raise ValueError('experience_author_role_mismatch')
                root = parent[0]
                latest = db.execute('''SELECT entry_id FROM experience_entry_v1
                    WHERE profile=? AND root_id=? ORDER BY seq DESC LIMIT 1''',
                    (profile, root)).fetchone()[0]
                if latest != parent_id:
                    raise ValueError('stale revision: revise the current latest entry')
            payload = dict(entry_id=entry_id, root_id=root, parent_id=parent_id,
                kind=kind, relation=relation, content=content,
                applicability=applicability, confidence=float(confidence),
                provenance=list(provenance), run_ref=run_ref, tags=tags,
                scope=scope,
                recorded_at_utc=_now(),
                status='refuted' if relation == 'counterexample' else 'recorded',
                epistemic_status='player_authored_not_api_verified')
            if author_role is not None:
                payload['author_role'] = owner
            size = _bytes(payload)
            if size > text_budget_bytes:
                db.execute('ROLLBACK TO experience_append')
                db.execute('RELEASE experience_append')
                released = True
                return dict(status='budget_required', required_text_budget_bytes=size,
                            text_budget_bytes=text_budget_bytes, written=False)
            encoded = _json(payload)
            db.execute('''INSERT INTO experience_entry_v1
                (profile,entry_id,root_id,parent_id,kind,relation,content,payload,payload_bytes,author_role)
                VALUES(?,?,?,?,?,?,?,?,?,?)''',
                (profile, entry_id, root, parent_id, kind, relation,
                 content, encoded, size, owner))
            db.executemany('INSERT INTO experience_tag_v1 VALUES(?,?,?)',
                [(profile, entry_id, tag) for tag in tags])
            db.execute('RELEASE experience_append')
            released = True
            db.commit()
            return dict(status='recorded', entry_id=entry_id, root_id=root,
                        relation=relation, written=True, text_bytes=size)
        except BaseException:
            if not released:
                db.execute('ROLLBACK TO experience_append')
                db.execute('RELEASE experience_append')
            raise

    def delete(self, entry_id, *, author_role=None, scope=None):
        """Physically remove one root chain, including all tags and revisions."""
        if scope is not None:
            _write_scope(scope)
        owner = _author_role(author_role)
        found = self._locate(entry_id)
        if found is None:
            raise ValueError('experience_entry_not_found')
        found_scope, db, profile, root_id, stored_role = found
        if scope is not None and scope != found_scope:
            raise ValueError('experience_scope_mismatch')
        if stored_role != owner:
            raise ValueError('experience_author_role_mismatch')
        db.execute('SAVEPOINT experience_delete')
        released = False
        try:
            count = db.execute('''SELECT count(*) FROM experience_entry_v1
                WHERE profile=? AND root_id=?''', (profile, root_id)).fetchone()[0]
            db.execute('''DELETE FROM experience_tag_v1
                WHERE profile=? AND entry_id IN (SELECT entry_id
                    FROM experience_entry_v1 WHERE profile=? AND root_id=?)''',
                (profile, profile, root_id))
            db.execute('''DELETE FROM experience_entry_v1
                WHERE profile=? AND root_id=?''', (profile, root_id))
            db.execute('RELEASE experience_delete')
            released = True
            db.commit()
        except BaseException:
            if not released:
                db.execute('ROLLBACK TO experience_delete')
                db.execute('RELEASE experience_delete')
            raise
        return dict(status='deleted', entry_id=entry_id, root_id=root_id,
                    scope=found_scope, deleted=True, deleted_revisions=count)

    def promote(self, entry_id, *, run_ref, author_role=None, kind=None,
                content=None, applicability=None, confidence=None,
                provenance=None, tags=None, text_budget_bytes=65536):
        """Move the latest episode revision into the persistent global notebook.

        A durable promotion record (source chain root -> global entry) commits
        atomically with the global note. If the episode-chain deletion then
        fails, a retry finds that record, finishes the deletion, and returns the
        already-promoted note without creating a duplicate.
        """
        if self.shared_db is None:
            raise ValueError('global_experience_disabled_in_isolated_scope')
        _text(entry_id, 'entry_id')
        _positive(text_budget_bytes, 'text_budget_bytes')
        owner = _author_role(author_role)
        row = self.db.execute('''SELECT root_id,author_role FROM experience_entry_v1
            WHERE profile=? AND entry_id=?''', (self.profile, entry_id)).fetchone()
        if row is None:
            # Only a chain that still lives in this episode notebook may be
            # promoted. An id that already exists globally is never duplicated.
            already = self.shared_db.execute('''SELECT 1 FROM experience_entry_v1
                WHERE profile=? AND entry_id=?''',
                (self.shared_profile, entry_id)).fetchone()
            if already is not None:
                raise ValueError('experience_already_global')
            raise ValueError('parent entry not found in this profile')
        root_id, stored_role = row
        if stored_role != owner:
            raise ValueError('experience_author_role_mismatch')
        latest = self.db.execute('''SELECT payload FROM experience_entry_v1
            WHERE profile=? AND root_id=? ORDER BY seq DESC LIMIT 1''',
            (self.profile, root_id)).fetchone()
        if latest is None:
            raise ValueError('parent entry not found in this profile')
        try:
            base = json.loads(latest[0])
        except (ValueError, TypeError, UnicodeError):
            raise ValueError('experience_data_corrupt')
        if not isinstance(base, dict):
            raise ValueError('experience_data_corrupt')

        def pick(value, fallback):
            return fallback if value is None else value

        if applicability is None:
            # An omitted episode-local default becomes a persistent global
            # claim; keep author-customised applicability, but never copy the
            # ephemeral current_episode label into global memory.
            applicability = base.get('applicability')
            if applicability == 'current_episode':
                applicability = 'cross_mission'
        started = False
        if not self.shared_db.in_transaction:
            # Serialize concurrent promotes of the same episode root so the
            # durable promotion record and the global note commit together.
            self.shared_db.execute('BEGIN IMMEDIATE')
            started = True
        try:
            promoted = self.shared_db.execute('''SELECT global_entry_id
                FROM experience_promotion_v1
                WHERE profile=? AND source_root_id=?''',
                (self.shared_profile, root_id)).fetchone()
            global_row = None
            if promoted is not None:
                global_row = self.shared_db.execute('''SELECT entry_id,root_id,payload_bytes
                    FROM experience_entry_v1 WHERE profile=? AND entry_id=?''',
                    (self.shared_profile, promoted[0])).fetchone()
                if global_row is None:
                    # The global note was explicitly deleted; that also retires
                    # the old promotion record so a fresh retry can recreate it.
                    self.shared_db.execute('''DELETE FROM experience_promotion_v1
                        WHERE profile=? AND source_root_id=?''',
                        (self.shared_profile, root_id))
                    promoted = None
            if promoted is not None:
                # A prior promote committed the global note but its episode
                # deletion failed. Retrying must finish that deletion and must
                # never create a duplicate global note.
                result = dict(status='promoted', entry_id=global_row[0],
                    root_id=global_row[1], promoted_from_entry_id=entry_id,
                    scope='global', previous_scope='episode', written=True,
                    episode_deleted=False, text_bytes=global_row[2],
                    retried=True)
            else:
                global_entry_id = uuid.uuid4().hex
                self.shared_db.execute('''INSERT INTO experience_promotion_v1
                    (profile,source_root_id,global_entry_id,promoted_at_utc)
                    VALUES(?,?,?,?)''',
                    (self.shared_profile, root_id, global_entry_id, _now()))
                result = self._append(self.shared_db, self.shared_profile,
                    kind=pick(kind, base.get('kind')),
                    content=pick(content, base.get('content')),
                    applicability=applicability,
                    confidence=pick(confidence, base.get('confidence')),
                    provenance=pick(provenance, base.get('provenance')),
                    run_ref=run_ref, tags=pick(tags, base.get('tags')),
                    text_budget_bytes=text_budget_bytes, parent_id=None,
                    relation='initial', author_role=author_role,
                    scope='global', entry_id=global_entry_id)
        except BaseException:
            if started and self.shared_db.in_transaction:
                self.shared_db.rollback()
            raise
        if started and self.shared_db.in_transaction:
            # budget_required or the idempotent retry leaves no global write.
            self.shared_db.rollback()
        if result.get('status') == 'budget_required':
            # The episode copy is retained until the global write commits.
            result['scope'] = 'global'
            result['previous_scope'] = 'episode'
            return result
        result['status'] = 'promoted'
        result.setdefault('promoted_from_entry_id', entry_id)
        result.setdefault('scope', 'global')
        result.setdefault('previous_scope', 'episode')
        self._delete_chain(self.db, self.profile, root_id)
        result['episode_deleted'] = True
        return result

    @staticmethod
    def _delete_chain(db, profile, root_id):
        db.execute('SAVEPOINT experience_delete')
        released = False
        try:
            db.execute('''DELETE FROM experience_tag_v1
                WHERE profile=? AND entry_id IN (SELECT entry_id
                    FROM experience_entry_v1 WHERE profile=? AND root_id=?)''',
                (profile, profile, root_id))
            db.execute('''DELETE FROM experience_entry_v1
                WHERE profile=? AND root_id=?''', (profile, root_id))
            db.execute('RELEASE experience_delete')
            released = True
            db.commit()
        except BaseException:
            if not released:
                db.execute('ROLLBACK TO experience_delete')
                db.execute('RELEASE experience_delete')
            raise

    @staticmethod
    def _selection(profile, *, cursor, entry_id, keyword, tags, history, owner):
        where = ['e.profile=?', 'e.seq>?']; args = [profile, cursor]
        if owner is not None:
            where.append('e.author_role=?'); args.append(owner)
        if entry_id is not None:
            # An old revision ID selects its root, so current reads cannot quietly
            # return a superseded claim. history=True exposes the complete chain.
            where.append('''e.root_id=(SELECT root_id FROM experience_entry_v1
                WHERE profile=? AND entry_id=?)''')
            args += [profile, entry_id]
        if not history:
            where.append('''NOT EXISTS(SELECT 1 FROM experience_entry_v1 n
                WHERE n.profile=e.profile AND n.root_id=e.root_id AND n.seq>e.seq)''')
        if keyword is not None:
            where.append('instr(lower(e.content),lower(?))>0'); args.append(keyword)
        if tags is not None:
            for tag in sorted(set(_text(t, 'tag').casefold() for t in tags)):
                where.append('''EXISTS(SELECT 1 FROM experience_tag_v1 t
                    WHERE t.profile=e.profile AND t.entry_id=e.entry_id AND t.tag=?)''')
                args.append(tag)
        return ' AND '.join(where), args

    @classmethod
    def _fetch_rows(cls, db, profile, *, cursor, limit, entry_id, keyword, tags,
                    history, owner):
        clause, args = cls._selection(profile, cursor=cursor, entry_id=entry_id,
            keyword=keyword, tags=tags, history=history, owner=owner)
        # Select the payload with its indexed columns in one statement. A
        # concurrent delete can then never turn an already selected row into a
        # false 'experience_data_corrupt' on a second lookup.
        return db.execute('''SELECT e.seq,e.payload_bytes,
            length(CAST(e.payload AS BLOB)),e.payload,e.entry_id,e.root_id,
            e.parent_id,e.kind,e.relation,e.content,e.author_role
            FROM experience_entry_v1 e WHERE '''
            + clause + ' ORDER BY e.seq LIMIT ?', args + [limit]).fetchall()

    @staticmethod
    def _cursor_for(band, seq):
        return EPISODE_CURSOR_BASE + seq if band == 'episode' else seq

    @staticmethod
    def _load_entry(target_scope, row):
        if not isinstance(row, tuple) or len(row) != 11:
            return None
        (seq, expected_size, actual_size, raw, entry_id, root_id, parent_id,
         kind, relation, content, stored_role) = row
        if (type(expected_size) is not int or expected_size < 2
                or expected_size != actual_size):
            return None
        if not isinstance(raw, str):
            return None
        try:
            item = json.loads(raw)
            # Validate payload bytes BEFORE adding a legacy scope field. Existing
            # rows written without 'scope' remain byte-exact and readable.
            if (not isinstance(item, dict) or _bytes(item) != expected_size
                    or item.get('scope', target_scope) != target_scope
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
        except (ValueError, TypeError, UnicodeError):
            return None
        item['scope'] = target_scope
        return item

    def _read_single(self, *, limit, text_budget_bytes, entry_id, keyword, tags,
                     cursor, history, owner):
        rows = self._fetch_rows(self.db, self.profile, cursor=cursor, limit=limit + 1,
            entry_id=entry_id, keyword=keyword, tags=tags, history=history, owner=owner)
        page, more = rows[:limit], len(rows) > limit
        for row in page:
            seq, expected_size, actual_size = row[0], row[1], row[2]
            if (type(expected_size) is not int or expected_size < 2
                    or expected_size != actual_size):
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
        for row in page:
            item = self._load_entry('episode', row)
            if item is None:
                return dict(status='unavailable', reason='experience_data_corrupt',
                            corrupt_sequence=row[0], entries=[], next_cursor=cursor,
                            has_more=True)
            entries.append(item)
        return dict(status='ok', entries=entries, next_cursor=next_cursor,
                    has_more=more, text_bytes=required)

    def _read_dual(self, *, limit, text_budget_bytes, entry_id, keyword, tags,
                   cursor, history, owner, scope):
        bands = []
        if cursor < EPISODE_CURSOR_BASE:
            if scope in ('all', 'global'):
                rows = self._fetch_rows(self.shared_db, self.shared_profile,
                    cursor=cursor, limit=limit + 1, entry_id=entry_id,
                    keyword=keyword, tags=tags, history=history, owner=owner)
                bands.extend(('global', row) for row in rows)
            if (scope in ('all', 'episode') and len(bands) < limit + 1):
                rows = self._fetch_rows(self.db, self.profile, cursor=0,
                    limit=limit + 1 - len(bands), entry_id=entry_id,
                    keyword=keyword, tags=tags, history=history, owner=owner)
                bands.extend(('episode', row) for row in rows)
        elif scope in ('all', 'episode'):
            rows = self._fetch_rows(self.db, self.profile,
                cursor=cursor - EPISODE_CURSOR_BASE, limit=limit + 1,
                entry_id=entry_id, keyword=keyword, tags=tags,
                history=history, owner=owner)
            bands.extend(('episode', row) for row in rows)

        page, more = bands[:limit], len(bands) > limit
        for band, row in page:
            seq, expected_size, actual_size = row[0], row[1], row[2]
            if (type(expected_size) is not int or expected_size < 2
                    or expected_size != actual_size):
                return dict(status='unavailable', reason='experience_data_corrupt',
                            corrupt_sequence=self._cursor_for(band, seq),
                            entries=[], next_cursor=cursor, has_more=True)
        required = 2 + sum(row[1] for _band, row in page) + max(0, len(page) - 1)
        next_cursor = (self._cursor_for(page[-1][0], page[-1][1][0])
                       if page else cursor)
        if required > text_budget_bytes:
            return dict(status='budget_required', required_text_budget_bytes=required,
                        text_budget_bytes=text_budget_bytes, entries=[],
                        next_cursor=cursor, has_more=bool(bands))
        entries = []
        for band, row in page:
            item = self._load_entry(band, row)
            if item is None:
                return dict(status='unavailable', reason='experience_data_corrupt',
                            corrupt_sequence=self._cursor_for(band, row[0]),
                            entries=[], next_cursor=cursor, has_more=True)
            entries.append(item)
        return dict(status='ok', entries=entries, next_cursor=next_cursor,
                    has_more=more, text_bytes=required)

    def read(self, *, limit, text_budget_bytes, entry_id=None, keyword=None,
             tags=None, cursor=None, history=False, author_role=None, scope='all'):
        owner = _author_role(author_role)
        _positive(limit, 'limit'); _positive(text_budget_bytes, 'text_budget_bytes')
        if type(history) is not bool:
            raise ValueError('history must be bool')
        _read_scope(scope)
        if cursor is None:
            cursor = 0
        if type(cursor) is not int or not 0 <= cursor <= _MAX_SAFE_CURSOR:
            raise ValueError('cursor must be a nonnegative sequence')
        if entry_id is not None:
            _text(entry_id, 'entry_id')
        if keyword is not None:
            _text(keyword, 'keyword')
        if tags is not None:
            if not isinstance(tags, list) or not tags:
                raise ValueError('tags must be a nonempty list when supplied')
            for tag in tags:
                _text(tag, 'tag')
        if self.shared_db is None and scope == 'global':
            # An isolated notebook has no global band; a global-only read is an
            # empty success rather than an implicit fallback to episode data.
            required = 2
            if required > text_budget_bytes:
                return dict(status='budget_required',
                            required_text_budget_bytes=required,
                            text_budget_bytes=text_budget_bytes, entries=[],
                            next_cursor=cursor, has_more=False)
            return dict(status='ok', entries=[], next_cursor=cursor,
                        has_more=False, text_bytes=required)
        if entry_id is None and keyword is None and not tags and author_role is None:
            raise ValueError('select entry_id, keyword or tags; no automatic full preload')
        owner_filter = owner if author_role is not None else None
        if self.shared_db is None:
            return self._read_single(limit=limit,
                text_budget_bytes=text_budget_bytes, entry_id=entry_id,
                keyword=keyword, tags=tags, cursor=cursor, history=history,
                owner=owner_filter)
        return self._read_dual(limit=limit,
            text_budget_bytes=text_budget_bytes, entry_id=entry_id,
            keyword=keyword, tags=tags, cursor=cursor, history=history,
            owner=owner_filter, scope=scope)

    def list(self, *, author_role, limit=32, cursor=None, text_budget_bytes=65536,
             scope='all'):
        """Host-owned GUI page of this role's latest claims, in the same SQLite."""
        if author_role is None:
            raise ValueError('invalid_experience_author_role')
        _author_role(author_role)
        if type(limit) is not int or not 1 <= limit <= 64:
            raise ValueError('role experience limit must be 1..64')
        return self.read(author_role=author_role, limit=limit, cursor=cursor,
                         text_budget_bytes=text_budget_bytes, scope=scope)


class _RoleExperienceResult(dict):
    """Dictionary result that may carry a private host-only error type."""
    pass


def _role_failure(status, reason, detail):
    return _RoleExperienceResult(status=status, reason=reason, detail=detail,
                                 automatic_retry=False, command_sent=False)


def _role_success(result, role):
    value = _RoleExperienceResult(automatic_retry=False, command_sent=False)
    if isinstance(result, dict):
        value.update(result)
    value['role'] = role
    if value.get('status') in ('rejected', 'unavailable') and not value.get('detail'):
        value['detail'] = 'role experience operation is ' + str(value['status'])
    return value


def _role_allowed(operation):
    common = set(_ROLE_EXPERIENCE_COMMON)
    if operation == 'list':
        return frozenset(common | {'cursor', 'limit', 'scope', 'run_ref'})
    if operation == 'delete':
        return frozenset(common | {'entry_id', 'run_ref', 'scope'})
    if operation == 'promote':
        return frozenset(common | {'entry_id', 'run_ref', 'scope'} |
                         set(_ROLE_EXPERIENCE_FIELDS))
    return frozenset(common | {'entry_id', 'run_ref', 'scope'} |
                     set(_ROLE_EXPERIENCE_FIELDS))


def _role_references(value, fallback):
    if value is None:
        value = fallback
    if isinstance(value, str):
        return [value] if value.strip() else value
    return value


def _role_tags(value, fallback):
    if value is None:
        value = fallback
    if isinstance(value, str):
        return [value] if value.strip() else value
    return value


def _role_record_fields(role, scope, args):
    applicability = ('cross_mission' if scope == 'global'
                     else 'current_episode')
    return dict(
        content=args['content'],
        kind=args.get('kind', 'observation'),
        applicability=args.get('applicability', applicability),
        confidence=args.get('confidence', 0.8),
        provenance=_role_references(args.get('provenance'),
                                    [role + '_observation']),
        tags=_role_tags(args.get('tags'), []),
    )


def _role_revision_fields(store, role, operation, args, *, scope_hint):
    reader = getattr(store, 'read_latest_owned_payload', None)
    payload = (reader(args['entry_id'], author_role=role)
               if callable(reader) else None)
    if not isinstance(payload, dict):
        payload = {}
    payload_scope = payload.get('scope')
    if payload_scope not in _WRITE_SCOPES:
        payload_scope = scope_hint if scope_hint in _WRITE_SCOPES else 'episode'
    applicability = ('cross_mission'
                     if scope_hint == 'global' or payload_scope == 'global'
                     else 'current_episode')

    def inherited(key, default):
        value = payload.get(key)
        return default if value is None else value

    fields = dict(
        content=args['content'],
        applicability=args.get('applicability',
                               inherited('applicability', applicability)),
        confidence=args.get('confidence', inherited('confidence', 0.8)),
        provenance=_role_references(args.get('provenance'),
            inherited('provenance', [role + '_observation'])),
        tags=_role_tags(args.get('tags'), inherited('tags', [])),
    )
    if operation == 'revise':
        fields['kind'] = args.get('kind', inherited('kind', 'observation'))
    return fields


def _role_execute_online(store, *, role, operation, args, scope, run_ref):
    budget = args.get('text_budget_bytes', 65536)
    if operation == 'list':
        fields = dict(author_role=role, limit=args.get('limit', 32),
            cursor=args.get('cursor'), text_budget_bytes=budget)
        if 'scope' in args:
            fields['scope'] = scope
        return _role_success(store.list(**fields), role)
    if operation == 'delete':
        fields = {'author_role': role}
        if scope is not None:
            fields['scope'] = scope
        return _role_success(store.delete(args['entry_id'], **fields), role)
    if operation == 'promote':
        fields = {name: args[name] for name in _ROLE_EXPERIENCE_FIELDS
                  if name in args}
        result = store.promote(args['entry_id'], author_role=role,
            run_ref=run_ref, text_budget_bytes=budget, **fields)
        return _role_success(result, role)
    if operation == 'record':
        fields = _role_record_fields(role, scope or 'episode', args)
        fields.update(author_role=role, run_ref=run_ref,
                      text_budget_bytes=budget)
        if 'scope' in args:
            fields['scope'] = scope
        return _role_success(store.record(**fields), role)
    fields = _role_revision_fields(store, role, operation, args,
                                   scope_hint=scope)
    fields.update(author_role=role, run_ref=run_ref, text_budget_bytes=budget)
    if scope is not None:
        fields['scope'] = scope
    result = getattr(store, operation)(args['entry_id'], **fields)
    return _role_success(result, role)


def _role_execute_offline(store, *, role, operation, args, scope, run_ref):
    budget = args.get('text_budget_bytes', 65536)
    shared = getattr(store, 'shared_db', None)
    if operation == 'list':
        if scope == 'episode':
            return _role_failure('unavailable', 'game_not_connected',
                'episode notes require a connected game; '
                "use scope 'global' or 'all' offline")
        result = store.list(author_role=role, limit=args.get('limit', 32),
            cursor=args.get('cursor'), text_budget_bytes=budget,
            scope='global')
        return _role_success(result, role)
    if operation == 'promote':
        return _role_failure('unavailable', 'game_not_connected',
            'promote requires an active episode; connect a game first')
    if operation == 'record':
        if scope == 'global':
            if shared is None:
                return _role_failure('rejected',
                    'global_experience_disabled_in_isolated_scope',
                    _ROLE_EXPERIENCE_REASON_DETAILS[
                        'global_experience_disabled_in_isolated_scope'])
            fields = _role_record_fields(role, 'global', args)
            result = store.record(scope='global', author_role=role,
                run_ref=run_ref, text_budget_bytes=budget, **fields)
            return _role_success(result, role)
        if shared is None:
            return _role_failure('unavailable', 'game_not_connected',
                'a connected game is required for episode notes')
        return _role_failure('rejected',
            'episode_note_requires_connected_game',
            "episode notes require a connected game; pass scope 'global' "
            'before gameplay starts')
    if shared is None or scope == 'episode':
        return _role_failure('unavailable', 'game_not_connected',
            'a connected game is required for ' + operation)
    if operation == 'delete':
        result = store.delete(args['entry_id'], author_role=role,
                              scope='global')
        return _role_success(result, role)
    fields = _role_revision_fields(store, role, operation, args,
                                   scope_hint='global')
    fields.update(author_role=role, run_ref=run_ref,
                  text_budget_bytes=budget, scope='global')
    result = getattr(store, operation)(args['entry_id'], **fields)
    return _role_success(result, role)


def execute_role_experience(store, *, role, args, runtime_run_ref,
                            offline=False):
    """One validated, role-owned execution path for host and offline CLI.

    Authored notes remain player_authored_not_api_verified in the store. This
    boundary adds ergonomic defaults and run_ref handling only; rejection
    details are fixed strings so SQL, paths and tracebacks never escape.
    """
    if not isinstance(args, dict):
        return _role_failure('rejected', 'invalid_role_experience_arguments',
                             'args must be a JSON object')
    if not isinstance(role, str) or role not in AUTHOR_ROLES:
        return _role_failure('rejected', 'invalid_experience_author_role',
            'role must be one of ' + ', '.join(sorted(AUTHOR_ROLES)))
    operation = args.get('operation')
    if operation not in ROLE_EXPERIENCE_OPERATIONS:
        return _role_failure('rejected', 'invalid_role_experience_operation',
            'operation must be one of ' +
            ', '.join(ROLE_EXPERIENCE_OPERATIONS))
    allowed = _role_allowed(operation)
    run_ref_supplied = 'run_ref' in args
    run_ref_value = args.get('run_ref')
    # Unknown keys are rejected even when their value is null: a typo such as
    # tagz=None must not pass as an omitted optional. Only allowed keys may be
    # stripped as "use the default". An explicit online run_ref None is still a
    # run mismatch rather than an omitted current run.
    unsupported = sorted((str(key) for key in args if key not in allowed))
    if unsupported:
        return _role_failure('rejected', 'invalid_role_experience_arguments',
            'unsupported field(s): ' + ', '.join(unsupported) +
            '; allowed arguments: ' + ', '.join(sorted(allowed)))
    cleaned = {key: value for key, value in args.items()
               if value is not None}
    if not offline and operation != 'list':
        if run_ref_supplied and run_ref_value != runtime_run_ref:
            return _role_failure('rejected', 'role_experience_run_changed',
                                 'run_ref does not match the current run')
    run_ref = runtime_run_ref
    scope = cleaned.get('scope')
    if operation == 'list':
        if scope is not None and scope not in _READ_SCOPES:
            return _role_failure('rejected', 'invalid_experience_scope',
                                 'scope must be all, global or episode')
        if scope is None:
            scope = 'all'
    elif operation == 'promote':
        if scope is not None and scope != 'global':
            return _role_failure('rejected', 'invalid_experience_scope',
                                 "promote accepts only scope 'global'")
    elif scope is not None and scope not in _WRITE_SCOPES:
        return _role_failure('rejected', 'invalid_experience_scope',
                             'scope must be episode or global')
    if operation == 'record' and 'entry_id' in cleaned:
        return _role_failure('rejected', 'invalid_role_experience_arguments',
            'record does not accept entry_id; allowed arguments: ' +
            ', '.join(sorted(allowed)))
    if offline:
        # Offline availability is decided before requiring an entry or content:
        # these operations cannot run without a connected episode regardless.
        shared = getattr(store, 'shared_db', None)
        if operation == 'promote':
            return _role_failure('unavailable', 'game_not_connected',
                'promote requires an active episode; connect a game first')
        if operation in ('revise', 'refute', 'delete') and (
                shared is None or scope == 'episode'):
            return _role_failure('unavailable', 'game_not_connected',
                'a connected game is required for ' + operation)
        if operation == 'record':
            if scope == 'global' and shared is None:
                return _role_failure('rejected',
                    'global_experience_disabled_in_isolated_scope',
                    _ROLE_EXPERIENCE_REASON_DETAILS[
                        'global_experience_disabled_in_isolated_scope'])
            if scope in (None, 'episode'):
                if shared is None:
                    return _role_failure('unavailable', 'game_not_connected',
                        'a connected game is required for episode notes')
                return _role_failure('rejected',
                    'episode_note_requires_connected_game',
                    "episode notes require a connected game; pass scope "
                    "'global' before gameplay starts")
    if operation in ('revise', 'refute', 'delete', 'promote'):
        entry_id = cleaned.get('entry_id')
        if not isinstance(entry_id, str) or not entry_id.strip():
            return _role_failure('rejected',
                'invalid_role_experience_arguments',
                'entry_id is required for ' + operation)
    if operation in ('record', 'revise', 'refute'):
        content = cleaned.get('content')
        if not isinstance(content, str) or not content.strip():
            return _role_failure('rejected',
                'invalid_role_experience_arguments',
                'content is required for ' + operation)
    try:
        if offline:
            return _role_execute_offline(store, role=role,
                operation=operation, args=cleaned, scope=scope, run_ref=run_ref)
        return _role_execute_online(store, role=role, operation=operation,
            args=cleaned, scope=scope, run_ref=run_ref)
    except (ValueError, TypeError, KeyError) as exc:
        message = str(exc)
        if offline and message in ('parent entry not found in this profile',
                                   'experience_entry_not_found',
                                   'experience_scope_mismatch'):
            # Offline operations are shared-notebook only. An episode-only row
            # is therefore a missing shared entry, not a writable target.
            return _role_failure('rejected', 'experience_entry_not_found',
                'entry_id was not found in the shared global notebook')
        if message == 'experience_entry_not_found':
            return _role_failure('rejected', 'experience_entry_not_found',
                _ROLE_EXPERIENCE_REASON_DETAILS['experience_entry_not_found'])
        if message in _ROLE_EXPERIENCE_REJECTIONS:
            return _role_failure('rejected', message,
                _ROLE_EXPERIENCE_REASON_DETAILS.get(
                    message, 'role experience request was rejected'))
        return _role_failure('rejected', 'invalid_role_experience_arguments',
            _ROLE_EXPERIENCE_SAFE_MESSAGES.get(
                message, 'invalid role experience arguments'))
    except Exception as exc:
        # No SQL, path, authored content or traceback enters the result.
        result = _role_failure('unavailable',
            'role_experience_storage_unavailable',
            'role experience storage is unavailable')
        result.error_type = type(exc).__name__
        return result
