# Instructions for an AI integrating or using SC Singleplayer AI Bridge

Audience: an AI agent helping a user set up this repository, implement an integration, or control a game through an already integrated API.

Read this file before proposing commands or generating an adapter. Use the current source as the authority for signatures and behavior. `MUST` marks an integration requirement; it does not grant permission to execute commands on the user's machine. Match execution to the user's current request.

## 1. Identify the task and the available layer

This repository contains a Windows Python API and an x86 native bridge. It does **not** contain a complete application host, a model backend, an MCP server, an HTTP server, a desktop client, or a one-command game launcher.

Determine which task you have:

| User task | Your work | Completion evidence |
| --- | --- | --- |
| Understand the API | Read this guide, the interface reference and relevant source. | Explain actual entrypoints and prerequisites. |
| Prepare/build the repository | Locate an existing compatible Python and compiler; build the default native artifacts. | Successful imports/build and correct artifacts. This does not establish game connectivity. |
| Connect this API to an AI/application | Reuse an existing compatible host, or implement the host responsibilities below within the user's requested scope. | Real startup, information delivery, a permitted action/result and effective stop through that host. |
| Play through an existing integration | Use the host's advertised action and information channels. | Observe actual game effects and honor the user's stop/acceptance conditions. |

If the user does not understand setup, perform the technical investigation and configuration you are authorized to perform. Ask for an unavailable user-specific prerequisite only when needed. Do not hand the user a fabricated `pip install` package, nonexistent launch command, or unfinished script described as ready to play.

Use these sources in order:

1. [Repository overview](../README.md) and [integration contract](API.md).
2. [Interface signatures](API_REFERENCE.md). Inherited methods are listed under their base classes.
3. The implementation for the operation you will expose. The reference contains internal helpers as well as public methods; it is not a model-tool allowlist.
4. [License and source notices](../NOTICE.md) when redistributing an integration.

## 2. Resolve configuration before execution

Use explicit configuration owned by the host. Model action requests must not supply executable paths, database paths, arbitrary native addresses, or a replacement game identity.

| Value | Current requirement |
| --- | --- |
| `repo_root` | The absolute root of this checkout, discovered on the user's machine. Run Python with this root on the module search path. |
| `python_exe` | Windows **64-bit Python 3.12**. Reuse an existing suitable environment. The API uses the Python standard library. |
| `tcc_exe` | An existing **32-bit x86 TinyCC** executable with its accompanying headers and libraries. Compiler architecture differs from Python architecture. |
| `game_exe` | The current adapter is for the specific StarCraft **1.05 x86** layout at **`D:/Starcraft/starcraft.exe`**. It is not a generic BWAPI/Remastered adapter. |
| `profile_id` | A nonempty, stable identifier for this user's legitimate knowledge/profile stores. |
| profile database paths | Explicit writable paths for knowledge, optional shared knowledge, bookmarks, building memory, lifecycle, durable delivery and per-role episode/global experience. Preserve the intended profile; do not erase learning to make startup succeed. |
| `save_dir` | The existing save directory for the actual game installation, verified by the host. `Lifecycle` requires it even when no optional menu capability is advertised. |
| session identity | Fresh PID, exact process creation FILETIME and current native epoch. PID alone is insufficient. |
| action interval | Explicit integer milliseconds, at least **334**, less than `2**31`. Camera, selection and commands share the action budget. |
| attention intervals | Explicit positive integers for view, dynamic, detail and contact attention, each less than `2**31`. Preserve and deliver the actual configured values. |
| text encoding | Existing automatic sampling uses `cp1252`; `EventLog` also requires an explicit encoding. Other localizations require a reviewed integration change, not an unsupported configuration promise. |

If the game path/version/layout differs, report that compatibility prerequisite. Changing only a path string, disabling validation or adding an executable to an allowlist does not port the adapter.

## 3. Build and perform a non-attaching import check

These commands are for a future setup task authorized by the user. `$Python` and `$Tcc` below mean the absolute executable paths you have actually discovered; resolve them before running the commands. The working directory is `repo_root`.

