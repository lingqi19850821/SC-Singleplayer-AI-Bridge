# API integration

## Layers

| Layer | Entry | Responsibility |
| --- | --- | --- |
| Restricted game API | `scapi.stage5.Stage5` | Lawful observations, tickets, selected-unit actions and scoped static knowledge. |
| Public dispatch | `scapi.player_runtime.ReadyRuntime` | Validate requests/tickets and dispatch API operations; no model creation or tactical decisions. |
| Automatic information | `AutomaticInformation`, `DeliveryOutbox` | Sample allowed sources, persist notifications, track delivery and publish snapshots/patches. |
| Events | `scapi.events.EventLog`, `tools.stage4_event_journal.Journal` | Read and persist an explicitly bound native capture run. |
| Knowledge/history | `PanelKnowledge`, `TerrainReference`, `ExperienceStore`, `TipsRuntime` | Preserve lawful observations, provenance, learned facts, terrain history and gated Tips. |
| Lifecycle | `scapi.lifecycle.Lifecycle` | Persist save/load/restart requests, cooldowns and trusted-host receipts. |
| Native internals | `tools.stage5_client.Client` | Fixed mailbox and process transport; private fields must not be forwarded as player observations. |

The source-derived [reference](API_REFERENCE.md) lists constructors and public methods. `Stage5` inherits the action and observation methods of `Stage4` and `Actions`.

## Construction and dispatch

After an external trusted host has verified the exact game process and prepared the bridge/capture/lifecycle, construct:

```python
Stage5(pid, *, knowledge_path=None, bookmark_path=None, building_memory_path=None,
       shared_knowledge_path=None, knowledge_mode='official_standard')
```

The path arguments name caller-controlled writable profile databases, not player-supplied tool arguments. A PID alone is not a sufficient identity check; retain the exact process creation time, current epoch and run identity.

With `knowledge_mode='official_standard'` and an explicit `shared_knowledge_path`, `Stage5` attaches a shared library of previously verified static unit/weapon/skill/supply facts, including lawfully learned `detector: True`. It does not preload an encyclopedia, roster or current unit truth. `isolated_scope` keeps all learning in the supplied episode database and never attaches the shared library. A shared database must still be chosen explicitly; there is no private default path.

Detailed selected-panel learning waits for the legal two-second HUD panel dwell. That dwell is camera-independent: moving the camera alone does not restart it, while a changed selection or visible rule set requires a fresh interval. A homogeneous selected own group of 2–12 units may learn shared command-card labels after its two-second dwell. Group-card learning records visible labels only; it does not establish single-unit stats, hidden cargo/production facts, current enabled state or action permission.

`ReadyRuntime(api, events, log, *, profile_id, lifecycle, run_ref, capabilities=(), tutorial_depot_limit=None, tutorial_new_depot_intents=None, diagnostics='full', role_experience_paths=None, shared_experience_paths=None, shared_experience_profile='official_standard')` accepts already-bound objects. `handle(request)` takes `{op, args, id?, purpose?}` and returns a public result. `dispatch(op, args)` is the internal dispatch entry; the host must preserve the same initialization, ownership, lifecycle and authorization conditions. Only advertise lifecycle capabilities the host actually implements.

`role_experience_paths` and `shared_experience_paths` are role-keyed mappings using `operator`, `tactics` or `interaction`. Each role receives its own episode notebook and, when mapped, its own persistent global notebook. The underlying store is:

```python
ExperienceStore(store, *, profile_id, shared_store=None, shared_profile_id=None)
```

The episode scope is the primary store; `shared_store` enables the separate global scope. The two databases and profiles remain distinct. Global notes are not imported or preloaded at session start. Query them explicitly with `list(scope='global')` on that role's `ExperienceStore`, supplying the required `author_role` (for example `list(author_role=<role>, scope='global')`), or use an equivalent selected `read(...)`. Promote only through the configured shared store.

`execute_role_experience(store, *, role, args, runtime_run_ref, offline=False)` is the validated role boundary supporting `record`, `revise`, `refute`, `promote`, `delete` and `list`. The offline helper [tools/role_experience_cli.py](../tools/role_experience_cli.py) reads one JSON request from standard input and never attaches to a game. In `official_standard` mode it requires an explicit absolute `--shared-experience-dir`. Offline operations can list or maintain global notes, but they cannot write episode notes or promote without a connected episode; pass `scope='global'` explicitly for an offline `record`.

Before accepting player operations, call `startup_page(...)`, completely deliver and flush the returned startup content, then acknowledge the delivered Tip IDs with `delivered_startup(ids)`. Repeat for remaining pages until `startup_complete` is true. Acknowledgement belongs after actual delivery; without it, `handle()` remains blocked with `startup_tips_not_delivered`.

`scapi_action`, `scapi_reference`, `scapi_context_ready` and GUI/CU session tools from an embedding application are not top-level functions in this repository. An integrator supplies its own transport and reference adapter. `player_context.startup_context` retains the optional adapter naming convention and points to this repository's interface reference; the host must map it to supported endpoints before advertising it.

