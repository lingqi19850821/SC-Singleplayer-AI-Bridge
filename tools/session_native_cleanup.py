"""Owned-session v49 withdrawal, plus an independent read-only verifier.

Import never connects, enumerates, loads a DLL, starts a process or sends an
action. Call only from the authorized host worker with its already owned
Client and fresh run evidence. This module never installs or unloads a DLL,
closes a process, changes pause state, clears faults or fabricates ACKs.

The 73-span inventory / five ping ABI spans and zero fields are reused from
evidence/minimap-ping-live-20261114/{prepare_runtime,verify_closeout}.py.
Historical bytes, PID and flags are NOT defaults or current evidence.
"""
from copy import deepcopy
from pathlib import Path, PureWindowsPath
import struct
from tools.native_episode_artifacts import approved_episode_candidate

GAME = 'D:/Starcraft/starcraft.exe'
# Modified 2026-11-16: resolve artifacts within this standalone checkout.
ROOT = Path(__file__).resolve().parents[1]
CANDIDATE = str(ROOT / 'build/stage5/scapi_stage5_v49_minimap_ping.dll')
NEXT_CANDIDATE = str(ROOT / 'build/stage5/scapi_stage5_v49_next_episode.dll')
ALLOWED_CANDIDATES = (CANDIDATE, NEXT_CANDIDATE)


def approved_candidate(candidate):
    return (_path(candidate) in {_path(p) for p in ALLOWED_CANDIDATES} or
            approved_episode_candidate(candidate))
ZERO_FIELDS = ('enable_actions', 'hook_installed', 'events_enabled', 'event_hooks',
               'events_fault', 'building_enabled', 'building_fault', 'auto_panel_fault')
V49_SPANS = (
    (0x405070,64),(0x40ca50,960),(0x40ce10,1452),(0x40d500,800),(0x410669,7),
    (0x411e40,43),(0x418f00,87),(0x418f60,131),(0x41a0e0,8),(0x41a7c3,8),
    (0x41ab52,8),(0x41dd50,32),(0x41e010,308),(0x41e200,64),(0x421ff8,76),
    (0x424660,32),(0x424940,151),(0x424b20,62),(0x424b80,51),(0x425390,62),
    (0x42bba0,176),(0x42d000,256),(0x433fe0,373),(0x435237,25),(0x43547a,14),
    (0x43548f,6),(0x4354b0,4),(0x43aaa0,163),(0x43ab50,306),(0x43b220,122),
    (0x443d80,80),(0x446c27,6),(0x446ef0,31),(0x4478a0,106),(0x4486f1,10),
    (0x448820,105),(0x44883a,5),(0x448890,106),(0x4488aa,5),(0x462e30,252),
    (0x462f50,132),(0x462fe0,483),(0x469480,7),(0x46966b,5),(0x46a9c0,151),
    (0x46bcf6,9),(0x470420,192),(0x470d10,138),(0x470da0,36),(0x470dd0,68),
    (0x470e70,64),(0x472390,32),(0x47a970,16),(0x47a980,16),(0x47e6d0,64),
    (0x47eaf7,5),(0x47f920,312),(0x48cae0,404),(0x48dea0,34),(0x499e80,60),
    (0x49d626,23),(0x49d8d0,64),(0x4a17c0,64),(0x4a2cb0,16),(0x4a452c,64),
    (0x4a9600,256),(0x4a96dd,7),(0x4aa3d0,64),(0x4c0ba0,1088),(0x4c10d0,64),
    (0x4c1960,64),(0x4c1ad0,64),(0x4d69f0,96))
PING_ABI = (
    (0x46bc80,'538bda56578bc133f6ba48036500'),
    (0x46bcc8,'c6864803650001'),
    (0x4b9630,'568bf1803e007458e8e38e00003b05c0304f00754be8d68f000085c07542'),
    (0x4b968b,'e8f025fbff'),(0x4b8cd7,'e8a42ffbff'))
BINDING_KEYS = ('run_id','pid','process_creation','epoch','candidate','capture_generation')


class CleanupBlocked(RuntimeError):
    pass


def _require(condition, reason):
    if not condition:
        raise CleanupBlocked(reason)