```powershell
& $Python -B -c "from scapi import Stage5, SelectionTicket, ButtonTicket, TargetTicket, ProductionTicket, CargoTicket; from scapi.player_runtime import ReadyRuntime; from scapi.experience import ExperienceStore, execute_role_experience; print('API imports OK; no game attachment attempted')"
```

```powershell
& $Python -B tools/build_native.py --tcc $Tcc
```

The default build creates:

- `build/stage5/scapi_stage5_v53_stability.dll`
- the byte-identical `build/stage5/scapi_stage5_v53_next_episode.dll`
- `build/stage5/stage5_loader_v53_stability.exe`
- `build/stage5/stage5_loader_v53_next_episode.exe`

[The builder](../tools/build_native.py) compiles, checks PE32/x86 output and verifies each loader's embedded exact DLL path. It does not execute the loader. It refuses existing outputs and competing build ownership. Do not delete a lock or overwrite an image merely to retry a build; determine who owns it and whether an image may still be resident. `--output-dir` is useful for isolated compilation, but default clients and loader preparation accept specific approved paths. An arbitrary successful build directory is not automatically a supported live installation.

Use **v53 / ABI 27** for the integration described here; the candidate set is the 676-byte `ScapiState` stability and next-episode pair. `--visible-phase-diagnostic` builds a separate **v50 / ABI 28** diagnostic candidate. The current `NativeCleanupAttempt` verifier supports v53 / ABI 27 and retains v52/v51/v49 legacy candidate recognition; a v50 build is not a drop-in end-to-end replacement.

Import success is a local dependency result. `Stage5(pid, ...)` is not an import check: it opens a real bridge connection, creates/opens stores and starts owned workers, including the game-exit cursor reset waiter. Do not construct it until the host preparation below is complete.

## 4. Implement or locate the trusted host

There are two distinct roles:

- **Integration AI / trusted host:** owns process verification, installation, capture, persistence, sampling, delivery, readiness, menu execution and cleanup.
- **Game-playing AI:** receives only lawful public observations and current tickets; submits allowed intentions through the host's player-request boundary.

Do not expose `tools.*`, `_client`, `reader`, `peek()`, raw native snapshots, arbitrary `getattr` dispatch or arbitrary Python evaluation as game-playing tools. Internal native data is not public observation data.

The host must provide the following implementation, or prove that an existing host already provides it:

| Responsibility | Repository primitive | Host work still required |
| --- | --- | --- |
| Prepare native execution | [`prepare_loader_runtime`](../tools/native_loader_runtime.py) | Verify game identity/layout and installation state; preserve pre-install originals; launch and own the actual loader process. |
| Capture events | [`StreamReader`, collector `main`](../tools/stage4_event_collector.py), [`Journal`](../tools/stage4_event_journal.py) | Match the v53 client, manage collector startup/handshake/exit and persist before native ACK. |
| Public API | [`Stage5`](../scapi/stage5.py) | Choose stores, preserve identity and serialize access on its owning worker. |
| Request boundary | [`ReadyRuntime`](../scapi/player_runtime.py) | Supply bound API/events/lifecycle, log, profile and run; implement model/tool transport. |
| Automatic information | [`AutomaticInformation`, `DeliveryOutbox`](../scapi/automatic_information.py) | Schedule sampling and implement durable publisher, transport receipts and delivery effects. |
| Menu/session changes | [`Lifecycle`](../scapi/lifecycle.py) | Execute actual permitted menu input, observe outcomes and rebind changed sessions. |
| Stop | [`NativeCleanupAttempt`, `verify_native_cleanup`](../tools/session_native_cleanup.py) | Freeze new work, establish real quiescence, drain owned workers and verify restoration. |

`scapi_action`, `scapi_reference`, `scapi_context_ready`, GUI/CU tools and an application's `PlayerSession` are not implementations shipped here. If you choose those tool names, implement their adapters. `player_context.startup_context` uses an optional reference-tool naming convention; do not advertise that tool without providing it.

### 4.1 Prepare and install the native bridge

