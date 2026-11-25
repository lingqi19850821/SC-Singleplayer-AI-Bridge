"""Reusable public player dispatch. The launcher owns IO, capture and menu input.

No installation, native snapshots in public replies, default storage paths,
game knowledge imports, automatic retries or tactical decisions.
"""
from dataclasses import asdict, fields
from datetime import datetime, timezone
import json
import re
import secrets
import sqlite3
import time
import traceback

from .actions import SelectionTicket
from .stage5 import ButtonTicket, TargetTicket, ProductionTicket, CargoTicket
from .tips import TipsRuntime
from .experience import AUTHOR_ROLES, ExperienceStore
from .automatic_information import INFORMATION_OPS
from .wire_identity import normalize_identity


def _now():
    return datetime.now(timezone.utc).isoformat()


def ticket(value, kind):
    if not isinstance(value, dict):
        raise ValueError('ticket object required')
    value = normalize_identity(value)
    required = {field.name for field in fields(kind)}
    if set(value) != required or any(type(v) is not int or v < 0 for v in value.values()):
        raise ValueError('matching complete ticket required')
    return kind(**value)


def validate_session_reference(value, pid, creation, epoch):
    """Pure preflight: current identity only; native code checks generations."""
    value = normalize_identity(value)
    if (not isinstance(value, dict) or any(type(value.get(key)) is not int or value[key] < 0
            for key in ('pid','process_creation','epoch'))):
        raise ValueError('complete reference session required')
    if value['pid'] != pid or value['process_creation'] != creation:
        return {'status':'rejected', 'reason':'different_process'}
    if value['epoch'] != epoch:
        return {'status':'rejected', 'reason':'stale_epoch'}
    return None


def project_view(result, requested):
    if requested is None:
        return result
    if (not isinstance(requested, list) or not requested or
            any(not isinstance(x, str) for x in requested) or
            len(set(requested)) != len(requested) or
            set(requested) - {'units', 'terrain', 'terrain_metadata'} or
            {'terrain', 'terrain_metadata'} <= set(requested)):
        raise ValueError('invalid explicit view projection')
    if result.get('status') != 'observed':
        return result
    answer = {k: v for k, v in result.items() if k not in ('units', 'terrain')}
    omitted = []
    if 'units' in requested:
        answer['units'] = result['units']
    else:
        omitted.append('units')
    if 'terrain' in requested:
        answer['terrain'] = result['terrain']
    elif 'terrain_metadata' in requested:
        answer['terrain_metadata'] = {k: v for k, v in result['terrain'].items() if k != 'cells'}
        answer['terrain_metadata']['total_cell_count'] = len(result['terrain'].get('cells', []))
        omitted.append('terrain.cells')
    else:
        omitted.append('terrain')
    answer['omitted_fields'] = omitted
    return answer


