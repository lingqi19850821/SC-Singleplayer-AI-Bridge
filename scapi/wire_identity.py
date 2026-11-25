"""Exact process identities at the public JSON boundary; typed APIs keep ints."""

UINT64_MAX = 2**64 - 1
_IDENTITY = frozenset(('pid', 'process_creation', 'epoch'))
_SELECTION = _IDENTITY | {'generation', 'count'}
_SHAPES = {_IDENTITY, _SELECTION,
           _SELECTION | {'card_generation', 'slot'},
           _SELECTION | {'target_generation', 'queued_order'},
           _SELECTION | {'queue_generation'},
           _SELECTION | {'cargo_generation', 'slot'}}
_TICKET_KEYS = ('selection', 'ticket', 'button', 'target', 'production', 'passenger')
_TICKET_SOURCES = {'current_selection', 'selected_panel', 'command_card',
                   'placement_preview', 'session', 'observe_view', 'own_units'}
_SELECTION_OPS = {'selected_panel', 'command_card', 'move_selected', 'right_click',
    'move_minimap', 'attack_move', 'attack_move_minimap', 'patrol', 'patrol_minimap',
    'assign_group', 'focus_selected', 'halt_construction', 'resume_construction',
    'cancel_construction', 'cancel_research', 'cancel_upgrade', 'cancel_morph', 'cancel_addon'}
_REQUEST_KEYS = {**dict.fromkeys(_SELECTION_OPS, 'ticket'),
    'activate_button': 'button', 'target_at': 'target', 'placement_preview': 'ticket',
    'cancel_production': 'production', 'unload_one': 'passenger'}


def creation_integer(value):
    """Accept exact Python uint64 or canonical ASCII decimal, never approximation."""
    if isinstance(value, str):
        if (not 1 <= len(value) <= 20 or not value.isascii() or not value.isdecimal()
                or (len(value) > 1 and value[0] == '0')):
            raise ValueError('canonical process_creation required')
        value = int(value)
    if type(value) is not int or not 0 <= value <= UINT64_MAX:
        raise ValueError('uint64 process_creation required')
    return value


def normalize_identity(value):
    """Known ticket/session path only; preserve exact later validation of all fields."""
    if not isinstance(value, dict) or not _IDENTITY <= value.keys():
        raise ValueError('complete reference session required')
    return {**value, 'process_creation': creation_integer(value['process_creation'])}


_CHAIN_REF_SENTINELS = frozenset(('$prev_selection', '$prev_target'))


def decode_request(request):
    """Normalize only the executable identity argument belonging to this operation."""
    if (not isinstance(request, dict) or not isinstance(request.get('op'), str)
            or not isinstance(request.get('args'), dict)):
        return request
    if request['op'] == 'action_chain':
        steps = request['args'].get('steps')
        if not isinstance(steps, list):
            return request
        decoded_steps = []
        for step in steps:
            if (isinstance(step, dict) and isinstance(step.get('op'), str)
                    and isinstance(step.get('args'), dict)):
                step_key = _REQUEST_KEYS.get(step['op'])
                if step_key is not None and step_key in step['args']:
                    value = step['args'][step_key]
                    if not (isinstance(value, str) and value in _CHAIN_REF_SENTINELS):
                        step = {**step, 'args': {**step['args'], step_key: normalize_identity(value)}}
            decoded_steps.append(step)
        return {**request, 'args': {**request['args'], 'steps': decoded_steps}}
    key = _REQUEST_KEYS.get(request.get('op'))
    if key is None or key not in request['args']:
        return request
    return {**request, 'args': {**request['args'], key: normalize_identity(request['args'][key])}}


def _encode_identity(value):
    if isinstance(value, dict) and frozenset(value) in _SHAPES:
        return {**value, 'process_creation': str(creation_integer(value['process_creation']))}
    return value


def _encode_result(value):
    if not isinstance(value, dict):
        return value
    result = dict(_encode_identity(value))
    for key in _TICKET_KEYS:
        if key in result:
            result[key] = _encode_identity(result[key])
    for key, ticket_key in (('buttons', 'button'), ('cargo', 'passenger')):
        if isinstance(result.get(key), list):
            result[key] = [{**row, ticket_key: _encode_identity(row[ticket_key])}
                           if isinstance(row, dict) and ticket_key in row else row
                           for row in result[key]]
    if isinstance(result.get('steps'), list):
        result['steps'] = [{**row, 'result': _encode_result(row['result'])}
                           if isinstance(row, dict) and 'result' in row else row
                           for row in result['steps']]
    return result


def _encode_record(value):
    if not isinstance(value, dict):
        return value
    result = dict(value)
    source = value.get('source')
    if isinstance(source, str) and source in _TICKET_SOURCES and 'result' in value:
        result['result'] = _encode_result(value['result'])
    if 'observed_selection' in value:
        result['observed_selection'] = _encode_identity(value['observed_selection'])
    return result


def encode_public_frame(value):
    """Clone known public frame paths; arbitrary experience contents stay untouched."""
    if not isinstance(value, dict):
        return value
    result = dict(value)
    if value.get('type') in ('information', 'context_recovery'):
        if isinstance(value.get('records'), list):
            result['records'] = [_encode_record(row) for row in value['records']]
    elif value.get('type') == 'response':
        if 'result' in value:
            result['result'] = _encode_result(value['result'])
        if isinstance(value.get('feedback'), dict):
            feedback = _encode_result(value['feedback'])
            for key in ('selection', 'command_card', 'placement_preview'):
                entry = feedback.get(key)
                if isinstance(entry, dict):
                    feedback[key] = ({**entry, 'result': _encode_result(entry['result'])}
                                     if 'result' in entry else _encode_result(entry))
            result['feedback'] = feedback
    elif value.get('type') == 'context' and 'session' in value:
        result['session'] = _encode_identity(value['session'])
    return result