def _path(value):
    return str(PureWindowsPath(value)).casefold()


def _binding(binding):
    _require(isinstance(binding.get('run_id'),str) and bool(binding['run_id']), 'run_id_required')
    for key in ('pid','process_creation','epoch','capture_generation'):
        _require(type(binding.get(key)) is int and binding[key]>0, 'invalid_binding_'+key)
    _require(approved_candidate(binding.get('candidate','')), 'v49_candidate_required')


def _same_binding(actual, expected):
    _require(all(actual.get(key)==expected[key] for key in BINDING_KEYS), 'receipt_binding_changed')


def _process(process, binding):
    _require(process.get('pid')==binding['pid'] and
             process.get('creation_time_filetime')==binding['process_creation'] and
             _path(process.get('path',''))==_path(GAME), 'process_identity_changed')


def _scope(client, binding, *, allow_inflight=False):
    _process(client.reader.metadata(), binding)
    _require(_path(str(client.dll_path))==_path(binding['candidate']) and
             client.protocol_version==27, 'transport_candidate_changed')
    state=client.peek()
    _require((state.get('magic'),state.get('version'),state.get('bytes'))==(0x33504353,27,676),
             'mailbox_layout_changed')
    _require(state.get('game_base')==0x400000,'target_base_changed')
    _require(state.get('epoch')==binding['epoch'] and
             state.get('events_epoch')==binding['epoch'] and
             state.get('events_generation')==binding['capture_generation'], 'native_generation_changed')
    _require(allow_inflight or state.get('state') not in (1,2), 'mailbox_in_flight_no_retry')
    _require(all(type(state.get(key)) is int for key in
                 ZERO_FIELDS+('auto_panel_sequence','auto_panel_ack','action_count','serial','completed_serial')),
             'incomplete_native_state')
    return state


def validate_original_snapshot(binding, original):
    """Reject absent/partial/other-run snapshots before any mutation."""
    _require(isinstance(binding,dict) and isinstance(original,dict),'original_snapshot_and_binding_required')
    _binding(binding)
    _process(original.get('process',{}), binding)
    _require(original.get('run_id')==binding['run_id'] and
             _path(original.get('pinned_dll',''))==_path(binding['candidate']) and
             original.get('protocol')==27 and original.get('state_bytes')==676 and
             original.get('captured_before_install') is True, 'fresh_original_snapshot_required')
    rows=original.get('code_matches',[])
    keys=[(int(row['va'],16),row['bytes']) for row in rows]
    _require(len(keys)==73 and set(keys)==set(V49_SPANS), 'complete_v49_73_spans_required')
    for row in rows:
        _require(row.get('matched') is True and len(bytes.fromhex(row['expected_hex']))==row['bytes'],
                 'original_bytes_missing_or_not_confirmed')
    abi=original.get('minimap_ping_abi_checks',[])
    _require(len(abi)==5 and {(int(row['va'],16),row.get('expected_hex')) for row in abi}==set(PING_ABI)
             and all(row.get('matched') is True and row.get('bytes')==len(bytes.fromhex(row['expected_hex']))
                     for row in abi), 'complete_v49_ping_abi_required')
    system=original.get('IAT_system_module',{})
    iat=original.get('IAT')
    base,size=system.get('base'),system.get('size')
    _require(type(iat) is int and 0<iat<2**32 and
             type(base) is int and type(size) is int and 0<base<2**32 and
             0<size and base+size<=2**32 and PureWindowsPath(system.get('path','')).name.casefold()
             in ('kernel32.dll','kernelbase.dll') and
             base<=iat<base+size,
             'original_iat_system_target_required')
    _require(isinstance(original.get('resident_candidates'),list), 'original_resident_inventory_required')
    prior_paths=[_path(row['path']) for row in original['resident_candidates']]
    _require(len(prior_paths)==len(set(prior_paths)) and _path(binding['candidate']) not in prior_paths,
             'candidate_not_absent_before_install')
    for row in original['resident_candidates']:
        _require(all(row.get('zero_fields',{}).get(key)==0 for key in ZERO_FIELDS),
                 'prior_resident_not_inert')
    return True


