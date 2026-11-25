# Python interface reference

Source-derived signatures. Inherited methods are listed under their base class.

Internal native transport and trusted-host helpers are separate from lawful player-facing observations.

## scapi.__init__

[Source](../scapi/__init__.py)

```python
__all__ = ['Stage5', 'Actions', 'SelectionTicket', 'ButtonTicket', 'TargetTicket', 'ProductionTicket', 'CargoTicket']
```

## scapi.actions

[Source](../scapi/actions.py)

### SelectionTicket

```python
pid: int
process_creation: int
epoch: int
generation: int
count: int
```

```python
action_result(reply)
```

Explicit allowlist; never forward a developer response wholesale.

### Actions

```python
__init__(self, pid)
```

```python
close(self)
```

```python
session(self)
```

```python
camera(self, x, y)
```

```python
select_at(self, x, y, *, add=False)
```

Shift-add/toggle when add=True; otherwise ordinary point selection.

```python
select_box(self, x1, y1, x2, y2)
```

```python
select_same_type(self, x, y)
```

Native screen same-type semantics, at most twelve actual units.

```python
current_selection(self)
```

```python
move_selected(self, ticket, x, y)
```

## scapi.automatic_information

[Source](../scapi/automatic_information.py)

```python
public_page(value)
```

```python
text_read(method, **args)
```

Complete a host-internal text read; never truncate or publish budgets.

### DeliveryOutbox

```python
__init__(self, db, profile)
```

```python
cursor(self, source)
```

```python
advance(self, values)
```

Empty source pages carry no undelivered public data.

```python
enqueue(self, message, effects=None, advances=None, handoff_receipts=None)
```

```python
pending(self)
```

```python
has_pending(self)
```

```python
has_pending_non_learning(self)
```

True when a non-learning batch (startup/tips/world scan) awaits ack.

```python
high_water(self)
```

```python
public_high_water(self)
```

Only model deliveries can participate in a model receipt barrier.

```python
pending_tips(self, phase=None)
```

```python
receive(self, identifier, omitted_records=None)
```

```python
processed(self, identifier)
```

### AutomaticInformation

```python
__init__(self, runtime, outbox, publish)
```

```python
configure(self, snapshot, *, calm_interval_ms=None, combat_hold_seconds=10.0, screen_terrain_same_origin_interval_s=None, screen_terrain_diff_cells_threshold=None)
```

```python
mark_action_completed(self)
```

Force fresh continuous state on the next scan after an action/wait.

```python
remember_authoring(self, result)
```

```python
remember_experience(self, result, role=None)
```

Queue one authored entry page for the role that actually wrote it.

```python
remember_target(self, result)
```

```python
preview_command(self, request)
```

```python
flush_learning(self)
```

Before a command: persist completion, without a second full scan.

```python
flush_reference_notices(self, publish=None)
```

Recover committed knowledge even when its source is not observed again.

```python
tick(self, *, force=False)
```

Complete a scan for startup/offline callers; live workers use step.

```python
flush_partial(self, *, before_action=False)
```

Publish earlier slices before a foreground action changes tickets.

Keep the same list/dict objects: the suspended generator owns them too.
Source cursors only advance with this durable information handoff.

```python
scan_pending(self)
```

```python
step(self)
```

Advance one logical read on the owning worker, never a second thread.

```python
pump_events_only(self)
```

Drain new journal events without any gameplay observation or action.

Used only by a held session that must keep cutscene/transmission text
flowing. It shares the normal scan's durable event cursor, so a later
full scan never emits the same event twice. View, terrain, selection,
resources and every native action remain untouched.

```python
action_feedback(self, request, result, *, enrich_selection=False)
```

Fresh next-action tickets, plus explicitly dated background state.

No terrain or static unit properties are expanded. Each read has its
own observation time: this is deliberately not an atomic world view.

## scapi.automatic_panel

[Source](../scapi/automatic_panel.py)

```python
decode_completed(raw, *, encoding)
```

Parse an immutable native completion; no current-unit/table rereads.

```python
drain_completed(api, *, encoding)
```

Commit before ack. Retain a public result until the host takes delivery.

## scapi.build_sites

[Source](../scapi/build_sites.py)

```python
decode_sites(raw, *, encoding, text_budget_bytes, token)
```

```python
build_sites(api, *, text_budget_bytes, encoding)
```

## scapi.building_memory

[Source](../scapi/building_memory.py)

### BuildingMemory

```python
__init__(self, source, path, session_key, *, poll_interval=0.05)
```

```python
status(self)
```

```python
query(self, *, limit, text_budget_bytes, cursor=None, name_for_kind=None)
```

Bound the complete UTF-8 JSON response; never truncate a row or name.

Names are resolved by the caller thread through its existing legitimate
knowledge source. Pagination is stable insertion order, with latest facts
at query time; appends and reobservations do not invalidate a cursor.

```python
query_changes(self, *, after_sequence, limit, text_budget_bytes, name_for_kind=None)
```

Host-only incremental current facts; no event log or native reads.

A checkpoint means included current rows, not deletion or survival.
Updates can repeat a token across pages; consumers upsert by token.
Failed reads/budgets leave the checkpoint unchanged. Original public
offset cursors and full historical query remain independent.

```python
query_kind(self, *, kind, after_sequence=0, limit, text_budget_bytes, name_for_kind=None)
```

Host-only selective name enrichment, ordered by first observation.

Here checkpoint is the stable first_sequence, not the change sequence.
A newly learned kind name can enrich its old hidden records without
scanning unrelated kinds or retaining historical pages in the host.

```python
flush(self, timeout=2.0)
```

Wait for a fresh source poll and durable ack; return explicit status.

```python
close(self, timeout=2.0)
```

Bounded drain, then stop; failures remain visible in the return value.

Source ownership and close/release belong to the adapter, never this store.
A source must provide bounded read_batch/ack calls for bounded shutdown.

