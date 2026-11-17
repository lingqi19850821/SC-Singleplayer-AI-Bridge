"""Current own resource totals and the supply rows shown by the 1.05 HUD.

This is a read-only decoder for an already connected, healthy bridge. It never
submits a mailbox request, waits for detail learning, or changes game state.
Only the current local human player's columns are read. Supply follows the HUD
race order, visibility test, cap and integer rounding; private half-unit values
and capacity beyond the HUD cap never cross the public boundary.
"""
import struct

from tools.stage2_readprobe import ProbeError, path_key


# These preferred-image addresses are proved from the actual 1.05 executable,
# not a Brood War layout. See evidence/readiness-20261111/resources/mapping.md.
_IMAGE_BASE = 0x400000
_LOCAL_PLAYER = 0x4F30C0
_PLAYER_CONTROLLER = 0x6509E0
_MINERALS = 0x4FEE58
_GAS = 0x4FEE88
_ACTIVE = 0x63C548
_TILES = 0x63C568
_FRAME = 0x4FEFA4
_PAUSE = 0x653940
_SUPPLY = (
    ('zerg', 0x501EAC, 0x501EDC, 0x501F0C),
    ('terran', 0x501F3C, 0x501F6C, 0x501F9C),
    ('protoss', 0x501FCC, 0x501FFC, 0x50202C),
)

# Small read-only layout guards for the independently inspected target paths.
# A mismatch returns no resource data. No fingerprint or executable hash is
# used. The fixed-image EXE is required; these absolute operands are not guessed
# for another version or relocation.
_LAYOUT_ANCHORS = (
    (0x4AA12D, bytes.fromhex('8a15c0304f0032c98bf0e8444ef6ff8a15c0304f0032c98bf8e8a54df6ff6685c07410')),
    (0x4AA160, bytes.fromhex('8a15c0304f00b101e8134ef6ff8a15c0304f00b1018bf8e8744df6ff6685c0bb05000000740d')),
    (0x4AA193, bytes.fromhex('8a15c0304f00b102e8e04df6ff8a15c0304f00b1028bf8e8414df6ff6685c07410')),
    (0x4AA1C4, bytes.fromhex('a1c0304f00660fb6154aef4f008b0c8588ee4f00425168d0d268008bcee80a0100008b15c0304f008b368bce8b049558ee4f0033d25068d4d26800e8ec000000')),
    (0x40EF15, bytes.fromhex('8b44240025ff000000c1e0028b88cc1f50008b802c2050003bc873468bc1')),
    (0x40EF37, bytes.fromhex('8b44240025ff000000c1e0028b883c1f50008b809c1f50003bc873248bc1')),
    (0x40EF59, bytes.fromhex('8b44240025ff000000c1e0028b88ac1e50008b800c1f50003bc873028bc183c408c3')),
    (0x40EFA5, bytes.fromhex('8b44240025ff0000008b0485fc1f500083c408c38b4c240081e1ff0000008b048d6c1f500083c408c38b54240081e2ff0000008b0495dc1e500083c408c3')),
    (0x4AA23E, bytes.fromhex('8b44241825ffff000040992bc28bf88b44241425ffff0000d1ff408bdf992bc281e3ffff0000d1f88bd0')),
)
_BRIDGE_GUARD = ('epoch', 'game_base', 'active', 'tile_pointer', 'pause',
                 'hook_installed', 'state', 'serial', 'completed_serial',
                 'action_count')


class _SnapshotRejected(Exception):
    def __init__(self, reason, status='rejected'):
        self.reason, self.status = reason, status


def _identity(metadata):
    pid, creation = metadata['pid'], metadata['creation_time_filetime']
    if type(pid) is not int or pid <= 0 or type(creation) is not int or creation <= 0:
        raise _SnapshotRejected('different_process')
    return pid, creation, path_key(metadata['path'])


def _state_gate(state, expected_epoch):
    if state['epoch'] != expected_epoch:
        raise _SnapshotRejected('stale_epoch')
    if state['hook_installed'] != 1:
        raise _SnapshotRejected('adapter_not_ready', 'unavailable')
    if state['state'] in (1, 2):
        raise _SnapshotRejected('busy')
    if state['state'] not in (0, 3):
        raise _SnapshotRejected('invalid_resource_state', 'unavailable')
    if state['serial'] != state['completed_serial']:
        raise _SnapshotRejected('busy')
    if state['game_base'] != _IMAGE_BASE:
        raise _SnapshotRejected('unsupported_resource_layout', 'unavailable')
    if state['active'] != 1 or not state['tile_pointer']:
        raise _SnapshotRejected('no_game', 'unavailable')
    if state['pause']:
        raise _SnapshotRejected('paused', 'unavailable')


