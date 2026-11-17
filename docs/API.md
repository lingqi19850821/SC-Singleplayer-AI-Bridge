# API integration

## Layers

| Layer | Entry | Responsibility |
| --- | --- | --- |
| Restricted game API | `scapi.stage5.Stage5` | Lawful observations, tickets and real selected-unit actions. |
| Public dispatch | `scapi.player_runtime.ReadyRuntime` | Validate requests/tickets and dispatch API operations; no model creation or tactical decisions. |
| Automatic information | `AutomaticInformation`, `DeliveryOutbox` | Sample allowed sources, persist notifications, track delivery and publish snapshots/patches. |
| Events | `scapi.events.EventLog`, `tools.stage4_event_journal.Journal` | Read and persist an explicitly bound native capture run. |
| Knowledge/history | `PanelKnowledge`, `TerrainReference`, `ExperienceStore`, `TipsRuntime` | Preserve lawful observations, provenance, learned facts and gated Tips. |
| Lifecycle | `scapi.lifecycle.Lifecycle` | Persist save/load/restart requests, cooldowns and trusted-host receipts. |
| Native internals | `tools.stage5_client.Client` | Fixed mailbox and process transport; private fields must not be forwarded as player observations. |

The source-derived [reference](API_REFERENCE.md) lists exact constructors and public methods. `Stage5` inherits the action and observation methods of `Stage4` and `Actions`.

## Construction and dispatch

After an external trusted host has verified the exact game process and prepared the bridge/capture/lifecycle, construct `Stage5(pid, knowledge_path=..., bookmark_path=..., building_memory_path=...)` with explicitly chosen writable profile databases. These are caller-controlled local paths, not player-supplied tool arguments. A PID alone is not a sufficient identity check; retain the exact process creation time, current epoch and run identity.

`ReadyRuntime(api, events, log, *, profile_id, lifecycle, run_ref, capabilities=(), ...)` accepts these already-bound objects. `handle(request)` takes `{op, args, id?, purpose?}` and returns a public result. `dispatch(op, args)` is the internal dispatch entry; the host must preserve the same initialization, ownership, lifecycle and authorization conditions. Only advertise lifecycle capabilities the host actually implements.

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

## Trusted native host and shutdown

This repository does not include the removed desktop application's startup/role/CU supervisor. It provides its API-facing safety primitives:

- `tools.native_loader_runtime.prepare_loader_runtime`: the original private execution-copy and token checks. A successful preparation explicitly does not prove access to the game. Do not bypass it by treating direct raw-loader execution as an equivalent startup path.
- `tools.native_episode_artifacts`: unique next-episode images and exact-path loader validation. Building requires `compiler=...` or `SCAPI_TCC`; the original no-overwrite and reparse-path checks remain.
- `tools.stage4_event_collector.StreamReader` and `main(client_factory=...)`: capture/journal transport. For v49 supply the matching `tools.stage5_client.Client`; the legacy module CLI default is not a v49 launcher.
- `tools.session_native_cleanup.NativeCleanupAttempt` / `verify_native_cleanup`: current binding, original bytes, capture drain, native withdrawal and restoration evidence. See the exact signatures in the reference; do not substitute process exit for these checks.

The external host must verify the exact process creation time, supported executable/layout, single installation, current ABI, original code/IAT, capture generation, writable profile ownership and ready state before enabling actions. The loader refuses a second install into an already resident image. It does not unload an active bridge to retry an unknown operation.

`Stage5.close()` closes its connections and stores; it is not a complete native uninstall. The host must disarm actions, settle the in-flight request, stop/drain capture and verify hook/IAT restoration with fresh evidence. DLL memory can remain resident and inert until normal game exit. Stop cannot be inferred from an empty queue, a closed Python object or a loader exit code alone.

## Supported scope and limitations

Default transport is v49 / ABI 27. The v50 visible-phase subclass/header is retained as an explicit diagnostic candidate and requires its matching build. It is not the default and does not establish general victory detection. Exact supported target remains the 1.05 x86 adapter and `D:/Starcraft/starcraft.exe`; changing that string alone does not port the binary layout.

This extraction was checked offline only. Live attach, game actions, complete stop, full missions and cross-mission continuation have not been verified from this standalone checkout. Excluding the application is intentional; no standalone ready-to-play application is claimed.
