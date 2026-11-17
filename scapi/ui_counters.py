"""Read only the currently shown target-1.05 leaderboard text controls.

Each entry is one actual UI text control, not an inferred objective/score.
Adjacent controls remain separate. No trigger, goal, score or player array is
read. Mapping proof: evidence/ammunition-panel-20261114/terrain-counter/.
"""
import struct
from time import monotonic as _monotonic

from .resources import (_identity,_state_gate,_native_guard,_SnapshotRejected,
                        _BRIDGE_GUARD,ProbeError)
from .observation_budget import ObservationReadBudget,ObservationDeadlineExceeded

_ROOT=0x68d0a0
_CLIP_RECT=0x4fe778
_TEXT_DRAW=0x4a88c0
_CONTROL_BYTES=0x46
_LAYOUT_ANCHORS=(
    (0x4a8390,bytes.fromhex('68f0624400ba405d4400b924874f00')),
    (0x4a83be,bytes.fromhex('a3a0d06800e8c8da0200')),
    (0x4a881e,bytes.fromhex('66837922097507c7412ec0884a00')),
    (0x4a88c0,bytes.fromhex('568bf18b461485c07442')),
    (0x4d7970,bytes.fromhex('568bf1f64618087425')),
    (0x4d69f0,bytes.fromhex('668379220074038b49328b4142')),
)


class _ReadBudget(Exception):
    def __init__(self,result):self.result=result


def _pointer(address,size):
    if not 0<address<=0xffffffff-size:
        raise _SnapshotRejected('invalid_ui_counter_layout','unavailable')


def _control_stamp(raw):
    # Links/rectangle, text/flags, id/type, draw callback and parent/root-child
    # pointers are material. Unrelated redraw/cursor fields do not timestamp UI.
    return raw[:12],raw[0x14:0x24],raw[0x2e:0x36],raw[0x42:0x46]


def _text_unchanged(reader,address,raw):
    offset=0
    while offset<len(raw):
        current=address+offset
        count=min(128,len(raw)-offset,4096-current%4096)
        if reader.read(current,count)!=raw[offset:offset+count]:return False
        offset+=count
    return True


def _visible_controls(reader,*,text_budget_bytes,control_budget,encoding,budget):
    def dword(address):return struct.unpack('<I',reader.read(address,4))[0]
    root=dword(_ROOT)
    clip=reader.read(_CLIP_RECT,16)
    stamps={};strings={};remaining=text_budget_bytes;rows=[]
    if not root:return rows,(root,clip,stamps,strings)
    _pointer(root,_CONTROL_BYTES)
    root_raw=reader.read(root,_CONTROL_BYTES);stamps[root]=_control_stamp(root_raw)
    if struct.unpack_from('<H',root_raw,0x22)[0]!=0:
        raise _SnapshotRejected('invalid_ui_counter_layout','unavailable')
    if not struct.unpack_from('<I',root_raw,0x18)[0]&8:
        return rows,(root,clip,stamps,strings)
    left,top,right,bottom=struct.unpack_from('<4h',root_raw,4)
    cx,cy,cr,cb=struct.unpack('<4i',clip)
    if not (cx<=left<=right<=cr and cy<=top<=bottom<=cb):
        raise _SnapshotRejected('ui_counters_not_fully_visible','unavailable')
    node=struct.unpack_from('<I',root_raw,0x42)[0];seen={root}
    while node:
        budget.check()
        if node in seen:
            raise _SnapshotRejected('invalid_ui_counter_layout','unavailable')
        if len(seen)-1>=control_budget:
            raise _ReadBudget({'status':'larger_observation_budget_required',
                               'required_controls_at_least':control_budget+1})
        seen.add(node);_pointer(node,_CONTROL_BYTES)
        raw=reader.read(node,_CONTROL_BYTES);stamps[node]=_control_stamp(raw)
        parent=struct.unpack_from('<I',raw,0x32)[0]
        if parent!=root:raise _SnapshotRejected('invalid_ui_counter_layout','unavailable')
        node=struct.unpack_from('<I',raw)[0]
        if not struct.unpack_from('<I',raw,0x18)[0]&8:continue
        if (struct.unpack_from('<H',raw,0x22)[0]!=9 or
                struct.unpack_from('<I',raw,0x2e)[0]!=_TEXT_DRAW):continue
        x,y,x2,y2=struct.unpack_from('<4h',raw,4)
        if not (0<=x<=x2<=right-left and 0<=y<=y2<=bottom-top):continue
        address=struct.unpack_from('<I',raw,0x14)[0]
        if not address:continue
        if address not in strings:
            captured=bytearray()
            while True:
                budget.check()
                if not remaining:
                    raise _ReadBudget({'status':'larger_text_budget_required',
                                       'required_bytes_at_least':text_budget_bytes+1})
                current=address+len(captured)
                count=min(128,remaining,4096-current%4096)
                _pointer(current,count)
                part=reader.read(current,count)
                if len(part)!=count:raise _SnapshotRejected('ui_counters_read_failed','unavailable')
                end=part.find(b'\0');used=end+1 if end>=0 else len(part)
                remaining-=used;captured.extend(part[:used])
                if end>=0:break
            strings[address]=bytes(captured)
        # Strip native colour/format controls using the same public text rule as
        # selected-panel text, preserving the actual bytes' decoded wording.
        text=bytes(value for value in strings[address][:-1] if value>=32 or value in (9,10)).decode(encoding,errors='strict')
        budget.check()
        if text:rows.append((y,x,{'text':text}))
    budget.check()
    rows.sort(key=lambda item:item[:2])
    budget.check()
    return [row[2] for row in rows],(root,clip,stamps,strings)


