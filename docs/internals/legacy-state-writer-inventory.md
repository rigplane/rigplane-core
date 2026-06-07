# Legacy State Writer Inventory

Status: MOR-407 final cleanup status, updated for MOR-437 dead-code/mirror cleanup
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
| `runtime/_civ_rx.py::_RADIO_STATE_HANDLERS` | `migrated` plus `compatibility_only` | MOR-437 made the in-scope Icom field families observation-backed: `_observations_from_frame(...)` emits `StateStore` observations for freq/mode, att (0x11), filter_width/agc_time_constant/data_mode (0x1A 03/04/06), power/ptt/tuner/tx-monitor (0x18/0x1C), active/dual-watch/split (0x07/0x0F), RIT (0x21), the 0x14 levels in `_CMD14_OBSERVATION_BACKED_SUBS` (af/rf-gain/squelch/nr/nb/mic/comp/monitor levels + cw_pitch), the 0x15 meters, and the 0x16 toggles/values in `_CMD16_OBSERVATION_BACKED_SUBS`/`_CMD16_OBSERVATION_BACKED_VALUE_SUBS` (nb/nr/auto-notch/manual-notch/comp-on/monitor-on/vox-on/preamp/agc). `_handle_*` skips the redundant `RadioState` mirror for those subs. | `deferred_follow_up`: handlers still mirror into `RadioState` for field families with no observation emitter yet (e.g. pbt_inner/pbt_outer 0x14 07/08, antenna 0x12, repeater tone/tsql, digisel, twin-peak, ipplus, apf, filter_shape, dial_lock, break_in, ssb_tx_bandwidth, tuning_step, scan, scope controls). Delete those mirrors only after each gains a documented snapshot projection and compatibility tests. |
| `runtime/_civ_rx.py::_notify_change` | `compatibility_only` | `state_store_changed` carries canonical revision/freshness delivery; legacy event names remain for Web event notifications and older callback consumers. | Do not use notify events to produce Web state revisions. MOR-347 static tests reject reintroducing Web poller revision bumps. |
| `runtime/_civ_rx.py::_publish_scope_frame` and `_scope_frame_queue` | `protocol_local_keep` | Scope sample streaming remains a separate sample protocol, not semantic radio state. | Scope controls are state fields; scope samples are not. |
| `runtime/_dual_rx_runtime.py` main getters/setters | `executor_cache_keep` plus `compatibility_only` | VFO switch/restore sequencing and `_last_*` cache use remain private executor behavior; confirmed values are also represented by `StateStore` where supported. | `deferred_follow_up`: remove direct active-slot `RadioState` writes after dual-RX slot projections cover all public consumers. |
| `runtime/radio_initial_state.py` | `migrated` | Initial sweeps flow through runtime receive/observation paths and seed `StateStore` for supported fields. | Keep hardware-dependent validation manual where profiles require it. |
| `runtime/radio_state_snapshot.py` | `executor_cache_keep` | Best-effort restore fallback may use `_last_*` caches but does not define consumer freshness. | No Web/rigctld delivery path may prefer this cache over `StateStore`. |
| `core/_state_cache.py` and re-export `_state_cache.py` | `executor_cache_keep` plus `compatibility_only` | Retained for runtime timeout fallback and import compatibility. | Public `state_cache` exposure is compatibility-only; new delivery code must use snapshots/projections. |
| `backends/icom7610/drivers/serial_stub.py` | `migrated` plus `compatibility_only` | MOR-437: the fake serial backend is now `ObservationPollable` — `SerialMockRadio.create_observation_poller(...)` feeds the provider observation pipeline, so production reads no longer depend on `StateStore` bulk sync from the stub. The internal `RadioState`/`StateCache` mirrors remain only to drive the stub's deterministic CI-V responses and legacy `radio_state`/`state_cache` test accessors. | Keep the internal mirrors scoped to CI-V simulation/legacy accessors; new stub state must be observable via the observation poller, not consumed from the mutable mirrors. |

## Web Runtime, Revisions, and Frontend Store

