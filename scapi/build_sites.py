"""Current typed placement starts, saved in the existing public reference DB.

The native evaluator is used only for complete lawful visible footprints.
Explored fog remains an explicit unknown and is still a legal action target.
No placement callback, menu expansion or game action is issued by this read.
"""
import struct
from .actions import action_result

HEADER=struct.Struct('<18I')
ROW=struct.Struct('<5I296s')
MAGIC=0x31534253


def _ranges(outcomes,columns,left,top,wanted):
    """Lossless horizontal ranges of tile-aligned footprint top-left starts."""
    result=[]
    for y in range(len(outcomes)//columns):
        x=0
        while x<columns:
            if outcomes[y*columns+x]!=wanted:
                x+=1
                continue
            first=x
            while x+1<columns and outcomes[y*columns+x+1]==wanted:x+=1
            result.append({'y':32*(top+y),'x_first':32*(left+first),'x_last':32*(left+x)})
            x+=1
    return result


def decode_sites(raw,*,encoding,text_budget_bytes,token):
    if len(raw)<HEADER.size:raise RuntimeError('short native build sites')
    h=HEADER.unpack_from(raw)
    if (h[:3]!=(MAGIC,1,len(raw)) or h[10]>=228 or h[11]>228 or
            not 1<=h[14]<=21 or not 1<=h[15]<=14 or h[17]>2 or
            (h[17]!=1 and h[11]) or HEADER.size+h[11]*ROW.size>len(raw)):
        raise RuntimeError('invalid native build sites header')
    end=HEADER.size+h[11]*ROW.size
    buildings=[];seen=set();required=0
    for i in range(h[11]):
        kind,width,height,offset,length,outcomes=ROW.unpack_from(raw,HEADER.size+i*ROW.size)
        if (kind>=228 or kind in seen or not width or width%32 or width>192 or
                not height or height%32 or height>256 or offset!=end or not length or
                length>len(raw)-offset or raw[offset+length-1] or b'\0' in raw[offset:offset+length-1]):
            raise RuntimeError('invalid native build sites building')
        seen.add(kind);end+=length;required+=length
        outcomes=outcomes[:h[14]*h[15]]
        if any(value>10 and value not in (254,255) for value in outcomes):
            raise RuntimeError('invalid native build site result')
        name=bytes(value for value in raw[offset:offset+length-1] if value>=32 or value in (9,10)).decode(encoding,errors='strict')
        if not name:raise RuntimeError('empty native build sites label')
        buildings.append({'type_token':token('type',kind),'name':name,
            'footprint':{'width':width,'height':height},
            'legal_starts':_ranges(outcomes,h[14],h[12],h[13],0),
            'explored_fog_starts':_ranges(outcomes,h[14],h[12],h[13],254)})
    if end!=len(raw):raise RuntimeError('trailing native build sites bytes')
    if required>text_budget_bytes:return h,{'status':'larger_text_budget_required','required_bytes':required}
    status=('observation_not_ready','observed','unsupported_loaded_buttonset')[h[17]]
    return h,{'status':status,'builder_type_token':token('type',h[10]),
        'coordinate_system':'world_pixels','anchor':'building_top_left','step':32,
        'camera':{'x':h[5],'y':h[6],'width':h[7],'height':h[8]},
        'selection_generation':h[9],'buildings':buildings,
        'validity':'at_observation; unchanged_lawful_sources_may_reuse_original_observation; command_revalidates',
        'fog_semantics':'current_legality_unknown; direct_explored_fog_build_allowed'}


def build_sites(api,*,text_budget_bytes,encoding):
    if type(text_budget_bytes) is not int or text_budget_bytes<1:
        raise ValueError('positive text_budget_bytes required')
    if getattr(api._client,'protocol_version',0)<25:
        return {'status':'unavailable','reason':'build_sites_require_protocol_25'}
    before=api._client.peek()
    if before['epoch']!=api._epoch:return {'status':'rejected','reason':'stale_epoch'}
    result=api._request('build-sites',0,0,api._epoch,before['selection_generation'])
    if not result:return {'status':'outcome_unknown','automatic_retry':False}
    if result['result']!='ok':return action_result(result)
    state=api._client.peek();guard=state['build_sites_serial']
    changed={'status':'rejected','reason':'observation_changed_during_read'}
    if (guard&1 or state['epoch']!=api._epoch or state['state']!=3 or
            state['serial']!=result['serial'] or state['completed_serial']!=result['serial']):return changed
    size=state['build_sites_bytes']
    if size<HEADER.size:raise RuntimeError('invalid native build sites size')
    raw=api._client.reader.read(state['build_sites_address'],size)
    after=api._client.peek()
    fields=('epoch','serial','completed_serial','selection_generation','selection_count','camera_x','camera_y',
            'build_sites_serial','build_sites_address','build_sites_bytes')
    if after['state']!=3 or any(after[key]!=state[key] for key in fields):return changed
    h,sites=decode_sites(raw,encoding=encoding,text_budget_bytes=text_budget_bytes,token=api._knowledge.token)
    if (h[3]!=api._epoch or h[9]!=state['selection_generation'] or
            (h[5],h[6])!=(state['camera_x'],state['camera_y'])):return changed
    if sites['status']!='observed':return sites
    reference=api._knowledge.token('build_sites',h[10])
    notice=api._knowledge.remember_public_reference(reference,'build_sites',sites,name='Build sites',
        observation={'observed_tick_ms':h[16],'observed_frame':h[4],'observed_epoch':h[3]})
    if notice is None:
        entry=api._knowledge.reference_read(token=reference)
        if entry.get('status')!='ok':raise RuntimeError('build sites reference missing after commit')
        notice={'token':reference,'revision':entry['entry']['revision']}
    return {'status':'observed','source':'build_sites','entries':[notice],
            'building_count':len(sites['buildings']),'builder_type_token':sites['builder_type_token'],
            'selection_generation':h[9],'observed_tick_ms':h[16]}