1. Verify the actual game path, PID, creation time, supported layout, existing resident images and absence of an unresolved previous installation attempt.
2. Capture the real pre-install restoration baseline. Read `validate_original_snapshot` in the cleanup module before installing: it requires the current identity/run/candidate, protocol 27, state size 676, the complete 73-span historical code inventory, five ping ABI spans, IAT/system-module evidence and resident inventory. The snapshot must actually precede installation. This repository has no complete exported one-command baseline-capture/host launcher; implement this host preparation before proceeding.
3. Call `prepare_loader_runtime(source_loader)` with the approved loader path. It returns a `LoaderRuntime` object, not a path or dict. It creates a private execution copy and performs a real suspended-child token probe. Its `receipt` may say `status="ready"` while `game_access_verified` remains `False`.
4. Use `LoaderRuntime.execution_path` for actual execution, call `mark_execution_started()` before attempting launch, and preserve exact argv plus stdout/stderr/exit and child-identity evidence. The [loader CLI](../native/stage3_loader.c) takes `PID ABSOLUTE_BUILT_DLL`. It validates the compiled DLL path and fixed game path; its PID argument does not replace the host's creation-time checks around execution.
5. Distinguish confirmed loader exit, successful installation and healthy bridge observation. Call `close(completed=True)` only after an actual execution exit is confirmed; otherwise retain the unknown attempt with `close()` and its receipt. `NativeLoaderRuntimeError.receipt` carries failed preparation/cleanup evidence. Do not bypass this preparation by directly executing the original build output.
6. Inspect the fresh matching bridge through the host's native client, initially with gameplay actions disabled. Confirm ABI, epoch, hooks and actual settings before continuing. Do not retry an unknown installation or install the same image twice.

The game-exit cursor waiter uses `SPI_SETCURSORS` to reset the system pointer scheme once the exact game process exits. It is separate from native bridge cleanup and may outlive the API process. Its existence is not proof of visible pointer recovery or complete application shutdown. See [cursor recovery](../scapi/cursor_recovery.py).

### 4.2 Establish capture and session ownership

For v53 / ABI 27 use `tools.stage5_client.Client`. The collector's CLI defaults to a Stage4 client; do not run it unmodified and assume v53 compatibility. A v50 / ABI 28 diagnostic candidate requires its matching visible-phase transport instead; v52/v51/v49 artifacts remain recognized only for cleanup of prior attempts.

Two integration choices exist:

- Embed `tools.stage4_event_collector.main(client_factory=Stage5Client, evidence_stage=..., evidence_root=...)` in a host-owned collector entrypoint. `main` reads `sys.argv`; its `--pid`, `--capacity`, `--batch-limit`, `--poll-ms`, `--journal`, `--status-file`, `--stop-file` are explicit. The existing wrapper restricts its evidence root and output paths to this checkout's `evidence/` tree. Use unused status/stop paths for each actual attempt.
- Implement equivalent ownership using `StreamReader` and `Journal`. This requires the same generation checks, durable writes, ACK order and cleanup as the existing collector.

The initial sequence is `capture-start` → fresh `events_generation` → `StreamReader(client, generation)` → `Journal.start_run(reader.epoch)` → verified open journal/native capture binding. `Journal.append(..., ack=reader.acknowledge)` persists the batch before ACK. Wait for and validate the actual running handshake, including process creation time, epoch, capacity and capture generation.

Keep these identifiers distinct:

| Identifier | Representation / meaning |
| --- | --- |
| `journal_run_id` | Integer returned by `Journal.start_run`; used by `EventLog`. |
| `run_ref` | Nonempty host string passed to `ReadyRuntime` and public frame provenance. |
| cleanup `binding.run_id` | Nonempty host string identifying the cleanup ownership scope; not the integer journal ID. |
| `capture_generation` | Positive native generation bound to this collector. |
| `process_creation` | Exact FILETIME integer inside Python; canonical decimal string at the public JSON boundary. |

Collector recovery requires both `--resume-run` and `--attach-generation`, the same capacity/generation and a matching still-open journal epoch. `--drain-only` is for an existing run. Do not create a fresh run to hide an unresolved old collector.

### 4.3 Construct the bound API/runtime

Construct these objects on their owning worker using real, verified values:

```python
api = Stage5(
    verified_pid,
    knowledge_path=knowledge_db,
    bookmark_path=bookmark_db,
    building_memory_path=building_memory_db,
    shared_knowledge_path=shared_knowledge_db,
    knowledge_mode="official_standard",
)
events = EventLog(journal_path, journal_run_id, encoding="cp1252")
lifecycle = Lifecycle(lifecycle_db, profile_id=profile_id, save_dir=verified_save_dir)
lifecycle.bind_session(verified_session)
runtime = ReadyRuntime(
    api, events, private_log,
    profile_id=profile_id,
    lifecycle=lifecycle,
    run_ref=run_ref,
    capabilities=implemented_menu_capabilities,
    diagnostics="minimal",
    role_experience_paths=role_episode_dbs,
    shared_experience_paths=role_shared_dbs,
    shared_experience_profile="official_standard",
)
```

This is a construction fragment, not a standalone launcher. All argument variables above are host-owned prerequisites. Import `Stage5` from `scapi`, `EventLog` from `scapi.events`, `Lifecycle` from `scapi.lifecycle`, and `ReadyRuntime` from `scapi.player_runtime`. `private_log` is a writable text stream with `write()` and `flush()`; it is not the model response channel. Use only menu capabilities actually implemented by the host, or an empty tuple. Do not add Tutorial-specific budgets unless the user requested that scenario.

`shared_knowledge_path` is optional. With `knowledge_mode="official_standard"` it attaches a caller-supplied shared library of previously verified static unit, weapon, skill and supply facts, including lawfully learned `detector: True`. It does not preload a roster, encyclopedia or current unit truth. Use `isolated_scope` when shared static learning must remain disabled. The selected-panel two-second HUD dwell is camera-independent: camera movement alone does not restart it, while changed selection or visible rules require a fresh interval. A homogeneous selected own group of 2–12 units can learn shared command-card labels after its two-second dwell; those labels do not establish single-unit stats, cargo/production facts, current enabled state or action permission.

`verified_session` must contain **exactly** `pid`, `process_creation`, `epoch`, each a positive Python integer. The method is `bind_session`, not `bind`. Bind only after real process/epoch verification and capture handshake. `lifecycle.profile` must equal the runtime `profile_id`; episode experience stores use that same episode profile, while their configured shared profile is separate. A later `runtime.attach(...)` requires the previous API/events to have been suspended and a legitimately rebound lifecycle; it does not install or verify a new bridge for you.

### 4.4 Deliver startup context and Tips, then enable actions

1. Configure actual native attention via the trusted client's `configure_attention(view_ms=..., dynamic_ms=..., detail_ms=..., contact_ms=...)`. Choose the action interval explicitly; keep actions disabled during preparation.
2. Obtain a current verified health/settings snapshot. Use [`startup_context`](../scapi/player_context.py) if its reference adapter is implemented, and deliver the actual settings, capabilities and session identity to the player.
3. While `runtime.startup_complete` is false, call `runtime.startup_page(limit=..., text_budget_bytes=...)`. Handle `budget_required` by completing the requested read, not by dropping text. Fully deliver and flush each successful page before `runtime.delivered_startup([item["id"] for item in page["items"]])`. Continue until startup is complete. Marking all Tips delivered in advance is invalid.
4. Establish the host's current user authorization/readiness and game control conditions. Use the same verified candidate/identity; an arbitrary model message is not a receipt for another run.
5. Only then enable actions through the trusted client with `arm(True, interval_ms=...)`, read back current settings and call `validate_armed_settings(snapshot, delivered_settings)`. Confirm current `control_state().actions_allowed`. Paused/scripted/inactive states do not become playable merely because startup completed.

## 5. Supply automatic information and a player action channel

### 5.1 Do not turn host reads into player tools

`ReadyRuntime.handle(request)` is the player-request boundary. It deliberately rejects every operation in `INFORMATION_OPS` with `information_is_automatic`, including `observe_view`, `resources`, `current_selection`, `command_card`, `events`, `tips_pending` and lifecycle status queries.

