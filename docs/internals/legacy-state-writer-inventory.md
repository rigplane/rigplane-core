# Legacy State Writer Inventory

Status: MOR-407 final cleanup status
Date: 2026-06-03
Spec: `docs/superpowers/specs/2026-06-02-radio-state-pipeline-design.md`

This inventory started as the MOR-335 baseline audit. MOR-407 resolves the
remaining `RadioState`, `StateCache`, and legacy revision overlap into explicit
cleanup decisions so reviewers can tell which legacy paths were deleted, which
now project through `StateStore`, which remain protocol-local, and which are
compatibility-only surfaces.

MOR-407 classification:

- required compatibility mirror fed from StateStore/observations:
  `compatibility_only` rows whose replacement/guard names `StateStore`,
  observations, or explicit compatibility ingress.
- protocol-local state that is not radio state: `protocol_local_keep`.
- removable legacy writer: `deleted`.
- bug requiring separate fix: `deferred_follow_up` constraints. No new
  out-of-scope bug was found during the MOR-407 audit.

Decision statuses:

- `deleted`: removed in MOR-347 or an earlier milestone.
- `migrated`: normal delivery now uses observations, `StateStore`, or
  `StateStore` snapshots.
- `protocol_local_keep`: local protocol/session state, not semantic radio
  state.
- `compatibility_only`: retained only for public API, Web schema, CLI/config,
  Hamlib wire behavior, or legacy test/backend compatibility.
- `executor_cache_keep`: private command/acquisition timeout, pacing, dedupe,
  or restore cache; it must not be treated as fresher consumer state.
- `deferred_follow_up`: intentionally left for a later issue because removing
  it requires a broader field-family migration or public compatibility decision.

## Icom Runtime and CI-V

| Path | MOR-407 status | Current replacement / guard | Remaining constraint |
|---|---|---|---|
| `runtime/_civ_rx.py::_update_state_cache_from_frame` | `migrated` plus `executor_cache_keep` | Supported CI-V frames call `_apply_state_store_observations(...)` before legacy mirrors; private `_state_cache` updates remain executor fallback. Covered by `tests/test_civ_rx_coverage.py`, Web meter/freshness regressions, and state pipeline diagnostics. | Keep cache reads private to executors; do not expose `_state_cache` as consumer delivery truth. |
| `runtime/_civ_rx.py::_RADIO_STATE_HANDLERS` | `compatibility_only` | `StateStore.apply(...)` is canonical for supported field families; handlers still mirror into `RadioState` for public API/backward test compatibility. | `deferred_follow_up`: delete handler mirrors only after every public `RadioState` field family has a documented snapshot projection and compatibility tests. |
| `runtime/_civ_rx.py::_notify_change` | `compatibility_only` | `state_store_changed` carries canonical revision/freshness delivery; legacy event names remain for Web event notifications and older callback consumers. | Do not use notify events to produce Web state revisions. MOR-347 static tests reject reintroducing Web poller revision bumps. |
| `runtime/_civ_rx.py::_publish_scope_frame` and `_scope_frame_queue` | `protocol_local_keep` | Scope sample streaming remains a separate sample protocol, not semantic radio state. | Scope controls are state fields; scope samples are not. |
| `runtime/_dual_rx_runtime.py` main getters/setters | `executor_cache_keep` plus `compatibility_only` | VFO switch/restore sequencing and `_last_*` cache use remain private executor behavior; confirmed values are also represented by `StateStore` where supported. | `deferred_follow_up`: remove direct active-slot `RadioState` writes after dual-RX slot projections cover all public consumers. |
| `runtime/radio_initial_state.py` | `migrated` | Initial sweeps flow through runtime receive/observation paths and seed `StateStore` for supported fields. | Keep hardware-dependent validation manual where profiles require it. |
| `runtime/radio_state_snapshot.py` | `executor_cache_keep` | Best-effort restore fallback may use `_last_*` caches but does not define consumer freshness. | No Web/rigctld delivery path may prefer this cache over `StateStore`. |
| `core/_state_cache.py` and re-export `_state_cache.py` | `executor_cache_keep` plus `compatibility_only` | Retained for runtime timeout fallback and import compatibility. | Public `state_cache` exposure is compatibility-only; new delivery code must use snapshots/projections. |
| `backends/icom7610/drivers/serial_stub.py` | `compatibility_only` | Fake serial backend still mirrors into `RadioState`/`StateCache` for legacy tests. | `deferred_follow_up`: migrate to provider observation test harness when serial stub tests no longer depend on mutable mirrors. |

