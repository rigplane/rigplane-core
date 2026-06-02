# Legacy State Writer Inventory

Status: MOR-335 baseline audit
Date: 2026-06-02
Spec: `docs/superpowers/specs/2026-06-02-radio-state-pipeline-design.md`

This inventory records the current pre-migration state/revision/cache paths.
It is intentionally descriptive: MOR-335 adds diagnostics only and does not
change delivery semantics.

Migration classes:

- `observation_adapter`: decoded or polled values should become observations.
- `pending_overlay`: local intent/read-after-write state only.
- `executor_cache`: private timeout, pacing, or dedupe cache.
- `protocol_local_keep`: protocol/session state that is not radio state.
- `compatibility_shim`: retained temporarily for API/wire stability.
- `delete`: replace with the shared state model when the field family migrates.

## Icom Runtime and CI-V

| Path | Current behavior | Class | Migration note |
|---|---|---|---|
| `runtime/_civ_rx.py::_update_state_cache_from_frame` | Parses solicited and unsolicited CI-V frames, updates `_state_cache`, `_last_freq_hz`, `_last_mode`, `_last_vfo`, and selected `RadioState` fields, then calls `_update_radio_state_from_frame`. | `observation_adapter` plus `executor_cache` | Convert decoded frame values to observations. Keep private request/cache hints only for executor fallback. |
| `runtime/_civ_rx.py::_RADIO_STATE_HANDLERS` | Directly writes `RadioState` for frequency, mode, VFO/dual-watch, scan/split/tuning, levels, meters, function flags, scope controls, PTT, RIT, and dual-RX cmd25/cmd26. | `observation_adapter` now, then `delete` | These are the highest-risk legacy confirmed-state writers. Diagnostics now record `direct_state_write` for handler dispatches. |
| `runtime/_civ_rx.py::_notify_change` | Only selected decoded fields notify Web, which indirectly bumps Web poller revision and broadcasts. Meter, frequency, mode, and many slow-state writes can be silent. | `compatibility_shim` then `delete` | Replace with `StateStore.apply(...) -> ChangeSet`; diagnostics record `revision_producing_event`. |
| `runtime/_civ_rx.py::_publish_scope_frame` and `_scope_frame_queue` | Scope samples use their own delivery queue/callback. | `protocol_local_keep` | Scope sample streaming is not canonical radio state; scope control values still need observations. |
| `runtime/_dual_rx_runtime.py` main getters/setters | Uses `_state_cache`, `_last_freq_hz`, `_last_mode`, `_filter_width`, and direct `RadioState` writes for fallback VFO routing and selected slot APIs. | `executor_cache` plus `pending_overlay` | Convert confirmed getter results to observations. Keep VFO-switch sequencing private to the executor. |
| `runtime/radio_initial_state.py` | Sends initial query sweep to populate `RadioState` through existing backend/RX paths. | `observation_adapter` | Initial acquisition should emit observations, not own delivery. |
| `runtime/radio_state_snapshot.py` | Best-effort snapshot/restore uses `_last_*` caches when reads fail. | `executor_cache` | Keep only as private restore fallback; do not expose as fresher consumer state. |
| `core/_state_cache.py` and re-export `_state_cache.py` | Shared mutable cache with per-field timestamps for freq/mode/PTT/meters/levels. | `executor_cache` and `compatibility_shim` | Retain only for executor timeout fallback until freshness metadata moves into the state model. |
| `backends/icom7610/drivers/serial_stub.py` | Fake serial backend mirrors private receiver state into `RadioState` and `StateCache`. | `compatibility_shim` | Test backend should migrate behind the same observation/state-store test harness. |

## Web Runtime, Revisions, and Frontend Store