The trusted host uses automatic sampling and, where required, `runtime.dispatch(...)` internally. Do not expose `dispatch` to the playing model to bypass this distinction. A Python caller may use typed `Stage5` methods as trusted integration code, but that does not make internal reads part of the model-facing action schema.

### 5.2 Implement the publisher and receipts

Create `DeliveryOutbox(db, profile_id)` on an owned SQLite connection, then `AutomaticInformation(runtime, outbox, publish)`. Bind its interval with `information.configure(verified_native_snapshot)`. Schedule `information.step()` on the runtime's owning worker: one logical read at a time. Its `tick()` completes a scan and is not the live-worker scheduling primitive. Keep foreground commands and SQLite operations serialized with this worker.

`publish` is a **host callback you must implement**, with this calling contract:

```text
publish(records, advances=None, handoff_receipts=None)
```

It must durably enqueue the batch before returning. Build an information message with `type="information"`, the correct source `run_ref`, and `records`. Build aligned private effects:

```python
effects = {
    "public_results": [
        {"op": record["source"], "result": record["result"]}
        for record in records
    ]
}
identifier = outbox.enqueue(
    message, effects=effects,
    advances=advances, handoff_receipts=handoff_receipts,
)
```

This is a publisher fragment. Preserve record order, source identity, observation timestamps, patch metadata and handoff identities. `enqueue` handles reference-notice metadata and may normalize or suppress a batch; transmit the persisted pending message from `outbox.pending()`, not the pre-enqueue input. Keep `effects` and internal receipt metadata private. When recovering historical knowledge notices, retain their source run instead of relabeling them as current observations.

For each pending item `(identifier, message, effects, received)`:

1. Encode and deliver the persisted public message through the host transport with its delivery identifier.
2. Obtain a trusted receipt for the actual delivered content. Flush alone does not prove model consumption. Call `outbox.receive(identifier, omitted_records=...)` only for that receipt.
3. Apply the returned, actually delivered `public_results` using `runtime.after_public_delivery(result, op)` and the corresponding Tip effects. Preserve recovery/idempotence if a crash occurred between receipt and effects.
4. Only after effects are complete call `outbox.processed(identifier)`. It releases the bulky payload while retaining receipt bookkeeping; do not call it just because a send was attempted.

Do not silently omit old selection/card records. The existing `omitted_records` mechanism requires exact index/source/feedback-revision matches and aligned effects. Apply snapshot/patch updates only to their correct run/source/base. Historical learning is reusable evidence, not a current selection or action permit. Paging and larger read budgets belong to the host, not a chain of model tool calls.

### 5.3 Process a foreground action

The host action loop must preserve this ordering on its owning worker:

```text
validate current request owner/run and readiness
information.flush_partial(before_action=True)
information.flush_learning()
information.preview_command(request)
result = runtime.handle(request)
information.remember_authoring(result)
information.remember_target(result)
feedback = information.action_feedback(request, result)
encode and deliver the final public response + feedback; flush
apply runtime.after_public_delivery only to the results actually delivered
resume automatic sampling / receipt processing, or enter shutdown on failure
```

This is host integration pseudocode, not an exported function. Do not run preparation reads when the runtime is suspended, failed or stopped. Preserve successful action results even when a later feedback read fails; do not replay the action to repair missing feedback. `action_feedback` returns fresh selection/card information where possible and explicitly dated background fields; `non_atomic=True` means it is not a single simultaneous world snapshot.

### 5.4 Serialize identities without precision loss

Use [`decode_request`](../scapi/wire_identity.py) for incoming executable identity arguments and `encode_public_frame` for outgoing **frames**, before JSON serialization. Python ticket fields remain integers internally. On the wire `process_creation` is a canonical decimal string; never round it through a JavaScript `Number` or recursively convert unrelated user/experience text.

```python
public_frame = encode_public_frame({
    "type": "response",
    "id": request.get("id"),
    "result": result,
    "feedback": feedback,
})
```

The frame shape matters: passing a naked result dict to `encode_public_frame` does not select its response-encoding branch. Do not forward private logs, developer snapshots or diagnostic exceptions as player observations.

### 5.5 Keep role experience in explicit episode and global scopes

