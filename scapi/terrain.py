"""Current lawful viewport terrain and current native placement preview.

No unseen terrain, engine addresses/types, hidden blockers or arbitrary type
queries cross this public decoder. Tile/mini-tile coordinates use the same
world pixel origin as unit observations. Budgets are explicit, never truncated.
"""
import struct
from .actions import action_result

TERRAIN_MAGIC=0x31525453
PLACEMENT_MAGIC=0x31504C53
TERRAIN_HEADER=64
TERRAIN_CELL=20
PLACEMENT_BYTES=108
NATIVE_REASON={0:'valid',1:'native_placement_rejected',2:'native_placement_rejected',
               3:'outside_map',4:'terrain_or_unit_obstruction',5:'creep_rule',
               6:'requires_power',7:'unreachable',8:'requires_geyser',
               9:'not_visible',10:'unexplored'}

def _changed():
    return {'status':'rejected','reason':'observation_changed_during_read'}

def _buffer(api,result,prefix,minimum):
    if not result:return None,{'status':'outcome_unknown','automatic_retry':False},None
    if result['result']!='ok':return None,action_result(result),None
    client=api._client
    state=client.peek()
    serial=state[prefix+'_serial']
    if serial&1:return None,_changed(),None
    if (state['epoch']!=api._epoch or state['state']!=3 or
        state['serial']!=result['serial'] or state['completed_serial']!=result['serial']):
        return None,_changed(),None
    size=state[prefix+'_bytes']
    if not minimum<=size<=68+21*14*36:raise RuntimeError('invalid terrain observation bytes')
    raw=client.reader.read(state[prefix+'_address'],size)
    after=client.peek()
    guard=('epoch','serial','completed_serial','camera_x','camera_y','selection_generation','target_serial',
           prefix+'_serial',prefix+'_address',prefix+'_bytes')
    if after['state']!=3 or any(after[k]!=state[k] for k in guard):return None,_changed(),None
    return raw,None,state

def terrain_view(api,*,tile_budget):
    """Return the whole current 32px grid, with sixteen 8px samples per tile.

    Explored fog retains ground geometry and the last observed creep state.
    Power is specific to the actual active placement type,
    and is unknown unless its complete candidate footprint is legally visible.
    """
    if type(tile_budget) is not int or tile_budget<1:raise ValueError('positive tile_budget required')
    if api._client.protocol_version<17:
        return {'status':'rejected','reason':'terrain_requires_protocol_17'}
    result=api._request('terrain-view',0,0,api._epoch)
    return terrain_from_reply(api,result,tile_budget=tile_budget)

