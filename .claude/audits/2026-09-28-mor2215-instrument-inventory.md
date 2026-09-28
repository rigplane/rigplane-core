# MOR-2215 — Instrument and live-consumer inventory (pinned revision)

Revision: `3c19f5c9e71cdc4626a96b465845b67778a47ebb` (detached HEAD, `fix(MOR-2881): enforce the same-Origin rule on core's state-changing HTTP routes (#3873)`).

Acceptance line: "Complete current inventory maps each instrument and live consumer to a migration or already-conformant proof; a list of 14 panels alone is insufficient."

## 1. Enumeration commands (re-runnable)

```
git checkout --detach 3c19f5c9e71cdc4626a96b465845b67778a47ebb
git ls-files 'frontend/src/primitives/**'
git ls-files 'frontend/src/components-v2/controls/**'
git ls-files 'frontend/src/components-v2/meters/**' 'frontend/src/components-v2/display/**' 'frontend/src/components-v2/vfo/**'
git ls-files 'frontend/src/lib/Button/**' 'frontend/src/components-v2/panels/lcd/**' 'frontend/src/semantic/*Surface.svelte'
```

Consumer derivation (per component name `C`), excluding tests and the component's own file:

```
grep -rl "<C\b\|import C\b\|import { C\b\|C as " frontend/src --include='*.svelte' --include='*.ts' \
  | grep -v __tests__ | grep -v "/C.svelte"
```

Behaviour-module consumers: same grep for `createContinuousPair`, `createContinuousScalar`,
`createCommittedScalar`, `meter-ballistics`, `s-meter-scale`, `frequency-instrument`,
`reading-text`, `control-instrument-behavior`.

Surface mount map: `grep -n "import .* from '\.\./" frontend/src/semantic/*Surface.svelte`
plus `grep -n "InstrumentHost\|Surface" frontend/src/components-v2/wiring/SemanticRadioSurfaces.svelte`
(production composition mounting every surface next to its companion InstrumentHost).

Linear ticket references: `grep -rn "MOR-[0-9]\+" <dirs> -o | grep -v __tests__ | sort -u`.

Paths below are relative to `frontend/src/`. `demo` = `ControlButtonDemo.svelte` (App.svelte
`demoMode === 'control-buttons'` lazy route) or `ValueControlLab.svelte` (inside that demo).

## 2. Primitives (`primitives/`) — behaviour/render-separated instrument layer