`role_experience_paths` and `shared_experience_paths` are role-keyed mappings using `operator`, `tactics` or `interaction`. `ReadyRuntime` creates one `ExperienceStore(store, *, profile_id, shared_store=None, shared_profile_id=None)` per configured role. The primary episode notebook is separate from the optional global notebook, with a distinct database and profile. Do not point two roles at one writable notebook or merge the scopes.

Global notes are **not** preloaded automatically at session start. Query the global scope explicitly with `list(scope='global')` on that role's `ExperienceStore`, supplying the required `author_role` (for example `list(author_role=<role>, scope='global')`), or use a selected `read(..., scope='global')`; never enumerate the whole notebook as startup context. A promotion moves the latest episode revision into the shared global notebook only when that store is configured.

`execute_role_experience(store, *, role, args, runtime_run_ref, offline=False)` is the validated role boundary for `record`, `revise`, `refute`, `promote`, `delete` and `list`. Keep `author_role` host-owned. The offline [role experience CLI](../tools/role_experience_cli.py) reads one JSON object from standard input and never attaches to a game:

```powershell
'{"operation":"list","scope":"global","limit":16}' | & $Python -B tools/role_experience_cli.py --role operator --shared-experience-dir "<ABSOLUTE_SHARED_DIR>"
```

In `official_standard` mode the CLI requires the explicit absolute shared directory. Offline requests can list or maintain global notes, but cannot write episode notes or promote without a connected episode; pass `scope='global'` for an offline `record`. Do not claim that the CLI starts or resumes gameplay.

### 5.6 Interpret automatic terrain, stationarity and contacts

`AutomaticInformation` publishes `screen_terrain` and `screen_terrain_creep` from lawful view samples. The screen form is compact: one character per 16x16px cell with an origin, rows, camera and overlays; current creep is distinct from last-observed fog creep. It is a screen projection, not a complete map, and missing cells remain unknown.

`TerrainReference.read(args)` accepts `format='cells'` (default) or `format='grid'` for source terrain. The grid form renders a bounded rectangle from stored history, not from a current screen read; unstored tiles stay `?` and remembered creep remains fog creep.

Eligible own-unit rows can carry a derived coarse `stationary_s` of `15`, `30`, `60`, `120`, `180`, `300` or `600` seconds. It is computed only from fresh unpaused own observations; observation gaps do not add time, and movement or disappearance removes the bucket. It is not a wall-clock history, an order history or proof that a unit is uncontrolled.

A captured `offscreen_non_enemy_contact` event reports `relation` as `allied`, `rescuable` or `neutral`. It is informational, not an enemy threat; it identifies no attacker and grants no action permission.

## 6. Instructions for the game-playing AI

Use the integration's advertised transport. Request schema:

```json
{"op":"operation_name","args":{},"id":"request-correlation-id","purpose":"short action intent"}
```

`id` is correlation metadata, not an idempotency key that permits replay. Allowed operations are implemented by `ReadyRuntime.dispatch`; operations in `INFORMATION_OPS` remain host-owned even if dispatch supports them.

Consume automatically delivered observations first. Retain their run, timestamps, availability and provenance. Keep the newest complete current tickets from action feedback or information delivery. Never manufacture a ticket from remembered IDs or discard fields to make it fit.

| Intent | Request sequence / argument rule |
| --- | --- |
| Select an observed actor | `select_at` with world-pixel `x`, `y`, optional `add`; or `select_box` with world-pixel `x1`, `y1`, `x2`, `y2`. Require an actual operable point/rectangle in the current view. Use the returned `selection` or newer feedback ticket. |
| Move or interact | `move_selected`, `right_click`, `attack_move`, `patrol`: complete selection `ticket`, world-pixel `x`, `y`, optional boolean `queued`. |
| Use the minimap | `move_minimap`, `attack_move_minimap`, `patrol_minimap`: selection `ticket`, **minimap** `pixel_x`, `pixel_y`, optional `queued`. Do not substitute world pixels. |
| Train/build/research/use a skill | Consume the current automatically delivered command card. Choose an actually enabled row; `activate_button` uses its complete `button` and optional `queued`. A remembered name/cost does not establish current availability. |
| Supply a target | If activation returns `target_required`, use its complete `target` in `target_at` with `x`, `y`. For placement use the automatic preview's coordinate/footprint contract; distinguish anchor coordinates from `engine_click_point`. |
| Cancel pending targeting | `cancel_pending` with empty args. This does not cancel an already issued order or ongoing production. |
| Cancel one production item | `cancel_production` with current `production` ticket and `slot`; do not infer completion from enqueue alone. |
| Unload a passenger | `unload_one` with the current enabled cargo row's complete `passenger` ticket. |
| Finish playing | `finish` with empty args requests finishing. The host must still perform verified shutdown. |