## scapi.building_source

[Source](../scapi/building_source.py)

### BuildingSource

```python
__init__(self, client, *, epoch=None, capacity=3400, refresh_ms=1000, batch_limit=256)
```

```python
read_batch(self)
```

```python
ack(self, next_sequence)
```

```python
stop(self)
```

Stop only this observation producer, leaving the simulation running.

```python
close(self)
```

## scapi.cursor_recovery

[Source](../scapi/cursor_recovery.py)

```python
identity(value)
```

### Windows

```python
__init__(self)
```

```python
checked(value)
```

```python
open_process(self, pid)
```

```python
metadata(self, handle, pid)
```

```python
mutex(self, name)
```

```python
event(self, name)
```

```python
wait(self, handle, timeout)
```

```python
release(self, handle)
```

```python
close(self, handle)
```

```python
reset(self)
```

```python
watch_game_exit(expected, *, platform, ready, record)
```

No memory access, retries, process discovery, or reset on API shutdown.

```python
arm_game_exit_cursor_reset(expected)
```

Called after an API client has verified its exact game connection.

```python
main()
```

## scapi.events

[Source](../scapi/events.py)

### EventLog

```python
__init__(self, path, run_id, *, encoding, weapon_knowledge=None)
```

```python
bind_weapon_knowledge(self, knowledge)
```

Use the player's existing knowledge scope for event/panel joins.

```python
close(self)
```

```python
head(self)
```

```python
read(self, *, limit, cursor=None)
```

## scapi.experience

[Source](../scapi/experience.py)

### ExperienceStore

```python
__init__(self, store, *, profile_id, shared_store=None, shared_profile_id=None)
```

```python
close(self)
```

```python
read_latest_owned_payload(self, entry_id, *, author_role)
```

Latest validated payload for entry_id's chain, only for its owner.

Returns None when the entry is missing, owned by a different role, or
the stored payload no longer passes byte/field integrity checks.

```python
record(self, *, kind, content, applicability, confidence, provenance, run_ref, tags, text_budget_bytes, author_role=None, scope='episode')
```

```python
revise(self, entry_id, *, kind, content, applicability, confidence, provenance, run_ref, tags, text_budget_bytes, author_role=None, scope=None)
```

```python
refute(self, entry_id, *, content, applicability, confidence, provenance, run_ref, tags, text_budget_bytes, author_role=None, scope=None)
```

Append the counterexample and a refuted status; retain old evidence.

```python
delete(self, entry_id, *, author_role=None, scope=None)
```

Physically remove one root chain, including all tags and revisions.

```python
promote(self, entry_id, *, run_ref, author_role=None, kind=None, content=None, applicability=None, confidence=None, provenance=None, tags=None, text_budget_bytes=65536)
```

Move the latest episode revision into the persistent global notebook.

A durable promotion record (source chain root -> global entry) commits
atomically with the global note. If the episode-chain deletion then
fails, a retry finds that record, finishes the deletion, and returns the
already-promoted note without creating a duplicate.

```python
read(self, *, limit, text_budget_bytes, entry_id=None, keyword=None, tags=None, cursor=None, history=False, author_role=None, scope='all')
```

```python
list(self, *, author_role, limit=32, cursor=None, text_budget_bytes=65536, scope='all')
```

Host-owned GUI page of this role's latest claims, in the same SQLite.

### _RoleExperienceResult

```python
execute_role_experience(store, *, role, args, runtime_run_ref, offline=False)
```

One validated, role-owned execution path for host and offline CLI.

Authored notes remain player_authored_not_api_verified in the store. This
boundary adds ergonomic defaults and run_ref handling only; rejection
details are fixed strings so SQL, paths and tracebacks never escape.

## scapi.lifecycle

[Source](../scapi/lifecycle.py)

### Lifecycle

```python
__init__(self, path, *, profile_id, save_dir, wall=time.time, monotonic=time.monotonic)
```

```python
close(self)
```

```python
remaining_seconds(self)
```

```python
status(self)
```

```python
bind_session(self, session)
```

Trusted host only, after current process/epoch and collector handshake.

```python
request(self, kind, *, slot=None)
```

```python
host_begin(self, op_id, *, session, quiescent)
```

Host evidence is private; public dispatch must not expose this method.

```python
claim_menu_input(self, op_id)
```

Host calls once BEFORE every individual CU input. No earned bursts.

```python
host_finish(self, op_id, *, outcome, evidence)
```

```python
host_reconcile(self, op_id, *, outcome, evidence)
```

Resolve an uncertain result from NEW observations; never reissue input.

```python
operation_status(self, op_id, *, text_budget_bytes=None)
```

```python
list_saves(self, *, limit, text_budget_bytes, cursor=0)
```

## scapi.nuclear_observation

[Source](../scapi/nuclear_observation.py)

```python
nuclear_event(kind, *, color, x, y)
```

Return only the allowed public fields of one captured nuclear event.

Marker color encodes a capture-local public serial and an own/not-own bit;
it contains no raw actor identity, player index, pointer or native slot.
No-longer-current is not a claim of cancellation, impact or destruction.

## scapi.observation_budget

[Source](../scapi/observation_budget.py)

### ObservationDeadlineExceeded

### ObservationReadBudget

```python
__init__(self, seconds=0.334, *, clock=None)
```

```python
check(self)
```

```python
reader(self, reader)
```

### _BudgetedReader

```python
__init__(self, reader, budget)
```

```python
read(self, address, size)
```

```python
metadata(self)
```

## scapi.panel_card

[Source](../scapi/panel_card.py)

### TextBudgetRequired

```python
visible_ammunition(reader, state, *, _budget=None)
```

Read the count actually drawn in the current single-unit stat panel.

Target-1.05 renderer evidence: ammunition-panel-20261114/native-static.
Read the visible text rather than reconstructing technology/disable rules
or racing the unit's newer internal counter against the last UI redraw.
A missing icon is unknown, never an invented zero or a remembered count.

