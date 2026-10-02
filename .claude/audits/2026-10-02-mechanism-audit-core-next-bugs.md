# LAN audio, observation delivery, recovery and acquisition: mechanism audit

Audited revisions, each against `040ab3d9f375671f7d80e3bc32454a7e54b2081a`:

- Audio routing: `5e5da1ba35eb82028549f6e5cd86d8de5e98e7cc` ([#3974](https://github.com/rigplane/rigplane-core/pull/3974)).
- Observation delivery: `370aebbd54cd4f3681391e58e4e2d7316be3c3a7` ([#3975](https://github.com/rigplane/rigplane-core/pull/3975)).
- Recovery: `573206c5edd5a722b905c386eced8a05bef57b3b`.

Additional acquisition candidate, against integrated `b4c6862d7b06084abe1113a2cb71c3b090806063`: `7c5d520ce966e4a9b12bd71c59165a1a00b07511`.

Additional grouped-cadence candidate, against integrated `14d01685434c33e81a8bd93561fb25f4d018dec7`: `49414d023ea67416e787a9839f6ec17826a1be72`.

Method: `mechanism-audit`, SHA-256 `10be2f400ab1f3066e03ac4944717b69798b6ca0d82db85249445be7645ff6ad`, ordered definition, prior-ruling, in-flight and consumer collection before steelman and verdict. This is a bounded helper-level audit of changed mechanisms and their direct consumers, not a whole-module or repository dead-code sweep. Collection and adjudication were read-only; implementation and CI evidence were consumed separately. No hardware acceptance is inferred.

## Inventory and liveness

Audio introduces one enum member and changes three existing functions. It adds no instance attribute, module constant or execution wrapper. `audio/route.py:DataModePolicy.DATA1_LAN` has one definition, one production assignment and two production uses: `resolve_audio_route`, `rigctld_wsjtx_policy` and the `web/handlers/audio.py:browser_tx_audio_facts` allowlist. The enum remains an importable surface; absence of an arbitrary downstream reader would not justify deletion. The route functions have CLI and Web production consumers, in addition to profile, packet-mode and browser-facts regression tests.

Delivery adds no symbol or stored state. `core/state_pipeline_contracts.py:ChangeSet.observed_paths` is defined by the shared contract, populated by `core/state_store.py:StateStore.apply` and meter coalescing, and consumed by acquisition credit and the existing runtime notifier. Literal reads of the established field exist in production and tests; it is serialized metadata, not test-only state.

Recovery introduces one instance map and one query method. `runtime/session_lifecycle.py:CoreRadioSessionLifecycle._recovery_waiters` has three production state writes (initialization, caller registration, matching-entry deletion) and two identity reads (`is_current_recovery_awaited_by` and cleanup). The query method has one production consumer, `runtime/_civ_rx.py:CivRuntime.stop_data_watchdog`, plus direct regression consumers. The added `CivRuntimeHost._session_lifecycle` annotation describes the existing owner; it creates no runtime service. Literal searches found no dynamic registry or serialization consumers for the map/query. No deletion inference depends on unseen downstream consumers.

Acquisition adds no symbol, instance state, registry or execution wrapper. `core/acquisition_scheduler.py:AcquisitionScheduler.try_claim` reuses `_request_key` and the canonical `_requests_by_key` table already maintained by queueing, partial settlement and reissue. Its sole production caller is `core/acquisition_drain.py:AcquisitionDrain.run_once`. Shared claims and provider-generation checks retain their existing definitions and consumers. The additional guard reads canonical pending identity before a claim-table write; it creates no second pending-request index or cleanup loop.

Grouped cadence extends the existing `_PendingCadenceUpdate` with one immutable `completed_paths` field. `record_acquisition_result` constructs and reads it while accumulating successful partial replies; `_reissue` carries it through the existing dataclass replacement, and existing settlement/failure cleanup retires the pending entry. `_poll_cadence_groups` supplies canonical membership. Web and rigctld share these result and cadence owners; no additional clock, path registry, poller or reconciliation loop is introduced. Literal definition, constructor, read, reissue and cleanup searches were inspected before adjudication.

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

### F4 — completed acquisition envelope identity

Verdict: already shared. Rank: parallel-owner hypothesis cleared.

Elements/definition: `core/acquisition_scheduler.py:AcquisitionScheduler.try_claim`, `_request_key`, `_requests_by_key`; consumer `core/acquisition_drain.py:AcquisitionDrain.run_once`.

Consumers: Web and rigctld retain their existing drains and seat policies while the shared scheduler owns pending request and flight identity. A drain captures a tuple of requests and awaits execution; another seat can settle a later tuple entry before the first seat reaches it.

Steelman: claim ownership alone cannot establish that a captured envelope remains pending. Looking up the canonical key and matching its current ID rejects completed or superseded requests without adding a second store, changing request priority, or duplicating seat cleanup. Same-ID partial envelopes remain claimable; generation and claimant identity checks still run for pending requests.

Divergence: the former claim-table-only check could recreate a claim for a completed ID, resend its work, and leave that claim after the next pass pruned the seat-local ledger. Existing orphan IDs do not themselves become pending requests or represent queued wire frames. The guard prevents this race's future duplicates and orphan creation; it does not retrospectively purge an already running process.

Prior ruling: the MOR-2292 contract in `tests/test_combined_acquisition_drain.py` assigns atomic cross-seat flight identity to the shared scheduler; no opposing scoped ruling was found. In-flight: that scheduler, key and both drain consumers already exist. Required surface: exists. Depends on: canonical key/ID membership before binding. Confidence: high for the deterministic completed-request race. Falsifier: a supported pending partial or reissued envelope rejected incorrectly, or another caller bypassing canonical claim ownership. Fix class: none beyond the bounded guard. Actionable: retain the single pending/claim owner. This does not establish the cause of intermittent IC-7300 missing telemetry.

### F5 — successful coverage of a shared cadence group

Verdict: already shared. Rank: parallel-clock hypothesis cleared.

Elements/definition: `core/acquisition_scheduler.py:AcquisitionScheduler.record_acquisition_result`, `_PendingCadenceUpdate`, `_poll_cadence_groups`, `_reissue`; consumers `core/acquisition_drain.py`, `runtime/_civ_rx.py` and `rigctld/handler.py`.

Consumers: rigctld can request a single SWR leaf while profile cadence groups SWR with Power, ALC, Comp and Id. CI-V ingress settles individual replies through the shared acquisition result path. The existing freshness service owns stale reconciliation.

Steelman: one successful SWR answer does not establish that its unread siblings were acquired. Repeated singleton answers previously advanced the shared deadline and could prevent those siblings from ever being requested. Conversely, a full group's final reply is itself a singleton; rejecting every partial envelope would break legitimate completion. Accumulating successful path coverage in the existing same-request pending update preserves partial semantic aggregation and reissue while allowing the clock to advance only after canonical group coverage. A full-group on-demand read also satisfies this contract; a reason-string check would incorrectly reject it.

Divergence: the old completion path treated every terminal singleton as a full-group success. Existing never-observed cadence fields are excluded from independent priming and do not undergo the stale transition that triggers reconciliation, so a second rescue poller would duplicate ownership and mask the completion defect.

Prior ruling: the MOR-1490 `prime_unobserved` contract already avoids singleton priming that suppresses grouped polling; MOR-874 and MOR-2614 own healthy-link grace and bounded timeout retry. In-flight: shared request settlement, pending cadence accumulation, canonical groups and stale reconciliation already exist. Required surface: cumulative successful coverage on the existing pending update. Depends on: same-request accumulation, reissue carry and excluding failed paths. Confidence: high for the reproduced singleton starvation mechanism; installed acceptance remains separate. Falsifier: a legitimate complete group that no longer rearms, a failed sibling credited as successful, or an unsupported membership mismatch. Fix class: bounded shared-completion repair. Actionable: retain the sole scheduler and freshness owners; no new polling framework.

## Validation evidence and weakest link

The audio candidate has [four-case RED](https://github.com/rigplane/rigplane-core/actions/runs/37014823411) and [43-case focused GREEN](https://github.com/rigplane/rigplane-core/actions/runs/37015065492). Delivery has [ingress/coalescer RED and passing rejected-generation negatives](https://github.com/rigplane/rigplane-core/actions/runs/37014998647), [471 checks on the fixed-production revision](https://github.com/rigplane/rigplane-core/actions/runs/37015351525) and [passing final-head ingress/format checks](https://github.com/rigplane/rigplane-core/actions/runs/37015644637). Recovery has [coalesced-caller RED](https://github.com/rigplane/rigplane-core/actions/runs/37017065077), [cancellation-tail RED](https://github.com/rigplane/rigplane-core/actions/runs/37018742110) and [60 final-head focused checks](https://github.com/rigplane/rigplane-core/actions/runs/37018988483), including real rearm, coalesced/unjoined callers, external and cross-radio stop, successor cleanup and advertised-port collisions. Each workflow's recorded inputs and job output establish its scope and result; these are development checks, not required PR CI. Fresh independent code review passed the pinned [audio](https://github.com/rigplane/rigplane-core/pull/3974#issuecomment-5954769997), [delivery](https://github.com/rigplane/rigplane-core/pull/3975#issuecomment-5954771013) and [recovery](https://github.com/rigplane/rigplane-core/pull/3976#issuecomment-5954898350) candidates. Required PR CI and merge integration are separate acceptance gates.

The additional acquisition regression has [exact RED](https://github.com/rigplane/rigplane-core/actions/runs/37031385365) at `e69c87853e26d2303b7981a5f535d54f8127a8b3`: one completed envelope was resent and one orphan claim remained. [Focused GREEN](https://github.com/rigplane/rigplane-core/actions/runs/37032132967) at the pinned acquisition candidate passed 203 scheduler, drain, combined-drain and IC-7300 pipeline tests plus lint/format checks. These are development checks; final exact-head PR review and required CI remain separate gates.

The grouped-cadence regression has [actual-profile RED](https://github.com/rigplane/rigplane-core/actions/runs/37038220599) at `b3b806bc8b31e810cb5063c767031024452b5196`: frequent singleton SWR reads left the Power, ALC and Id queries entirely unsent despite fresh PTT and answered SWR. [Focused GREEN](https://github.com/rigplane/rigplane-core/actions/runs/37038820725) at `49414d023ea67416e787a9839f6ec17826a1be72` passed 208 scheduler, drain and actual-profile pipeline tests plus three-path lint/format checks. The added safeguards cover explicit and class-derived sibling progress, full-group on-demand partial/coalesced completion and a failed sibling's expedited retry; existing adaptive and fresh-dispatch reissue cases remain green. These software checks do not substitute for installed-radio acceptance. Required PR CI and independent final-head review remain separate.

Weakest link: recovery waiter topology outside the covered callers; inspect that edge first if another cancellation/reopen symptom occurs. High-rate notification cost across all downstream clients and physical radio behavior are also outside this static audit. The acquisition race is proven, but its link to full missing-meter TX windows is not: actual request admission/send/ingress/discard chronology remains necessary. Nothing here closes a complete customer complaint or proves hardware behavior.

## Cleared

Shared profile routing, canonical accepted-observation metadata, Web throttling, lifecycle task retirement, bounded reconnect attempts, advertised-port ownership, canonical pending-request identity and cumulative grouped-acquisition coverage were examined and retained. No extra Core execution framework, duplicated store, second recovery loop or rescue poller is required by these changes.