For example, after the host has delivered a valid current selection ticket and a lawful destination, the request template is:

```python
request = {
    "op": "move_selected",
    "args": {"ticket": current_selection_ticket, "x": world_x, "y": world_y},
    "id": unique_request_id,
    "purpose": "Move the selected actors to the observed destination",
}
```

The variables are actual delivered values, not placeholders to send literally. Do not execute this example as an unsolicited game action.

Additional semantics are in `Stage5.control_guide()` and the current [implementation](../scapi/stage5.py): selection is at most twelve actual units; own selected units can receive permitted offscreen orders, while initiating production/research/upgrades/construction requires actors on screen. World pixels, 32-pixel tiles, 8-pixel walk cells and minimap pixels are distinct domains. Native validation remains authoritative for each action. Absence, `unavailable`, unknown terrain and old sightings are not zero, empty space or proof of safety. Existing human pause belongs to the human.

### Interpret outcomes before choosing another action

| Result / reason | Required interpretation |
| --- | --- |
| `selected` / `applied` | That operation was accepted at its stated layer; use returned/current feedback and verify the intended visible effect as needed. |
| `enqueued` | Submitted to the game's command queue; movement/building/training has not thereby completed. Wait for lawful feedback. |
| `target_required` | The operation needs another target step with the returned target ticket. |
| `rejected` with stale ticket/generation/epoch or different process | Do not edit ticket fields. Receive fresh context/tickets; let the host rebind if the session changed. |
| `read_current_enabled_command_card` | Wait for a current automatically delivered enabled card and use its button ticket. Do not call the blocked information operation through the player endpoint. |
| `information_is_automatic` | Stop querying that operation as a player request; consume the automatic channel. |
| `startup_tips_not_delivered` | Host startup delivery/receipts are incomplete. Do not set `startup_complete` manually. |
| `outcome_unknown`, timeout, uncertain installation or `automatic_retry=False` | Preserve the attempt and report the uncertainty. Do not automatically resubmit. Only fresh independent observation can resolve what happened. |
| `runtime_stopped`, `runtime_suspended`, menu/session pending | Let the host complete the appropriate stop/rebind/permit flow. Do not reopen action admission yourself. |

The internal `observation_pending` special case is a definite non-executed operation handled by the API after durable observation persistence. It is not permission to create a general retry wrapper.

## 7. Menu operations and session changes

Optional player requests `save_game`, `load_game`, `restart_mission` require actual host capabilities `save`, `load`, `restart`. `objectives` is also a lifecycle capability, but its information path is host-owned. Advertise only capabilities you have implemented.

`Lifecycle.request(...)` creates a permit; it does not click menus. The host must:

1. Quiesce the bound runtime and call `host_begin(op_id, session=..., quiescent=...)` with real proof for exactly `actions_disabled`, `building_closed`, `events_stopped`, `no_request_in_flight`, `native_interval_elapsed`.
2. Before **each** actual menu input call `claim_menu_input(op_id)` and require `status="allowed"`.
3. Observe the result and call `host_finish(op_id, outcome=..., evidence=...)` using the exact evidence fields in [lifecycle.py](../scapi/lifecycle.py). File existence or a click acknowledgment is not sufficient by itself.
4. For load/restart, retain same-process identity, verify the new epoch and capture binding, reject old tickets, then `bind_session` to the receipt's expected new session. `rebind_required` does not permit using the old API state.
5. Before resuming after any resolved menu operation, including save/objectives, restore and verify capture and owned API workers. If `runtime.suspend()` was used, construct fresh API/EventLog objects and call `runtime.attach(...)` only after the verified capture handshake and applicable lifecycle binding. Complete startup delivery/readiness and restore the delivered settings before re-enabling actions. `host_finish` returning `gameplay` does not perform these steps; constructing fresh Python objects does not require reinstalling an already healthy resident image.
6. Reconcile an unknown outcome only through `host_reconcile` with fresh independent evidence, not replayed menu input. Preserve the 300-second save cooldown and registered save-slot rules.

