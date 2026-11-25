"""Stage-five actions through actual selection and the current native UI card.

The playing surface contains no native handlers, unit IDs, packet encodings,
unexplored build-site data or preloaded ability effects. A button token expires
when its native card changes. Enqueued means sent, never a completed effect.
"""
from dataclasses import asdict,dataclass
from pathlib import Path
import struct
import time
from .actions import SelectionTicket,action_result
from .stage4 import Stage4,LARVA_TYPE,_needs_learning
from .panel_card import PanelText,TextBudgetRequired,BASIC_ACTIONS
from tools.stage5_client import Client,State
from .view_bookmarks import BookmarkStore,validate_save
from .building_source import BuildingSource
from .building_memory import BuildingMemory
from .supply_knowledge import SupplyKnowledge,normalize_selected_supply
from .resources import resource_snapshot
from .selection_values import selected_own_values

@dataclass(frozen=True)
class ButtonTicket:
    pid:int
    process_creation:int
    epoch:int
    generation:int
    count:int
    card_generation:int
    slot:int

@dataclass(frozen=True)
class TargetTicket:
    pid:int
    process_creation:int
    epoch:int
    generation:int
    count:int
    target_generation:int
    queued_order:int=0

@dataclass(frozen=True)
class ProductionTicket:
    pid:int
    process_creation:int
    epoch:int
    generation:int
    count:int
    queue_generation:int

@dataclass(frozen=True)
class CargoTicket:
    pid:int
    process_creation:int
    epoch:int
    generation:int
    count:int
    cargo_generation:int
    slot:int

BUTTON_BYTES=32
PHASES={0:'ready',2:'targeting',3:'placing',4:'enqueued'}
SKILL_CALLBACKS={0x47a9f0,0x47acf0,0x47aff0,0x47b000,0x47b0a0,0x47b0b0}
# Native build placement callbacks (stage5_auto_panel.h build domain). Their
# presence on the live card is the open build submenu; the 4A17F0 opener alone
# is an ordinary worker command and never a build-site preview.
BUILD_PLACEMENT_HANDLERS=frozenset((0x47ad80,0x47adc0))
# Own-overview native flags. These stay private: public rows never expose them.
S5_OWN_BUILDING=1
S5_OWN_POSITION=2
S5_OWN_CARRIER_POSITION=4
S5_OWN_INCOMPLETE=8
S5_OWN_HIDDEN_HEALTH=16
S5_OWN_LOADED=32

def _selection(ticket):
    return SelectionTicket(**{key:getattr(ticket,key) for key in SelectionTicket.__dataclass_fields__})

