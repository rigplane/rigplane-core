<!-- Archived verbatim from a read-only mechanism-audit run. Audited revision: f9b599e90249e19bfb8234f1722f8d9effa9029e (main after #3750). Method: .claude/skills/mechanism-audit/SKILL.md at that revision. Scope: commits f048e1f9 (#3744, MOR-2651) and 33a46b78 (#3741, MOR-2658). The auditor was from a different model family than the commits' author. -->

# Mechanism audit — Tract B (unread readouts), revision f9b599e90249e19bfb8234f1722f8d9effa9029e

Method file read: `.claude/skills/mechanism-audit/SKILL.md` (this worktree). Read-only; no edits, no git writes, no test runs, no Linear/GitHub writes. Commits in scope: f048e1f9 (MOR-2651, PR #3744), 33a46b78 (MOR-2658, PR #3741). Prior rulings consulted: `CLAUDE.md` Layer boundaries, `.importlinter`, `.claude/audits/README.md` + the 2026-09-26 frontend/backend reports + 2026-09-15 control-conversion report; Linear MOR-2470, MOR-2478, MOR-2520, MOR-2651, MOR-2658 (read-only).

## Step 0–3 summary (evidence base)

Definition sites located for every symbol cited (greps: `Number.isFinite(`, `: ''`, `UNKNOWN_TEXT`, `textOf`, `formatValue`, `min-width.*ch`, `:empty::before`, `swrRatio`, `getRedlineRaw`, `getScaleMarks`, `extractMeterState`, `toTxProps(`, `micGain|driveGain`). Consumer sets counted per element across `frontend/src` and `frontend/tests`, production and tests separated.

Prior rulings that bind: owner rule MOR-2520 (2026-09-21): no placeholders on the operator's screen — unread is blank/unlit, never a dash or invented value. MOR-2651 (2026-09-26) extends it and orders the whole-page guard. Each slice's task text said "Do not add a shared helper or a new abstraction in this PR" — a per-slice size constraint, not a ruling on the end state (observation). The owner's global rule of three governs when the wave ends.

## Steelman (step 4)

The strongest case for the current arrangement: (a) every guard added by these two commits is a one-line wrapper around a *shared* formatter that already exists (`normalizedPercentDisplay`, `rawToPercentDisplay`, `formatPower`, `formatSValue`), so the duplicated token count is one ternary per readout, not a duplicated formatter; (b) each readout genuinely differs in units, precision, and widest-text width, so the *formatted* half is legitimately local; (c) the `:empty::before` strut and `unknownDisplay` default already live in the shared value-control renderers, so ValueControl-based readouts already get the shared unread treatment; (d) the ticket chain explicitly forbade per-slice abstraction, and the census is mid-flight (MOR-2651's final guard slice is unlanded), so consolidating mid-wave would churn the same files twice. This steelman holds for the guard-shape duplication and mostly holds for the CSS convention; it does **not** hold for the `''` vs `'---'` divergence (F1) or the fabricated `MON 50%` / `PROC 0` paths (F3), which the live rule already forbids.

---

## Deletions (ranked first)

### D1 — `extractMeterState`: dead
Verdict:          dead
Elements:         `frontend/src/components-v2/layout/layout-utils.ts: extractMeterState` (with its `?? 0` chain, lines 37–44)
Consumers:        tests only — `RadioLayout.isolated.test.ts:196` imports it; two describe blocks (lines 282, 354) exercise it. The mobile meter reads `toMeterProps` (NaN-honest), as the #3741 body reported (observation).
Written / read:   1 definition + 13 test hits, 0 production hits (`grep -rn extractMeterState frontend/src frontend/tests`).
Guards checked:   literal grep only (no dynamic access exists in this TypeScript app); out-of-repo: `layout-utils` is internal, not exported by `component-kit-api`; public API: no; tests-only: yes, flagged.
Collateral:       the two `describe` blocks assert nothing real once the code is dead and must go with it — a human decision per the method.
Depends on:       none.
Confidence:       high.
Falsifier:        a production import of `extractMeterState` — none found.
Fix class:        delete. (The module's `extractVfoState`/`hasLiveAudioFromState` keep production consumers; only `extractMeterState` is dead.)

### D2 — `toTxProps` outputs `micGain` / `driveGain`: dead fields with fabricated defaults
Verdict:          dead (production), tests-only consumers
Elements:         `panel-props.ts: toTxProps` — `micGain: state?.micGain ?? 128`, `driveGain: state?.driveGain ?? 128` (pinned in `stillPresentOutOfScope`, `no-fabricated-defaults.test.ts:165–166`) plus the `TxPanelProps` fields.
Consumers:        TxPanel never reads `p.micGain`/`p.driveGain` (only availability flags/feedback bindings); MobileRadioLayout reads only `tx.rfPower*`/`atu*`/band fields; AmberCockpit/AmberScope read only `tx.voxActive/compActive/compLevel/compLevelAvailable`. Tests: `panel-props.test.ts:89,95,167` pin the values. RadioLayout's mic/drive seats render from the semantic view-model (`instruments.txAuxScalars`), not `toTxProps`.
Guards checked:   dynamic access none (literal field reads); internal; not public; tests-only: yes, flagged.
Collateral:       the two `stillPresentOutOfScope` rows and the pinning tests go together.
Depends on:       none.
Confidence:       high.
Falsifier:        any production render of `toTxProps().micGain`/`.driveGain`.
Fix class:        delete (also dissolves two of F3's adjacent fabricated defaults).

### D3 — `MobileRadioLayout.svelte: rfFrontEnd` derived: dead
Verdict:          dead
Elements:         `MobileRadioLayout.svelte:94` — `let rfFrontEnd = $derived(toRfFrontEndProps(radioState, caps));`
Consumers:        none — the file's only other `rfFrontEnd` hit is `surfaceHandlers.rfFrontEnd` (:117), a different symbol. 1 write, 0 reads.
Guards checked:   literal; internal; not public; no tests assert it.
Collateral:       none.
Depends on:       none.
Confidence:       high.
Falsifier:        a template reference — full-file grep shows none.
Fix class:        delete. (After this, `panel-adapters.ts:107` is `toRfFrontEndProps`' one production caller.)

### D4 — `smeter-scale.ts: getRedlineRaw`, `getScaleMarks`: dead exports
Verdict:          dead
Elements:         `smeter-scale.ts: getRedlineRaw` (:128), `getScaleMarks` (:260)
Consumers:        `getRedlineRaw` — 0 hits anywhere (src + tests). `getScaleMarks` — 0 calls; one comment mention (`meter-contract.test.ts:64`).
Guards checked:   dynamic access none; out-of-repo — `smeter-scale` is not imported by `component-kit-api` (grep); public API no; tests-only no.
Collateral:       the `meter-contract.test.ts` comment sentence.
Depends on:       none.
Confidence:       high for `getRedlineRaw`; medium for `getScaleMarks`.
Falsifier:        a kit or e2e consumer of either symbol.
Fix class:        delete. Pre-existing dead (exposed by this sweep, not introduced by the commits).

### D5 — residual non-dB engineering branch in `projectSignalMeter`: dead in production
Verdict:          dead (unreachable branch), one deletion guard partially open
Elements:         `smeter-scale.ts: projectSignalMeter`, `scaleMode === 'none'` non-dB arm — `stateText = 'unit unknown'` (:521), `valueText = String(value)` arm
Consumers:        reachable only with `{kind:'engineering', unit≠'db'}` for the signal meter. The model validator forbids it (`radio-view-model.ts: validateMeterValueDomain` forces `signal → 'db'`); every live producer passes the validated model or `{kind:'unknown'}` (which now enters the unread branch at :474). Direct callers: `ReceiverInstrumentHost.svelte:246`, `StationMeterInstrumentHost.svelte:255` (model domains), `meter-renderer-view.ts:62` (explicit `'unknown'`), `LinearSMeter.svelte:132` (no domain). No test pins the non-dB wording (`grep "unit unknown"` → only the definition).
Guards checked:   the "guard returning a constant" is the validator upstream; dynamic access none; **public API**: `projectSignalMeter` is exported from a shared module and could be called by a kit consumer with a hand-built domain — the kit does not import it today, but this is the one guard I could not fully close. Out-of-repo consumers: unknown.
Collateral:       none.
Depends on:       none.
Confidence:       medium.
Falsifier:        a shipped profile/adapter producing a non-dB engineering domain for the signal meter, or a kit consumer passing one — none found.
Fix class:        delete the non-dB half of the `'none'` branch (keep the `engineeringDb` `'scale unavailable'` arm); per the repo's "delete before narrow" ruling, deletion is the default.

---

## Consolidations (ranked by debugging cost)

### F1 — unread-readout text ("unread → empty, else formatted"): diverged duplicates inside one convention
Verdict:          B (gap: no shared unread-display surface) with a rank-1 diverged subset
Rank:             diverged (the `''` vs `'---'`/`'—'` split), then parallel
Elements:         guard-style definition sites (production): `TxPanel.svelte: rawTxLevelDisplay` (:79), `rfPowerDisplay` (:86); `MobileRadioLayout.svelte: formatRfPowerDisplay` (:527), inline TX-power (:804) and SWR (:811) guards, **and the pre-existing `'---'` family `formatSValueDisplay` (:513), `formatDbmDisplay` (:516), `formatOffsetDisplay` (:521)**; `CwPanel.svelte: formatCwPitchDisplay` (:170), `formatKeySpeedDisplay` (:173); `RfFrontEnd.svelte: displayRfGain` (:127); `AmberCockpit.svelte: ritOffsetLabel` guard (:111, `'---'`); `SpectrumToolbar.svelte` inline ternaries (:523, :549, :608). Semantic family: `FilterInstrumentHost.svelte: textOf` (:54), `TxAuxScalarHost.svelte: formatValue` (:62), `CwKeyerInstrumentHost.svelte: formatValue` (:73), `UNKNOWN_TEXT` in `MemorySurface` (''), `AntennaInstrumentHost` (''), **`band-instruments.ts:13` ('—')**, fieldline/studioline frequency-renderers (''). Renderer family: the five value-control renderers' `unknownDisplay ?? (displayFn ? displayFn(NaN) : '')` unread default.
Consumers:        every site is live — each is its panel's only unread path (per-element grep counts recorded in the run).
Definition site:  no shared "unread display" primitive exists; the shared layers hold only the shared *formatters* and the renderers' `unknownDisplay` prop.
Divergence:       **observable and operator-visible**: the same input (an unread value) renders `''` on the readouts these commits fixed and `'---'` / `'--- dBm'` / `'—'` on the mobile S-value/dBm/RIT/XIT readouts (`MobileRadioLayout.svelte:513–521`, rendered at :579–580, :690, :694), the AmberCockpit RIT label (:111), and `band-instruments.ts` `UNKNOWN_TEXT = '—'` (consumed by BandSurface/BandInstrumentHost). The `'---'` family's own comment ("Placeholders follow the established '---' segment convention", :511) records the superseded convention.
Prior ruling:     MOR-2520 (owner, 2026-09-21): "no null values or anything like that, or dashes, or question marks… nothing that is not in the experience of a real radio"; MOR-2651 reaffirms 2026-09-26. The `---` sites predate these commits but sit in a file this tract changed and contradict the live rule.
In-flight:        the fixed halves converge on `''`; the mobile/`band-instruments` halves are unfixed census leftovers (MOR-2651's census lists only mobile TX power/SWR for this file).
Required surface: one shared unread-display decision — e.g. an exported `finiteOrEmpty(fmt)` combinator in a shared frontend lib — or a single ruling that `''` is the sentinel plus the MOR-2651 whole-page guard test (still unlanded) forbidding `'—'`/`'---'` fallbacks.
Depends on:       none.
Confidence:       high.
Falsifier:        an owner ruling that dashes are wanted on mobile/legacy surfaces — the opposite of MOR-2520/MOR-2651.
Fix class:        design (one surface) — but the diverged subset is rule application, not consolidation, and needs no design.
Actionable:       the `---` sites yes, now; the guard-combinator consolidation after the census's final guard slice (the wave already exceeds the rule of three, so the count no longer defers it — the mid-flight census does).

### F2 — RF-power percent display guard: one capability written twice, identical
Verdict:          C (per-slice locality) / rank parallel — not currently actionable
Rank:             parallel
Elements:         `TxPanel.svelte: rfPowerDisplay` (:86–87) and `MobileRadioLayout.svelte: formatRfPowerDisplay` (:527–528). Bodies byte-identical: `Number.isFinite(v) ? normalizedPercentDisplay(v) : ''` (observation). This answers adjudication 2: no divergence — one ternary written twice, not one capability answering differently.
Consumers:        TxPanel RF-Power ValueControl `displayFn`; mobile power-sheet slider `displayFn`. Both consume the shared `normalizedPercentDisplay`.
Divergence:       none observable.
Prior ruling:     per-slice no-helper instruction (size constraint); the owner's rule of three makes an extract-now move for two one-liners unwarranted outside F1's surface.
Required surface: covered by F1's combinator if adopted.
Depends on:       F1.
Confidence:       high.
Falsifier:        the two bodies diverging — they do not.
Fix class:        none now; fold into F1 later.

### F3 — fabricated defaults in `stillPresentOutOfScope`: two reach the screen
Verdict:          live findings (fabricated values), not duplication
Rank:             diverged (invented values on screen)
Per entry (adjudication 3):
- `monLevel ?? 128` — **reaches the screen**: `TxPanel.svelte:323` renders `MON {Math.round(monLevel/2.55)}%` when `monActive && monLevel > 0`; a rig reporting `monitorOn` without `monitorGain` renders a fabricated `MON 50%` (observation; the #3741 body itself called this "adjacent"). Finding, deferred A12 member.
- `compLevel ?? 0` — `TxPanel.svelte:318`'s `COMP …%` needs `compLevel > 0`, so `?? 0` prints no number there (cleared); but `AmberCockpit.svelte:178` and `AmberScope.svelte:144` render `PROC ${tx.compLevel}` whenever `compActive && compLevelAvailable` — `compressorOn` with no reported level renders `PROC 0` on the LCD faces (observation). Finding.
- `micGain ?? 128`, `driveGain ?? 128` — feed no rendered readout (D2); cleared as fabrications, dead as outputs.
- `voxActive ?? false`, `compActive ?? false` — conservative-off booleans feeding active flags only; cleared.
- `rfGain ?? 1.0`, `squelch ?? 0`, `att ?? 0`, `pre ?? 0` — `RfFrontEnd.svelte` never reads `p.rfGain`/`p.squelch`; `att`/`pre` feed `active={att > 0}` / selection state, where the default renders the conservative *off* direction, not a numeric readout; cleared with the note that an invented "off" is still a state claim under MOR-2520's strictest reading (inference, low priority).
Prior ruling:     MOR-1409 A12 owns the sentinel migration; `rfPower` was finished by this tract, the rest deferred.
Depends on:       D2 (delete micGain/driveGain first).
Confidence:       high for `monLevel`/`compLevel`; medium for the att/pre caveat.
Falsifier:        a wire-schema proof that `monitorOn`/`compressorOn` can never be reported without the level field.
Fix class:        extend the A12 sentinel migration (`?? Number.NaN` + consumer guards) to `monLevel`/`compLevel`; delete D2's pair.

### F4 — reserved-width slot CSS (`min-width: Nch` + `tabular-nums` + `:empty::before` strut): established convention, locally re-applied
Verdict:          already-shared where it matters; C for the per-panel slots
Rank:             parallel (maintenance cost only)
Elements:         the `:empty::before { content: '\200b' }` strut and `unknownDisplay` default live once each in the shared value-control renderers (`DiscreteRenderer.svelte:475`, `HBarRenderer.svelte:336`, `BipolarRenderer.svelte:335`, `DualParamRenderer.svelte:326`). The `min-width: Nch` reservation appears ~90 times in ~40 files (grep count), including the commits' additions (`.tx-level-slot` 4ch, `.cw-value-slot` 6ch, `.m-tx-power-value` 5ch, `.m-tx-swr-value` 8ch, `.ref-value`/`.receiver-value` 4ch).
Consumers:        all live; the widest-text sizing is deliberately per-readout (units differ), so the ch values cannot be shared.
Divergence:       one real one: the empty-inline-block baseline fix (`min-height: 1lh; align-self: center`) exists only in `CwPanel.svelte` because that is where the visual run caught it; other custom reserved slots (mobile, toolbar) share the same latent baseline risk (inference).
Prior ruling:     per-slice no-abstraction instruction; the convention predates this wave.
Required surface: a shared `.reserved-slot`-style primitive could absorb strut + tabular-nums + baseline handling; no slice was granted it.
Depends on:       none.
Confidence:       high on the facts; medium on the latent-baseline inference.
Fix class:        none now; record as a candidate shared class after the census lands.

### Adjudication 6 (known-with-unknown-unit path) — **cleared as reuse**: f048e1f9 widened the existing unread branch (`smeter-scale.ts:474` — `value === null || domain?.kind === 'unknown'`) and replaced `formatUnknownUnit` with `''` inside the six existing `meter-utils.ts` formatters; `formatUnknownUnit` was deleted, no parallel path added, and `bar-meter-projector.ts:197` now suppresses the `. formatted` fragment when the formatter returns `''`. One mechanism, correctly located in the shared meter modules.

---

## Weakest link

D5 (the residual non-dB `'unit unknown'` branch). The reachability argument rests on the model validator being the only production producer of S-meter domains plus my grep-based conclusion that no kit/e2e consumer hand-builds a domain. If a component-kit consumer or a future adapter passes `{kind:'engineering', unit:'w'}` to `projectSignalMeter`, the branch is live and D5 collapses from "dead" to "diverged wording vs the unread treatment". Check first: `frontend/component-kit-api/verify-package.mjs` fixtures and any out-of-repo kit consumers before deleting.

## Cleared (examined, healthy)

- `meter-utils.ts` formatters/levels/scales: all 30 exported symbols have live consumers except `swrRatio` (production-consumed internally by `isSwrFault`; over-exported, not dead) — swept by per-symbol count.
- `bar-meter-projector.ts: projectLevelMeter/projectBarMeters`: one projection path; the `''` reuse is correct (adjudication 6).
- `projectSignalMeter`'s unread branch and `LinearSMeter.svelte`: one S-meter mechanism, no parallel projector; #3744 reused, not added.
- `TxPanel`/`CwPanel`/`RfFrontEnd`/`SpectrumToolbar` local formatters beyond the guard ternaries: legitimately local (units/precision differ); no unused local functions found in the in-file sweep of all five touched Svelte modules.
- `toTxProps().rfPower` consumer set: all three consumers (TxPanel guard, mobile readout, mobile slider) guard NaN; AmberCockpit/AmberScope never read `.rfPower` (verified).
- `extractVfoState`/`hasLiveAudioFromState`/`VfoStateProps`: live (layout wiring, keyboard-map, type consumers).
- `PEAK_DECAY_MS`/`updatePeakHold`/`peakHoldDisplay`: shared, multiple live consumers, single definition (a prior consolidation holding).
