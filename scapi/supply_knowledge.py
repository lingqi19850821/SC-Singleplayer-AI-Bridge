"""Immediately learned own-panel provided supply, keyed by actual loaded raw rule.

The caller establishes a fresh legal OWN selection/name before learn(). Loaded
rules only validate applicability; reading a table never learns its contents.
All SQLite access stays on the existing PanelKnowledge caller thread.
"""
import struct


SUPPLY_PROVIDED_VA = 0x6AD390
UNIT_TYPE_COUNT = 228
MORPH_QUEUE_TYPES = frozenset((0x85, 0x84, 0x89, 0x90, 0x92))


def _kind(value):
    if type(value) is not int or not 0 <= value < UNIT_TYPE_COUNT:
        raise ValueError('invalid supply display kind')
    return value


def _raw(value):
    if type(value) is not int or not 0 <= value <= 255:
        raise ValueError('invalid loaded supply byte')
    return value


def _table(value):
    if not isinstance(value, (bytes, bytearray, memoryview)) or len(value) != UNIT_TYPE_COUNT:
        raise ValueError('private supply snapshot must contain exactly 228 bytes')
    result = bytes(value)
    if len(result) != UNIT_TYPE_COUNT:
        raise ValueError('invalid private supply snapshot byte format')
    return result


def selected_supply_type(snapshot, expected_kind, expected_owner):
    """Pure mirror of target 424AD0/43BBB0 on an authorized selected CUnit.

    Root must establish ownership and recheck epoch, selection generation,
    selected pointer and ownership around its snapshot. This never calls native
    functions and cannot establish that an arbitrary snapshot was legal to read.
    """
    if not isinstance(snapshot, (bytes, bytearray, memoryview)) or len(snapshot) != 0x148:
        raise RuntimeError('invalid selected-unit snapshot')
    kind = struct.unpack_from('<H', snapshot, 0x64)[0]
    owner = snapshot[0x4C]
    if (type(expected_kind) is not int or type(expected_owner) is not int
            or kind != expected_kind or owner != expected_owner or not 0 <= kind < UNIT_TYPE_COUNT):
        raise RuntimeError('selected own panel changed')
    if not struct.unpack_from('<I', snapshot, 0xDC)[0] & 1:
        index = snapshot[0xA4]
        if index >= 5:
            raise RuntimeError('invalid current build queue index')
        target = struct.unpack_from('<H', snapshot, 0x98 + index * 2)[0]
        if target in MORPH_QUEUE_TYPES:
            kind = target
    return kind


def normalize_selected_supply(unit_snapshot, rawtable, *, expected_kind=None, expected_owner=None):
    """Return private {kind, raw, amount}; caller supplies the legal OWN gate.

    Optional expected values permit comparison with the already verified panel.
    Without them this only normalizes the supplied bytes, not ownership/access.
    """
    if not isinstance(unit_snapshot, (bytes, bytearray, memoryview)) or len(unit_snapshot) != 0x148:
        raise RuntimeError('invalid selected-unit snapshot')
    if expected_kind is None:
        expected_kind = struct.unpack_from('<H', unit_snapshot, 0x64)[0]
    if expected_owner is None:
        expected_owner = unit_snapshot[0x4C]
    kind = selected_supply_type(unit_snapshot, expected_kind, expected_owner)
    raw = _table(rawtable)[kind]
    return {'kind': kind, 'raw': raw, 'amount': (raw + 1) >> 1}


class SupplyKnowledge:
    """Global learned fixed facts, with exact current-map raw-byte matching.

    snapshot() reads one private 228-byte table. Only when its contents change
    does it fetch matching learned rows, using indexed (kind,raw) lookups with
    at most 228 returned rows. value() never touches SQLite or game memory.
    No standard amounts, names, defaults, two-second waits, or auto-learning.
    """

    def __init__(self, knowledge, rules):
        self.knowledge = knowledge
        self.rules = rules
        self._current = None
        self._known = {}
        knowledge.db.execute('''CREATE TABLE IF NOT EXISTS learned_supply_v1(
            kind INTEGER NOT NULL CHECK(kind >= 0 AND kind < 228),
            raw INTEGER NOT NULL CHECK(raw >= 0 AND raw <= 255),
            amount INTEGER NOT NULL CHECK(amount = ((raw + 1) >> 1)),
            PRIMARY KEY(kind,raw)) WITHOUT ROWID''')
        knowledge.commit()
        self.snapshot()

    def snapshot(self):
        """Private current-map applicability bytes, never a public knowledge list."""
        current = _table(self.rules.read(SUPPLY_PROVIDED_VA, UNIT_TYPE_COUNT))
        if current != self._current:
            # CROSS JOIN fixes the tiny current-map side as the outer loop;
            # learned facts are sought through their composite primary key.
            placeholders = ','.join('(?,?)' for _ in range(UNIT_TYPE_COUNT))
            parameters = tuple(item for kind, raw in enumerate(current) for item in (kind, raw))
            if getattr(self.knowledge, '_shared_attached', False):
                learned_source = (
                    '(SELECT kind,raw,amount FROM main.learned_supply_v1 '
                    'UNION SELECT kind,raw,amount FROM shared.learned_supply_v1) AS learned'
                )
            else:
                learned_source = 'learned_supply_v1 AS learned'
            rows = self.knowledge.db.execute(
                f'WITH current_map(kind,raw) AS (VALUES {placeholders}) '
                f'SELECT learned.kind,learned.raw,learned.amount FROM current_map '
                f'CROSS JOIN {learned_source} '
                'WHERE learned.kind=current_map.kind AND learned.raw=current_map.raw',
                parameters).fetchall()
            known = {}
            for kind, raw, amount in rows:
                if (_kind(kind) != kind or _raw(raw) != raw or current[kind] != raw
                        or type(amount) is not int or amount != (raw + 1) >> 1):
                    raise RuntimeError('invalid persisted learned supply fact')
                known[kind] = (raw, amount)
            self._known, self._current = known, current
        return self._current

    def learn(self, display_kind, raw):
        """Persist only an authorized own-selected UI-normalized observation."""
        kind, raw = _kind(display_kind), _raw(raw)
        amount = (raw + 1) >> 1
        try:
            self.knowledge.db.execute(
                'INSERT OR REPLACE INTO learned_supply_v1(kind,raw,amount) VALUES(?,?,?)',
                (kind, raw, amount))
            if getattr(self.knowledge, '_shared_attached', False):
                self.knowledge.db.execute(
                    'INSERT OR REPLACE INTO shared.learned_supply_v1(kind,raw,amount) VALUES(?,?,?)',
                    (kind, raw, amount))
            self.knowledge.commit()
        except BaseException:
            self.knowledge.db.rollback()
            raise
        # Only publish after durable commit. Other map profiles remain on disk.
        if self._current is not None and self._current[kind] == raw:
            self._known[kind] = (raw, amount)
        return amount

    def value(self, kind, snapshot):
        """Cache-only learned amount if this private snapshot's raw byte matches."""
        kind = _kind(kind)
        current = _table(snapshot)
        fact = self._known.get(kind)
        return fact[1] if fact is not None and fact[0] == current[kind] else None