| File | Family | Home | Live production consumers | Demo-only | Proof (tests; Linear) |
|---|---|---|---|---|---|
| `control-instruments/ControlInstrumentRendererHost.svelte` (+ behaviour owners `control-instrument-behavior.ts`, `control-instrument-renderer.svelte.ts`) | finite choice or toggle / action (renderer host) | primitives | 10 semantic hosts: Antenna/Band/Dsp/Filter/RfFrontEnd/RitXitScan/RxAudio InstrumentHosts, TxAuxFiniteHost, VfoOperationSeatHost, ScopeControlsSurface | — | `control-instrument-renderer.test.ts`, `control-instrument-renderers.component.test.ts`, `control-instrument-behavior.test.ts` |
| `frequency/FrequencyDisplayInteractive.svelte` (+ `frequency-instrument.svelte.ts`, `frequency-interaction.svelte.ts`, `frequency-tuning.ts`, `frequency-entry-renderer.svelte.ts`) | frequency | primitives | `components-v2/vfo/VfoPanel.svelte`, `semantic/VfoSurface.svelte` | — | `frequency-tuning.test.ts`, `display/__tests__/FrequencyDisplayInteractive.component.svelte.test.ts`; MOR-1441, MOR-2514, MOR-1480, MOR-1444 |
| `frequency/FrequencyRendererSeat.svelte` | frequency (renderer seat) | primitives | `FrequencyDisplayInteractive.svelte`, `semantic/ReceiverInstrumentHost.svelte` | — | `FrequencyRendererSeat.isolated.test.ts` |
| `frequency/StandardFrequencyReadout.svelte` (+ `frequency-readout.ts`) | frequency (readout) | primitives | `components-v2/display/FrequencyDisplay.svelte`, `FrequencyRendererSeat.svelte` | — | `StandardFrequencyReadout.isolated.test.ts`; MOR-2509, MOR-2654 |
| `meters/meter-ballistics.svelte.ts` + `meters/s-meter-scale.ts` | meter (behaviour owners) | primitives | `LinearSMeter`, `BarGauge`, `signal-meter-motion`, `bar-meter-motion`, `VfoPanel`, `MetersDockPanel`, `wiring/SemanticRadioSurfaces.svelte`, `semantic/radio-view-model.ts` | — | `meter-ballistics.test.ts`, `s-meter-scale.test.ts`; MOR-2509, MOR-2705 |
| `reading-text.ts` | readout (the ONE unread-display rule) | primitives | pervasive: semantic surfaces/hosts, primitives | — | `reading-text.test.ts`, `semantic/__tests__/reading-text-type-tie.test.ts`, `state-vocabulary.component.test.ts`; MOR-2688 |
| `scalar/continuous-scalar.svelte.ts` + `scalar/value-control-core.ts` | continuous scalar | primitives | `ValueControl.svelte`, panels Dsp/Tx/RfFrontEnd/Filter/Cw | — | `continuous-scalar.test.ts`, `value-control-core.test.ts`; MOR-2535 |
| `scalar/continuous-pair.svelte.ts` | RF-SQL pair | primitives | `panels/RfFrontEnd.svelte`, `semantic/RfFrontEndInstrumentHost.svelte` | — | `continuous-pair.test.ts` |
| `scalar/committed-scalar.svelte.ts` | continuous scalar (commit-on-apply) | primitives | `panels/CwPanel.svelte`, `semantic/CwKeyerSurface.svelte` | — | `committed-scalar.test.ts` |
| `control-feedback/control-feedback-presentation.ts` | readout (a11y feedback presentation) | primitives | wiring + panels via `bindSemanticSurfaceHandlers` | — | `wiring/__tests__/control-feedback-composition.component.test.ts`, `TxPanel/CwPanel.feedback-wiring` tests; MOR-1711 |
| `stage/ScaledStage.svelte` (+ `stage-scale.ts`) | readout (fixed-native stage scaler) | primitives | `skins/segmentline/PeerSplitLayout.svelte` | — | `ScaledStage.isolated.test.ts`, `stage-scale.test.ts`; MOR-1160, MOR-2270 |

## 3. Controls (`components-v2/controls/`, incl. `value-control/` and `skins/`)

