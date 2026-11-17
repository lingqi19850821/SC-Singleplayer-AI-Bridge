# Modified 2026-11-16: package-relative import; research CLI omitted.
"""Bounded stage-3 development client; only our DLL's fixed mailbox is writable.

No arbitrary address, packet, unit ID or hidden-state query is exposed. Native
diagnostics are separately marked and never used as playable observations.
"""
from __future__ import annotations
import argparse
import ctypes as C
from ctypes import wintypes as W
from datetime import datetime, timezone
import json
from pathlib import Path
import struct
import sys
import time
import warnings

from tools.stage2_readprobe import WinAPI, WinReader, MODULEENTRY32W, ProbeError, path_key

ROOT=Path(__file__).resolve().parents[1]
DLL=ROOT/'build/stage3/scapi_stage3_v5.dll'
ALLOWED=(Path('D:/Starcraft/starcraft.exe'),)
FIELDS='''magic version bytes state serial operation expected_epoch expected_selection
x y enable_actions result completed_serial hook_installed game_base ui_thread
observed_thread all_calls site_calls other_thread_calls reentries epoch active pause
raw_counter tile_pointer camera_x camera_y camera_max_x camera_max_y width_tiles
height_tiles selection_generation selection_count first_site_tick last_site_tick
last_caller before_queue after_queue actual_x actual_y point_blocked x2 y2
add_selection action_interval_ms last_action_tick executed_tick finished_tick action_count'''.split()
SIGNED={'state','x','y','x2','y2','camera_x','camera_y','camera_max_x','camera_max_y','actual_x','actual_y'}
class State(C.Structure):
    _fields_=[(name,C.c_int32 if name in SIGNED else C.c_uint32) for name in FIELDS]
OPS={'probe':1,'camera':2,'select':3,'move':4,'detach':5,'ui-point':6,'box':7,'same':8}
RESULTS='''ok bad_operation no_game stale_epoch paused camera_locked uninitialized
outside_map not_on_screen ui_blocked no_selectable_unit no_selection stale_selection
not_owned queue_unconfirmed bad_thread detached disabled protection_restore_failed queue_full bad_argument script_controlled'''.split()

class MailboxBusyError(ValueError):
    """Another request owns the single shared action channel; never retry it."""

def dll_export_rva(name,dll_path=DLL,state_type=State):
    """Read the one built PE's named export; reject forwarded/out-of-image data."""
    b=dll_path.read_bytes(); pe=struct.unpack_from('<I',b,0x3c)[0]; o=pe+24
    if b[:2]!=b'MZ' or b[pe:pe+4]!=b'PE\0\0' or struct.unpack_from('<H',b,o)[0]!=0x10b:
        raise ValueError('not our PE32 DLL')
    image=struct.unpack_from('<I',b,o+56)[0]
    count=struct.unpack_from('<H',b,pe+6)[0]; osize=struct.unpack_from('<H',b,pe+20)[0]
    sections=[struct.unpack_from('<IIII',b,o+osize+i*40+8) for i in range(count)]
    def off(rva,n=1):
        for vs,va,rs,raw in sections:
            if va<=rva and rva+n<=va+rs:
                return raw+rva-va
        raise ValueError('export outside file-backed image')
    er,es=struct.unpack_from('<II',b,o+96); q=off(er,40)
    _,_,_,_,_,_,nf,nn,funcs,names,ords=struct.unpack_from('<IIHHIIIIIII',b,q)
    for i in range(nn):
        nr=struct.unpack_from('<I',b,off(names+i*4,4))[0]; a=off(nr)
        end=b.find(b'\0',a)
        if end<0: raise ValueError('bad export name')
        if b[a:end].decode('ascii')==name:
            ordinal=struct.unpack_from('<H',b,off(ords+i*2,2))[0]
            if ordinal>=nf:raise ValueError('bad export ordinal')
            rva=struct.unpack_from('<I',b,off(funcs+ordinal*4,4))[0]
            if not rva or er<=rva<er+es or rva+C.sizeof(state_type)>image:
                raise ValueError('bad mailbox export')
            return rva
    raise ValueError('missing mailbox export')