```python
selected_own_types(reader, state)
```

Private identity checks for the actual selected own group, even offscreen.

Reads no global unit list and grants no new learned values or skill names.

```python
visible_work_progress(reader, state)
```

Read the actual visible statdata progress control, never a hidden timer.

Native 4A2E30 writes the displayed WORD percentage to control id14 +24.
No remaining time, estimated duration or offscreen progress is inferred.

### PanelText

```python
__init__(self, reader, budget, encoding)
```

```python
string(self, address)
```

```python
read_own_card(reader, state, text)
```

## scapi.panel_knowledge

[Source](../scapi/panel_knowledge.py)

### PanelKnowledge

```python
__init__(self, scope, path=':memory:', *, shared_path=None, knowledge_mode='official_standard')
```

```python
close(self)
```

```python
reference_facts(self, token, source, *, owner_token=None)
```

Read current scope's stored static facts for one token/source pair.

```python
remember_public_reference(self, token, source, facts, *, name=None, owner_token=None, observation=None, notice_run_ref=None, merge_static=False)
```

Project only already lawful public facts, never import old scopes.

```python
pending_reference_notices(self, *, scope=None, run_ref=None, limit=64)
```

Read a page of unhanded notices, retaining their original provenance.

```python
ack_reference_notices(self, identities)
```

Only the durable host outbox may release this source queue.

```python
reference_read(self, *, token=None, cursor=None, limit=32)
```

```python
token(self, category, value)
```

```python
observe_grade(self, owner, group_id, grade)
```

```python
grade(self, owner, group_id)
```

```python
remember_name(self, kind, name)
```

```python
name(self, kind)
```

```python
mark_stats_known(self, kind, marked_at_utc=None)
```

Record completed numeric stats for any observed relation.

```python
mark_owned_known(self, kind, marked_at_utc=None)
```

Record a completed owned-card observation and its legacy full marker.

```python
mark_fully_known(self, kind, marked_at_utc=None)
```

Legacy callers stay consistent with the split durable markers.

```python
mark_detector(self, kind, *, commit=True)
```

Record a legally visible selected-panel ``Detector`` status label.

```python
detector(self, kind)
```

True only when observed; unknown stays None, never false.

```python
is_stats_known(self, kind)
```

Stats are complete for any relation; legacy full markers still count.

```python
is_owned_card_known(self, kind)
```

Owned-card truth; legacy rows require skills proof to avoid non-owned.

```python
has_historical_nonempty_build_options(self, kind)
```

```python
has_current_scope_build_options(self, kind)
```

```python
needs_learning(self, kind, *, owned, has_health=True, incomplete_building=False)
```

Single sparse truth for an unlearned public row; never preloads.

```python
kind_for_token(self, token)
```

Resolve one opaque type token by the private salted token space.

```python
client_learning_catalog(self)
```

Private host/GUI catalog; never part of a model-facing operation.

```python
client_learning_detail(self, token)
```

Private host/GUI detail; raw kind/profile/weapon bytes never leave.

```python
known_name(self, kind, current_label)
```

Compare a caller-verified current lawful label; never preload names.

```python
has_name_history(self, kind)
```

One indexed existence check, without loading other mission names.

```python
canonical(profile)
```

```python
bind_profiles(self, kind, owner, profiles)
```

```python
profiles(self, kind, owner)
```

```python
observe_armor_extra(self, kind, owner, extra)
```

```python
armor_extra(self, kind, owner)
```

```python
learn(self, kind, channel, value, profile=None)
```

```python
matches(self, kind, channel, value, profile=None)
```

```python
value(self, kind, channel, grade=None, profile=None)
```

```python
skills(self, kind, current=None, learn=False)
```

```python
learn_skill_names(self, kind, skills)
```

Learn visible group-card labels without an exact single-unit card signature.

A multi-selection exposes only the shared 3x3 card, never a single unit's
complete skill set. Recording those labels in ``learned_panel_skill_name_v1``
lets the current card show a lawfully observed label again, but deliberately
does not write ``learned_panel_skills_v3``: a group-only card must never
become an exact-signature match for an individual unit.

Like :meth:`skills`, this method leaves the commit to its caller. It returns
the ``(slot, name)`` pairs actually inserted, in input order, including a
pair newly inserted only into the attached shared store.

```python
known_skill_name(self, kinds, slot, name)
```

Reuse a legally learned current label; no group/offscreen learning.

```python
build_options(self, kind, current=None, learn=False)
```

Current mission permission wins; older learned lists remain historical.

```python
queue_observation(self, sequence, payload)
```

Inside the learning transaction, before releasing native storage.

```python
pending_observations(self)
```

One natural native completion per handoff; pending reports all work.

Unacknowledged rows survive worker restart and scope/epoch changes in
this same player profile. No learned history is bulk loaded or evicted.

```python
ack_observations(self, receipts)
```

```python
commit(self)
```

```python
observation_scope(self, scope)
```

Bind old completed evidence to its original run in this same store.

Rule-compatible learned values remain reusable. Owner grades, names,
tokens and mission build permissions cannot contaminate a newer run.

```python
transaction(self)
```

```python
learn_weapon(self, kind, channel, weapon, name, profile, evidence=None)
```

```python
has_weapon_candidates(self, kind, channel, profile)
```

```python
weapon_candidate(self, kind, channel, profile, mapping, label, prefix=b'', encoding=None)
```

One indexed lexical seek; history is retained without enumerating it.

```python
weapon(self, kind, channel, profile, reused=None)
```

```python
project(self, kind, owner, groups, profiles=None, reused_weapons=None)
```

Only last legally observed grades, never current enemy raw grades.

## scapi.panel_rules

[Source](../scapi/panel_rules.py)

### PanelRules

```python
__init__(self, reader, game_base)
```

```python
read(self, address, count)
```

```python
byte(self, address)
```