def _inventory_issues(residents, client, binding, original, *, all_inert):
    issues=[]
    expected_paths={_path(row['path']) for row in original['resident_candidates']}|{_path(binding['candidate'])}
    paths=[_path(row['path']) for row in residents]
    if len(paths)!=len(set(paths)) or set(paths)!=expected_paths:
        issues.append('resident_inventory_changed')
    for row in residents:
        state=row.get('state',{});target=_path(row['path'])==_path(binding['candidate'])
        if ((state.get('magic'),state.get('version'),state.get('bytes'))!=(0x33504353,27,676) or
                ((all_inert or not target) and any(state.get(key)!=0 for key in ZERO_FIELDS))):
            issues.append('resident_activity_or_fault_'+row['path'])
        if target and row.get('mailbox_address')!=client.address:
            issues.append('candidate_mailbox_changed')
    for prior in original['resident_candidates']:
        match=[row for row in residents if _path(row['path'])==_path(prior['path'])]
        if len(match)!=1 or any(match[0].get(key)!=prior.get(key) for key in ('base','size')):
            issues.append('prior_resident_identity_changed')
    return issues


def validate_quiescence(binding, receipt):
    _same_binding(receipt.get('binding',{}),binding)
    _require(receipt.get('actions_frozen') is True and receipt.get('inflight_actions')==0,
             'host_actions_not_quiescent')
    collector=receipt.get('collector',{})
    _process(collector.get('process',{}),binding)
    native,persisted=collector.get('native_status',{}),collector.get('persisted',{})
    _require(collector.get('running') is False and not collector.get('error') and
             not collector.get('cleanup_error') and collector.get('capture_stop_result')=='ok' and
             collector.get('capture_generation')==binding['capture_generation'] and
             native.get('epoch')==binding['epoch'] and persisted.get('epoch')==binding['epoch'] and
             persisted.get('closed_ns') is not None, 'collector_not_closed_for_binding')
    _require(all(native.get(key)==0 for key in ('flags','lost','invalid_events','last_error')) and
             persisted.get('overflow') is False and
             all(persisted.get(key)==0 for key in ('lost_max','gap_events','missing_sequences')) and
             native.get('write_seq')==native.get('ack_seq') and
             persisted.get('last_sequence')==native.get('write_seq',0)-1,
             'collector_not_durable_drained_or_fault_free')


def module_entry_for_api(api):
    """Use the exact ctypes structure bound to this API instance's functions.

    CLI imports can load stage2_readprobe under both package and bare names.
    Equal layouts remain distinct Python ctypes classes; do not cast past the
    type check or replace a live function's declared signature.
    """
    import ctypes as C
    first=getattr(api.k.Module32FirstW,'argtypes',None)
    following=getattr(api.k.Module32NextW,'argtypes',None)
    _require(isinstance(first,(list,tuple)) and len(first)==2 and
             isinstance(following,(list,tuple)) and len(following)==2,
             'module_enumeration_signature_missing')
    entry_type=getattr(first[1],'_type_',None)
    fields=('dwSize','th32ModuleID','th32ProcessID','GlblcntUsage','ProccntUsage',
            'modBaseAddr','modBaseSize','hModule','szModule','szExePath')
    _require(isinstance(entry_type,type) and issubclass(entry_type,C.Structure) and
             entry_type.__name__=='MODULEENTRY32W' and
             tuple(row[0] for row in entry_type._fields_)==fields and
             getattr(following[1],'_type_',None) is entry_type,
             'module_enumeration_structure_binding_changed')
    entry=entry_type();entry.dwSize=C.sizeof(entry)
    return entry


