"""Trusted startup context assembled without native reads or caller paths."""
from .view_bookmarks import SLOTS


# Fixed public limits enforced by actions.py/stage5.py and lifecycle.py.
SELECTION_MAX_UNITS = 12
GROUP_SLOTS = tuple(range(10))
SAVE_COOLDOWN_SECONDS = 300
ATTENTION_FIELDS = {'view':'view_wait_ms', 'dynamic':'dynamic_wait_ms',
                    'detail':'details_wait_ms', 'contact':'contact_wait_ms'}


def _duration(snapshot, key, *, allow_zero=False):
    value = snapshot.get(key)
    if type(value) is not int or not (0 if allow_zero else 1) <= value < 2**31:
        raise ValueError('current native setting unavailable: ' + key)
    return value


def startup_context(snapshot, runtime, lifecycle, *, configured_interval_ms):
    """Use only the launcher's existing verified health snapshot."""
    configured = _duration({'configured_interval_ms':configured_interval_ms}, 'configured_interval_ms')
    return {'type':'context', 'schema':'scapi.player-context.v1',
            'reference':{'tool':'scapi_reference','source':'docs/API_REFERENCE.md',
                         'usage':'Known usage: act directly. Otherwise query scapi_reference: manual for capabilities/syntax, knowledge for learned facts, terrain for explored static tiles, tips for offered Tips. Game information and learning remain automatic.'},
            'settings':{
                'actions':{'configured_interval_ms':configured,
                           'native_pre_arm_interval_ms':_duration(snapshot, 'action_interval_ms', allow_zero=True),
                           'applies_when':'armed'},
                'attention_ms':{name:_duration(snapshot, field) for name,field in ATTENTION_FIELDS.items()},
                'lifecycle':{'capabilities':list(runtime.capabilities),
                             'save_cooldown_seconds':SAVE_COOLDOWN_SECONDS,
                             'save_cooldown_remaining_seconds':lifecycle.remaining_seconds()},
                'information':{'delivery':'automatic', 'learning':'automatic',
                               'queries_required':False, 'paging_owned_by':'host'},
                'tutorial':{'restricted':runtime._tutorial_depot_limit is not None,
                            'depot_total_limit':runtime._tutorial_depot_limit,
                            'new_depot_intent_limit':runtime._tutorial_new_depot_intents},
                'selection':{'max_units':SELECTION_MAX_UNITS},
                'groups':{'slots':list(GROUP_SLOTS)},
                'view_bookmarks':{'slots':list(SLOTS)}}}


def validate_armed_settings(snapshot, settings):
    if _duration(snapshot, 'action_interval_ms') != settings['actions']['configured_interval_ms']:
        raise ValueError('armed action interval differs from delivered settings')
    if any(_duration(snapshot, field) != settings['attention_ms'][name]
           for name,field in ATTENTION_FIELDS.items()):
        raise ValueError('armed attention differs from delivered settings')