| File | Family | Home | Live production consumers | Demo-only | Proof |
|---|---|---|---|---|---|
| `AttenuatorControl.svelte` | finite choice or toggle (RF attenuator) | controls | `panels/RfFrontEnd.svelte`, `semantic/RfFrontEndInstrumentHost.svelte` | — | `AttenuatorControl.test.ts` |
| `BandSelector.svelte` | finite choice or toggle (band) | controls | `layout/LeftSidebar`, `layout/RadioLayout`, `layout/MobileRadioLayout` | — | `BandSelector.isolated.test.ts`; MOR-1367 |
| `BottomSheet.svelte` | action (app chrome container) | controls | `layout/MobileRadioLayout` | — | `BottomSheet.test.ts` |
| `CollapsiblePanel.svelte` | readout (container) | controls | LeftSidebar, RadioLayout, RightSidebar, MobileRadioLayout, SemanticControlPanel | — | `CollapsiblePanel.test.ts` |
| `InstallPrompt.svelte` | action (app chrome) | controls | **zero live consumers** (only `types/pwa.d.ts` type ref) | — | `InstallPrompt.test.ts` (orphan with test) |
| `LanguageSelector.svelte` | finite choice or toggle (app chrome) | controls | `layout/RadioLayout` | — | `LanguageSelector.test.ts` |
| `ManagedTotControl.svelte` | finite choice or toggle (managed TOT) | controls | `panels/TxPanel`, `ManagedTotStatusControl` | — | `ManagedTotControl.isolated.test.ts`; MOR-2674 |
| `ManagedTotStatusControl.svelte` | readout (TOT status) | controls | `layout/StatusBar` | — | `ManagedTotStatusControl.isolated.test.ts`; MOR-2674 |
| `PttFab.svelte` | action (PTT) | controls | `layout/MobileRadioLayout` | — | indirect via `MobileRadioLayout.component.svelte.test.ts`, `MobileRadioLayout.honesty.isolated.test.ts`; MOR-1376 |
| `PullToRefresh.svelte` | action (app chrome) | controls | **zero live consumers** | — | `PullToRefresh.test.ts` (orphan with test) |
| `SegmentedButton.svelte` | finite choice or toggle | controls | none (re-exported as `SegmentedControl` from `lib/SegmentedControl`) | `ControlButtonDemo` | `SegmentedButton.test.ts` |
| `StatusBadge.svelte` | readout (badge) | controls | none | `ControlButtonDemo` | `StatusBadge.test.ts` |
| `ThemePicker.svelte` | finite choice or toggle (app chrome) | controls | `layout/StatusBar` | — | **none found — legacy, not migrated** |
| `WorkspaceImportExport.svelte` | action (app chrome) | controls | `layout/RadioLayout` | — | `WorkspaceImportExport.test.ts`; MOR-1080 |
| `WorkspaceSettingsPanel.svelte` | action (app chrome panel) | controls | `layout/RadioLayout` | — | `WorkspaceSettingsPanel.test.ts`; MOR-1077, MOR-2215, MOR-2218, MOR-1076 |
| `value-control/ValueControl.svelte` | continuous scalar (host) | controls | panels Cw/Dsp/Essentials/Filter/RfFrontEnd/RitXit/RxAudio/Tx/Vox, MobileRadioLayout; semantic hosts CwKeyer/DspScalar/RfFrontEnd/RxAudio/TxAux + RitXitScanSurface | demo | `ValueControl.test.ts`, `ValueControl.controlled.component.svelte.test.ts`, `scalar-render-presentation.test.ts` |
| `value-control/BipolarRenderer.svelte` | continuous scalar (renderer) | controls | `ValueControl.svelte` only | — | via ValueControl suite; MOR-2692, MOR-2677, MOR-2657, MOR-2522 |
| `value-control/DiscreteRenderer.svelte` | continuous scalar (renderer) | controls | `ValueControl.svelte` only | — | via ValueControl suite; MOR-2657, MOR-2522 |
| `value-control/DualParamRenderer.svelte` | RF-SQL pair (renderer) | controls | `panels/RfFrontEnd.svelte`, `semantic/RfFrontEndInstrumentHost.svelte` | — | `DualParamRenderer.controlled.svelte.test.ts`; MOR-2657, MOR-2522 |
| `value-control/HBarRenderer.svelte` | continuous scalar (renderer) | controls | `ValueControl.svelte` only | — | via ValueControl suite; MOR-2657, MOR-2522 |
| `value-control/KnobRenderer.svelte` | continuous scalar (renderer) | controls | `ValueControl.svelte` only | — | via ValueControl suite; MOR-2522, MOR-2657 |
| `value-control/skins/ProfessionalKnob.svelte` | continuous scalar (skin) | controls/skins | none — code comment: "demo-only, not wired to any production caller" | `ControlButtonDemo` | **none — demo skin** |

Demo containers (not instruments): `ControlButtonDemo.svelte` (App.svelte demoMode route),
`ValueControlLab.svelte` (mounted inside the demo).

## 4. Meters / Display / VFO

