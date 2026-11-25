"""Single-consumer automatic lawful information and durable delivery.

Only PlayerSession's main worker calls this module. No mailbox or SQLite
connection is shared with the stdin reader or model transport.
"""
import json
import hashlib
import inspect
import math
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone
from .terrain_reference import build_screen_terrain


INFORMATION_OPS = frozenset(('observe_view', 'terrain_view', 'own_units',
    'build_sites', 'resource_sites',
    'current_selection', 'selected_panel', 'resources', 'ui_counters', 'state', 'session',
    'minimap_geometry', 'remembered_buildings', 'building_memory_status',
    'events', 'events_head', 'list_views', 'get_view', 'command_card',
    'placement_preview', 'tips_pending', 'experience_read', 'lifecycle_status',
    'menu_status', 'list_saves', 'objectives'))

# These are the existing host page/read budgets, not truncation or total caps.
# Exact required-byte replies grow a read; native entity capacity is 1700.
PAGE_ROWS = 64
TEXT_BYTES = 65536
UNIT_CAPACITY = 1700
SELECTION_ACTIONS = frozenset(('select_at','select_box','select_same_type','select_group'))
COMPASS_8 = ('E','SE','S','SW','W','NW','N','NE')
# Coarse derived-stationary buckets. A stationary unit therefore contributes at
# most seven sparse row patches over ten minutes, never a patch every scan.
STATIONARY_BUCKETS_S = (15, 30, 60, 120, 180, 300, 600)


def _stationary_bucket_s(ms):
    """Largest coarse bucket not above the observed duration, or None."""
    if type(ms) is not int or ms < 15000:
        return None
    seconds = ms // 1000
    bucket = None
    for candidate in STATIONARY_BUCKETS_S:
        if candidate > seconds:
            break
        bucket = candidate
    return bucket


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def _stable(value):
    if isinstance(value, dict):
        return {k: _stable(v) for k, v in value.items() if k not in
                ('observed_tick_ms', 'observed_frame', 'received_monotonic_ns', 'last_seen_tick_ms')}
    if isinstance(value, list):
        return [_stable(v) for v in value]
    return value


def _pointer_part(value):
    return str(value).replace('~', '~0').replace('/', '~1')


def _view_patch(before, after):
    """Lossless dictionary/array difference using RFC 6901 field pointers.

    Arrays whose length changes are replaced as one value. Equal-length arrays
    retain order and are compared by index. A set value replaces its entire
    addressed value; remove only addresses absent dictionary keys.
    """
    changed, removed = {}, []

    def visit(old, new, pointer):
        if type(old) is not type(new):
            changed[pointer] = deepcopy(new)
        elif isinstance(new, dict):
            for key in old:
                if key not in new:
                    removed.append(pointer + '/' + _pointer_part(key))
            for key, value in new.items():
                child = pointer + '/' + _pointer_part(key)
                if key not in old:
                    changed[child] = deepcopy(value)
                else:
                    visit(old[key], value, child)
        elif isinstance(new, list):
            if len(old) != len(new):
                changed[pointer] = deepcopy(new)
            else:
                for index, value in enumerate(new):
                    visit(old[index], value, pointer + '/' + str(index))
        elif _json(old) != _json(new):
            # JSON scalar spelling distinguishes bool/int/float and +/- zero.
            changed[pointer] = new

    visit(before, after, '')
    return {'set': changed, 'remove': removed}


def public_page(value):
    return {k: v for k, v in value.items() if k not in
            ('next_cursor', 'checkpoint', 'has_more', 'text_budget_bytes', 'text_bytes')}


def text_read(method, **args):
    """Complete a host-internal text read; never truncate or publish budgets."""
    budget = TEXT_BYTES
    while True:
        result = method(text_budget_bytes=budget, **args)
        partial_panel = result.get('panel', {}).get('name_status') == 'larger_read_budget_required'
        if result.get('status') not in ('budget_required', 'larger_text_budget_required') and not partial_panel:
            return result
        needed = max((result.get(k, 0) for k in
            ('required_bytes', 'required_bytes_at_least', 'required_text_budget_bytes')),
            default=0)
        budget = max(needed, budget * 2)


class DeliveryOutbox:
    """Persist information before emitting it; receipt is separate from flush.

    Received-but-unprocessed rows survive a crash between receipt and Tip/name
    effects. Reapplying those effects is idempotent. No command is ever stored
    for replay.
    """
    def __init__(self, db, profile):
        self.db, self.profile = db, profile
        db.execute('''CREATE TABLE IF NOT EXISTS automatic_delivery_v1(
            id INTEGER PRIMARY KEY, profile TEXT NOT NULL, message TEXT NOT NULL,
            effects TEXT NOT NULL, received INTEGER NOT NULL DEFAULT 0,
            processed INTEGER NOT NULL DEFAULT 0)''')
        db.execute('''CREATE TABLE IF NOT EXISTS automatic_cursor_v1(
            profile TEXT NOT NULL, source TEXT NOT NULL, value TEXT NOT NULL,
            PRIMARY KEY(profile,source))''')
        db.execute('CREATE INDEX IF NOT EXISTS automatic_delivery_pending_v1 '
                   'ON automatic_delivery_v1(profile,processed,id)')
        db.execute('''CREATE TABLE IF NOT EXISTS automatic_handoff_v1(
            profile TEXT NOT NULL, receipt TEXT NOT NULL, delivery INTEGER NOT NULL,
            PRIMARY KEY(profile,receipt))''')
        db.execute('''CREATE TABLE IF NOT EXISTS automatic_reference_handoff_v1(
            profile TEXT NOT NULL, identity TEXT NOT NULL, scope TEXT NOT NULL,
            run_ref TEXT NOT NULL, source TEXT NOT NULL, delivery INTEGER NOT NULL,
            PRIMARY KEY(profile,identity))''')
        self.reference_knowledge = None
        db.commit()

    def cursor(self, source):
        row = self.db.execute('SELECT value FROM automatic_cursor_v1 WHERE profile=? AND source=?',
                              (self.profile, source)).fetchone()
        return json.loads(row[0]) if row else None

    def advance(self, values):
        """Empty source pages carry no undelivered public data."""
        with self.db:
            for key,value in values.items():
                self.db.execute('INSERT OR REPLACE INTO automatic_cursor_v1 VALUES(?,?,?)',
                                (self.profile,key,_json(value)))

    def enqueue(self, message, effects=None, advances=None, handoff_receipts=None):
        receipts=[str(value) for value in (handoff_receipts or [])]
        reference_ack_ids=[item['identity'] for record in message.get('records',[])
                           if isinstance(record,dict)
                           for item in record.get('_reference_notices',[])]
        if receipts:
            existing=[self.db.execute('SELECT delivery FROM automatic_handoff_v1 WHERE profile=? AND receipt=?',
                                     (self.profile,value)).fetchone() for value in receipts]
            if all(existing):
                self._ack_reference_notices(message)
                return str(existing[0][0])
            if any(existing):
                raise RuntimeError('partially handed-off observation batch requires reconciliation')
        with self.db:
            message, effects, reference_ids = self._reference_notices(message,effects)
            # Compare only against durably queued panels. A failed transaction
            # must not make a later retry look as if its panel was delivered.
            message, effects, panel_cursor, suppressed_only = self._completed_panels(message, effects)
            cur = self.db.execute('INSERT INTO automatic_delivery_v1(profile,message,effects,processed) VALUES(?,?,?,?)',
                                 (self.profile, _json(message), _json(effects), int(suppressed_only)))
            if panel_cursor is not None:
                self.db.execute('INSERT OR REPLACE INTO automatic_cursor_v1 VALUES(?,?,?)',
                                (self.profile,'completed_panel_semantics_v1',_json(panel_cursor)))
            for key, value in (advances or {}).items():
                self.db.execute('INSERT OR REPLACE INTO automatic_cursor_v1 VALUES(?,?,?)',
                                (self.profile, key, _json(value)))
            for value in receipts:
                self.db.execute('INSERT INTO automatic_handoff_v1 VALUES(?,?,?)',
                                (self.profile,value,cur.lastrowid))
            for item in reference_ids:
                self.db.execute('INSERT INTO automatic_reference_handoff_v1 VALUES(?,?,?,?,?,?)',
                                (self.profile,item['identity'],item['scope'],item['run_ref'],item['source'],cur.lastrowid))
        self._ack_reference_notices(identities=reference_ack_ids)
        return str(cur.lastrowid)

    def _ack_reference_notices(self, message=None, *, identities=None):
        if self.reference_knowledge is None:return
        if identities is None:
            identities=[item['identity'] for record in message.get('records',[])
                        if isinstance(record,dict)
                        for item in record.get('_reference_notices',[])]
        # On observation-receipt replay only notices with their own committed
        # handoff can be released. A newly learned revision must remain pending.
        durable=[value for value in identities if self.db.execute(
            'SELECT 1 FROM automatic_reference_handoff_v1 WHERE profile=? AND identity=?',
            (self.profile,value)).fetchone()]
        if durable:self.reference_knowledge.ack_reference_notices(durable)

    def _reference_notices(self, message, effects):
        """Atomically link source notices to this outbox, without wire metadata.

        The source queue and outbox use different SQLite connections. A crash
        after this transaction but before source cleanup replays the stable
        notice identity; it never creates a second public notice.
        """
        records=message.get('records',[])
        if not any(isinstance(row,dict) and '_reference_notices' in row for row in records):
            return message,effects or {},[]
        message,effects=deepcopy(message),deepcopy(effects or {})
        public=effects.get('public_results')
        if (not isinstance(public,list) or len(public)!=len(records) or
                any(not isinstance(row,dict) or row.get('op')!=item.get('source')
                    for row,item in zip(public,records))):
            raise RuntimeError('reference notice delivery effects are not aligned')
        kept=[];kept_effects=[];identities=[];new_notices=[];source_runs=set()
        only_notices=all(row.get('source')=='knowledge_updates' for row in records)
        for record,effect in zip(message['records'],public):
            notices=record.pop('_reference_notices',None)
            if notices is None:
                kept.append(record);kept_effects.append(effect);continue
            entries=record.get('result',{}).get('entries')
            if (record.get('source')!='knowledge_updates' or not isinstance(notices,list) or
                    not isinstance(entries,list) or len(entries)!=len(notices) or
                    effect.get('result')!=record.get('result')):
                raise RuntimeError('reference notice identities do not match public entries')
            retained=[]
            for item,entry in zip(notices,entries):
                if (not isinstance(item,dict) or set(item)!={'identity','scope','run_ref','source'} or
                        any(not isinstance(value,str) or not value for value in item.values())):
                    raise RuntimeError('invalid durable reference notice identity')
                source_runs.add(item['run_ref'])
                identity=item['identity']
                exists=self.db.execute('SELECT 1 FROM automatic_reference_handoff_v1 '
                    'WHERE profile=? AND identity=?',(self.profile,identity)).fetchone()
                if not exists and identity not in identities:
                    identities.append(identity);new_notices.append(item);retained.append(entry)
            if retained:
                record['result']['entries']=retained
                effect['result']=deepcopy(record['result'])
                kept.append(record);kept_effects.append(effect)
        if len(source_runs)!=1 or (not only_notices and message.get('run_ref') is not None and message['run_ref'] not in source_runs):
            raise RuntimeError('reference notices must retain their source run')
        if only_notices or message.get('run_ref') is None:message['run_ref']=next(iter(source_runs))
        message['records']=kept;effects['public_results']=kept_effects
        # A duplicate source handoff is internal work, never a model receipt.
        # No empty public message is emitted after source-cleanup recovery.
        return message,effects,new_notices

    def _completed_panels(self, message, effects):
        """Keep evidence/receipts for equivalent completions, without re-emitting.

        The private signature includes pre-projection facts and a host-stream
        identity. It is never sent to the model. One cursor is overwritten per
        profile; all prior pending deliveries retain their own receipt state.
        """
        records = message.get('records')
        if (message.get('type') != 'information' or not isinstance(records,list)
                or not any(isinstance(row,dict) and row.get('source') == 'selected_panel' for row in records)):
            internal=(message.get('type')=='information' and records==[] and
                      not (set(effects or {})-{'public_results'}))
            return message,effects or {},None,internal
        message, effects = deepcopy(message), deepcopy(effects or {})
        records = message['records']
        kept, kept_effects, suppressed = [], [], []
        public = effects.get('public_results')
        cursor = self.cursor('completed_panel_semantics_v1') or {}
        touched = False
        for index,record in enumerate(records):
            candidate = record.pop('_completed_panel',None) if isinstance(record,dict) else None
            omit = False
            if isinstance(record,dict) and record.get('source') == 'selected_panel':
                touched = True
                if candidate is not None:
                    if (not isinstance(public,list) or len(public) != len(records)
                            or any(not isinstance(row,dict) or row.get('op') != item.get('source')
                                   for row,item in zip(public,records))):
                        raise RuntimeError('completed panel delivery effects are not aligned')
                    signature = candidate['signature']
                    knowledge_changed=(candidate['knowledge_changed'] and
                        any(row.get('source')=='knowledge_updates' for row in records))
                    omit = not knowledge_changed and cursor.get('signature') == signature
                    cursor = {'signature':signature}
                else:
                    # Unavailable/foreign/ordinary panels also replace what the
                    # model last saw. Never suppress A after intervening B.
                    cursor = {}
            if omit:
                suppressed.append(record)
            else:
                kept.append(record)
                if isinstance(public,list):kept_effects.append(public[index])
        message['records'] = kept
        if not suppressed:
            return message,effects,cursor if touched else None,False
        # An internal disposition is NOT a model receipt. Keep received=0;
        # processed=1 prevents an empty information batch/false public effects.
        internal = not kept and not (set(effects)-{'public_results'})
        effects['public_results'] = kept_effects
        effects['suppressed_completed_panels'] = suppressed
        effects['suppression_reason'] = 'unchanged_completed_panel'
        return message,effects,cursor if touched else None,internal

    def pending(self):
        rows = self.db.execute('SELECT id,message,effects,received FROM automatic_delivery_v1 '
                              'WHERE profile=? AND processed=0 ORDER BY id LIMIT ?', (self.profile,PAGE_ROWS))
        return [(str(row[0]), json.loads(row[1]), json.loads(row[2]), bool(row[3])) for row in rows]

    def has_pending(self):
        return self.db.execute('SELECT 1 FROM automatic_delivery_v1 WHERE profile=? AND processed=0 LIMIT 1',
                               (self.profile,)).fetchone() is not None

    @staticmethod
    def _is_learning_only_message(message):
        if not isinstance(message, dict) or message.get('type') != 'information':
            return False
        records = message.get('records')
        if not isinstance(records, list) or not records:
            return False
        for record in records:
            if not isinstance(record, dict):
                return False
            source = record.get('source')
            if source in ('knowledge_updates', 'learned_observation', 'learning_status'):
                continue
            if source == 'selected_panel' and record.get('update') == 'append':
                continue
            return False
        return True

    def has_pending_non_learning(self):
        """True when a non-learning batch (startup/tips/world scan) awaits ack."""
        rows = self.db.execute('SELECT message FROM automatic_delivery_v1 '
                               'WHERE profile=? AND processed=0 ORDER BY id',
                               (self.profile,))
        for (raw,) in rows:
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                return True
            if not self._is_learning_only_message(message):
                return True
        return False

    def high_water(self):
        return self.db.execute('SELECT coalesce(max(id),0) FROM automatic_delivery_v1 WHERE profile=?',
                               (self.profile,)).fetchone()[0]

    def public_high_water(self):
        """Only model deliveries can participate in a model receipt barrier."""
        return self.db.execute('SELECT coalesce(max(id),0) FROM automatic_delivery_v1 '
                               'WHERE profile=? AND (processed=0 OR received=1)',
                               (self.profile,)).fetchone()[0]

    def pending_tips(self, phase=None):
        sql = ('SELECT 1 FROM automatic_delivery_v1 WHERE profile=? AND processed=0 '
               "AND coalesce(json_array_length(json_extract(effects,'$.tips')),0)>0")
        args = [self.profile]
        if phase is not None:
            sql += " AND json_extract(effects,'$.phase')=?"
            args.append(phase)
        return self.db.execute(sql+' LIMIT 1',args).fetchone() is not None

    def receive(self, identifier, omitted_records=None):
        if not isinstance(identifier, str) or not identifier.isdecimal():
            raise ValueError('matching internal delivery receipt required')
        mask=[] if omitted_records is None else omitted_records
        if not isinstance(mask,list):
            raise ValueError('omitted_records must be an exact record mask')
        indexes=set()
        for item in mask:
            if (not isinstance(item,dict) or set(item)!={'index','source','feedback_revision'}
                    or type(item['index']) is not int or item['index']<0
                    or item['source'] not in ('current_selection','command_card')
                    or type(item['feedback_revision']) is not int or item['feedback_revision']<0
                    or item['index'] in indexes):
                raise ValueError('invalid omitted record mask')
            indexes.add(item['index'])
        mask=sorted(mask,key=lambda item:item['index'])
        row = self.db.execute('SELECT effects,processed,message,received FROM automatic_delivery_v1 WHERE profile=? AND id=?',
                              (self.profile, identifier)).fetchone()
        if row is None:
            raise ValueError('unknown information delivery receipt')
        effects=json.loads(row[0])
        if row[1] or row[3]:
            if effects.get('omitted_records',[])!=mask:
                raise ValueError('receipt omission mask differs from durable receipt')
            return effects,bool(row[1])
        if mask:
            records=json.loads(row[2]).get('records',[])
            public=effects.get('public_results',[])
            if len(public)!=len(records):
                raise ValueError('receipt effects do not match original information records')
            for item in mask:
                index=item['index']
                if (index>=len(records) or records[index].get('source')!=item['source']
                        or records[index].get('feedback_revision')!=item['feedback_revision']
                        or public[index].get('op')!=item['source']):
                    raise ValueError('receipt omission does not match original record')
            effects['public_results']=[item for index,item in enumerate(public) if index not in indexes]
            effects['omitted_records']=mask
        with self.db:
            self.db.execute('UPDATE automatic_delivery_v1 SET received=1,effects=? WHERE profile=? AND id=?',
                            (_json(effects),self.profile,identifier))
        return effects,False

    def processed(self, identifier):
        with self.db:
            # A confirmed transport batch is not a second permanent game-memory
            # journal. Retain its idempotent receipt, release its bulky payload.
            row=self.db.execute('SELECT effects FROM automatic_delivery_v1 WHERE profile=? AND id=?',
                                (self.profile,identifier)).fetchone()
            receipt=json.loads(row[0]) if row else {}
            retained={'omitted_records':receipt['omitted_records']} if receipt.get('omitted_records') else {}
            for key in ('suppressed_completed_panels','suppression_reason'):
                if key in receipt:retained[key]=receipt[key]
            self.db.execute("UPDATE automatic_delivery_v1 SET processed=1,message='{}',effects=? "
                            'WHERE profile=? AND id=? AND received=1',
                            (_json(retained),self.profile, identifier))


