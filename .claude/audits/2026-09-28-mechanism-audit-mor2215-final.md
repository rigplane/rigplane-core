# MOR-2215 — final whole-path mechanism audit: frontend instrument layer

## Header

- **Revision:** `3c19f5c9e71cdc4626a96b465845b67778a47ebb` (`3c19f5c9`), detached `origin/main`, in a detached worktree at the pinned revision. `git status --short` was empty (clean tree).
- **Method followed:** `.claude/skills/mechanism-audit/SKILL.md`, read in full from that worktree. Steps 0–5 were run in the method's order, including the 3a dead-code sweep and the step-4 steelman, which comes before any verdict. The role's operating policy, `docs/internals/coordinator-policy.md`, was read from the same worktree.
- **Read-only:**
  - Commands used: `git` (rev-parse, status, log, show), `grep`, `sed`, `awk`, `find`, and one `node -e` that only reads files.
  - The two enumeration scripts were kept outside the repository, not in the tree.
  - Not done: tests, builds, npm, `gh`, Linear, network, or any write to the audited tree.
- **Model:** claude-opus-5-5. Whether the dispatcher selected it explicitly is unknown to me.
- **Scope:**
  - `frontend/src/primitives/`.
  - `frontend/src/components-v2/`: controls including value-control, meters, display, vfo, panels, layout and wiring.
  - `frontend/src/semantic/`, `frontend/src/presentation/`, `frontend/src/skins/`, `frontend/src/component-kits/` and `frontend/src/lib/runtime/adapters/`.
  - `frontend/component-kit-api/`.
  - Tests counted only as consumers.
  - The backend was not opened.
- **Prior rulings read:**
  - `.claude/audits/README.md`.
  - The 2026-09-26 frontend audit.
  - The 2026-09-27 audits: unread-readouts, control-domain-vectors, mor2688-design and mor2688-s4c, and mor2704-design.
  - The 2026-09-06 finite-choice audit (verdicts); cw-filter-feedback (F4/F5 and steelman).
  - Other 2026-09-06 audits: grep only.
  - `CLAUDE.md` (frontend layering, layer boundaries).
  - `docs/plans/2026-04-12-target-frontend-architecture.md`, including the 2026-09-02 addendum and its conformance checklist.
  - `docs/plans/2026-07-25-ui-composition-architecture-v3.md` (the target-ownership paragraph).
  - `frontend/eslint.config.js` (the import bans).
  - Linear: not read.
- **Labels:** [O] = observed in source; [I] = inference. Paths are relative to `frontend/src/` unless prefixed. Every count comes from a literal search, which cannot see computed dynamic access.
- **Premise check:** the mandate's words "remaining duplicated/displaced behavior" presuppose that some residue exists. I treated that as a question under test; step 4 argues the strongest case that nothing remains.
- **Text that directs an agent:** none aimed at an auditor. Developer instructions in comments were read as evidence, not obeyed. Two examples:
  - `lib/runtime/adapters/panel-adapters.ts` (the ARMED-SIGNAL CONTRACT block): "do not refactor without a concrete reason";
  - `components-v2/layout/LeftSidebar.svelte` (props doc): "Do not 'tidy' the two into one prop".

## How appearances reach instruments at this revision (context for every verdict)

- **Standard, `desktop-v2`:**
  - `skins/desktop-v2/DesktopSkin.svelte` → `components-v2/layout/RadioLayout.svelte`, semantic-deck branch plus `standardServicePanels`.
  - That branch consumes only `InstrumentComposition` handles from `components-v2/wiring/SemanticRadioSurfaces.svelte`. [O]
  - The manifest declares vfo, rxTx, txAux, meters, scopeDisplay, filter, rfFrontEnd, band, antenna, ritXitScan, repeater, rxAudio, dsp, cwKeyer, memory and scopeControls (`presentation/layouts/desktop-declarations.ts`, `DESKTOP_V2_ZONES`). [O]
  - So the legacy sidebars render only BandSelector (LW/MW + SWL) and AudioSpectrumPanel. [O]
- **`sdr-test` ("SDR Screen (test)", operator-selectable, `StatusBar.svelte` options):**
  - The same RadioLayout, but the non-standard arm: `instruments.dsp()`, `instruments.cwKeyer()` and the others with default arguments. [O]
- **Mobile:**
  - `skins/mobile/MobileSkin.svelte` → `components-v2/layout/MobileRadioLayout.svelte`.
  - The manifest declares only vfo and rxTx (`presentation/layouts/mobile-declarations.ts: MOBILE_ZONES`).
  - Semantic handles are used for the receiver S-meter, scopeControls and TX recovery.
  - Everything else is a legacy panel: EssentialsPanel, ScanPanel, RfFrontEnd, DspPanel, RitXitPanel, AgcPanel, AntennaPanel, CwPanel, FilterPanel, TxPanel, a raw RF-power ValueControl and FrequencyDisplay. [O]
- **Five LCD and segmentline skins** (lcd-cockpit, lcd-scope, peer-split, unified-instrument, panadapter-first):
  - They reach `components-v2/layout/LcdLayout.svelte`.
  - Their manifests declare only vfo and rxTx (`lcd-declarations.ts: LCD_ZONES`; `segmentline-declarations.ts`).
  - LcdLayout mounts `LeftSidebar`/`RightSidebar` without a `declared` set, so every legacy panel renders.
  - It also mounts AmberCockpit/AmberScope or PeerSplitLayout, and `lcd/VfoControlPanel.svelte`. [O]
- **`dual-sdr-face`:** `skins/dual-sdr-face/DualSdrFaceSkin.svelte`, display-only by its own comment. [O]
- **Component kits:** the shipped `component-kits.config.ts` is `{ kits: [] }` with no selection. So kit appearances are reachable only by configuration or through an external presentation. [O]

## Step 0 — definition sites (key shared mechanisms)