| File | Family | Home | Live production consumers | Demo-only | Proof |
|---|---|---|---|---|---|
| `components-v2/meters/BarGauge.svelte` (+ `bar-meter-motion.svelte.ts`, `bar-gauge-utils.ts`) | meter | meters | `semantic/StationMeterBarPlacement.svelte` (→ MetersSurface) | — | `BarGauge.isolated/peakhold/fault/forced-colors/reduced-motion/constant-nodes.test.ts`, `meter-contract.test.ts`; MOR-2255, MOR-1282, MOR-1345, MOR-2521, MOR-1250 |
| `components-v2/meters/LinearSMeter.svelte` (+ `smeter-scale.ts`, `lower-scale.ts`, `meter-display.ts`, `signal-meter-motion.svelte.ts`, `meter-geometry-grid.ts`) | meter (S-meter) | meters | `VfoPanel`, `MobileRadioLayout`, `semantic/VfoIndicatorRow`, `semantic/MetersSurface`, `semantic/VfoSurface` | — | `LinearSMeter.*` suite (isolated, display, geometry-quantize, lower-scale, mockup-v8, forced-colors, reduced-motion, sdr-appearance, vfo-face); MOR-2250, MOR-2509, MOR-2521, MOR-1250, MOR-1233, MOR-2613, MOR-2214, MOR-977 |
| `components-v2/display/FrequencyDisplay.svelte` (+ `frequency-format.ts`) | frequency (readout) | display | `layout/MobileRadioLayout` | — | `FrequencyDisplay.test.ts`; MOR-2654 |
| `components-v2/vfo/ActiveReceiverToggle.svelte` | finite choice or toggle | vfo | `semantic/VfoOperationGroup.svelte` | — | `ActiveReceiverToggle.test.ts` |
| `components-v2/vfo/VfoPanel.svelte` (+ `vfo-utils.ts`, `vfo-ops-utils.ts`) | frequency (composite VFO panel) | vfo | `semantic/VfoSurface.svelte` | — | `VfoPanel.isolated.test.ts` |

## 5. Button family (`lib/Button/`)

| File | Family | Home | Live production consumers | Demo-only | Proof |
|---|---|---|---|---|---|
| `ControlButton.svelte` | action (button core) | lib/Button | `semantic/DspInstrumentHost`, `semantic/RepeaterSurface`; wrapped by Dot/Fill/Hardware variants | demo | `ControlButton.test.ts` |
| `HardwareButton.svelte` | action | lib/Button | panels RxAudio/Scan/Vox/Mode/Essentials + `ActiveReceiverToggle`, `RadioLayout`, `MobileRadioLayout` | — | `ControlButton.test.ts` (family file) |
| `HardwarePlainButton.svelte` | action | lib/Button | `panels/RitXitPanel` | demo | `ControlButton.test.ts` (family file) |
| `DotButton.svelte` | action | lib/Button | none | `ControlButtonDemo` | `ControlButton.test.ts` (family file) |
| `FillButton.svelte` | action | lib/Button | none | `ControlButtonDemo` | `ControlButton.test.ts` (family file) |
| `StatusIndicator.svelte` | readout (indicator) | lib/Button | none | `ControlButtonDemo` | **none — legacy, not migrated** |

## 6. LCD panels (`components-v2/panels/lcd/`)

