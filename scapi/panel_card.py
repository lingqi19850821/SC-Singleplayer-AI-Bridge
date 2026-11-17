"""Read the current own command card, not future button sets or hidden tech.

Target 1.05 offsets are documented in stage4/resume-20261109/static.
Only current visible slots and their current loaded labels/costs are read.
"""
import struct
import re
from .observation_budget import ObservationReadBudget

# Native 1.05 handlers for ordinary command-card operations. The text header
# describes tooltip cost layout, not whether a button is an ability. Labels and
# ability information still come only from the currently visible command card.
# Loaded-handler evidence: panel-card-fix-20261109/category-zero-action-handlers.json.
BASIC_ACTIONS=frozenset((
    0x47a8c0,0x47a8d0,0x47a8e0,0x47a9b0,0x47a9c0, # movement/combat
    0x47a930,0x47a940,0x47a950,0x47a960,0x47a970,0x47a980,0x47a990,
    0x47aec0,0x47af00,0x47af10,0x4a17f0, # worker operations/submenus
    0x47b020,0x47b050,0x47a760,0x47aea0,0x47b010,0x47a770,
    0x47a860,0x47ad70,0x47a8b0,0x47a9e0,0x47ad60, # cancellation
))


class TextBudgetRequired(Exception):
    pass


def visible_ammunition(reader,state,*,_budget=None):
    """Read the count actually drawn in the current single-unit stat panel.

    Target-1.05 renderer evidence: ammunition-panel-20261114/native-static.
    Read the visible text rather than reconstructing technology/disable rules
    or racing the unit's newer internal counter against the last UI redraw.
    A missing icon is unknown, never an invented zero or a remembered count.
    """
    icons={2:0x0d,19:0x0d,72:0x49,82:0x49,81:0x55,83:0x55}
    if (not state['panel_available'] or state['selection_count']!=1 or
            state['panel_type'] not in icons):return None
    reader=(_budget or ObservationReadBudget()).reader(reader)
    delta=state['game_base']-0x400000
    def dword(address):return struct.unpack('<I',reader.read(address,4))[0]
    def word(address):return struct.unpack('<H',reader.read(address,2))[0]
    def identity(pointer):
        return (word(pointer+0x64),reader.read(pointer+0x4c,1),reader.read(pointer+0xa5,1))
    def control(raw):
        return (struct.unpack_from('<I',raw)[0],struct.unpack_from('<H',raw,0x20)[0],
                struct.unpack_from('<I',raw,0x18)[0]&8,
                struct.unpack_from('<H',raw,0x24)[0],
                struct.unpack_from('<I',raw,0x14)[0],struct.unpack_from('<I',raw,0x26)[0])
    changed='ammunition observation changed during read'
    primary=dword(0x68d2f8+delta)
    selected=reader.read(0x684940+delta,48)
    first=0x5b2e68+delta
    if (not first<=primary<first+1700*0x148 or (primary-first)%0x148 or
            struct.unpack('<12I',selected)!=(primary,)+(0,)*11 or
            reader.read(0x68c9dc+delta,1)!=b'\0'):
        raise RuntimeError(changed)
    actor=identity(primary)
    if actor[:2]!=(state['panel_type'],bytes((state['panel_owner'],))):raise RuntimeError(changed)
    original_root=dword(0x68ca68+delta)
    if not original_root:return None
    root_kind=word(original_root+0x22)
    root=dword(original_root+0x32) if root_kind else original_root
    if not root:return None
    head=dword(root+0x42);node=head;seen=set();stamp=[];match=None
    while node:
        if node in seen:raise RuntimeError('cyclic ammunition control list')
        seen.add(node)
        fields=control(reader.read(node,0x2a));stamp.append((node,fields))
        next_node,identifier,visible,style,text_pointer,record=fields
        if visible and style==0x0c and 9<=identifier<=12 and record:
            icon=reader.read(record,12)
            if struct.unpack_from('<3H',icon,4)==(icons[actor[0]],3,icons[actor[0]]):
                if not text_pointer:raise RuntimeError('missing visible ammunition text')
                raw_text=reader.read(text_pointer,8)
                text=raw_text.split(b'\0',1)
                if len(text)!=2 or re.fullmatch(rb'[0-9]{1,3} ',text[0]) is None:
                    raise RuntimeError('invalid visible ammunition text')
                current=int(text[0])
                if current>255:raise RuntimeError('invalid visible ammunition count')
                match=(record,icon,text_pointer,raw_text,current)
                break
        node=next_node
    if (dword(0x68d2f8+delta)!=primary or reader.read(0x684940+delta,48)!=selected or
            reader.read(0x68c9dc+delta,1)!=b'\0' or identity(primary)!=actor or
            dword(0x68ca68+delta)!=original_root or word(original_root+0x22)!=root_kind or
            (root_kind and dword(original_root+0x32)!=root) or dword(root+0x42)!=head or
            any(control(reader.read(address,0x2a))!=fields for address,fields in stamp)):
        raise RuntimeError(changed)
    if match is None:return None
    record,icon,text_pointer,raw_text,current=match
    if reader.read(record,12)!=icon or reader.read(text_pointer,8)!=raw_text:raise RuntimeError(changed)
    return {'current':current}


