"""Single-consumer automatic lawful information and durable delivery.

Only PlayerSession's main worker calls this module. No mailbox or SQLite
connection is shared with the stdin reader or model transport.
"""
import json
import hashlib
import time
import uuid
from copy import deepcopy
from datetime import datetime, timezone


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
        self.pending_target = None
        self.target_mode = None
        self.known_names = {}
        self.name_refresh = {}
        self.last_control = None
        self.last_view = None
        self.last_view_scope = None
        self.last_view_identity = None
        self.last_view_availability_keys = set()
        self.last_own = None
        self.last_own_scope = None
        self.last_own_identity = None
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
        self.last_step = None
        self.last_preview = None
        self._completion_stream = uuid.uuid4().hex
        if outbox is not None:
            outbox.reference_knowledge=getattr(getattr(runtime,'api',None),'_knowledge',None)

    def configure(self, snapshot):
        value = snapshot['view_wait_ms']
        if type(value) is not int or value <= 0:
            raise ValueError('native automatic observation interval unavailable')
        self.period = value / 1000

    def remember_authoring(self, result):
        if result.get('entry_id'):
            self.selectors.append(({'entry_id': result['entry_id']}, None))

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

    def _call(self, method, **args):
        result = method(**args)
        if result.get('status') == 'outcome_unknown':
            raise RuntimeError('automatic observation outcome unknown; no mailbox retry')
        return result

    def _record(self, records, source, result, *, append=False, key=None, update=None):
        if result.get('status') == 'outcome_unknown':
            raise RuntimeError('automatic observation outcome unknown; no mailbox retry')
        if source=='resource_sites':
            self._record_resource_sites(records,result)
            return
        before_projection = len(records)
        clean = self._reference_projection(records,source,public_page(result))
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
            clean=project_automatic_view(clean)
        if source=='own_units' and not append:
            self._record_owned_roster(records,clean)
            rows=clean.get('units',[])
            for row in rows if isinstance(rows,list) else rows.values():
                name=row.get('name')
                if isinstance(name,str) and name and name not in self.seen_selectors:
                    self.seen_selectors.add(name);self.selectors.append(({'keyword':name},None))
            return
        self._cache_feedback(source, clean)
        if source=='session':self.last_session=deepcopy(clean)
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
                history=deepcopy(self.last_view)
                for name in self.last_view_availability_keys:
                    history.pop(name,None)
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
                    self.selectors.append(({'keyword': name}, None))

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
        semantic = {key:value for key,value in result.items() if key not in
                    ('observation_id','observed_tick_ms','observed_frame','received_monotonic_ns','last_seen_tick_ms')}
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

    def _record_owned_roster(self,records,clean):
        api=getattr(self.runtime,'api',None)
        scope=(getattr(self.runtime,'run_ref',None),getattr(api,'_pid',None),
               getattr(api,'_creation',None),getattr(api,'_epoch',None))
        identity=self._view_identity()
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

    def _reference_projection(self,records,source,result):
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
        static=('details','weapons','basic_commands','skills','build_options','build_options_status','supply_provided','fully_known')
        notices=[]
        durable=(self.outbox is not None and callable(getattr(knowledge,'pending_reference_notices',None)))
        if durable:self.outbox.reference_knowledge=knowledge
        def project(row):
            if not isinstance(row,dict):return row
            facts={key:deepcopy(row[key]) for key in static if key in row}
            dynamic={key:deepcopy(value) for key,value in row.items() if key not in static}
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
                changed=(remember(token,source,facts,name=row.get('name'),owner_token=row.get('owner_token'),**notice_args)
                         if applicable else None)
                dynamic['knowledge_token']=token
                if changed and not durable:notices.append(changed)
            return dynamic
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

    @staticmethod
    def _dynamic_unit(row):
        fields = ('appearance_token','type_token','owner_token','name','relation','position',
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
        self.feedback_cache[source] = {'fresh':False,
            'observed_at_utc':datetime.now(timezone.utc).isoformat(),'result':value}

    def action_feedback(self, request, result):
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
            resource=self._selected_resource_feedback(request,result)
            if resource is not None:
                feedback['resource_sites']={'fresh':True,'observed_at_utc':datetime.now(timezone.utc).isoformat(),
                                            'result':resource}
            if isinstance(result.get('selection'),dict):
                selection = {'selection':deepcopy(result['selection'])}
            else:
                # The base ticket-only probe avoids Stage5.current_selection's
                # separate all-own-entity scan on the action critical path.
                from .actions import Actions
                selection = Actions.current_selection(rt.api)
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
        self.last_control = self._call(api.control_state)
        self._record(records, 'state', self.last_control)
        yield 'state'
        self._record(records, 'session', self._call(api.session))
        yield 'session'
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
        self._record(records, 'own_units', text_read(api.own_units, unit_budget=UNIT_CAPACITY, encoding='cp1252'))
        yield 'own_units'
        selection = text_read(api.current_selection, encoding='cp1252')
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
                self._record(records,'build_sites',text_read(sites,encoding='cp1252'))
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
        if memory.get('buildings') or memory.get('status') != 'observed':
            # Upserts use stable public record tokens. Keep no old page cache.
            self._record(records,'remembered_buildings',memory,append=True,update='upsert')
        if memory.get('status') == 'observed' and memory.get('checkpoint',memory_start) != memory_start:
            advances[memory_source] = memory['checkpoint']
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
            kind=next(iter(self.name_refresh))
            name_key,after=self.name_refresh[kind]
            named=text_read(api.remembered_building_kind,kind=kind,limit=PAGE_ROWS,after_sequence=after)
            yield 'remembered_building_kind'
            if named.get('buildings') or named.get('status')!='observed':
                self._record(records,'remembered_buildings',named,append=True,update='upsert')
            if named.get('status')=='observed':
                complete=not named.get('has_more')
                advances[name_key]={'after':named['checkpoint'],'complete':complete}
                if complete:del self.name_refresh[kind]
                else:self.name_refresh[kind]=(name_key,named['checkpoint'])
            names_more=bool(self.name_refresh)
        source = 'events:' + rt.run_ref
        page = self._call(rt.events.read, limit=PAGE_ROWS, cursor=self.outbox.cursor(source))
        yield 'events'
        self._record(records, 'events_head', {k: v for k, v in page.items() if k not in ('events', 'next_cursor', 'has_more')})
        if page.get('events'):
            self._record(records, 'events', page, append=True)
            advances[source] = page['next_cursor']
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
            selector, cursor = self.selectors.pop(0)
            exp = text_read(rt.experience.read, limit=PAGE_ROWS, cursor=cursor, **selector)
            yield 'experience'
            if exp.get('entries'):
                self._record(records, 'experience', exp, append=True)
            if exp.get('has_more'):
                self.selectors.insert(0, (selector, exp['next_cursor']))
            if self.selectors:
                self.next_due = now
        if records:
            self.publish(records, advances=advances)
        elif advances:
            self.outbox.advance(advances)
        self._scan_records = self._scan_advances = None