| File | Family | Home | Live production consumers | Demo-only | Proof |
|---|---|---|---|---|---|
| `AmberCockpit.svelte` | readout (composite LCD face) | panels/lcd | `layout/LcdLayout.svelte` | — | `AmberCockpit.agc-preamp-labels` + `.qsy-authority.isolated.test.ts`, `AmberPanels.honesty-migration.isolated.test.ts`, `lcd-availability.isolated.test.ts`, `AmberLabels.profile-data.isolated.test.ts`; MOR-1409, MOR-2673, MOR-429, MOR-2537, MOR-2546, MOR-1529, MOR-2683 |
| `AmberScope.svelte` | scope | panels/lcd | `layout/LcdLayout.svelte` | — | `AmberPanels.honesty-migration.isolated.test.ts`, `audio-fft-demand.isolated.test.ts`, `lcd-availability.isolated.test.ts`; MOR-1409, MOR-2673, MOR-2683, MOR-2537, MOR-2546, MOR-1529, MOR-429 |
| `AmberAfScope.svelte` | scope (audio FFT) | panels/lcd | `AmberScope`, `AmberCockpit` | — | `AmberAfScope.honesty.component.test.ts`, `scope-frame.test.ts`; MOR-1161 |
| `AmberFrequency.svelte` | frequency | panels/lcd | `AmberScope`, `AmberCockpit` | — | `lcd-components.isolated.test.ts`; MOR-2654 |
| `AmberSmeter.svelte` | meter | panels/lcd | `AmberScope`, `AmberCockpit` | — | `AmberUnreadSmeter.mor-2740.isolated.test.ts` + honesty-migration; MOR-483, MOR-2705, MOR-2740 |
| `AmberFilterGhost.svelte` | readout (filter envelope) | panels/lcd | `AmberScope`, `AmberCockpit` | — | `AmberFilterGhost.ftx1.isolated.test.ts` + honesty-migration |
| `AmberIndStrip.svelte` | readout (indicator strip) | panels/lcd | `AmberScope`, `AmberCockpit` | — | `AmberIndStrip.rfg-quiet-at-max.isolated.test.ts`; MOR-2546, MOR-2683 |
| `AmberMemoryStrip.svelte` | readout (memory) | panels/lcd | `AmberCockpit` | — | `AmberMemoryStrip.isolated.test.ts`; MOR-2659 |
| `AmberTelemetryStrip.svelte` | readout (telemetry) | panels/lcd | `AmberCockpit` | — | `AmberTelemetryStrip.isolated.test.ts`; MOR-483, MOR-2659 |
| `AmberSparkline.svelte` | readout (sparkline) | panels/lcd | `AmberTelemetryStrip` | — | indirect only (via `AmberTelemetryStrip.isolated.test.ts`) — no dedicated test |
| `LcdContrastControl.svelte` | continuous scalar | panels/lcd | `layout/LcdLayout.svelte` | — | indirect via `semantic-lcd-migration.component.test.ts` (LcdLayout tree) |
| `LcdDisplayModeControl.svelte` | finite choice or toggle | panels/lcd | `layout/LcdLayout.svelte` | — | indirect via `LcdLayout.command-bus-migration.isolated.test.ts` |
| `VfoControlPanel.svelte` | frequency (VFO control composite) | panels/lcd | `layout/LcdLayout.svelte` | — | `VfoControlPanel.authority.isolated.test.ts`, `semantic-lcd-migration.component.test.ts`, `mor1566-scope-vfo-family-conformance.isolated.test.ts` |

## 7. Semantic surfaces and the instruments each mounts

Production composition: `components-v2/wiring/SemanticRadioSurfaces.svelte` mounts every
surface next to its companion InstrumentHost. "Mounts" = components in the surface's tree.

