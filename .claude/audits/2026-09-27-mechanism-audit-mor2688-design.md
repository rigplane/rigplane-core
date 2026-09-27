Method file read: `.claude/skills/mechanism-audit/SKILL.md`. I followed steps 0 to 5 in order, including the 3a dead-code sweep and the step-4 steelman before any verdict.

**Audit ground.** I audited revision `8018f8ec`: detached HEAD, `git status` clean. I worked read-only throughout and ran no tests.
- **Linear:** not read. No Linear tool was available to me, so owner rulings are quoted from the dispatch and from in-repo text.
- **GitHub (read-only):** `gh pr view 3753` reports MERGED at head `7ec7b8f7`. That merge is not part of `8018f8ec`. I read its `offenders.json` at that head: 60 entries.
- **Layer map and prior rulings:** CLAUDE.md, `frontend/eslint.config.js`, the 2026-04-12 ADR with its 2026-09-02 addendum, the v3 invariants, and the prior audits. `.importlinter` covers only the Python package `rigplane`.
- **Paths:** relative to `frontend/src` unless prefixed.
- **Search method:** every count comes from a literal grep, with tests excluded unless stated. A literal search cannot see dynamic access.
- **Files not opened in full:** MemoryPanel, the ManagedTot controls, AmberScope and HostedFaceInstrumentBridge (grep only); `panel-props.ts` (lines 115–140 only); and most test files (grep only).
- **Text directing an agent:** none found in the files. Comments stating rules were treated as evidence.

**Premises tested.**
- **H1**, "59 files, 37 under `semantic/`": confirmed.
- **H2**, "about 15 copies": refuted as a count of the whole population. There are 70 sites that give the empty answer plus 26 diverged sites (Q2). The 15 was the prior tract's own scope.
- **H3** is implicit in the dispatch: "drawing can move to value-or-nothing without changing behaviour". The strongest case against it: several drawing outputs carry more than lit/unlit.
  - The PBT slider's `data-presentation` attribute distinguishes `confirmed` from `retained`.
  - The segmentline skin has its own paint rule for `[data-state='unsupported']`.
  - Unread toggles use `aria-checked="mixed"`.
  - VfoSurface tests pin the dash.

  H3 holds only if those outputs stay exactly as they are at each call site.

## Q1: Inventory

Commands, all in `frontend/src`, excluding `__tests__`, `*.test.ts` and `*.spec.ts`:
- `grep -rlE "status (===|!==) '(known|unknown)'"` → 59 files (37 in `semantic/`), 277 lines.
- `grep -rnE "\.(structural|operational)\b"` → 282 lines in 47 files.
- `grep -rnE "reading\??\.status\b" | grep -vE "status (===|!==|==|!=) "` → 18 uses that are not comparisons, such as `data-state={…reading.status}` and controller keys.
- The union of the three is **555 lines in 63 files**.
- The display-observation vocabulary is counted separately: `grep -rnE "\.state (===|!==) '(current|stale|unknown|unsupported|known)'"` → 95 lines in 22 files, 47 of them in `skins/segmentline`.

How I classified. I read every line and adjudicated its class by hand; I read the surrounding context for about 60 lines.
- (a): the result is only drawn.
- (b): the result gates an action, a disable, a permit or an authority check — or the line does both jobs.
- (c): the line sits in an adapter, the validator or a projection module, or builds a controller input.

Two mechanical classifiers bracket the (b) count at 40 (keywords on the line itself) and 152 (keywords within ±4 lines).

