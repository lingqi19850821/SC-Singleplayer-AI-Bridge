"""Completed native selected-panel observations, durably acknowledged by one host.

The producer copies text/rules at the legal two-second completion, before a
selection-changing command can execute. No follow-up Agent read is necessary.
Build options mean this mission permits a building in the actual actor's loaded
submenu graph. They do not assert resource/prerequisite readiness; the real
current command card supplies enabled state and actionable tickets.
"""
import ctypes
import struct
from dataclasses import asdict
from .actions import SelectionTicket
from .panel_card import BASIC_ACTIONS
from tools.stage5_client import State,Client

HEADER=struct.Struct('<8I')
TEXT=struct.Struct('<10I')
PROFILE=struct.Struct('<18I')
MAGIC=0x31504153


def decode_completed(raw, *, encoding):
    """Parse an immutable native completion; no current-unit/table rereads."""
    if len(raw)<HEADER.size+ctypes.sizeof(State)+PROFILE.size:
        raise RuntimeError('automatic_panel_short_snapshot')
    magic,version,size,sequence,tick,count,progress_valid,percent=HEADER.unpack_from(raw)
    start=HEADER.size+ctypes.sizeof(State)+PROFILE.size
    if (magic!=MAGIC or version!=1 or size!=len(raw) or not sequence or
            count>240 or start+TEXT.size*count>size or progress_valid not in (0,1) or percent>100):
        raise RuntimeError('automatic_panel_invalid_snapshot')
    native=State.from_buffer_copy(raw[HEADER.size:HEADER.size+ctypes.sizeof(State)])
    state={name:getattr(native,name) for cls in reversed(State.__mro__) for name,*_ in getattr(cls,'_fields_',[])}
    if (state['version']!=Client.protocol_version or state['bytes']!=ctypes.sizeof(State) or not state['details_ready'] or
            not state['panel_available'] or state['selection_count']!=1 or
            state['panel_type']>=228 or state['panel_owner']>=12):
        raise RuntimeError('automatic_panel_invalid_completed_state')
    p=PROFILE.unpack_from(raw,HEADER.size+ctypes.sizeof(State))
    if (p[0] not in (0,1) or p[5] not in (0,1,2) or p[6] not in (0,1) or
            p[11] not in (0,1) or p[12] not in (0,1) or p[17] not in (0,1)):
        raise RuntimeError('automatic_panel_invalid_profile_flags')
    profiles={}
    if p[0]:profiles['armor']={'format':1,'base':p[1],'group':p[2],'increment':p[3],
                             'extra':p[4] if p[4]<2**31 else p[4]-2**32}
    for index,channel in enumerate(('ground_attack','air_attack'),1):
        row=p[index*6:index*6+6]
        if row[0]:profiles[channel]={'format':1,'weapon':row[1],'group':row[2],'base':row[3],'increment':row[4]}
    name=None;names={};evidence={};basic=[];skills=[];buildings=[];seen=set()
    end=start+count*TEXT.size
    for index in range(count):
        role,key,enabled,handler,category,cost,label,mapping,offset,length=TEXT.unpack_from(raw,start+index*TEXT.size)
        if (offset!=end or not length or offset+length>size or
                raw[offset+length-1]!=0 or b'\0' in raw[offset:offset+length-1] or
                (role,key,category if role==1 else 0) in seen):
            raise RuntimeError('automatic_panel_invalid_text')
        seen.add((role,key,category if role==1 else 0));end+=length
        text_raw=raw[offset:offset+length]
        text=bytes(b for b in text_raw[:-1] if b>=32 or b in (9,10)).decode(encoding,errors='strict')
        if role==0:
            name=text
        elif role==1:
            if key>=130 or category>1:raise RuntimeError('automatic_panel_invalid_weapon')
            names[key]=text
            evidence[('ground_attack','air_attack')[category]]={
                'label':label,'raw':text_raw,'encoding':encoding,'mapping':mapping}
        elif role==2:
            if not 1<=key<=9 or enabled not in (0,1):raise RuntimeError('automatic_panel_invalid_button')
            item={'slot':key,'name':text}
            if category==0 and handler in BASIC_ACTIONS:
                basic.append({**item,'enabled':bool(enabled)})
            elif category in (0,3):
                if cost:item['energy_cost']=cost
                skills.append(item)
        elif role==3:
            if key>=228:raise RuntimeError('automatic_panel_invalid_building')
            buildings.append({'name':text,'availability':'allowed_in_mission'})
        else:raise RuntimeError('automatic_panel_unsupported_text_role')
    if end!=size or name is None:raise RuntimeError('automatic_panel_incomplete_text')
    return {'sequence':sequence,'tick':tick,'state':state,'profiles':profiles,'name':name,
            'weapon_names':names,'weapon_evidence':evidence,
            'incomplete_building':bool(p[17]),
            'card':{'basic_commands':basic,'skills':skills} if state['panel_owned'] and p[11] else None,
            'card_status':'available' if p[11] else 'unavailable',
            'build_options':buildings if state['panel_owned'] and p[5]==1 else None,
            'build_options_status':('complete' if p[5]==1 else 'unsupported_loaded_buttonset') if state['panel_owned'] else None,
            'work_progress':{'percent':percent,'source':'visible_progress_bar'} if progress_valid else None}


