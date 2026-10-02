# LAN audio, observation delivery and recovery: mechanism audit

Audited revisions, each against `040ab3d9f375671f7d80e3bc32454a7e54b2081a`:

- Audio routing: `5e5da1ba35eb82028549f6e5cd86d8de5e98e7cc` ([#3974](https://github.com/rigplane/rigplane-core/pull/3974)).
- Observation delivery: `370aebbd54cd4f3681391e58e4e2d7316be3c3a7` ([#3975](https://github.com/rigplane/rigplane-core/pull/3975)).
- Recovery: `573206c5edd5a722b905c386eced8a05bef57b3b`.

Method: `mechanism-audit`, ordered definition, prior-ruling, in-flight and consumer collection before steelman and verdict. This is a bounded helper-level audit of changed mechanisms and their direct consumers, not a whole-module or repository dead-code sweep. Collection and adjudication were read-only; implementation and CI evidence were consumed separately. No hardware acceptance is inferred.

## Inventory and liveness

Audio introduces one enum member and changes three existing functions. It adds no instance attribute, module constant or execution wrapper. `audio/route.py:DataModePolicy.DATA1_LAN` has one definition, one production assignment and two production uses: `resolve_audio_route`, `rigctld_wsjtx_policy` and the `web/handlers/audio.py:browser_tx_audio_facts` allowlist. The enum remains an importable surface; absence of an arbitrary downstream reader would not justify deletion. The route functions have CLI and Web production consumers, in addition to profile, packet-mode and browser-facts regression tests.

Delivery adds no symbol or stored state. `core/state_pipeline_contracts.py:ChangeSet.observed_paths` is defined by the shared contract, populated by `core/state_store.py:StateStore.apply` and meter coalescing, and consumed by acquisition credit and the existing runtime notifier. Literal reads of the established field exist in production and tests; it is serialized metadata, not test-only state.

Recovery introduces one instance map and one query method. `runtime/session_lifecycle.py:CoreRadioSessionLifecycle._recovery_waiters` has three production state writes (initialization, caller registration, matching-entry deletion) and two identity reads (`is_current_recovery_awaited_by` and cleanup). The query method has one production consumer, `runtime/_civ_rx.py:CivRuntime.stop_data_watchdog`, plus direct regression consumers. The added `CivRuntimeHost._session_lifecycle` annotation describes the existing owner; it creates no runtime service. Literal searches found no dynamic registry or serialization consumers for the map/query. No deletion inference depends on unseen downstream consumers.

Collection used native `rg` for definitions and both read/write directions, and pinned `git diff`/`git show`. Behavior searches included route/policy/input, notify/observe/freshness, and recovery/cancel/drain/shield. Existing canonical targets and related documentation were inspected before adjudication.

## Deletions

No new dead or vestigial element was established in the changed scope. Removing the erroneous ephemeral-bind fallback and its helper is a correctness repair of formerly live behavior, not a dead-code finding. No automatic deletion or consolidation is recommended.

## Consolidations and ownership

### F1 — profile-dependent LAN DATA routing

Verdict: already shared. Rank: parallel-mechanism hypothesis cleared.

Elements/definition: `audio/route.py:resolve_audio_route`, `rigctld_wsjtx_policy`, `DataModePolicy`; consumer `web/handlers/audio.py:browser_tx_audio_facts`.

Consumers: CLI packet-mode configuration and Web capability projection use the shared route decision; packet sequencing continues in the existing rigctld handler. The Web allowlist admits a resolved policy and does not independently resolve radio inputs.

Steelman: an explicit profile input plus real supported setter identifies the single-DATA LAN case without guessing from transport alone. An additive enum case reuses the established factory and avoids another command executor or profile parser. Existing multi-DATA and legacy behavior is preserved.

Divergence: the previous blanket single-DATA legacy fallback omitted a declared LAN input; no competing Core routing implementation was found. Prior ruling: no scoped ruling opposing this extension was found. In-flight: the factory already exists and has both CLI and Web consumers. Required surface: exists. Depends on: none. Confidence: high. Falsifier: another production resolver for the same declared route, or a supported profile that contradicts the predicate. Fix class: none. Actionable: no additional consolidation; retain the shared decision.

### F2 — fresh metadata for unchanged observations

Verdict: already shared. Rank: displaced-storage hypothesis cleared.

Elements/definition: `core/state_pipeline_contracts.py:ChangeSet`, `core/state_store.py:StateStore.apply`, `runtime/_civ_rx.py:CivRuntime._notify_state_store_changed`, `web/server.py:WebServer._on_radio_state_change`.

Consumers: accepted observations feed the existing state-store notification and Web delivery. Acquisition credit and coalescing already consume the same accepted-observation fact.

Steelman: observation freshness and semantic value change are different facts. Including the canonical accepted path preserves that distinction, semantic revision and rejection rules without adding a second store, poller or clock. Web's existing 50 ms throttle owns outbound coalescing.

Divergence: the notifier previously ignored accepted same-value observations; the encoder could represent them but was not awakened. Prior ruling: `docs/internals/legacy-state-writer-inventory.md` assigns canonical delivery to `state_store_changed`; legacy events remain compatibility notifications. In-flight: canonical metadata and the notifier already exist. Required surface: exists. Depends on: none. Confidence: high for ownership; notification cost under every possible consumer remains unmeasured. Falsifier: an unbounded consumer or producer bypassing the existing throttle. Fix class: none. Actionable: no new delivery layer.

### F3 — recovery cleanup and endpoint identity

Verdict: C, legitimately local to the shared runtime lifecycle. Rank: parallel-owner hypothesis cleared.

Elements/definition: `runtime/session_lifecycle.py:CoreRadioSessionLifecycle.soft_reconnect`, `is_current_recovery_awaited_by`, `_teardown`; `runtime/_civ_rx.py:CivRuntime.stop_data_watchdog`, `_watchdog_recover`; `runtime/_control_phase.py:ControlPhaseRuntime.soft_reconnect`.

Consumers: the same lifecycle serves direct and watchdog recovery callers. The watchdog stop path queries the actual caller-to-recovery relationship; no Web client owns this cancellation mechanism. Transport bind and conninfo advertisement remain in the established control/transport layer.

Steelman: the query must distinguish a joined watchdog waiter from a registered watchdog that has not joined. A precise caller-to-captured-task map supplies that missing relationship while existing cancel/drain helpers retain ownership. `asyncio.shield` alone would suppress cancellation propagation required by an external stop and would need additional ownership logic. The map is cleaned in `finally` with task identity checks. An explicit `CancelledError` path prevents directly or indirectly cancelled recovery from rearming a watchdog.

Divergence: child recovery previously cancelled its own waiter, and cancellation could rearm another watchdog during release. The ContextVar draft was rejected and removed. The port fallback could bind somewhere other than the advertised CI-V destination; deleting it reuses bounded retry/exhaustion rather than inventing another port-selection loop.

Prior ruling: `docs/architecture/2026-06-22-radio-session-lifecycle.md`, #1217, requires exhaustion through full release and soft recovery reuse of control/token. In-flight: lifecycle task ownership and transport advertisement already exist. Required surface: one read-only waiter-topology query; no native equivalent preserving the present external-cancellation contract was identified. Depends on: matching-entry cleanup and cancellation without rearm. Confidence: high for covered task graphs. Falsifier: a different caller topology that leaves a waiter/watchdog alive or permits late reopen. Fix class: none beyond the reviewed repair. Actionable: retain the single lifecycle owner; no second recovery framework.

## Validation evidence and weakest link

The audio candidate has genuine four-case RED and 43-case focused GREEN. Delivery has ingress/coalescer RED, passing rejected-generation negatives, 471 fixed-production checks and passing final-head ingress/format checks. Recovery has independent blocked-review counterexamples, their subsequent RED reproductions, and 60 final-head focused checks including real rearm, coalesced/unjoined callers, external and cross-radio stop, successor cleanup and advertised-port collisions. Fresh independent code review passed each pinned candidate. Required PR CI and merge integration are separate acceptance gates.

Weakest link: recovery waiter topology outside the covered callers; inspect that edge first if another cancellation/reopen symptom occurs. High-rate notification cost across all downstream clients and physical radio behavior are also outside this static audit. Nothing here closes a complete customer complaint or proves hardware behavior.

## Cleared

Shared profile routing, canonical accepted-observation metadata, Web throttling, lifecycle task retirement, bounded reconnect attempts and advertised-port ownership were examined and retained. No extra Core execution framework, duplicated store or second recovery loop is required by these changes.
