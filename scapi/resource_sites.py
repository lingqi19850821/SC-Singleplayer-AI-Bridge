"""Current player-vision resource sites and a private screen-qualified absence mask."""
import struct

CAPACITY=1700
ROW=struct.Struct('<Iii13IiiiI')
VISIBILITY=struct.Struct('<10Iii2I')
VISIBILITY_MAGIC=0x31565352
KINDS={176:('mineral','mineral_field'),177:('mineral','mineral_field'),
       178:('mineral','mineral_field'),188:('gas','geyser'),
       110:('gas','gas_building'),149:('gas','gas_building'),157:('gas','gas_building')}


def _changed():
    return {'status':'rejected','reason':'observation_changed_during_read'}


def resource_sites(api,*,unit_budget,include_selected_amount=False):
    # Never let a failed/partial census leave permission to retire old sites.
    api._resource_visibility=None
    if type(unit_budget) is not int or unit_budget<1:
        raise ValueError('positive unit_budget required')
    if type(include_selected_amount) is not bool:
        raise ValueError('include_selected_amount must be boolean')
    if getattr(api._client,'protocol_version',0) not in (26,27):
        return {'status':'unavailable','reason':'resource_sites_requires_protocol_26'}
    reply=api._request('resource-sites',int(include_selected_amount),0,api._epoch)
    if not reply:return {'status':'outcome_unknown','automatic_retry':False}
    if reply['result']!='ok':
        if reply['result'] in ('queue_unconfirmed','unknown'):
            return {'status':'outcome_unknown','reason':reply['result'],'automatic_retry':False}
        return {'status':'rejected','reason':reply['result']}
    if reply.get('operation')!='resource-sites':return _changed()
    state=api._client.peek()
    reply_state=reply['developer_after']
    camera_guard=('camera_x','camera_y','camera_max_x','camera_max_y')
    guard=('epoch','serial','completed_serial','operation','view_serial','view_address','view_count',
           'executed_tick','active','tile_pointer','width_tiles','height_tiles',
           'selection_generation','selection_count','terrain_serial','terrain_address','terrain_bytes',*camera_guard)
    def bound(s):
        return (s['state']==3 and s['operation']==48 and s['epoch']==api._epoch and not s['terrain_serial']&1 and
                s['serial']==reply['serial']==s['completed_serial']==s['view_serial'] and
                all(s[key]==reply_state[key] for key in camera_guard))
    if not bound(state):return _changed()
    count=state['view_count'];address=state['view_address']
    if type(count) is not int or not 0<=count<=CAPACITY:
        raise RuntimeError('invalid native resource count')
    if (not state['active'] or not state['tile_pointer'] or
            not 1<=state['width_tiles']<=256 or not 1<=state['height_tiles']<=256):
        return _changed()
    if type(address) is not int or not 0<address<=0xffffffff-count*ROW.size:
        raise RuntimeError('invalid native resource buffer')
    size=state['terrain_bytes'];visibility_address=state['terrain_address']
    expected_size=56+(state['width_tiles']*state['height_tiles']+7)//8
    if (size!=expected_size or not 56<size<=56+8192 or
            type(visibility_address) is not int or not 0<visibility_address<=0xffffffff-size):
        raise RuntimeError('invalid resource visibility buffer')
    if count>unit_budget:
        after=api._client.peek()
        if not bound(after) or any(after[key]!=state[key] for key in guard):return _changed()
        return {'status':'larger_observation_budget_required','required_units':count}
    raw=api._client.reader.read(address,count*ROW.size) if count else b''
    visible_raw=api._client.reader.read(visibility_address,size)
    after=api._client.peek()
    if not bound(after) or any(after[key]!=state[key] for key in guard):return _changed()
    if len(raw)!=count*ROW.size:raise RuntimeError('incomplete native resource buffer')
    if len(visible_raw)!=size:raise RuntimeError('incomplete resource visibility buffer')
    magic,version,size_header,epoch,frame,tick,width,height,selection,tile_count,camera_x,camera_y,vw,vh=VISIBILITY.unpack_from(visible_raw)
    if ((magic,version,size_header,epoch,tick,width,height,selection,tile_count)!=
            (VISIBILITY_MAGIC,2,size,api._epoch,state['executed_tick'],state['width_tiles'],state['height_tiles'],
             state['selection_generation'],state['width_tiles']*state['height_tiles']) or
            frame!=reply_state['raw_counter'] or
            (camera_x,camera_y,vw,vh)!=(state['camera_x'],state['camera_y'],
                width*32-state['camera_max_x'],height*32-state['camera_max_y']) or
            not (0<vw<=640 and 0<vh<=400 and 0<=camera_x<=width*32-vw and 0<=camera_y<=height*32-vh)):
        raise RuntimeError('invalid resource visibility header')
    bits=bytes(visible_raw[56:])
    if tile_count%8 and bits[-1]>>(tile_count%8):raise RuntimeError('invalid resource visibility padding')
    for index in range(tile_count):
        if bits[index//8]&(1<<(index%8)):
            tx=index%width*32;ty=index//width*32
            if not (camera_x<=tx and tx+32<=camera_x+vw and camera_y<=ty and ty+32<=camera_y+vh):
                raise RuntimeError('resource absence mask extends outside viewport')
    sites=[];seen=set();amount_seen=False
    for fields in ROW.iter_unpack(raw):
        visual,x,y,relation,hp,shields,energy,flags,kind,owner,ground,air,armor,gw,aw,health,extra,tx,ty,domain=fields
        if (kind not in KINDS or relation not in (1,2,3,4) or owner>=12 or
                not 0<=x<state['width_tiles']*32 or not 0<=y<state['height_tiles']*32 or
                any((visual,hp,shields,aw,health,extra,domain)) or flags not in (0,1) or
                (flags==0 and energy!=0) or (flags==1 and
                 (not include_selected_amount or state['selection_count']!=1 or energy>65535)) or
                not 0<=ground<=x<armor<=width*32 or not 0<=air<=y<gw<=height*32 or
                (tx,ty)!=(-1,-1)):
            raise RuntimeError('invalid native resource row')
        # Positive rows cover all current player vision; a zero screen mask
        # cannot revoke an otherwise lawful offcamera resource observation.
        if flags and amount_seen:raise RuntimeError('multiple selected resource amounts')
        if flags:amount_seen=True
        resource,form=KINDS[kind];key=f'{resource}:{x}:{y}'
        if key in seen:return {'status':'rejected','reason':'ambiguous_resource_site'}
        seen.add(key)
        site={'site_token':api._knowledge.token('resource_site',key),
                      'resource_kind':resource,'form':form,'position':{'x':x,'y':y},
                      'relation':{1:'own',2:'allied',3:'enemy',4:'neutral'}[relation],
                      'footprint':{'left':ground,'top':air,'right':armor,'bottom':gw}}
        if flags:site['remaining']={'value':energy,'observed_tick_ms':tick}
        sites.append(site)
    api._resource_visibility={'epoch':epoch,'tick':tick,'frame':frame,'width':width,'height':height,
                              'selection_generation':selection,'selection_count':state['selection_count'],
                              'census_serial':state['serial'],'complete':True,'bits':bits,
                              'scope':'current_operable_viewport',
                              'camera':{'x':camera_x,'y':camera_y,'width':vw,'height':vh}}
    return {'status':'observed','observed_tick_ms':state['executed_tick'],
            'scope':'current_player_vision','sites':sites}
