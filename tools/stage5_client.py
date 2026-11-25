"""Development transport for the fixed stage-five candidate; never public data."""
import ctypes as C
from pathlib import Path
import struct
from tools.stage4_client import Client as BaseClient,State as BaseState,EXTRA_FIELDS as S4_EXTRA
from tools.stage3_client import FIELDS as S3_FIELDS,RESULTS

EXTRA_FIELDS='''command_slot expected_command_generation command_generation command_address command_count command_phase
queue_serial expected_queue_serial train_slot queue_count queue_type0 queue_type1 queue_type2 queue_type3 queue_type4
action_qpc_low action_qpc_high finished_qpc_low finished_qpc_high qpc_frequency_low qpc_frequency_high
target_serial expected_target_serial cargo_address cargo_count cargo_serial expected_cargo_serial cargo_slot command_sample_serial queued_order group_slot pending_queued_order own_address own_count own_serial building_address building_bytes building_capacity building_epoch building_enabled building_fault building_ack placement_address placement_bytes placement_serial terrain_address terrain_bytes terrain_serial auto_panel_address auto_panel_bytes auto_panel_sequence auto_panel_ack auto_panel_fault queue_names_address build_sites_address build_sites_bytes build_sites_serial menu_open'''.split()
class State(BaseState):
    _fields_=[(name,C.c_uint32) for name in EXTRA_FIELDS]

class Client(BaseClient):
    dll_path=Path(__file__).resolve().parents[1]/'build/stage5/scapi_stage5_v53_stability.dll'
    state_type=State
    fields=S3_FIELDS+S4_EXTRA+EXTRA_FIELDS
    protocol_version=27
    operations={**BaseClient.operations,'command-card':16,'command-button':17,'command-target':18,
        'cancel-train-slot':19,'attack-point':20,'patrol-point':21,'attack-minimap':22,'patrol-minimap':23,
        'halt-construction':24,'cancel-construction':25,'cancel-research':26,'cancel-upgrade':27,
        'cancel-morph':28,'cancel-addon':29,'resume-construction':30,'right-click':31,
        'cancel-pending':32,'unload-one':33,'group-assign':34,'group-select':35,'focus-selected':36,'save-view':37,'own-overview':38,
        'building-start':39,'building-stop':40,'terrain-view':41,'placement-preview':42,
        'build-sites':43,'menu-state':45,'menu-open':46,'menu-close':47,'resource-sites':48}
    result_names=RESULTS+'''command_unavailable stale_command incompatible_state no_pending_target
not_applied pending_target queue_changed cargo_changed observation_pending'''.split()

    def arm(self,enabled,interval_ms=None):
        if interval_ms is not None and (type(interval_ms) is not int or not 334<=interval_ms<2**31):
            raise ValueError('stage five requires an interval of at least 334 milliseconds')
        return super().arm(enabled,interval_ms)

    def _request_extra(self,extra):
        allowed={'command_slot','expected_command_generation','expected_queue_serial','train_slot','expected_target_serial',
                 'expected_cargo_serial','cargo_slot','queued_order','group_slot'}
        extra=extra or {}
        if not isinstance(extra,dict) or set(extra)-allowed:raise ValueError('unsupported request fields')
        for name,value in extra.items():
            if type(value) is not int or not 0<=value<2**32:raise ValueError('invalid fixed request field')
        if extra.get('queued_order',0)>1 or extra.get('group_slot',0)>9:raise ValueError('invalid queue flag or group slot')
        for name in allowed:self._write(getattr(State,name).offset,struct.pack('<I',extra.get(name,0)))