```python
word(self, address)
```

```python
shielded(self, kind)
```

```python
own_completed_grades(self)
```

```python
profiles(self, kind, ground_weapon, air_weapon, armor_extra)
```

## scapi.player_context

[Source](../scapi/player_context.py)

```python
startup_context(snapshot, runtime, lifecycle, *, configured_interval_ms)
```

Use only the launcher's existing verified health snapshot.

```python
validate_armed_settings(snapshot, settings)
```

## scapi.player_runtime

[Source](../scapi/player_runtime.py)

```python
ticket(value, kind)
```

```python
validate_session_reference(value, pid, creation, epoch)
```

Pure preflight: current identity only; native code checks generations.

```python
project_view(result, requested)
```

### ReadyRuntime

```python
__init__(self, api, events, log, *, profile_id, lifecycle, run_ref, capabilities=(), tutorial_depot_limit=None, tutorial_new_depot_intents=None, diagnostics='full', role_experience_paths=None, shared_experience_paths=None, shared_experience_profile='official_standard')
```

```python
experience_for(self, role)
```

Role notebook when configured; the legacy shared store otherwise.

```python
shared_experience_active(self)
```

```python
attach(self, api, events, run_ref)
```

Trusted launcher after verified lifecycle bind and capture handshake.

```python
startup_page(self, *, limit, text_budget_bytes)
```

```python
delivered_startup(self, ids)
```

Trusted launcher calls only after the complete startup page IO flush.

```python
pending_tips(self, *, limit, text_budget_bytes)
```

```python
snapshot(self)
```

Private evidence only; never include in a returned public result.

```python
record(self, value)
```

```python
suspend(self)
```

Disarm, close event reader and Stage5 workers; parent stops collector.

```python
close(self)
```

Commit/close owned persistence in addition to normal runtime cleanup.

```python
dispatch(self, op, args)
```

```python
handle(self, request)
```

```python
after_public_delivery(self, result, op)
```

Host only AFTER emit+flush of this final projected public response.

## scapi.resource_sites

[Source](../scapi/resource_sites.py)

```python
resource_sites(api, *, unit_budget, include_selected_amount=False)
```

## scapi.resources

[Source](../scapi/resources.py)

### _SnapshotRejected

```python
__init__(self, reason, status='rejected')
```

```python
resource_snapshot(client, expected_epoch)
```

Read current own totals without an action or a native request.

Minerals/gas are the authoritative current totals supplied to the HUD; its
on-screen number animation may momentarily lag. Supply is exactly the HUD's
visible integer rows (including its per-race cap and upward rounding), never
a cross-race sum. `available` and `over_capacity` are differences between
those displayed integers, not an additional fractional gameplay allowance.

Menus/inactive games, pause, detached bridges and non-human/invalid local
slots are unavailable. Epoch/process changes, in-flight requests, lifecycle
drift, frame changes or differing repeated totals return no partial data.
No automatic retry, pause, selection, camera, learning or action occurs.

## scapi.selection_values

[Source](../scapi/selection_values.py)

```python
selected_own_values(reader, game_base, pointer, kind, selected)
```

## scapi.stage4

[Source](../scapi/stage4.py)

```python
selected_panel_result(reply, knowledge=None, card=None, profiles=None, weapon_names=None, weapon_evidence=None, reused_weapons=None, commit=True, incomplete_building=False, status_text=None)
```

### Stage4

```python
control_state(self)
```

Current control ownership; subtitles alone never imply a lock.

```python
__init__(self, pid, *, knowledge_path=None, shared_knowledge_path=None, knowledge_mode='official_standard')
```

```python
close(self)
```

```python
observe_view(self, *, unit_budget, text_budget_bytes=65536, encoding='cp1252')
```

Current verified view items; explicit budget, no silent truncation.

```python
minimap_geometry(self)
```

```python
move_minimap(self, ticket, pixel_x, pixel_y)
```

One native Move for the real selected group; camera stays in place.

```python
selected_panel(self, ticket, *, learn_details=False, text_budget_bytes=None, encoding=None)
```

## scapi.stage5

[Source](../scapi/stage5.py)

### ButtonTicket

```python
pid: int
process_creation: int
epoch: int
generation: int
count: int
card_generation: int
slot: int
```

### TargetTicket

```python
pid: int
process_creation: int
epoch: int
generation: int
count: int
target_generation: int
queued_order: int = 0
```

### ProductionTicket

```python
pid: int
process_creation: int
epoch: int
generation: int
count: int
queue_generation: int
```

### CargoTicket

```python
pid: int
process_creation: int
epoch: int
generation: int
count: int
cargo_generation: int
slot: int
```

### Stage5

```python
drain_observations(self, *, encoding='cp1252')
```

Host-only serial drain; completed legal learning commits before ack.

Called by the unique host worker periodically and before game actions.
No Agent query, thread, selection or menu action is required. Build
options are mission-permitted loaded submenu entries; current enabled
state and operation references remain properties of the current card.

```python
ack_observations(self, receipts)
```

Host-only ack AFTER durable information outbox enqueue, not emit.

```python
remembered_building_changes(self, *, after_sequence, limit, text_budget_bytes)
```

Host-only indexed updates; its checkpoint is an observation history.

```python
remembered_building_kind(self, *, kind, after_sequence=0, limit, text_budget_bytes)
```

Host-only indexed name enrichment of previously observed history.

```python
terrain_view(self, *, tile_budget)
```

Current lawful viewport grid, using the same world pixels as units.

```python
build_sites(self, *, text_budget_bytes, encoding)
```

Current lawful per-building starts before entering placement mode.

```python
menu_state(self)
```

Trusted host-only read of native Game Menu and control ownership.

```python
menu_control(self, *, action)
```

Trusted host protection only, through the actual native Game Menu.

```python
visible_phase(self, *, max_age_ms)
```

Host-only availability/freshness; v50 cannot emit a visible stage.