| Capability | Definition site [O] | Production consumers [O] |
|---|---|---|
| Continuous scalar lifetime, policies, renderer seat | `primitives/scalar/continuous-scalar.svelte.ts: createContinuousScalar, create{HBar,Bipolar,Discrete,Knob}ContinuousScalarPolicy, createRenderedNativeRangeContinuousScalarPolicy` | 25 non-test importers: value-control 8, panels 5, semantic 9, kit API, and others |
| RF/SQL pair | `primitives/scalar/continuous-pair.svelte.ts: createContinuousPair` | `panels/RfFrontEnd.svelte`, `semantic/RfFrontEndInstrumentHost.svelte`, value-control |
| Committed scalar (break-in delay) | `primitives/scalar/committed-scalar.svelte.ts` | `panels/CwPanel.svelte`, `semantic/CwKeyerSurface.svelte` |
| Finite behavior and gate | `primitives/control-instruments/control-instrument-behavior.ts: usable (@internal), bindActionInstrument, bindToggleInstrument, bindChoiceInstrument, bindAbsoluteChoiceInstrument` | 22 files in `semantic/`, plus `skins/dual-sdr-face/DualSdrFace.svelte` (`usable` only); 0 in `components-v2/panels/` |
| Finite renderer seats and kit appearance | `primitives/control-instruments/control-instrument-renderer.svelte.ts: create{Action,Toggle,Choice,AbsoluteChoice}RendererSeat`; `ControlInstrumentRendererHost.svelte` | 13 files in `semantic/` (the renderer host in 10), `component-kits/` (2), the wiring's renderer context; 0 in `components-v2/panels/` |
| Pending target (freshest in-flight) | `lib/runtime/adapters/panel-adapters.ts: latestPendingParam`; `armedFact`, which wraps it; the `getPending*`/`get*Armed` accessors | legacy panels (armed); `SemanticRadioSurfaces.svelte` (pending plus 3 armed) |
| Scalar renderers | `components-v2/controls/value-control/ValueControl.svelte`; HBar, Bipolar, Discrete, Knob and DualParam renderers; `skins/ProfessionalKnob.svelte`; `skin.ts: Skin` (= kit `ScalarAppearance`) | legacy panels plus 6 semantic hosts |
| Frequency | `primitives/frequency/FrequencyRendererSeat.svelte`, `FrequencyDisplayInteractive.svelte`, `frequency-readout.ts: projectFrequencyReadout`, `frequency-interaction.svelte.ts` | `semantic/ReceiverInstrumentHost`, `semantic/VfoSurface` via `components-v2/vfo/VfoPanel`, `components-v2/display/FrequencyDisplay` |
| Meters | `primitives/meters/meter-ballistics.svelte.ts`, `s-meter-scale.ts`; `components-v2/meters/smeter-scale.ts: projectSignalMeter`; `semantic/bar-meter-projector.ts`, `meter-renderer-view.ts`; `component-kits/MeterRendererSeat.svelte` | MetersSurface, StationMeterInstrumentHost, ReceiverInstrumentHost, legacy meters |
| Unread text rule | `primitives/reading-text.ts: finiteValue, valueText, readingText, observationValue` | 35 importing files, legacy and semantic |
| TX keying | `lib/runtime/tx-controller/managed-app-host: getManagedAppTxController`; `components-v2/wiring/managed-tx-gesture.ts` | AppGlobalHost, RadioLayout, MobileRadioLayout, TxPanel, SemanticRadioSurfaces, ManagedTotStatusControl |

## Step 1–2 — prior rulings and in-flight work that bind this audit

- **ADR addendum, 2026-09-02 (MOR-2215):**
  - It names the finite instrument set and says: "the single home going forward is `frontend/src/primitives/`".
  - "Legacy v1/v2 presentation under `frontend/src/components-v2/` is to be removed … the tree's removal is an owner decision recorded in MOR-2215".
  - Conformance item 3: "Never fabricate a value or `aria-pressed="false"` on a toggle over an unread reading (MOR-1358, P13)".
- **Dispatch (quoting the ticket):** "Standard reuses the v2.11.1 appearance, components and grid against current contracts". This permits the semantic hosts' use of components-v2 renderers, and eslint's `FORBIDDEN_SEMANTIC_IMPORTS` does not ban them. Those imports are not reported.
- **`docs/plans/2026-07-25-ui-composition-architecture-v3.md` (target ownership):** "Existing `components-v2/` … migrate incrementally; there is no flag-day move."
- **Look-preservation ruling:** `ControlButtonDemo.svelte` gallery text: "Per the owner's look-preservation ruling on MOR-2215 (comment `0e7ed41d`), [ProfessionalKnob] is not deleted". ProfessionalKnob is therefore not a deletion candidate.
- **MOR-2524/MOR-2727 (commit `77bda207`, 2026-09-27), for the semantic RIT/XIT fader:** "A double-click sends no reset; CLEAR stays the reset."
- **In-flight tracker, MOR-1713:** `frontend/scripts/control-feedback-debt-baseline.mjs: CONTROL_FEEDBACK_DEBT_BASELINE`. It has 39 rows and is enforced shrink-only by `control-feedback-debt-inventory.mjs: assertShrinkOnly`.
- **Landed since the prior audits** [O]:
  - MOR-2704 G-slices: one `usable` (the `@internal` export), `semantic/accepted-scope-values.ts` (2 consumers) and `components-v2/wiring/dual-receiver-strips.ts: activeTuneReceiver`.
  - MOR-2688 S4d: `semantic/meter-renderer-view.ts` goes through `observationValue`.
  - MOR-2728: the legacy VfoHeader branch is deleted.
  - `bindAbsoluteChoiceInstrument` exists, closing the 09-06 finite-choice F1 surface gap; consumers are BandInstrumentHost and RxAudioInstrumentHost.
- **Prior findings rechecked:**
  - Closed at this revision [O, grep]:
    - unread-readouts D1–D5;
    - mor2688-design D1 (`projectRadioViewModel` file gone) and D3 (the `numberOf` fallback);
    - 09-26 frontend D1 (`domainOf`).
  - Still present (cited, not re-reported):
    - 09-26 frontend F1, reader half (see F6);
    - 09-26 frontend F2, `panel-adapters.ts: freshestNotchStrand`;
    - mor2688-design Q2, "VFO surface" dash: the `'—'` slot label in `semantic/VfoSurface.svelte` survives in 3 expressions.

## Step 3a — dead-code sweep (counts)

**Module level** [O]:
- Every `.svelte` (148) and `.ts` (141) production file in the seven in-scope source directories was checked for a non-test importer specifier, static or `import(`.
- `import.meta.glob` and template-literal imports occur 0 times.
- 22 were flagged. On re-check with the `.svelte.ts` import form, 13 were cleared.
- Remaining:
  - no importer at all: `components-v2/layout/MobileNav.svelte`, `components-v2/controls/StatusBadge.svelte`, `components-v2/controls/InstallPrompt.svelte`;
  - tests only: `components-v2/controls/PullToRefresh.svelte`, `components-v2/panels/VoxPanel.svelte`, `components-v2/layout/layout-utils.ts`;
  - test-support by design (cleared, see Cleared): `semantic/internal-identifier-rule.ts`, `presentation/languages/state-vocabulary.ts`, `semantic/fixtures/topologies.ts`.

**Export level** [O]:
- 1143 `export` declarations in the 141 `.ts` modules. For each name I counted files outside its definition, split into non-test and test.
- Most names with no external reader are types used in their own module's signatures: over-exported, not dead.
- 33 names appear only on their own definition line. The values among them:
  - `vfo-adapter.ts: deriveMainVfo, deriveSubVfo`;
  - `vfo-ops-utils.ts: vfoCopyLabel, vfoTxLabel`;
  - `keyboard-map.ts: resolveSequenceStart`;
  - `layout-utils.ts: hasLiveAudioFromState`;
  - the tests-only helpers in D8.
- Svelte module-script exports:
  - `semantic/ScopeControlsSurface.svelte: numberOf` has 0 external importers (over-export);
  - `semantic/AntennaSurface.svelte` re-exports `ANTENNA_PORTS, tunerIdle, antennaSwitchBlocks` for `AntennaSurface.test.ts` only.
- **Limit:** non-exported Svelte-local declarations were swept only in the files opened for findings, not file by file across all 148 components.

## Step 4 — steelman (written before the verdicts)