def read_current_inventory(client):
    """Future authorized use only: reuse the owned handle, enumerate its modules.

    No process discovery and no Client construction. All returned SCAPI state
    reads are from current modules of this exact process, never an old list.
    """
    import ctypes as C
    from pathlib import Path
    from tools.stage3_client import dll_export_rva
    entry=module_entry_for_api(client.api)
    handle=client.api.snapshot(0x18,client.process['pid'])
    rows=[]
    try:
        ok=client.api.k.Module32FirstW(handle,C.byref(entry))
        while ok:
            if PureWindowsPath(entry.szExePath).name.casefold().startswith('scapi_'):
                rva=dll_export_rva('SCAPI_State',Path(entry.szExePath),client.state_type)
                header=struct.unpack('<3I',client.reader.read(entry.modBaseAddr+rva,12))
                _require(header==(0x33504353,27,676),'resident_unknown_layout')
                raw=client.reader.read(entry.modBaseAddr+rva,676)
                state=client.state_type.from_buffer_copy(raw)
                _require((state.magic,state.version,state.bytes)==(0x33504353,27,676),
                         'resident_unknown_layout')
                rows.append({'path':entry.szExePath,'base':entry.modBaseAddr,'size':entry.modBaseSize,
                             'mailbox_address':entry.modBaseAddr+rva,
                             'state':{key:getattr(state,key) for key in client.fields}})
            ok=client.api.k.Module32NextW(handle,C.byref(entry))
        _require(C.get_last_error()==18,'module_inventory_incomplete')
    finally:
        client.api.k.CloseHandle(handle)
    # WoW64 snapshots can repeat the identical module. Only exact mappings
    # collapse; a different base/size or duplicate candidate remains an issue.
    return list({(_path(row['path']),row['base'],row['size']):row for row in rows}.values())


def verify_native_cleanup(client, *, binding, original, quiescence, drained,
                          inventory_reader=read_current_inventory):
    """Independent read-only check; also usable after an unknown request result.

    Uses no request(), arm(), ACK, external process exit or API.close(). Missing
    evidence raises CleanupBlocked; mismatches return fully_withdrawn:false.
    """
    validate_original_snapshot(binding,original)
    validate_quiescence(binding,quiescence)
    _same_binding(drained.get('binding',{}),binding)
    _require(drained.get('building_worker_closed') is True and
             drained.get('building_flushed') is True and
             drained.get('completed_delivery_pending')==0, 'host_observations_not_drained')
    state=_scope(client,binding)
    issues=[]
    # A zero hook flag alone can follow a native restoration failure. Require
    # the completed native SC_DETACH result too (16 = SC_DETACHED), including
    # for independent inspection after a request timeout.
    if (state.get('state')!=3 or state.get('operation')!=5 or state.get('result')!=16 or
            state.get('completed_serial')!=state.get('serial')):
        issues.append('native_detach_completion_not_confirmed')
    for key in ZERO_FIELDS:
        if state.get(key)!=0:issues.append('nonzero_'+key)
    if state.get('auto_panel_sequence')!=state.get('auto_panel_ack'):
        issues.append('automatic_panel_not_acked')
    checks=[]
    for row in original['code_matches']+original['minimap_ping_abi_checks']:
        expected=bytes.fromhex(row['expected_hex']);va=int(row['va'],16)
        good=client.reader.read(va,len(expected))==expected
        checks.append({'va':hex(va),'bytes':len(expected),'matched':good})
        if not good:issues.append('original_code_not_restored_'+hex(va))
    iat=struct.unpack('<I',client.reader.read(0x4e5190,4))[0]
    if iat!=original['IAT']:issues.append('iat_not_restored')
    residents=inventory_reader(client)
    issues.extend(_inventory_issues(residents,client,binding,original,all_inert=True))
    # Re-read the stopped ring directly; a saved collector receipt is not proof
    # that no event appeared between its last sample and capture-stop.
    from tools.stage4_event_collector import StreamReader
    native=StreamReader(client,binding['capture_generation']).header()
    if (native['write_seq']!=native['ack_seq'] or
            native['write_seq']!=quiescence['collector']['native_status']['write_seq'] or
            any(native[key] for key in ('flags','lost','invalid_events','last_error'))):
        issues.append('live_event_ring_not_drained_or_fault_free')
    if state.get('building_address'):
        h=struct.unpack('<8I',client.reader.read(state['building_address'],32))
        if (h[0]!=0x314d4253 or h[1]!=1 or h[2]!=binding['epoch'] or
                h[3]!=state.get('building_capacity') or h[6] or h[7] or
                h[5]!=h[4]+1 or state.get('building_ack')!=h[5]):
            issues.append('live_building_ring_not_drained_or_fault_free')
    after=_scope(client,binding)
    if any(after.get(key)!=state.get(key) for key in ZERO_FIELDS+('action_count','serial','completed_serial')):
        issues.append('native_state_changed_during_verification')
    return {'binding':deepcopy(binding),'fully_withdrawn':not issues,'issues':issues,
            'verification_read_only':True,'code_matches':checks,'IAT_restored':iat==original['IAT'],
            'after':after,'resident_candidates':residents,'live_event_status':native,
            'saved_game':False,'process_terminated':False,'human_accepted':False}