## Web Runtime, Revisions, and Frontend Store

| Path | MOR-407 status | Current replacement / guard | Remaining constraint |
|---|---|---|---|
| `web/radio_poller.py::_revision` / `bump_revision` | `deleted` | Removed in MOR-347. Web state revisions come from `StateStore.snapshot().state_revision`; `tests/test_state_pipeline_contracts.py` rejects reintroducing poller revision API or server callback bumps. | Public Web payload still includes legacy `revision`, but it aliases canonical `stateRevision`. |
| `web/radio_poller.py::_execute` command ACK paths | `compatibility_only` | Setter success records CommandService lifecycle/pending overlays only; it no longer confirms `StateStore` values. The remaining `RadioState` writes are legacy read-after-write mirrors and Web delivery ignores them when `StateStore` has a value. BSR is the exception because it has register readback and emits explicit `poll_response` observations. | Keep compatibility mirrors out of delivery/model ownership. `deferred_follow_up`: migrate remaining legacy mirror fields to typed readback observations or pending overlays field-family by field-family. |
| `web/radio_poller.py::_last_polled` and `_send_query` | `executor_cache_keep` | Poll cadence is acquisition/runtime local. Meter and state query observations feed `StateStore` where supported. | Do not use poll cadence markers as delivery freshness for Web consumers. |
| `web/radio_poller.py::_poll_unselected_slot` / host `_vfo_slot_override` | `migrated` plus `protocol_local_keep` | VFO swap mechanics remain protocol-local; returned values are slot-scoped observations where supported. | Keep swap/restore invisible to consumer state except via confirmed observations. |
| `web/server.py::_radio_state` and `build_public_state` | `migrated` plus `compatibility_only` | HTTP and WS state build from `command_state_store.snapshot()`. `sync_state_store_from_radio_state(...)` is a compatibility ingress for legacy state-poller snapshots and startup composition points. HTTP power keeps only a legacy public mirror; it does not confirm `StateStore`. | Normal delivery must not call compatibility sync or read backend/poller/legacy state to build radio values. Legacy ingress is allowed only at explicit startup/poller callback boundaries. |
| `web/server.py::_health_revision` | `compatibility_only` | Public health transitions are separate from semantic `stateRevision` and included in ETags. | Keep `healthRevision` additive/backward compatible until a public freshness schema replacement exists. |
| `web/server.py::_broadcast_state_update` | `migrated` | Broadcast encodes snapshots from `StateStore` and suppresses duplicate state keys using state/freshness/health revisions. Audio FFT scope metadata is derived from the same snapshot before broadcast. | Delivery triggers may broadcast events, but they must not mutate confirmed state or ingest legacy state. |
| `web/server.py::_serve_state` | `migrated` | HTTP ETag uses `stateRevision-freshnessRevision-healthRevision`; body `revision` aliases `stateRevision`. | Preserve legacy `revision` key for existing clients. |
| `web/_delta_encoder.py` | `migrated` | MOR-347 splits transport-local `transportSeq` from canonical `revision`/`stateRevision`. `tests/test_delta_encoder.py` covers the split. | `revision` remains a legacy alias for canonical state revision when supplied; no frontend should treat `transportSeq` as state freshness. |
| `web/handlers/control.py` initial state | `migrated` | Initial WS state calls server `build_state_update_envelope(force_full=True)` so HTTP and WS share the same snapshot revision/freshness. | Fallback `build_public_state_payload(... revision=0)` remains compatibility-only for handler tests without a server. |
| `web/web_startup.py` poller startup | `migrated` plus `compatibility_only` | Observation-capable providers use `create_observation_poller(...)` and apply typed observations to `StateStore` before broadcast. The legacy `StatePollable`/`sync_state_store_from_radio_state(...)` branch remains only for providers that have not opted into observations. | New production providers must not claim observation readiness while relying on legacy bulk sync. |
| `frontend/src/lib/transport/http-client.ts` | `migrated` | Frontend gates updates on canonical `stateRevision` plus `freshnessRevision`, falling back to legacy `revision` for compatibility. | Legacy `revision` fallback remains public Web compatibility. |
| `frontend/src/lib/transport/ws-client.ts` | `migrated` | WS full/delta envelopes preserve `stateRevision` and `freshnessRevision`; MOR-347 adds backend `transportSeq` without requiring frontend changes. | Future frontend tests may consume `transportSeq` for ordering only. |
| `frontend/src/lib/stores/radio.svelte.ts` | `migrated` plus `compatibility_only` | Store stale rejection uses canonical state/freshness revision helpers with legacy fallback. Optimistic maps remain local pending overlays. | Keep restart handling compatible with legacy low revision resets. |

