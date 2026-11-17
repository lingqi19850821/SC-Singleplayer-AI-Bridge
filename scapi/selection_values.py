"""Current own HP/shields/energy only for real GUI-selected members."""
import struct


def selected_own_values(reader,game_base,pointer,kind,selected):
    delta=game_base-0x400000
    def dword(address):return struct.unpack('<I',reader.read(address,4))[0]
    def word(address):return struct.unpack('<H',reader.read(address,2))[0]
    first=0x5b2e68+delta
    if (not first<=pointer<first+1700*0x148 or (pointer-first)%0x148 or
            pointer not in struct.unpack('<12I',selected) or not 0<=kind<228):
        raise RuntimeError('invalid selected member')
    local=dword(0x4f30c0+delta)
    stamp=(word(pointer+0x64),reader.read(pointer+0x4c,1)[0],reader.read(pointer+0xa5,1))
    if local>=12 or stamp[:2]!=(kind,local):raise RuntimeError('selected member changed')
    hp=dword(pointer+8)
    maximum=dword(0x6abd38+delta+kind*4)//256
    maximum=maximum or (hp+255)//256 or 1
    shield_max=word(0x6adb78+delta+kind*2)
    status=dword(pointer+0xdc)
    result={}
    if maximum<=9999 and shield_max<=9999 and not status&0x04000000:
        result['hp']=(hp+255)//256
        if reader.read(0x6abb68+delta+kind,1)!=b'\0':
            result['shields']=dword(pointer+0x60)//256
    if dword(0x6b0358+delta+kind*4)&0x200000:
        result['energy']=word(pointer+0xa2)//256
    if (reader.read(0x684940+delta,48)!=selected or dword(0x4f30c0+delta)!=local or
            (word(pointer+0x64),reader.read(pointer+0x4c,1)[0],reader.read(pointer+0xa5,1))!=stamp):
        raise RuntimeError('selected member changed during read')
    return result