def _native_guard(reader):
    def dword(address):
        return struct.unpack('<I', reader.read(address, 4))[0]
    active = reader.read(_ACTIVE, 1)[0]
    tiles, pause, frame, local = (dword(address) for address in
                                  (_TILES, _PAUSE, _FRAME, _LOCAL_PLAYER))
    # The original game has eight playing slots. Neutral/unused/observer-like
    # indexes never become an alternate player's resource lookup.
    if local >= 8:
        raise _SnapshotRejected('no_local_human_player', 'unavailable')
    controller = reader.read(_PLAYER_CONTROLLER + 36 * local, 1)[0]
    if controller != 2:
        raise _SnapshotRejected('no_local_human_player', 'unavailable')
    return active, tiles, pause, frame, local, controller


def _totals(reader, local):
    def own_dword(column):
        return struct.unpack('<I', reader.read(column + 4 * local, 4))[0]
    return (own_dword(_MINERALS), own_dword(_GAS),
            tuple(tuple(own_dword(column) for column in columns)
                  for _, *columns in _SUPPLY))


def _public_supply(supply):
    rows = []
    for (race, *_), (provided, used, limit) in zip(_SUPPLY, supply):
        # 40EEF0 returns unsigned min(provided, limit), and 4AA120 tests AX.
        capacity = min(provided, limit) & 0xffff
        if not capacity:
            continue
        # 4AA230 masks to 16 bits, then rounds each half-unit value UP.
        shown_used = ((used & 0xffff) + 1) // 2
        shown_capacity = (capacity + 1) // 2
        rows.append({'race': race, 'used': shown_used,
                     'capacity': shown_capacity,
                     'available': max(0, shown_capacity - shown_used),
                     'over_capacity': max(0, shown_used - shown_capacity)})
    return rows


def resource_snapshot(client, expected_epoch):
    """Read current own totals without an action or a native request.

    Minerals/gas are the authoritative current totals supplied to the HUD; its
    on-screen number animation may momentarily lag. Supply is exactly the HUD's
    visible integer rows (including its per-race cap and upward rounding), never
    a cross-race sum. `available` and `over_capacity` are differences between
    those displayed integers, not an additional fractional gameplay allowance.

    Menus/inactive games, pause, detached bridges and non-human/invalid local
    slots are unavailable. Epoch/process changes, in-flight requests, lifecycle
    drift, frame changes or differing repeated totals return no partial data.
    No automatic retry, pause, selection, camera, learning or action occurs.
    """
    if type(expected_epoch) is not int or not 0 < expected_epoch < 2**32:
        raise ValueError('valid expected epoch required')
    try:
        identity = _identity(client.process)
        if _identity(client.reader.metadata()) != identity:
            raise _SnapshotRejected('different_process')
        before = client.peek()
        _state_gate(before, expected_epoch)
        for address, expected in _LAYOUT_ANCHORS:
            if client.reader.read(address, len(expected)) != expected:
                raise _SnapshotRejected('unsupported_resource_layout', 'unavailable')
        native_before = _native_guard(client.reader)
        active, tiles, pause, frame, local, _ = native_before
        if active != 1 or not tiles:
            raise _SnapshotRejected('no_game', 'unavailable')
        if pause:
            raise _SnapshotRejected('paused', 'unavailable')
        if tiles != before['tile_pointer'] or frame < before['raw_counter']:
            raise _SnapshotRejected('observation_changed_during_read')
        first = _totals(client.reader, local)
        second = _totals(client.reader, local)
        native_after = _native_guard(client.reader)
        after = client.peek()
        _state_gate(after, expected_epoch)
        if _identity(client.reader.metadata()) != identity:
            raise _SnapshotRejected('different_process')
        if (native_after != native_before or first != second or
                after['raw_counter'] > native_after[3] or
                any(after[key] != before[key] for key in _BRIDGE_GUARD)):
            raise _SnapshotRejected('observation_changed_during_read')
        return {'status': 'observed', 'minerals': first[0], 'gas': first[1],
                'supply': _public_supply(first[2])}
    except _SnapshotRejected as error:
        return {'status': error.status, 'reason': error.reason}
    except (ProbeError, OSError, ValueError, KeyError, IndexError, struct.error):
        # Do not forward a Win32 diagnostic containing addresses or partial
        # buffers onto the player surface.
        return {'status': 'unavailable', 'reason': 'resource_read_failed'}