class ReadyRuntime:
    _SELECTION = {'selected_panel', 'move_selected', 'right_click', 'move_minimap',
        'attack_move', 'attack_move_minimap', 'patrol', 'patrol_minimap', 'assign_group',
        'focus_selected', 'halt_construction', 'resume_construction', 'cancel_construction',
        'cancel_research', 'cancel_upgrade', 'cancel_morph', 'cancel_addon'}
    _DIRECT = {'resource_sites', 'build_sites', 'own_units', 'terrain_view', 'current_selection', 'ui_counters', 'select_at', 'select_box',
        'select_same_type', 'select_group', 'camera', 'resources', 'remembered_buildings',
        'save_view', 'list_views', 'get_view', 'recall_view'}
    _NOARGS = {'state':'control_state', 'session':'session', 'minimap_geometry':'minimap_geometry',
        'cancel_pending':'cancel_pending', 'building_memory_status':'building_memory_status'}
    _MENU = {'save_game':'save', 'load_game':'load', 'restart_mission':'restart', 'objectives':'objectives'}
    _KEEP_BUTTONS = {'resource_sites', 'build_sites', 'selected_panel', 'own_units', 'terrain_view', 'current_selection',
        'resources', 'ui_counters', 'remembered_buildings', 'save_view', 'list_views', 'get_view',
        'state', 'session', 'minimap_geometry', 'building_memory_status', 'assign_group'}
    _INTENT = (_SELECTION - {'selected_panel'}) | set(_MENU) | {
        'finish', 'select_at', 'select_box', 'select_same_type', 'select_group',
        'camera', 'save_view', 'recall_view', 'activate_button', 'target_at',
        'cancel_production', 'unload_one', 'cancel_pending',
        'experience_record', 'experience_revise', 'experience_refute'}

    def __init__(self, api, events, log, *, profile_id, lifecycle, run_ref, capabilities=(),
                 tutorial_depot_limit=None, tutorial_new_depot_intents=None, diagnostics='full',
                 role_experience_paths=None, shared_experience_paths=None,
                 shared_experience_profile='official_standard'):
        if diagnostics not in ('full', 'minimal'):
            raise ValueError('diagnostics must be full or minimal')
        self.diagnostics = diagnostics
        if not isinstance(profile_id, str) or not profile_id.strip():
            raise ValueError('explicit profile_id required')
        if not isinstance(run_ref, str) or not run_ref.strip():
            raise ValueError('explicit run_ref required')
        if not isinstance(capabilities, (tuple, list, set, frozenset)) or any(
                not isinstance(c, str) for c in capabilities):
            raise ValueError('actual capability names required')
        if lifecycle.profile != profile_id:
            raise ValueError('lifecycle and player profiles must match')
        if ((tutorial_depot_limit is not None or tutorial_new_depot_intents is not None) and
                (type(tutorial_depot_limit) is not int or tutorial_depot_limit < 1
                 or type(tutorial_new_depot_intents) is not int or tutorial_new_depot_intents < 0)):
            raise ValueError('trusted Tutorial Depot limits must be nonnegative integer budgets with positive total')
        self.profile_id, self.lifecycle, self.log = profile_id, lifecycle, log
        self._tutorial_depot_limit = tutorial_depot_limit
        self._tutorial_new_depot_intents = tutorial_new_depot_intents
        self.capabilities = tuple(capabilities)
        self.api = self.events = self.tips = self.experience = None
        self.run_ref = run_ref
        self.buttons = {}
        self.finished = self.failed = False
        self.full_public_response = None
        self.startup_complete = False
        self._startup_offered = set()
        self._store_id = None
        self._delivery_sequence = 0
        self.shared_experience_profile = shared_experience_profile
        self.role_experiences = {}
        self._closed_role_experiences = set()
        try:
            self._init_role_experiences(role_experience_paths, shared_experience_paths)
            self._attach(api, events, run_ref, initial=True)
        except BaseException:
            self._close_role_experiences()
            raise

    def _init_role_experiences(self, role_experience_paths, shared_experience_paths):
        """Each role owns independent episode and persistent SQLite notebooks."""
        if role_experience_paths is None:
            return
        if not isinstance(role_experience_paths, dict):
            raise ValueError('role experience paths must be a role-keyed mapping')
        shared_paths = {} if shared_experience_paths is None else shared_experience_paths
        if not isinstance(shared_paths, dict):
            raise ValueError('shared experience paths must be a role-keyed mapping')
        for role, episode_path in role_experience_paths.items():
            if not isinstance(role, str) or role not in AUTHOR_ROLES:
                raise ValueError('invalid_experience_author_role')
            self.role_experiences[role] = ExperienceStore(
                episode_path, profile_id=self.profile_id,
                shared_store=shared_paths.get(role),
                shared_profile_id=self.shared_experience_profile)

    def _close_role_experiences(self):
        failures = []
        experiences = getattr(self, 'role_experiences', {}) or {}
        closed = getattr(self, '_closed_role_experiences', None)
        if closed is None:
            closed = self._closed_role_experiences = set()
        for role, store in list(experiences.items()):
            if role in closed:
                continue
            try:
                for db in (getattr(store, 'db', None),
                           getattr(store, 'shared_db', None)):
                    if not isinstance(db, sqlite3.Connection):
                        continue
                    try:
                        if db.in_transaction:
                            db.commit()
                    except sqlite3.ProgrammingError:
                        pass
                    except BaseException:
                        failures.append(traceback.format_exc())
                    try:
                        db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
                    except (sqlite3.ProgrammingError, sqlite3.OperationalError):
                        pass
                    except BaseException:
                        failures.append(traceback.format_exc())
            finally:
                try:
                    store.close()
                except BaseException:
                    failures.append(traceback.format_exc())
                else:
                    closed.add(role)
        return failures

    def _persistent_databases(self):
        """Every owned/borrowed SQLite handle this runtime must flush on exit."""
        values = []
        api = getattr(self, 'api', None)
        if api is not None:
            knowledge = getattr(api, '_knowledge', None)
            if knowledge is not None:
                values.append(getattr(knowledge, 'db', None))
            bookmarks = getattr(api, '_bookmarks', None)
            if bookmarks is not None:
                values.append(getattr(bookmarks, 'db', None))
        experience = getattr(self, 'experience', None)
        if experience is not None:
            values.append(getattr(experience, 'db', None))
        closed = getattr(self, '_closed_role_experiences', set())
        for role, store in (getattr(self, 'role_experiences', {}) or {}).items():
            if role in closed:
                continue
            values.append(getattr(store, 'db', None))
            values.append(getattr(store, 'shared_db', None))
        answer = []
        seen = set()
        for db in values:
            if isinstance(db, sqlite3.Connection) and id(db) not in seen:
                seen.add(id(db))
                answer.append(db)
        return answer

    def _commit_persistence(self):
        """Commit and checkpoint before any handle is closed.

        Closing a SQLite connection with an open transaction rolls it back; a
        clean worker exit must flush WAL state so the parent can snapshot it.
        """
        failures = []
        for db in self._persistent_databases():
            try:
                if db.in_transaction:
                    db.commit()
            except sqlite3.ProgrammingError:
                continue
            except BaseException:
                failures.append(traceback.format_exc())
                continue
            try:
                db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            except (sqlite3.ProgrammingError, sqlite3.OperationalError):
                pass
            except BaseException:
                failures.append(traceback.format_exc())
        return failures

    def _close_api_persistence_handles(self, api):
        """Best-effort second close of every SQLite-backed API handle."""
        failures = []
        bookmarks = getattr(api, '_bookmarks', None)
        if bookmarks is not None:
            try:
                close = getattr(bookmarks, 'close', None)
                if callable(close):
                    close()
            except BaseException:
                failures.append(traceback.format_exc())
        building = getattr(api, '_building_memory', None)
        if building is not None:
            try:
                source = getattr(api, '_building_source', None)
                stop = getattr(source, 'stop', None)
                if callable(stop):
                    stop()
                close = getattr(building, 'close', None)
                if callable(close):
                    close()
            except BaseException:
                failures.append(traceback.format_exc())
        knowledge = getattr(api, '_knowledge', None)
        if knowledge is not None:
            try:
                close = getattr(knowledge, 'close', None)
                if callable(close):
                    close()
                else:
                    db = getattr(knowledge, 'db', None)
                    if isinstance(db, sqlite3.Connection):
                        db.close()
            except BaseException:
                failures.append(traceback.format_exc())
        return failures

    def experience_for(self, role):
        """Role notebook when configured; the legacy shared store otherwise."""
        experiences = getattr(self, 'role_experiences', {}) or {}
        return (experiences[role] if role in experiences
                else getattr(self, 'experience', None))

    @property
    def shared_experience_active(self):
        experiences = getattr(self, 'role_experiences', {}) or {}
        return any(getattr(store, 'shared_db', None) is not None
                   for store in experiences.values())

    def _attach(self, api, events, run_ref, *, initial):
        if api is None or events is None:
            raise ValueError('current public API and capture context required')
        if not isinstance(run_ref, str) or not run_ref.strip():
            raise ValueError('explicit run_ref required')
        db = api._knowledge.db
        db.execute('''CREATE TABLE IF NOT EXISTS ready_runtime_profile_v1(
            profile TEXT PRIMARY KEY,store_identity TEXT NOT NULL)''')
        row = db.execute('SELECT store_identity FROM ready_runtime_profile_v1 WHERE profile=?',
                         (self.profile_id,)).fetchone()
        if initial and row is None:
            db.execute('INSERT INTO ready_runtime_profile_v1 VALUES(?,?)',
                       (self.profile_id, secrets.token_hex(16)))
            row = db.execute('SELECT store_identity FROM ready_runtime_profile_v1 WHERE profile=?',
                             (self.profile_id,)).fetchone()
        if row is None or (not initial and row[0] != self._store_id):
            raise ValueError('attach requires the same legitimate player store')
        db.execute('''CREATE TABLE IF NOT EXISTS ready_depot_intent_v1(
            profile TEXT PRIMARY KEY,reserved_at_utc TEXT NOT NULL,
            run_ref TEXT NOT NULL,button TEXT NOT NULL)''')
        db.execute('''CREATE TABLE IF NOT EXISTS ready_depot_intent_v2(
            seq INTEGER PRIMARY KEY,profile TEXT NOT NULL,reserved_at_utc TEXT NOT NULL,
            run_ref TEXT NOT NULL,button TEXT NOT NULL)''')
        db.execute('CREATE INDEX IF NOT EXISTS ready_depot_intent_profile_v2 ON ready_depot_intent_v2(profile)')
        db.commit()
        self._store_id = row[0]
        self.api, self.events, self.run_ref = api, events, run_ref
        binder=getattr(events,'bind_weapon_knowledge',None)
        if binder is not None:binder(api._knowledge)
        self.tips = TipsRuntime(api._knowledge, profile_id=self.profile_id)
        self.experience = ExperienceStore(api._knowledge, profile_id=self.profile_id)
        self.buttons.clear()
        self._startup_offered.clear()
        self._refresh_startup()

    def attach(self, api, events, run_ref):
        """Trusted launcher after verified lifecycle bind and capture handshake."""
        if self.api is not None or self.events is not None:
            raise ValueError('suspend the previous API before attach')
        if not self.lifecycle.status()['gameplay_allowed']:
            raise ValueError('host must verify and bind the lifecycle session first')
        self._attach(api, events, run_ref, initial=False)

    def _refresh_startup(self):
        page = self.tips.pending(limit=1, text_budget_bytes=2,
            capabilities=self.capabilities, startup_only=True)
        self.startup_complete = page['status'] == 'ok' and not page['items'] and not page['has_more']

    def startup_page(self, *, limit, text_budget_bytes):
        if self.api is None:
            return {'status':'unavailable', 'reason':'runtime_suspended', 'automatic_retry':False}
        page = self.tips.pending(limit=limit, text_budget_bytes=text_budget_bytes,
                                 capabilities=self.capabilities, startup_only=True)
        if page['status'] == 'ok':
            self._startup_offered.update(item['id'] for item in page['items'])
        return page

    def delivered_startup(self, ids):
        """Trusted launcher calls only after the complete startup page IO flush."""
        if self.api is None or not isinstance(ids, list) or any(
                not isinstance(value, str) or value not in self._startup_offered for value in ids):
            raise ValueError('acknowledge only this runtime startup offers after delivery')
        self.tips.acknowledge(ids)
        self._startup_offered.difference_update(ids)
        self._refresh_startup()
        return self.startup_complete

    def pending_tips(self, *, limit, text_budget_bytes):
        if not self._ready():
            return self._blocked()
        return self.tips.pending(limit=limit, text_budget_bytes=text_budget_bytes,
                                 capabilities=self.capabilities)

    def _ready(self):
        return (not self.failed and not self.finished and self.api is not None
                and self.startup_complete and self.lifecycle.status()['gameplay_allowed'])

    def _blocked(self):
        reason = ('runtime_stopped' if self.failed or self.finished else
                  'runtime_suspended' if self.api is None else
                  'startup_tips_not_delivered' if not self.startup_complete else 'menu_or_session_pending')
        return {'status':'unavailable', 'reason':reason, 'automatic_retry':False}

    def snapshot(self):
        """Private evidence only; never include in a returned public result."""
        return None if self.api is None else self.api._client.peek()

    def record(self, value):
        self.log.write(json.dumps({'at_utc':_now(), **value}, ensure_ascii=False) + '\n')
        self.log.flush()

    def suspend(self):
        """Disarm, close event reader and Stage5 workers; parent stops collector."""
        api, events = self.api, self.events
        failures = self._commit_persistence()
        self.api = self.events = self.tips = self.experience = None
        self.buttons.clear()
        if api is not None:
            try:
                api._client.arm(False)
            except BaseException:
                failures.append(traceback.format_exc())
        if events is not None:
            try:
                events.close()
            except BaseException:
                failures.append(traceback.format_exc())
        if api is not None:
            try:
                api.close()
            except BaseException:
                failures.append(traceback.format_exc())
        self.record({'runtime_suspended':not failures, 'private_close_errors':failures})
        if failures:
            self.failed = True
            raise RuntimeError('runtime quiescence failed; consult private log')

    def close(self):
        """Commit/close owned persistence in addition to normal runtime cleanup."""
        api = getattr(self, 'api', None)
        failures = self._commit_persistence()
        failures.extend(self._close_role_experiences())
        if api is not None or getattr(self, 'events', None) is not None:
            try:
                self.suspend()
            except BaseException:
                failures.append(traceback.format_exc())
        elif getattr(self, 'experience', None) is not None:
            self.experience = None
        if api is not None:
            # Stage5.close normally closes these. Retain an explicit best-effort
            # close so a partially failed suspend cannot leak panel/building/
            # bookmark SQLite handles past worker exit.
            failures.extend(self._close_api_persistence_handles(api))
        if failures:
            self.failed = True
            raise RuntimeError('runtime close failed; consult private log')

    @staticmethod
    def _noargs(args):
        if args:
            raise ValueError('operation takes no arguments')

    def _reserve_depot(self, row):
        # Trial limits are an explicit trusted task constraint, never a general
        # StarCraft rule. Existing trial configurations continue to opt in.
        if self._tutorial_depot_limit is None:
            return None
        if not re.search(r'\bsupply\s*depot\b', row.get('name', ''), re.IGNORECASE):
            return None
        own = self.api.own_units(unit_budget=1700, text_budget_bytes=65536, encoding='cp1252')
        if own.get('status') != 'observed' or not isinstance(own.get('units'), list):
            return {'status':'unavailable', 'reason':'depot_limit_count_not_current', 'automatic_retry':False}
        count = sum(bool(re.search(r'\bsupply\s*depot\b', unit.get('name', ''), re.IGNORECASE))
                    for unit in own['units'])
        if count >= self._tutorial_depot_limit:
            return {'status':'rejected', 'reason':'human_depot_limit'}
        db = self.api._knowledge.db
        with db:
            db.execute('BEGIN IMMEDIATE')
            # Preserve the old single-intent record as part of the same lifetime
            # budget; a continuation never resets history after API close/load.
            reserved = sum(db.execute('SELECT count(*) FROM ' + table + ' WHERE profile=?',
                (self.profile_id,)).fetchone()[0] for table in
                ('ready_depot_intent_v1','ready_depot_intent_v2'))
            if reserved >= self._tutorial_new_depot_intents:
                return {'status':'rejected', 'reason':'human_depot_limit'}
            db.execute('''INSERT INTO ready_depot_intent_v2
                (profile,reserved_at_utc,run_ref,button) VALUES(?,?,?,?)''',
                (self.profile_id, _now(), self.run_ref, json.dumps(row['button'], sort_keys=True)))
        # Transaction committed BEFORE issuing, even if the command later fails.
        self.record({'supply_depot_guard':'reserved_one_new_intent', 'run_ref':self.run_ref})
        return None

    def dispatch(self, op, args):
        if {'tutorial_depot_limit','tutorial_new_depot_intents'} & set(args):
            raise ValueError('Tutorial budgets are host configuration only')
        if op == 'finish':
            self._noargs(args)
            self.finished = True
            return {'status':'finishing'}
        if op == 'lifecycle_status':
            self._noargs(args)
            return self.lifecycle.status()
        if op == 'menu_status':
            return self.lifecycle.operation_status(**args)
        if not self._ready():
            return self._blocked()
        if op == 'list_saves':
            return self.lifecycle.list_saves(**args)
        if op in self._MENU:
            if self._MENU[op] not in self.capabilities:
                return {'status':'unavailable', 'reason':'lifecycle_capability_unavailable', 'automatic_retry':False}
            return self.lifecycle.request(self._MENU[op], **args)
        if op == 'tips_pending':
            return self.pending_tips(**args)
        if op.startswith('experience_') and op[11:] in ('record','read','revise','refute'):
            if 'author_role' in args:
                raise ValueError('author_role is host-only')
            method = getattr(self.experience, op[11:])
            if op[11:] in ('revise','refute'):
                return method(args.pop('entry_id'), **args)
            return method(**args)
        api = self.api
        for key in ('ticket','button','target','production','passenger'):
            if key in args:
                rejected = validate_session_reference(args[key], api._pid, api._creation, api._epoch)
                if rejected is not None:
                    return rejected
        if op == 'observe_view':
            requested = args.pop('fields', None)
            project_view({'status':'observed', 'units':[], 'terrain':{}}, requested)
            self.full_public_response = api.observe_view(**args)
            return project_view(self.full_public_response, requested)
        if op == 'command_card':
            self.buttons.clear()
            result = api.command_card(ticket(args.pop('ticket'), SelectionTicket), **args)
            if result.get('status') == 'observed':
                self.buttons = {tuple(sorted(row['button'].items())):row
                                for row in result['buttons'] if row['enabled']}
            return result
        if op == 'activate_button':
            button = ticket(args.pop('button'), ButtonTicket)
            row = self.buttons.get(tuple(sorted(asdict(button).items())))
            if row is None:
                return {'status':'rejected', 'reason':'read_current_enabled_command_card'}
            denied = self._reserve_depot(row)
            self.buttons.clear()
            if denied is not None:
                return denied
            return api.activate_button(button, **args)
        if op in self._SELECTION:
            if op not in self._KEEP_BUTTONS:
                self.buttons.clear()
            return getattr(api, op)(ticket(args.pop('ticket'), SelectionTicket), **args)
        if op in ('target_at','placement_preview','cancel_production','unload_one'):
            key, kind = {'target_at':('target',TargetTicket), 'placement_preview':('ticket',TargetTicket),
                         'cancel_production':('production',ProductionTicket), 'unload_one':('passenger',CargoTicket)}[op]
            if op != 'placement_preview':
                self.buttons.clear()
            return getattr(api, op)(ticket(args.pop(key), kind), **args)
        if op in self._DIRECT:
            if op not in self._KEEP_BUTTONS:
                self.buttons.clear()
            return getattr(api, op)(**args)
        if op in self._NOARGS:
            self._noargs(args)
            if op not in self._KEEP_BUTTONS:
                self.buttons.clear()
            return getattr(api, self._NOARGS[op])()
        if op == 'events':
            return self.events.read(**args)
        if op == 'events_head':
            self._noargs(args)
            return self.events.head()
        return {'status':'rejected', 'reason':'unsupported_public_operation'}

    def handle(self, request):
        op = request.get('op') if isinstance(request, dict) else None
        if isinstance(op, str) and op in INFORMATION_OPS:
            # This is the model-facing boundary. The trusted automatic worker
            # uses dispatch() internally and owns all read budgets/cursors.
            result = {'status':'rejected', 'reason':'information_is_automatic', 'automatic_retry':False}
            try:
                self.record({'phase':'error', 'op':op, 'reason':result['reason']})
            except BaseException:
                self.failed = True
                return {'status':'outcome_unknown', 'reason':'runtime_evidence_unavailable', 'automatic_retry':False}
            return result
        if self.diagnostics == 'minimal':
            return self._handle_minimal(request)
        before = result = error = None
        self.full_public_response = None
        try:
            if (not isinstance(request, dict) or set(request) - {'op','args','id','purpose'} or
                    not isinstance(request.get('op'), str) or not isinstance(request.get('args', {}), dict) or
                    ('purpose' in request and (not isinstance(request['purpose'], str) or not request['purpose'].strip()))):
                raise ValueError('request requires op, object args and optional nonempty purpose')
            # No API reads at all while suspended or a menu permit is pending.
            if self.api is not None and self.lifecycle.status()['gameplay_allowed']:
                before = self.snapshot()
            result = self.dispatch(request['op'], dict(request.get('args', {})))
        except (TypeError, ValueError, KeyError):
            error = traceback.format_exc()
            result = {'status':'rejected', 'reason':'invalid_public_arguments', 'automatic_retry':False}
        except BaseException:
            error = traceback.format_exc()
            self.failed = True
            result = {'status':'outcome_unknown', 'reason':'public_operation_failed', 'automatic_retry':False}
        try:
            after = (self.snapshot() if self.api is not None
                     and self.lifecycle.status()['gameplay_allowed'] else None)
            self.record({'request':request, 'developer_before':before, 'developer_after':after,
                         'public_response':result, 'public_full_response':self.full_public_response or result,
                         'private_error':error})
        except BaseException:
            self.failed = True
            return {'status':'outcome_unknown', 'reason':'runtime_evidence_unavailable', 'automatic_retry':False}
        return result

    def _handle_minimal(self, request):
        """Critical intent and failures only; no diagnostic native snapshots."""
        started = time.perf_counter()
        self.full_public_response = None
        op = request.get('op') if isinstance(request, dict) else None
        ident = request.get('id') if isinstance(request, dict) else None
        intent = False
        begun = False
        error = None
        try:
            if (not isinstance(request, dict) or set(request) - {'op','args','id','purpose'} or
                    not isinstance(op, str) or not isinstance(request.get('args', {}), dict) or
                    ('purpose' in request and (not isinstance(request['purpose'], str) or not request['purpose'].strip()))):
                raise ValueError('request requires op, object args and optional nonempty purpose')
            args = request.get('args', {})
            intent = op in self._INTENT or (op == 'selected_panel' and bool(args.get('learn_details')))
            if intent:
                purpose = request.get('purpose', op)
                try:
                    self.record({'phase':'begin', 'op':op, 'args':args, 'purpose':purpose,
                                 'purpose_source':'agent' if 'purpose' in request else 'operation', 'id':ident})
                    begun = True
                except BaseException:
                    self.failed = True
                    return {'status':'outcome_unknown', 'reason':'runtime_evidence_unavailable', 'automatic_retry':False}
            result = self.dispatch(op, dict(args))
        except (TypeError, ValueError, KeyError):
            error = traceback.format_exc()
            result = {'status':'rejected', 'reason':'invalid_public_arguments', 'automatic_retry':False}
        except BaseException:
            error = traceback.format_exc()
            self.failed = True
            result = {'status':'outcome_unknown', 'reason':'public_operation_failed', 'automatic_retry':False}
        finally:
            self.full_public_response = None
        if intent or error is not None or result.get('status') in ('rejected','unavailable','outcome_unknown'):
            entry = {'phase':'end' if begun else 'error', 'op':op, 'id':ident,
                     'status':result.get('status'), 'reason':result.get('reason'),
                     'elapsed_ms':round((time.perf_counter() - started) * 1000, 3)}
            if error is not None:
                entry['private_error'] = error
            try:
                self.record(entry)
            except BaseException:
                self.failed = True
                return {'status':'outcome_unknown', 'reason':'runtime_evidence_unavailable', 'automatic_retry':False}
        return result

    def after_public_delivery(self, result, op):
        """Host only AFTER emit+flush of this final projected public response."""
        if self.api is None or not isinstance(result, dict):
            return
        status = result.get('status')
        if status is not None and status not in ('observed','ok','selected','applied','enqueued','target_required'):
            return
        self._delivery_sequence += 1
        source = 'public:%s:%s:%d' % (self.run_ref, op, self._delivery_sequence)
        names, button_names = [], []
        if op == 'selected_panel' and isinstance(result.get('panel'), dict):
            names.append(result['panel'].get('name'))
        field = {'observe_view':'units', 'own_units':'units', 'current_selection':'members'}.get(op)
        if field and isinstance(result.get(field), (list,dict)):
            rows=result[field].values() if isinstance(result[field],dict) else result[field]
            names += [row.get('name') for row in rows if isinstance(row, dict)]
        if op == 'command_card' and isinstance(result.get('buttons'), list):
            button_names = [row.get('name') for row in result['buttons']
                            if isinstance(row, dict) and row.get('enabled') is True]
        if op == 'events' and isinstance(result.get('events'), list):
            for row in result['events']:
                if not isinstance(row, dict):
                    continue
                key = {'production_completed':'unit_name', 'building_completed':'building_name'}.get(row.get('kind'))
                if key:
                    names.append(row.get(key))
        names = [name for name in names if isinstance(name, str) and name.strip()]
        button_names = [name for name in button_names if isinstance(name, str) and name.strip()]
        if names:
            self.tips.observe_public_names(names, source_ref=source)
        if button_names:
            self.tips.observe_button_names(button_names, source_ref=source)
        if op == 'tips_pending' and status == 'ok':
            self.tips.acknowledge([item['id'] for item in result['items']])