class AutomaticInformation:
    def __init__(self, runtime, outbox, publish):
        self.runtime, self.outbox, self.publish = runtime, outbox, publish
        self.last = {}
        self.selectors = []
        self.seen_selectors = set()
        self.next_due = 0.0
        self.period = 0.5  # replaced by the actual native basic-view wait
        self.calm_period = 0.0  # >0 enables calm-vs-combat continuous scan pacing
        self.combat_hold_seconds = 10.0
        self._combat_until = 0.0
        self._next_continuous_due = 0.0
        self.pending_target = None
        self.target_mode = None
        self.known_names = {}
        self.name_refresh = {}
        self.last_control = None
        self.control_unavailable = False
        self.last_view = None
        self.last_view_scope = None
        self.last_view_identity = None
        self.last_view_availability_keys = set()
        self.screen_terrain_scope = None
        self.last_grid_sig = None
        self.last_creep_sig = None
        self.last_creep_tick_ms = None
        self.screen_terrain_same_origin_interval_s = 2.0
        self.screen_terrain_diff_cells_threshold = 96
        self.last_screen_origin = None
        self.last_screen_rows = None
        self.last_screen_overlay_sig = None
        self.last_screen_emit_tick_ms = None
        self.last_screen_emit_mono = None
        self._remembered_buildings_seen = {}
        self._remembered_buildings_scope = None
        self._building_memory_status_key_value = None
        self.last_own = None
        self.last_own_scope = None
        self.last_own_identity = None
        self._own_stationary_track = {}
        self._own_stationary_last_tick_ms = None
        self._own_stationary_scope = None
        self._last_menu_status = None
        self.last_resources = None
        self.last_resources_scope = None
        self.last_resources_identity = None
        self.last_session = None
        self._scan_iterator = None
        self._scan_api = None
        self._scan_records = None
        self._scan_advances = None
        self.feedback_cache = {}
        self.feedback_revision = 0
        self._enemy_frame = {}
        self._enemy_frame_scope = None
        self.last_step = None
        self.last_preview = None
        self._completion_stream = uuid.uuid4().hex
        if outbox is not None:
            outbox.reference_knowledge=getattr(getattr(runtime,'api',None),'_knowledge',None)

    DISCRETE_THREAT_EVENTS = frozenset((
        'own_under_attack', 'enemy_in_view', 'offscreen_enemy_contact',
        'nuclear_launch_warning', 'nuclear_marker_seen', 'minimap_ping',
    ))

    def configure(self, snapshot, *, calm_interval_ms=None, combat_hold_seconds=10.0,
                  screen_terrain_same_origin_interval_s=None,
                  screen_terrain_diff_cells_threshold=None):
        value = snapshot['view_wait_ms']
        if type(value) is not int or value <= 0:
            raise ValueError('native automatic observation interval unavailable')
        self.period = value / 1000
        if calm_interval_ms is not None:
            if type(calm_interval_ms) is not int or calm_interval_ms < 0:
                raise ValueError('calm_interval_ms must be a nonnegative integer')
            self.calm_period = max(self.period, calm_interval_ms / 1000) if calm_interval_ms > 0 else 0.0
        if isinstance(combat_hold_seconds, bool) or not isinstance(combat_hold_seconds, (int, float)) or combat_hold_seconds < 0:
            raise ValueError('combat_hold_seconds must be a nonnegative number')
        self.combat_hold_seconds = float(combat_hold_seconds)
        if screen_terrain_same_origin_interval_s is not None:
            if (isinstance(screen_terrain_same_origin_interval_s, bool)
                    or not isinstance(screen_terrain_same_origin_interval_s, (int, float))
                    or screen_terrain_same_origin_interval_s < 0):
                raise ValueError('screen_terrain_same_origin_interval_s must be a nonnegative number')
            self.screen_terrain_same_origin_interval_s = float(screen_terrain_same_origin_interval_s)
        if screen_terrain_diff_cells_threshold is not None:
            if (type(screen_terrain_diff_cells_threshold) is not int
                    or screen_terrain_diff_cells_threshold < 0):
                raise ValueError('screen_terrain_diff_cells_threshold must be a nonnegative integer')
            self.screen_terrain_diff_cells_threshold = screen_terrain_diff_cells_threshold

    def mark_action_completed(self):
        """Force fresh continuous state on the next scan after an action/wait."""
        self._next_continuous_due = 0.0

    @staticmethod
    def _has_visible_enemy(view):
        if not isinstance(view, dict) or view.get('status') != 'observed':
            return False
        units = view.get('units')
        rows = units.values() if isinstance(units, dict) else units if isinstance(units, list) else ()
        return any(isinstance(row, dict) and row.get('relation') == 'enemy' for row in rows)

    @staticmethod
    def _accepts_keyword(method, name):
        try:
            parameters = inspect.signature(method).parameters.values()
        except (TypeError, ValueError):
            return False
        return any(parameter.name == name or parameter.kind == inspect.Parameter.VAR_KEYWORD
                   for parameter in parameters)

    @staticmethod
    def _selection_requested(request, result):
        if not isinstance(request, dict) or not isinstance(result, dict):
            return False
        ticket = result.get('selection')
        if (not isinstance(ticket, dict) or type(ticket.get('count')) is not int
                or ticket['count'] < 1):
            return False
        op = request.get('op')
        if op in SELECTION_ACTIONS:
            return True
        if op == 'action_chain':
            args = request.get('args')
            steps = args.get('steps') if isinstance(args, dict) else None
            return isinstance(steps, list) and any(
                isinstance(step, dict) and step.get('op') in SELECTION_ACTIONS for step in steps)
        return False

    def _selection_feedback(self, ticket):
        """Enrich a live selection ticket through the real Stage5 value path.

        The ticket came from the action itself, so the full current_selection
        probe is only a fallback. Any unusable enrichment stays fail-closed:
        the lawful ticket remains, but no member/value is invented.
        """
        api = getattr(self.runtime, 'api', None)
        for name in ('selected_members_for_ticket', 'current_selection'):
            method = getattr(api, name, None)
            if not callable(method):
                continue
            kwargs = {}
            if self._accepts_keyword(method, 'reuse_own_snapshot'):
                kwargs['reuse_own_snapshot'] = True
            try:
                result = (method(deepcopy(ticket), **kwargs) if name == 'selected_members_for_ticket'
                          else method(**kwargs))
            except (TypeError, ValueError, KeyError, AttributeError, RuntimeError):
                continue
            if not isinstance(result, dict):
                continue
            if result.get('status') == 'outcome_unknown':
                return result
            if isinstance(result.get('selection'), dict):
                return result
        return {'selection':deepcopy(ticket)}

    @staticmethod
    def _build_sites_active(card, target_mode):
        """Build sites are lawful only in an open build menu or placement/Land mode."""
        if target_mode == 'placing':
            return True
        return (isinstance(card, dict) and card.get('status') == 'observed'
                and (card.get('mode') == 'placing' or card.get('build_submenu') is True))

    @staticmethod
    def _compass(dx, dy):
        angle = math.degrees(math.atan2(dy, dx))
        return COMPASS_8[int((angle + 22.5) // 45) % 8]

    def _attach_enemy_motion(self, view):
        """Pure two-frame enemy motion; no engine velocity or fog extrapolation."""
        api = getattr(self.runtime, 'api', None)
        scope = (getattr(self.runtime, 'run_ref', None), getattr(api, '_pid', None),
                 getattr(api, '_creation', None), getattr(api, '_epoch', None))
        if scope != self._enemy_frame_scope:
            self._enemy_frame_scope = scope
            self._enemy_frame = {}
        live = (isinstance(view, dict) and view.get('status') == 'observed'
                and view.get('temporal') != 'last_observed_not_current'
                and view.get('session_changed') is not True and view.get('active') is not False)
        tick = view.get('observed_tick_ms') if isinstance(view, dict) else None
        if not live or type(tick) is not int:
            self._enemy_frame = {}
            return view
        units = view.get('units')
        rows = units.values() if isinstance(units, dict) else units if isinstance(units, list) else ()
        previous = self._enemy_frame
        current = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            # Only this frame's derivation may publish motion. A stationary
            # row or a non-enemy row therefore never carries a stale vector.
            row.pop('motion', None)
            if row.get('relation') != 'enemy':
                continue
            token = row.get('entity_token')
            position = row.get('position')
            if (not isinstance(token, str) or not token or token in current
                    or not isinstance(position, dict)
                    or type(position.get('x')) is not int
                    or type(position.get('y')) is not int):
                continue
            x, y = position['x'], position['y']
            prior = previous.get(token)
            if prior is not None:
                px, py, prior_tick = prior
                dt_ms = tick - prior_tick
                dx, dy = x - px, y - py
                if dt_ms > 0 and (dx or dy):
                    row['motion'] = {'dx':dx, 'dy':dy, 'dt_ms':dt_ms,
                                     'dir':self._compass(dx, dy)}
            current[token] = (x, y, tick)
        self._enemy_frame = current
        return view

    @staticmethod
    def _role_experiences(rt):
        stores = getattr(rt, 'role_experiences', None)
        return stores if isinstance(stores, dict) and stores else {}

    def remember_authoring(self, result):
        if isinstance(result, dict) and result.get('entry_id'):
            self.selectors.append(({'entry_id': result['entry_id']}, None))

    def remember_experience(self, result, role=None):
        """Queue one authored entry page for the role that actually wrote it."""
        if (not isinstance(result, dict) or not result.get('entry_id')
                or result.get('written') is not True):
            return
        stores = self._role_experiences(self.runtime)
        selector = {'entry_id': result['entry_id']}
        if role is not None and stores and role in stores:
            self.selectors.append((selector, None, role))
        else:
            # Standalone single-store sessions keep their existing selector and
            # automatic delivery behavior.
            self.selectors.append((selector, None))

    def _queue_keyword_selector(self, name):
        """Queue observed public names for the configured role notebooks."""
        stores = self._role_experiences(self.runtime)
        if stores:
            for role in sorted(stores):
                self.selectors.append(({'keyword': name}, None, role))
        else:
            self.selectors.append(({'keyword': name}, None))

    def remember_target(self, result):
        if result.get('status') == 'target_required':
            self.pending_target = result.get('target')
            self.target_mode = result.get('mode')

    def preview_command(self, request):
        self.last_preview = None
        if not isinstance(request, dict) or request.get('op') != 'target_at' or self.target_mode != 'placing':
            return
        args = request.get('args', {})
        result = {'status':'rejected','reason':'invalid_public_arguments','command_issued':False}
        if isinstance(args, dict) and all(type(args.get(key)) is int and 0 <= args[key] < 2**31
                                          for key in ('x', 'y')):
            state = self.runtime.api._client.peek()
            fields = ('camera_x', 'camera_y', 'width_tiles', 'height_tiles', 'camera_max_x', 'camera_max_y')
            if (any(type(state.get(key)) is not int or state[key] < 0 for key in fields)
                    or state['width_tiles'] * 32 <= state['camera_max_x']
                    or state['height_tiles'] * 32 <= state['camera_max_y']):
                result = {'status':'unavailable','reason':'placement_camera_unavailable','command_issued':False}
            elif args['x'] >= state['width_tiles'] * 32 or args['y'] >= state['height_tiles'] * 32:
                result = {'status':'rejected','reason':'offmap','command_issued':False}
            else:
                camera = (state['camera_x'], state['camera_y'])
                screen = (args['x'] - camera[0], args['y'] - camera[1])
                size = (state['width_tiles'] * 32 - state['camera_max_x'],
                        state['height_tiles'] * 32 - state['camera_max_y'])
                if any(not 0 <= point < extent for point, extent in zip(screen, size)):
                    # Reject before division: C truncation for negative screen
                    # coordinates must never become Python's floor projection.
                    result = {'status':'rejected','reason':'offscreen','command_issued':False}
                else:
                    # Native s5_placement_target_gate: camera tile + screen/32,
                    # then partial-left/top correction. This is independent of
                    # footprint parity; native preview keeps main/addon geometry.
                    anchor = [32 * (origin // 32 + point // 32)
                              for origin, point in zip(camera, screen)]
                    anchor = [point + 32 if point < origin else point
                              for point, origin in zip(anchor, camera)]
                    try:
                        result = self.runtime.dispatch('placement_preview', {
                            'ticket':args.get('target'), 'x':anchor[0], 'y':anchor[1],
                            'anchor':'engine_anchor'})
                    except (TypeError, ValueError, KeyError):
                        # Invalid public tickets/arguments still reach handle()
                        # for its ordinary structured action rejection. Never
                        # turn a preview argument error into a dead host.
                        result = {'status':'rejected','reason':'invalid_public_arguments','command_issued':False}
                    if result.get('status') == 'ok' and result.get('camera') != {'x':camera[0], 'y':camera[1]}:
                        result = {'status':'rejected','reason':'observation_changed_during_read','command_issued':False}
        self.last_preview={'request_id':request.get('id'),'observed_at_utc':datetime.now(timezone.utc).isoformat(),
                           'result':deepcopy(result)}
        record=getattr(self.runtime,'record',None)
        if callable(record):
            try:record({'automatic_placement_preview':deepcopy(self.last_preview)})
            except Exception:pass  # Auxiliary evidence cannot change known game outcomes.
        self.publish([{'source':'placement_preview','update':'replace','result':result}])
        return result

    def flush_learning(self):
        """Before a command: persist completion, without a second full scan."""
        self.flush_reference_notices()
        while True:
            records=[]
            drained=self._call(self.runtime.api.drain_observations)
            if drained.get('status') != 'ok':
                self._record(records,'learning_status',drained)
                self.publish(records)
                raise RuntimeError('automatic learning persistence unavailable; commands remain blocked')
            for observation in drained.get('observations', []):
                self._record(records,observation.get('kind','learned_observation'),observation,append=True)
            if records:
                # This durable enqueue MUST precede handoff acknowledgement and
                # every subsequent API read. Failure leaves the source durable.
                durable_ids=[item['observation_id'] for item in drained.get('observations',[]) if 'observation_id' in item]
                if drained.get('receipts') and len(durable_ids)!=len(drained['receipts']):
                    raise RuntimeError('stable completed-observation identity required for durable handoff')
                self.publish(records,handoff_receipts=durable_ids)
            receipts=drained.get('receipts',[])
            if receipts:
                ack=self.runtime.api.ack_observations(receipts)
                if ack.get('status') != 'ok':
                    raise RuntimeError('automatic observation handoff was not acknowledged')
            if drained.get('pending',0)<=len(receipts):
                return
            if not receipts:
                raise RuntimeError('automatic observation drain made no progress')

    @staticmethod
    def _reference_notice_record(notices):
        return {'source':'knowledge_updates','update':'append',
                'result':{'status':'available','source':'knowledge',
                          'entries':[deepcopy(row['entry']) for row in notices]},
                '_reference_notices':[{key:row[key] for key in ('identity','scope','run_ref','source')}
                                      for row in notices]}

    def flush_reference_notices(self, publish=None):
        """Recover committed knowledge even when its source is not observed again."""
        knowledge=getattr(getattr(self.runtime,'api',None),'_knowledge',None)
        pending=getattr(knowledge,'pending_reference_notices',None)
        publisher=self.publish if publish is None else publish
        if self.outbox is None or not callable(pending) or not callable(publisher):return
        self.outbox.reference_knowledge=knowledge
        while True:
            page=pending(limit=PAGE_ROWS)
            if not page:return
            # Each recovered message keeps the original run. Scope remains
            # private provenance, never current-scope reference population.
            first=page[0]
            group=[]
            for item in page:
                if (item['scope'],item['run_ref'])!=(first['scope'],first['run_ref']):break
                group.append(item)
            publisher([self._reference_notice_record(group)])
            remaining=pending(limit=1)
            if remaining and remaining[0]['identity']==first['identity']:
                raise RuntimeError('reference notice publisher did not complete durable handoff')

    @staticmethod
    def _transient_busy(result):
        return (isinstance(result, dict) and result.get('status') == 'rejected'
                and result.get('reason') == 'busy')

    def _record_control(self, records, control):
        """Retain the last good control facts across a transient busy probe."""
        if self._transient_busy(control):
            # A busy mailbox says nothing about control ownership. Keep the prior
            # state for evidence and mark control unavailable. The player-session
            # objectives guard must OR this flag into its early return.
            self.control_unavailable = True
            return
        self.control_unavailable = False
        self.last_control = control
        if self._control_paused(control):
            self._own_stationary_last_tick_ms = None
        self._record(records, 'state', control)

    def _call(self, method, **args):
        result = method(**args)
        if result.get('status') == 'outcome_unknown':
            raise RuntimeError('automatic observation outcome unknown; no mailbox retry')
        return result

    @staticmethod
    def _screen_sample_available(terrain):
        """Only a real scoped terrain attachment can drive the screen source.

        Older synthetic/legacy payloads may still carry a generic `cells` list
        for unrelated projection tests; they have neither a terrain epoch nor
        the decoder's explicit exploration field and must emit nothing.
        """
        cells=terrain.get('cells')
        if not isinstance(cells,list):return False
        if type(terrain.get('epoch')) is int:return True
        if isinstance(terrain.get('camera'),dict):return True
        fields=('explored','currently_visible','tile_summary_status')
        return any(isinstance(cell,dict) and any(key in cell for key in fields)
                   for cell in cells)

    @staticmethod
    def _screen_grid_signature(screen):
        return _json({'origin':screen.get('origin'),'rows':screen.get('rows'),
                      'ramps':screen.get('ramps',[]),'no_build':screen.get('no_build',[]),
                      'blocks_view':screen.get('blocks_view',[])})

    @staticmethod
    def _screen_creep_signature(screen):
        return _json({'creep_current':screen.get('creep_current',[]),
                      'creep_fog':screen.get('creep_fog',[])})

    @staticmethod
    def _screen_overlay_signature(screen):
        return _json({'ramps':screen.get('ramps',[]),
                      'no_build':screen.get('no_build',[]),
                      'blocks_view':screen.get('blocks_view',[])})

    @staticmethod
    def _screen_row_changes(old_rows,new_rows):
        """Count differing characters; flag anything that is not pure fog reveal."""
        if not isinstance(old_rows,list) or not isinstance(new_rows,list):
            return None
        if len(old_rows)!=len(new_rows):
            return None
        changed=0;authoritative=False
        for old,new in zip(old_rows,new_rows):
            if not isinstance(old,str) or not isinstance(new,str) or len(old)!=len(new):
                return None
            for old_ch,new_ch in zip(old,new):
                if old_ch==new_ch:continue
                changed+=1
                if old_ch!='?' or new_ch=='?':authoritative=True
        return changed,authoritative

    def _reset_screen_emission(self):
        self.last_screen_origin=None
        self.last_screen_rows=None
        self.last_screen_overlay_sig=None
        self.last_screen_emit_tick_ms=None
        self.last_screen_emit_mono=None

    def _record_screen_terrain(self,records,terrain,store,scope):
        """Emit a compact screen map on change; fog-only reveals are throttled.

        The grid signature intentionally excludes the sample clock and camera,
        so a stationary unchanged screen contributes zero bytes. A changed
        map (including a door toggling under the same camera) replaces the
        screen immediately. Same-origin pure fog reveals smaller than the
        configured threshold accumulate until the interval elapses, and a
        suppressed sample never advances the emitted baseline. Remembered
        creep spread is a small delta and at most one such delta per 2000ms of
        observed game time.
        """
        screen=build_screen_terrain(terrain,store=store)
        if screen is None:return
        if self.screen_terrain_scope!=scope:
            self.screen_terrain_scope=scope
            self.last_grid_sig=None
            self.last_creep_sig=None
            self.last_creep_tick_ms=None
            self._reset_screen_emission()
        grid_sig=self._screen_grid_signature(screen)
        creep_sig=self._screen_creep_signature(screen)
        tick=screen.get('observed_tick_ms')
        if type(tick) is not int:tick=None
        origin=screen.get('origin')
        rows=screen.get('rows')
        overlay_sig=self._screen_overlay_signature(screen)
        if grid_sig!=self.last_grid_sig:
            emit=True
            if self.last_grid_sig is not None and origin==self.last_screen_origin:
                changes=self._screen_row_changes(self.last_screen_rows,rows)
                if overlay_sig==self.last_screen_overlay_sig and changes is not None:
                    changed_cells,authoritative=changes
                    emit=(authoritative or changed_cells
                          >=self.screen_terrain_diff_cells_threshold)
                    if not emit:
                        previous_tick=self.last_screen_emit_tick_ms
                        previous_mono=self.last_screen_emit_mono
                        if type(tick) is int and type(previous_tick) is int:
                            interval_ms=self.screen_terrain_same_origin_interval_s*1000
                            emit=(tick<previous_tick
                                  or tick-previous_tick>=interval_ms)
                        elif previous_mono is None:
                            emit=True
                        else:
                            emit=(time.monotonic()-previous_mono
                                  >=self.screen_terrain_same_origin_interval_s)
                if not emit:
                    # Do not advance the emitted signatures: later fog reveals
                    # must accumulate against the last screen actually sent.
                    return
            records.append({'source':'screen_terrain','update':'replace','result':screen})
            self.last_grid_sig=grid_sig
            self.last_creep_sig=creep_sig
            self.last_creep_tick_ms=tick
            self.last_screen_origin=deepcopy(origin)
            self.last_screen_rows=deepcopy(rows) if isinstance(rows,list) else rows
            self.last_screen_overlay_sig=overlay_sig
            self.last_screen_emit_tick_ms=tick
            self.last_screen_emit_mono=time.monotonic()
            return
        if creep_sig!=self.last_creep_sig:
            previous_tick=self.last_creep_tick_ms
            if (previous_tick is None or tick is None or tick<previous_tick
                    or tick-previous_tick>=2000):
                records.append({'source':'screen_terrain_creep','update':'replace',
                    'result':{'camera':deepcopy(screen.get('camera')),
                              'observed_tick_ms':tick,
                              'creep_current':deepcopy(screen.get('creep_current',[])),
                              'creep_fog':deepcopy(screen.get('creep_fog',[]))}})
                self.last_creep_sig=creep_sig
                self.last_creep_tick_ms=tick

    def _record(self, records, source, result, *, append=False, key=None, update=None):
        if result.get('status') == 'outcome_unknown':
            raise RuntimeError('automatic observation outcome unknown; no mailbox retry')
        if source == 'building_memory_status' and isinstance(result, dict):
            status_key = self._building_memory_status_key(result)
            if status_key == self._building_memory_status_key_value:
                return
            self._building_memory_status_key_value = status_key
        if source=='observe_view' and not append:
            result=self._attach_enemy_motion(result)
        if source=='resource_sites':
            self._record_resource_sites(records,result)
            return
        learned_group_card = None
        if source == 'command_card':
            # Stage5 exposes this one-shot side channel; a callable consumer
            # keeps command_card's public return schema unchanged. Also accept
            # and remove a legacy private key rather than ever forwarding it.
            legacy = result.pop('_learned_group_card', None) if isinstance(result, dict) else None
            api = getattr(self.runtime,'api',None)
            consume = getattr(api,'consume_learned_group_card',None)
            if callable(consume):
                learned_group_card = consume()
            if learned_group_card is None:
                learned_group_card = legacy
            if result.get('status') != 'observed':
                learned_group_card = None
        before_projection = len(records)
        clean = self._reference_projection(records,source,public_page(result),
                                           learned_group_card=learned_group_card)
        completion = self._completion_signature(source,result) if append else None
        knowledge_changed = any(row.get('source') == 'knowledge_updates' for row in records[before_projection:])
        if source=='observe_view':
            from .terrain_reference import project_automatic_view
            api=getattr(self.runtime,'api',None)
            store=getattr(getattr(api,'_knowledge',None),'terrain_reference',None)
            terrain=clean.get('terrain')
            if store is not None and clean.get('status')=='observed' and isinstance(terrain,dict):
                session={'pid':getattr(api,'_pid',None),'process_creation':getattr(api,'_creation',None),
                         'epoch':getattr(api,'_epoch',None)}
                notice=store.observe(terrain,observed_session=session,expected_session=session)
                if notice.get('status')=='available':records.append({'source':'terrain_updates','update':'append','result':notice})
                accepted=(isinstance(notice,dict)
                          and notice.get('status') in ('available','unchanged'))
                rejected=isinstance(notice,dict) and notice.get('status')=='rejected'
                if (not append and not rejected and terrain.get('cells')
                        and (accepted or self._screen_sample_available(terrain))):
                    scope=(getattr(self.runtime,'run_ref',None),getattr(api,'_pid',None),
                           getattr(api,'_creation',None),getattr(api,'_epoch',None))
                    self._record_screen_terrain(records,terrain,store,scope)
            clean=project_automatic_view(clean)
        if source=='observe_view' and not append:
            self._project_own_stationary('observe_view',clean)
        if source=='own_units' and not append:
            self._track_own_stationary(clean)
            self._project_own_stationary('own_units',clean)
            self._record_owned_roster(records,clean)
            rows=clean.get('units',[])
            for row in rows if isinstance(rows,list) else rows.values():
                name=row.get('name')
                if isinstance(name,str) and name and name not in self.seen_selectors:
                    self.seen_selectors.add(name);self._queue_keyword_selector(name)
            return
        self._cache_feedback(source, clean)
        if source=='menu_status' and isinstance(clean,dict):
            self._last_menu_status=deepcopy(clean)
            if self._control_paused(clean):
                self._own_stationary_last_tick_ms=None
        if source=='session':
            self.last_session=deepcopy(clean)
            self._note_own_stationary_scope()
            if clean.get('session_changed') is True:
                self._reset_own_stationary()
        if source == 'observe_view' and not append:
            # Validate all JSON values, including unchanged ones, without
            # retaining a second serialized copy of the complete terrain.
            _json(clean)
            api = getattr(self.runtime, 'api', None)
            scope = (getattr(self.runtime, 'run_ref', None),
                     getattr(api, '_pid', None), getattr(api, '_creation', None),
                     getattr(api, '_epoch', None))
            identity=self._view_identity()
            availability_keys=set()
            if self._retain_view_history(clean,scope,identity):
                # A retained historical view is a blind snapshot: no new frame
                # was observed, so a later roster read must not age stationary
                # time across this gap.
                self._own_stationary_last_tick_ms=None
                history=deepcopy(self.last_view)
                for name in self.last_view_availability_keys:
                    history.pop(name,None)
                # A retained historical observation is not two consecutive live
                # frames; never republish a previous derived motion vector.
                rows=history.get('units')
                for row in (rows.values() if isinstance(rows,dict) else
                            rows if isinstance(rows,list) else ()):
                    if isinstance(row,dict):row.pop('motion',None)
                # These clocks and payload fields describe the old observation,
                # never the time/status of this unsuccessful read.
                observation_fields={'units','terrain','observed_tick_ms','observed_frame','received_monotonic_ns','last_seen_tick_ms'}
                availability={name:value for name,value in clean.items() if name not in observation_fields}
                availability_keys=set(availability)|{'temporal'}
                history.update(availability)
                history['temporal']='last_observed_not_current'
                clean=history
            published=False
            if self.last_view is None or self.last_view_scope != scope:
                records.append({'source': source, 'update': 'replace', 'result': clean})
                published=True
            else:
                patch = (_view_patch(self.last_view, clean)
                         if _json(_stable(self.last_view))!=_json(_stable(clean)) else {'set':{},'remove':[]})
                if patch['set'] or patch['remove']:
                    records.append({'source': source, 'update': 'patch', 'result': patch,
                                    '_delivery_result': clean})
                    published=True
            if published:
                self.last_view = deepcopy(clean)
                self.last_view_scope = scope
                self.last_view_availability_keys=availability_keys
                self.last_view_identity=identity if clean.get('status')=='observed' else (
                    self.last_view_identity if clean.get('temporal')=='last_observed_not_current' else None)
                self._view_patch_evidence(records[-1],clean)
        else:
            stamp = _json(_stable(clean))
            stamp_key = source if key is None else key
            if append or self.last.get(stamp_key) != stamp:
                records.append({'source': source, 'update': update or ('append' if append else 'replace'), 'result': clean})
                if completion is not None:
                    records[-1]['_completed_panel'] = {'signature':completion,'knowledge_changed':knowledge_changed}
                if source in ('current_selection','command_card'):
                    records[-1]['feedback_revision'] = self.feedback_revision
                self.last[stamp_key] = stamp
        if source in ('selected_panel', 'own_units', 'current_selection', 'observe_view'):
            rows = ([clean.get('panel', {})] if source == 'selected_panel' else
                    clean.get('members' if source == 'current_selection' else 'units', []))
            if isinstance(rows,dict):rows=rows.values()
            for row in rows:
                name = row.get('name')
                if isinstance(name, str) and name and name not in self.seen_selectors:
                    self.seen_selectors.add(name)
                    self._queue_keyword_selector(name)

    def _completion_signature(self, source, result):
        if (source != 'selected_panel' or result.get('status') != 'observed'
                or result.get('temporal') != 'completed_observation'
                or not isinstance(result.get('observation_id'),str)
                or not result['observation_id'] or not isinstance(result.get('panel'),dict)):
            return None
        api = getattr(self.runtime,'api',None)
        identity = {'pid':getattr(api,'_pid',None),'process_creation':getattr(api,'_creation',None),
                    'epoch':getattr(api,'_epoch',None)}
        selection = result.get('selection')
        if (not isinstance(selection,dict)
                or any(type(value) is not int or value <= 0 or selection.get(key) != value
                       for key,value in identity.items())
                or result.get('observed_epoch') != identity['epoch']
                or type(selection.get('generation')) is not int or selection['generation'] < 0
                or type(selection.get('count')) is not int or selection['count'] != 1):
            return None
        # Only observation-envelope clocks/IDs are irrelevant. Nested source
        # facts, selection, owner and rules remain part of exact equality.
        # Dynamic command-card toggles (enabled state or subset appearances of
        # already-accumulated basic_commands/skills) use post-merge static facts
        # so oscillating worker/transport/siege/building buttons do not defeat
        # unchanged-completion suppression.
        semantic = {key:value for key,value in result.items() if key not in
                    ('observation_id','observed_tick_ms','observed_frame','received_monotonic_ns','last_seen_tick_ms')}
        if isinstance(semantic.get('panel'), dict):
            from .panel_knowledge import PanelKnowledge
            panel = deepcopy(semantic['panel'])
            knowledge = getattr(api, '_knowledge', None)
            token = panel.get('type_token') or ('name:' + panel['name'] if panel.get('name') else None)
            stored_facts = (knowledge.reference_facts(token, 'selected_panel', owner_token=panel.get('owner_token'))
                            if knowledge is not None and token and hasattr(knowledge, 'reference_facts') else None)
            card_available = panel.get('card_status') != 'unavailable'
            for field in ('basic_commands', 'skills'):
                if field in panel:
                    if isinstance(stored_facts, dict) and field in stored_facts:
                        panel[field] = stored_facts[field]
                    else:
                        panel[field] = PanelKnowledge._merge_card_items(None, panel[field])
                elif card_available and isinstance(stored_facts, dict) and field in stored_facts:
                    panel[field] = stored_facts[field]
            semantic['panel'] = panel
        return _json({'stream':self._completion_stream,'run_ref':getattr(self.runtime,'run_ref',None),
                      'knowledge_scope':getattr(getattr(api,'_knowledge',None),'scope',None),
                      'result':semantic})

    def _view_identity(self):
        """Read shared-state identity only; this submits no native command."""
        api=getattr(self.runtime,'api',None)
        peek=getattr(getattr(api,'_client',None),'peek',None)
        if not callable(peek):return None
        try:
            state=peek()
            return {key:state.get(key) for key in ('epoch','active','tile_pointer','width_tiles','height_tiles')}
        except Exception:return None

    @staticmethod
    def _resource_stamp(value):
        # Ordinary census clocks do not publish another copy. An explicit new
        # amount observation retains its own time, including an unchanged value.
        value=deepcopy(value);value.pop('observed_tick_ms',None)
        sites=value.get('sites',{})
        if isinstance(sites,dict):
            for row in sites.values():row.pop('observed_tick_ms',None)
        return _json(value)

    def _record_resource_sites(self,records,result,*,include_selected_amount=False,selected=None):
        if result.get('status')=='outcome_unknown':
            raise RuntimeError('automatic resource observation outcome unknown; no mailbox retry')
        api=self.runtime.api
        scope=(self.runtime.run_ref,getattr(getattr(api,'_knowledge',None),'scope',None),
               getattr(api,'_pid',None),getattr(api,'_creation',None),getattr(api,'_epoch',None))
        clean=deepcopy(result);identity=self._view_identity()
        previous=(self.last_resources.get('sites',{}) if self.last_resources is not None
                  and self.last_resources_scope==scope else {})
        if result.get('status')=='observed':
            store=getattr(getattr(api,'_knowledge',None),'terrain_reference',None)
            if store is None:raise RuntimeError('resource history store unavailable')
            session={'pid':api._pid,'process_creation':api._creation,'epoch':api._epoch}
            peek=getattr(getattr(api,'_client',None),'peek',None)
            state=peek() if callable(peek) else {}
            camera=None
            if all(type(state.get(k)) is int and state[k]>=0 for k in
                   ('camera_x','camera_y','camera_max_x','camera_max_y','width_tiles','height_tiles')):
                camera={'x':state['camera_x'],'y':state['camera_y'],
                        'width':state['width_tiles']*32-state['camera_max_x'],
                        'height':state['height_tiles']*32-state['camera_max_y']}
            serial=state.get('serial')
            census_serial=(serial if type(serial) is int and serial>0
                and state.get('state')==3 and state.get('operation')==48 and state.get('epoch')==api._epoch
                and state.get('completed_serial')==serial and state.get('view_serial')==serial
                and state.get('executed_tick')==result.get('observed_tick_ms') else None)
            clean=store.observe_resources(result,visibility=getattr(api,'_resource_visibility',None),
                observed_session=session,expected_session=session,
                include_selected_amount=include_selected_amount,selected=selected,current_camera=camera,census_serial=census_serial,
                published_tokens=previous)
            if clean.get('status')=='observed':clean['sites']={**deepcopy(previous),**clean['sites']}
        elif (previous and identity is not None and identity==self.last_resources_identity
                and scope==self.last_resources_scope):
            clean=dict(deepcopy(self.last_resources),**{key:value for key,value in result.items()
                       if key not in ('sites','observed_tick_ms')},temporal='last_observed_not_current')
        if self.last_resources is None or self.last_resources_scope!=scope:
            record={'source':'resource_sites','update':'replace','result':clean}
        elif self._resource_stamp(self.last_resources)!=self._resource_stamp(clean):
            record={'source':'resource_sites','update':'patch','result':_view_patch(self.last_resources,clean),
                    '_delivery_result':clean}
        else:return clean
        records.append(record)
        self.last_resources=deepcopy(clean);self.last_resources_scope=scope
        self.last_resources_identity=identity if clean.get('status')=='observed' else (
            self.last_resources_identity if clean.get('temporal')=='last_observed_not_current' else None)
        return clean

    def _selected_resource_feedback(self,request,result):
        if (not isinstance(request,dict) or request.get('op') not in
                ('select_at','select_box','select_same_type','select_group') or result.get('status')!='selected'):
            return None
        method=getattr(self.runtime.api,'resource_sites',None)
        if not callable(method):return None  # Older offline fixtures have no resource source.
        sample=self._call(method,unit_budget=UNIT_CAPACITY,include_selected_amount=True)
        records=[]
        clean=self._record_resource_sites(records,sample,include_selected_amount=True,selected=result.get('selection'))
        if records:self.publish(records)  # Durable handoff before the next foreground request.
        if sample.get('status')!='observed':return deepcopy(sample)
        selected={row['site_token'] for row in sample['sites'] if 'remaining' in row}
        sites={token:deepcopy(row) for token,row in clean.get('sites',{}).items()
               if token in selected and row.get('remaining',{}).get('status')=='last_observed'
               and row['remaining'].get('observed_tick_ms')==sample['observed_tick_ms']}
        if not sites:return None
        return {'status':'observed','scope':'selected_resource_ui','observed_tick_ms':sample['observed_tick_ms'],
                'sites':sites}

    def _reset_own_stationary(self):
        self._own_stationary_track = {}
        self._own_stationary_last_tick_ms = None

    def _own_stationary_current_scope(self):
        api = getattr(self.runtime, 'api', None)
        return (getattr(self.runtime, 'run_ref', None),
                getattr(api, '_pid', None), getattr(api, '_creation', None),
                getattr(api, '_epoch', None),
                getattr(getattr(api, '_knowledge', None), 'scope', None))

    def _note_own_stationary_scope(self):
        scope = self._own_stationary_current_scope()
        if self._own_stationary_scope is not None and scope != self._own_stationary_scope:
            self._reset_own_stationary()
        self._own_stationary_scope = scope

    @staticmethod
    def _control_paused(value):
        if not isinstance(value, dict):
            return False
        if value.get('paused') in (1, True) or value.get('menu_open') in (1, True):
            return True
        if value.get('pause') in (1, True):
            return True
        return value.get('control') == 'paused'

    def _own_stationary_paused(self):
        """Live human pause/menu state; a paused scan never ages durations."""
        api = getattr(self.runtime, 'api', None)
        peek = getattr(getattr(api, '_client', None), 'peek', None)
        live = None
        if callable(peek):
            try:
                live = peek()
            except Exception:
                live = None
        if isinstance(live, dict) and self._control_paused(live):
            return True
        if self._control_paused(self.last_control):
            return True
        return self._control_paused(self._last_menu_status)

    @staticmethod
    def _stationary_rows(snapshot):
        units = snapshot.get('units') if isinstance(snapshot, dict) else None
        if isinstance(units, dict):
            return units.values()
        return units if isinstance(units, list) else ()

    def _fallback_stationary_eligible(self, row, token):
        """Fallback for stubs without Stage5's private row flags.

        Stage5 only publishes `work` for the valid building work record, so its
        mere presence marks a building even when both names are null.
        """
        if 'work' in row or 'supply_provided' in row:
            return False
        remembered = getattr(self, '_remembered_buildings_seen', None)
        if isinstance(remembered, dict) and token in remembered:
            return False
        return True

    def _track_own_stationary(self, snapshot):
        """Age eligible mobile units from fresh, unpaused own snapshots only."""
        if not isinstance(snapshot, dict) or snapshot.get('status') != 'observed':
            # A blocked/changed/failed roster is a blind gap: it supplies no
            # continuous observation, so the next fresh frame must start a new
            # clock reference instead of aging the duration across that gap.
            self._own_stationary_last_tick_ms = None
            return
        if snapshot.get('temporal') == 'last_observed_not_current':
            self._own_stationary_last_tick_ms = None
            return
        self._note_own_stationary_scope()
        if snapshot.get('session_changed') is True:
            self._reset_own_stationary()
            return
        if snapshot.get('reason') == 'paused' or self._own_stationary_paused():
            # Keep coordinates/durations intact; only the clock reference is
            # cleared so time spent in a human pause is never accumulated.
            self._own_stationary_last_tick_ms = None
            return
        tick_ms = snapshot.get('observed_tick_ms')
        if type(tick_ms) is not int:
            self._own_stationary_last_tick_ms = None
            return
        api = getattr(self.runtime, 'api', None)
        eligibility = getattr(api, 'last_own_unit_eligibility', None)
        current = {}
        for row in self._stationary_rows(snapshot):
            if not isinstance(row, dict):
                continue
            token = row.get('entity_token')
            position = row.get('position')
            if (not isinstance(token, str) or not token
                    or not isinstance(position, dict)
                    or type(position.get('x')) is not int
                    or type(position.get('y')) is not int):
                continue
            if isinstance(eligibility, dict):
                if eligibility.get(token) is not True:
                    continue
            elif eligibility is None:
                if not self._fallback_stationary_eligible(row, token):
                    continue
            else:
                continue
            current[token] = (position['x'], position['y'])
        previous_tick = self._own_stationary_last_tick_ms
        track = {}
        for token, (x, y) in current.items():
            previous = self._own_stationary_track.get(token)
            if (previous is None or previous.get('x') != x
                    or previous.get('y') != y):
                track[token] = {'x':x, 'y':y, 'stationary_ms':0}
                continue
            stationary_ms = previous.get('stationary_ms')
            if type(stationary_ms) is not int:
                stationary_ms = 0
            if previous_tick is not None and tick_ms > previous_tick:
                stationary_ms += tick_ms - previous_tick
            track[token] = {'x':x, 'y':y, 'stationary_ms':stationary_ms}
        # Disappearing, invalid or newly ineligible entities (loaded into a
        # Bunker/hangar, dead, out of the roster) start fresh when they return.
        self._own_stationary_track = track
        self._own_stationary_last_tick_ms = tick_ms

    def _project_own_stationary(self, source, snapshot):
        """Attach the sparse bucket to own rows; moving removes it again."""
        if source not in ('own_units', 'observe_view'):
            return
        if not isinstance(snapshot, dict) or snapshot.get('status') != 'observed':
            return
        if snapshot.get('temporal') == 'last_observed_not_current':
            return
        self._note_own_stationary_scope()
        if snapshot.get('session_changed') is True:
            self._reset_own_stationary()
            return
        for row in self._stationary_rows(snapshot):
            if not isinstance(row, dict):
                continue
            if source == 'observe_view' and row.get('relation') != 'own':
                row.pop('stationary_s', None)
                continue
            token = row.get('entity_token')
            entry = (self._own_stationary_track.get(token)
                     if isinstance(token, str) and token else None)
            if isinstance(entry, dict):
                # The visible frame may be a moment newer than the roster scan;
                # a different current position proves movement, so never carry
                # a stale stationary bucket across that race. A paused roster
                # likewise must not keep a bucket on a changed/absent position.
                position = row.get('position')
                positioned = (isinstance(position, dict)
                              and type(position.get('x')) is int
                              and type(position.get('y')) is int)
                if source == 'own_units' and not positioned:
                    entry = None
                elif (positioned and (entry.get('x') != position['x']
                                      or entry.get('y') != position['y'])):
                    entry = None
            bucket = (_stationary_bucket_s(entry.get('stationary_ms'))
                      if isinstance(entry, dict) else None)
            if bucket is None:
                row.pop('stationary_s', None)
            else:
                row['stationary_s'] = bucket

    def _record_owned_roster(self,records,clean):
        api=getattr(self.runtime,'api',None)
        scope=(getattr(self.runtime,'run_ref',None),getattr(api,'_pid',None),
               getattr(api,'_creation',None),getattr(api,'_epoch',None))
        identity=self._view_identity()
        self._note_own_stationary_scope()
        if isinstance(clean,dict) and clean.get('session_changed') is True:
            self._reset_own_stationary()
        if (self._own_stationary_paused()
                or (isinstance(clean,dict) and clean.get('reason')=='paused')):
            # A paused read (successful or retained) must never leave a clock
            # reference that later spans the pause.
            self._own_stationary_last_tick_ms=None
        snapshot=deepcopy(clean)
        if clean.get('status')=='observed':
            units=clean.get('units')
            if not isinstance(units,list):raise ValueError('complete owned roster requires a list')
            keyed={};counts={}
            for row in units:
                token=row.get('entity_token') if isinstance(row,dict) else None
                if (not isinstance(token,str) or not token.startswith('entity:')
                        or any(c not in '0123456789abcdef' for c in token[7:]) or len(token)!=23
                        or token in keyed):
                    raise ValueError('complete owned roster requires unique opaque entity tokens')
                keyed[token]=row
                name=row.get('name')
                if isinstance(name,str):counts[name]=counts.get(name,0)+1
            snapshot.update(units=keyed,count=len(keyed),counts_by_name=counts,
                            membership_semantics='current_owned_roster; removal_does_not_prove_death')
        elif self.last_own is not None and self._retain_view_history(clean,scope,identity,baseline=self.last_own,
                baseline_scope=self.last_own_scope,baseline_identity=self.last_own_identity):
            self._own_stationary_last_tick_ms=None
            snapshot=deepcopy(self.last_own)
            for name in ('reason','required_units','required_bytes','required_bytes_at_least'):
                snapshot.pop(name,None)
            snapshot.update({key:value for key,value in clean.items() if key not in
                ('units','observed_tick_ms','observed_frame','received_monotonic_ns','last_seen_tick_ms')})
            snapshot['temporal']='last_observed_not_current'
        _json(snapshot)
        self._cache_feedback('own_units',snapshot)
        if self.last_own is None or self.last_own_scope!=scope:
            record={'source':'own_units','update':'replace','result':snapshot}
        elif _json(_stable(self.last_own))!=_json(_stable(snapshot)):
            record={'source':'own_units','update':'patch','result':_view_patch(self.last_own,snapshot),
                    '_delivery_result':snapshot}
        else:return
        records.append(record)
        self.last_own=deepcopy(snapshot);self.last_own_scope=scope
        self.last_own_identity=identity if snapshot.get('status')=='observed' else (
            self.last_own_identity if snapshot.get('temporal')=='last_observed_not_current' else None)

    def _retain_view_history(self, clean, scope, identity, *, baseline=None, baseline_scope=None,baseline_identity=None):
        """Only a verified same-game temporary failure can retain old facts."""
        if baseline is None and baseline_scope is None:
            baseline,baseline_scope,baseline_identity=self.last_view,self.last_view_scope,self.last_view_identity
        if (not isinstance(scope[0],str) or not scope[0]
                or any(type(value) is not int or value<=0 for value in scope[1:])):
            return False
        if (baseline is None or baseline_scope!=scope
                or (baseline.get('status')!='observed'
                    and baseline.get('temporal')!='last_observed_not_current')):
            return False
        if baseline.get('session_changed') or baseline.get('active') is False:
            return False
        if clean.get('status') not in ('unavailable','rejected','observation_not_ready'):
            return False
        if 'units' in clean or 'terrain' in clean:
            return False  # Do not hide a new partial/unknown payload behind old facts.
        temporary={'paused','camera_locked','ui_blocked','script_controlled','busy',
                   'observation_changed_during_read','view_not_ready','attention_not_ready'}
        if clean.get('reason') not in temporary and not (
                clean.get('status')=='observation_not_ready' and clean.get('reason') is None):
            return False
        if clean.get('session_changed') or clean.get('active') is False:
            return False
        if self.last_session and (self.last_session.get('session_changed') or self.last_session.get('active') is False):
            return False
        old=baseline_identity
        if not identity or not old or identity!=old:return False
        return (type(identity['epoch']) is int and identity['epoch']==scope[-1]
                and type(identity['active']) in (int,bool) and identity['active']==1
                and type(identity['tile_pointer']) is int
                and identity['tile_pointer']>0
                and all(type(identity[key]) is int and identity[key]>0 for key in ('width_tiles','height_tiles')))

    def _view_patch_evidence(self,record,snapshot):
        """Metadata-only actual public patch witness; timing IO is nonfatal."""
        result=record['result'];sets=result.get('set',{}) if record['update']=='patch' else {}
        evidence={'source':'observe_view','update':record['update'],
            'public_record_bytes':len(_json({key:value for key,value in record.items() if key!='_delivery_result'}).encode()),
            'set_value_bytes':{key:len(_json(value).encode()) for key,value in sets.items()},
            'remove_paths':result.get('remove',[]),'whole_terrain_set':'/terrain' in sets,
            'whole_cells_set':'/terrain/cells' in sets,'whole_units_set':'/units' in sets,
            'status':snapshot.get('status'),'reason':snapshot.get('reason'),
            'temporal':snapshot.get('temporal'),'observed_tick_ms':snapshot.get('observed_tick_ms')}
        record_private=getattr(self.runtime,'record',None)
        if callable(record_private):
            try:record_private({'automatic_view_patch':evidence})
            except Exception:pass

    def _reference_projection(self,records,source,result,learned_group_card=None):
        knowledge=getattr(getattr(self.runtime,'api',None),'_knowledge',None)
        remember=getattr(knowledge,'remember_public_reference',None)
        if not callable(remember):return result
        api=self.runtime.api
        identity={'pid':getattr(api,'_pid',None),'process_creation':getattr(api,'_creation',None),
                  'epoch':getattr(api,'_epoch',None)}
        applicable=(all(type(value) is int and value>0 for value in identity.values())
                    and result.get('observed_epoch',identity['epoch'])==identity['epoch'])
        selected=result.get('selection')
        if isinstance(selected,dict):
            applicable=applicable and all(selected.get(key)==value for key,value in identity.items())
        elif result.get('temporal')=='completed_observation':
            applicable=False
        static=('details','weapons','basic_commands','skills','build_options','build_options_status','supply_provided','fully_known','detector')
        notices=[]
        durable=(self.outbox is not None and callable(getattr(knowledge,'pending_reference_notices',None)))
        if durable:self.outbox.reference_knowledge=knowledge
        def project(row):
            if not isinstance(row,dict):return row
            facts={key:deepcopy(row[key]) for key in static if key in row}
            if row.get('detector') is not True or source!='selected_panel':
                # Static detector knowledge is only ever a positive single-panel
                # observation; unknown stays omitted and roster/view rows never
                # persist it.
                facts.pop('detector',None)
            dynamic={key:deepcopy(value) for key,value in row.items() if key not in static}
            if 'fully_known' in row:
                # The static marker is retained in public_reference_v1 above, but
                # a current panel answer must also say whether it is confirmed.
                dynamic['fully_known']=deepcopy(row['fully_known'])
            if source=='selected_panel' and row.get('detector') is True:
                dynamic['detector']=True
            own=(row.get('owned') is True or row.get('relation')=='own' or source=='own_units' or
                 (source=='current_selection' and result.get('owned') is True))
            if row.get('owned') is False or row.get('relation') not in (None,'own'):own=False
            for key in ('hp','shields','energy'):
                if key=='energy' and not own:
                    dynamic.pop(key,None)
                    continue
                value=row.get(key)
                if isinstance(value,dict) and 'maximum' in value:
                    facts[key]={'maximum':value['maximum']}
                    dynamic[key]={part:val for part,val in value.items() if part!='maximum'}
            token=row.get('type_token') or ('name:'+row['name'] if row.get('name') else None)
            if token:
                notice_args={'notice_run_ref':self.runtime.run_ref} if durable else {}
                changed=(remember(token,source,facts,name=row.get('name'),owner_token=row.get('owner_token'),
                                  merge_static=True,**notice_args)
                         if applicable else None)
                dynamic['knowledge_token']=token
                if source=='own_units' and 'type_token' in dynamic:
                    # The owned roster already carries an opaque entity token;
                    # keep its lean wire schema and bind knowledge by knowledge_token.
                    dynamic.pop('type_token')
                if changed and not durable:notices.append(changed)
            return dynamic
        if source=='command_card' and isinstance(learned_group_card,dict):
            # A group card teaches visible labels for its shared type/owner, but
            # not a single unit's complete skill signature. remember_public_reference
            # deduplicates identical facts, so later scans cannot spam notices.
            type_token=learned_group_card.get('type_token')
            skills=learned_group_card.get('skills')
            basics=learned_group_card.get('basic_commands')
            if (isinstance(type_token,str) and type_token
                    and ((isinstance(skills,list) and skills)
                         or (isinstance(basics,list) and basics))):
                facts={}
                if isinstance(skills,list):facts['skills']=deepcopy(skills)
                if isinstance(basics,list):facts['basic_commands']=deepcopy(basics)
                if facts:
                    notice_args={'notice_run_ref':self.runtime.run_ref} if durable else {}
                    changed=remember(type_token,'group_command_card',facts,
                        name=learned_group_card.get('name'),
                        owner_token=learned_group_card.get('owner_token'),
                        merge_static=True,**notice_args)
                    if changed and not durable:notices.append(changed)
        clean=deepcopy(result)
        if source=='selected_panel' and isinstance(clean.get('panel'),dict):clean['panel']=project(clean['panel'])
        elif source in ('own_units','observe_view','current_selection'):
            field='members' if source=='current_selection' else 'units'
            if isinstance(clean.get(field),list):clean[field]=[project(row) for row in clean[field]]
        if durable:
            existing={item['identity'] for row in records for item in row.get('_reference_notices',[])}
            pending=[row for row in knowledge.pending_reference_notices(
                scope=knowledge.scope,run_ref=self.runtime.run_ref,limit=PAGE_ROWS)
                if row['identity'] not in existing]
            if pending:records.append(self._reference_notice_record(pending))
        if notices:
            records.append({'source':'knowledge_updates','update':'append',
                            'result':{'status':'available','source':'knowledge','entries':notices}})
        return clean

    def tick(self, *, force=False):
        """Complete a scan for startup/offline callers; live workers use step."""
        if self._scan_iterator is not None:
            for _ in self._scan_iterator:
                pass
            self._scan_iterator = None
        for _ in self._scan(force=force):
            pass

    def flush_partial(self, *, before_action=False):
        """Publish earlier slices before a foreground action changes tickets.

        Keep the same list/dict objects: the suspended generator owns them too.
        Source cursors only advance with this durable information handoff.
        """
        records,advances=self._scan_records,self._scan_advances
        if before_action and records:
            # Foreground feedback replaces these volatile permission snapshots.
            # Never enqueue an older "current" ticket behind that feedback.
            # Learned panel facts, history and events remain intact.
            for record in records:
                if record['source'] in ('current_selection','command_card'):
                    # An omitted observation was never delivered: it must not
                    # suppress a later legal automatic sample/name receipt.
                    self.last.pop(record['source'],None)
            records[:]=[record for record in records
                        if record['source'] not in ('current_selection','command_card')]
        if records:
            self.publish(records,advances=advances)
            records.clear()
            advances.clear()
        elif advances:
            self.outbox.advance(advances)
            advances.clear()

    @property
    def scan_pending(self):
        return self._scan_iterator is not None

    def step(self):
        """Advance one logical read on the owning worker, never a second thread."""
        if self._scan_iterator is not None and self._scan_api is not self.runtime.api:
            self.flush_partial()
            self._scan_iterator.close()
            self._scan_iterator = None
        if self._scan_iterator is None:
            self._scan_api = self.runtime.api
            self._scan_iterator = self._scan()
        started = time.perf_counter()
        try:
            name = next(self._scan_iterator)
        except StopIteration:
            self._scan_iterator = None
            self._scan_records = self._scan_advances = None
            name = 'complete'
        elapsed = (time.perf_counter()-started)*1000
        self.last_step = {'name':name,'elapsed_ms':elapsed,'budget_ms':334,
                          'over_budget':elapsed>334}
        return self.last_step

    def pump_events_only(self):
        """Drain new journal events without any gameplay observation or action.

        Used only by a held session that must keep cutscene/transmission text
        flowing. It shares the normal scan's durable event cursor, so a later
        full scan never emits the same event twice. View, terrain, selection,
        resources and every native action remain untouched.
        """
        rt = self.runtime
        events = getattr(rt, 'events', None)
        outbox = self.outbox
        run_ref = getattr(rt, 'run_ref', None)
        if events is None or outbox is None or not isinstance(run_ref, str):
            return None
        source = 'events:' + run_ref
        page = self._call(events.read, limit=PAGE_ROWS, cursor=outbox.cursor(source))
        records, advances = [], {}
        self._record(records, 'events_head', {k: v for k, v in page.items()
                                              if k not in ('events', 'next_cursor', 'has_more')})
        new_events = page.get('events') or []
        if new_events:
            self._record(records, 'events', page, append=True)
            advances[source] = page['next_cursor']
            if any(isinstance(ev, dict) and ev.get('kind') in self.DISCRETE_THREAT_EVENTS
                   for ev in new_events):
                self._combat_until = max(self._combat_until,
                                         time.monotonic() + self.combat_hold_seconds)
        if records:
            self.publish(records, advances=advances)
        elif advances:
            outbox.advance(advances)
        return page

    @staticmethod
    def _dynamic_unit(row):
        fields = ('appearance_token','type_token','owner_token','name','relation','rescuable','position',
                  'hp','shields','energy','armor_upgrade','ground_attack_upgrade','air_attack_upgrade',
                  'work','work_progress')
        result = {key:deepcopy(row[key]) for key in fields if key in row}
        for field in ('hp','shields','energy'):
            if isinstance(result.get(field),dict):
                result[field] = {key:value for key,value in result[field].items() if key != 'maximum'}
        return result

    def _cache_feedback(self, source, result):
        if source in ('resources','state','current_selection','command_card'):
            value = deepcopy(result)
        elif source=='own_units':
            value={key:deepcopy(result[key]) for key in
                   ('status','reason','temporal','observed_tick_ms','count','counts_by_name','membership_semantics') if key in result}
        elif source=='observe_view':
            value = {'status':result.get('status'),
                     'observed_tick_ms':result.get('observed_tick_ms'),'information_source':'observe_view'}
            if isinstance(result.get('units'),(list,dict)) and result.get('status')=='observed':
                value['count']=len(result['units'])
            for key in ('reason','temporal'):
                if key in result:value[key]=deepcopy(result[key])
        else:
            return
        cached={'fresh':False,
            'observed_at_utc':datetime.now(timezone.utc).isoformat(),'result':value}
        if source in ('current_selection','command_card'):
            cached['feedback_revision']=self.feedback_revision
        self.feedback_cache[source] = cached

    def action_feedback(self, request, result, *, enrich_selection=False):
        """Fresh next-action tickets, plus explicitly dated background state.

        No terrain or static unit properties are expanded. Each read has its
        own observation time: this is deliberately not an atomic world view.
        """
        self.feedback_revision += 1
        feedback = {'schema':'scapi.action-feedback.v1','non_atomic':True,
                    'revision':self.feedback_revision,
                    'target':deepcopy(self.pending_target),'mode':self.target_mode}
        if self.last_preview and isinstance(request,dict) and self.last_preview['request_id']==request.get('id'):
            feedback['placement_preview']=deepcopy(self.last_preview)
        unavailable = {'fresh':False,'observed_at_utc':None,
                       'result':{'status':'unavailable','reason':'not_sampled'}}
        for public, source in (('resources','resources'),('state','state'),
                               ('own_units','own_units'),('units','observe_view')):
            feedback[public] = deepcopy(self.feedback_cache.get(source,unavailable))
        rt = self.runtime
        if (rt.api is None or rt.finished or rt.failed or result.get('status') == 'outcome_unknown'):
            feedback.update(status='unavailable',selection=deepcopy(unavailable),command_card=deepcopy(unavailable))
            return feedback
        try:
            res_method = getattr(rt.api, 'resources', None)
            if callable(res_method):
                try:
                    fresh_res = res_method()
                    if isinstance(fresh_res, dict) and fresh_res.get('status') == 'observed':
                        self._cache_feedback('resources', fresh_res)
                        feedback['resources'] = {
                            'fresh': True,
                            'observed_at_utc': datetime.now(timezone.utc).isoformat(),
                            'result': deepcopy(fresh_res),
                        }
                except Exception:
                    pass
            resource=self._selected_resource_feedback(request,result)
            if resource is not None:
                feedback['resource_sites']={'fresh':True,'observed_at_utc':datetime.now(timezone.utc).isoformat(),
                                            'result':resource}
            if self._selection_requested(request,result):
                # The action already supplied the live ticket. Enrich it through
                # Stage5's selected_own_values path without another base probe.
                selection=self._selection_feedback(result['selection'])
            elif isinstance(result.get('selection'),dict):
                selection = {'selection':deepcopy(result['selection'])}
            else:
                # The base ticket-only probe avoids Stage5.current_selection's
                # separate all-own-entity scan on the action critical path.
                from .actions import Actions
                selection = Actions.current_selection(rt.api)
                if (enrich_selection and isinstance(selection.get('selection'),dict)
                        and type(selection['selection'].get('count')) is int
                        and selection['selection']['count']>=1):
                    selection=self._selection_feedback(selection['selection'])
            feedback['selection'] = {'fresh':True,'observed_at_utc':datetime.now(timezone.utc).isoformat(),
                                     'result':selection}
            if selection.get('status') == 'outcome_unknown':
                rt.failed = True
                feedback.update(status='outcome_unknown',command_card=deepcopy(unavailable))
                return feedback
            self._cache_feedback('current_selection',selection)
            ticket = selection.get('selection')
            if ticket and ticket.get('count'):
                card = text_read(lambda **kw:rt.dispatch('command_card',{'ticket':ticket,**kw}),encoding='cp1252')
            else:
                card = {'status':'unavailable','reason':'no_selection'}
            feedback['command_card'] = {'fresh':True,'observed_at_utc':datetime.now(timezone.utc).isoformat(),
                                        'result':card}
            self._cache_feedback('command_card',card)
            if card.get('status') == 'outcome_unknown':
                rt.failed = True
                feedback['status'] = 'outcome_unknown'
            else:
                feedback['status'] = 'ok'
            if card.get('status') == 'observed' and card.get('mode') not in ('placing','targeting'):
                self.pending_target = self.target_mode = None
                feedback['target'] = feedback['mode'] = None
        except (TypeError,ValueError,KeyError,AttributeError):
            feedback.update(status='unavailable',reason='feedback_not_available')
            feedback.setdefault('selection',deepcopy(unavailable))
            feedback.setdefault('command_card',deepcopy(unavailable))
        except Exception:
            # A known action remains known even if a later observation fails.
            # The worker returns its result/marker, then closes without replay.
            rt.failed = True
            feedback.update(status='unavailable',reason='feedback_read_failed')
            feedback.setdefault('selection',deepcopy(unavailable))
            feedback.setdefault('command_card',deepcopy(unavailable))
        return feedback

    @staticmethod
    def _remembered_building_signature(row):
        return _json({'appearance_token':row.get('appearance_token'),
                      'last_seen_position':row.get('last_seen_position'),
                      'currently_observed':row.get('currently_observed'),
                      'name':row.get('name')})

    def _remembered_buildings_cache(self,api,memory_source):
        scope=(getattr(self.runtime,'run_ref',None),memory_source,
               getattr(api,'_pid',None),getattr(api,'_creation',None),
               getattr(api,'_epoch',None))
        if scope!=self._remembered_buildings_scope:
            self._remembered_buildings_scope=scope
            self._remembered_buildings_seen={}
        return self._remembered_buildings_seen

    def _filter_remembered_buildings(self,memory,memory_source,api):
        """Drop rows whose only movement is their observation clock.

        A repeated record token with an unchanged stable signature is not a new
        fact. Rows without a stable token and non-observed pages fail open.
        Suppressing every row never suppresses the cursor advance: the caller
        applies that from the original page.
        """
        if (not isinstance(memory,dict) or memory.get('status')!='observed'
                or not isinstance(memory.get('buildings'),list)):
            return memory
        seen=self._remembered_buildings_cache(api,memory_source)
        kept=[]
        for row in memory['buildings']:
            token=row.get('record_token') if isinstance(row,dict) else None
            if not isinstance(token,str) or not token:
                kept.append(row)
                continue
            signature=self._remembered_building_signature(row)
            if seen.get(token)!=signature:
                kept.append(row)
                seen[token]=signature
        if len(kept)==len(memory['buildings']):return memory
        filtered=dict(memory)
        filtered['buildings']=kept
        return filtered

    def _remembered_buildings_update(self,memory,memory_source,memory_start,advances):
        """Filter one changes page and advance its checkpoint independently."""
        filtered=self._filter_remembered_buildings(
            memory,memory_source,getattr(self.runtime,'api',None))
        if (isinstance(memory,dict) and memory.get('status')=='observed'
                and memory.get('checkpoint',memory_start)!=memory_start):
            # The checkpoint advances even when every row in this page was an
            # unchanged reobservation; otherwise the same page repeats forever.
            advances[memory_source]=memory['checkpoint']
        return filtered

    @staticmethod
    def _building_memory_status_key(status):
        counters=('pending','persisted_next_sequence','ack_next_sequence','write_sequence')
        stable={key:value for key,value in status.items() if key not in counters}
        stable['has_pending']=bool(status.get('pending'))
        return _json(stable)

    def _drain_name_refresh(self, api, records, advances):
        """Drain empty building-history name checks in one scan; emit at most one non-empty page."""
        progressed = False
        while self.name_refresh:
            kind = next(iter(self.name_refresh))
            name_key, after = self.name_refresh[kind]
            named = text_read(api.remembered_building_kind, kind=kind, limit=PAGE_ROWS, after_sequence=after)
            if named.get('buildings') or named.get('status') != 'observed':
                self._record(records, 'remembered_buildings', named, append=True, update='upsert')
            if named.get('status') == 'observed':
                progressed = True
                complete = not named.get('has_more')
                advances[name_key] = {'after': named['checkpoint'], 'complete': complete}
                if complete:
                    del self.name_refresh[kind]
                else:
                    self.name_refresh[kind] = (name_key, named['checkpoint'])
                if not named.get('buildings') and complete:
                    continue
            break
        # A failed name read must not force the scan loop to retry immediately;
        # retain the pending kind for the normal next-period backoff.
        return bool(self.name_refresh) and progressed

    def _drain_experience_selectors(self, rt, records):
        """Drain local experience pages; never schedules a continuous world scan."""
        role_stores = self._role_experiences(rt)
        diagnosed = False
        while self.selectors:
            item = self.selectors.pop(0)
            if len(item) == 3:
                selector, cursor, role = item
            else:
                selector, cursor = item
                role = None
            if role is not None and role_stores:
                store = role_stores.get(role)
                if store is None:
                    continue
                exp = text_read(store.read, limit=PAGE_ROWS, cursor=cursor,
                                author_role=role, **selector)
                if isinstance(exp, dict):
                    # Every entry already carries its role/scope; the page-level
                    # marker lets role delivery keep other roles' claims separate.
                    exp = dict(exp, author_role=role)
            else:
                store = getattr(rt, 'experience', None)
                if store is None:
                    continue
                exp = text_read(store.read, limit=PAGE_ROWS, cursor=cursor, **selector)
            if exp.get('status') != 'ok':
                # Re-inserting the same cursor with has_more=True and an
                # immediate next_due would busy-loop a corrupt/unavailable
                # notebook at 100% CPU. Emit one diagnostic, advance past an
                # explicit corrupt sequence, otherwise drop the selector.
                if not diagnosed:
                    self._record(records, 'experience', exp, append=True)
                    diagnosed = True
                corrupt = exp.get('corrupt_sequence')
                if (type(corrupt) is int and corrupt >= 0
                        and (cursor is None or corrupt > cursor)):
                    self.selectors.insert(0, (selector, corrupt, role)
                                          if role is not None else
                                          (selector, corrupt))
                continue
            if exp.get('has_more'):
                self.selectors.insert(0, (selector, exp['next_cursor'], role)
                                      if role is not None else
                                      (selector, exp['next_cursor']))
            if exp.get('entries'):
                self._record(records, 'experience', exp, append=True)
                break
            if exp.get('has_more'):
                break
        return bool(self.selectors)

    def _scan(self, *, force=False):
        now = time.monotonic()
        if not force and now < self.next_due:
            return
        self.next_due = now + self.period
        rt = self.runtime
        records, advances = [], {}
        self._scan_records,self._scan_advances=records,advances
        self._record(records, 'lifecycle_status', rt.lifecycle.status())
        if rt.api is None or not rt._ready():
            if records:
                self.publish(records)
            self._scan_records = self._scan_advances = None
            return
        api = rt.api
        self.flush_learning()
        yield 'learning'
        before_discrete = len(records)
        control = self._call(api.control_state)
        self._record_control(records, control)
        yield 'state'
        self._record(records, 'session', self._call(api.session))
        yield 'session'
        paced = self.calm_period > 0.0
        early_events_page = None
        if paced:
            source = 'events:' + rt.run_ref
            early_events_page = self._call(rt.events.read, limit=PAGE_ROWS, cursor=self.outbox.cursor(source))
            yield 'events'
            self._record(records, 'events_head', {k: v for k, v in early_events_page.items()
                                                  if k not in ('events', 'next_cursor', 'has_more')})
            new_events = early_events_page.get('events') or []
            if new_events:
                self._record(records, 'events', early_events_page, append=True)
                advances[source] = early_events_page['next_cursor']
                if any(isinstance(ev, dict) and ev.get('kind') in self.DISCRETE_THREAT_EVENTS for ev in new_events):
                    self._combat_until = max(self._combat_until, now + self.combat_hold_seconds)
            if self._has_visible_enemy(self.last_view):
                self._combat_until = max(self._combat_until, now + self.combat_hold_seconds)
            discrete_wake = (len(records) > before_discrete or bool(new_events)
                             or bool(early_events_page.get('has_more')))
            need_continuous = (force or discrete_wake or now >= self._next_continuous_due
                               or now < self._combat_until or self.last_view is None
                               or self.last_own is None
                               or (callable(getattr(api, 'resource_sites', None)) and self.last_resources is None))
            if not need_continuous:
                more_local = False
                if self.name_refresh:
                    more_local = self._drain_name_refresh(api, records, advances) or more_local
                    yield 'remembered_building_kind'
                if self.selectors:
                    more_local = self._drain_experience_selectors(rt, records) or more_local
                    yield 'experience'
                if more_local:
                    self.next_due = now
                if records:
                    self.publish(records, advances=advances)
                elif advances:
                    self.outbox.advance(advances)
                self._scan_records = self._scan_advances = None
                return
        self._record(records, 'minimap_geometry', self._call(api.minimap_geometry))
        yield 'minimap_geometry'
        self._record(records, 'resources', self._call(api.resources))
        yield 'resources'
        counters=getattr(api,'ui_counters',None)
        if callable(counters):
            self._record(records,'ui_counters',text_read(counters,encoding='cp1252'))
            yield 'ui_counters'
        resource_sites=getattr(api,'resource_sites',None)
        if callable(resource_sites):
            self._record(records,'resource_sites',self._call(resource_sites,
                unit_budget=UNIT_CAPACITY,include_selected_amount=False))
            yield 'resource_sites'
        own_units = text_read(api.own_units, unit_budget=UNIT_CAPACITY, encoding='cp1252')
        self._record(records, 'own_units', own_units)
        yield 'own_units'
        selection_args = {'encoding':'cp1252'}
        if (isinstance(own_units, dict) and own_units.get('status') == 'observed'
                and self._accepts_keyword(api.current_selection, 'reuse_own_snapshot')):
            selection_args['reuse_own_snapshot'] = True
        selection = text_read(api.current_selection, **selection_args)
        self._record(records, 'current_selection', selection)
        yield 'current_selection'
        selection_current = True
        peek = getattr(getattr(api,'_client',None),'peek',None)
        if callable(peek) and isinstance(selection.get('selection'),dict):
            current = peek()
            selected = selection['selection']
            selection_current = (current.get('epoch') == selected.get('epoch') and
                                 current.get('selection_generation') == selected.get('generation') and
                                 current.get('selection_count') == selected.get('count'))
        card = None
        if not selection_current:
            # A foreground action may have changed selection after this scan
            # yielded. Do not clear its newly returned enabled-button cache.
            pass
        elif selection.get('selection', {}).get('count'):
            ticket = selection['selection']
            panel = text_read(lambda **kw: rt.dispatch('selected_panel', {'ticket': ticket, **kw}), encoding='cp1252')
            panel_at=datetime.now(timezone.utc).isoformat()
            before_panel=len(records)
            self._record(records, 'selected_panel', panel)
            if len(records)>before_panel:
                records[-1].update(observed_at_utc=panel_at,observed_selection=deepcopy(ticket))
            yield 'selected_panel'
            current = peek() if callable(peek) else None
            if current is None or (current.get('epoch') == ticket.get('epoch') and
                    current.get('selection_generation') == ticket.get('generation') and
                    current.get('selection_count') == ticket.get('count')):
                card = text_read(lambda **kw: rt.dispatch('command_card', {'ticket': ticket, **kw}), encoding='cp1252')
                self._record(records, 'command_card', card)
            yield 'command_card'
            sites=getattr(api,'build_sites',None)
            if callable(sites):
                if self._build_sites_active(card, self.target_mode):
                    self._record(records,'build_sites',text_read(sites,encoding='cp1252'))
                else:
                    # Never pre-scan idle/mining/combat or ordinary selections.
                    self._record(records,'build_sites',{'status':'unavailable',
                        'reason':'not_in_build_or_land_mode'})
            yield 'build_sites'
        else:
            rt.buttons.clear()
            self._record(records, 'selected_panel', {'status': 'unavailable', 'reason': 'no_selection'})
            self._record(records, 'command_card', {'status': 'unavailable', 'reason': 'no_selection'})
            self._record(records, 'build_sites', {'status': 'unavailable', 'reason': 'no_selection'})
        self._record(records, 'observe_view', text_read(api.observe_view, unit_budget=UNIT_CAPACITY, encoding='cp1252'))
        yield 'observe_view'
        self._record(records, 'building_memory_status', self._call(api.building_memory_status))
        yield 'building_memory_status'
        memory_source = 'buildings:' + api._building_memory.session_key
        memory_start = self.outbox.cursor(memory_source) or 0
        memory = text_read(api.remembered_building_changes, limit=PAGE_ROWS, after_sequence=memory_start)
        yield 'remembered_building_changes'
        filtered_memory = self._remembered_buildings_update(
            memory, memory_source, memory_start, advances)
        if filtered_memory.get('buildings') or filtered_memory.get('status') != 'observed':
            # Upserts use stable public record tokens. Keep no old page cache.
            self._record(records,'remembered_buildings',filtered_memory,append=True,update='upsert')
        # Native type capacity is 228. This small current-name map is not a
        # building-history cache. A newly lawfully learned label enriches old
        # last-seen rows through the indexed kind feed, even when no building
        # observation sequence changed and that building remains in fog.
        for kind,name in api._knowledge._names.items():
            if self.known_names.get(kind) != name:
                self.known_names[kind]=name
                name_key=memory_source+':name:'+str(kind)+':'+hashlib.sha256(name.encode('utf-8')).hexdigest()
                checkpoint=self.outbox.cursor(name_key)
                if checkpoint is None or not checkpoint.get('complete'):
                    self.name_refresh[kind]=(name_key,(checkpoint or {}).get('after',0))
        names_more=False
        if self.name_refresh:
            names_more = self._drain_name_refresh(api, records, advances)
            yield 'remembered_building_kind'
        if early_events_page is not None:
            page = early_events_page
        else:
            source = 'events:' + rt.run_ref
            page = self._call(rt.events.read, limit=PAGE_ROWS, cursor=self.outbox.cursor(source))
            yield 'events'
            self._record(records, 'events_head', {k: v for k, v in page.items() if k not in ('events', 'next_cursor', 'has_more')})
            if page.get('events'):
                self._record(records, 'events', page, append=True)
                advances[source] = page['next_cursor']
                if any(isinstance(ev, dict) and ev.get('kind') in self.DISCRETE_THREAT_EVENTS for ev in page['events']):
                    self._combat_until = max(self._combat_until, now + self.combat_hold_seconds)
        if paced:
            if self._has_visible_enemy(self.last_view):
                self._combat_until = max(self._combat_until, now + self.combat_hold_seconds)
            self._next_continuous_due = now + (self.period if now < self._combat_until else self.calm_period)
        views = text_read(api.list_views)
        self._record(records, 'list_views', views)
        yield 'list_views'
        for view in views.get('views', []):
            if view.get('saved'):
                self._record(records, 'view:' + str(view['slot']), text_read(api.get_view, slot=view['slot']))
                yield 'saved_view'
        # The lifecycle registry is append-only. Seek its SQLite row identity;
        # do not cycle and cache every old list_saves page forever.
        save_source='saves:'+rt.profile_id
        after_save=self.outbox.cursor(save_source) or 0
        save_rows=rt.lifecycle.db.execute('SELECT rowid,id,name,created FROM lifecycle_slot '
            'WHERE profile=? AND rowid>? ORDER BY rowid LIMIT ?',
            (rt.profile_id,after_save,PAGE_ROWS)).fetchall()
        yield 'save_registry'
        if save_rows:
            self._record(records,'list_saves',{'status':'observed','saves':[
                {'slot':row[1],'name':row[2],'saved_at':row[3]} for row in save_rows]},append=True,update='upsert')
            advances[save_source]=save_rows[-1][0]
        elif save_source not in self.last:
            self._record(records,'list_saves',{'status':'observed','saves':[]},key=save_source,update='upsert')
        if page.get('has_more') or memory.get('has_more') or names_more or len(save_rows)==PAGE_ROWS:
            self.next_due = now
        # Stored claims remain author-authored. Selection of relevant history is
        # automatic and one page per cycle; a cursor never reaches the Agent.
        if self.selectors:
            if self._drain_experience_selectors(rt, records):
                self.next_due = now
            yield 'experience'
        if records:
            self.publish(records, advances=advances)
        elif advances:
            self.outbox.advance(advances)
        self._scan_records = self._scan_advances = None