max_age_ms is chosen by the host's current handoff contract. This read
does not issue a game request, enable CU, arm actions or infer playing.
The caller adds its own run/capture generation to the returned binding.

```python
placement_preview(self, ticket, x, y, *, anchor='main_building_top_left')
```

Observe the current native placement, without submitting a command.

The default anchor is the main building's tile-aligned top left. The
response supplies engine_click_point for the existing target_at call,
including the native offset when an Addon is being placed.

```python
observe_view(self, *, unit_budget, terrain_tile_budget=None, text_budget_bytes=65536, encoding='cp1252')
```

Return current unit observations together with the viewport grid.

Protocol 17 publishes both buffers in the same native request.
The default grid budget covers the complete native viewport rather
than truncating cells. An explicit smaller budget is also accepted.

```python
resource_sites(self, *, unit_budget, include_selected_amount=False)
```

Current resource locations in player vision, independent of the camera.

```python
ui_counters(self, *, text_budget_bytes=65536, encoding='cp1252')
```

Current visible scoreboard/counter text, independent of selection.

```python
own_units(self, *, unit_budget, text_budget_bytes, encoding)
```

Current own entities and positions, independent of the camera.

One row per entity; no regional grouping, estimates or detailed stats.
Text budget counts UTF-8 label bytes in all returned rows. Unsupported
containment positions are explicit nulls, never old coordinates.
Larva locations are omitted while their entities/names remain present.

```python
selected_members_for_ticket(self, ticket, *, text_budget_bytes=65536, encoding='cp1252', reuse_own_snapshot=False)
```

Join an already observed live ticket to lawfully selected own values.

This is the same selected_own_values path as current_selection, without
the redundant initial ticket probe when a foreground action already
returned the live ticket. A missing local member falls back once to a
fresh own snapshot; unselected or enemy pointers never acquire values.

```python
current_selection(self, *, text_budget_bytes=65536, encoding='cp1252', reuse_own_snapshot=False)
```

Current real selection, with names/positions of its own members.

Ownership lookup uses the same current own snapshot. Enemy selections
do not acquire any off-screen name, position or dynamic value here.
A caller that just sampled own_units in this epoch may reuse that
snapshot; any missing local member falls back to one fresh sample.

```python
__init__(self, pid, *, knowledge_path=None, bookmark_path=None, building_memory_path=None, shared_knowledge_path=None, knowledge_mode='official_standard')
```

```python
close(self)
```

```python
resources(self)
```

Current own HUD resource/supply counters; free, read-only, no learning.

```python
remembered_buildings(self, *, limit, text_budget_bytes, cursor=None)
```

Read learned building history; a historical record never proves life.

```python
selected_panel(self, ticket, *, learn_details=False, text_budget_bytes=None, encoding=None)
```

```python
building_memory_status(self)
```

```python
assign_group(self, ticket, slot)
```

```python
select_group(self, slot)
```

```python
focus_selected(self, ticket)
```

```python
save_view(self, slot, *, label, note, text_budget_bytes)
```

```python
list_views(self, *, text_budget_bytes)
```

```python
get_view(self, slot, *, text_budget_bytes)
```

```python
recall_view(self, slot)
```

```python
control_guide()
```

Current interface contracts; automatic information, no play advice.

```python
consume_learned_group_card(self)
```

Pop this call's internal group-card learning handoff.

The host consumes it for public-reference projection. It is deliberately
never part of ``command_card``'s public return dict.

```python
command_card(self, ticket, *, text_budget_bytes, encoding)
```

```python
activate_button(self, button, *, queued=False)
```

Use a current train/research/upgrade/build/skill/menu button.

Targeted operations return a target ticket. Each subsequent placement
is another operation in the same budget. No new ability cost is read.

```python
cancel_production_slots(self, production, slots, *, confirmation_timeout=2.0)
```

Submit one original entry, then stop if its effect is unconfirmed.

The transport acknowledges submission only. Even an expected remaining
list can also result from completion or human edits, so it cannot
authorize the next cancellation. No queued operation is retried.

```python
cancel_all_production(self, production, *, confirmation_timeout=2.0)
```

Request clearing this queue through individually charged actions.

```python
target_at(self, target, x, y)
```

Click the current operable viewport for this exact pending operation.

```python
move_selected(self, ticket, x, y, *, queued=False)
```

Move the actual selected group, optionally appending a native order.

```python
move_minimap(self, ticket, pixel_x, pixel_y, *, queued=False)
```

One native Move at an existing minimap point; camera stays in place.

```python
attack_move(self, ticket, x, y, *, queued=False)
```

```python
right_click(self, ticket, x, y, *, queued=False)
```

Native contextual right-click on the current operable viewport.

Ground normally moves; units can cause attack, follow, boarding or
worker work as resolved by the game's current actors and target.
Use actual Load/Unload buttons for transport commands as available.

```python
cancel_pending(self)
```

Press native ESC for the current command target/placement mode.

```python
unload_one(self, passenger)
```

Unload one current visible cargo icon of the real selected carrier.

```python
patrol(self, ticket, x, y, *, queued=False)
```

```python
attack_move_minimap(self, ticket, pixel_x, pixel_y, *, queued=False)
```

```python
patrol_minimap(self, ticket, pixel_x, pixel_y, *, queued=False)
```

```python
halt_construction(self, ticket)
```

```python
resume_construction(self, ticket, x, y)
```

```python
cancel_construction(self, ticket)
```

```python
cancel_research(self, ticket)
```

```python
cancel_upgrade(self, ticket)
```

```python
cancel_morph(self, ticket)
```

```python
cancel_addon(self, ticket)
```

```python
cancel_production(self, production, slot)
```

Cancel exactly one current queue entry; consumes one shared action.

## scapi.supply_knowledge

[Source](../scapi/supply_knowledge.py)

```python
selected_supply_type(snapshot, expected_kind, expected_owner)
```