| Path | MOR-407 status | Current replacement / guard | Remaining constraint |
|---|---|---|---|
| `web/radio_poller.py::_revision` / `bump_revision` | `deleted` | Removed in MOR-347. Web state revisions come from `StateStore.snapshot().state_revision`; `tests/test_state_pipeline_contracts.py` rejects reintroducing poller revision API or server callback bumps. | Public Web payload still includes legacy `revision`, but it aliases canonical `stateRevision`. |
| `web/radio_poller.py::_execute` command ACK paths | `deleted` no-op plus `migrated`/`compatibility_only` mirrors | MOR-437 deleted the pure no-op `_apply_command_response_observation(...)` and every call site (~15): setter success was already lifecycle-only, so the no-op carried no behavior. The per-family legacy `RadioState` mirrors for now observation-backed families were also removed — att, preamp, agc, rf_gain, squelch, nr/nb levels, mic_gain, compressor/monitor level+on, vox_on, split, tuner_status, filter_width, data_mode, agc_time_constant, auto/manual notch. Read-after-write for these is owned by CommandService scoped pending overlays plus the `_civ_rx.py` observation emitters. BSR keeps its real `_apply_bsr_readback_observations(...)` `poll_response` emitter (the BAND audit relies on it). | Setter success must never confirm `StateStore`. `deferred_follow_up`: the mirrors listed in "MOR-437 intentionally-kept compatibility mirrors" below remain because those families have no observation emitter yet; migrate them field-family by field-family. |
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
| `backends/yaesu_cat/poller.py` | `migrated` plus `compatibility_only` | Web startup uses Yaesu observation poller ingress for the fields that the FTX-1 `[state_acquisition]` profile (`rigs/ftx1.toml:59-92`) declares and that `backends/yaesu_cat/observations.py` emits: main+sub freq/mode, PTT, main+sub S-meter, global power/SWR meters, and main+sub AF/RF/squelch controls. Legacy `RadioState` callbacks remain for compatibility poller callers only. | The remaining Yaesu fields are read only into legacy `RadioState` in `poller.py::_poll_slow`/`_poll_fast` with **no `[state_acquisition]` capability and no observation adapter lane** — they are explicit gaps, not silent MOR-401 production claims: ATT/preamp, AGC, NB/NR levels, auto/manual notch, filter_width, IF-shift, TX power-level setpoint, mic_gain, compressor on/level, split + FR/FT routing, vfo_select active slot, VOX, tuner, RIT/XIT clarifier, CW (keyer speed/pitch/break-in/spot/delay), narrow, dial_lock, tone/CTCSS, contour/APF, and the ALC meter. `monitor` is correctly **excluded** (FTX-1 has no CAT monitor — ML is rejected, EX-menu only; `rigs/ftx1.toml:39,481-485`), not a gap. `deferred_follow_up`: these Yaesu gaps are tracked under MOR-424 and are **not release-gating** — no current release claims FTX-1 backend-neutral control parity. They should be promoted to a release gate (as MOR-437 was for Icom) only when an FTX-1 release is scheduled. |
| `backends/yaesu_cat/poller.py` EMA S-meter state | `executor_cache_keep` | Local smoothing memory is acquisition-local; observation mode emits the resulting S-meter samples only for the declared `receiver.*.meters.s_meter` paths. | Do not add new smoothed meter paths without explicit acquisition/profile policy. |
| `backends/rigctld_client/radio.py::_state` | `migrated` plus `compatibility_only` | `backends/rigctld_client/observations.py` adapts external Hamlib responses and command responses to observations; `RigctldClientRadio.create_observation_poller(...)` wires adapter-covered production reads into Web startup. After MOR-437 the rigctld-client is otherwise at the desired end-state: freq/mode, PTT, rf_gain, af_level, preamp, att, nb, nr, and filter_width are all observation-backed (`filter_width` via `get_mode` polling readback in `read_freq_mode_controls`, `observations.py:90-99,203`). | The only genuine acquisition-profile gaps are the two explicit `FieldAvailability.UNSUPPORTED` declarations in `observations.py:142-162`: `global.tx_state.power_on` (external rigctld exposes no power state) and the active VFO slot on rigs without VFO support (`receiver.main.vfo.active_slot`). Direct SET echoes remain compatibility until command pending overlays cover all fields. |
| `backends/rigctld_client/radio.py::_vfo_supported` | `executor_cache_keep` | Capability probe result remains a backend capability cache. | Not radio state. |
| `core.radio_protocol.StateCacheCapable` | `compatibility_only` | Protocol remains to preserve public API/backend compatibility. New consumers should prefer `state_store`/snapshot projections when available. | Public removal/deprecation requires a separate compatibility issue. |

## Profile and Schema Gaps

| Gap | MOR-347 status | Current replacement / guard | Remaining constraint |
|---|---|---|---|
| Meter push/support metadata | `migrated` plus `deferred_follow_up` | Acquisition scheduler/coalescer and profile capability metadata now cover current meter freshness behavior. | Extend profile metadata before adding model-specific meter push policy. |
| VFO path precision | `migrated` plus `protocol_local_keep` | `FieldPath` includes receiver and active/fixed slot dimensions; Hamlib VFO labels remain protocol-local. | Continue migrating dual-RX public fields to slot-aware projections. |
| Hamlib wire assumptions | `protocol_local_keep` plus `compatibility_only` | `chk_vfo`, split TX labels, dump-state constants, and text formatting remain Hamlib compatibility. | Preserve wire behavior while values move to `StateStore` projections. |

## MOR-437 Dead-Code and Migrated-Mirror Cleanup