def drain_completed(api, *, encoding):
    """Commit before ack. Retain a public result until the host takes delivery."""
    from .stage4 import selected_panel_result
    client=api._client
    if getattr(client,'protocol_version',0)<23:return
    state=client.peek()
    fault=state['auto_panel_fault']
    seq=state['auto_panel_sequence'];ack=state['auto_panel_ack']
    if seq<ack:raise RuntimeError('automatic_panel_invalid_ack')
    if seq==ack:
        if fault:raise RuntimeError('automatic_panel_native_fault:'+str(fault))
        return
    if seq!=ack+1 or not state['auto_panel_address']:raise RuntimeError('automatic_panel_sequence_gap')
    raw=client.reader.read(state['auto_panel_address'],state['auto_panel_bytes'])
    after=client.peek()
    if any(after[k]!=state[k] for k in ('auto_panel_address','auto_panel_bytes','auto_panel_sequence','auto_panel_ack')):
        raise RuntimeError('automatic_panel_changed_during_read')
    completion=decode_completed(raw,encoding=encoding)
    if completion['sequence']!=seq:raise RuntimeError('automatic_panel_sequence_mismatch')
    s=completion['state'];kind=s['panel_type'];knowledge=api._knowledge
    capture_scope=f'{api._pid}:{api._creation}:{client.address}:{s["epoch"]}'
    with knowledge.observation_scope(capture_scope),knowledge.transaction():
        knowledge.observe_armor_extra(kind,s['panel_owner'],s['panel_armor_bonus']-s['panel_armor_level'])
        result=selected_panel_result({'result':'ok','developer_after':s},knowledge,
            completion['card'],completion['profiles'],completion['weapon_names'],
            completion['weapon_evidence'],commit=False,
            incomplete_building=completion['incomplete_building'])
        knowledge.remember_name(kind,completion['name']);result['panel']['name']=completion['name']
        if not completion['incomplete_building']:
            if completion['build_options_status'] is not None:
                knowledge.build_options(kind,{'status':completion['build_options_status'],
                    'options':completion['build_options']},learn=True)
            if completion['build_options'] is not None:
                result['panel']['build_options']=completion['build_options']
            if completion['build_options_status'] is not None:
                result['panel']['build_options_status']=completion['build_options_status']
        if completion['work_progress']:result['panel']['work_progress']=completion['work_progress']
        if s['panel_owned']:result['panel']['card_status']=completion['card_status']
        observation={'kind':'selected_panel','status':'observed','observed_tick_ms':completion['tick'],
                     'observed_epoch':s['epoch'],'observation_id':knowledge.token('panel_observation',seq),
                     'temporal':'completed_observation',
                     'selection':asdict(SelectionTicket(api._pid,api._creation,s['epoch'],
                          s['selection_generation'],s['selection_count'])),**result}
        knowledge.queue_observation(seq,observation)
    # Native ack permits overwrite only after both knowledge and its complete
    # public delivery payload are durable. Host outbox handoff has its own ack.
    client._write(State.auto_panel_ack.offset,struct.pack('<I',seq))