class Client:
    dll_path=DLL
    state_type=State
    fields=FIELDS
    protocol_version=2
    operations=OPS
    result_names=RESULTS

    def __init__(self,pid):
        self.api=WinAPI(); self.reader=WinReader(self.api,pid)
        self.writer=None; self.mutex=None
        try:
            self.process=self.reader.metadata()
            if path_key(self.process['path']) not in {path_key(p) for p in ALLOWED}:
                raise ValueError('unapproved target path')
            h=self.api.snapshot(0x18,pid); found=set()
            try:
                e=MODULEENTRY32W();e.dwSize=C.sizeof(e)
                ok=self.api.k.Module32FirstW(h,C.byref(e))
                while ok:
                    if path_key(e.szExePath)==path_key(self.dll_path):found.add((e.modBaseAddr,e.modBaseSize))
                    ok=self.api.k.Module32NextW(h,C.byref(e))
                if C.get_last_error()!=18:raise self.api.failure('module enumeration')
            finally:self.api.k.CloseHandle(h)
            if len(found)!=1:raise ValueError('our injected DLL not unique/present')
            base,size=found.pop(); rva=dll_export_rva('SCAPI_State',self.dll_path,self.state_type)
            if rva+C.sizeof(self.state_type)>size:raise ValueError('mailbox outside DLL')
            self.address=base+rva
            self.peek()
            k=self.api.k
            k.WriteProcessMemory.argtypes=[W.HANDLE,C.c_void_p,C.c_void_p,C.c_size_t,C.POINTER(C.c_size_t)]
            k.WriteProcessMemory.restype=W.BOOL
            k.CreateMutexW.argtypes=[C.c_void_p,W.BOOL,W.LPCWSTR];k.CreateMutexW.restype=W.HANDLE
            k.WaitForSingleObject.argtypes=[W.HANDLE,W.DWORD];k.WaitForSingleObject.restype=W.DWORD
            k.ReleaseMutex.argtypes=[W.HANDLE];k.ReleaseMutex.restype=W.BOOL
            self.writer=k.OpenProcess(0x438,False,pid)
            if not self.writer:raise self.api.failure('OpenProcess(mailbox writer)')
            times=[W.FILETIME() for _ in range(4)]
            if not k.GetProcessTimes(self.writer,*(C.byref(t) for t in times)):
                raise self.api.failure('writer identity')
            creation=times[0].dwLowDateTime|(times[0].dwHighDateTime<<32)
            if creation!=self.process['creation_time_filetime'] or path_key(self.api.image_path(self.writer))!=path_key(self.process['path']):
                raise ValueError('process identity changed')
            self.mutex=k.CreateMutexW(None,False,f'Local\\SCAPI.Stage3.{pid}.{creation}')
            if not self.mutex:raise self.api.failure('CreateMutexW')
            # This helper waits for the actual game, independently of API.close().
            from scapi.cursor_recovery import arm_game_exit_cursor_reset
            try:self.cursor_exit_watch=arm_game_exit_cursor_reset(self.process)
            except Exception as error:
                self.cursor_exit_watch={'status':'unavailable','reason':str(error)}
                warnings.warn('game-exit cursor recovery unavailable: '+str(error), RuntimeWarning)
        except BaseException:
            self.close();raise

    def close(self):
        for name in ('writer','mutex'):
            h=getattr(self,name,None)
            if h:self.api.k.CloseHandle(h);setattr(self,name,None)
        self.reader.close()

    def peek(self):
        raw=self.reader.read(self.address,C.sizeof(self.state_type));s=self.state_type.from_buffer_copy(raw)
        if (s.magic,s.version,s.bytes)!=(0x33504353,self.protocol_version,C.sizeof(self.state_type)):
            raise ValueError('mailbox signature/size mismatch')
        return {n:getattr(s,n) for n in self.fields}

    def _write(self,offset,data):
        if offset<0 or offset+len(data)>C.sizeof(self.state_type):raise ValueError('outside fixed mailbox')
        buf=C.create_string_buffer(data);n=C.c_size_t()
        if not self.api.k.WriteProcessMemory(self.writer,self.address+offset,buf,len(data),C.byref(n)) or n.value!=len(data):
            raise self.api.failure('mailbox write',received=n.value,expected=len(data))

    def arm(self,enabled,interval_ms=None):
        if self.api.k.WaitForSingleObject(self.mutex,0) not in (0,0x80):raise MailboxBusyError('another client owns mailbox')
        try:
            s=self.peek()
            if s['state'] in (1,2):raise ValueError('in-flight request; no concurrent arm')
            if enabled and (s['hook_installed']!=1 or not s['site_calls'] or
                            s['observed_thread']!=s['ui_thread']):
                raise ValueError('counting probe must first confirm a healthy hook and the UI site thread')
            if interval_ms is not None:
                if type(interval_ms) is not int or not 1<=interval_ms<2**31:
                    raise ValueError('interval must be a positive signed-32-bit millisecond duration')
                self._write(State.action_interval_ms.offset,struct.pack('<I',interval_ms))
            if enabled and not 1<=self.peek()['action_interval_ms']<2**31:
                raise ValueError('choose an explicit global action interval before arming')
            self._write(State.enable_actions.offset,struct.pack('<I',int(enabled)))
            return self.peek()
        finally:self.api.k.ReleaseMutex(self.mutex)

    def _request_extra(self,extra):
        if extra:raise ValueError('this protocol has no extra request fields')

    def request(self,op,x=0,y=0,epoch=None,selection=None,timeout=5.0,*,x2=0,y2=0,add=False,extra=None):
        if op not in self.operations:raise ValueError('unsupported operation')
        if any(type(v) is not int or not -2**31<=v<2**31 for v in (x,y,x2,y2)):
            raise ValueError('coordinates must be signed 32-bit integers')
        if type(add) is not bool:raise ValueError('add must be boolean')
        # A collector ACK may briefly own this read-only observation's mutex.
        # Wait once before publication; never resend or wait on unknown work.
        mutex_wait_ms=200 if op=='menu-state' else 0
        if self.api.k.WaitForSingleObject(self.mutex,mutex_wait_ms) not in (0,0x80):raise MailboxBusyError('another client owns mailbox')
        try:
            before=self.peek()
            if before['state'] in (1,2):raise MailboxBusyError('request already in flight; outcome must be inspected, never auto-retry')
            if not before['hook_installed']:raise ValueError('hook is detached')
            serial=(before['serial']+1)&0xffffffff
            if not serial:raise ValueError('serial exhausted; restart required')
            epoch=before['epoch'] if epoch is None else epoch
            selection=before['selection_generation'] if selection is None else selection
            self._request_extra(extra)
            payload=struct.pack('<IIIIii',serial,self.operations[op],epoch,selection,x,y)
            self._write(State.serial.offset,payload)
            self._write(State.x2.offset,struct.pack('<iiI',x2,y2,int(add)))
            # Payload is complete before the aligned 4-byte publish. The DLL
            # acquires this state with InterlockedCompareExchange.
            self._write(State.state.offset,struct.pack('<i',1))
            started=time.perf_counter();after=None
            while time.perf_counter()-started<timeout:
                after=self.peek()
                if after['state']==3 and after['completed_serial']==serial:
                    return {'operation':op,'serial':serial,'result':self.result_names[after['result']] if after['result']<len(self.result_names) else 'unknown',
                            'elapsed_ms':round((time.perf_counter()-started)*1000,3),
                            'requested':{'x':x,'y':y,'x2':x2,'y2':y2,'add':add,'epoch':epoch,'selection_generation':selection},
                            'developer_before':before,'developer_after':after}
                time.sleep(0.01)
            raise TimeoutError(f'outcome unknown for serial {serial}; do not retry; inspect existing mailbox')
        finally:self.api.k.ReleaseMutex(self.mutex)
