"""Public projection of target-1.05 native nuclear observations.

The native per-frame marker source owns permission and three-second screen
attention. The event journal owns identity, persistence and delivery. This
module never infers markers from an action receipt, warning or cached target.
"""


def nuclear_event(kind, *, color, x, y):
    """Return only the allowed public fields of one captured nuclear event.

    Marker color encodes a capture-local public serial and an own/not-own bit;
    it contains no raw actor identity, player index, pointer or native slot.
    No-longer-current is not a claim of cancellation, impact or destruction.
    """
    if kind == 12:
        if (color, x, y) != (0, 0, 0):
            raise ValueError('invalid captured nuclear warning')
        return {'kind': 'nuclear_launch_warning'}
    if (kind not in (10, 11) or type(color) is not int or not 2 <= color <= 0xFFFFFFFF):
        raise ValueError('invalid captured nuclear marker identity')
    result = {'kind': 'nuclear_marker_seen' if kind == 10 else 'nuclear_marker_no_longer_current',
              'marker_token': 'nuclear:' + str(color >> 1),
              'relation': 'own' if color & 1 else 'other'}
    if kind == 10:
        if type(x) is not int or type(y) is not int or x < 0 or y < 0:
            raise ValueError('invalid captured nuclear marker position')
        result['position'] = {'x': x, 'y': y}
    elif (x, y) != (0, 0):
        raise ValueError('invalid captured nuclear stale position')
    return result