| Layer | (a) | (b) | (c) | Total |
|---|---|---|---|---|
| semantic/*.svelte | 289 | 70 | 11 | 370 |
| semantic/*.ts | 1 | 2 | 61 | 64 |
| lib/runtime/adapters | 0 | 0 | 35 | 35 |
| lib/stores + lib/media | 0 | 1 | 3 | 4 |
| components-v2/wiring | 10 | 18 | 1 | 29 |
| components-v2 layout/panels/vfo | 2 | 13 | 0 | 15 |
| components/spectrum | 1 | 9 | 0 | 10 |
| primitives | 5 | 9 | 0 | 14 |
| presentation/languages | 0 | 0 | 5 | 5 |
| skins | 3 | 3 | 0 | 6 |
| component-kits | 2 | 1 | 0 | 3 |
| **Total** | **313** | **126** | **116** | **555** |

Separately, there are 86 fail-closed `disabled={…}` bindings on operational, usable, status, known or available, in 21 files (`grep -rnE "disabled=\{[^}]*(operational|usable\(|status|Known|known|available)" --include='*.svelte'`).

## Q2: Copies

**Sites that give the empty answer: 70** (observation).
- **Semantic helpers, 23.**
  - Byte-identical body `f.reading.status === 'known' ? String(f.reading.value) : ''` (differing only in parameter name) in: `BandSurface`, `FilterInstrumentHost`, `FilterSurface`, `RitXitScanSurface` and `ScopeControlsSurface: textOf`; `RxAudioInstrumentHost: unlitTextOf`; and `VfoIndicatorRow: numeric`, `agc`, `sharedOffset`.
  - Other helpers: `VfoIndicatorRow: booleanLabel`, `sharedBoolean`; `CwKeyerSurface: textOf`; `AntennaInstrumentHost: textOf`; `DspSurface: fmt`; `RxAudioSurface: afText`; `RxAudioInstrumentHost: afPercent`; `RitXitScanSurface: signedOffset`; `FilterSurface: formatExactWidth`; `TxAuxScalarHost`, `CwKeyerInstrumentHost` and `DspScalarHost: formatValue`; `MemorySurface: UNKNOWN_TEXT`.
- **Semantic inline template ternaries, 17:** `ScopeControlsSurface` ×2 (it re-inlines its own `textOf`), `VfoIndicatorRow` ×11 suffixes, `RepeaterSurface` ×1, `RfFrontEndInstrumentHost` ×2, `DspSurface` AGC-T ×1.
- **Legacy guards on the NaN / null "unread" marker, 18:**
  - `TxPanel: rawTxLevelDisplay`, `rfPowerDisplay`; `MobileRadioLayout: formatRfPowerDisplay` plus 2 inline guards (TX power, SWR).
  - `RxAudioPanel` and `EssentialsPanel: formatAfLevelDisplay`; `RfFrontEnd: displayRfGain`.
  - `CwPanel: formatCwPitchDisplay`, `formatKeySpeedDisplay`; `RitXitPanel: formatOffsetDisplay`; `FilterPanel: formatWidthDisplay`, `formatExactWidthDisplay`; `VfoPanel: formatFrequency`; `SpectrumToolbar` inline ×3.
  - **F2 is now five byte-identical guards** of the form `Number.isFinite(v) ? normalizedPercentDisplay(v) : ''`: `TxPanel: rfPowerDisplay`, `MobileRadioLayout: formatRfPowerDisplay`, `RxAudioPanel` and `EssentialsPanel: formatAfLevelDisplay` (identical down to the name), and `RfFrontEnd: displayRfGain`.
- **Shared renderers, 12.**
  - Five value-control renderers share the same default for an unread value (the `unknownDisplay` fallback), byte-identical: `HBar`, `Knob`, `Discrete` and `Bipolar` renderers plus `ProfessionalKnob`.
  - `UNKNOWN_TEXT = ''` appears in three `frequency-renderer.ts` files.
  - `lcd-display-helpers.ts: stateText`, `telemetryText`, `formatBandwidth`, `formatOffset`.
- **A second input vocabulary for the same capability, about 20 copies.** These map `display.state` "current or stale" to the value and anything else to nothing. Examples: `VfoSurface: displayValue`, `VfoIndicatorRow: rfGainShown`, `ReceiverInstrumentHost` (:174), `FilterSurface: numberOf`, `meter-renderer-view.ts` ×3, `VfoPanel` (:125), `SpectrumPanel` (:314), and four adapter copies.

**Diverged sites, 26: same input, different output.** The text is observed; reachability is inferred.
- **Mobile:** `MobileRadioLayout: formatSValueDisplay` gives `---`, `formatDbmDisplay` gives `--- dBm`, and `formatOffsetDisplay` gives `---`. **That last one has the same name and the same input as `RitXitPanel: formatOffsetDisplay`, which returns `''`.**
- **LCD faces:** `AmberCockpit: ritOffsetLabel` and `mainMode ?? '---'`; `AmberScope: mainMode`, `subMode`.
- **Band:** `band-instruments.ts: UNKNOWN_TEXT = '—'`, used by `BandInstrumentHost: entryRangeText` and `BandSurface: reasonLabel`, `txDeniedReason`.
- **VFO surface:** `VfoSurface: formatFrequency` `—`, `.vfo-mode ?? '—'`, `instrumentSlot`, the selector slot and the meter label. `roleLabel` renders "MAIN (unknown)" and `activeReceiverStatus` renders "Active receiver: unknown".
- **dual-sdr-face:** `ReceiverInstrumentCluster` `—` (5 lines); `DualSdrFace` has a hard-coded `ANT<br />—` and a scope-mode `—`.
- **Readiness:** `rx-audio-instruments.ts: READINESS_LABEL['not-applicable'] = 'n/a'`, rendered by `RxAudioInstrumentHost`.
- **RX/TX:** `RxTxSurface` renders a visible `{item.field}: {item.code}` list, for example "txPermit: tx-target-unknown".

The ratchet lists some of these under MOR-2655, 2673, 2675, 2691 and 2692. It does not list `roleLabel`, `n/a`, the RxTxSurface codes, or the band dash.

**Reserved-slot CSS (F4).**
- **Widths:** 69 ch-width declarations (`min-width: Nch` 56 lines in 34 files; `min-inline-size` 13 lines in 9) form 67 rule blocks in 43 files; 38 of those blocks also carry `tabular-nums`.
- **Byte-identical rule bodies:**
  - `.vc-value` in Bipolar, Discrete and HBar renderers.
  - `inline-block; 4ch; tabular-nums` in `RfFrontEnd` rf-gain, `TxPanel .tx-level-slot`, `TxAuxScalarHost .tx-aux-level > output`.
  - `6ch; tabular-nums` in `DspScalarHost`, `DspSurface .dsp-level > output`, `FilterSurface .pbt-value`.
  - `inline-block; 5ch; tabular-nums` in `CwKeyerSurface .cw-mode-line output`, `RitXitScanSurface .scan-readout`.
  - The step-lamp slot in `RepeaterSurface` and `ScopeControlsSurface`.
  - `4ch; text-align:center` without tabular digits in `SpectrumToolbar .ref-value`, `DspSurface .dsp-agc-time-value`.
- **Strut:** `:empty::before{content:'\200b'}` appears ×6. The `.vc-value` lines in Discrete, Bipolar and HBar are identical; the others are `.vc-num` in DualParam and `.bw-value` and `.modal-fixed-value` in FilterPanel.
- **Baseline fix:** `min-height:1lh; align-self:center` exists only in `CwPanel .cw-value-slot`. `CwKeyerSurface` uses `1lh` for a different purpose (reserving a sentence line).

## Q3: Which layer owns the conversion

**Who can call what** (eslint and ADR table, observation). Only `primitives/` is callable by every consumer that draws:
- semantic, presentation, skins, the legacy components-v2 panels, and component-kits;
- and primitives themselves, since `control-instrument-renderer` and `continuous-scalar` draw too.

Why each alternative is wrong:
- **`semantic/`:** primitives are banned from importing it (`FORBIDDEN_PRIMITIVES_IMPORTS`).
- **Adapters:** semantic may import only their types (ADR), and primitives are banned from them.
- **`presentation/languages` and `$lib/utils`:** outside the ADR's primitives row, and the legacy panels never use a design language.

The 2026-09-02 addendum already rules that `pressed-of.ts` moves to `primitives/` because "a primitive cannot import" it.

**Steelman for A (the view model carries the display-ready form).**
- VFO facts are already `T | null` (`VfoViewModel.frequencyHz`, `mode`, `filter`).
- `display?: DisplayObservation` is already a display qualifier produced by the adapter.
- It matches the ADR's "semantic logic once in adapters".

Against A:
- Behaviour decisions still need `reading` and `availability` in the same object, so the surfaces would still see a status.
- A required new key breaks `validateTxAuxField`'s `exactKeys(['reading','availability'])` and 416 hand-built `reading: {status…}` literals in 87 files (`grep -rhoE "reading: \{ ?status: '(known|unknown)'" src fixtures tests`). An optional key brings the fallback back.
- It holds a second copy of the value.
- The legacy panels (NaN marker), the frequency renderers (`null`), segmentline (`DisplayValue`) and the kit renderers never receive the view model.

**Steelman for B (surfaces call one shared function).**
- No contract change; each site swaps a ternary for a call.
- One function serves all three input vocabularies.
- Precedents exist: `pressedOf`, `bindToggleInstrument.confirmed`, `continuous-scalar: canonicalOf`.

Against B:
- Surfaces still pass the field object, so enforcement needs a lint rule later.
- The five import-closure pins need edits.

**Decision: B, in `primitives/`.** A new module with no runtime dependencies, next to `primitives/control-instruments/` and importing `InstrumentReading`/`InstrumentField` as types only. It maps a `{status}` reading, a `display.state` observation and a non-finite or null marker to `T | null`, adds a text form (`''` for nothing), and carries the slot class.

It must not live inside `control-instrument-behavior.ts`. That file ships in the component-kit pack (`component-kit-api/verify-package.mjs` asserts its `.d.ts`), so anything added there becomes public API. The view model and the adapters keep the status exactly as today.

## Q4: Behaviour that must keep the status

Every site below keeps reading the unchanged view model (`reading`, `availability`, `txTarget`, `txPermit`, `disabledReasons`); option B changes no producer.
- **TX:**
  - `rx-tx-surface.ts: keyBlockedReasons`, `txDisabledReasons`, `targetUnknownMessage`, and the `TxTargetViewModel.reason` union; `RxTxSurface: keyUnavailable`.
  - In the adapter, `currentBandTx: TxPermit` and the `disabledReasons` block.
  - The `validateRadioViewModel` invariants: an allowed permit with an unread band or an unread target is invalid.
  - `lib/stores/radio.svelte.ts` target validation; `tx-capabilities.ts`.
  - `lib/utils/tx-permit.ts: getFrequencyPermit`, and the transitional `getTxPermit` that `MobileRadioLayout` uses (it fails closed when unread).
- **Antenna:** `AntennaInstrumentHost: tunerIdle`, `antennaSwitchBlocks`, `ANTENNA_BLOCKED_LABEL`.
- **Gates:**
  - The shared fail-closed gate `control-instrument-behavior.ts: canInvoke` / `canInvokeAction`, and its 17 local `usable` copies (F3).
  - Scope span keys stay inert while the mode is unread (MOR-2565); `activeKnown` in RitXitScanSurface and RitXitScanInstrumentHost (the wrong-VFO guard).
  - `BandInstrumentHost: receiverKnown`; `RepeaterSurface: stepAvailable`; `RfFrontEndInstrumentHost` authority check (:147).
  - `VfoOperationGroup`'s 12 `disabled={!…operational}` bindings; `vfo-operation-projection.ts`.
  - `ActiveReceiverToggle`; `DualSdrFace: preEnabled`; `disabled-reason.ts: disabledReasonText`.
- **Command routing and authority:**
  - `SemanticRadioSurfaces`: 7 authority snapshots, `scopeAuthority`, band select, and the dual-watch and dial-lock toggles that act only when the value is read (plus :1926 and :1938).
  - `panel-adapters.ts` control-feedback lanes; `scope-adapter.ts`.
  - `EiBiBrowser` and `AmberCockpit: activeTuneReceiver` (20 byte-identical lines); `media-session.ts: tuneStep`.
- **Controller lifecycle:** the `continuous-scalar` and `continuous-pair` change-detection keys include `reading.status`.
- **External API:** the component-kit API exports `InstrumentReading` under `COMPONENT_KIT_API_VERSION = 1`. Changing it is a kit-API break, not a refactor.

## Step 3a: dead-code sweep

- **Scope:** 45 modules (`semantic`, `presentation/languages`, `primitives/control-instruments` and `skins/segmentline` `*.ts` files, plus `display-observation.ts`), holding 151 exported value symbols.
- **Live:** 117 have a production reader outside their own module.
- **Over-exported, not dead:** 30 are used only inside their own module.
- **Test-only, not adjudicated:** 4 language constants are read only by tests: `FIELDLINE_LABEL_TONES`, `SEGMENTLINE_SURFACES`, `RF_STATES`, `TX_SESSION_STATES`. They sit outside this mechanism.
- **Local helpers:** 67 display helpers in the surfaces and panels; all are used in their own file.
- **Missed by the grep:** the word search missed `projectRadioViewModel` because a comment mentions it. Tracing its imports found it (D1).

## Step 4: steelman for leaving everything as it is

Each readout's formatter differs in units and precision, and what is shared is one ternary. The ADR accepts inline markup in eleven of twelve surfaces. The census is already converging on `''`, the merged page guard's ratchet catches regressions, and a new import touches five closure-pinned safety surfaces.

- **The steelman wins for:** per-readout widths and formatters; the segmentline projection; the meters, which already pass value-or-null into the projector; and the frequency renderers, which already take `null`.
- **It loses where:** the same input gives different output (including the same-named `formatOffsetDisplay`); where copies are byte-identical five to nine times; and where the only shared gate, `canInvoke`, is not exported, so 17 copies grew.

## Q5: Verdicts

### Deletions

**D1: `presentation/languages/projection.ts: projectRadioViewModel`**
- **Verdict:** vestigial fork. The abandoned side is this module; the live side is `semantic/design-language-renderers.ts: renderSlot` (used by MetersSurface, RxTxSurface, VfoSurface).
- **Consumers:** tests only, `presentation/languages/__tests__/projection.test.ts`.
- **Written / read:** one definition, zero production imports (grep over src, tests, fixtures, component-kit-api). The `segmentline/frequency-renderer.ts` comment says the same.
- **Guards checked:** literal search only; not in the kit pack or the kit index; local-extensions and Pro consumers unknown; tests are the only consumer, so deleting the test is a human decision.
- **Collateral:** that test, the segmentline comment, and the MOR-1243 "unknownness preserved" docstring.
- **Depends on:** none. **Confidence:** high.
- **Falsifier:** any production or out-of-repo import.

**D2: `AntennaSurface.svelte` re-exports `UNKNOWN_TEXT`, `usable`, `textOf`**
- **Verdict:** dead.
- **Consumers:** none. AntennaSurface.test.ts imports four other names; the other importers use the default export.
- **Guards checked:** static Svelte module exports, no `import *` found.
- **Collateral:** the `AntennaInstrumentHost: UNKNOWN_TEXT` comment whose only justification is this re-export.
- **Depends on:** none. **Confidence:** high.

**D3: `ScopeControlsSurface: numberOf` fallback argument**
- **Verdict:** unreachable branch.
- **Why:** `spanText` is guarded by `usable(sc.span)`. The step `invoke` runs only through `createActionRendererSeat` → `bindActionInstrument` → `canInvoke`, which requires a read value. The fallbacks 3, 1 and 0 are never used.
- **Guards checked:** not exported.
- **Depends on:** none. **Confidence:** medium–high.

**D4: stale prose**
- **Verdict:** dead (describes behaviour the code no longer has).
- **Elements:** the `pressed-of.ts` sentence "`textOf`/`fmt` render `?`/`—`" (every copy returns `''`), and the `CwKeyerSurface.test.ts` claim that `pressedOf` is "shared with four sibling surfaces" (it has 2 production importers).
- **Depends on:** none. **Confidence:** high.

### Consolidations

**F1: Unread → display text**
- **Verdict:** B (gap). **Rank:** diverged.
- **Elements:** the 70 sites that give `''`, the ~20 observation→value copies, and the 26 diverged sites (Q2).
- **Divergence:** operator-visible: `''` against `—`, `---`, "unknown" and "n/a". Latently, `radio-display-model.ts: displayValue` treats a field that is read but not operational as unread, while `textOf` prints it.
- **Prior ruling:** MOR-2520 (2026-09-21), MOR-2651 and MOR-2650 (2026-09-26), and the owner's direction of 2026-09-27. VfoSurface tests cite MOR-2509 and MOR-2527 to pin the dash.
- **In-flight:** `pressedOf` (2 consumers); `bindToggleInstrument.confirmed`; segmentline `stateText`.
- **Required surface:** the Q3 primitive.
- **Depends on:** D4; the diverged subset needs its own tickets.
- **Confidence:** high. **Actionable:** yes.

**F2: Percent-display guard ×5, byte-identical**
- **Verdict:** A. **Rank:** parallel. (At the prior audit's revision `f9b599e9` there were only 2 copies.)
- **Depends on:** F1. **Confidence:** high.

**F3: The "usable" behaviour gate**
- **Verdict:** A (displaced). **Rank:** parallel.
- **Elements:** 17 `const usable` in 17 semantic files (`grep -rnE "(export )?const usable = "`); `acceptedNumber`/`acceptedBoolean` ×3 (VfoHeader and ScopeSettingsPopover identical, SpectrumToolbar differs only in a variable name); `activeTuneReceiver` ×2 identical.
- **Definition site:** `canInvoke` exists in primitives but is not exported.
- **Required surface:** export it.
- **Falsifier:** any copy differing in behaviour (none found).
- **Actionable:** yes, but separate from the drawing work.

**F4: Reserved-slot CSS**
- **Verdict:** A for the strut, `tabular-nums` and baseline trio; C for the ch widths. **Rank:** parallel.
- **Elements:** Q2.
- **Actionable:** yes, but only `visual` can prove it (Q7).

## Q6: Slices, in dependency order

The deletions D1–D4 are independent and can go first. Every run should leave `offenders.json` unchanged.

- **S1:** the primitive and its unit test, including a "no runtime import" pin modelled on `pressed-of.test.ts` (2 files); the slot-class carrier (1); `FilterSurface`, `FilterInstrumentHost` and `ScopeControlsSurface: textOf` plus ScopeControls' two inline copies (3). **6 files.**
  - Pins: FilterSurface.test.ts, FilterInstrumentHost.isolated.test.ts, ScopeControlsSurface.test.ts — 12 + 3 + 22 lines matching `toBe('')|not.toContain('—')|MOR-26[5-9]x|EMPTY|unlit`.
  - None of the three has a closure pin.
  - Risk: low, provided the CSS stays where it is.
- **S2:** RitXitScanSurface, RxAudioInstrumentHost, VfoIndicatorRow (the 8 `data-state={…reading.status}` attributes stay verbatim), DspSurface, RepeaterSurface, RfFrontEndInstrumentHost. **6 files.**
  - Pins: VfoIndicatorRow.test.ts (15), RitXitScanSurface.test.ts (9).
- **S3:** the closure-pinned surfaces BandSurface, RxAudioSurface, CwKeyerSurface, MemorySurface and AntennaInstrumentHost (after D2), plus their allow-list tests. **9–10 files.**
  - Depends on S1's purity pin.
- **S4:** the three host `formatValue`s, plus the observation copies `VfoSurface: displayValue` only, `VfoIndicatorRow: rfGainShown`, `ReceiverInstrumentHost` and `MetersSurface`. **7 files.**
  - FilterSurface's PBT helpers are excluded.
- **S5:** the legacy guards (TxPanel, MobileRadioLayout without its `---` trio, RxAudioPanel, EssentialsPanel, RfFrontEnd, CwPanel, RitXitPanel, FilterPanel, VfoPanel, SpectrumToolbar). **10 files.**
  - Pins: the panel isolated and honesty tests, `no-fabricated-defaults.test.ts`.
- **S6 (optional, after S5):** the renderers' shared default for unread values.
- **S7–S8:** the F4 CSS groups, identical groups first.
  - Proof must be `visual` with no baseline change plus `tests/e2e/i18n/desktop-geometry.spec.ts`.
  - The spread of the `1lh` baseline fix must not ride in these slices.

**Kept off the no-behaviour-change path:** the 26 diverged sites, the PBT `data-presentation` attribute, the VfoSurface dash, and the kit API.

## Q7: Risks and unknowns

- **R1, predicate drift.** Checking status alone equals `usable` for fields built by the adapter: `txAuxField` (:202) and `meterField` (:392) emit a read value only when operational. That no longer holds after a surface re-projects a field (`pbt-presentation-continuity.ts`, `RfFrontEndInstrumentHost: readingOf`, FilterSurface's `retained`), and the validator does not enforce it. Check: each migrated site gets a test case with a value that is read but not operational.
- **R2, drawing outputs that carry status.** `data-state` (paint rules in segmentline and VfoIndicatorRow), `data-presentation`, `aria-checked="mixed"`, `role="switch"` only when read, and the omitted `aria-pressed` must stay verbatim.
- **R3, closure pins.** Five surfaces carry import allow-lists whose premise is "cannot reach the TX controller, transport or permit"; editing them is a safety edit to review.
- **R4, CSS proof.** 49 test lines in 23 files pin the CSS by regex over the component source, so an F4 move breaks them without any pixel change. It is unknown whether any approved baseline renders an unread readout (check `fixtures/harness-state.ts`). The page guard checks tokens, not geometry.
- **R5, NaN formatters.** `normalizedPercentDisplay`, `formatPower` and `formatOffsetKHz` have no NaN branch. A dropped guard prints "NaN%".
- **R6, kit API.** Keep the primitive out of `control-instrument-behavior.ts` (Q3).
- **R7, owner rulings still open.**
  - The visible antenna-block sentences contain "unknown" (MOR-2691).
  - `telemetryDescription` aria-labels read "Unsupported" / "No reading" and are not in the ratchet.
  - Titles: "TX status unknown" (mobile), "Radio power state unknown" (status bar).
  - MOR-1243's "preserve unknown" ruling survives only in the code D1 deletes.
- **R8, `panel-props.ts: toVfoProps`.** It uses `'---'` as the unread marker for mode and filter and sets `isActive: receiver === 'main'` for an unread VFO. Whether either reaches the screen is unverified.

## Weakest link

The weakest verdict is F1 counting the VfoSurface tile dash as diverged. Two test comments cite MOR-2509 and MOR-2527 for keeping the dash on the semantic and sdr tiles. If a ruling from those tickets outlives MOR-2520, those items are a sanctioned exception, not a defect. Check first: MOR-2527 and MOR-2509 in Linear, with their dates against 2026-09-21. The ratchet's listing of the `vfo-list` dash under MOR-2655 points the other way.

## Cleared

- **Meters:** the unread path through `meter-renderer-view.ts`, `bar-meter-projector.ts`, `smeter-scale.ts: projectSignalMeter` and `meter-utils.ts` is one mechanism; an unread meter rests as an empty bar.
- **Frequency renderers:** they already take `null`; one copy per design language (C).
- **Segmentline:** `projectPeerSplitDisplay` with `lcd-display-helpers` is one mechanism for that family (C).
- **Adapter reading constructors:** `txAuxField` and `meterField` are the only two in the adapter.
- **Wiring:** its status uses are command authority and routing, correctly located.
- **`disabledReasonText`:** one title-sentence mechanism.
- **ch widths:** legitimately set per readout (C).
- **`getTxPermit`:** a documented transitional seam that fails closed.

Key files (absolute), in `frontend/`:
- `src/primitives/control-instruments/control-instrument-behavior.ts`
- `src/semantic/radio-view-model.ts`
- `src/semantic/pressed-of.ts`
- `src/presentation/languages/projection.ts`
- `src/semantic/VfoSurface.svelte`
- `src/components-v2/layout/MobileRadioLayout.svelte`
- `src/semantic/__tests__/CwKeyerSurface.test.ts`
- `component-kit-api/verify-package.mjs`