## rigctld Server and Hamlib Compatibility

| Path | MOR-407 status | Current replacement / guard | Remaining constraint |
|---|---|---|---|
| `rigctld/handler.py::_PendingRigState` | `compatibility_only` | CommandService pending overlays and `StateStore` projections are canonical where present; local pending state preserves Hamlib read-after-write behavior. | Keep scoped to Hamlib commands; do not share as generic radio state. |
| `rigctld/handler.py::_FallbackRigState` | `compatibility_only` | GET paths prefer `StateStore` projections, then public `RadioState` compatibility, then fallback cache where needed for Hamlib wire stability. | `deferred_follow_up`: remove individual fallback fields once projections cover every GET and compatibility tests prove behavior. |
| `rigctld/handler.py::_split_tx_vfo` | `protocol_local_keep` | Hamlib TX VFO label/session behavior is protocol state, not confirmed radio state. | Preserve wire output. |
| `rigctld/handler.py` GET paths | `migrated` plus `compatibility_only` | Frequency, mode, PTT, levels, split, functions, and dual-RX reads project `StateStore` first when available. | Compatibility fallbacks may not appear fresher than a present projection. |
| `rigctld/routing.py` level/func routing | `migrated` plus `compatibility_only` | Routing reads can update fallback cache, but handler projections prefer `StateStore`. | Backend reads should continue moving into observation adapters. |
| `rigctld/poller.py` | `executor_cache_keep` | Background cache maintenance remains private telemetry plumbing. | `deferred_follow_up`: delete as consumer source after rigctld fake-radio tests cover all projection reads. |
| `rigctld/server.py` session `vfo_mode` | `protocol_local_keep` | Per-client `chk_vfo` state is Hamlib session state. | Preserve Hamlib protocol behavior. |
| `rigctld/contract.py` and dump-state constants | `compatibility_only` | Positional dump-state and command response text remain public Hamlib wire compatibility. | State migration must not alter text output without explicit compatibility callout. |

MOR-422 active receiver/RIT cleanup: rigctld `get_vfo` formats active VFO only
from fresh `StateStore` projection (`global.slow_state.active` for dual-RX,
`receiver.<rx>.vfo.active_slot` for single-RX); missing or stale projection
returns Hamlib `EIO` instead of legacy `RadioState`/default values. `get_rit`
and `get_xit` both project the shared Icom CI-V offset register from
`global.operator_controls.rit_freq`; missing/stale projection performs
backend readback, records a `hamlib_response` observation, and then responds
from Store, or returns `EIO` when readback is unavailable.

## Yaesu and External rigctld Client Backends