The strongest case that the current arrangement is correct and nothing should move:

1. **Layer 1 is already shared everywhere it matters.**
   - Legacy panels and semantic hosts call the same engines: `createContinuousScalar`/`createContinuousPair`/committed-scalar, the same `panel-adapters` feedback and pending accessors, the same `panel-commands` handlers (which fail closed on unread fields, e.g. `makeAgcHandlers.onAgcModeChange` needs `knownReceiverField`) and one TX controller.
   - What differs is presentation. A legacy host showing less is display debt, not a second radio truth.
2. **The legacy hosts are explicitly transitional.**
   - The ADR addendum orders components-v2 presentation removed; the composition plan says "no flag-day move".
   - The mobile/LCD/segmentline manifests honestly declare that only vfo and rxTx are migrated.
   - MOR-1713 tracks the unbound controls with a ratchet.
   - Porting behavior into code scheduled for deletion is waste; the conclusion would be "retire", not "consolidate".
3. **Standard is required to reuse the v2.11.1 look.** Hardware-style keys without aria state, toggle-style break-in keys and compact DSP keys are that look. `standard`/`appearance` props are explicit, named behavior policies (a legitimate task-dependent difference), not hidden dependencies.
4. **Kit appearances are unshipped.** `component-kits.config.ts` has no kits, so kit-contract gaps have no live path.
5. **The semantic scalar hosts already protect renderer changes.**
   - TxAuxScalarHost, DspScalarHost and CwKeyerInstrumentHost render the confirmed value (`data-canonical-value`) and the command status (`data-command-status`) outside the renderer.
   - They retire HBar-only status on a switch to the knob form.
   - So a form change loses nothing there.

Where it wins, and these are cleared:
- points 1 and 5 for the continuous engines and scalar hosts;
- point 3 for break-in toggle keys and other explicit `standard` interaction policies;
- point 4 for the priority of kit gaps.

Where it loses:
- **Point 3 does not cover fabricating state.** "Against current contracts" includes the addendum's honest-unknown rule. Standard's compact keys say "not pressed" about unread readings while the same host's other renderings stay silent (F7). Standard also omits pending targets that the current armed-signal contract says consumers must not suppress (F3).
- **Point 1 does not cover command policy.** The legacy RIT fader sends a reset that the semantic one refuses by ruling (F1), and native DSP inputs dispatch on every input event (F2).
- **Point 2 is a scheduling argument.** It holds only if the owner has scoped mobile/LCD out of MOR-2215, and no in-repo ruling says so. The required outcome says "every existing user-facing instrument", and mobile and the five LCD/segmentline skins are live, selectable appearances today.

## Step 5 — verdicts

### DELETIONS (independent, no design decision)

### D1 — `lib/runtime/adapters/vfo-adapter.ts: deriveMainVfo, deriveSubVfo`: dead
```
Verdict:          dead
Elements:         vfo-adapter.ts: deriveMainVfo, deriveSubVfo (deriveVfoOps in the same module stays live:
                  components-v2/panels/lcd/VfoControlPanel.svelte)
Consumers:        none
Written / read:   1 definition, 0 reads each; `grep -rnw` over frontend/src, tests, fixtures,
                  component-kit-api, scripts (literal)
Guards checked:   dynamic access: none found (literal search); out-of-repo: local extensions reach the
                  host through lib/local-extensions/host-api.ts (state + intents), not UI modules [O imports];
                  Pro tier unknown; public API: not in component-kit-api; tests-only: no tests either
Collateral:       none (architecture-boundaries.test.ts names the module path, not these symbols)
Depends on:       none
Confidence:       high
Falsifier:        an out-of-repo import of these two functions
Fix class:        delete
```
[I] Probably orphaned by `71006ba8` (MOR-2728), which deleted `LegacyVfoPanelAdapter.svelte`. This is inferred from the commit's file list; the diff was not opened. `toVfoProps` stays live because MobileRadioLayout uses it.

### D2 — `components-v2/vfo/vfo-ops-utils.ts: vfoCopyLabel, vfoTxLabel`: dead
```
Verdict:          dead
Elements:         vfo-ops-utils.ts: vfoCopyLabel, vfoTxLabel (vfoSwapLabel/vfoEqualLabel stay: VfoOperationGroup,
                  VfoOperationSeatHost)
Consumers:        none
Written / read:   1 / 0 each, including tests (literal grep)
Guards checked:   as D1
Collateral:       none
Depends on:       none
Confidence:       high
Falsifier:        an out-of-repo importer
Fix class:        delete
```
[I] The consumer was `VfoOps.svelte`, which `71006ba8` deleted.

### D3 — `components-v2/layout/keyboard-map.ts: resolveSequenceStart`: vestigial fork
```
Verdict:          vestigial-fork (abandoned side: resolveSequenceStart; live side: resolveSequenceStarts)
Elements:         keyboard-map.ts: resolveSequenceStart
Consumers:        none (0 reads incl. tests)
Written / read:   1 / 0; git log -S shows the plural form added by 7ca8fdc6 (#2612, 2026-08-14)
Guards checked:   as D1
Collateral:       none
Depends on:       none
Confidence:       high
Falsifier:        an out-of-repo keyboard consumer
Fix class:        delete
```

