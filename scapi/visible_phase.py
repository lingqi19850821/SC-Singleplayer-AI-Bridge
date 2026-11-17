"""Fail-closed host projection of the optional native modal diagnostics.

The v50 observer has no verified presentation boundary. No modal candidate or
raw result identity is exposed here, even if a caller supplies nonzero phase
or capability bits. A new verified implementation must change this projector
and its acceptance evidence before any visible victory notification exists.
"""
from tools.stage5_visible_phase_client import PHASE_FIELDS


def project_visible_phase(before, after, *, pid, process_creation, epoch,
                          now_tick_ms, max_age_ms):
    if type(max_age_ms) is not int or not 0 < max_age_ms < 2**31:
        raise ValueError('max_age_ms must be an explicit positive signed-32-bit duration')
    result = {'available': False, 'phase': 'unknown', 'fresh': False,
              'reason': 'native_observer_unavailable', 'source': 'native_modal_observer'}
    if not all(name in before and name in after for name in PHASE_FIELDS):
        return result
    result['reason'] = 'incoherent_observation'
    guard = before['phase_sample_serial']
    if (not guard or guard & 1 or guard != after['phase_sample_serial'] or
            any(before[name] != after[name] for name in PHASE_FIELDS) or
            any(before.get(name) != after.get(name)
                for name in ('epoch', 'active', 'tile_pointer', 'hook_installed'))):
        return result
    result['reason'] = 'stale_epoch'
    if before['epoch'] != epoch or before['phase_sample_epoch'] != epoch:
        return result
    result['reason'] = 'observer_inactive'
    if (before.get('hook_installed') != 1 or after.get('hook_installed') != 1 or
            not after.get('active') or not after.get('tile_pointer')):
        return result
    age = (now_tick_ms - before['phase_sample_tick']) & 0xffffffff
    result['reason'] = 'stale_observation'
    if age > max_age_ms:
        return result
    result.update(fresh=True, reason='visible_boundary_unverified', observation={
        'pid': pid, 'process_creation': process_creation, 'epoch': epoch,
        'sample_serial': guard, 'sample_tick_ms': before['phase_sample_tick'],
        'age_ms': age})
    return result