def selected_own_types(reader,state):
    """Private identity checks for the actual selected own group, even offscreen.

    Reads no global unit list and grants no new learned values or skill names.
    """
    delta=state['game_base']-0x400000
    pointers=reader.read(0x684940+delta,48)
    owner=struct.unpack('<I',reader.read(0x4f30c0+delta,4))[0]
    first=0x5b2e68+delta;types=[];stamp=[]
    if owner>=12:raise RuntimeError('invalid selected owner')
    for pointer in struct.unpack('<12I',pointers):
        if not pointer:continue
        if not first<=pointer<first+1700*0x148 or (pointer-first)%0x148:
            raise RuntimeError('invalid selected unit identity')
        raw=reader.read(pointer,0xa6);kind=struct.unpack_from('<H',raw,0x64)[0]
        if raw[0x4c]!=owner or kind>=228:raise RuntimeError('invalid selected own member')
        types.append(kind);stamp.append((pointer,kind,raw[0x4c],raw[0xa5]))
    if len(types)!=state['selection_count']:raise RuntimeError('selection changed during card read')
    return tuple(types),(pointers,owner,tuple(stamp))


def visible_work_progress(reader,state):
    """Read the actual visible statdata progress control, never a hidden timer.

    Native 4A2E30 writes the displayed WORD percentage to control id14 +24.
    No remaining time, estimated duration or offscreen progress is inferred.
    """
    if not state['panel_owned'] or state['selection_count']!=1 or not state['details_ready']:return None
    delta=state['game_base']-0x400000
    def word(address):return struct.unpack('<H',reader.read(address,2))[0]
    def dword(address):return struct.unpack('<I',reader.read(address,4))[0]
    primary=dword(0x68d2f8+delta);gui=dword(0x684940+delta)
    if not primary or primary!=gui or reader.read(0x68c9dc+delta,1)!=b'\0':return None
    root=dword(0x68ca68+delta)
    if not root:return None
    original_root=root
    if word(root+0x22):root=dword(root+0x32)
    if not root:return None
    node=dword(root+0x42);seen=set();progress=None;stamp=[]
    while node:
        if node in seen:raise RuntimeError('cyclic statdata control list')
        seen.add(node);raw=reader.read(node,0x26);stamp.append((node,raw))
        if struct.unpack_from('<H',raw,0x20)[0]==14:
            if struct.unpack_from('<I',raw,0x18)[0]&8:
                percent=struct.unpack_from('<H',raw,0x24)[0]
                if percent>100:raise RuntimeError('invalid visible progress percentage')
                progress={'percent':percent,'source':'visible_progress_bar'}
            break
        node=struct.unpack_from('<I',raw)[0]
    if (dword(0x68d2f8+delta)!=primary or dword(0x684940+delta)!=gui or
            dword(0x68ca68+delta)!=original_root or reader.read(0x68c9dc+delta,1)!=b'\0' or
            any(reader.read(address,len(raw))!=raw for address,raw in stamp)):
        raise RuntimeError('work progress changed during read')
    return progress