### D4 — `components-v2/layout/RadioLayout.svelte`: unreachable arms and the state only they read
```
Verdict:          dead (branches made unreachable by constant manifests and a two-mount census)
Elements:         RadioLayout.svelte template: the `skinId === 'mobile'` / `'lcd-cockpit'` / `'lcd-scope'` arms;
                  the final `{:else}` legacy grid (LeftSidebar/RightSidebar/SpectrumPanel and the
                  `{#if !semanticMeters}` MetersDockPanel dock); in the settings modal the
                  `{#if !declared.has('dsp'|'rfFrontEnd'|'ritXitScan'|'cwKeyer')}` DspPanel/AgcPanel/RfFrontEnd/
                  RitXitPanel/CwPanel blocks and the `{#if !semanticDeck}` SPLIT/A↔B/A=B row. Script state read
                  only there: activeReceiverLabel (already 0 reads), meterTxActive and its txCtl subscription,
                  vfoOps / toVfoOpsProps, vfoHandlers
Consumers:        production none; tests: semantic-desktop-migration.component.test.ts registers probe manifests
                  (VFO_ONLY, RX_TX_ONLY, PRE_BATCH_2/3); a RadioLayout test mounts skinId 'no-such-layout'
Written / read:   mounts: exactly 2 production `<RadioLayout` (DesktopSkin 'desktop-v2', SdrTestSkin 'sdr-test') [O];
                  both manifests declare vfo, meters, dsp, rfFrontEnd, ritXitScan, cwKeyer [O]
Guards checked:   dynamic access: registerLayouts rejects an already-registered id, so a kit cannot redefine
                  either manifest [O presentation/layouts/contract.ts]; out-of-repo: RadioLayout is not in the kit;
                  public API: no; tests-only: yes (flag)
Collateral:       the tests exercising these arms; MOR-1409 A13b comment (D10)
Depends on:       none
Confidence:       high
Falsifier:        a third production mount of RadioLayout, or a desktop-v2/sdr-test manifest without these zones
Fix class:        delete
```

### D5 — `components-v2/panels/MetersDockPanel.svelte`: vestigial fork, production-dead after D4
```
Verdict:          vestigial-fork (live side: semantic/MetersSurface.svelte + StationMeterInstrumentHost)
Elements:         MetersDockPanel.svelte (439 lines)
Consumers:        its only production mount is inside D4's unreachable fallback; tests:
                  MetersDockPanel.isolated / .peakhold.svelte / .reduced-motion.svelte
Written / read:   1 production mount (dead arm), 3 test files
Guards checked:   as D4; DockMeterPanel.svelte is a different, live component (MobileRadioLayout)
Collateral:       the three test files; comment mentions in BarGauge/MetersSurface tests
Depends on:       D4
Confidence:       high
Falsifier:        a live mount outside RadioLayout's fallback
Fix class:        delete
```

### D6 — Legacy components with no production importer
```
Verdict:          dead
Elements:         components-v2/layout/MobileNav.svelte (0 importers anywhere; a comment in responsive.css names it);
                  components-v2/controls/StatusBadge.svelte (0; ControlButtonDemo mentions it in prose) and then
                  status-badge-style.ts (consumers: StatusBadge.svelte + StatusBadge.test.ts);
                  components-v2/controls/InstallPrompt.svelte (0) and then install-prompt-utils.ts
                  (InstallPrompt.svelte + test);
                  components-v2/controls/PullToRefresh.svelte (tests only: PullToRefresh.test.ts);
                  components-v2/panels/VoxPanel.svelte (tests only: VoxPanel.component.test.ts)
Consumers:        as listed
Written / read:   importer census above (literal, static and `import(`)
Guards checked:   dynamic access: no glob or computed imports in src [O]; out-of-repo/public: not in the kit;
                  tests-only: PullToRefresh, VoxPanel, and the two *-utils once their component goes (flag)
Collateral:       tests named above; 3 MOR-1713 baseline rows for VoxPanel (D10)
Depends on:       status-badge-style / install-prompt-utils only after their component
Confidence:       high (MobileNav, StatusBadge, InstallPrompt), high (tests-only ones)
Falsifier:        a mount through a path this literal search cannot see
Fix class:        delete
```

### D7 — `components-v2/layout/layout-utils.ts` (sole export `hasLiveAudioFromState`): tests only
```
Verdict:          dead (tests-only)
Elements:         layout-utils.ts: hasLiveAudioFromState
Consumers:        tests only: RadioLayout.isolated.test.ts (describe 'hasLiveAudioFromState'); two meter tests
                  list the path string in scan inventories (smeter-no-double-conversion.test.ts,
                  mor1409-meter-adapter-reachability.test.ts)
Written / read:   1 / 0 production; `git log -S hasLiveAudioFromState -- frontend/src` shows only c13051f5 (add)
                  and 400dd0dd (tests)
Guards checked:   as D1; tests-only yes (flag)
Collateral:       the describe block and the two path-list entries
Depends on:       none
Confidence:       high
Falsifier:        a production importer
Fix class:        delete
```
The 2026-09-27 unread-readouts audit listed `hasLiveAudioFromState` as live in its Cleared section. At this revision the git history above does not support that. [O/I]

### D8 — Tests-only helper exports (production-dead)
```
Verdict:          dead (tests-only), except calculateDragValue/handleWheelStep: undetermined under the 2026-09-06
                  native-scalar-normalization ruling ("Public exported helper … No production caller established")
Elements:         primitives/control-feedback/control-feedback-presentation.ts: confirmedPressed, confirmedChecked,
                  confirmedSelected; primitives/scalar/continuous-pair.svelte.ts: nativeRangeContinuousPairPolicy
                  (production uses createRenderedNativeRangeContinuousPairPolicy); primitives/scalar/value-control-core.ts:
                  dualParamThumbPercent, dualParamDeviationFromValues, calculateDragValue, handleWheelStep;
                  components-v2/meters/bar-gauge-utils.ts: valueToSegments; components-v2/panels/rf-frontend-utils.ts:
                  buildPreOptions; cw-panel-logic.ts: isBreakInActive; dsp-panel-logic.ts: isNotchActive;
                  panels/audio-scope/audio-spectrum-renderer.ts: resetSmoothing; lib/runtime/adapters/
                  mod-input-auto.svelte.ts: isAutoLanModInputEnabled; presentation/workspace/contract.ts:
                  workspaceLayoutManifestId; workspace/store.svelte.ts: setPinnedCommands; workspace/legacy-readers.ts:
                  classifyLegacyKey; presentation/groups/contract.ts: listGroupIds; presentation/layouts/contract.ts:
                  listLayoutIds; skins/registry.ts: presentationResourcePlan
Consumers:        tests only (each name: 0 production reads outside its definition; 1–6 test files)
Written / read:   export sweep above (literal `grep -w`)
Guards checked:   dynamic access: literal only; public API: none is exported by component-kit-api (the kit asserts
                  its packed .d.ts set and strips @internal); out-of-repo: Pro unknown; tests-only: yes (flag each)
Collateral:       the named tests
Depends on:       none
Confidence:       high (medium for the two value-control-core helpers under the prior ruling)
Falsifier:        an out-of-repo consumer, or a ruling that these are a supported API
Fix class:        delete (human decision per test)
```

### D9 — `skins/dual-sdr-face/DualSdrFace.svelte`: preamp-cycle branch is dead in production
```
Verdict:          dead (tests-only)
Elements:         DualSdrFace.svelte: onPreChange prop, preEnabled, preNext (a local next-value cycle over preValues)
Consumers:        production: DualSdrFaceSkin.svelte `readonlyDisplay` passes no callback ("No command callback:
                  this production entrypoint remains display-only"), so P.AMP is always disabled; tests:
                  DualSdrFace.component.test.ts (cycle); DualSdrFaceSkin.component.test.ts pins
                  `not.toContain('onPreChange=')`
Written / read:   1 production mount without the prop [O]
Guards checked:   public API: no; out-of-repo: none; tests-only: yes (two tests pin opposite halves; flag)
Collateral:       the cycle test
Depends on:       none
Confidence:       high
Falsifier:        a planned wiring of this face (then it becomes a duplicate of
                  lib/runtime/commands/panel-commands.ts: keyboardCycle inside a skin: radio semantics at level 2)
Fix class:        delete (human decision)
```

### D10 — Dead prose and dead tracker rows
```
Verdict:          dead (prose / data)
Elements:         (a) components-v2/panels/lcd/VfoControlPanel.svelte docstring: "The remaining buttons have no
                  semantic equivalent yet". Semantic equivalents exist (VfoOperationGroup swap/equalize,
                  RitXitScanInstrumentHost XIT/clear, TxAuxFiniteHost atuTune, CwKeyerSurface break-in) but are not
                  mounted on LCD, so the sentence is ambiguous.
                  (b) RadioLayout.svelte MOR-1409 A13b comment: the panels "now self-source their state through the
                  semantic surfaces". They source through lib/runtime/adapters/panel-adapters.ts
                  (e.g. AgcPanel: deriveAgcProps, getAgcHandlers) [O].
                  (c) scripts/control-feedback-debt-baseline.mjs: 5 of 39 identities name value expressions absent
                  from their files: CwKeyerSurface ×1, DspSurface `numberOf(dsp.nbLevel, 0)`, RfFrontEndSurface ×2,
                  RxAudioSurface ×1 [O, file readback]. assertShrinkOnly fails only on growth, so these rows are
                  inert. Plus 3 rows for dead VoxPanel.
Consumers:        none
Written / read:   (a)(b) 1 comment each; (c) 5 of 39 identities with no matching expression in their file [O]
Guards checked:   n/a; whether bound legacy sites (e.g. CwPanel CW Pitch, feedback-policy spread) are still
                  classified radio-backed: unknown (answering it needs the inventory run, excluded)
Collateral:       control-feedback-debt-baseline.test.ts pins the count (39) and a sha256 digest
Depends on:       (c) after D6 for the VoxPanel rows
Confidence:       high (a, b as text facts; deletion per the owner ruling of 2026-08-31), high (c, the 5 rows)
Falsifier:        (c) the inventory still resolving those identities by another route
Fix class:        delete
```

### DUPLICATION (ranked by debugging cost)

### F1 — Bipolar fader reset: Standard follows its rulings, the legacy hosts reset per lane from the track
```
Verdict:          A — the reset policy was decided in the Standard hosts and not converged in their twins;
                  migration incomplete
Rank:             diverged
Elements:         RIT/XIT — semantic/RitXitScanSurface.svelte: offsetPolicy, offsetInput, changeOffset vs
                  components-v2/panels/RitXitPanel.svelte: handleOffsetChange and its raw
                  `<ValueControl label="Offset" renderer="bipolar">` (ValueControl.svelte: createRawBinding →
                  createBipolarContinuousScalarPolicy({debounceMs: 50})).
                  PBT — semantic/FilterSurface.svelte: passbandInput (PBT rows declare no lease default;
                  label/value double-click calls onPbtReset) vs components-v2/panels/FilterPanel.svelte: passbandPolicy,
                  pbtInput (defaultValue 0 on each PBT lane), pbtInnerBinding, pbtOuterBinding
Consumers:        semantic: Standard, and sdr-test for RIT; legacy: MobileRadioLayout (rit chip, setup sheet, filter
                  sheet) and LeftSidebar on the five LcdLayout skins (no declared set) [O]
Written / read:   RIT reset override: 1 site (RitXitScanSurface: offsetPolicy); legacy RIT fader: 1 raw site;
                  FilterPanel pbtInput defaultValue 0 on 2 lanes; onPbtReset callers: FilterSurface label/value cells
                  and FilterPanel's Reset button [O]
Guards checked:   not a deletion; the kit exposes none of these hosts
Collateral:       legacy tests that pin the track double-click, if any (not enumerated: unknown)
Definition site:  primitives/scalar/continuous-scalar.svelte.ts: createBipolarContinuousScalarPolicy
                  (reset: domain.defaultValue ?? 0); lib/runtime/commands/panel-commands.ts:
                  makeFilterHandlers.onPbtReset (the atomic, lattice-centred, gated PBT reset)
Divergence:       [O] RIT/XIT track double-click: semantic `reset: () => null`, so no command; legacy
                  lease.reset() → applyCandidate('reset') → onRitOffsetChange(raw_origin ?? 0) or XIT. PBT: Standard
                  sends one atomic onPbtReset from the label/value cell; the legacy track double-click sends a per-lane
                  set_pbt_inner/outer to 0 Hz through its own lane. [I] The pointer-down of that double-click first
                  sends a position command, the hazard MOR-2535 names. Arrow keys on RIT: semantic immediate with
                  keyboardStep = raw_step; legacy debounced 50 ms, keyboardStep 50 only with a domain. IF shift: both
                  reset to 0 — the same command, geometry only (cleared).
Prior ruling:     77bda207 (MOR-2524 slice 2a / MOR-2727, 2026-09-27): "A double-click sends no reset; CLEAR stays
                  the reset" (RitXitScanSurface). FilterSurface header (MOR-2535): "The reset gesture lives on the row's
                  LABEL and VALUE cell, never on the range track"; "the ONE PBT reset dispatch site … not split across
                  two lease resets". No ruling for the legacy twins found.
In-flight:        MOR-1713 baseline rows `RitXitPanel.svelte::ValueControl::Offset::offsetValue`,
                  `FilterPanel.svelte::ValueControl::PBT Inner::pbtInner`, `…::PBT Outer::pbtOuter`
Required surface: exists (the reset override; the onPbtReset handler, which legacy FilterPanel already calls from
                  its Reset button); converge by adopting those, or retire the legacy hosts
Depends on:       owner scoping of legacy mobile/LCD hosts (closure section)
Confidence:       high on source; runtime not exercised
Falsifier:        a ruling that the legacy faces keep the v2 track double-click reset, or these panels absent from
                  shipped skins
Fix class:        consolidate
Actionable:       yes
```

### F2 — Continuous controls lose command feedback, and change dispatch policy, with the appearance
```
Verdict:          A — migration incomplete: bound command-feedback scalars exist and are consumed by the
                  Standard hosts, not by these sites
Rank:             diverged
Elements:         reading-evidence-only sites [O]: RF power — components-v2/panels/TxPanel.svelte ("TX LEVELS" modal)
                  and components-v2/layout/MobileRadioLayout.svelte (power BottomSheet); AF level — panels/RxAudioPanel,
                  panels/EssentialsPanel; NR level and NB depth — panels/DspPanel; sdr-test/grouped faces —
                  semantic/DspSurface.svelte `nativeLevel` `<input type="range" oninput=level(...)>` for nrLevel,
                  nbDepth, notchFreq, agcTimeConstant (and manualNotchWidth without choices).
                  Twins [O]: semantic/TxAuxScalarHost.svelte input('rfPower') (rfPowerFeedback from
                  getRfPowerControlFeedback, always supplied); semantic/RxAudioInstrumentHost.svelte afLevelBinding
                  (command-feedback when runtime.rxEnabled is false); semantic/DspScalarHost.svelte input() (all 7 DSP
                  scalar fields). DspSurface receives these DspScalarHost handles on every face but uses them for the
                  four fields only when compactAgcTime (Standard) is set.
Consumers:        legacy sites: mobile and the LCD skins (RightSidebar RxAudioPanel/DspPanel); native DSP path:
                  sdr-test (RadioLayout non-standard arm `instruments.dsp()`) and grouped faces; twins: Standard
Written / read:   7 legacy raw sites plus 1 native snippet (up to 5 DSP fields) [O]; each has a MOR-1713 baseline row
Guards checked:   not a deletion; the kit is not involved
Collateral:       the baseline rows shrink as sites adopt; panel tests (not enumerated)
Definition site:  continuous-scalar.svelte.ts; feedback accessors in lib/runtime/adapters/panel-adapters.ts
Divergence:       [O] no requested/confirmed/error status and no aria-busy at the legacy/native sites; dispatch:
                  raw debounce 50 ms vs host debounceMs 0 / rendered-native-range policy vs native per-input-event
                  (queue coalescing downstream: unknown); gating: `disabled` prop vs usable() + feedback availability;
                  the native input places an unread thumb at numberOf(…, min) (aria-valuenow = min), while the
                  bound renderer draws none
Prior ruling:     ADR addendum 2026-09-02 (legacy presentation to be removed); mobile/LCD/segmentline manifests
                  declare only vfo+rxTx; 09-26 frontend F3 ruled the notch-width *markup* C, not this dispatch path
In-flight:        MOR-1713 baseline lists every site above; already adopted: CwPanel pitch/speed, FilterPanel
                  width/PBT/IF shift, RfFrontEnd RF gain, TxPanel mic/drive/comp/mon [O bound]
Required surface: exists
Depends on:       owner scoping (legacy sites); none for the sdr-test DSP path
Confidence:       high
Falsifier:        these panels absent from shipped skins, or a ruling that legacy/test faces are out of MOR-2215
Fix class:        consolidate (adoption)
Actionable:       yes
```

### F3 — Pending-target coverage diverges between host families; Standard omits it for mode, AGC and attenuator
```
Verdict:          A — the target signal is shared; the Standard hosts do not consume it for these fields
Rank:             diverged
Elements:         [O] Standard hosts with no requested target: semantic/FilterInstrumentHost.svelte: modeSeat,
                  mode(), standardMode() (mode); semantic/DspInstrumentHost.svelte: agcSeat, agcKeys (AGC),
                  compactDspButton/compactAutoNotch (NB/NR/NOTCH/A-NOTCH: the pending marker that toggle() and
                  notchMode() render is dropped), manualNotchSeat/autoNotchSeat (no `requested`);
                  semantic/RfFrontEndInstrumentHost.svelte: attenuatorSeat and the attenuator snippet.
                  Legacy hosts that render it: panels/ModePanel (getModeArmed), AgcPanel (getAgcArmed), RfFrontEnd
                  (getAttenuatorArmed, 2-value), DspPanel (getManualNotchArmed/getAutoNotchArmed)
Consumers:        Standard (desktop-v2), sdr-test, and kit seat views vs mobile/LCD
Written / read:   requested-target input for mode/AGC/attenuator in semantic/: 0 (grep); legacy armed accessors: one
                  read each in their panels [O]
Guards checked:   not a deletion
Collateral:       none
Definition site:  lib/runtime/adapters/panel-adapters.ts: latestPendingParam (armedFact wraps it; getPending* call it)
Divergence:       after a MODE/AGC/ATT click, Standard marks nothing until readback; LCD/mobile mark the key armed
                  with an sr-only announcement. grep for set_mode/set_agc/set_attenuator in semantic/ and wiring: 0
Prior ruling:     panel-adapters.ts ARMED-SIGNAL CONTRACT (MOR-1519/MOR-1536): consumers "MUST NOT suppress the
                  confirmed-vs-armed distinction"; no Standard exemption found
In-flight:        none found
Required surface: exists (the armed/pending accessors; the seat `requested` field)
Depends on:       none
Confidence:       high on source; runtime not exercised
Falsifier:        a Standard-path pending presentation for these fields through another channel
Fix class:        consolidate (adoption)
Actionable:       yes
```

### F4 — Legacy finite controls do not adopt the shared finite behavior
```
Verdict:          A — migration incomplete
Rank:             diverged
Elements:         HardwareButton keys in panels/ModePanel, FilterPanel (select/shape), AgcPanel, DspPanel, RfFrontEnd,
                  TxPanel, RitXitPanel, ScanPanel, AntennaPanel, EssentialsPanel, panels/lcd/VfoControlPanel
Consumers:        mobile and the five LCD/segmentline skins
Written / read:   imports of control-instrument-behavior/-renderer in components-v2/panels: 0 [O]
Guards checked:   not a deletion
Collateral:       legacy panel tests (not enumerated)
Definition site:  control-instrument-behavior.ts and control-instrument-renderer.svelte.ts (0 consumers in panels/)
Divergence:       [O] (a) availability: legacy keys stay enabled over unread readings, and the shared handlers refuse
                  (panel-commands.ts: makeAgcHandlers.onAgcModeChange) — same outcome, the control reads as
                  available; (b) AT: lib/Button/ControlButton.svelte renders data-active but never aria-pressed, so
                  legacy toggles expose no confirmed on/off (semantic: aria-pressed={behavior.confirmed});
                  (c) VFO ops: VfoControlPanel draws "A↔B"/"A=B" without scheme or capability gates, vs
                  vfo-ops-utils.ts: vfoSwapLabel/vfoEqualLabel and vfo-operation-projection
Prior ruling:     09-06 finite-choice F1–F5 (semantic scope); ADR addendum items 3 and 5; MOR-1092 note in VfoControlPanel
In-flight:        semantic twins exist and are mounted on Standard
Required surface: exists
Depends on:       owner scoping
Confidence:       high as code facts; per-radio operator impact unknown
Falsifier:        a ruling that legacy skins are out of MOR-2215
Fix class:        consolidate (adoption) or retire the legacy hosts
Actionable:       after scoping
```

### F5 — Mobile frequency readout: a parallel copy of FrequencyRendererSeat, confirmed-only
```
Verdict:          A — incomplete adoption
Rank:             diverged for the pending target; parallel for the seat logic
Elements:         components-v2/display/FrequencyDisplay.svelte (inline getSelectedFrequencyReadout,
                  createFrequencyInteractionLease, StandardFrequencyReadout fallback,
                  projectFrequencyReadout({confirmedHz: freq})) vs primitives/frequency/FrequencyRendererSeat.svelte
Consumers:        FrequencyDisplay: MobileRadioLayout (2 mounts); FrequencyRendererSeat: FrequencyDisplayInteractive
                  (VfoPanel, VfoSurface), ReceiverInstrumentHost
Written / read:   FrequencyDisplay: 2 mounts; pendingDisplayHz passed 0 times [O]
Guards checked:   public API — FrequencyRenderer (kit) is honoured by both paths
Collateral:       none
Divergence:       [O] mobile passes no pendingDisplayHz, so a tune in flight is not marked pending
                  (SemanticRadioSurfaces reads getPendingFrequencyHz 3×); [I] the renderer is captured once, which is
                  inert today because main.ts: startApp activates kits before mount
Prior ruling:     none found
In-flight:        none found
Required surface: exists; whether primitives/frequency/frequency-instrument.svelte.ts can express a passive
                  binding: unknown
Depends on:       owner scoping (mobile)
Confidence:       high (source)
Falsifier:        mobile tuning designed never to have a pending state
Fix class:        consolidate
Actionable:       yes
```

### F6 — RF/SQL published raw range: three readers (prior 09-26 frontend F1, reader half, still present)
```
Verdict:          B (as ruled 2026-09-26); the formula half was refuted afterwards (README: backend normalizes by 255, MOR-2637)
Rank:             parallel
Elements:         semantic/RfFrontEndInstrumentHost.svelte: rawRangeOf/safeRawBound; components-v2/wiring/
                  SemanticRadioSurfaces.svelte: rfFrontEndRawControlPublished; lib/runtime/commands/panel-commands.ts:
                  keyboardControlRawDomain (not opened beyond grep)
Consumers:        unchanged from the 2026-09-26 report
Written / read:   as in the 2026-09-26 report (rechecked by grep: all three readers still present)
Guards checked:   n/a
Collateral:       none
Divergence:       validation strictness only (lenient 0/255 keyboard fallback)
Prior ruling:     2026-09-26-mechanism-audit-frontend.md F1; README note
In-flight:        none
Required surface: as in the prior report
Depends on:       none
Confidence:       high
Falsifier:        as in the prior report
Fix class:        design (small)
Actionable:       low urgency
```

### DISPLACEMENT

### F7 — Standard renderings re-derive pressed or selected state beside the shared confirmed getter
```
Verdict:          A — displaced: a local re-derivation beside
                  control-instrument-behavior.ts: bindToggleInstrument.confirmed
Rank:             diverged
Elements:         [O] semantic/DspInstrumentHost.svelte: compactDspButton (active = reading known && value === true
                  → aria-pressed={active}), compactAutoNotch (aria-pressed={notchBehavior.isSelected('auto')});
                  semantic/CwKeyerSurface.svelte `{#if standard}` APF key (aria-pressed={apfChoice.isSelected(true)});
                  semantic/FilterInstrumentHost.svelte: standardMode (HardwareButton; ControlButton renders no aria state)
Consumers:        Standard only: RadioLayout dspFiniteLayout renders the compact keys; standardServicePanels passes
                  standard=true to cwKeyer; standardModeLayout renders standardMode
Written / read:   4 renderings (compactDspButton, compactAutoNotch, CwKeyerSurface Standard APF, standardMode) [O]
Guards checked:   not a deletion
Collateral:       tests that pin Standard compact-key attributes (unknown)
Definition site:  honest forms in the same hosts: DspInstrumentHost.toggle (aria-pressed={behavior.confirmed}),
                  notchToggle (selected === undefined ? undefined : …); CwKeyerSurface break-in keys (MOR-2690:
                  aria-pressed only when known); FilterInstrumentHost.mode (aria-pressed={available && isSelected})
Divergence:       unread NB/NR/NOTCH/A-NOTCH/APF read "not pressed" on Standard and carry no state elsewhere;
                  Standard's mode keys expose no selection to assistive technology
Prior ruling:     ADR addendum 2026-09-02 item 3 (MOR-1358, P13); per-option false is accepted for choice groups
                  (09-06 finite-choice F4), so standardMode's defect is the missing state, not a fabricated one
In-flight:        none found
Required surface: exists
Depends on:       none
Confidence:       high on source; assistive-technology output not run
Falsifier:        an owner ruling that exempts the v2.11.1 look from P13
Fix class:        consolidate
Actionable:       yes
```

### F8 — The public scalar appearance contract lacks seams the built-ins receive
```
Verdict:          B — gap in components-v2/controls/value-control/skin.ts: Skin (= component-kit-api ScalarAppearance)
Rank:             diverged (latent)
Elements:         skin.ts: Skin.bipolar typed Component<SkinRendererProps>; ValueControl.svelte skinComponent branch
                  passes rendererProps, without issuedStatusPresentation, to a bipolar appearance, while the built-in
                  BipolarRenderer receives it; skins/ProfessionalKnob.svelte ignores `accessibility` (0 references)
                  that KnobRenderer consumes
Consumers:        issuedStatusPresentation on bipolar: panels/FilterPanel.svelte (IF shift, PBT inner/outer);
                  'professional': built-in appearance selectable in component-kits.config.ts, plus ControlButtonDemo
Written / read:   Skin.bipolar: 1 type; built-in appearance 'professional': 1 [O]
Guards checked:   public API: yes (COMPONENT_KIT_API_VERSION = 1): changing Skin is a kit-API change
Collateral:       component-kit-api/verify-package.mjs fixtures (not opened)
Divergence:       [I] with a kit bipolar appearance, FilterPanel.passbandIssuedStatus.accept is never called, so the
                  caller-formatted announcement is lost (the seat's generic view remains); with 'professional', the
                  disabled reason and status value text are absent from the knob's accessibility
Prior ruling:     look-preservation ruling (keep ProfessionalKnob); COMPONENT_KIT_API_VERSION = 1
In-flight:        none found
Required surface: an issuedStatusPresentation field on the bipolar appearance props; ProfessionalKnob honouring
                  `accessibility`
Depends on:       none
Confidence:       high (source)
Falsifier:        a kit convention that forbids bipolar overrides
Fix class:        design (small)
Actionable:       low; no shipped path
```

### F9 — Design-language resolution is a non-reactive DOM read
```
Verdict:          undetermined
Rank:             parallel
Elements:         semantic/design-language-renderers.ts: activeDesignLanguage (reads
                  document.documentElement.dataset.designLanguage), renderSlot; consumers RxTxSurface.stateFeedback,
                  MetersSurface (meters slot), VfoSurface (frequencyDisplay slot)
Consumers:        as listed
Written / read:   activeDesignLanguage: 1 definition, 1 production caller (renderSlot); renderSlot: 3 callers [O]
Guards checked:   not a deletion
Collateral:       none
Divergence:       [I] App.svelte's $effect writes the attribute, and no {#key} on the language exists in App,
                  RadioLayout, SemanticRadioSurfaces or skins [O grep]; so after a studioline↔fieldline switch on
                  desktop-v2 (the only multi-language skin: presentation/workspace/contract.ts
                  WORKSPACE_SKIN_DESIGN_LANGUAGES) the previous language's output persists until an input changes.
                  Impact low: only segmentline.css selects data-dl-* (ADR addendum), and meters change every reading.
Prior ruling:     MOR-1278 activation doctrine (the attribute is the activation)
In-flight:        none
Required surface: a reactive language input
Depends on:       none
Confidence:       medium
Falsifier:        a remount or reactive read I did not find; a live language-switch test
Fix class:        design
Actionable:       low
```

## Per-family verdicts

| Family | Verdict | Evidence (file: symbol) |
|---|---|---|
| Continuous scalars: ValueControl, HBar, Bipolar, Discrete, Knob, Professional | **Finding** (F2, F8); the engine is conformant | `continuous-scalar.svelte.ts: createContinuousScalar` shared by legacy and semantic hosts. `TxAuxScalarHost`/`DspScalarHost`/`CwKeyerInstrumentHost` keep `data-canonical-value` and `data-command-status` outside the renderer, so a form change is safe. `ValueControl.svelte: skinComponent` is the gap |
| Filter width | Conformant | `semantic/FilterSurface.svelte: filterWidthScalar` (command feedback); `panels/FilterPanel.svelte: filterWidthBinding` (bound, feedback-integrated) |
| PBT / IF shift | **Finding** (F1, PBT reset); IF shift conformant | Both hosts are bound with feedback. PBT reset: Standard's single atomic `onPbtReset` (MOR-2535) vs `FilterPanel`'s per-lane track reset through `passbandPolicy`. IF shift resets to 0 on both |
| RIT/XIT | **Finding** (F1) | `RitXitScanSurface: offsetPolicy` vs `RitXitPanel` raw ValueControl |
| RF/SQL pair | Conformant, with the prior F6 follow-up | `continuous-pair.svelte.ts: createContinuousPair` in both `RfFrontEnd.svelte` and `RfFrontEndInstrumentHost.svelte`; one `DualParamRenderer` |
| Finite choices and toggles (mode, filter, AGC, antenna, band, scan, NB/NR, notch, filter shape, break-in) | **Finding** (F3, F4, F7) | seats and behaviors used only in semantic/; `DspInstrumentHost: compactDspButton`; `FilterInstrumentHost: standardMode`; `CwKeyerSurface` APF. Break-in toggle keys under `standard` are an explicit policy (cleared) |
| Actions: PTT/TX keys | Conformant | `getManagedAppTxController` is the only keying path (6 consumers); `managed-tx-gesture.ts: createManagedTxGesture` / `createManagedMobilePttSurface`; PttFab guards are geometry |
| Actions: tune | Conformant | `lcd/VfoControlPanel.svelte: requestAtuTune` reuses `semantic/rx-tx-surface.ts: keyBlockedReasons` |
| Actions: VFO operations | **Finding** (F4c; D2, D4) | `VfoControlPanel` hard-coded labels vs `vfo-ops-utils.ts: vfoSwapLabel` and `semantic/vfo-operation-projection.ts` |
| Meters: S-meter/LinearSMeter, BarGauge, TX meters | Conformant on the semantic path; legacy dock dead (D4/D5) | One projection path: `meter-renderer-view.ts: toSignalMeterRendererView/toLevelMeterRendererView`, `bar-meter-projector.ts`, `MeterRendererSeat.svelte`. Not reached: mobile `DockMeterPanel` and LCD `AmberCockpit`/`AmberScope`, left for budget; kit signal-view parity for SWR-in-tile is not verified |
| Frequency display and tuning | **Finding** (F5); partially reached | `FrequencyRendererSeat` and `FrequencyDisplayInteractive` adopted by the semantic path. `VfoSurface`'s appearance matrix (`'semantic'`, `'sdr'`, `'standard'` branches) was not adjudicated in full, beyond the dash still present from the prior audit |
| Scope/panorama controls | Conformant for the gate; partially reached | One gate: `usable` plus `semantic/accepted-scope-values.ts` (SpectrumToolbar, ScopeSettingsPopover). Mobile renders `instruments.scopeControls`. LCD's legacy SpectrumToolbar feedback parity was not reached |
| Standard composition (v2.11.1 grid) | **Finding** (D4/D5, F3, F7) | RadioLayout's live branch consumes only `InstrumentComposition` handles. Its raw `runtime.state` reads outside dead arms are only `activeMode` (`applyModeDefault`). The `standard` and `appearance` props are explicit (C) |

## Blocks MOR-2215 closure?

The first two are on the Standard path and are unconditional:

1. **F7:** Standard's compact DSP keys (NB/NR/NOTCH/A-NOTCH) and its APF key claim "not pressed" over unread readings. Standard's mode keys expose no confirmed selection to assistive technology. The non-Standard renderings of the same hosts are honest.
2. **F3:** Standard shows no pending target for mode, AGC, attenuator and the compact NB/NR/NOTCH keys. The LCD and mobile appearances show it for mode, AGC, attenuator and notch.

The rest block closure unless the owner has scoped the mobile/LCD legacy hosts out of MOR-2215. I found no such ruling in the repository, and I did not read Linear.

3. **F1:** on mobile and the five LCD skins, a track double-click sends a RIT/XIT reset to the origin, which Standard refuses (MOR-2727). It also sends a per-lane PBT reset to 0 Hz, where Standard sends one atomic, gated reset (MOR-2535). Arrows are debounced by 50 ms there as well.
4. **F2:**
   - On mobile/LCD, RF power (×2), AF level (×2), NR level and NB depth bind readings only, with no requested/confirmed/error feedback.
   - On sdr-test, four DSP fields use native inputs that dispatch on every input event.
5. **F4:** legacy finite keys on mobile/LCD:
   - expose no pressed state to assistive technology;
   - stay enabled over unread readings;
   - draw scheme-blind A↔B/A=B.
6. **F5:** the mobile frequency readout is confirmed-only, so the tune target in flight is dropped.

Follow-ups (do not block): D1–D10, F6, F8, F9.

## Weakest link

**F3.** My claim that Standard shows no pending target for mode, AGC and attenuator rests on literal searches:
- the semantic hosts' seat inputs have no `requested` for these fields;
- `set_mode`/`set_agc`/`set_attenuator` appear 0 times in `semantic/` and the wiring.

If another Standard channel presents that target, F3 shrinks to the compact-key subset. Possible channels: a VFO chip, the status bar, or a view-model display state under other vocabulary. Check that first, with a grep for `getModeArmed`/`getAgcArmed`/`getAttenuatorArmed` and pending-mode vocabulary across `frontend/src`, and a slow-radio MODE click on the Standard face.

## Cleared (examined and healthy)

- **Continuous and pair engines:** one each (`createContinuousScalar`, `createContinuousPair`, committed-scalar), consumed by both host families.
- **Semantic scalar hosts** (TxAux, DSP, CW): confirmed value and command status live outside the renderer, so hbar↔knob does not lose information.
- **MOR-2704 consolidations landed:**
  - one `usable` (`@internal`; the kit verifier asserts its absence);
  - `acceptedNumber`/`acceptedBoolean` in `semantic/accepted-scope-values.ts`;
  - `activeTuneReceiver` in `dual-receiver-strips.ts`;
  - `DspInstrumentHost`'s keyed wrapper is not a copy.
- **09-06 finite-choice F1 gap closed:** `bindAbsoluteChoiceInstrument` exists, with 2 consumers.
- **Finite seats:** they hand kit and built-in renderers the same view (available, confirmed/selected, reading, requested, feedback).
- **Meter kit seat:** projects from the same frame as the built-in fallback.
- **Armed vs pending accessors:** one mechanism. `armedFact` wraps `latestPendingParam`, as its own doc says.
- **TX keying:** one managed controller; gesture guards are geometry.
- **Explicit `standard` behavior policies:** CwKeyerSurface break-in toggle keys and the `standard` Rx/Tx labels are explicit props, not hidden dependencies.
- **ProfessionalKnob:** retained per the look-preservation ruling.
- **Semantic → components-v2 imports:** the renderer imports are sanctioned by the Standard-reuse clause and eslint. The radio helpers are single definitions shared by both families (meter-utils `normalizePower`, `agc-utils`, `dsp-panel-logic`, `smeter-scale: projectSignalMeter`).
- **Prior deletions closed:** unread-readouts D1–D5; mor2688-design D1 and D3; 09-26 frontend D1.
- **Test-support modules:** `semantic/internal-identifier-rule.ts` (the placeholder guard's rule, used by the Playwright guard) and `presentation/languages/state-vocabulary.ts` (MOR-2036 fork pins) are test infrastructure by design, not dead code.