| Path | Current behavior | Class | Migration note |
|---|---|---|---|
| `web/radio_poller.py::_revision` / `bump_revision` | Web-owned public revision counter, bumped by optimistic command updates and selected state-change callbacks. | `compatibility_shim` then `delete` | Canonical revision must move to the state model. Diagnostics record `revision_producing_event`. |
| `web/radio_poller.py::_execute` | Many command handlers optimistically write `RadioState` and call `bump_revision`; frequency/mode/filter/DSP/scope/power/antenna paths are included. | `pending_overlay` | Preserve read-after-write UX as pending intent, not confirmed state. |
| `web/radio_poller.py::_last_polled` and `_send_query` | Poll freshness and meter/state query cadence are poller-local. | `executor_cache` | Diagnostics now record `meter_cadence` and `backend_read`; later acquisition policy should own scheduling. |
| `web/radio_poller.py::_poll_unselected_slot` / host `_vfo_slot_override` | Temporarily swaps VFO slot, routes late 0x03/0x04 responses into inactive slot, then restores. | `observation_adapter` plus `protocol_local_keep` | Keep the swap/restore protocol mechanics local; convert returned values into slot-scoped observations. |
| `web/server.py::_radio_state` and `build_public_state` | Web projects the mutable `RadioState` plus Web poller revision into HTTP/WS payloads. | `compatibility_shim` | Snapshot builder should consume canonical state model snapshots. |
| `web/server.py::_health_revision` | Separate health revision for readiness transitions. | `compatibility_shim` | Confirms spec assumption that freshness/health revision must be separate from value revision. |
| `web/server.py::_broadcast_state_update` | Throttled WebSocket delivery trigger; delta encoder revision is separate from public state revision. | `compatibility_shim` | Diagnostics now record `web_delivery_trigger`. Delta delivery remains representation-only. |
| `web/server.py::_serve_state` | HTTP snapshot uses `revision-healthRevision` ETag from Web payload. | `compatibility_shim` | HTTP should use canonical state/freshness revisions from the state model. |
| `web/_delta_encoder.py` | Maintains transport-local `revision` and emits full/delta envelopes. | `compatibility_shim` then `delete` as state revision source | Keep transport sequence only if renamed/split from canonical state revision. |
| `web/handlers/control.py` initial state | Sends a full state envelope whose revision is the delta encoder revision, while payload also has public revision. | `compatibility_shim` | Confirms spec assumption that wire `transportSeq` and state revision are currently conflated. |
| `web/web_startup.py` `StatePollable` branch | Yaesu/external pollers replace `server._radio_state` and trigger broadcast on callback. | `observation_adapter` | Callback should apply observations/change sets; diagnostics record a backend read callback. |
| `frontend/src/lib/transport/http-client.ts` | HTTP callback only fires when `revision` or `healthRevision` advances. | `compatibility_shim` | Confirms meter jitter symptom: silent meter writes remain invisible until a revision/health change. |
| `frontend/src/lib/transport/ws-client.ts` | Applies delta encoder envelope and copies envelope `revision` to full state. | `compatibility_shim` | Split transport sequence from canonical `stateRevision`. |
| `frontend/src/lib/stores/radio.svelte.ts` | Store accepts state only when revision/health advances; optimistic maps overlay receiver/top-level fields. | `compatibility_shim` plus `pending_overlay` | Optimistic maps are pending overlays. Revision gate should use canonical value/freshness revisions. |

## rigctld Server and Hamlib Compatibility

| Path | Current behavior | Class | Migration note |
|---|---|---|---|
| `rigctld/handler.py::_PendingRigState` | Local optimistic read-after-write state for MAIN freq/mode/filter/data_mode. | `pending_overlay` | Keep as scoped pending intent until shared pending overlays exist. |
| `rigctld/handler.py::_FallbackRigState` | Handler-local fallback cache for freq/mode/data/PTT/S-meter/RF power/SWR. | `compatibility_shim` and `executor_cache` | Replace consumer-visible fallback with shared state/freshness; keep only private executor fallback if needed. |
| `rigctld/handler.py::_split_tx_vfo` | Tracks Hamlib protocol TX VFO label across split commands. | `protocol_local_keep` | This is session/protocol state, not confirmed radio state. |
| `rigctld/handler.py` GET paths | Prefer `RadioState`, then pending/cache, then backend reads for some fields. | `compatibility_shim` | GET should consume shared state projections. Diagnostics now record `rigctld_delivery_trigger`. |
| `rigctld/routing.py` level/func routing | Reads backend meters/levels/functions and updates `_FallbackRigState`. | `observation_adapter` plus `compatibility_shim` | Backend reads should become observations; Hamlib response formatting remains compatibility. |
| `rigctld/poller.py` | Background poller updates `StateCache` for freq/mode/data_mode. | `executor_cache` then `delete` as consumer source | Later rigctld should consume shared state model instead of this cache. |
| `rigctld/server.py` session `vfo_mode` | Per-client Hamlib `chk_vfo` state. | `protocol_local_keep` | Keep local; do not migrate into radio state. |
| `rigctld/contract.py` and dump-state constants | Hamlib wire compatibility, positional dump-state assumptions. | `compatibility_shim` | Preserve wire behavior; state migration must not alter protocol text output. |