class PanelText:
    def __init__(self,reader,budget,encoding):
        self.reader=reader;self.remaining=budget;self.encoding=encoding
        self.stamp=[]

    def string(self,address):
        raw=bytearray()
        while self.remaining:
            count=min(128,self.remaining,4096-address%4096)
            part=self.reader.read(address,count);end=part.find(b'\0')
            used=end+1 if end>=0 else len(part)
            self.remaining-=used
            self.stamp.append((address,part[:used]))
            raw.extend(part[:end] if end>=0 else part)
            if end>=0:
                return bytes(b for b in raw if b>=32 or b in (9,10)).decode(self.encoding,errors='strict')
            address+=count
        raise TextBudgetRequired()


def read_own_card(reader,state,text):
    delta=state['game_base']-0x400000
    def read(va,n):return reader.read(va+delta,n)
    def u32(va):return struct.unpack('<I',read(va,4))[0]
    def u16(va):return struct.unpack('<H',read(va,2))[0]
    root=u32(0x68ca50);primary=u32(0x68d2f8);gui=u32(0x684940)
    if not root or not primary or primary!=gui or read(0x68c9dc,1)!=b'\0':return None
    table=u32(0x68d344)
    if not table:return None
    count=struct.unpack('<H',reader.read(table,2))[0]
    node=struct.unpack('<I',reader.read(root+0x42,4))[0]
    seen=set();slots=set();basic=[];skills=[];stamp=[]
    while node:
        # The native command card has nine slots. Reject malformed lists rather
        # than truncating or allowing cycles/unbounded pointer traversal.
        if node in seen or len(seen)>=9:return None
        seen.add(node);raw=reader.read(node,0x46)
        next_node=struct.unpack_from('<I',raw,0)[0]
        slot=struct.unpack_from('<H',raw,0x20)[0]
        flags=struct.unpack_from('<I',raw,0x18)[0]
        record=struct.unpack_from('<I',raw,0x26)[0]
        stamp.append((node,next_node,slot,flags,record))
        if flags&8:
            if slot not in range(1,10) or slot in slots or not record:return None
            slots.add(slot);button=reader.read(record,20)
            action_var,label_id=struct.unpack_from('<HH',button,14)
            if not 1<=label_id<=count:return None
            offset=struct.unpack('<H',reader.read(table+2*label_id,2))[0]
            if offset<2+count*2:return None
            label=table+offset
            header=reader.read(label,2) # category zero is a header byte, not strlen
            name=text.string(label+2)
            item={'slot':slot,'name':name,'enabled':not bool(flags&2)}
            if header[1]==0:
                action=struct.unpack_from('<I',button,8)[0]-delta
                if action in BASIC_ACTIONS:basic.append(item)
                else:
                    item.pop('enabled')
                    skills.append(item)
            elif header[1]==3:
                if action_var>=44:return None # native tech data has 44 entries
                item.pop('enabled') # learned knowledge is not live readiness
                # The native disabled-button tooltip shows the research or
                # requirement label and clears its cost fields (4A1DBA..1E9B).
                if not flags&2 and read(0x6af338+state['panel_type'],1)[0]&7:
                    cost=u16(0x69f548+2*action_var)
                    # Native tooltip rendering skips zero cost fields (4A22BC).
                    if cost:item['energy_cost']=cost
                skills.append(item)
        node=next_node
    if (u32(0x68ca50)!=root or u32(0x68d2f8)!=primary or u32(0x684940)!=gui or
            read(0x68c9dc,1)!=b'\0'):return None
    for node,next_node,slot,flags,record in stamp:
        raw=reader.read(node,0x2a)
        if (struct.unpack_from('<I',raw,0)[0],struct.unpack_from('<H',raw,0x20)[0],
            struct.unpack_from('<I',raw,0x18)[0],struct.unpack_from('<I',raw,0x26)[0])!=(next_node,slot,flags,record):
            return None
    return {'basic_commands':basic,'skills':skills}