class Stage5(Stage4):
    client_factory=Client

    def drain_observations(self,*,encoding='cp1252'):
        """Host-only serial drain; completed legal learning commits before ack.

        Called by the unique host worker periodically and before game actions.
        No Agent query, thread, selection or menu action is required. Build
        options are mission-permitted loaded submenu entries; current enabled
        state and operation references remain properties of the current card.
        """
        from .automatic_panel import drain_completed
        self._automatic_panel_encoding=encoding
        drain_completed(self,encoding=encoding)
        return {'status':'ok',**self._knowledge.pending_observations()}

    def ack_observations(self,receipts):
        """Host-only ack AFTER durable information outbox enqueue, not emit."""
        self._knowledge.ack_observations(receipts)
        return {'status':'ok'}

    def remembered_building_changes(self,*,after_sequence,limit,text_budget_bytes):
        """Host-only indexed updates; its checkpoint is an observation history."""
        if self._client.peek()['epoch']!=self._epoch:
            return {'status':'rejected','reason':'stale_epoch'}
        result=self._building_memory.query_changes(after_sequence=after_sequence,limit=limit,
            text_budget_bytes=text_budget_bytes,name_for_kind=self._knowledge.name)
        if 'memory_status' in result:
            result['memory_status']=self._public_building_status(result['memory_status'])
        return result

    def remembered_building_kind(self,*,kind,after_sequence=0,limit,text_budget_bytes):
        """Host-only indexed name enrichment of previously observed history."""
        if self._client.peek()['epoch']!=self._epoch:
            return {'status':'rejected','reason':'stale_epoch'}
        result=self._building_memory.query_kind(kind=kind,after_sequence=after_sequence,limit=limit,
            text_budget_bytes=text_budget_bytes,name_for_kind=self._knowledge.name)
        if 'memory_status' in result:
            result['memory_status']=self._public_building_status(result['memory_status'])
        return result

    def terrain_view(self,*,tile_budget):
        """Current lawful viewport grid, using the same world pixels as units."""
        from .terrain import terrain_view
        return terrain_view(self,tile_budget=tile_budget)

    def build_sites(self,*,text_budget_bytes,encoding):
        """Current lawful per-building starts before entering placement mode."""
        from .build_sites import build_sites
        return build_sites(self,text_budget_bytes=text_budget_bytes,encoding=encoding)

    def menu_state(self):
        """Trusted host-only read of native Game Menu and control ownership."""
        reply=self._request('menu-state',0,0,self._epoch)
        if not reply:return {'status':'outcome_unknown','reason':'native_menu_state_request_outcome_unknown',
            'diagnostic':{'operation':'menu-state','phase':'published_wait','publication':'unknown'},'automatic_retry':False}
        if reply['result']!='ok':
            result=action_result(reply)
            if 'diagnostic' in reply:result['diagnostic']=reply['diagnostic']
            return result
        state=reply['developer_after']
        if state['epoch']!=self._epoch:return {'status':'rejected','reason':'stale_epoch'}
        active=bool(state['active'] and state['tile_pointer'])
        control=('inactive' if not active else 'script' if state['control_locked'] else
                 'paused' if state['pause'] else 'player')
        return {'status':'observed','active':active,'paused':bool(state['pause']),
                'menu_open':bool(state['menu_open']),'control':control,
                'observed_tick_ms':state['executed_tick']}

    def menu_control(self,*,action):
        """Trusted host protection only, through the actual native Game Menu."""
        if action not in ('open','close'):raise ValueError('native Menu action must be open or close')
        reply=self._request('menu-'+action,0,0,self._epoch)
        if not reply:return {'status':'outcome_unknown','automatic_retry':False}
        if reply['result']!='ok':return action_result(reply)
        return {'status':'applied','action':action}

    def visible_phase(self,*,max_age_ms):
        """Host-only availability/freshness; v50 cannot emit a visible stage.

        max_age_ms is chosen by the host's current handoff contract. This read
        does not issue a game request, enable CU, arm actions or infer playing.
        The caller adds its own run/capture generation to the returned binding.
        """
        from ctypes import windll
        from .visible_phase import project_visible_phase
        before=self._client.peek()
        after=self._client.peek()
        return project_visible_phase(before,after,pid=self._pid,
            process_creation=self._creation,epoch=self._epoch,
            now_tick_ms=windll.kernel32.GetTickCount() & 0xffffffff,max_age_ms=max_age_ms)

    def placement_preview(self,ticket,x,y,*,anchor='main_building_top_left'):
        """Observe the current native placement, without submitting a command.

        The default anchor is the main building's tile-aligned top left. The
        response supplies engine_click_point for the existing target_at call,
        including the native offset when an Addon is being placed.
        """
        from .terrain import placement_preview
        return placement_preview(self,ticket,x,y,anchor=anchor)

    def observe_view(self,*,unit_budget,terrain_tile_budget=None,text_budget_bytes=65536,encoding='cp1252'):
        """Return current unit observations together with the viewport grid.

        Protocol 17 publishes both buffers in the same native request.
        The default grid budget covers the complete native viewport rather
        than truncating cells. An explicit smaller budget is also accepted.
        """
        if getattr(self._client,'protocol_version',16)<17:
            return super().observe_view(unit_budget=unit_budget,text_budget_bytes=text_budget_bytes,encoding=encoding)
        if type(unit_budget) is not int or unit_budget<1:
            raise ValueError('positive unit_budget required')
        before=self._client.peek()
        if terrain_tile_budget is None:
            width=before['width_tiles']*32-before['camera_max_x']
            height=before['height_tiles']*32-before['camera_max_y']
            if width<=0 or height<=0:
                return {'status':'rejected','reason':'uninitialized'}
            terrain_tile_budget=((width+31)//32+1)*((height+31)//32+1)
        if type(terrain_tile_budget) is not int or terrain_tile_budget<1:
            raise ValueError('positive terrain_tile_budget required')
        self._requested_terrain_budget=terrain_tile_budget
        try:result=super().observe_view(unit_budget=unit_budget,text_budget_bytes=text_budget_bytes,encoding=encoding)
        finally:self.__dict__.pop('_requested_terrain_budget',None)
        if result.get('status')!='observed':return result
        after=self._client.peek()
        if (before['epoch']!=self._epoch or
            any(after[k]!=before[k] for k in ('epoch','camera_x','camera_y','tile_pointer'))):
            return {'status':'rejected','reason':'observation_changed_during_read'}
        for unit in result['units']:
            p=unit.get('position')
            if p is not None:unit['tile_position']={'x':p['x']//32,'y':p['y']//32}
        return result

    def resource_sites(self,*,unit_budget,include_selected_amount=False):
        """Current resource locations in player vision, independent of the camera."""
        from .resource_sites import resource_sites
        return resource_sites(self,unit_budget=unit_budget,include_selected_amount=include_selected_amount)

    def ui_counters(self,*,text_budget_bytes=65536,encoding='cp1252'):
        """Current visible scoreboard/counter text, independent of selection."""
        from .ui_counters import ui_counters_snapshot,_monotonic
        from .observation_budget import ObservationReadBudget
        controls=64;budget=ObservationReadBudget(clock=_monotonic)
        while True:
            result=ui_counters_snapshot(self._client,self._epoch,
                text_budget_bytes=text_budget_bytes,control_budget=controls,encoding=encoding,_budget=budget)
            required=result.get('required_controls_at_least',0)
            if result.get('status')=='larger_observation_budget_required' and required>controls:
                controls=max(required,controls*2)
                continue
            required=result.get('required_bytes_at_least',0)
            if result.get('status')=='larger_text_budget_required' and required>text_budget_bytes:
                text_budget_bytes=max(required,text_budget_bytes*2)
                continue
            return result

    def _view_attachment(self,reply):
        if getattr(self._client,'protocol_version',16)<17:
            return {'status':'observed'}
        from .terrain import terrain_from_reply
        budget=self._requested_terrain_budget
        terrain=terrain_from_reply(self,reply,tile_budget=budget)
        if terrain.get('status')!='observed':return terrain
        if terrain['observed_frame']!=reply['developer_after']['raw_counter']:
            return {'status':'rejected','reason':'observation_changed_during_read'}
        return {'status':'observed','terrain':terrain}

    def own_units(self,*,unit_budget,text_budget_bytes,encoding):
        """Current own entities and positions, independent of the camera.

        One row per entity; no regional grouping, estimates or detailed stats.
        Text budget counts UTF-8 label bytes in all returned rows. Unsupported
        containment positions are explicit nulls, never old coordinates.
        Larva locations are omitted while their entities/names remain present.
        """
        if type(unit_budget) is not int or unit_budget<1:raise ValueError('positive unit_budget required')
        if type(text_budget_bytes) is not int or text_budget_bytes<1 or not isinstance(encoding,str) or not encoding:
            raise ValueError('explicit positive text budget and encoding required')
        r=self._request('own-overview',0,0,self._epoch)
        if not r:return {'status':'outcome_unknown','automatic_retry':False}
        if r['result']!='ok':return action_result(r)
        changed={'status':'rejected','reason':'observation_changed_during_read'}
        guard=struct.unpack('<I',self._client.reader.read(self._client.address+State.own_serial.offset,4))[0]
        if guard&1:return changed
        s=self._client.peek()
        if (s['epoch']!=self._epoch or s['own_serial']!=guard or s['state']!=3 or
            s['serial']!=r['serial'] or s['completed_serial']!=r['serial']):return changed
        count=s['own_count']
        if not 0<=count<=1700:raise RuntimeError('invalid own entity count')
        if count>unit_budget:return {'status':'larger_observation_budget_required','required_units':count}
        # Protocol 17 appends work names. Production is published only by
        # the current command card; old overview queue fields stay private.
        work_schema=self._client.protocol_version>=17
        lifetime_schema=self._client.protocol_version>=25
        stride=68 if lifetime_schema else 64 if work_schema else 28
        raw=self._client.reader.read(s['own_address'],count*stride) if count else b''
        # Source C strings require one terminator per distinct type; those
        # terminators are not part of the published UTF-8 text budget.
        text=PanelText(self._client.reader,text_budget_bytes+min(count*(8 if work_schema else 1),333 if work_schema else 228),encoding)
        supply=getattr(self,'_supply',None)
        supply_snapshot=supply.snapshot() if supply is not None else None
        names={};labels={};rows=[];by_identity={};by_command_identity={};types_by_command_identity={};seen=set();required=0
        eligibility={}
        learning_needed={}
        def label(address):
            if address not in labels:labels[address]=text.string(address)
            return labels[address]
        try:
            for record in struct.iter_unpack('<IIii13I' if lifetime_schema else '<IIii12I' if work_schema else '<IIiiIII',raw):
                identity,kind,x,y,flags,address,carrier=record[:7]
                if not identity or identity in seen or kind>=228 or flags&~63 or not address:
                    raise RuntimeError('invalid own entity record')
                if (flags&S5_OWN_INCOMPLETE) and not (flags&1):
                    raise RuntimeError('invalid own entity record')
                if lifetime_schema and identity>0x07ffffff:raise RuntimeError('invalid own observation identity')
                seen.add(identity)
                if flags&2 and not (0<=x<s['width_tiles']*32 and 0<=y<s['height_tiles']*32):
                    raise RuntimeError('invalid own position')
                if kind not in names:names[kind]=label(address)
                name=names[kind];required+=len(name.encode('utf8'))
                # The lawful observation serial is shared with visible rows;
                # the finite native command identity remains private below.
                row={'entity_token':self._knowledge.token('entity',f'lifetime:{identity}' if lifetime_schema else identity),
                     'type_token':self._knowledge.token('type',kind),
                     'name':name}
                # Private eligibility is derived only from this lawful row: a
                # mobile, positioned unit outside a building/carrier/hangar.
                eligibility[row['entity_token']]=(
                    kind!=LARVA_TYPE and bool(flags&S5_OWN_POSITION)
                    and not (flags&(S5_OWN_BUILDING|S5_OWN_CARRIER_POSITION|S5_OWN_LOADED)))
                if kind!=LARVA_TYPE:row['position']={'x':x,'y':y} if flags&2 else None
                if work_schema:
                    valid,queued,*work_names=record[7:16]
                    if valid not in (0,1) or queued>5 or (valid and not flags&1):
                        raise RuntimeError('invalid own work record')
                    if not valid and any(record[8:16]):raise RuntimeError('invalid unavailable own work record')
                    if valid:
                        queue_names=work_names[:5];research,upgrade=work_names[5:]
                        if (any(not p for p in queue_names[:queued]) or any(queue_names[queued:]) or
                            (research and upgrade)):
                            raise RuntimeError('invalid own work names')
                        research_name=label(research) if research else None
                        upgrade_name=label(upgrade) if upgrade else None
                        row['work']={'research':research_name,'upgrade':upgrade_name}
                        required+=sum(len(n.encode('utf8')) for n in (research_name,upgrade_name) if n is not None)
                learning_key=(kind,bool(flags&S5_OWN_INCOMPLETE),bool(flags&S5_OWN_HIDDEN_HEALTH))
                if learning_key not in learning_needed:
                    learning_needed[learning_key]=_needs_learning(
                        self._knowledge,kind,owned=True,
                        has_health=not learning_key[2],
                        incomplete_building=learning_key[1])
                if learning_needed[learning_key]:row['unlearned']=True
                amount=supply.value(kind,supply_snapshot) if supply is not None else None
                if amount is not None and amount>0:row['supply_provided']=amount
                command_identity=record[16] if lifetime_schema else identity
                if command_identity>>19 or not 1<=command_identity&2047<=1700 or command_identity in by_command_identity:
                    raise RuntimeError('invalid private own command identity')
                rows.append(row);by_identity[identity]=row;by_command_identity[command_identity]=row
                types_by_command_identity[command_identity]=kind
        except TextBudgetRequired:
            return {'status':'larger_text_budget_required','required_bytes_at_least':text_budget_bytes+1}
        after=self._client.peek()
        if (any(after[k]!=s[k] for k in ('epoch','own_serial','own_count','own_address','serial','completed_serial')) or
            after['state']!=3 or after['own_serial']&1):return changed
        if required>text_budget_bytes:
            return {'status':'larger_text_budget_required','required_bytes':required}
        for kind,name in names.items():self._knowledge.remember_name(kind,name)
        self._knowledge.commit()
        self._last_own_by_identity=by_identity
        self._last_own_by_command_identity=by_command_identity
        self._last_own_types_by_command_identity=types_by_command_identity
        self._last_own_epoch=s['epoch']
        result={'status':'observed','observed_tick_ms':s['executed_tick'],'units':rows}
        self.last_own_unit_eligibility=eligibility
        if lifetime_schema:result['identity_semantics']='lawful_observation_lifetimes; hidden_gaps_not_linked'
        return result

    def selected_members_for_ticket(self,ticket,*,text_budget_bytes=65536,encoding='cp1252',reuse_own_snapshot=False):
        """Join an already observed live ticket to lawfully selected own values.

        This is the same selected_own_values path as current_selection, without
        the redundant initial ticket probe when a foreground action already
        returned the live ticket. A missing local member falls back once to a
        fresh own snapshot; unselected or enemy pointers never acquire values.
        """
        if isinstance(ticket,dict):
            try:ticket=SelectionTicket(**{key:ticket[key] for key in SelectionTicket.__dataclass_fields__})
            except (KeyError,TypeError):return {'status':'rejected','reason':'invalid_selection_ticket'}
        if not isinstance(ticket,SelectionTicket):
            return {'status':'rejected','reason':'invalid_selection_ticket'}
        values=asdict(ticket)
        if any(type(value) is not int or value<0 for value in values.values()):
            return {'status':'rejected','reason':'invalid_selection_ticket'}
        if ticket.pid!=self._pid or ticket.process_creation!=self._creation:
            return {'status':'rejected','reason':'different_process'}
        if ticket.epoch>=2**32 or ticket.generation>=2**32 or not 0<=ticket.count<=12:
            return {'status':'rejected','reason':'invalid_selection_ticket'}
        before=self._client.peek()
        if before.get('epoch')!=self._epoch or before.get('epoch')!=ticket.epoch:
            return {'status':'rejected','reason':'stale_epoch'}
        base=before['game_base'];pool=base+(0x5b2e68-0x400000)
        selected=self._client.reader.read(base+(0x684940-0x400000),48)
        local=struct.unpack('<I',self._client.reader.read(base+(0x4f30c0-0x400000),4))[0]
        selected_pointers=[];count=0;seen=set()
        for pointer in struct.unpack('<12I',selected):
            if not pointer:continue
            count+=1
            if pointer in seen or pointer<pool or pointer>=pool+1700*0x148 or (pointer-pool)%0x148:
                return {'status':'rejected','reason':'selection_changed_during_read'}
            seen.add(pointer)
            owner=self._client.reader.read(pointer+0x4c,1)[0]
            unique=self._client.reader.read(pointer+0xa5,1)[0]
            identity=(pointer-pool)//0x148+1 | unique<<11
            selected_pointers.append((pointer,identity,owner==local))
        snapshot_ok=(reuse_own_snapshot and getattr(self,'_last_own_epoch',None)==self._epoch and
                     isinstance(getattr(self,'_last_own_by_command_identity',None),dict) and
                     isinstance(getattr(self,'_last_own_types_by_command_identity',None),dict))
        if snapshot_ok:
            by_command=self._last_own_by_command_identity
            types_by_command=self._last_own_types_by_command_identity
            snapshot_ok=all(identity in by_command and identity in types_by_command
                            for _,identity,owned in selected_pointers if owned)
        if not snapshot_ok:
            own=self.own_units(unit_budget=1700,text_budget_bytes=text_budget_bytes,encoding=encoding)
            if own['status']!='observed':return own
        members=[]
        for pointer,identity,owned in selected_pointers:
            if not owned:continue
            member=self._last_own_by_command_identity.get(identity)
            if member is None:continue
            row=dict(member)
            try:row.update(selected_own_values(self._client.reader,base,pointer,
                self._last_own_types_by_command_identity[identity],selected))
            except RuntimeError:return {'status':'rejected','reason':'selection_changed_during_read'}
            members.append(row)
        confirmation=self._request('probe',0,0,self._epoch)
        if not confirmation:return {'status':'outcome_unknown','automatic_retry':False}
        if confirmation['result']!='ok':return action_result(confirmation)
        after=confirmation['developer_after']
        if (after['epoch']!=ticket.epoch or after['selection_generation']!=ticket.generation or
                after['selection_count']!=ticket.count or count!=ticket.count):
            return {'status':'rejected','reason':'selection_changed_during_read'}
        return {'selection':asdict(ticket),'owned':count>0 and len(members)==count,'members':members}

    def current_selection(self,*,text_budget_bytes=65536,encoding='cp1252',reuse_own_snapshot=False):
        """Current real selection, with names/positions of its own members.

        Ownership lookup uses the same current own snapshot. Enemy selections
        do not acquire any off-screen name, position or dynamic value here.
        A caller that just sampled own_units in this epoch may reuse that
        snapshot; any missing local member falls back to one fresh sample.
        """
        result=super().current_selection()
        if 'selection' not in result:return result
        return self.selected_members_for_ticket(result['selection'],text_budget_bytes=text_budget_bytes,
            encoding=encoding,reuse_own_snapshot=reuse_own_snapshot)

    def __init__(self,pid,*,knowledge_path=None,bookmark_path=None,building_memory_path=None,
                 shared_knowledge_path=None,knowledge_mode='official_standard'):
        super().__init__(pid,knowledge_path=knowledge_path,
                         shared_knowledge_path=shared_knowledge_path,
                         knowledge_mode=knowledge_mode)
        self._group_card_dwell=None
        self._learned_group_card=None
        self._bookmarks=None
        self._building_source=None
        self._building_memory=None
        self._supply=None
        self.last_own_unit_eligibility={}
        try:
            self._supply=SupplyKnowledge(self._knowledge,self._rules)
            path=bookmark_path or Path(__file__).resolve().parents[1]/'build/stage5/view-bookmarks.sqlite3'
            scope=f'{self._pid}:{self._creation}:{self._client.address}:{self._epoch}'
            self._bookmarks=BookmarkStore(path,scope)
            self._building_source=BuildingSource(self._client,epoch=self._epoch)
            memory_path=building_memory_path or Path(__file__).resolve().parents[1]/'build/stage5/building-memory.sqlite3'
            self._building_memory=BuildingMemory(self._building_source,memory_path,scope)
        except BaseException:
            self.close();raise

    def close(self):
        persistence_failure=None
        if self._building_memory is not None:
            # Freeze the producer before draining so heartbeats cannot keep
            # extending shutdown; this does not pause or spend a game action.
            self._building_source.stop()
            status=self._building_memory.close()
            if not status['closed']:
                raise RuntimeError('building_memory_worker_still_running:'+str(status))
            self._building_memory=None
            if not status['flushed']:
                persistence_failure=RuntimeError('building_memory_not_flushed:'+str(status))
        try:
            if self._building_source is not None:
                self._building_source.close()
                self._building_source=None
            if self._bookmarks is not None:
                self._bookmarks.close()
                self._bookmarks=None
        finally:super().close()
        if persistence_failure:raise persistence_failure

    def resources(self):
        """Current own HUD resource/supply counters; free, read-only, no learning."""
        return resource_snapshot(self._client,self._epoch)

    def remembered_buildings(self,*,limit,text_budget_bytes,cursor=None):
        """Read learned building history; a historical record never proves life."""
        if self._client.peek()['epoch']!=self._epoch:
            return {'status':'rejected','reason':'stale_epoch'}
        result=self._building_memory.query(limit=limit,text_budget_bytes=text_budget_bytes,
            cursor=cursor,name_for_kind=self._knowledge.name)
        if 'memory_status' in result:
            result['memory_status']=self._public_building_status(result['memory_status'])
        return result

    def selected_panel(self,ticket,*,learn_details=False,text_budget_bytes=None,encoding=None):
        result=super().selected_panel(ticket,learn_details=learn_details,
            text_budget_bytes=text_budget_bytes,encoding=encoding)
        if (result.get('status')!='observed' or not result.get('panel',{}).get('owned') or
                not result.get('panel',{}).get('name')):
            return result
        state=self._client.peek();kind=state['panel_type']
        rules=self._supply.snapshot()
        # Learn fixed capacity on a named own provider selection. A positive
        # DAT byte alone is insufficient: generic unit panels do not render
        # provided supply. This is a provider-capable handler gate, not a claim
        # that its supply line remains visible in every busy/cargo panel mode.
        if not 0<=kind<228 or not rules[kind]:return result
        base=state['game_base'];pool=base+(0x5b2e68-0x400000)
        dispatch=self._client.reader.read(base+(0x4f7990-0x400000)+kind*12,12)
        dispatch_kind,condition,handler=struct.unpack('<III',dispatch)
        if dispatch_kind!=kind or handler not in (0x4a5700,0x4a7830):return result
        selected_address=base+(0x684940-0x400000)
        pointers=self._client.reader.read(selected_address,48)
        values=struct.unpack('<12I',pointers)
        present=[p for p in values if p]
        if len(present)!=1 or present[0]<pool or present[0]>=pool+1700*0x148 or (present[0]-pool)%0x148:
            return {'status':'rejected','reason':'selection_changed_during_read'}
        pointer=present[0]
        local=struct.unpack('<I',self._client.reader.read(base+(0x4f30c0-0x400000),4))[0]
        if local>=12 or self._client.reader.read(pointer+0x4c,1)[0]!=local:
            return {'status':'rejected','reason':'selection_changed_during_read'}
        unit=self._client.reader.read(pointer,0x148)
        try:value=normalize_selected_supply(unit,rules,expected_kind=kind,expected_owner=local)
        except RuntimeError:
            return {'status':'rejected','reason':'selection_changed_during_read'}
        confirmation=self._request('observe-selected',0,0,ticket.epoch,ticket.generation)
        if not confirmation:return {'status':'outcome_unknown','automatic_retry':False}
        if confirmation['result']!='ok':return action_result(confirmation)
        current=confirmation['developer_after']
        if (current['epoch']!=ticket.epoch or current['selection_generation']!=ticket.generation or
                not current['panel_owned'] or current['panel_type']!=kind or not current['panel_available'] or
                self._client.reader.read(selected_address,48)!=pointers or
                self._client.reader.read(pointer+0x4c,1)[0]!=local):
            return {'status':'rejected','reason':'selection_changed_during_read'}
        latest_unit=self._client.reader.read(pointer,0x148)
        try:latest=normalize_selected_supply(latest_unit,self._supply.snapshot(),expected_kind=kind,expected_owner=local)
        except RuntimeError:
            return {'status':'rejected','reason':'selection_changed_during_read'}
        if latest!=value or latest_unit[0xa5]!=unit[0xa5]:
            return {'status':'rejected','reason':'observation_changed_during_read'}
        if self._client.reader.read(base+(0x4f7990-0x400000)+kind*12,12)!=dispatch:
            return {'status':'rejected','reason':'observation_changed_during_read'}
        amount=self._supply.learn(value['kind'],value['raw'])
        if amount>0:result['panel']['supply_provided']=amount
        return result

    @staticmethod
    def _public_building_status(status):
        # Full diagnostic exceptions belong in the local log, not in a playing
        # observation that could otherwise expose process addresses or paths.
        result={key:status[key] for key in ('pending','fault','enabled','ready','closed','fatal',
            'persisted_next_sequence','ack_next_sequence','write_sequence') if key in status}
        result['storage_error']='building_history_persistence_failed' if status.get('storage_error') else None
        result['source_error']='building_observation_read_failed' if status.get('source_error') else None
        return result

    def building_memory_status(self):
        status=self._public_building_status(self._building_memory.status())
        status['session_changed']=self._client.peek()['epoch']!=self._epoch
        return status

    @staticmethod
    def _group_slot(slot):
        if type(slot) is not int or not 0<=slot<=9:raise ValueError('group slot must be integer 0 through 9')

    def assign_group(self,ticket,slot):
        self._group_slot(slot)
        if not self._validate_ticket(ticket):return {'status':'rejected','reason':'different_process'}
        r=self._request('group-assign',0,0,ticket.epoch,ticket.generation,extra={'group_slot':slot})
        if not r:return {'status':'outcome_unknown','automatic_retry':False}
        return {'status':'enqueued','slot':slot} if r['result']=='ok' else action_result(r)

    def select_group(self,slot):
        self._group_slot(slot)
        r=self._request('group-select',0,0,self._epoch,extra={'group_slot':slot})
        if not r:return {'status':'outcome_unknown','automatic_retry':False}
        if r['result']!='ok':return action_result(r)
        s=r['developer_after']
        return {'status':'selected','slot':slot,'selection':asdict(SelectionTicket(
            self._pid,self._creation,s['epoch'],s['selection_generation'],s['selection_count']))}

    def focus_selected(self,ticket):
        if not self._validate_ticket(ticket):return {'status':'rejected','reason':'different_process'}
        r=self._request('focus-selected',0,0,ticket.epoch,ticket.generation)
        if not r:return {'status':'outcome_unknown','automatic_retry':False}
        if r['result']!='ok':return action_result(r)
        s=r['developer_after']
        return {'status':'applied','camera':{'x':s['actual_x'],'y':s['actual_y']}}

    def _bookmark_session(self):
        r=self._request('probe',0,0,self._epoch)
        if not r:return {'status':'outcome_unknown','automatic_retry':False}
        if r['result']!='ok':return action_result(r)
        if r['developer_after']['epoch']!=self._epoch:return {'status':'rejected','reason':'stale_epoch'}
        return None

    @staticmethod
    def _bookmark_result(result):
        if result['status']=='text_budget_required':return {**result,'status':'larger_text_budget_required'}
        return result

    def save_view(self,slot,*,label,note,text_budget_bytes):
        valid=validate_save(slot,label,note,text_budget_bytes=text_budget_bytes)
        if valid['status']!='ready':return self._bookmark_result(valid)
        failure=self._bookmark_session()
        if failure:return failure
        r=self._request('save-view',0,0,self._epoch)
        if not r:return {'status':'outcome_unknown','automatic_retry':False}
        if r['result']!='ok':return action_result(r)
        s=r['developer_after']
        return self._bookmark_result(self._bookmarks.save(slot,s['actual_x'],s['actual_y'],label,note,
            text_budget_bytes=text_budget_bytes,overwrite=True))

    def list_views(self,*,text_budget_bytes):
        failure=self._bookmark_session()
        return failure or self._bookmark_result(self._bookmarks.list(text_budget_bytes=text_budget_bytes))

    def get_view(self,slot,*,text_budget_bytes):
        failure=self._bookmark_session()
        return failure or self._bookmark_result(self._bookmarks.get(slot,text_budget_bytes=text_budget_bytes))

    def recall_view(self,slot):
        failure=self._bookmark_session()
        if failure:return failure
        coords=self._bookmarks.read_coords(slot)
        if coords is None:return {'status':'rejected','reason':'empty_slot','slot':slot}
        return self.camera(*coords)

    @staticmethod
    def control_guide():
        """Current interface contracts; automatic information, no play advice."""
        return {
            'information':'The player host automatically acquires and delivers lawful selection, panel, command-card, resources, unit, terrain, placement, history and notification information. Agent queries and learn_details are not prerequisites. Internal read methods remain host implementation details.',
            'learning':'A legal single selection continuously visible for details_wait_ms automatically begins and completes detailed learning. Repeated reads, including the legacy learn_details flag, do not restart attention. Changed selection, camera, visible numerical/skill rules or mission building permissions require a fresh interval. Temporary redraw suppression of an unchanged previously visible card preserves timing; cached identity supplies no new skill/cost evidence or current action permission. Completed observations preserve their original epoch/timestamp even after selection changes.',
            'delivery':'Completed panel values and public observation payloads commit in PanelKnowledge before native storage is acknowledged. The host durably queues observations before acknowledging handoff receipts. Unacknowledged facts replay with a stable observation_id; replay does not assert the old unit is still selected or alive.',
            'build_options':'After the detail interval, build_options contains this mission-permitted building labels reachable in the actual selected own actor loaded button-set submenu graph. availability=allowed_in_mission does not assert current resources or prerequisites. build_options_status=unsupported_loaded_buttonset explicitly marks an unsupported graph; no partial list is presented as complete. Discovery does not open or mutate real menus. Current enabled flags and actionable button tickets come from the actual current command card.',
            'skills':'Legally learned skill names are reused on current own group or offscreen command cards only when the actual loaded label matches knowledge for a selected member type. New names and costs require legal detailed learning. Current enabled state is distinct from learned cost/name knowledge.',
            'progress':'A lawful selected own panel can report work_progress.percent from its actual visible progress bar after the detail interval. It has no remaining-time estimate. Offscreen own work exposes verified project/queue names, never hidden progress or timers.',
            'selection':'Actual native selection has at most twelve members. Its ticket remains valid across camera movement when selection identity is unchanged. Orders require own actors; targets obey current viewport/minimap conditions. Starting production, research, upgrades or construction requires the actual actors on screen.',
            'budget':'Camera, selection and native commands/cancellations share the configured action interval, at least 334ms and at most three operations per second. Automatic information and learning consume no game action. Sequential cancellation charges each actual entry.',
            'terrain':'Terrain uses world pixels, 32-pixel tiles and 8-pixel walk cells. Geometry, current occupancy and last-observed fog state have distinct evidence and timestamps. Unknown remains unknown. Height levels and terrain categories are separate. Walkability is not a route or unit-clearance guarantee.',
            'placement':'Preview exists only during a real pending placement and describes main/Addon footprints plus engine_click_point. valid describes buildability, not route clearance. Explored-fog valid=null/can_issue_target=true leaves dynamic revalidation to native arrival. Unexplored or UI-obscured placement remains unavailable.',
            'history':'Building history preserves lawful past sightings. currently_observed never asserts hidden survival or destruction. Host change cursors are separate from paged history cursors; persistence/capture failures are explicit.',
            'weapon_feedback':'Qualified onscreen own impacts can include concrete weapon name/token; offscreen warnings disclose neither weapon nor attacker. Impacts do not identify the attacker or prove final HP loss. Learned associations reuse only after current rule/mapping/known-label checks; no encyclopedia is preloaded.',
            'resources':'Own HUD minerals/gas and displayed race supply are automatically observed. Learned supply_provided is fixed type capacity, distinct from player total/remaining supply.',
            'groups':'Native group slots0..9 preserve eligible actual own members. select_group and focus_selected are distinct charged selection/camera actions.',
            'view_bookmarks':'Slots2,3,4 store labeled camera bookmarks in the current session. save replaces a slot; recall is a charged camera action.',
            'commands':'Agent issues gameplay actions through current operation tickets. target_required includes a pending target ticket. cancel_pending invalidates that pending target; it does not cancel ongoing orders. queued=True appends supported ordinary orders; production uses its separate native queue.',
            'transport':'The actual current card supplies Load/Unload buttons and passenger tickets. unload_one affects one current cargo icon. Cargo snapshots and tickets expire when their native generation changes.',
            'production':'Per-slot cancellation uses the current production ticket; enqueued confirms submission only. Internal batch helpers stop after the first charged submission with cancellation_effect_unconfirmed and no automatic retry.',
            'construction':'halt_construction frees a Terran worker while retaining the unfinished building. resume_construction and cancel_construction remain native game commands and use native recovery/refund rules.',
            'outcomes':'Enqueued confirms submission, not completion. Automatic lawful observations report effects. Unknown outcomes are not automatically resubmitted. Native observation_pending is a definite unexecuted action, internally persisted and resubmitted once without charging the blocked attempt.'}

    def _typed_ticket(self,ticket,kind):
        if not isinstance(ticket,kind):raise ValueError('matching operation ticket required')
        if not self._validate_ticket(_selection(ticket)):return False
        for key,value in asdict(ticket).items():
            if type(value) is not int or value<0:raise ValueError('invalid operation ticket')
            if key not in ('process_creation',) and value>=2**32:raise ValueError('operation ticket overflow')
        return True

    def _result(self,reply,ticket):
        if not reply:return {'status':'outcome_unknown','automatic_retry':False}
        if reply['result']!='ok':return action_result(reply)
        state=reply['developer_after'];phase=state['command_phase']
        if phase in (2,3):
            if ticket is None:return {'status':'outcome_unknown','automatic_retry':False}
            target=TargetTicket(**asdict(ticket),target_generation=state['target_serial'],
                                queued_order=state['pending_queued_order'])
            return {'status':'target_required','mode':PHASES[phase],'target':asdict(target)}
        return {'status':'enqueued' if phase==4 else 'applied'}

    def consume_learned_group_card(self):
        """Pop this call's internal group-card learning handoff.

        The host consumes it for public-reference projection. It is deliberately
        never part of ``command_card``'s public return dict.
        """
        learned=getattr(self,'_learned_group_card',None)
        self._learned_group_card=None
        return learned

    def _observe_group_command_card(self,state,member_types,member_stamp,
                                    skill_items,basic_items,buttons):
        """Learn a stable homogeneous multi-selection card after its dwell.

        Box-selecting 2..12 units exposes only the shared 3x3 command card. This
        records visible labels; it never claims single-unit armor, attack, HP,
        production or cargo facts.
        """
        count=state.get('selection_count');kinds=set(member_types)
        paused=bool(state.get('paused',False) or state.get('pause',False))
        if (type(count) is not int or not 2<=count<=12
                or not state.get('active') or paused or state.get('menu_open')
                or len(kinds)!=1):
            self._group_card_dwell=None
            return
        kind=next(iter(kinds))
        skill_items=tuple(sorted(skill_items))
        basic_items=tuple(sorted(basic_items))
        stamp=(state.get('epoch'),state.get('selection_generation'),kind,
               skill_items,basic_items)
        raw_tick=state.get('real_tick')
        now=raw_tick if type(raw_tick) is int else time.monotonic_ns()//1_000_000
        wait_ms=state.get('details_wait_ms')
        if type(wait_ms) is not int or wait_ms<=0:wait_ms=2000
        # Human-visible details stay on screen for a real dwell: never accept a
        # native/task value below the two-second learning floor.
        wait_ms=max(2000,wait_ms)
        dwell=getattr(self,'_group_card_dwell',None)
        if dwell is None or dwell[0]!=stamp or now<dwell[1]:
            # A backwards/restarted tick is not elapsed dwell; begin a fresh
            # interval instead of treating the wrapped difference as complete.
            self._group_card_dwell=(stamp,now)
            return
        if ((now-dwell[1])&0xFFFFFFFF)<wait_ms:return
        self._knowledge.learn_skill_names(kind,[{'slot':slot,'name':name}
                                                for slot,name in skill_items])
        self._knowledge.commit()
        visible={slot:name for slot,name in skill_items}
        for item in buttons:
            if item.get('slot') in visible:item['name']=visible[item['slot']]
        if skill_items or basic_items:
            owner=(member_stamp[1] if isinstance(member_stamp,(tuple,list))
                   and len(member_stamp)>1 else None)
            self._learned_group_card={
                'type_token':self._knowledge.token('type',kind),
                'owner_token':self._knowledge.token('owner',owner) if owner is not None else None,
                'name':self._knowledge.name(kind),
                'skills':[{'slot':slot,'name':name} for slot,name in skill_items],
                'basic_commands':[{'slot':slot,'name':name} for slot,name in basic_items]}

    def command_card(self,ticket,*,text_budget_bytes,encoding):
        self._learned_group_card=None
        if not self._validate_ticket(ticket):return {'status':'rejected','reason':'different_process'}
        if type(text_budget_bytes) is not int or text_budget_bytes<1 or not encoding:
            raise ValueError('explicit positive text budget and encoding required')
        # Current group/offscreen cards reuse learned names without requiring a
        # fresh single visible panel. Raw labels are private comparisons only.
        reply=self._request('command-card',0,0,ticket.epoch,ticket.generation)
        if not reply:return {'status':'outcome_unknown','automatic_retry':False}
        if reply['result']!='ok':return action_result(reply)
        # Acquire a publication guard before state, not merely after parsing:
        # state and cargo rows are separate ReadProcessMemory calls.
        changed={'status':'rejected','reason':'observation_changed_during_read'}
        serial_address=self._client.address+State.command_sample_serial.offset
        guard=struct.unpack('<I',self._client.reader.read(serial_address,4))[0]
        if guard&1:return changed
        state=self._client.peek()
        if (state['command_sample_serial']!=guard or state['epoch']!=ticket.epoch or
            state['selection_generation']!=ticket.generation or state['completed_serial']!=reply['serial'] or
            state['state']!=3 or state['serial']!=reply['serial']):return changed
        raw=self._client.reader.read(state['command_address'],9*BUTTON_BYTES)
        from .panel_card import selected_own_types
        if not 0<=state['cargo_count']<=8:raise RuntimeError('invalid native cargo count')
        cargo_raw=self._client.reader.read(state['cargo_address'],state['cargo_count']*32) if state['cargo_count'] else b''
        # Protocol 24 publishes names alongside the selected queue under the
        # same guard. Never substitute a learned name for an unknown live one.
        queue_names=()
        names_supported=getattr(self._client,'protocol_version',23)>=24
        if state['queue_count'] and names_supported and state['queue_names_address']:
            queue_names=struct.unpack('<5I',self._client.reader.read(state['queue_names_address'],20))
        after=self._client.peek()
        guard_after=struct.unpack('<I',self._client.reader.read(serial_address,4))[0]
        if guard_after!=guard or any(after[k]!=state[k] for k in ('command_sample_serial','epoch',
                'selection_generation','command_generation','queue_serial','cargo_serial','completed_serial',
                'state','serial')):return changed
        # Validate local copies only after publication. A writer clears and
        # fills this array while its guard is odd; those transient zeros are
        # a changed observation, not malformed persistent source data.
        if not 0<=state['queue_count']<=5:raise RuntimeError('invalid native production count')
        if state['queue_count']:
            if not names_supported:return {'status':'unavailable','reason':'production_names_unavailable'}
            if not queue_names:raise RuntimeError('missing production names')
            if (any(not p for p in queue_names[:state['queue_count']]) or
                    any(queue_names[state['queue_count']:])):
                raise RuntimeError('invalid native production names')
        try:member_types,member_stamp=selected_own_types(self._client.reader,state)
        except RuntimeError:return changed
        # Only committed local copies are parsed below. Labels are immutable
        # loaded strings; no mutable node/passenger pointer becomes public.
        text=PanelText(self._client.reader,text_budget_bytes,encoding);buttons=[]
        queue=[];placement_button=False;skill_items=[];basic_items=[]
        try:
            for row in struct.iter_unpack('<8I',raw):
                slot,enabled,handler,argument,label,category,node,record=row
                if not slot:continue
                if handler in BUILD_PLACEMENT_HANDLERS:placement_button=True
                item={'slot':slot,'enabled':bool(enabled),'button':asdict(ButtonTicket(
                    **asdict(ticket),card_generation=state['command_generation'],slot=slot))}
                skill=category==3 or (category==0 and handler not in BASIC_ACTIONS)
                if skill:
                    candidate=text.string(label)
                    skill_items.append((slot,candidate))
                    if self._knowledge.known_skill_name(member_types,slot,candidate):item['name']=candidate
                else:
                    label_name=text.string(label)
                    basic_items.append((slot,label_name))
                    item['name']=label_name
                buttons.append(item)
            for i in range(state['queue_count']):
                kind=state[f'queue_type{i}']
                if not 0<=kind<228:raise RuntimeError('invalid native production type')
                queue.append({'slot':i,'name':text.string(queue_names[i]),
                              'type_token':self._knowledge.token('type',kind)})
        except TextBudgetRequired:return {'status':'larger_text_budget_required'}
        try:
            if selected_own_types(self._client.reader,state)!=(member_types,member_stamp):return changed
        except RuntimeError:return changed
        # Name reads consume the caller's budget and may take time. Recheck
        # publication after decoding so a changed selection cannot leak rows.
        if struct.unpack('<I',self._client.reader.read(serial_address,4))[0]!=guard:return changed
        cargo=[]
        for expected,row in enumerate(struct.iter_unpack('<8I',cargo_raw)):
            slot,visual,kind,enabled,unit,unique,node,sort_position=row
            if slot!=expected or kind>=228 or enabled not in (0,1):raise RuntimeError('invalid native cargo row')
            item={'slot':slot,'appearance_token':f'sprite:{visual}',
                'type_token':self._knowledge.token('type',kind),'enabled':bool(enabled),
                'passenger':asdict(CargoTicket(**asdict(ticket),cargo_generation=state['cargo_serial'],slot=slot))}
            name=self._knowledge.name(kind)
            if name is not None:item['name']=name
            cargo.append(item)
        # Placement callbacks alone also occur on a normal building card (for
        # example a direct Build Addon). The menu-set id is the live submenu
        # witness: a default single/group card keeps one selected unit type as
        # its set id, while an opened build submenu uses a distinct button set.
        build_submenu=False
        if placement_button:
            menu=None
            try:menu=struct.unpack('<H',self._client.reader.read(
                state['game_base']+(0x68c9bc-0x400000),2))[0]
            except Exception:pass
            build_submenu=(menu is not None and menu not in member_types)
        self._observe_group_command_card(state,member_types,member_stamp,
                                         skill_items,basic_items,buttons)
        return {'status':'observed','buttons':buttons,'mode':PHASES.get(state['command_phase'],'ready'),
                'build_submenu':build_submenu,
                'cargo':cargo,'production_queue':queue,'production':asdict(ProductionTicket(
                    **asdict(ticket),queue_generation=state['queue_serial']))}

    @staticmethod
    def _queue_flag(queued):
        if type(queued) is not bool:raise ValueError('queued must be boolean')
        return int(queued)

    def activate_button(self,button,*,queued=False):
        """Use a current train/research/upgrade/build/skill/menu button.

        Targeted operations return a target ticket. Each subsequent placement
        is another operation in the same budget. No new ability cost is read.
        """
        flag=self._queue_flag(queued)
        if not self._typed_ticket(button,ButtonTicket):return {'status':'rejected','reason':'different_process'}
        if not 1<=button.slot<=9:raise ValueError('button slot must be 1..9')
        ticket=_selection(button)
        reply=self._request('command-button',0,0,ticket.epoch,ticket.generation,
                            extra={'command_slot':button.slot,'expected_command_generation':button.card_generation,
                                   'queued_order':flag})
        result=self._result(reply,ticket)
        if reply and reply['result']=='ok':
            state=reply['developer_after']
            if (state.get('selection_generation',ticket.generation)!=ticket.generation and
                    0<state.get('selection_count',0)<=12):
                result['selection']=asdict(SelectionTicket(self._pid,self._creation,state['epoch'],
                    state['selection_generation'],state['selection_count']))
        return result

    def _production_snapshot(self,production):
        """Private coherent observation, never an execution acknowledgement."""
        address=self._client.address+State.command_sample_serial.offset
        guard=struct.unpack('<I',self._client.reader.read(address,4))[0]
        if guard&1:return None
        state=self._client.peek()
        after=struct.unpack('<I',self._client.reader.read(address,4))[0]
        if after!=guard or state['command_sample_serial']!=guard:return None
        if (state['epoch']!=production.epoch or state['selection_generation']!=production.generation or
                state['selection_count']!=production.count):return None
        if not 0<=state['queue_count']<=5:return None
        values=tuple(state[f'queue_type{i}'] for i in range(state['queue_count']))
        if any(not 0<=kind<228 for kind in values):return None
        return state['queue_serial'],values

    def cancel_production_slots(self,production,slots,*,confirmation_timeout=2.0):
        """Submit one original entry, then stop if its effect is unconfirmed.

        The transport acknowledges submission only. Even an expected remaining
        list can also result from completion or human edits, so it cannot
        authorize the next cancellation. No queued operation is retried.
        """
        if not self._typed_ticket(production,ProductionTicket):return {'status':'rejected','reason':'different_process'}
        if (not isinstance(slots,(tuple,list)) or len(slots)>5 or len(set(slots))!=len(slots) or
            any(type(x) is not int or not 0<=x<5 for x in slots)):
            raise ValueError('distinct native queue slots required')
        if isinstance(confirmation_timeout,bool) or not isinstance(confirmation_timeout,(int,float)) or not 0<confirmation_timeout<=30:
            raise ValueError('bounded positive confirmation timeout required')
        if not slots:return {'status':'applied','operations':[]}
        before=self._production_snapshot(production)
        if before is None or before[0]!=production.queue_generation or max(slots)>=len(before[1]):
            return {'status':'stopped','reason':'queue_changed','operations':[]}
        slot=max(slots)
        result=self.cancel_production(production,slot)
        operations=[{'slot':slot,'result':result}]
        if result.get('status')!='enqueued':return {'status':'stopped','operations':operations}
        deadline=time.monotonic()+confirmation_timeout
        after=self._production_snapshot(production)
        while after is None or after[0]==before[0]:
            if time.monotonic()>=deadline:break
            time.sleep(.02)
            after=self._production_snapshot(production)
        return {'status':'outcome_unknown','reason':'cancellation_effect_unconfirmed',
                'automatic_retry':False,'operations':operations}

    def cancel_all_production(self,production,*,confirmation_timeout=2.0):
        """Request clearing this queue through individually charged actions."""
        if not self._typed_ticket(production,ProductionTicket):return {'status':'rejected','reason':'different_process'}
        state=self._production_snapshot(production)
        if state is None or state[0]!=production.queue_generation:return {'status':'rejected','reason':'queue_changed'}
        return self.cancel_production_slots(production,list(range(len(state[1]))),
                                            confirmation_timeout=confirmation_timeout)

    def target_at(self,target,x,y):
        """Click the current operable viewport for this exact pending operation."""
        if not self._typed_ticket(target,TargetTicket):return {'status':'rejected','reason':'different_process'}
        if target.queued_order not in (0,1):raise ValueError('invalid target queued order')
        ticket=_selection(target)
        reply=self._request('command-target',x,y,ticket.epoch,ticket.generation,
                            extra={'expected_target_serial':target.target_generation,'queued_order':target.queued_order})
        return self._result(reply,ticket)

    def _selected_command(self,op,ticket,x=0,y=0,*,queued=False):
        flag=self._queue_flag(queued)
        if not self._validate_ticket(ticket):return {'status':'rejected','reason':'different_process'}
        extra={'extra':{'queued_order':flag}} if op in ('attack-point','patrol-point','attack-minimap',
                                                      'patrol-minimap','right-click') else {}
        return self._result(self._request(op,x,y,ticket.epoch,ticket.generation,**extra),ticket)

    def move_selected(self,ticket,x,y,*,queued=False):
        """Move the actual selected group, optionally appending a native order."""
        flag=self._queue_flag(queued)
        if not self._validate_ticket(ticket):return {'status':'rejected','reason':'different_process'}
        reply=self._request('move',x,y,ticket.epoch,ticket.generation,extra={'queued_order':flag})
        return action_result(reply) if reply else {'status':'outcome_unknown','automatic_retry':False}

    def move_minimap(self,ticket,pixel_x,pixel_y,*,queued=False):
        """One native Move at an existing minimap point; camera stays in place."""
        flag=self._queue_flag(queued)
        if not self._validate_ticket(ticket):return {'status':'rejected','reason':'different_process'}
        reply=self._request('move-minimap',pixel_x,pixel_y,ticket.epoch,ticket.generation,
                            extra={'queued_order':flag})
        return action_result({**reply,'operation':'move'}) if reply else {'status':'outcome_unknown','automatic_retry':False}

    def attack_move(self,ticket,x,y,*,queued=False):return self._selected_command('attack-point',ticket,x,y,queued=queued)
    def right_click(self,ticket,x,y,*,queued=False):
        """Native contextual right-click on the current operable viewport.

        Ground normally moves; units can cause attack, follow, boarding or
        worker work as resolved by the game's current actors and target.
        Use actual Load/Unload buttons for transport commands as available.
        """
        return self._selected_command('right-click',ticket,x,y,queued=queued)
    def cancel_pending(self):
        """Press native ESC for the current command target/placement mode."""
        return self._result(self._request('cancel-pending',0,0,self._epoch),None)
    def unload_one(self,passenger):
        """Unload one current visible cargo icon of the real selected carrier."""
        if not self._typed_ticket(passenger,CargoTicket):return {'status':'rejected','reason':'different_process'}
        if not 0<=passenger.slot<8:raise ValueError('cargo slot must be 0..7')
        t=_selection(passenger)
        return self._result(self._request('unload-one',0,0,t.epoch,t.generation,
            extra={'expected_cargo_serial':passenger.cargo_generation,'cargo_slot':passenger.slot}),t)
    def patrol(self,ticket,x,y,*,queued=False):return self._selected_command('patrol-point',ticket,x,y,queued=queued)
    def attack_move_minimap(self,ticket,pixel_x,pixel_y,*,queued=False):return self._selected_command('attack-minimap',ticket,pixel_x,pixel_y,queued=queued)
    def patrol_minimap(self,ticket,pixel_x,pixel_y,*,queued=False):return self._selected_command('patrol-minimap',ticket,pixel_x,pixel_y,queued=queued)
    def halt_construction(self,ticket):return self._selected_command('halt-construction',ticket)
    def resume_construction(self,ticket,x,y):return self._selected_command('resume-construction',ticket,x,y)
    def cancel_construction(self,ticket):return self._selected_command('cancel-construction',ticket)
    def cancel_research(self,ticket):return self._selected_command('cancel-research',ticket)
    def cancel_upgrade(self,ticket):return self._selected_command('cancel-upgrade',ticket)
    def cancel_morph(self,ticket):return self._selected_command('cancel-morph',ticket)
    def cancel_addon(self,ticket):return self._selected_command('cancel-addon',ticket)

    def cancel_production(self,production,slot):
        """Cancel exactly one current queue entry; consumes one shared action."""
        if not self._typed_ticket(production,ProductionTicket):return {'status':'rejected','reason':'different_process'}
        if type(slot) is not int or not 0<=slot<5:raise ValueError('production slot must be 0..4')
        ticket=_selection(production)
        reply=self._request('cancel-train-slot',0,0,ticket.epoch,ticket.generation,
                            extra={'train_slot':slot,'expected_queue_serial':production.queue_generation})
        return self._result(reply,ticket)