Pure mirror of target 424AD0/43BBB0 on an authorized selected CUnit.

Root must establish ownership and recheck epoch, selection generation,
selected pointer and ownership around its snapshot. This never calls native
functions and cannot establish that an arbitrary snapshot was legal to read.

```python
normalize_selected_supply(unit_snapshot, rawtable, *, expected_kind=None, expected_owner=None)
```

Return private {kind, raw, amount}; caller supplies the legal OWN gate.

Optional expected values permit comparison with the already verified panel.
Without them this only normalizes the supplied bytes, not ownership/access.

### SupplyKnowledge

```python
__init__(self, knowledge, rules)
```

```python
snapshot(self)
```

Private current-map applicability bytes, never a public knowledge list.

```python
learn(self, display_kind, raw)
```

Persist only an authorized own-selected UI-normalized observation.

```python
value(self, kind, snapshot)
```

Cache-only learned amount if this private snapshot's raw byte matches.

## scapi.terrain

[Source](../scapi/terrain.py)

```python
terrain_view(api, *, tile_budget)
```

Return the whole current 32px grid, with sixteen 8px samples per tile.

Explored fog retains ground geometry and the last observed creep state.
Power is specific to the actual active placement type,
and is unknown unless its complete candidate footprint is legally visible.

```python
terrain_from_reply(api, result, *, tile_budget)
```

Decode the terrain attachment committed by observe-view's same request.

```python
placement_preview(api, ticket, x, y, *, anchor='main_building_top_left')
```

Validate one actual placement without issuing a command or spending APM.

x/y are tile-aligned world pixels. The default uses the main building's
top left; engine_anchor preserves target_at's existing click semantics.
Submit a successful preview using its engine_click_point to target_at.
That click may differ from the footprint's world origin when the camera
is aligned to 8 pixels but not 32; it inverts the actual native snap.

## scapi.terrain_reference

[Source](../scapi/terrain_reference.py)

```python
canonical(value)
```

```python
aligned(value)
```

```python
build_screen_terrain(terrain, store=None)
```

Render the observed live/fog screen as compact 16x16px characters.

Current cells use live walkability so doors are visible immediately. Fog
cells use their static ground attachment, while remembered creep and an
unresolved terrain label may fall back to this scope's lawfully learned
SQLite history. Any missing 8px subcell keeps its 16px cell unknown.

```python
merge_known(old, new)
```

Unknown/obscured subcells do not erase legitimately learned geometry.

### TerrainReference

```python
__init__(self, knowledge)
```

```python
observe_resources(self, snapshot, *, visibility, observed_session, expected_session, include_selected_amount=False, selected=None, current_camera=None, census_serial=None, published_tokens=())
```

Merge one lawful resource census; reference history never drives game IO.

A missing row is only absent from the census. A complete matching
operable-viewport mask must cover every old footprint tile before recording an
observed absence. Resource amount is admitted only by a successful real
selection's one-shot UI read, never by an automatic census.

```python
geometry_qualification(cell)
```

```python
geometry(cell)
```

```python
observe(self, terrain, *, observed_session, expected_session)
```

Caller supplies identities from the completed lawful sample boundary.

```python
read_grid(self, args)
```

Render one bounded rectangle entirely from last-observed SQLite rows.

This is history, never a current screen: every remembered creep tile is
creep_fog, and a rectangle tile with no stored row is unknown.

```python
read(self, args)
```

```python
project_automatic_view(view)
```

Automatic wire keeps UI metadata; fixed map lives in SQLite.

Existing remembered_buildings updates remain the independent building overlay.
Placement still uses the actual native preview, never this historical map.

## scapi.terrain_semantics

[Source](../scapi/terrain_semantics.py)

```python
terrain_label(tileset, kind)
```

Only name an unambiguous rendered-tile family, never a whole map.

```python
attach_unit_terrain(fields, terrain)
```

Match the same native snapshot's actual centre, not its hit-test anchor.

## scapi.tips

[Source](../scapi/tips.py)

### TipsRuntime

```python
__init__(self, store, *, profile_id, panel_scope=None)
```

```python
close(self)
```

```python
reference_read(self, *, tip_id=None, cursor=None, limit=32)
```

Only Tips already lawfully offered to this profile are readable.

```python
bind_panel_scope(self, scope)
```

Record scope provenance only; internal panel names grant no Tip gates.

```python
observe_button_names(self, names, *, source_ref)
```

Trusted host only, after the final public response has been flushed.

Pass eligible enabled labels actually delivered by command_card. This is
not a player tool accepting arbitrary assertions. Unknown labels are not
stored and no index or additional label is returned.

```python
observe_public_names(self, names, *, source_ref)
```

Trusted host only, after flushing the final successful public result.

Extract only actual public name fields, including completion names with
no public type ID; keep resulting Tip gate + provenance, not another unit
catalog. Never use internal panel_name, Tip text, event free text, failed
responses, intermediate reads or inferred labels as ingestion sources.

```python
pending(self, *, limit, text_budget_bytes, capabilities=(), startup_only=False)
```

Budgeted offers. Only acknowledge after the response reaches the player.

capabilities must come from the host's actual lifecycle implementation;
values are restart/objectives/save/load, not inferred from Tip text.
Byte budget covers compact UTF-8 JSON of the complete items array.

```python
acknowledge(self, tip_ids)
```

Acknowledge only previously offered IDs; no name or skill unlocks.

## scapi.ui_counters

[Source](../scapi/ui_counters.py)

### _ReadBudget

```python
__init__(self, result)
```

```python
ui_counters_snapshot(client, expected_epoch, *, text_budget_bytes=65536, control_budget=64, encoding='cp1252', _budget=None)
```

Free read; grow explicit read budgets on demand, never return a prefix.

Native pause/menu, process/epoch drift, layout mismatch, changing UI data and
malformed linked lists return no partial counters. Budget replies are read
requirements rather than truncation or a persistent limit on displayed UI.
One cooperative time budget covers reads and validation, including retries
when Stage5 passes the same internal budget. No OS read is interruptible.