## Yaesu and External rigctld Client Backends

| Path | Current behavior | Class | Migration note |
|---|---|---|---|
| `backends/yaesu_cat/radio.py::_state` | Request/response getters and setters directly mutate `RadioState`; IF bulk query seeds several fields. | `observation_adapter` plus `pending_overlay` | Getter/IF responses should emit observations. Setter echoes become pending overlays until confirmed. |
| `backends/yaesu_cat/poller.py` | Fast/medium/slow loops call getters, smooth S-meter, mutate backend `RadioState`, then invoke callback. | `observation_adapter` | Poller is an acquisition scheduler precursor; callback should carry observations/change sets. |
| `backends/yaesu_cat/poller.py` EMA S-meter state | Local smoothing memory for meter samples. | `executor_cache` | Keep as acquisition/presentation policy only if explicitly needed; do not treat as confirmed state. |
| `backends/rigctld_client/radio.py::_state` | External Hamlib responses and local SET commands directly mutate `RadioState`; VFO support probe caches capability. | `observation_adapter` plus `pending_overlay` | Translate rigctld responses to observations. SET writes should be pending until readback/confirmation. |
| `backends/rigctld_client/radio.py::_vfo_supported` | Capability probe result for external rigctld VFO command availability. | `executor_cache` | Keep as backend capability/cache, not radio state. |
| `core.radio_protocol.StateCacheCapable` | Protocol advertises `state_cache` and `radio_state` to consumers. | `compatibility_shim` | Shared state service should replace consumer reliance on these mutable objects. |

## Profile and Schema Gaps

| Gap | Current behavior | Class | Migration note |
|---|---|---|---|
| Meter push/support metadata | Profiles expose meter calibration/redlines, but not complete unsolicited-vs-polled meter freshness policy. | `compatibility_shim` | Add profile capability metadata for acquisition policy and freshness windows. |
| VFO path precision | Profiles know VFO schemes/codes, but current state paths still mix receiver active state, VFO slots, and Hamlib VFO labels. | `protocol_local_keep` and `compatibility_shim` | Target `FieldPath` must include receiver and slot. |
| Hamlib wire assumptions | dump-state constants, `chk_vfo`, and split VFO labels are protocol compatibility, not state correctness. | `compatibility_shim` and `protocol_local_keep` | Preserve wire output while moving GET values to shared state projections. |

## Baseline Diagnostics Added in MOR-335

`rigplane.core.state_diagnostics.StateDiagnosticsRecorder` is disabled by
default and records nothing unless explicitly enabled. When enabled through
`WebConfig(state_diagnostics=True)` or tests, instrumentation records:

- `direct_state_write`: CI-V `RadioState` handler dispatches.
- `revision_producing_event`: CI-V notify events and Web poller revision bumps.
- `meter_cadence`: Web poller meter query cadence.
- `backend_read`: Web poller state/meter queries and `StatePollable` callbacks.
- `web_delivery_trigger`: HTTP state and WebSocket state delivery.
- `rigctld_delivery_trigger`: rigctld handler responses.

Focused tests cover the recorder's inert behavior, CI-V S-meter direct writes
without notify/revision, and the current Web-visible symptom where a silent
meter write is only delivered after an unrelated state-change trigger.

## Spec Assumptions Confirmed

- A mutable `RadioState` is currently consumer-visible and has multiple writers.
- Meter fields can change without a public Web revision-producing event.
- HTTP and WebSocket both gate frontend updates through legacy revision fields.
- Web delta revision and public state revision are separate counters today but
  are exposed with the same `revision` name in different envelope positions.
- rigctld has protocol-local state that must not become canonical radio state.
- Hamlib external rigctld remains the correct Core boundary; no direct Hamlib
  binding is needed for this migration.

## Deferred Follow-Ups

- Add typed `Observation`, `FieldPath`, and `ChangeSet` contracts in the next
  implementation milestone.
- Replace the CI-V direct writer handlers with observation adapters one field
  family at a time.
- Add frontend tests for `stateRevision`/`transportSeq` split when the wire
  schema changes.
- Add rigctld fake-radio tests that compare GET responses against shared state
  projections once the state model exists.