| Path | MOR-407 status | Current replacement / guard | Remaining constraint |
|---|---|---|---|
| `backends/yaesu_cat/radio.py::_state` | `migrated` plus `compatibility_only` | `backends/yaesu_cat/observations.py` provides provider observations for polling reads; mutable `_state` remains public backend compatibility. | `deferred_follow_up`: remove direct setter echo mirrors after pending overlays cover Yaesu SET commands. |
| `backends/yaesu_cat/poller.py` | `migrated` plus `compatibility_only` | Web startup uses Yaesu observation poller ingress for adapter-covered production reads: fast S-meter, TX power/SWR meters, medium frequency/mode/PTT, and slow AF/RF/squelch controls. Legacy `RadioState` callbacks remain for compatibility poller callers only. | Residual legacy-only Yaesu reads are explicit limitations, not silent MOR-401 production claims: ALC is canonical but not marked pollable in the FTX-1 acquisition profile; COMP has no canonical meter path; filter width, AGC, NB/NR levels and toggles, notch, TX power level, mic gain, split, VOX, dial lock, compressor controls, attenuator/preamp, tuner, contour, IF shift, RIT/XIT, narrow mode, CW fields, Yaesu FR/FT extension fields, and VFO select lack FTX-1 pollable acquisition policy or a backend-neutral field mapping. |
| `backends/yaesu_cat/poller.py` EMA S-meter state | `executor_cache_keep` | Local smoothing memory is acquisition-local; observation mode emits the resulting S-meter samples only for the declared `receiver.*.meters.s_meter` paths. | Do not add new smoothed meter paths without explicit acquisition/profile policy. |
| `backends/rigctld_client/radio.py::_state` | `migrated` plus `compatibility_only` | `backends/rigctld_client/observations.py` adapts external Hamlib responses and command responses to observations; `RigctldClientRadio.create_observation_poller(...)` wires adapter-covered production reads into Web startup. | Unsupported acquisition-profile fields, such as power state and rigs without VFO support, remain explicit readiness gaps. Direct SET echoes remain compatibility until command pending overlays cover all fields. |
| `backends/rigctld_client/radio.py::_vfo_supported` | `executor_cache_keep` | Capability probe result remains a backend capability cache. | Not radio state. |
| `core.radio_protocol.StateCacheCapable` | `compatibility_only` | Protocol remains to preserve public API/backend compatibility. New consumers should prefer `state_store`/snapshot projections when available. | Public removal/deprecation requires a separate compatibility issue. |

## Profile and Schema Gaps

| Gap | MOR-347 status | Current replacement / guard | Remaining constraint |
|---|---|---|---|
| Meter push/support metadata | `migrated` plus `deferred_follow_up` | Acquisition scheduler/coalescer and profile capability metadata now cover current meter freshness behavior. | Extend profile metadata before adding model-specific meter push policy. |
| VFO path precision | `migrated` plus `protocol_local_keep` | `FieldPath` includes receiver and active/fixed slot dimensions; Hamlib VFO labels remain protocol-local. | Continue migrating dual-RX public fields to slot-aware projections. |
| Hamlib wire assumptions | `protocol_local_keep` plus `compatibility_only` | `chk_vfo`, split TX labels, dump-state constants, and text formatting remain Hamlib compatibility. | Preserve wire behavior while values move to `StateStore` projections. |

## MOR-407 Cleanup Guards and Regression Coverage

- `tests/test_state_pipeline_contracts.py` keeps a narrow public API guard for
  Web poller revision methods, then uses poisoned legacy `RadioState` and
  poller revision paths to prove Web HTTP/WS/callback delivery reads the
  canonical `StateStore` snapshot without delivery-time legacy sync or direct
  `StateStore` mutation.
- `tests/test_radio_poller_coverage.py` verifies Web setter success does not
  confirm StateStore fields without readback, while preserving legacy mirrors
  and scoped pending overlays for read-after-write behavior.
- `tests/test_web_server_coverage.py` verifies observation-capable startup uses
  observation pollers instead of legacy bulk sync, and HTTP power does not seed
  confirmed StateStore state from a command ACK.
- `tests/test_rigctld_handler.py` verifies rigctld SET commands enter
  CommandService without command-ACK confirmation, and GET paths project
  StateStore values ahead of RadioState/fallback compatibility paths.
- `tests/test_delta_encoder.py` verifies canonical `revision`/`stateRevision`
  are split from transport-local `transportSeq`.
- Web regression tests cover meter-only semantic changes, freshness-only
  updates, HTTP/WS snapshot agreement, ETag state/freshness/health behavior,
  and legacy `revision` aliasing.
- rigctld tests cover `StateStore` projection precedence and Hamlib
  compatibility fallbacks.

## Public Compatibility Callouts

- Web state payloads still include legacy `revision`; it is now explicitly an
  alias for canonical `stateRevision` whenever the backend supplies a canonical
  revision.
- WebSocket full/delta envelopes now include additive `transportSeq`. Existing
  clients may ignore it. It must be treated as ordering metadata only, never as
  semantic state freshness.
- Public `RadioState`, `StateCacheCapable`, CLI/config behavior, and Hamlib
  rigctld wire text remain compatible in MOR-347.