## scapi.view_bookmarks

[Source](../scapi/view_bookmarks.py)

```python
validate_save(slot, label, note, *, text_budget_bytes)
```

Validate metadata before the caller spends a native action allowance.

### BookmarkStore

```python
__init__(self, path, session_key)
```

```python
close(self)
```

```python
contains(self, slot)
```

Cheap occupancy check; it never fetches text or coordinates.

```python
save(self, slot, x, y, label, note, *, text_budget_bytes, overwrite=False)
```

```python
list(self, *, text_budget_bytes)
```

Return all three slots and saved labels, never note bodies.

```python
get(self, slot, *, text_budget_bytes)
```

Read one full label/note or return the required budget first.

```python
read_coords(self, slot)
```

Controller-only recall input; no labels or notes are materialized.

## scapi.visible_phase

[Source](../scapi/visible_phase.py)

```python
project_visible_phase(before, after, *, pid, process_creation, epoch, now_tick_ms, max_age_ms)
```

## scapi.visible_status

[Source](../scapi/visible_status.py)

```python
visible_panel_status(reader, state, text, *, _budget=None)
```

Return the drawn status string, or None when its control is absent.

The native renderer owns localization and status precedence. In particular,
foreign hallucination identity is never inferred from the raw unit flags.
A stale own-only hallucination text pointer is suppressed for foreign units.
RuntimeError means the sample changed or its source was malformed.
TextBudgetRequired retains the caller's existing explicit budget contract.

## scapi.weapon_names

[Source](../scapi/weapon_names.py)

### WeaponNames

```python
__init__(self, reader, game_base)
```

```python
read_selected(self, state, text)
```

```python
selected_evidence(self, stamp, encoding)
```

```python
reuse(self, observed, knowledge)
```

Compare known bytes only; never scan/decode a new loaded string.

Each SQL seek returns one known candidate for this observed key. On a
mismatch the first different byte strictly extends the loaded prefix,
so failed seeks are bounded by known label length, not history count.
Buffers contain at most one loaded prefix per current tooltip; no
historical raw/name lists are accumulated. Each added byte is read
only up to a previously legally learned candidate length.

```python
unchanged(self, stamp)
```

## scapi.wire_identity

[Source](../scapi/wire_identity.py)

```python
creation_integer(value)
```

Accept exact Python uint64 or canonical ASCII decimal, never approximation.

```python
normalize_identity(value)
```

Known ticket/session path only; preserve exact later validation of all fields.

```python
decode_request(request)
```

Normalize only the executable identity argument belonging to this operation.

```python
encode_public_frame(value)
```

Clone known public frame paths; arbitrary experience contents stay untouched.

## tools.__init__

[Source](../tools/__init__.py)

## tools.build_native

[Source](../tools/build_native.py)

```python
pe32(path)
```

```python
compile_native(compiler, arguments)
```

```python
require_max_path(targets, parser)
```

```python
refuse_existing(targets, parser)
```

```python
verify_loader_allowlist(loader, dll)
```

```python
main(argv=None)
```

## tools.native_episode_artifacts

[Source](../tools/native_episode_artifacts.py)

```python
episode_spec(candidate)
```

Validate only the exact host recipe, not an arbitrary DLL/EXE directory.

```python
approved_episode_candidate(candidate)
```

```python
approved_episode_loader(loader)
```

```python
build_episode_candidate(*, compiler=None)
```

Copy unchanged bridge bytes and compile one exact-path loader, once.

No game/process discovery or injection occurs here. Old images are never
overwritten or deleted; their private memory remains until ordinary game exit.

## tools.native_loader_runtime

[Source](../tools/native_loader_runtime.py)

### NativeLoaderRuntimeError

```python
__init__(self, message, receipt)
```

### _GUID

### _FILE_INFO

### _ACL_INFO

### _SID_ATTR

### _LUID

### _LUID_ATTR

### _OVERLAPPED

### _STARTUPINFO

### _PROCESS_INFO

### _Windows

```python
__init__(self)
```

```python
check(ok, operation)
```

```python
close(self, handle)
```

```python
open(self, path, *, write=False, create=False, directory=False, share=1)
```

```python
local_app_data(self)
```

```python
sid_rid(self, sid)
```

```python
label(self, handle)
```

```python
token(self, process)
```

```python
copy_and_lock(self, source, target)
```

```python
probe(self, path, receipt)
```

### LoaderRuntime

```python
__init__(self, windows, source)
```

```python
mark_execution_started(self)
```

```python
close(self, *, completed=False)
```

Release handles; never let file cleanup replace installation ownership.

A thrown/unknown execution retains its unique copy. Only a returned
process result or a never-started attempt permits removal.

```python
prepare_loader_runtime(source_loader)
```

Pin a fixed or exact recipe-bound episode loader and inspect its child.

## tools.role_experience_cli

[Source](../tools/role_experience_cli.py)

```python
parser()
```

```python
main(argv=None)
```

## tools.session_native_cleanup

[Source](../tools/session_native_cleanup.py)

```python
approved_candidate(candidate)
```

### CleanupBlocked

```python
validate_original_snapshot(binding, original)
```

Reject absent/partial/other-run snapshots before any mutation.

```python
validate_quiescence(binding, receipt)
```

```python
module_entry_for_api(api)
```

Use the exact ctypes structure bound to this API instance's functions.

CLI imports can load stage2_readprobe under both package and bare names.
Equal layouts remain distinct Python ctypes classes; do not cast past the
type check or replace a live function's declared signature.

```python
read_current_inventory(client)
```

Future authorized use only: reuse the owned handle, enumerate its modules.

No process discovery and no Client construction. All returned SCAPI state
reads are from current modules of this exact process, never an old list.