def terrain_from_reply(api,result,*,tile_budget):
    """Decode the terrain attachment committed by observe-view's same request."""
    if type(tile_budget) is not int or tile_budget<1:raise ValueError('positive tile_budget required')
    raw,error,state=_buffer(api,result,'terrain',TERRAIN_HEADER)
    if error:return error
    h=struct.unpack_from('<16I',raw)
    if h[0]!=TERRAIN_MAGIC or h[1] not in (1,2,3,4) or h[2]!=len(raw) or h[3]!=api._epoch:
        raise RuntimeError('invalid terrain observation header')
    semantic=h[1]>=2
    expected_version=3 if api._client.protocol_version>=20 else 2 if api._client.protocol_version>=19 else 1
    if h[1]!=expected_version and not (h[1]==4 and api._client.protocol_version>=23):
        raise RuntimeError('terrain protocol/layout mismatch')
    header,cell_bytes=(68,36 if h[1]==4 else 32 if h[1]==3 else 28) if semantic else (TERRAIN_HEADER,TERRAIN_CELL)
    tileset=struct.unpack_from('<I',raw,64)[0] if semantic and len(raw)>=68 else None
    if semantic and tileset not in range(8) and tileset!=0xffffffff:raise RuntimeError('invalid tileset')
    count=h[11]
    if count>21*14 or len(raw)!=header+count*cell_bytes:
        raise RuntimeError('invalid terrain observation count')
    if (h[5],h[6])!=(state['camera_x'],state['camera_y']):return _changed()
    base={'status':'observed' if h[12] else 'observation_not_ready',
          'epoch':h[3],'observed_frame':h[4],'observed_tick_ms':h[13],
          'camera':{'x':h[5],'y':h[6],'width':h[7],'height':h[8]},
          'coordinate_system':'world_pixels','tile_size':32,'walk_tile_size':8,
          'geometry_source':'native_known_rendered_megatiles_and_vf4',
          'selection_generation':h[9],'target_generation':h[10]}
    if h[1]==4:base['entity_identity']='scope_salted_entity_token'
    if api._client.protocol_version>=25:
        base['entity_identity_semantics']='lawful_observation_lifetimes; hidden_gaps_not_linked'
    if semantic:
        from .terrain_semantics import HEIGHT_NAMES
        base['height_levels']={str(i):name for i,name in enumerate(HEIGHT_NAMES)}
        base['height_semantics']='ordinal_ground_layers; independent_of_water_or_material'
        base['classification_source']='known_rendered_megatile_inverse_cv5_editor_palette'
        base['movement_semantics']='walkable_8px_is_ground_terrain_only; air_can_cross_ground_obstacles; route_and_unit_clearance_not_guaranteed'
        if h[1]>=3:base['geometry_source']='native_visible_ground_vf4_and_legally_observed_fog_cache'
        if api._client.protocol_version>=21:
            base['geometry_source']='native_visible_ground_and_explored_static_ground'
            base['fog_semantics']='static_ground_retained; creep_is_last_observed_until_visible_refresh; dynamic_occupancy_not_current'
    if not h[12]:return base
    if count>tile_budget:return {**base,'status':'larger_observation_budget_required','required_tiles':count}
    cells=[]
    for i in range(count):
        fields=struct.unpack_from('<9I' if h[1]==4 else '<8I' if h[1]==3 else '<7I' if semantic else '<5I',raw,header+i*cell_bytes)
        x,y,flags,walk,height=fields[:5]
        if flags&~(131071 if api._client.protocol_version>=21 else 32767):raise RuntimeError('unsupported terrain flags')
        known=bool(flags&128);mini=bool(flags&512)
        mini_mask=fields[7] if h[1]>=3 else 65535 if mini else 0
        if mini_mask>65535 or bool(mini_mask)!=mini:raise RuntimeError('invalid mini geometry validity mask')
        if walk>65535 or any((height>>(2*j))&3==3 for j in range(16)):
            raise RuntimeError('invalid native terrain geometry')
        cell={'x':x,'y':y,'explored':bool(flags&1),'currently_visible':bool(flags&2),
              'terrain_buildable':bool(flags&4) if known else None,
              'creep':bool(flags&16) if flags&(8|65536) else None,
              'power_for_current_placement':bool(flags&64) if flags&32 else None,
              'blocks_view':bool(flags&256) if known else None,
              'walkable_8px':[[bool(walk&(1<<(row*4+col))) if mini_mask&(1<<(row*4+col)) else None for col in range(4)] for row in range(4)] if mini else None,
              'ground_height_8px':[[(height>>(2*(row*4+col)))&3 if mini_mask&(1<<(row*4+col)) else None for col in range(4)] for row in range(4)] if mini else None}
        cell['observed_ground_occupants']=[name for bit,name in (
            (1024,'own'),(2048,'ally'),(4096,'enemy'),(8192,'neutral')) if flags&bit]
        cell['observed_ground_building']=bool(flags&16384)
        if h[1]>=3:
            cell['tile_summary_status']='observed' if known else 'tile_summary_not_observed'
            # This mask qualifies each complete, legally explored 8px square
            # independently of the representative point used by the summary.
            cell['ground_geometry_known_8px']=[[bool(mini_mask&(1<<(row*4+col)))
                                              for col in range(4)] for row in range(4)]
            if not known:
                cell['explored']=cell['currently_visible']=None
                cell['observed_ground_building']=None
        if api._client.protocol_version>=21:
            if flags&65536 and (flags&8 or flags&2):raise RuntimeError('invalid remembered creep observation')
            cell['creep_observation']='current' if flags&8 else 'last_observed' if flags&65536 else 'unknown'
            cell['ground_geometry_source']='explored_static_ground' if flags&32768 else 'visible_or_previously_observed_ground' if mini else 'unknown'
        if h[1]==4:
            static_walk=fields[8]
            if static_walk>65535 or static_walk&~mini_mask:
                raise RuntimeError('invalid fixed terrain passage mask')
            cell['static_walkable_8px']=[[bool(static_walk&(1<<(row*4+col))) if mini_mask&(1<<(row*4+col)) else None for col in range(4)] for row in range(4)] if mini else None
        if semantic:
            from .terrain_semantics import terrain_label
            ramp,kind=fields[5:7]
            if ramp>65535:raise RuntimeError('invalid ramp mask')
            if kind>65535 and kind not in (0xffffffff,0xfffffffe):raise RuntimeError('invalid terrain classification')
            cell['terrain']=terrain_label(tileset,kind) if known else {'status':'unknown','reason':'not_currently_observed'}
            if cell['creep'] is True:
                cell['terrain']={'status':'identified','name':'Creep-covered ground','category':'creep_covered_ground'}
            cell['ramp_marked_8px']=[[bool(ramp&(1<<(row*4+col))) if mini_mask&(1<<(row*4+col)) else None for col in range(4)] for row in range(4)] if mini else None
            cell['contains_ramp_marker']=bool(ramp&mini_mask) if mini else None
            cell['ground_terrain_passage']=('open' if walk==65535 else 'blocked' if walk==0 else 'partly_walkable') if mini_mask==65535 else 'partly_observed' if mini_mask else 'unknown'
        cells.append(cell)
    base['cells']=cells
    base['occupancy_semantics']='positive_observed_ground_collision_overlap; absence_does_not_prove_clear'
    base['placement_addon_offset']={'x':h[14],'y':h[15]}
    return base