| Surface | Instrument components mounted | Proof |
|---|---|---|
| `AntennaSurface` | via `AntennaInstrumentHost` → `ControlInstrumentRendererHost` | `AntennaSurface.test.ts`, `AntennaInstrumentHost.isolated.test.ts` |
| `BandSurface` | via `BandInstrumentHost` → `ControlInstrumentRendererHost` (+ `BandFrequencyEntry` entry draft) | `BandSurface.test.ts`, `BandInstrumentHost.isolated.test.ts` |
| `CwKeyerSurface` | via `CwKeyerInstrumentHost` → `ValueControl`, `ControlInstrumentRendererHost`, `createCommittedScalar` | `CwKeyerSurface.test.ts`, `CwKeyerInstrumentHost.isolated.test.ts` |
| `DspSurface` | via `DspInstrumentHost` → `ControlInstrumentRendererHost`; `DspScalarHost` → `ValueControl`; `ControlButton` | `DspSurface.test.ts`, `DspInstrumentHost/DspScalarHost.isolated.test.ts` |
| `FilterSurface` | via `FilterInstrumentHost` → `ControlInstrumentRendererHost` | `FilterSurface.test.ts`, `.dblclick-reset`, `.width-command` isolated, `FilterInstrumentHost.isolated.test.ts` |
| `MemorySurface` | native `<button>` elements only (no shared instrument component) | `MemorySurface.test.ts` |
| `MetersSurface` | `LinearSMeter`, `StationMeterBarPlacement` → `BarGauge`, `MeterRendererSeat`, `StationMeterInstrumentHost` | `MetersSurface.isolated.test.ts`, `StationMeterInstrumentHost.isolated.test.ts` |
| `ModeSurface` | `FilterInstrumentHandles` snippets (from `FilterInstrumentHost` → `ControlInstrumentRendererHost`) | **no dedicated surface test** — view-model only in `mode-filter.test.ts`; legacy gap |
| `RepeaterSurface` | `ControlButton` (tone/shift choice buttons) | **none found — legacy, not migrated** |
| `RfFrontEndSurface` | via `RfFrontEndInstrumentHost` → `DualParamRenderer`, `ValueControl`, `AttenuatorControl`, `ControlInstrumentRendererHost` | `RfFrontEndSurface.test.ts`, `RfFrontEndInstrumentHost.isolated.test.ts` |
| `RitXitScanSurface` | via `RitXitScanInstrumentHost` → `ValueControl`, `ControlInstrumentRendererHost` (toggles + scan actions) | `RitXitScanSurface.test.ts`, `RitXitScanInstrumentHost.isolated.test.ts` |
| `RxAudioSurface` | via `RxAudioInstrumentHost` → `ValueControl`, `ControlInstrumentRendererHost` | `RxAudioSurface.test.ts`, `RxAudioInstrumentHost.isolated.test.ts` |
| `RxTxSurface` | `renderSlot` design-language slots + native `<button>`s; station meters via `StationMeterInstrumentHost` in composition | `rx-tx-surface.component.test.ts` |
| `ScopeControlsSurface` | `ScopeFlatKey`, `ScopeMorePanel` (`components/spectrum`), `ControlInstrumentRendererHost` | `ScopeControlsSurface.test.ts` |
| `ScopeDisplaySurface` | text readouts only ("three short facts, not a canvas") | `ScopeDisplaySurface.test.ts` |
| `TxAuxSurface` | via `TxAuxScalarHost` → `ValueControl`; `TxAuxFiniteHost` → `ControlInstrumentRendererHost` | `TxAuxSurface.test.ts`, `TxAuxScalarHost.isolated.test.ts`, `TxAuxFiniteHost.test.ts` |
| `VfoSurface` | `FrequencyDisplayInteractive`, `LinearSMeter`, `VfoPanel`, `VfoIndicatorRow` (→ LinearSMeter), `VfoOperationGroup` → `ActiveReceiverToggle` / `VfoOperationSeatHost`, `ReceiverInstrumentHost` → `FrequencyRendererSeat` + `MeterRendererSeat` | `VfoSurface.test.ts`, `VfoSurface.panel-rows.test.ts`, `ReceiverInstrumentHost.isolated.test.ts`, `VfoIndicatorRow.test.ts` |

## 8. Totals

Instrument rows (components incl. behaviour owners): 57. Surface rows: 17. Total mapped: 74.

| Family | Rows |
|---|---|
| continuous scalar | 9 |
| RF-SQL pair | 2 |
| finite choice or toggle | 9 |
| action | 11 |
| meter | 4 |
| frequency | 7 |
| scope | 2 |
| readout | 13 |

| Proof status | Component rows | Surface rows |
|---|---|---|
| dedicated/direct test (conformant or migrated with proof) | 50 | 15 |
| indirect/family-level test only (PttFab, AmberSparkline, LcdContrastControl, LcdDisplayModeControl) | 4 | 0 |
| no proof — legacy, not migrated (ThemePicker, ProfessionalKnob, StatusIndicator) | 3 | 2 (ModeSurface, RepeaterSurface) |
| zero live consumers (InstallPrompt, PullToRefresh — tested orphans) | 2 | 0 |
| demo-only consumers (SegmentedButton, StatusBadge, DotButton, FillButton, ProfessionalKnob, StatusIndicator) | 6 | 0 |

First three proof rows are mutually exclusive (50 + 4 + 3 = 57); the last two rows are
overlapping qualifiers cutting across them (orphans sit inside "dedicated", demo-only rows
sit inside "dedicated" or "no proof"). Family rows sum to 57.