Do not save, restart or enter the next mission merely because an operation is available. Follow the user's requested gameplay scope. Default v53 has no generally verified victory-detection promise; a v50 diagnostic field is not proof of automatic full-mission progression.

## 8. Stop and release ownership

Design shutdown before enabling gameplay. `finish`, `runtime.finished`, `Stage5.close()`, a collector exit or a loader exit is not complete native withdrawal.

For the current v53 / ABI 27 binding:

1. Freeze new player actions and stop scheduling new reads; disable action admission and settle/inspect in-flight work without replaying unknown operations.
2. Stop the owned collector through its normal stop mechanism and await actual exit. Require matching identity/generation, successful capture stop, final durable drain, closed journal and no cleanup error. A final `running=False` flag alone is insufficient.
3. Preserve a still-valid native client and the pre-install `original` snapshot. Construct one `NativeCleanupAttempt(binding=binding, original=original)` for this ownership scope. Use the actual positive integer identity/generation fields and string run ID described above.
4. Call `attempt.execute(client, quiescence=real_quiescence, drain_after_stop=host_callback)`. The callback is called as `host_callback(client, binding_copy)`: after capture/building producers stop and **before detach**, it must finish building-worker closure/flush and completed observation delivery. It returns the same binding and truthful `building_worker_closed=True`, `building_flushed=True`, `completed_delivery_pending=0`.
5. Accept native withdrawal only when the report has `fully_withdrawn=True`. The verifier checks original code/IAT, hooks, capture/fault/ring state, resident inventory and no player action during cleanup. Complete remaining owned store/stream/worker closure and report any failure.
6. On timeout or unknown publication, preserve the attempt. Do not replace it with a new attempt to repeat detach; use `verify_native_cleanup(...)` for read-only verification with real current evidence.

Do not close the only client needed for restoration verification before cleanup finishes. Inspect `Stage5.close()` and `ReadyRuntime.suspend()` when wiring ownership: they have their own worker/store/disarm effects, and are not substitutes for the restoration protocol. Native images may remain inert until ordinary game exit.

For a later mission, `build_episode_candidate(compiler=...)` creates a unique v53 image/loader pair and returns `(str(dll), Path(loader))`; v52/v51/v49 episode names remain recognized for cleanup only. It does not attach the candidate. The host must bind all transports to that approved candidate, prove the previous image fully withdrawn, preserve old resident identities and establish a fresh installation/capture/runtime scope. Do not treat this helper as an unlimited, already validated cross-mission launcher.

## 9. Verify and report at the achieved level

Choose checks appropriate to the user's requested task. Do not execute the commands or game flow in this guide merely because you are reviewing or editing the repository.

| Evidence obtained | What you may report |
| --- | --- |
| Source/signature review | Documented interface and identified integration requirements. |
| Import/build success | Local dependency/build success. |
| Fresh installation/capture/readiness receipts | Those individual live stages; not successful gameplay. |
| Actual permitted action plus observed effect and verified native stop through the intended host | The tested core integration flow, with its exact scope. |
| Full requested gameplay and explicit user acceptance | Only the goal and environment actually accepted. |

When blocked, report the exact stage, source/result evidence, missing prerequisite and next concrete action. Keep the first failure; do not replace it with a later readiness symptom. Do not claim a ready-to-play integration based on generated glue, static checks or successful DLL compilation.

The exported repository was initially checked for source/dependency/import consistency and offline native compilation. Its standalone live attach, full stop, visible cursor recovery and cross-mission behavior are not established by that extraction. A new host must earn its own evidence.