def ui_counters_snapshot(client,expected_epoch,*,text_budget_bytes=65536,control_budget=64,encoding='cp1252',_budget=None):
    """Free read; grow explicit read budgets on demand, never return a prefix.

    Native pause/menu, process/epoch drift, layout mismatch, changing UI data and
    malformed linked lists return no partial counters. Budget replies are read
    requirements rather than truncation or a persistent limit on displayed UI.
    One cooperative time budget covers reads and validation, including retries
    when Stage5 passes the same internal budget. No OS read is interruptible.
    """
    if type(expected_epoch) is not int or not 0<expected_epoch<2**32:raise ValueError('valid expected epoch required')
    if type(text_budget_bytes) is not int or text_budget_bytes<1:raise ValueError('positive text budget required')
    if type(control_budget) is not int or control_budget<1:raise ValueError('positive control budget required')
    if not isinstance(encoding,str) or not encoding:raise ValueError('text encoding required')
    budget=_budget if _budget is not None else ObservationReadBudget(clock=_monotonic)
    try:
        budget.check()
        identity=_identity(client.process);reader=budget.reader(client.reader)
        if _identity(reader.metadata())!=identity:raise _SnapshotRejected('different_process')
        before=client.peek();budget.check();_state_gate(before,expected_epoch)
        for address,expected in _LAYOUT_ANCHORS:
            if reader.read(address,len(expected))!=expected:
                raise _SnapshotRejected('unsupported_ui_counter_layout','unavailable')
        native_before=_native_guard(reader)
        active,tiles,pause,frame,*_=native_before
        if active!=1 or not tiles:raise _SnapshotRejected('no_game','unavailable')
        if pause:raise _SnapshotRejected('paused','unavailable')
        if tiles!=before['tile_pointer'] or frame<before['raw_counter']:
            raise _SnapshotRejected('observation_changed_during_read')
        counters,(root,clip,stamps,strings)=_visible_controls(reader,
            text_budget_bytes=text_budget_bytes,control_budget=control_budget,encoding=encoding,budget=budget)
        if (reader.read(_ROOT,4)!=struct.pack('<I',root) or reader.read(_CLIP_RECT,16)!=clip or
                any(_control_stamp(reader.read(address,_CONTROL_BYTES))!=stamp for address,stamp in stamps.items()) or
                any(not _text_unchanged(reader,address,raw) for address,raw in strings.items())):
            raise _SnapshotRejected('observation_changed_during_read')
        native_after=_native_guard(reader);after=client.peek();budget.check();_state_gate(after,expected_epoch)
        if _identity(reader.metadata())!=identity:raise _SnapshotRejected('different_process')
        if (native_before!=native_after or after['raw_counter']>native_after[3] or
                any(after[key]!=before[key] for key in _BRIDGE_GUARD)):
            raise _SnapshotRejected('observation_changed_during_read')
        budget.check()
        return {'status':'observed','observed_frame':frame,'source':'visible_leaderboard_controls','counters':counters}
    except ObservationDeadlineExceeded:return {'status':'unavailable','reason':'ui_counters_deadline_exceeded'}
    except _ReadBudget as error:return error.result
    except _SnapshotRejected as error:return {'status':error.status,'reason':error.reason}
    except (ProbeError,OSError,ValueError,KeyError,IndexError,struct.error):
        return {'status':'unavailable','reason':'ui_counters_read_failed'}
