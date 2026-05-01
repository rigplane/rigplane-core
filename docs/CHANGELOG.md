# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- **Internal: source code reorganized into explicit layered structure
  (epic #1283).** `src/icom_lan/` is now organised into 11 layered
  packages (`core/`, `commands/`, `profiles/`, `audio/`, `scope/`,
  `dsp/`, `runtime/`, `backends/`, `web/`, `rigctld/`, `cli/`) with
  per-layer charters in `LAYER.md` files and the full layout/matrix in
  `docs/plans/2026-04-29-modularization-plan.md`. **No public API
  changes** — every existing import path continues to work via
  `sys.modules`-aliased re-export shims, and the Tier 1 / Tier 2 lazy
  surface in `icom_lan/__init__.py` is unchanged.
- **Tier 1 Capability Protocols extended (epic #1322).** Three new
  protocols added to `icom_lan.radio_protocol` / re-exported from
  `icom_lan` for `isinstance`-based feature detection:
  `StatePollable` + `StatePoller` (replaces backend-id branching in
  `web_startup`, see #1298 / #1323), `RigctldRoutable` (#1324),
  `UsbAudioCapable` (#1326). `PowerControlCapable` gains
  `native_power_unit` to drop the last `web/handlers/control.py`
  backend-id discriminator (#1325).
- **`import-linter` introduced for layer-boundary enforcement.**
  Config at repo-root `.importlinter` declares one layered contract
  + three sibling-independence contracts (`web`⊥`rigctld`,
  `profiles`⊥`audio`, `commands`⊥`scope`⊥`dsp`); run locally via
  `uv run lint-imports`. CI gates every PR.
- **`_require_*()` helpers for optional deps (#1274).** New
  `src/icom_lan/_optional_deps.py` provides `_require_numpy`,
  `_require_sounddevice`, `_require_opuslib`, `_require_pillow` (and
  others where applicable). Ad-hoc `try/except ImportError` blocks across
  the codebase now share uniform error messages. Closes
  `05-recommendations.md` PR 3 (Form A enforcement).
- **`_fetch_initial_state` extracted to `radio_initial_state.py` (#1260).**
  ~75 LOC moved out of `radio.py` god-object; `IcomRadio._fetch_initial_state`
  is now a thin delegator. Public API unchanged. Final Tier 3 wave 3 sub-issue
  of #1063.
- **`_reconnect_loop` / `_watchdog_loop` extracted to `radio_reconnect.py`
  (#1259).** ~130 LOC moved out of `radio.py` god-object; `IcomRadio` methods
  remain as thin delegators. Public API unchanged. Tier 3 wave 3 of #1063.
- **`snapshot_state` / `restore_state` extracted to `radio_state_snapshot.py`
  (#1258).** ~150 LOC moved out of `radio.py` god-object; `IcomRadio` methods
  are thin delegators. Public API unchanged. Tier 3 wave 3 of #1063.
- **`_civ_rx._update_radio_state_from_frame` decomposed into table-driven
  dispatch (#1257).** The 400-line if/elif over CI-V commands now dispatches
  via `_HANDLERS: dict[int, Callable]`; each branch is a small private
  handler. Behavior preserved — verified by 72 golden-test fixtures (#1266).
  Also collapsed dead code at lines 1082-1093 (second `elif cmd == 0x12`
  block, shadowed by first occurrence). Tier 3 wave 2 of #1063.
- **Method/path routing extracted from `web/server.py` into `web_routing.py`
  (#1262).** The route dispatch — previously inline in `WebServer` — now lives
  in a dedicated module; `WebServer` delegates. Public API unchanged. Tier 3
  wave 4 of #1063.
- **`ControlButtonDemo` is now lazy-loaded (#1232).** Moved out of the main
  bundle into a code-split chunk; the demo component loads on demand when the
  debug route is mounted.
- **Type safety: removed `as any` casts in `command-bus.ts` (#1233).** The 14
  casts were replaced with precise types; any remaining cases (if any) are
  documented with `eslint-disable-next-line` and a rationale.
- **`AudioStats.jitter_ms` renamed to `reorder_depth_ema_ms` (#1231).** The
  field measures reorder-depth EMA, not RFC 3550 jitter. Internal field; no
  back-compat alias.
- **`BoundedQueue` helper extracted to `_bounded_queue.py` (#1230).** Four
  asyncio call sites (transport RX, radio scope/civ event queues, web fanout)
  now share a single bounded-queue implementation. No behavior change; drop
  policies preserved per callsite.
- **`transport._handle_packet` decomposed into dispatch table (#1239).**
  Six packet types (single/multi retransmit, ping req/reply, scope fast-path,
  generic data) now dispatch via a dict; behavior preserved.
- **`web/handlers/control.py:_enqueue_read_only` decomposed into dispatch
  table (#1263).** Same pattern as #1239 (transport): the if/elif ladder over
  command names becomes a `dict[str, Callable]` lookup, with each branch
  extracted to a small private async method (`_ro_<command>`). Behavior
  preserved. Tier 3 wave 4 of #1063.
- **Batch 1 of panel→adapter migration (#1244).** `CwPanel`, `DspPanel`,
  `MeterPanel`, `TxPanel` no longer import from `$lib/stores/capabilities`
  directly; capability flags now flow via panel-props from the wiring layer.
  Tier 2 batch 1 of #1063.
- **Batch 2 of panel→adapter migration (#1245).** New `capabilities-adapter.ts`
  centralizes capability-derived state. `RfFrontEnd`, `AudioRoutingControl`,
  and the `filter-controls.ts` / `meter-utils.ts` helpers no longer import
  `$lib/stores/capabilities` directly. Tier 2 batch 2 of #1063.
- **Batch 3 of panel→adapter migration (#1246).** `AudioSpectrumPanel`,
  `MemoryPanel`, `AmberTelemetryStrip`, `VfoControlPanel` no longer import
  from `$lib/stores/*` directly; live radio state now flows via per-panel
  adapters in `panel-adapters.ts`. Tier 2 batch 3 of #1063.
- **Batch 4 of panel→adapter migration (#1247).** New `lcd-chrome-adapter.ts`
  and `qsy-history-adapter.ts` plus an `amberScopeProps()` adapter migrate
  `LcdContrastControl`, `LcdDisplayModeControl`, `AmberMemoryStrip`, and
  `AmberScope` off direct `$lib/stores/*` imports. Tier 2 batch 4 of #1063.
- **Batch 5 (finisher) of panel→adapter migration (#1248).** `AmberCockpit`
  and `RxAudioPanel` migrated off direct `$lib/stores/*` imports. After this
  batch, no non-test panel under `components-v2/panels/` imports from
  `$lib/stores/*`. Tier 2 batch 5/5 of #1063 — unblocks #1241 (ESLint
  lockdown).
- **ESLint lockdown: panels banned from `$lib/stores/*` (#1241).** With all
  18 panels migrated to adapters across batches 1-5, the boundary is now
  enforced at lint time. Tests remain exempt for mocking purposes. Closes
  Tier 2 of #1063.
- **`WebServer.start()` / `stop()` orchestration extracted to `web_startup.py`
  (#1261).** The 200+ LOC of startup/shutdown logic now lives in a dedicated
  module; `WebServer.start()` / `stop()` are thin delegators. Public API
  unchanged. Tier 3 wave 4 of #1063.

### Fixed

- **rigctld: `chk_vfo` now returns `"0"` unconditionally for all radio profiles
  (#1319).** The dual-RX `"1"` advertising introduced in v0.17.0 (#722, #723)
  caused WSJT-X / fldigi / JS8Call to fail with "Hamlib error: Feature not
  implemented" on IC-7610, IC-9700, and FTX-1 because Hamlib's `vfo_opt` mode
  prefixes every command with a VFO token that the parser does not yet accept.
  This is a rollback to pre-v0.17.0 behaviour; full `vfo_opt` support is
  tracked as a follow-up to #1319 for v0.20.x.

### Tests

- **Public-API surface regression test (#1273).** New
  `tests/test_public_api_surface.py` asserts every tier-1 symbol from
  `docs/api/public-api-surface.md` imports cleanly AND that tier-1 imports
  do not transitively pull tier-3 modules into `sys.modules`. Closes the
  missing acceptance criterion of `05-recommendations.md` PR 1.
- **Golden-test fixtures for `_civ_rx._update_radio_state_from_frame` (#1256).**
  Added 72 synthetic frame fixtures (`tests/fixtures/civ_rx_frames.json`) and
  a parametrized dispatch test (`tests/test_civ_rx_dispatch_golden.py`).
  Combined with the existing `test_civ_rx_coverage.py`, branch coverage of
  the dispatch ladder (lines 750-1150 of `_civ_rx.py`) reaches 95.4% raw /
  98.3% excluding the unreachable cmd 0x12 duplicate block at lines 1082-1093.
  Tier 3 wave 1 of #1063 — fences the upcoming table-driven dispatch refactor
  (#1257).

### Docs

- **`local-extensions/` host API documented as Tier 1 Pro-facing contract
  (#1277).** `docs/api/public-api-surface.md` now lists the exported types
  and functions from `frontend/src/lib/local-extensions/{host-api,manifest}.ts`
  with breakage policy. Closes the contract-visibility gap identified
  during the strategic-context analysis.
- **New `docs/architecture/open-core-policy.md` (#1276).** Codifies hard
  constraints on icom-lan as the open-core half of a planned commercial
  product: no telemetry, headless sacred, no hollowing out, Radio protocol
  + `local-extensions/` as the Pro boundary, frontend WebKitGTK-floor
  compatibility. CLAUDE.md gains a one-line cross-link.
- **`AudioBackend` Protocol docstring promoted to stability marker (#1275).**
  The Protocol (and `PortAudioBackend` / `FakeAudioBackend` impls) now
  document their tier and breakage policy in their own docstrings, matching
  `docs/api/public-api-surface.md` (Tier 2 — Best-effort, lazily exposed via
  PEP 562 `__getattr__`). Closes `05-recommendations.md` PR 4.
- **`docs/api/public-api-surface.md` Tier 1 list corrected (#1273).** Removed
  `Meter` from the tier-1 public-types bullet — there is no `Meter` symbol
  exported from `icom_lan` (only `MeterType`, which is tier-3). Discovered
  while writing the tier-1 surface regression test.
- **Panel → adapter migration plan (#1240).** Doc at
  `docs/plans/2026-04-29-panel-adapter-migration.md` inventories the 18
  remaining panels with direct `$lib/stores/*` imports, groups them into
  clusters with proposed adapters, and breaks migration into ≤4-panel
  batches. Concrete batch sub-issues will open from this plan. (Relocated
  from `frontend/docs/` — that path is gitignored as "Frontend internal
  docs"; this doc sits beside the target-frontend-architecture ADR
  instead.)

### Deprecated

- **Web UI v1 layout shell (#874, #1220).** The legacy `AppShell` /
  `DesktopLayout` / `MobileLayout` tree under `frontend/src/components/layout/`
  is deprecated; v2 (`RadioLayoutV2` + `frontend/src/components-v2/`) is the
  only supported path going forward. v2 has been the default since v0.15.1.
  The `?ui=v1` URL fallback is on track for removal alongside the v1 code
  drop (tracked under epic #874 / sub-issues #1216, #1217). Documentation
  has been updated to v2-only language in this release; user-facing docs no
  longer describe the v1 shell.

### Removed

- **`meter_cal._TABLES` and `meter_cal.calibrate()` (#1209).** The hardcoded
  IC-7610 calibration tables and the `calibrate()` lookup wrapper were
  unreachable since #1173 shipped per-rig TOML calibration in v0.19. All
  consumers route through `interpolate_swr` against
  `RadioProfile.meter_calibrations` (loaded from TOML
  `[[meters.<name>.calibration]]`). No public-API impact —
  `MeterType` and `interpolate_swr` remain exported.
- **`IcomRadio.set_split_mode` deprecation alias (#1205).** The async method
  on `icom_lan.radio.IcomRadio`, the sync wrapper on `icom_lan.sync.IcomRadio`,
  and the `profiles_runtime.apply_profile` fallback branch are gone. Use
  `set_split(...)` (`SplitCapable` protocol) — the canonical name introduced
  in #1108. Deprecation was announced in v0.19; this is the scheduled v0.20
  cleanup.
- **`IcomRadio.get_alc` / `sync.IcomRadio.get_alc` (#1207)** — deprecated in
  v0.19 (#1129), removed per schedule. Use `get_alc_meter`
  (`MetersCapable`).
- **`icom_lan.commands.levels` backward-compat aliases (#1208).** The
  `get_power` / `set_power` / `get_sql` / `set_sql` aliases (deprecated in
  v0.19, #1167 + #1182) are gone — both the PEP 562 `__getattr__` shim and
  the sentinel `__all__` entries in `icom_lan.commands.levels` and
  `icom_lan.commands` have been deleted. Use the canonical builders
  directly: `get_rf_power`, `set_rf_power`, `get_squelch`, `set_squelch`.
- **`IcomRadio.set_vfo("A"/"B"/"MAIN"/"SUB")` legacy overload + `select_vfo`
  alias (#1206).** Deprecated in v0.19 (#1187, #1172) and replaced by the
  receiver-tier protocols `ReceiverBankCapable.select_receiver` (MAIN/SUB)
  and `VfoSlotCapable.set_vfo_slot` (A/B). The matching `SyncRadio.set_vfo`
  / `SyncRadio.select_vfo` wrappers are removed in lockstep. Internal
  callers (`apply_profile`, `restore_state`) now route through the
  capability protocols or the silent `_set_vfo_wire` helper. Legacy fallback
  paths in `web/radio_poller.py` and `rigctld/handler.py` remain for
  third-party backends that only expose the legacy overload.

## [0.19.0] — 2026-04-29

### Tier-1 API stability commitment

- **API: tier-1 stability commitment from v0.19 (#1195).** The public API
  surface is now organised into three explicit tiers (stable / best-effort /
  internal) with a documented migration policy. See
  `docs/api/public-api-surface.md` for the tier policy, full symbol lists,
  and import examples.
- **Lazy `__init__.py` via PEP 562 (#1194).** Trimmed eager imports from 203
  to 71 lines; tier-2 symbols now lazy-load on first access. `from icom_lan
  import Radio` no longer transitively pulls in `web/`, `cli`, `rigctld/`,
  or `audio.backend` — measured ~13 % fewer submodules in `sys.modules`.
- **Layering enforcement via ruff TID251 (#1196).** Tier-3 internals
  (`icom_lan.web.*`, `icom_lan.rigctld.*`, `icom_lan.cli`) now banned from
  cross-tree imports. Pre-existing `icom_lan.radio.IcomRadio` ban preserved
  in web modules (#1201).

### Added

- **Receiver-tier protocols on backends.** `ReceiverBankCapable` and
  `VfoSlotCapable` (declared since #711, never implemented) now have
  concrete impls on both Icom (#1170) and Yaesu CAT (#1171) backends.
  Profile-driven dispatch covers IC-7610 / IC-9700 / IC-7300 / IC-705 and
  FTX-1 / Lab599 / X6100 single-RX rigs.
- **`SplitCapable` protocol (#1108)** — universal split control across all
  supported HF/VHF rigs. `set_split_mode` deprecated, alias retained until
  v0.20.
- **`RitXitCapable` protocol (#1099)** — extracted from
  `TransceiverStatusCapable`. Six canonical `*_rit_*` methods on
  YaesuCatRadio plus read-modify-write fix preserving the unaffected RX/TX
  bit on CF000.
- **17 protocol declarations** added across existing capability protocols:
  `DspControlCapable` (filter family, notch, agc), `LevelsCapable`
  (af_level/rf_gain getters, squelch), `MetersCapable` (power_meter,
  alc_meter, swr_meter), `AudioCapable` (codec/sample_rate properties),
  `ScopeCapable` (getters, scope_stream), `VoiceControlCapable`
  (get_compressor), `CwControlCapable` (break_in), `AntennaControlCapable`
  (get_attenuator), `PowerControlCapable` (get_rf_power lift).
- **Default web UX (#1087):** `icom-lan web` now auto-detects loopback and
  enables the audio bridge by default; rigctld serves on 4532 by default
  with `--no-rigctld` opt-out (#1088, #1089).
- **`[bridge]` extras folded into core (#1090).** `pip install icom-lan`
  ships `opuslib`, `sounddevice`, and `numpy` out of the box. Legacy
  `[audio]` and `[bridge]` extras retained as no-op aliases.
- **Calibrated SWR float on Icom rigs (#1173).** `IcomRadio.get_swr` now
  returns calibrated SWR (1.0–6.0+) via TOML calibration tables (5 anchor
  points per rig, sourced from official Icom CI-V references). New
  `get_swr_meter()` on async + sync API for raw 0-255 access.
- **`SetPower` poller dataclass unit-tagged (#1168)** — explicit
  `unit="raw_255"` (Icom default) vs `"watts"` (Yaesu) ends silent
  Icom/Yaesu unit mismatch.
- **State-contract sweep (#1169):** `RadioState.cw_spot` is now tri-state
  (`bool | None`); Yaesu-specific `rx_func_mode`/`tx_func_mode` moved into
  `YaesuStateExtension`.
- **Frontend runtime architecture (epic #708 follow-up).** `FrontendRuntime`
  singleton with `ScopeController`, lib/runtime/ pattern, panel-props /
  panel-commands separation enforced via ESLint.

### Changed

- **`__init__.py` is now ~80 lines** (was 203). Tier-1 symbols eager;
  everything else lazy via PEP 562 `__getattr__`.
- **Audio extras simplified.** `opuslib`, `sounddevice`, `numpy` moved from
  `[bridge]` extra into the main `dependencies` list. `[dsp]` extra now
  installs only `scipy>=1.11`. Documentation updated accordingly (#1090).
- **`set_vfo("A"/"B"/"MAIN"/"SUB")` overload deprecated** (#1187, #1172).
  Web and rigctld migrated to `select_receiver` / `set_vfo_slot`. Legacy
  overload emits `DeprecationWarning`; removal scheduled for v0.20.
- **Filter-width unified on segmented BCD index (#1157)** per wfview
  reference. Removed `direct_bcd_hz` profile encoding (was incorrect for
  IC-705 / IC-9700). Added per-mode segment tables to all four Icom rigs.
- **Scope poller uses public ScopeCapable getters (#1166)** with bounded
  per-call timeout (#1186). Eliminates raw `_civ(0x27, …)` layering
  violation while preserving fire-and-forget semantics (#1188).

### Fixed

- **6 rigctld consistency fixes (consolidating P0-classified findings now
  reclassified P3 — only Yaesu-routing path was active):**
  dial-lock (#1092), tuner-status (#1094), squelch dispatch (#1093),
  notch-filter (#1102), powerstat stub (#1095), set_level SQL set-side
  (#1163).
- **Yaesu compressor-level alias delegation (#1098)** — was returning
  hardcoded 0; now correctly forwards to `*_processor_level`.
- **Yaesu APF mode reachable from web (#1110)** — poller no longer drops
  `SetApf` actions; mode-1 toggle preserves user-tuned freq (#1141).
- **`set_vfo` legacy fallback for backends without `ReceiverBankCapable`
  (#1189)** — `SerialMockRadio` and similar legacy backends no longer
  silently no-op `V VFOA` / `V VFOB`.
- **`get_split` cache fallback honors `TimeoutError` (#1158)**, not just
  `CommandError` — completes the documented fallback contract.
- **CW pitch idx ↔ Hz conversion on Yaesu (#1162)** — `get_cw_pitch` /
  `set_cw_pitch` now correctly translate Yaesu's 0-75 idx to/from 300-1050
  Hz, fixing silent state corruption in `state.cw_pitch`.
- **CI-V worker cancel propagation (#1188)** — caller-side `wait_for`
  cancel now cancels the in-flight CI-V command at worker level, preventing
  cascade-skipping of subsequent queued commands.
- **Layering violation in scope poller (#1166)** and **filter-width
  encoding** (#1101) — moved from raw `_civ` in web/ into backend
  protocol methods.
- **IC-7300 GET decode bug** — silently read 2 BCD bytes when radio sent 1;
  fixed during filter-width unification.
- **VfoSlotCapable RTW bug on Yaesu (#1099)** — `set_rit_status` /
  `set_rit_tx_status` now read CF000 first to preserve the unaffected
  RX/TX bit (P1-02 from audit catalog).
- **Numerous Codex post-merge review fixes** — covering dispatch table
  gaps, capability fallbacks, type-signature drift, and exception-narrowing
  across `web/` and `rigctld/`.

### Removed

- **`vfo_exchange` / `vfo_equalize` aliases (#1114)** — deprecated since
  v0.17, removed per accelerated Q4 deprecation policy.
- **Seven LAN audio aliases overdue from v0.15 (#1111)** —
  `start_audio_rx`, `stop_audio_rx`, `start_audio_tx`, `push_audio_tx`,
  `start_audio`, `stop_audio`, `stop_audio_tx`. Use the canonical
  `*_opus`-suffixed names.
- **Internal facade helpers privatised (#1112):** `_push_pcm_tx`,
  `_push_tx_pcm`, `_has_command`, `_has_write_command`. Public
  `supports_command` is the canonical introspection API.
- **`audio_capabilities()` instance method** — use module-level
  `types.get_audio_capabilities()`.
- **`direct_bcd_hz` filter-width encoding** — no Icom rig actually used it;
  unified on segmented BCD index per wfview.

### Deprecated

- **`icom_lan.commands.levels` backward-compat aliases (#1167):**
  `get_power`, `set_power`, `get_sql`, `set_sql`. Removal v0.20. Use
  canonical `get_rf_power` / `set_rf_power` / `get_squelch` / `set_squelch`.
- **`set_split_mode` (Icom)** — replaced by `set_split` (`SplitCapable`).
  Removal v0.20.
- **`set_vfo("A"/"B"/"MAIN"/"SUB")` overload** — replaced by
  `select_receiver` + `set_vfo_slot`. Removal v0.20.
- **`get_alc` (Icom)** — replaced by `get_alc_meter` (`MetersCapable`).

### Docs

- New "Stability tiers" section in `docs/api/public-api-surface.md`
  documents tier-1 / 2 / 3 policy with full symbol lists and migration
  rules.
- Per-rig SWR calibration anchor tables documented in `rigs/*.toml` per R1
  research (sourced from official Icom CI-V References + wfview).

### Internal

- 47 PRs landed across this release cycle including audit work, Tier-1
  stabilization, Form H UX defaults, Form F sealing, and Codex automated
  review follow-ups.
- 7 architectural epics closed: API audit (#1071), v0.18.1 hotfix bundle
  (#1091), Tier-1 stabilization (#1096), Form H UX defaults (#1087),
  Codex post-merge sweep (#1140), audit closure (#1165), Form F sealing
  (#1193).

## [0.18.0] — 2026-04-19

### Added

- **Meter responsiveness (epic #936)** — backend priority polling tier plus
  frontend rAF needle smoothing. S-meter on RX jumps from ~3 Hz to an
  effective 16 Hz with asymmetric visual smoothing (50 ms attack /
  150 ms release); Pwr/SWR/ALC on TX go from ~3 Hz to 6.7 Hz each.
  PTT-on skips the LOW tier so TX-tier meters meet the smooth-needle
  acceptance target; Vd/Id/Comp deferred to a LOW tier (~750 ms each).
  (#936, #937, #938, #941)
- **Amber-LCD twin skins (epic #887)** — `lcd-cockpit` and `lcd-scope`
  skin variants with dedicated wrappers, 60/40 scope-dominant grid for
  single-RX, peer dual-cockpit grid for dual-RX, per-VFO indicator
  zones + global strip, AmberAfScope dominant mode + running-max line,
  ghost-graticule fallback when AF-FFT is unavailable, memory /
  recent-QSY strip, telemetry strip (VD/TEMP/ID + sparklines), Display
  Mode effects (vintage / CRT / flicker), LCD contrast in the
  control-strip, warm-dark theme. (#808, #823, #836, #837, #838, #861,
  #864, #877, #887-#895, #896-#900, #902, #904-#908, #911, #914, #915,
  #916, #918, #919, #920, #921, #929, #932, #933)
- **Mobile IA overhaul** — chip-scroll navigation + ESSENTIALS panel;
  persistent guarded PTT FAB; container-query collapse + aux row
  reserve; first-class RIT/XIT mobile chip; auto-collapse mode-specific
  panels. (#810, #839, #840, #842, #843, #857, #885, #894, #912, #926,
  #928, #930)
- **MetersDockPanel (epic #820)** — new station-health dock with
  Po/SWR/ALC/S tiles plus Id/Vd/COMP tiles gated by capabilities;
  peak-hold and SWR/ALC fault highlighting; replaced legacy bottom-dock
  cards; audio spectrum relocated accordingly. (#820, #821, #822, #823,
  #848, #866, #872, #878, #880)
- **Active-receiver UI (epic #825)** — `ActiveReceiverToggle` segmented
  `[M|S]` control; keyboard bindings for active-receiver switch with
  audio-focus sync; legacy activate-chip + adapter removed. (#825,
  #827, #828, #856, #858, #868, #875)
- **Skin system** — StatusBar dropdown skin-switcher; `lcd-cockpit` /
  `lcd-scope` registered as first-class skin IDs; variant prop threaded
  through dedicated wrappers. (#888, #889, #895, #901, #902, #904,
  #909, #913)
- **Spectrum toolbar** — keyboard shortcuts; grouping + separators +
  visual spec. (#830, #831, #847, #855)
- **Settings reorg** — settings gear moved into StatusBar; subgrid VFO
  digit/badge layout resolves crowding. (#807, #860, #865, #871)

### Changed

- **LCD token cascade + contrast core** — unified grid scaffold,
  AmberCockpit extracted, behavior-preserving refactors. (#833, #844,
  #853, #859, #890, #903)
- **Panel simplification** — removed panel-wide click-to-activate and
  STANDBY/ACTIVE pill; consolidated ON/OFF into a single power toggle;
  renamed SETTINGS → SETUP and pruned chip-duplicated panels;
  strengthened active/inactive VFO panel treatment. (#805, #824, #826,
  #841, #849, #854, #867, #925)
- **Bottom-dock reshuffle** — replaced bottom-dock cards with
  `MetersDockPanel`; relocated `AudioSpectrumPanel`; BRT/REF moved to
  mobile spectrum gear; SCOPE status moved to `VfoHeader` bridge. (#812,
  #821, #832, #869, #870, #880)

### Fixed

- **Scope reconnect deadlock** — break scope re-enable deadlock on
  reconnect. (#881)
- **CI-V watchdog** — self-cancel + patient OpenClose recovery. (#851)
- **Session rejection** — retry-with-mono fallback on stereo `rx_codec`
  session rejection. (#797, #802)
- **SWR calibration** — honor TOML calibration table at `raw=0`;
  non-linear calibration from TOML config. (#440, #924, #927)
- **hamlib rig model** — read from TOML in Yaesu `dump_state`. (#441,
  #923)
- **TX meter telemetry** — preserved when SUB is active; Vd tile
  readable in RX idle. (#822, #872, #891, #910)
- **AmberScope / AmberCockpit fallbacks** — VFO A filter-width fallback
  restored; ghost fallback when AF-FFT unavailable. (#918, #919, #920,
  #921)
- **Scope controls on mobile + v1** — restored source/dual controls;
  SCOPE pills no longer cropped by VFO bridge column overflow. (#832,
  #873, #883)
- **lcd-scope variant reachability** — end-to-end variant plumbing via
  dedicated skin wrappers. (#895, #909, #913)
- **v2-control-button** — distinguish idle vs disabled states. (#804,
  #879)
- **RightSidebar** — restored `AudioSpectrumPanel`; preserve
  cross-sidebar drag in `loadPanelOrder`. (#884, #886)
- **Codex review batches** — P1/P2 findings resolved across mobile grid
  rows, IP+ per-RX, legacy LCD, QSY debounce + orientation PTT release,
  tile-smoother pruning on capability toggle. (#887, #917, #931, #934,
  #935, #941)

### Docs

- LCD twin-skin redesign plan (epic #887).
- UI refinement design spikes (epic #818, #819).

### Chores

- Repo-wide `ruff format` pass (#922).
- 53 Svelte build warnings cleared — build now emits 0 warnings.
- `chore(#828)` VFO area tooltip audit + standardization (#882).
- `chore(#829)` StatusBar tooltip audit + standardization (#876).

## [0.17.0] — 2026-04-18

### Added

- **Dual-RX stereo LAN audio (epic #787)** — IC-7610 now delivers true stereo
  L=MAIN, R=SUB over LAN.  Backend negotiates `PCM_2CH_16BIT` in conninfo,
  locks `Phones L/R Mix = OFF` at relay start, and gates the behaviour on a
  new `lan_dual_rx_audio_routing` capability so IC-9700 / FTX-1 aren't
  affected.  Frontend resolves `focus` × `split_stereo` via a WebAudio
  ChannelSplitter + per-channel gain + panner graph — no CI-V round-trip
  for routing. (#752, #753, #756, #757, #770, #775, #776, #777, #778, #779,
  #781, #787, #788, #789, #790, #791, #792, #793, #794, #795, #798, #799,
  #800)
- **Dual VFO / dual receiver model (epic #708)** — new `ReceiverBankCapable`,
  `VfoSlotCapable`, `TransceiverBankCapable` protocols; per-receiver A/B
  `VfoSlotState`; split `swap_ab` / `swap_main_sub` command codes;
  `DualVfoDisplay` showing both MAIN+SUB on the desktop skin; receiver
  focus selector on the mobile skin. (#708, #709, #710, #711, #712, #714,
  #715, #716, #717, #718, #719, #722)
- **rigctld full VFOA/VFOB protocol** — implements the complete Hamlib
  per-VFO command set so split-aware digital-mode clients (WSJT-X, fldigi,
  JS8Call) round-trip cleanly. (#722, #723)
- **Composite WS commands** — `quick_dualwatch` and `quick_split` batch the
  radio side of the DW/split setup into single commands with
  double-click / long-press affordances in the UI. (#775, #776, #778, #779)
- **CLI log rotation** — `RotatingFileHandler` controlled by
  `ICOM_LOG_MAX_BYTES` and `ICOM_LOG_BACKUP_COUNT` env vars; prevents
  unbounded growth of `logs/icom-lan.log`.

### Changed

- **IC-7610 profile** declares `lan_dual_rx_audio_routing` capability and
  migrates VFO codes to `swap_main_sub` / `equal_main_sub` plus a new
  `0x14 0x0D` cmd29 route. (#748)
- **IC-9700 profile** correctly declares the MAIN/SUB scheme with proper
  byte codes. (#713)
- **IC-705 / IC-7300** migrated from legacy `swap` / `equal` keys to
  `swap_ab` / `equal_ab`.
- **VfoPanel** now uses `receiverLabel` + `vfoSlotLabel` for correct
  dual-VFO display naming. (#747, #728)
- **RFC §11** documents the dual-RX LAN routing contract: wire format,
  the Phones L/R Mix-OFF invariant, the focus × split_stereo gain/pan
  table, and the historical context of the `0x02` / `0x03` trap.

### Fixed

- **IC-7610 LAN session allocation** — decoupled `tx_codec` from
  `rx_codec` in conninfo; stock firmware rejected the session with
  `error=0xFFFFFFFF` when `tx_codec` was a 2-channel value (mic path is
  mono-only; wfview UI enforces the same constraint). (#794, #795)
- **CollapsiblePanel phantom-collapse on hover** — left-sidebar panels
  spontaneously toggled when the mouse passed over their headers.  Root
  cause was an uninitialised swipe-tracking state that was updated on
  plain `pointermove`; now gated on `swipeActive` set only by
  `pointerdown`. (#796)
- **Audio WebSocket queue** — `audio_config` WS send is queued when the
  socket is not yet open and flushed on `onopen`, so ACTIVATE on
  MAIN/SUB always reaches the radio. (#786)
- **Optimistic UI state** — equalize / swap operations update the UI
  immediately; audio focus follows ACTIVATE. (#785)
- **Scope follows active receiver** — spectrum/waterfall switches to the
  newly selected MAIN/SUB band on every `0x07 0xD0/0xD1`. (#784)
- **Broadcaster mid-stream codec refresh** — picks up codec / channel /
  sample-rate changes without reconnecting. (#766, #769)
- **Broadcaster frame_ms** — derived from actual payload size, not
  hard-coded 20 ms; fixes label mismatch on IC-7610 PCM16 packets. (#765)
- **VFO MAIN/SUB buttons** now emit proper receiver-select
  (`0x07 0xD0/0xD1`), not the MAIN↔SUB swap hack. (#773)
- **MAIN↔SUB poller flip** on IC-7610 + silence `Radio.__del__` test
  warnings. (#751)
- **sync.py default codec** aligned with the async `IcomRadio` default —
  both paths now return `PCM_2CH_16BIT` by default. (#798)
- **vitest flakiness** — split into fast + isolated projects to stabilise
  keyboard-wiring tests. (#771, #782)
- **post-review P1** — `swap_vfo_ab` safety + rigctld split rollback. (#746)
- **rig loader** parses `transceiver_count` from `[radio]` section. (#745)
- **DualVfoDisplay** — dedicated activate button resolves WCAG 4.1.2. (#744)
- **command-bus** — tighten focus→mode handoff race. (#720)

### Docs

- Dual-RX / transceiver / receiver / VFO model primer in
  `docs/internals`. (#724)
- IC-7610 cmd29 parity reconciled with wfview's `IC-7610.rig`. (#725)
- Opus DSP/tap gate behavior + one-shot warning documented. (#762)

### Chores

- `chore: mypy cleanup` — zero errors on default install; `numpy` /
  `sounddevice` / `opuslib` optional-dep imports now ignored via
  `[[tool.mypy.overrides]]`.
- Delete speculative `AudioBufferPool` + PCM-8 mapping. (#765, #768)

## [0.16.4] — 2026-04-16

### Fixed
- **Web UI TX audio:** transcoder now uses the radio's negotiated sample rate
  (was hard-coded to 48 kHz, silently dropped TX on radios negotiated at 24 kHz) (#691)

### Changed
- **Test suite performance:** backend 87.6s → 64s (−27%), frontend 10.6s → 5.8s (−45%);
  CI per matrix job ~4m30s → ~2m00s (−55%) (#706, #707)

## [0.16.3] — 2026-04-16

### Fixed
- **Web UI:** resolve 33 issues across controls, sync, errors, layout, a11y, and
  performance — wiring layer (DW/VFO targeting), control panels (freq keyboard,
  ATU/ATT/APF), canvas perf (rAF idle loop, DX cap, gradient cache), error
  notifications (Toast mounted in v2 layouts), connection state (WS reconnect,
  scope/audio indicators), waterfall resize preservation (#693, #694–#702)
- **DSP pipeline:** add sample rate validation and auto-resampling for RNNoise (#692)
- **Audio broadcaster:** resolve subscriber leak and pong-timeout loop (#687, #690)
- **Audio WebSocket:** fix crash loop on PTT (#684, #688)
- **CLI:** hard errors for invalid inputs, silent ignores, startup ordering (#689)
- **CLI:** hard errors for explicitly requested features with validation (#686)

## [0.16.2] — 2026-04-15

### Added
- **Companion tuning step sync** — tuning step is now synced to the RC-28
  companion dispatcher via `PUT /api/local/v1/rc28/tuning-step`; incoming
  `companion_state` WS messages update the step in real time
- **WsChannel.reconnect()** — reconnects a WebSocket channel using its
  last-known URL, enabling full lifecycle restore after disconnect

### Fixed
- **Connect/Disconnect lifecycle** — the web UI button now controls the
  entire frontend connection: all WebSocket channels (control, scope,
  audio), HTTP polling, and MediaSession are torn down on Disconnect and
  restored on Connect; the server↔radio connection is never affected
- **Scope/audio channels survive reconnect** — `reconnectAll()` reopens
  all named WS channels (scope, audio-scope) after a disconnect+connect
  cycle; previously they stayed dead until page reload
- **StatusBar state tracking** — connect button now tracks `controlState`
  (WS+HTTP) instead of `radioState` (server↔radio), so the UI updates
  immediately on disconnect/connect
- **Transport warning noise** — suppressed misleading `_packet_pump`
  warning and reduced UDP error log verbosity
- **Companion auto-step preservation** — `setTuningStepFromCompanion()`
  preserves the auto-step preference when syncing from companion

## [0.16.1] — 2026-04-14

### Fixed
- **LAN discovery crash** — `OSError: [Errno 65] No route to host` when network
  is unavailable no longer produces a raw traceback; CLI prints a clear message
  and suggests using `--host` explicitly
- **CI strict mypy** — resolved `no-any-return` in `radio_poller.py` for
  `mypy --strict` boundary check
- **Dynamic CI badges** — tests, version, and mypy badges in README now
  auto-update from CI via gist-backed shields.io endpoints

## [0.16.0] — 2026-04-14

### Added
- **DSP Pipeline** (Epic #682) — pluggable audio processing framework:
  - `DSPNode` Protocol, `DSPPipeline` engine, `PassthroughNode`, `GainNode`
  - `NRScipyNode` — spectral subtraction noise reduction (scipy FFT)
  - `TapRegistry` — multi-consumer PCM analysis bus
  - Inter-node resampling utility; `[dsp]` optional dependency group
- **CW Auto Tuner** (#675, #677, #678) — FFT peak detection engine (`CwAutoTuner`),
  backend `cw_auto_tune` command, restored AUTO TUNE button in Web UI
- **AudioAnalyzer** (#679) — realtime SNR estimation from PCM stream
- **UDP Discovery Responder** — companion apps can broadcast `ICOM_LAN_DISCOVER` on
  UDP 8470 and receive server URL, version, and radio status via unicast;
  `--no-discovery` CLI flag to opt out
- **Unified frontend architecture** (Epics #647–#653, #662–#665) — `FrontendRuntime`
  singleton, skin registry, runtime adapters, self-wired panels (AGC, Mode, Antenna,
  RfFrontEnd, RIT/XIT, Scan, CW, DSP, TX, Filter, BandSelector), eslint import
  boundary guardrails, LCD and mobile layout migration to unified runtime path
- **SUB receiver polling** (#562, #563) — TOML commands, receiver routing, AF/RF/squelch
  level polling in slow loop
- **TX meters** (#559) — ALC, Power, COMP, SWR polling during transmit
- **Scope backpressure** (#533) — adaptive poller gap, scope backlog shedding hook,
  `queue_pressure` metric on `IcomTransport`
- **Initial state fetch** (#532) — `_fetch_initial_state()` on connect and reconnect,
  readiness-gated state snapshot in WebServer
- **Cross-sidebar drag** (#566–#568) — move panels between left/right sidebars,
  localStorage persistence, dynamic panel rendering
- **Yaesu FTX-1 enhancements** (#551) — IF bulk query, clarifier clear, APF, CW spot,
  break-in delay, power switch (PS), data mode methods
- **IC-7300 improvements** (#545, #546, #564) — segmented BCD filter width encoding,
  scope marker TOML entry, cleanup of NOT_IMPLEMENTED comments
- **Meter calibration** (#556) — power/SWR/ALC tables in `ic7610.toml`, scope REF
  range constraints, `meter_redlines` in RadioProfile, generic calibration accessors
- **SystemController** (#665) — centralized HTTP system actions
- **Skin abstraction** (#326) — `ProfessionalSkin` (Phase 1)
- **Frontend test coverage** (#555) — component-level tests for LCD, Mobile, Spectrum,
  BandPlan, DspPanel, CwPanel, SpectrumToolbar, DxOverlay, EiBi, state-adapter,
  ws-client, radio store, audio subsystem
- **FTX-1 polling tests** (#551) — integration test suite for Yaesu CAT poller

### Changed
- **Single version source** — `__version__` now reads from `pyproject.toml` via
  `importlib.metadata` instead of being hardcoded in `__init__.py`
- **Frontend panel architecture** — extracted DspPanel + CwPanel logic to dedicated
  panel-logic modules (#594); extracted SpectrumToolbar, BandPlanOverlay,
  MobileRadioLayout, SpectrumPanel inline logic to separate files (#590–#593)
- **LCD layout** (#636) — adapts to reduced viewport height

### Fixed
- **CW auto tune** (#671) — reverted incorrect `cw_sync_tune`, removed broken
  AUTO TUNE button before reimplementing correctly
- **Shutdown reliability** (#634) — `os._exit()` for orphaned threads, manual loop
  with executor timeout, PortAudio stream close before task cancel, shutdown step
  timeouts
- **Audio stability** — drop frames while `AudioContext` suspended, resume once in
  `start()` instead of per-frame
- **Yaesu serial** — report `disconnected` status correctly, show serial port in
  startup banner, graceful poller disconnect handling
- **Connection readiness** (#602) — expose readiness fields from backend state
- **Frontend null guards** (#603–#605) — null receiver state, null numeric fields
  coerced to defaults, encoder revision for initial state snapshot
- **Disconnect cleanup** (#600) — clear stale state, reset delta and radio store
- **Code review fixes** (#670, #576) — 5 findings from session audit, layering and
  model guards, reconnect timing
- **Drag-reorder** — unregister instances from registry on component destroy
- **All mypy errors resolved** — `ControlPhaseHost` protocol gap, `YaesuCatRadio`
  missing `get_data_mode`/`set_data_mode`, scipy stubs, `no-any-return` fixes
- **All ruff errors resolved** — unused imports in test_cli.py

### Documentation
- Refreshed Web UI guide for v2 runtime and skin workflows (#681)

## [0.15.1] — 2026-04-10

### Changed
- **Web UI v2 is now the default layout.** New visitors and fresh installs see the
  redesigned RadioLayout v2 interface. Users who previously selected v1 keep their
  choice (persisted in localStorage). Switch manually with `?ui=v1` or `?ui=v2`.

## [0.15.0] — 2026-04-10

### Added
- **Zero-config CLI startup** (Epic #526) — `icom-lan web` auto-discovers radio via LAN broadcast,
  `--preset hamradio|digimode|serial|headless` for common scenarios, smart startup banner with
  loopback device hints (#527, #528, #529, #530).
- **Drag-and-drop panel reorder** — drag handles on right sidebar panels (#557).
- **Complete CI-V command coverage** (Epic #535) — scope settings popover (#538), missing polling
  entries (#539), VOX/CW/DSP panels (#540), TX band edge support (#541), memory channel
  manager + scan modes (#542, #543), TX meters + scope toolbar controls (#536, #537).
- **Center dead zone for RF/SQL dual slider** — prevents accidental threshold jumps.
- **Poller deadlock regression tests** (#554) — state consistency + deadlock detection tests.
- **Yaesu CAT backend and CLI factory routing** — `--backend yaesu-cat`, capability-based polling,
  rigctl routing strategy, Web ControlHandler support, meters/advanced-control conformance,
  and follow-up code review fixes for issues #427-#445.
- **Universal radio profile system** — declarative `OperatingProfile` / `apply_profile()` /
  `PRESETS`, packet/data profile helpers for IC-705, and additional sync control methods.
- **TLS/HTTPS for Web UI** — HTTPS listener support with automatic self-signed certificates (#205).
- **Audio FFT UI work** — full-color `AudioSpectrumPanel`, standard-layout integration, audio-scope
  WebSocket channel, variable FFT bandwidth handling, and audio spectrum rendering fixes.
- **Expanded Web/rigctld command coverage** — raw CI-V passthrough, levels/functions support,
  data mode inputs/levels, VOX, tone/TSQL, CW text/stop, band/split, system/config,
  selected/unselected freq+mode, memory API support, and scope toolbar controls.
- **Capability/tag cleanup** — extracted `capabilities.py`, added `system_settings` tag,
  `supports_command()` on the Radio protocol, and removed remaining protocol abstraction gaps.
- **Issue #448 UI/antenna work** — v2 antenna panel, capability/state tracking, corrected IC-7610
  TX ANT vs RX ANT semantics, and startup readiness checks split between connect-time validation
  and server-side guards.

### Changed
- **Connection readiness contract** — `radio.connect()` now owns bounded wait-for-ready and fails
  if the radio never becomes usable; Web UI and rigctld startup now perform instant guards only.
- **Protocol/capability routing** — replaced several `isinstance(AdvancedControlCapable)` checks with
  capability tags and centralized capability constants.
- **Spectrum/waterfall interaction architecture** — clean separation of gesture, drag, and tune layers.
- **Frontend/test hygiene** — resolved Svelte/type issues, fixed frontend redesign regressions,
  refreshed API docs and badges, and updated test fixtures for stricter protocol mocks.

### Fixed
- **Meter calibration** (#536) — corrected S-meter, RF power, SWR, ALC calibration tables per
  IC-7610 CI-V Reference p.4; dimmed irrelevant meter rows.
- **Scope REF BCD encode/decode** (#553) — fixed to match IC-7610 CI-V Reference p.15.
- **CENTER Type polling** (#552) — fixed root cause: poller was overwriting scope CENTER Type
  to Filter on every poll cycle; restored CTR mode indicator at center position.
- **Tuning indicator** (#552) — proportional positioning + scope REF display.
- **Deadlock: EnableScope** — EnableScope await blocked all commands during initial fetch.
- **Click-to-tune** — only on waterfall, not spectrum area; via pointerup instead of click event.
- **Reliable shutdown** — 3-tier signal handling, reuse_address for TIME_WAIT, force exit on
  second Ctrl-C, proper audio relay shutdown order.
- **AF scope** — bandwidth tracks actual filter width, crash fix when center_freq is 0.
- **Power-off state** not detected on server restart.
- **Startup fail-fast** — added pre-flight port check (#422), fail-fast on `civ_port=0` (#424),
  and eliminated half-working Web/rigctld startups when the radio transport is not actually ready.
- **IC-705 Wi-Fi binding** — hardened routed local bind handling and validated LAN support.
- **Audio/runtime stability** — fixed broadcaster restart behavior, audio handler lifecycle,
  control transport queue overflow after long runs, and Python 3.13 flaky tests (#398).
- **Scope/UI correctness** — fixed scope dispatch capability checks, scope polling/state updates,
  step-control width, BCD span payloads, speed arrow direction, PTT TX wiring, and optimistic
  state sync for antenna/scope controls.
- **Type-check/lint cleanup** — resolved all 188 ruff lint errors and 499 mypy type errors:
  file-level noqa for re-export modules, mixin TYPE_CHECKING base pattern, per-module mypy
  overrides for duck-typing consumers, and ControlPhaseHost protocol expansion.

### Documentation
- Added/updated Radio Profiles guide, web/rigctld API references, and test badges/documentation sync.

## [0.14.2] — 2026-03-27

### Changed
- **Git cleanup** — removed 83 tracked files (-33k lines): backups, internal dev docs
  (plans/sprints/reviews/audits), scripts, mockups, references, credentials in run-dev.sh
- **Documentation refresh** — index.md, radios.md, README.md updated for multi-vendor reality;
  FTX-1 moved from "planned" to "tested"; mkdocs nav expanded with 12 missing pages;
  5 broken links fixed; mkdocs build --strict passes clean
- **CI fixed** — removed parity matrix tests (depended on deleted files); marked 2 flaky
  reconnect tests as xfail (#398); CI green on Python 3.11/3.12/3.13

## [0.14.1] — 2026-03-27

### Fixed
- FTX-1 LCD layout, band indicator, DSP/TX panel redesign, CAT fixes (feature/ftx1-filter-width)
- Removed FTX-1 monitor tests (ML command not supported via CAT)
- Fixed tuner routing through command queue for Yaesu radios

## [0.14.0] — 2026-03-27

### Added

- **Multi-vendor rig profile support** — TOML schema extended for non-Icom radios:
  - `rigs/ftx1.toml` — Yaesu FTX-1 (Yaesu CAT, 17 modes, dual RX, meter calibration)
  - `rigs/x6100.toml` — Xiegu X6100 (CI-V 0x70, IC-705 compatible, QRP 8W)
  - `rigs/tx500.toml` — Lab599 TX-500 (Kenwood CAT, minimal command set, QRP 10W)
- **`[protocol]` section** — `type = "civ" | "kenwood_cat" | "yaesu_cat"` (default: `"civ"`)
- **`[controls]` section** — UI control styles: `toggle`, `stepped`, `selector`,
  `toggle_and_level`, `level_is_toggle`
- **`[meters]` section** — Non-linear calibration tables for S-meter and TX meters
  with `redline_raw` threshold
- **`[[rules]]` section** — Declarative constraint rules: `mutex`, `disables`,
  `requires`, `value_limit`
- **Extended VFO schemes** — added `"ab_shared"` (FTX-1) and `"single"` (simple QRP)
- **`[commands]` now optional** — non-CI-V radios may have empty command maps
- **`civ_addr` now optional** — defaults to 0 for Kenwood/Yaesu CAT radios
- `RadioProfile` and `RigConfig` extended with `protocol_type`, `controls`,
  `meter_calibrations`, `rules`
- **Yaesu CAT backend** (Epic #107) — full implementation for Yaesu FTX-1/FT-710/FT-991A:
  - YaesuCatTransport (async line protocol, `;` terminated, echo handling)
  - CAT template formatter + response parser (compile-once)
  - Polling scheduler for smooth meters (fast meters, slower state)
  - Full Web UI integration (command dispatch, levels, audio)
- **Audio FFT Scope** (Epic #383) — IF waterfall from USB/LAN audio stream:
  - AudioFftScope class (real-time FFT processor, consumes PCM, produces ScopeFrame)
  - Backend-agnostic (works with any AudioCapable radio)
  - Reuses existing scope protocol (SpectrumPanel + WaterfallCanvas)
- **Amber LCD display** (#389, #386) — retro KX3-style UI for radios without hardware spectrum:
  - 7-segment font, segmented bargraph, status indicators
  - Embedded Audio FFT strip (trapezoid filter visualization)
  - Grouped indicators (ATT/PRE/ATU/Contour/PROC/VOX)
  - Adaptive lerp (smooth animated filter width transitions)
- **Profile-driven command dispatch** (Epic #390-#396) — auto-wire all TOML commands to Web UI:
  - Frontend capability guards for multi-radio (hide unsupported controls)
  - Optimistic UI updates for NB/NR levels
  - Auto-reconnect on persistent serial errors
- **Serial discovery** (Epic #222) — `icom-lan discover` scans LAN + USB serial:
  - Multi-protocol probing (CI-V auto baud, Yaesu CAT, Kenwood CAT)
  - Deduplication (same radio found via LAN and serial)
- 42 new tests in `test_rig_multi_vendor.py` + 636 new tests total (3934 passed, 0 regressions)

## [0.12.0] — 2026-03-15

### Added

- **Data-driven rig profiles** (Epic #251) — radio configuration moved from hardcoded Python
  to TOML files in `rigs/`:
  - `rigs/ic7610.toml` — IC-7610 reference profile (full feature set, dual receiver)
  - `rigs/ic7300.toml` — IC-7300 profile (single receiver, VFO A/B, no DIGI-SEL/IP+)
  - `rigs/_schema.md` — TOML schema specification
  - `rig_loader.py` — `load_rig()`, `discover_rigs()`, `RigConfig`, `RigLoadError`
  - `command_map.py` — `CommandMap` (immutable CI-V wire byte lookup)
- **IC-7300 support** — tested via USB serial backend; rig profile defines all 200+
  supported commands, VFO A/B scheme, and IC-7300-specific wire byte overrides
- **`cmd_map` parameter on all 223 command functions** — every builder function in
  `commands.py` now accepts `cmd_map: CommandMap | None = None`; when provided, wire bytes
  come from the TOML profile instead of hardcoded IC-7610 defaults
- **`RadioProfile` additions** — `vfo_scheme` (`"ab"` | `"main_sub"`), `has_lan` fields
- **Web UI capability guards** — UI controls for DIGI-SEL, IP+, and dual-receiver
  features are automatically hidden when the active profile doesn't support them
- **Dynamic VFO labels** — Web UI shows "MAIN" / "SUB" for IC-7610 (main_sub scheme)
  and "VFO A" / "VFO B" for IC-7300 (ab scheme)
- **`/api/v1/info` enriched** — `capabilities` object now includes `vfoScheme`, `hasLan`,
  `maxReceivers`, `modes`, `filters` from the active rig profile
- **`/api/v1/capabilities` additions** — `receivers`, `vfoScheme` fields
- **`/api/v1/state` adapts** — omits `sub` receiver state for single-receiver rigs

### Changed

- +3497 lines, 236 new tests across `test_rig_loader.py`, `test_command_map.py`,
  `test_rig_ic7610.py`, `test_rig_ic7300.py`, `test_commands_cmd_map.py`
- Hardcoded IC-7610 wire bytes remain as defaults when `cmd_map=None` — fully backward-compatible

## [0.11.0] — 2026-03-12

### Added
- **Abstract Radio Protocol** (`radio_protocol.py`) — vendor-neutral interface with `Radio`, `AudioCapable`, `ScopeCapable`, `DualReceiverCapable` protocols
- **Epic #140 complete** — 100% CI-V command coverage (134/134 IC-7610 commands implemented)
- **Epic #215 complete** — Post-audit cleanup: mypy 197→0 errors, dead code removed (-616 lines), `__all__` API surface defined
- `IcomRadio.model`, `.capabilities`, `.radio_state` properties
- `set_state_change_callback()`, `set_reconnect_callback()` public methods
- `control_connected` property for transport health status
- `get_mode()` now returns Protocol-compatible `tuple[str, int | None]`
- Graceful shutdown: SIGTERM handler ensures clean radio disconnect on kill
- `_force_cleanup_civ()` for unconditional CI-V transport teardown
- Retry mechanism for `civ_port=0` (radio session not ready): 3×10s retries
- Connection indicators in Web UI update from `/api/v1/state` poll (200ms)
- `/api/v1/capabilities` endpoint uses `radio.capabilities`

### Fixed
- **Sequence counter overflow** — `_civ_send_seq` / `_audio_send_seq` now wrap at uint16 (was unbounded, crashed after ~1.5h)
- **Broken pipe recovery** — watchdog falls back to full reconnect when soft_reconnect fails
- **CI-V indicator accuracy** — `connected` property checks actual transport health, not just state enum
- UDP error logging rate-limited (first 3, then every 100th)
- `0x16` added to `_COMMANDS_WITH_SUB` (NB/NR/DIGI-SEL sub-command parsing)
- `server.stop()` uses full `disconnect()` instead of `soft_disconnect()` for complete session cleanup

### Changed
- All Web UI/rigctld consumers now use `Radio` Protocol type hints instead of `IcomRadio`
- `isinstance(radio, AudioCapable)` guards instead of `hasattr`
- Test coverage: 85% → 95% (3173 tests, +1434 from v0.10.0)
- **Type safety** — 0 mypy errors, full protocol-based typing for Radio/AudioCapable/ScopeCapable/DualReceiverCapable

## [0.8.0] — 2026-02-28

### Added

- **Web UI v1** — full-featured browser interface at `icom-lan web`:
    - Real-time spectrum and waterfall display (Canvas2D, click-to-tune)
    - Radio controls: VFO A/B, mode, filter, power, ATT, preamp, PTT
    - Band selector buttons (160m–6m with FT8 defaults)
    - Frequency entry, tuning step selector with snap, arrow keys, scroll wheel
    - Frequency marker and filter passband overlay on spectrum/waterfall
    - Eight real-time meter bars (S-meter, Power, SWR, ALC, COMP, Id, Vd, TEMP)
    - RX audio playback and TX audio capture in the browser (WebSocket binary)
    - Responsive layout, light/dark theme toggle, keyboard shortcuts
    - WebSocket pub/sub for scope, meters, audio, and control channels
- **Connect/Disconnect button** in Web UI — toggle radio connection without restarting server
- **Soft reconnect** — disconnect closes only CI-V/audio, keeps control transport alive.
  Reconnect re-opens CI-V instantly (~1s) without discovery or re-authentication.
  Audio auto-restarts after reconnect.
- **Skip discovery on reconnect** — `transport.reconnect()` reuses cached `remote_id`,
  eliminating the 30-60s discovery timeout on IC-7610.
- **Connection state machine** — `RadioConnectionState` enum formalizing connect lifecycle (#61)
- **State cache with TTL** — cached GET fallback values with configurable TTL
  (10s freq/mode, 30s power) via `cache_ttl_s` parameter (#63)
- **API docs from docstrings** — mkdocstrings-generated API reference (#65)
- **Scope assembly timeout** — 5s default prevents memory leak on incomplete frames (#62)

### Changed

- **CI-V commander: fire-and-forget for SET commands** — SET operations no longer wait
  for ACK from the radio, matching wfview behavior. GET commands retain 2s timeout
  with cache fallback on timeout. NAK silently logged at debug level. (#56)
- **`radio.py` refactored into focused modules** — split from 2395 to 1549 lines (#60):
    - `_control_phase.py` (452 lines) — authentication, conninfo, connection setup
    - `_civ_rx.py` (418 lines) — CI-V frame dispatch and RX pump
    - `_audio_recovery.py` (132 lines) — audio stream snapshot/resume
    - `_connection_state.py` — FSM enum for connection lifecycle
    - Public API surface unchanged (mixin pattern)
- **Optimistic port connection** — uses default ports (control+1, control+2) immediately
  instead of blocking on status packet. Status read in background with 2s timeout;
  if radio reports different ports, uses those instead. Eliminates up to 24s connection
  delay when radio returns `civ_port=0` after rapid reconnects.
- **CLI `--port` renamed to `--control-port`** to avoid confusion (#54)

### Fixed

- **CI-V GET timeout during scope streaming** (release blocker, #66) — RX pump now
  drains ALL pending packets from the transport queue each iteration instead of
  processing one at a time. Scope flood (~225 pkt/sec) no longer starves ACK/response
  packets behind hundreds of scope frames.
- **Conninfo local ports** — send reserved ephemeral UDP ports in conninfo packet
  (wfview-style `socket.bind(("", 0))`). Root cause of CI-V instability: radio
  didn't know where to send responses when local ports were 0.
- **Safari iOS audio** — AudioContext resume after background via `visibilitychange`
  listener; increased jitter buffer pre-roll from 50ms to 200ms for VPN use.
- **Flaky `test_hello_on_connect`** — race condition fix, pytest-asyncio dependency (#64)
- **Duplicate WebSocket connections** on page load/reconnect (#50)
- **Scope enable** — single entry point via `server.ensure_scope_enabled()` (#51)
- **PTT button** — toggle mode for click vs hold (#57)
- **Filter sync** after band change (#58)
- **PTT wait_response** restored after fire-and-forget refactor (#59)
- **Watchdog false disconnect** — use packet counter instead of qsize
- **Tuning flood** — throttle tuning commands to prevent CI-V timeout cascade
- **Frequency clamping** — valid range 30 kHz – 60 MHz

### Documentation

- Web UI user guide (`docs/guide/web-ui.md`)
- RFC for Web UI v1 protocol spec and architecture
- Updated architecture docs with mixin pattern and new module structure
- Updated test count: 1202 tests (was 1040)
- Roadmap Phase 8: Virtual Audio Bridge

## [0.7.0] — 2026-02-26

### Added

- Internal PCM<->Opus transcoder foundation for upcoming high-level PCM audio APIs.
- Typed audio exceptions: `AudioCodecBackendError`, `AudioFormatError`, `AudioTranscodeError`.
- High-level async PCM audio APIs: `start_audio_rx_pcm()` / `stop_audio_rx_pcm()`,
  `start_audio_tx_pcm()` / `push_audio_tx_pcm()` / `stop_audio_tx_pcm()`.
- Audio capability introspection: `audio_capabilities()`, `AudioCapabilities`.
- CLI: `icom-lan audio caps`, `audio rx`, `audio tx`, `audio loopback`.
- Runtime audio stats: `get_audio_stats()` with packet loss, jitter, latency metrics.
- Rigctld WSJT-X compatibility: `icom-lan serve --wsjtx-compat`.
- Golden protocol test suite: 45 parametrized fixtures.
- TCP server wire integration tests.

### Changed

- Audio API names explicit with `_opus` suffix.
- Rigctld mode mapping includes `PKTRTTY`.

### Fixed

- First-TX latency spikes in WSJT-X workflows.
- Abandoned rigctld requests no longer execute in background.

### Deprecated

- Ambiguous audio aliases (two-minor-release deprecation window).

## [0.6.0] — 2026-02-25

### Added

- Scope/waterfall API with `ScopeFrame`, `ScopeAssembler`, callbacks.
- Scope rendering: `render_spectrum()`, `render_waterfall()`, `render_scope_image()`.
- CLI `icom-lan scope` with themes, capture, JSON output.
- Mock radio server for integration testing (30 new tests).

## [0.5.1] — 2026-02-25

### Fixed

- `_ensure_audio_transport()` raises `ConnectionError` when audio port is 0.
- Ruff lint warnings resolved.

## [0.5.0] — 2026-02-25

### Added

- Command29 support for dual-receiver radios (IC-7610).
- Attenuator and preamp CLI commands with Command29 framing.

## [0.4.0] — 2026-02-25

### Changed

- Faster non-audio connect path (lazy audio port init).

## [0.3.2] — 2026-02-25

### Added

- Commander layer with priority queue, pacing, dedupe, transactions.
- New APIs: `get_mode_info()`, `get_filter()`, `set_filter()`, `snapshot_state()`.
- Extended integration test coverage.

## [0.3.0] — 2026-02-25

### Added

- Audio streaming (full-duplex, JitterBuffer, codec enum).
- Synchronous API (`icom_lan.sync`).
- Radio model presets (IC-7610, IC-7300, IC-705, IC-9700, IC-R8600, IC-7851).
- Token renewal and auto-reconnect with watchdog.

## [0.2.0] — 2026-02-25

### Added

- CLI tool with full command set.
- VFO control, RF controls, CW keying, power control, network discovery.

## [0.1.0] — 2026-02-24

### Added

- Transport layer, authentication, CI-V commands, meters, PTT, keep-alive.
- Clean-room Icom LAN UDP protocol implementation.

[Unreleased]: https://github.com/morozsm/icom-lan/compare/v0.19.0...HEAD
[0.19.0]: https://github.com/morozsm/icom-lan/compare/v0.18.0...v0.19.0
[0.18.0]: https://github.com/morozsm/icom-lan/compare/v0.17.0...v0.18.0
[0.17.0]: https://github.com/morozsm/icom-lan/compare/v0.16.4...v0.17.0
[0.16.4]: https://github.com/morozsm/icom-lan/compare/v0.16.3...v0.16.4
[0.16.3]: https://github.com/morozsm/icom-lan/compare/v0.16.2...v0.16.3
[0.16.2]: https://github.com/morozsm/icom-lan/compare/v0.16.1...v0.16.2
[0.16.1]: https://github.com/morozsm/icom-lan/compare/v0.16.0...v0.16.1
[0.16.0]: https://github.com/morozsm/icom-lan/compare/v0.15.1...v0.16.0
[0.15.1]: https://github.com/morozsm/icom-lan/compare/v0.15.0...v0.15.1
[0.15.0]: https://github.com/morozsm/icom-lan/compare/v0.14.2...v0.15.0
[0.14.2]: https://github.com/morozsm/icom-lan/compare/v0.14.1...v0.14.2
[0.14.1]: https://github.com/morozsm/icom-lan/compare/v0.14.0...v0.14.1
[0.14.0]: https://github.com/morozsm/icom-lan/compare/v0.12.0...v0.14.0
[0.12.0]: https://github.com/morozsm/icom-lan/compare/v0.11.0...v0.12.0
[0.11.0]: https://github.com/morozsm/icom-lan/compare/v0.8.0...v0.11.0
[0.8.0]: https://github.com/morozsm/icom-lan/compare/v0.7.0...v0.8.0
[0.7.0]: https://github.com/morozsm/icom-lan/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/morozsm/icom-lan/compare/v0.5.1...v0.6.0
[0.5.1]: https://github.com/morozsm/icom-lan/compare/v0.5.0...v0.5.1
[0.5.0]: https://github.com/morozsm/icom-lan/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/morozsm/icom-lan/compare/v0.3.2...v0.4.0
[0.3.2]: https://github.com/morozsm/icom-lan/compare/v0.3.1...v0.3.2
[0.3.0]: https://github.com/morozsm/icom-lan/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/morozsm/icom-lan/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/morozsm/icom-lan/releases/tag/v0.1.0