```python
verify_native_cleanup(client, *, binding, original, quiescence, drained, inventory_reader=read_current_inventory)
```

Independent read-only check; also usable after an unknown request result.

Uses no request(), arm(), ACK, external process exit or API.close(). Missing
evidence raises CleanupBlocked; mismatches return fully_withdrawn:false.

### NativeCleanupAttempt

```python
__init__(self, *, binding, original)
```

```python
execute(self, client, *, quiescence, drain_after_stop, inventory_reader=read_current_inventory)
```

## tools.stage2_readprobe

[Source](../tools/stage2_readprobe.py)

### ProbeError

```python
__init__(self, code, message, **details)
```

```python
record(self)
```

```python
number(value)
```

```python
checked_range(address, size)
```

```python
path_key(path)
```

```python
module_address(module, rva, size)
```

### PROCESSENTRY32W

### MODULEENTRY32W

### MEMORY_BASIC_INFORMATION

### WinAPI

```python
__init__(self)
```

```python
failure(self, operation, **details)
```

```python
image_path(self, handle)
```

```python
snapshot(self, flags, pid)
```

```python
processes(self)
```

```python
module(self, pid)
```

### WinReader

```python
__init__(self, api, pid)
```

```python
close(self)
```

```python
metadata(self)
```

```python
validate_range(self, address, size)
```

```python
read(self, address, size)
```

## tools.stage3_client

[Source](../tools/stage3_client.py)

### State

### MailboxBusyError

```python
dll_export_rva(name, dll_path=DLL, state_type=State)
```

Read the one built PE's named export; reject forwarded/out-of-image data.

### Client

```python
__init__(self, pid)
```

```python
close(self)
```

```python
peek(self)
```

```python
arm(self, enabled, interval_ms=None)
```

```python
request(self, op, x=0, y=0, epoch=None, selection=None, timeout=5.0, *, x2=0, y2=0, add=False, extra=None)
```

## tools.stage4_client

[Source](../tools/stage4_client.py)

### State

### Client

```python
configure_attention(self, *, view_ms, dynamic_ms, detail_ms, contact_ms)
```

```python
configure_detail_test(self, wait_ms)
```

Explicit development value; this does not choose a production policy.

## tools.stage4_event_collector

[Source](../tools/stage4_event_collector.py)

```python
record_hash(data)
```

```python
decode_slot(raw, sequence, epoch)
```

### StreamReader

```python
__init__(self, client, generation)
```

```python
header(self)
```

```python
read_batch(self, limit)
```

```python
acknowledge(self, next_sequence)
```

```python
write_status(path, value)
```

```python
main(*, client_factory=Client, evidence_stage='stage4', evidence_root=None)
```

## tools.stage4_event_journal

[Source](../tools/stage4_event_journal.py)

```python
__all__ = ['AppendResult', 'EVENT_TEXT_BYTES', 'Event', 'Journal', 'JournalClosedError', 'JournalConflict', 'JournalCursorError', 'JournalError', 'JournalEvent', 'JournalOrderError', 'JournalSchemaError', 'JournalValidationError', 'Page', 'RunStatus']
```

### JournalError

### JournalValidationError

### JournalConflict

### JournalOrderError

### JournalClosedError

### JournalSchemaError

### JournalCursorError

### Event

```python
sequence: int
epoch: int
observed_tick: int
type: int
x: int
y: int
color: int
text: bytes
text_bytes: int | None = None
```

### JournalEvent

```python
run_id: int
sequence: int
epoch: int
observed_tick: int
received_monotonic_ns: int
type: int
x: int
y: int
color: int
text_bytes: int
text: bytes
overflow: bool
gap: bool
```

### RunStatus

```python
run_id: int
epoch: int
started_ns: int
closed_ns: int | None
overflow: bool
lost_max: int
last_sequence: int
event_count: int
gap_events: int
missing_sequences: int
first_received_ns: int | None
last_received_ns: int | None
last_observed_tick: int | None
```

### AppendResult

```python
run_id: int
inserted: int
duplicates: int
persisted_through: int | None
acknowledged: bool
ack_next: int | None
overflow: bool
gap_events: int
missing_sequences: int
```

### Page

```python
events: tuple[JournalEvent, ...]
next_cursor: str | None
has_more: bool
```

### Journal

```python
__init__(self, path: str | Path, *, clock_ns: Callable[[], int]=time.monotonic_ns)
```

```python
close(self)
```

```python
start_run(self, epoch: int, *, started_ns: int | None=None)
```

Create a new isolated run, even when ``epoch`` was used before.

```python
finish_run(self, run_id: int, *, closed_ns: int | None=None)
```

Mark a run closed.  Existing event rows are untouched.

```python
append(self, run_id: int, events: Iterable[Event], *, overflow: bool=False, lost: int=0, ack: Callable[[int], None] | None=None)
```

Append a batch in one transaction.

``overflow``/``lost`` are transport-status observations, not event
payload.  They may be supplied with an empty ``events`` iterable to
update the run's sticky overflow state.  ``lost`` is the highest
observed native lost counter; it never decreases within a run.

``ack`` is invoked only after a successful COMMIT. New sequences
must form a contiguous prefix; an out-of-order/gap batch is rolled
back without acknowledging it, so the missing records can be read
before retrying that batch. Legacy runs with gaps remain readable
but cannot receive further appends; explicit recovery is required.

```python
status(self, run_id: int)
```

```python
read_events(self, run_id: int, *, limit: int, cursor: str | None=None)
```

Read at most ``limit`` events after ``cursor``.

``limit`` is mandatory and must be a positive int.  The query fetches
at most ``limit + 1`` rows to determine ``has_more``; it never loads
the whole run.

## tools.stage5_client

[Source](../tools/stage5_client.py)

### State

### Client

```python
arm(self, enabled, interval_ms=None)
```

## tools.stage5_visible_phase_client

[Source](../tools/stage5_visible_phase_client.py)

### State

### Client