MOR-437 (acceptance criterion #4) removed the now-dead command-response no-op
and the legacy `RadioState` mirror writes in `web/radio_poller.py` for the Icom
field families that earlier lanes made observation-backed in
`runtime/_civ_rx.py`.

Removed:

- `web/radio_poller.py::_apply_command_response_observation` — a pure no-op
  (it `del`'d its arguments). Deleted with all ~15 call sites.
- `web/radio_poller.py::_apply_att_compatibility_mirror` /
  `_apply_preamp_compatibility_mirror` — the per-family mirror helpers for the
  migrated att/preamp families.
- The inline `RadioState` mirror writes in `_execute(...)` for the migrated
  families: att (0x11), preamp (0x16 02), agc (0x16 12), nr_level (0x14 06),
  nb_level (0x14 12), auto_notch (0x16 41), manual_notch (0x16 48),
  agc_time_constant (0x1A 04), data_mode (0x1A 06), mic_gain (0x14 0B),
  vox_on (0x16 46), compressor_level (0x14 0E), monitor_on (0x16 45),
  monitor_gain (0x14 15), filter_width (0x1A 03), split (0x0F), nb (0x16 22),
  nr (0x16 40), tuner_status (0x1C 01). rf_gain/squelch had only the no-op and
  no mirror.

Read-after-write for the removed families is guaranteed by two layers, with no
poller-side `RadioState` write: (1) CommandService records a scoped pending
overlay carrying the written value on `execute(intent)`; (2) the next poll
readback produces a typed `StateStore` observation from `_civ_rx.py` that
reconciles the overlay. `tests/test_radio_poller_coverage.py::
test_compatibility_mirror_commands_do_not_confirm_state_without_readback` proves
the overlay still carries the value after the poller executes the command and
that the legacy mirror is no longer written for migrated families.

Kept (real emitter — must NOT be removed):

- `web/radio_poller.py::_apply_bsr_readback_observations` — emits explicit
  `poll_response` freq/mode observations from the BSR register readback; the
  BAND audit relies on it.

MOR-437 intentionally-kept compatibility mirrors (`deferred_follow_up`, not yet
observation-backed — leave until each family gains an observation emitter):

- pbt_inner / pbt_outer (0x14 07/08 — emit-capable in `_civ_rx.py` but still on
  the `_handle_14` mirror path, not the mirror-skip set; mirror kept via
  `_apply_compatibility_mirror`).
- notch_filter, if_shift, filter_shape (0x16 56), cw_pitch (0x14 09 mirror in
  poller kept although observation-backed — not in this lane's family list),
  dial_lock (0x16 50), break_in (0x16 47), drive_gain, ref_adjust,
  tx_freq_monitor (0x1C 03), af_mute, tuning_step (0x10), scan state
  (scanning/scan_type/scan_resume_mode, 0x0E), main_sub_tracking,
  ssb_tx_bandwidth (0x16 58), repeater_tone/tsql (0x16 42/43) and tone/tsql
  freq (0x1B), antenna (0x12: tx_antenna/rx_antenna_1/2), apf (0x16 32),
  twin_peak_filter (0x16 4F), digisel, ipplus (0x16 65), vox_gain/anti_vox_gain/
  vox_delay, nb_depth/nb_width, dash_ratio, RIT toggles/freq poller mirrors,
  scope controls (0x27 sub-commands), and the optimistic `power_on` write in
  `SetPowerstat` (radio stops answering polls when off). The active-slot mirror
  in `SelectVfo` (`receiver.0.vfo.active_slot`) is also kept — the 0x07 0xD2
  observation writes `global.slow_state.active`, a different path.
- Private `_state_cache` executor fallbacks remain `executor_cache_keep`.

## MOR-407 Cleanup Guards and Regression Coverage

- `tests/test_state_pipeline_contracts.py` keeps a narrow public API guard for
  Web poller revision methods, then uses poisoned legacy `RadioState` and
  poller revision paths to prove Web HTTP/WS/callback delivery reads the
  canonical `StateStore` snapshot without delivery-time legacy sync or direct
  `StateStore` mutation.
- `tests/test_radio_poller_coverage.py` verifies Web setter success does not
  confirm StateStore fields without readback. After MOR-437 it also asserts the
  migrated families no longer write the legacy `RadioState` mirror while scoped
  pending overlays still carry the written value for read-after-write, and that
  the kept `deferred_follow_up` mirrors (pbt_inner/pbt_outer) plus the BSR
  readback emitter remain.
- `tests/test_state_pipeline_contracts.py::
  test_web_poller_command_response_no_op_remains_removed` is the MOR-437 static
  guard: it rejects reintroducing `_apply_command_response_observation` or the
  `_apply_att_compatibility_mirror`/`_apply_preamp_compatibility_mirror`
  helpers, and asserts `_apply_bsr_readback_observations` and the generic
  `_apply_compatibility_mirror` helper are still present.
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