class NativeCleanupAttempt:
    """Retain one instance per binding; execute once, inspect unknown read-only.

    The host must retain/log this attempt across its worker messages. An unknown
    publication must never be retried by constructing a replacement attempt.
    """
    def __init__(self, *, binding, original):
        self.binding,self.original=deepcopy(binding),deepcopy(original)
        self.attempted=False

    def execute(self, client, *, quiescence, drain_after_stop,
                inventory_reader=read_current_inventory):
        report={'binding':deepcopy(self.binding),'fully_withdrawn':False,
                'status':'blocked','operations':[],'automatic_retry':False}
        if self.attempted:
            report['reason']='attempt_already_used_inspect_read_only';return report
        owns_mutex=False
        try:
            _require(callable(drain_after_stop) and callable(inventory_reader),'owned_cleanup_callbacks_required')
            validate_original_snapshot(self.binding,self.original)
            validate_quiescence(self.binding,quiescence)
            _scope(client,self.binding)
            wait=client.api.k.WaitForSingleObject(client.mutex,0)
            _require(wait in (0,0x80),'mailbox_mutex_busy_unpublished')
            owns_mutex=True
            # Windows mutex ownership is recursive on this host worker thread;
            # existing arm/request acquire and release their nested ownership.
            # Keep the outer lease through binding checks and final reads.
            before=_scope(client,self.binding)
            _require(before.get('hook_installed')==1,'detach_already_absent_inspect_read_only')
            _require(not _inventory_issues(inventory_reader(client),client,self.binding,self.original,all_inert=False),
                     'current_resident_inventory_or_prior_activity_changed')
            self.attempted=True
            client.arm(False)
            report['operations'].append({'operation':'disarm','confirmed':not _scope(client,self.binding)['enable_actions']})
            _require(report['operations'][-1]['confirmed'],'disarm_unconfirmed')
            for operation,fields in (('capture-stop',('events_enabled','event_hooks')),
                                     ('building-stop',('building_enabled',)),('detach',('hook_installed',))):
                current=_scope(client,self.binding)
                if operation=='detach':
                    drained=drain_after_stop(client,deepcopy(self.binding))
                    _same_binding(drained.get('binding',{}),self.binding)
                    _require(drained.get('building_worker_closed') is True and
                             drained.get('building_flushed') is True and
                             drained.get('completed_delivery_pending')==0,'host_observations_not_drained')
                    current=_scope(client,self.binding)
                if any(current.get(key) for key in fields):
                    reply=client.request(operation,epoch=self.binding['epoch'])
                    report['operations'].append({'operation':operation,'result':reply.get('result')})
                    _require(reply.get('result')==('detached' if operation=='detach' else 'ok'),
                             operation+'_unconfirmed')
            proof=verify_native_cleanup(client,binding=self.binding,original=self.original,
                quiescence=quiescence,drained=drained,inventory_reader=inventory_reader)
            if proof['after']['action_count']!=before['action_count']:
                proof['issues'].append('player_action_executed_during_cleanup');proof['fully_withdrawn']=False
            report.update(status='verified' if proof['fully_withdrawn'] else 'verification_failed',
                          fully_withdrawn=proof['fully_withdrawn'],verification=proof)
        except Exception as error:
            report.update(status='outcome_unknown' if isinstance(error,TimeoutError) else 'blocked',
                          reason=str(error),error_type=type(error).__name__)
        finally:
            if owns_mutex and not client.api.k.ReleaseMutex(client.mutex):
                report.update(status='blocked',fully_withdrawn=False,reason='mailbox_mutex_release_failed')
        return report
