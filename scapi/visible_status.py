"""Current visible selected-panel status text; no hidden status-bit inference.

Target 1.05 evidence: evidence/ammunition-panel-20261114/status-static.
The caller supplies an already lawful single-panel snapshot and its existing
PanelText budget. This reader does not learn new skills or expose timer bits.
"""
import struct

from .panel_card import TextBudgetRequired
from .observation_budget import ObservationReadBudget


def visible_panel_status(reader, state, text, *, _budget=None):
    """Return the drawn status string, or None when its control is absent.

    The native renderer owns localization and status precedence. In particular,
    foreign hallucination identity is never inferred from the raw unit flags.
    A stale own-only hallucination text pointer is suppressed for foreign units.
    RuntimeError means the sample changed or its source was malformed.
    TextBudgetRequired retains the caller's existing explicit budget contract.
    """
    if not state['panel_available'] or state['selection_count'] != 1:
        return None
    reader = (_budget or ObservationReadBudget()).reader(reader)
    delta = state['game_base'] - 0x400000
    changed = 'visible status observation changed during read'

    def word(address):
        return struct.unpack('<H', reader.read(address, 2))[0]

    def dword(address):
        return struct.unpack('<I', reader.read(address, 4))[0]

    def identity(pointer):
        return (word(pointer + 0x64), reader.read(pointer + 0x4C, 1)[0],
                reader.read(pointer + 0xA5, 1)[0])

    def control(raw):
        return (struct.unpack_from('<I', raw)[0],
                struct.unpack_from('<H', raw, 0x20)[0],
                struct.unpack_from('<I', raw, 0x18)[0] & 8,
                struct.unpack_from('<I', raw, 0x14)[0])

    primary = dword(0x68D2F8 + delta)
    selected = reader.read(0x684940 + delta, 48)
    first = 0x5B2E68 + delta
    if (not first <= primary < first + 1700 * 0x148 or (primary - first) % 0x148
            or struct.unpack('<12I', selected) != (primary,) + (0,) * 11
            or reader.read(0x68C9DC + delta, 1) != b'\0'):
        raise RuntimeError(changed)
    actor = identity(primary)
    local = dword(0x4F30C0 + delta)
    if (actor[:2] != (state['panel_type'], state['panel_owner'])
            or actor[0] >= 228 or actor[1] >= 12 or local >= 12
            or bool(state['panel_owned']) != (actor[1] == local)):
        raise RuntimeError(changed)

    original_root = dword(0x68CA68 + delta)
    if not original_root:
        return None
    root_kind = word(original_root + 0x22)
    root = dword(original_root + 0x32) if root_kind else original_root
    if not root:
        return None
    head = dword(root + 0x42)
    node = head
    seen = set()
    stamp = []
    pointer = 0
    while node:
        if node in seen:
            raise RuntimeError('cyclic visible status control list')
        seen.add(node)
        fields = control(reader.read(node, 0x26))
        stamp.append((node, fields))
        next_node, identifier, visible, text_pointer = fields
        if identifier == 0xFFEC:  # native signed control id -20
            if visible:
                pointer = text_pointer
            break  # same first-match lookup as the native 0x4D69F0
        node = next_node

    table_stamp = None
    if pointer and actor[1] != local:
        # 4A4A48 -> 4AA3D0 resolves own hallucination text id 0x340 from
        # this loaded table. Do not guess an English/localized translation.
        table = dword(0x68D344 + delta)
        if not table:
            raise RuntimeError('missing visible status string table')
        count = word(table)
        if count < 0x340:
            raise RuntimeError('missing own-only status label')
        offset = word(table + 2 * 0x340)
        if offset < 2 + 2 * count:
            raise RuntimeError('invalid own-only status label')
        table_stamp = (table, count, offset)
        if pointer == table + offset:
            pointer = 0

    source = []
    result = None
    if pointer:
        raw_text = bytearray()
        address = pointer
        while text.remaining:
            count = min(128, text.remaining, 4096 - address % 4096)
            part = reader.read(address, count)
            end = part.find(b'\0')
            used = end + 1 if end >= 0 else len(part)
            if not used:
                raise RuntimeError('empty visible status source read')
            text.remaining -= used
            source.append((address, part[:used]))
            raw_text.extend(part[:end] if end >= 0 else part)
            if end >= 0:
                try:
                    result = bytes(b for b in raw_text if b >= 32 or b in (9, 10)).decode(
                        text.encoding, errors='strict') or None
                except UnicodeError as exc:
                    raise RuntimeError('invalid visible status encoding') from exc
                break
            address += used
        else:
            raise TextBudgetRequired()

    if (dword(0x68D2F8 + delta) != primary
            or reader.read(0x684940 + delta, 48) != selected
            or reader.read(0x68C9DC + delta, 1) != b'\0'
            or identity(primary) != actor or dword(0x4F30C0 + delta) != local
            or dword(0x68CA68 + delta) != original_root
            or word(original_root + 0x22) != root_kind
            or (root_kind and dword(original_root + 0x32) != root)
            or dword(root + 0x42) != head
            or any(control(reader.read(address, 0x26)) != fields for address, fields in stamp)
            or any(reader.read(address, len(raw)) != raw for address, raw in source)):
        raise RuntimeError(changed)
    if table_stamp is not None:
        table, count, offset = table_stamp
        if (dword(0x68D344 + delta) != table or word(table) != count
                or word(table + 2 * 0x340) != offset):
            raise RuntimeError(changed)
    return result