def placement_preview(api,ticket,x,y,*,anchor='main_building_top_left'):
    """Validate one actual placement without issuing a command or spending APM.

    x/y are tile-aligned world pixels. The default uses the main building's
    top left; engine_anchor preserves target_at's existing click semantics.
    Submit a successful preview using its engine_click_point to target_at.
    That click may differ from the footprint's world origin when the camera
    is aligned to 8 pixels but not 32; it inverts the actual native snap.
    """
    if any(type(v) is not int or not 0<=v<2**31 for v in (x,y)) or x%32 or y%32:
        raise ValueError('tile-aligned nonnegative world pixel coordinates required')
    if anchor not in ('main_building_top_left','engine_anchor'):raise ValueError('invalid placement anchor')
    if api._client.protocol_version<17:return {'status':'rejected','reason':'terrain_requires_protocol_17'}
    if (ticket.pid!=api._pid or ticket.process_creation!=api._creation or
        ticket.epoch!=api._epoch):return {'status':'rejected','reason':'stale_target_ticket'}
    result=api._request('placement-preview',x,y,ticket.epoch,ticket.generation,
                        x2=int(anchor=='main_building_top_left'),
                        extra={'expected_target_serial':ticket.target_generation,'queued_order':ticket.queued_order})
    raw,error,state=_buffer(api,result,'placement',PLACEMENT_BYTES)
    if error:return error
    if len(raw)!=PLACEMENT_BYTES:raise RuntimeError('invalid placement observation bytes')
    h=struct.unpack('<27I',raw)
    if h[0]!=PLACEMENT_MAGIC or h[1]!=1 or h[2]!=len(raw) or h[3]!=ticket.epoch:
        raise RuntimeError('invalid placement observation header')
    if ((h[5],h[6])!=(state['camera_x'],state['camera_y']) or
        h[7]!=ticket.generation or h[8]!=ticket.target_generation or
        h[7]!=state['selection_generation'] or h[8]!=state['target_serial']):
        return _changed()
    def footprint(start,reason):
        return {'x':h[start],'y':h[start+1],'width':h[start+2],'height':h[start+3],
                'valid':None if api._client.protocol_version>=21 and reason==9 else reason==0,
                'reason':'explored_fog_revalidated_on_arrival' if api._client.protocol_version>=21 and reason==9 else NATIVE_REASON.get(reason,'native_placement_rejected')}
    answer={'status':'ok','valid':h[23]==0 and h[24]==0,'epoch':h[3],
            'observed_frame':h[4],'observed_tick_ms':h[9],
            'camera':{'x':h[5],'y':h[6]},'coordinate_system':'world_pixels',
            'anchor':anchor,'engine_click_point':{'x':h[12],'y':h[13]},
            'main_footprint':footprint(15,h[23]),
            'addon_footprint':footprint(19,h[24]) if h[14] else None,
            'power_required':bool(h[25]),'powered':bool(h[26]) if h[25] else None,
            'command_issued':False}
    if api._client.protocol_version>=21:
        reasons=(h[23],h[24]) if h[14] else (h[23],)
        deferred=9 in reasons
        known_failure=any(reason not in (0,9) for reason in reasons)
        answer.update(valid=False if known_failure else None if deferred else True,
                      can_issue_target=not known_failure,
                      validation='game_revalidates_on_arrival' if deferred else 'native_current_preview')
        if deferred:answer['powered']=None
    return answer