Automatic information uses `AutomaticInformation(runtime, outbox, publish)`. The host owns sampling, output delivery and receipts. `DeliveryOutbox.receive(id, ...)` and `processed(id)` are trusted delivery operations; a sent message or queued request does not prove a model consumed it. Apply patches within the same run/source, retain original observation times, and never treat historical observations as current action permission.

## Tickets, actions and information

- Preserve complete `SelectionTicket`, `ButtonTicket`, `TargetTicket`, `ProductionTicket` and `CargoTicket` values. JSON `process_creation` is an exact decimal string through `scapi.wire_identity`; do not round it through a JavaScript number.
- Initial selection must be in the operable viewport, excluding UI. Already-selected own units can receive allowed offscreen orders. Point/world targets, minimap integer pixels, anchors and footprints are distinct coordinate domains.
- Data returned by the public API is constrained by visibility, lawful observation, actual selection and configured attention. Internal native structures are not a player-facing query API.
- A command accepted/enqueued is not proof of movement, construction or production completion. An unknown command outcome must not be automatically retried. At most one request owns the mailbox.
- Observation budgets, pagination limits and legal intervals remain part of the exact method contracts. Learned facts and remembered terrain retain their provenance; absence/unknown is not zero or proof of safety.
- Existing human pauses must remain human-owned. Save/load/restart capabilities require a trusted external executor and fresh binding after a session change.

## Automatic terrain, stationarity and contacts

`AutomaticInformation` publishes `screen_terrain` and `screen_terrain_creep` records from lawful view samples. The screen projection is compact: one character per 16x16px cell, with rows, origin, camera, ramp/no-build/blocking overlays and current versus last-observed creep separated. It is a screen view, not a complete map; missing cells remain unknown.

`TerrainReference.read(args)` accepts `format='cells'` (default) or `format='grid'` for source terrain. The grid form is rendered from stored history and is bounded to an aligned rectangle; it is not a live screen read. An unstored tile remains `?`, and remembered creep in that history is fog creep.

Eligible own-unit rows may carry a derived coarse `stationary_s` value in `15`, `30`, `60`, `120`, `180`, `300` or `600` seconds. It is computed only from fresh, unpaused own observations; observation gaps do not add time, and movement or disappearance removes the bucket. It is not a wall-clock claim, an order history, or proof that a unit is uncontrolled.

Captured event type `offscreen_non_enemy_contact` reports `relation` as `allied`, `rescuable` or `neutral`. It is informational: it is not an enemy threat, does not identify an attacker and grants no action permission.

## Trusted native host and shutdown

This repository does not include the removed desktop application's startup/role/CU supervisor. It provides its API-facing safety primitives:

- `tools.native_loader_runtime.prepare_loader_runtime`: the original private execution-copy and token checks. A successful preparation explicitly does not prove access to the game. Do not bypass it by treating direct raw-loader execution as an equivalent startup path.
- `tools.native_episode_artifacts`: unique next-episode images and exact-path loader validation. Building requires `compiler=...` or `SCAPI_TCC`; the original no-overwrite and reparse-path checks remain. v53 images are current; v52, v51 and v49 episode names remain recognized for cleanup of prior attempts.
- `tools.stage4_event_collector.StreamReader` and `main(client_factory=...)`: capture/journal transport. For v53 / ABI 27 supply the matching `tools.stage5_client.Client`; the legacy module CLI default is not a v53 launcher. A v50 diagnostic build requires its matching visible-phase client.
- `tools.session_native_cleanup.NativeCleanupAttempt` / `verify_native_cleanup`: current binding, original bytes, capture drain, native withdrawal and restoration evidence. The verifier supports v53 / ABI 27 and the recognized v52/v51/v49 legacy candidates. See the exact signatures in the reference; do not substitute process exit for these checks.

The external host must verify the exact process creation time, supported executable/layout, single installation, current ABI, original code/IAT, capture generation, writable profile ownership and ready state before enabling actions. The loader refuses a second install into an already resident image. It does not unload an active bridge to retry an unknown operation.

`Stage5.close()` closes its connections and stores; it is not a complete native uninstall. The host must disarm actions, settle the in-flight request, stop/drain capture and verify hook/IAT restoration with fresh evidence. DLL memory can remain resident and inert until normal game exit. Stop cannot be inferred from an empty queue, a closed Python object or a loader exit code alone.

## Supported scope and limitations

Default transport is **v53 / ABI 27** with a 676-byte `ScapiState`. The v50 visible-phase subclass/header is retained only as an explicit **v50 / ABI 28** diagnostic candidate and requires its matching build; it is not the default and does not establish general victory detection. Exact supported target remains the 1.05 x86 adapter and `D:/Starcraft/starcraft.exe`; changing that string alone does not port the binary layout.

This extraction was checked offline only. Live attach, game actions, complete stop, full missions and cross-mission continuation have not been verified from this standalone checkout. Excluding the application is intentional; no standalone ready-to-play application is claimed.
