"""Stage-4 development transport. Raw panel/mailbox data is not public output."""
import ctypes as C
from pathlib import Path
import struct
from tools.stage3_client import Client as BaseClient, State as BaseState, FIELDS as BASE_FIELDS, OPS

EXTRA_FIELDS='''minimap_left minimap_top minimap_width minimap_height minimap_scale
details_wait_ms details_started_tick details_active details_ready
panel_available panel_owned panel_type panel_hp panel_max_hp panel_shields
panel_max_shields panel_has_shields panel_energy panel_armor_level panel_shield_level
panel_armor_base panel_ground_weapon panel_air_weapon panel_ground_base
panel_ground_bonus panel_air_base panel_air_bonus panel_ground_level panel_air_level
panel_has_energy panel_name_address
events_address events_bytes events_capacity events_epoch events_enabled events_fault event_hooks events_generation
view_wait_ms view_started_tick view_ready view_address view_count view_serial
contact_wait_ms dynamic_wait_ms scene_frame_count control_locked
scene_address scene_bytes scene_sample_max_ms panel_owner panel_visual panel_has_health
panel_armor_bonus panel_max_energy panel_ground_upgrade_id panel_air_upgrade_id panel_armor_upgrade_id panel_rescuable'''.split()
class State(BaseState):
    _fields_=[(n,C.c_int32 if n in {'minimap_left','minimap_top'} else C.c_uint32) for n in EXTRA_FIELDS]

class Client(BaseClient):
    dll_path=Path(__file__).resolve().parents[1]/'build/stage4/scapi_stage4_v10_rescue.dll'
    state_type=State
    fields=BASE_FIELDS+EXTRA_FIELDS
    protocol_version=10
    operations={**OPS,'move-minimap':9,'observe-selected':10,'learn-details':11,
                'capture-start':12,'capture-ack':13,'capture-stop':14,'observe-view':15}

    def configure_attention(self,*,view_ms,dynamic_ms,detail_ms,contact_ms):
        for value in (view_ms,dynamic_ms,detail_ms,contact_ms):
            if type(value) is not int or not 1<=value<2**31:raise ValueError('explicit valid attention durations required')
        if self.api.k.WaitForSingleObject(self.mutex,0) not in (0,0x80):raise ValueError('mailbox busy')
        try:
            s=self.peek()
            if s['state'] in (1,2) or s['events_enabled']:raise ValueError('configure only with idle mailbox and stopped capture')
            for name,value in [('view_wait_ms',view_ms),('details_wait_ms',detail_ms),
                               ('contact_wait_ms',contact_ms),('dynamic_wait_ms',dynamic_ms)]:
                self._write(getattr(State,name).offset,struct.pack('<I',value))
        finally:self.api.k.ReleaseMutex(self.mutex)

    def configure_detail_test(self,wait_ms):
        """Explicit development value; this does not choose a production policy."""
        if type(wait_ms) is not int or not 1<=wait_ms<2**31:raise ValueError('positive duration required')
        if self.api.k.WaitForSingleObject(self.mutex,0) not in (0,0x80):raise ValueError('mailbox busy')
        try:
            if self.peek()['state'] in (1,2):raise ValueError('request in flight')
            self._write(State.details_wait_ms.offset,struct.pack('<I',wait_ms))
        finally:self.api.k.ReleaseMutex(self.mutex)
