"""Explicit opt-in transport for the v50 diagnostic candidate; never installs it.

The default stage5_client remains v49 / ABI 27. Selecting this class requires
the separate v50 DLL and rejects old residents with the normal ABI check.
"""
import ctypes as C
from pathlib import Path
from tools.stage5_client import Client as BaseClient, State as BaseState

PHASE_FIELDS = '''phase_sample_serial phase_sample_epoch phase_sample_tick
phase_candidate phase_candidate_flags phase_diagnostic visible_phase
visible_phase_capabilities'''.split()


class State(BaseState):
    _fields_ = [(name, C.c_uint32) for name in PHASE_FIELDS]


class Client(BaseClient):
    dll_path = Path(__file__).resolve().parents[1] / 'build/stage5/scapi_stage5_v50_visible_phase_diagnostic.dll'
    state_type = State
    fields = BaseClient.fields + PHASE_FIELDS
    protocol_version = 28
