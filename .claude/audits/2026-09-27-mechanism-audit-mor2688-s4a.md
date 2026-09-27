# Mechanism audit — PR #3776, MOR-2688 slice S4a ("one value-or-nothing rule and its entry points")

- **Revision:** `88430fe2` (`88430fe2c4084ca478417cb013aa222c42444c74`), detached HEAD, clean tree. Merge base `234cf95d` (= `git merge-base HEAD origin/main`). [O]
- **Method:** `.claude/skills/mechanism-audit/SKILL.md`, read in full in a detached worktree at the audited revision. I followed steps 0–5 in order, with the 3a sweep and the step-4 steelman done before any verdict. Policy: `docs/internals/coordinator-policy.md` in the same worktree.
- **Read-only:** I used git and grep only, including `git grep <rev>` at both revisions, plus one census script (not tracked). I ran no tests, type checks, npm, gh, Linear or mini commands. The mutation outcomes in Q4 are reasoned, not run.
- **Scope:** 3 commits, 7 files, +183/−21.
  - Production files: `primitives/reading-text.ts`, `VfoIndicatorRow`, `ReceiverInstrumentHost`, `MetersSurface`, `ReceiverInstrumentCluster`.
  - Test files: `reading-text.test.ts`, `ReceiverInstrumentHost.isolated.test.ts`.
  - Seven files crosses the 6-file soft threshold. I did not read the PR body, so whether it justifies this is unknown. [O]
- **Coverage:** paths are relative to `frontend/src`.
  - Opened only in part: VfoSurface, FilterSurface, `radio-view-model.ts`, `radio-view-model-adapter.ts`, `bar-meter-projector.ts` (lines 20–235), `meter-renderer-view.ts` (94–150), and the host files outside the diff.
  - `VfoIndicatorRow.test.ts` and `MetersSurface.isolated.test.ts`: grep plus one test each.
  - Linear not read. MOR-2704 and MOR-2705 are known only from the dispatch and the S3 audit.
- **Text directing an agent:** none. `reading-text.ts` says "do not add a value import here"; I treated it as a maintainer rule, which the purity test backs.
- **Claims under test, taken from the dispatch as claims, not premises:**
  - H1: there is one mechanism.
  - H2: the five sites migrate with identical behaviour.
  - H3: no NaN/null entry point was added because no S4a site reads that vocabulary.
- **Strongest case against H1:** "one rule" is true of the module, not of the codebase. The observation entry point never reaches `valueText` in production, and drawing code still restates the observation rule (Q1, F1).

## Steps 0–3
- **Step 0 — definitions.** `valueText`, `readingValue`, `readingText`, `observationValue` and `ValueObservation` are each defined once, in `primitives/reading-text.ts`. [O] A second search, by behaviour rather than by name, found the same rule elsewhere [O]:
  - `RfFrontEndInstrumentHost.svelte: valueText` — same name, local, `value === null ? '' : rawToPercent(field, value)`, present at the merge base;
  - `VfoSurface.svelte: displayValue`;
  - `panel-adapters.ts: hasUsableObservation`;
  - `lcd-display-helpers.ts: stateText`, over `DisplayValue`, `state === 'known'`.
- **Step 1 — prior rulings.** [O]
  - Owner decision, 2026-09-27, quoted in the module docstring: drawing code must not decide known/unknown.
  - The design audit (`.claude/audits/2026-09-27-mechanism-audit-mor2688-design.md`, Q6) defines S4 as: "the three host `formatValue`s, plus the observation copies `VfoSurface: displayValue` only, `VfoIndicatorRow: rfGainShown`, `ReceiverInstrumentHost` and `MetersSurface`".
  - The same audit's steelman "wins for … the segmentline projection; the meters, which already pass value-or-null into the projector".
  - The S1 audit's F2 asks for "one core rule … `readingText` and the display and finite entry points would delegate to it and share one test table". It warns: "It becomes a fork if S4/S5 add entry points in other modules".
- **Step 2 — work in flight.** The S3 audit assigns to S4 the whole of `ReceiverInstrumentCluster.svelte`, five items in `VfoSurface: standardPanelSections` (frontToggle, dspToggle, notch, offsetChip, rfg), and `VfoIndicatorRow: rfGainShown`. [O]
- **Step 3 — liveness.** Literal `git grep -F "<name>("` over non-test `frontend/src`, excluding the module itself. Production call lines, base → head [O]:
  - `readingText`: 33 → 33;
  - `readingValue`: 0 → 3 (VfoIndicatorRow 1, Cluster 2);
  - `observationValue`: 0 → 3 (VfoIndicatorRow, ReceiverInstrumentHost, MetersSurface);
  - `valueText` (the imported one): 0 → 1 (the Cluster's BW text);
  - `ValueObservation`: no production reader outside the module; one test file.
- **Out-of-repo readers.** No hits in `component-kit-api`, and `local-extensions/` is absent. All five names are new in this PR, so nothing outside the repo can depend on them yet. [O/I]
- **Step 3a — sweep of `reading-text.ts`:** 4 functions, 1 type, 0 constants, 0 fields. Two parts have no production reader. [O]
  - `valueText`'s default `format = String`: all five calls pass a formatter (1 in the module, 1 in the Cluster, 3 in tests).
  - `observationValue`'s `observation === undefined` branch.
- **Touched files:** no import or variable lost its reader. `LevelMeterProjection.state` is still read by `MetersSurface`'s `data-meter-state={bar.state}`. [O]

## Q1 — One mechanism?
- **Inside the module: yes.** `valueText` is the only `''`-for-nothing expression. `readingText` is now `valueText(readingValue(source), format)`. The entry points return `null`, never text. [O]
- **The PR creates no other copy.** Among the added code lines, the only one containing `''` is `valueText`'s body; every other added `''` sits in docstrings or tests. [O]
- **Do both entry points reach the rule?**
  - `readingValue` does, in production: inside `readingText`, and in the Cluster's `BW {valueText(bwRaw, …)}`.
  - `observationValue` reaches it only in the test table. [O] Its three production callers use the value directly: `displayFrequency` hands a number to the digit readout, `rfGainText` uses it in a composite rule, and `swrLowerScale` uses it as a predicate.
- **For those sites, "nothing → `''`" is decided where it always was.** Both places are verdict C. [O/I]
  - `VfoIndicatorRow: rfGainText` (`shown !== null && levelFormatsBelowMax(…) ? … : ''`), under the owner ruling of 2026-09-23 that RFG shows only while RF gain is reduced.
  - `bar-meter-projector.ts: projectLevelMeter` (`formatted = isObserved ? … : ''`), cleared by the design-audit steelman for meters.
- **Outside the module the rule is still restated in drawing code (F1).** Its adapter-layer twin, `hasUsableObservation`, is verdict C: it is a type predicate that narrows `.value`, and `FORBIDDEN_PRIMITIVES_IMPORTS` bans primitives from importing adapters. [O/I]
- **Verdict on H1:** true for the module and for everything this PR added; not true of the codebase.

## Q2 — Behaviour identity
| Site | read | unread | stale | `0` | absent group | Result |
|---|---|---|---|---|---|---|
| `VfoIndicatorRow: rfGainShown` | value | null | value | 0 (`??` keeps it) | no `display` → `readingValue(field)`, as before | identical [I] |
| `ReceiverInstrumentHost: displayFrequency` | value | null | value | 0 | `record` null → null; no `display` → `record.frequencyHz`, as before | identical [I] |
| `MetersSurface: swrLowerScale` | digits | `''` | digits | digits | no SWR group → not called | identical [I] |
| `ReceiverInstrumentCluster: meterValue` | value | null | n/a | 0 | no indicator → null in both | identical [I] |
| `ReceiverInstrumentCluster: bwRaw` + BW text | `BW 2.4k` | `BW ` | n/a | `BW 0` | no indicator → `BW ` | identical [I] |

`false` does not apply: all five fields are numeric. [O] Edge cases:

1. **`if (field.display)` versus `!== undefined`.** They differ only for `display: null`, which would now throw inside `observationValue`. The type forbids that value, and a literal search finds 0 `display: null` in `frontend/src`. [O]
2. **`?? null`.** It changes the result only for a current or stale observation that has no value. The producers rule that out: `radio-view-model.ts: num` requires a finite number, and `display-observation.ts: qualifyEvidence` returns current or stale only for finite numbers, booleans and non-empty strings. [O]
3. **MetersSurface now reads `projection.evidence` instead of `projection.state`.** The two agree by construction [I, read not run]:
   - `StationMeterInstrumentHost` calls `projectSwrMeter`, which calls `projectLevelMeter(…, txObserved=true)`, which calls `projectTxMeterPresentation`. That last function sets `state` and `evidence.state` from the same branch.
   - `RenderLevelProjection` omits only `source`, so `evidence` reaches the frame.
   - `toLevelMeterRendererView` builds the public view, a different object; `swrLowerScale` does not receive it.
   - `MetersSurface.isolated.test.ts: "keeps the SWR lower-row digits identical between current and stale on a non-ratio domain"` drives this path through the real projector. [O]

**Wider predicates (operational, isFinite, usable).** None was folded into an entry point, and none was changed: the only diff lines that match `operational|isFinite|usable` are docstring lines. [O] Left in place in the touched files:
- `MetersSurface: observed` (operational && known) feeds the S-meter tile's `reading` const, together with `Number.isFinite`. The operational part belongs to MOR-2704; the finite part to S4b/S5. Its `data-observed` use is an R2 attribute and stays.
- `ReceiverInstrumentHost: meterMotionInput` (operational && known && finite): MOR-2704.
- `VfoIndicatorRow`'s S-meter check `{#if … known && Number.isFinite(…)}`: not MOR-2704, because it has no operational gate; it belongs to S4b/S5.
- `VfoIndicatorRow: rfGainText`, `levelFormatsBelowMax`: an owner-ruled presentation rule, not MOR-2704.
- `ReceiverInstrumentCluster`: none.

## Q3 — Structural typing
- **Two real types reach `observationValue` in production.** [O]
  - `DisplayObservation<number>`, from `rfGainShown` and `displayFrequency`.
  - `bar-meter-projector.ts: LevelMeterEvidence`, from `swrLowerScale`. The docstring names only the first.
- **States.** [O]
  - `DisplayObservation`: current and stale carry `value`; unknown carries `reason`; unsupported carries nothing.
  - `LevelMeterEvidence`: current and stale carry `value`; unsupported, idle and unknown carry nothing.
  - In both, only current and stale carry a value, and `observationValue` maps exactly those two to the value. No state is treated as a value, and no value-carrying state is dropped.
- **Assignability.** Both types fit `ValueObservation<T>` (`state: string; value?: T`) structurally. [I; tsc not run]
- **The structural type is wider than both.** `state` is any string, and `value` is optional on every state. Three consequences:
  1. The compiler cannot see drift between the literals inside `observationValue` and the real unions. A renamed state, or an added state that carries a value, would compile and silently become nothing: a read value would go dark. [I]
  2. Foreign vocabularies fit. `radio-display-model.ts: DisplayValue<T>` (`state: 'known'`, with a value) compiles as a `ValueObservation` and would yield null for every known value. `InstrumentReading` and `TxAuxReading` do not fit, because they use `status`. No current caller passes a `DisplayValue`. [I]
  3. Drift is caught at two of the three sites only. The ReceiverInstrumentHost and MetersSurface isolated tests feed real producer output; the unit table builds `ValueObservation` literals directly. [O/I]
- **The layering premise holds.** `eslint.config.js: FORBIDDEN_PRIMITIVES_IMPORTS` bans `**/semantic/**`, and that block has no `allowTypeImports`. [O]

## Q4 — The shared table
The table has 8 rows. For each row, three `it` blocks drive `valueText`, `readingValue`/`readingText` and `observationValue`. The observation block also asserts that `undefined`, `{state:'unsupported'}` and `{state:'unknown'}` give null. [O]

Mutation outcomes, reasoned but not run [I]:

| Mutation | Table |
|---|---|
| `valueText`: `!value`; drop the `null` or the `undefined` test; always format; always `''` | red |
| `valueText`: change the default `String` | green — nothing uses the default (D1) |
| `readingValue`: drop the undefined guard; return the value on unknown; always null | red |
| `readingText`: ignore `format` | red |
| `observationValue`: drop current; drop stale; `\|\|` for `??`; drop the undefined guard | red |
| `observationValue`: ignore `state` (`observation?.value ?? null`), or accept unknown, unsupported or idle as value states | **green** |

- **Why the last mutation survives.** No row, and none of the three extra assertions, supplies a `value` together with a non-value state. So the header's "the vocabulary decides, never the value" is pinned for `readingValue`, where the type enforces it, but not for `observationValue`. The real types make the widening harmless today, so this is a test gap, not a behaviour defect. [I]
- **The new comment in `ReceiverInstrumentHost.isolated.test.ts` overstates the test.** It says "Red under a mutation of `observationValue` that drops current or stale". The test holds only a stale case and a not-observed case, so dropping `current` leaves it green. [O] Other tests in that file mount current observations and would probably catch that mutation. [I]

## Q5 — Prose and dead names
- **Call-site lists (module docstring, one per entry point).** They match the step-3 grep at HEAD. [O]
  - Nothing checks them. Only three tests reference `reading-text`: the unit test, and the closure allow-lists in `CwKeyerSurface.test.ts` and `RxAudioSurface.test.ts`, which pin only their own imports. [O]
  - S4b's first new caller will make them false with every test green. [I]
- **"every drawing site that reads it (…)" is false**, in two ways. [O]
  - It files `FilterSurface: numberOf` under NaN/null. That function reads `display.state` and `reading.status` and substitutes a numeric fallback (50, 9999, 0, or a limit); the design audit lists it among the display-state copies.
  - It omits other NaN/null readers: `RfFrontEndInstrumentHost: valueText`, `ReceiverInstrumentHost: meterMotionInput` and `VfoIndicatorRow`'s S-meter check.
- **"It lands in S4b with its first callers"** states a future change. CLAUDE.md puts such claims in the ticket, not the code.
- **"only this module knows how a vocabulary maps to nothing" is false at HEAD.** 133 status lines and 28 current-state lines remain outside the module (Q6), and `lcd-display-helpers.ts: stateText` does the same for `DisplayValue`. [O]
- **The `ValueObservation` docstring.** [O]
  - It says "the shape is restated here", but the shape is generalized: `state: string`, optional `value`.
  - It lists `idle`, which exists only in `LevelMeterEvidence`, a type the docstring never names.
- **The "Q7, R1" citation resolves.** The file has the heading "## Q7: Risks and unknowns" and the bullet "**R1, predicate drift.**". [O] The docstring's paraphrase, "pins it: widening … changes behaviour", simplifies R1. R1 says status alone *equals* `usable` for adapter-built fields and diverges only after a surface re-projects a field. "Pins" also overstates: an audit recommends, a test pins. Low severity.
- **Dead names:** D1 and D2. None of this PR's own comments describes code that is gone.
- **The Cluster's older comment is still false.** "same shape as the segmentline wave's `stateText`, reused rather than duplicated" sits directly under this PR's new comment, but `obsText` never calls `stateText` (S3 audit Q5; D4). [O]
- **The PR's other comments are true at HEAD.** This covers VfoIndicatorRow, ReceiverInstrumentHost and the Cluster; for example, `bandwidthCh` does read `bwRaw`. [O] MetersSurface's "`projection.state` mirrors exactly that" is true by construction (Q2 edge 3), but nothing pins it. [I]

## Q6 — Census
**Counting rule:**
- Command: `git grep -nE <regex> <rev> -- frontend/src`.
- Excluded: `*__tests__*`, `*.test.ts`, `*.spec.ts`, `*.json` and `primitives/reading-text.ts`.
- Unit: single-line literal matches, counted as lines. Multi-line forms are missed (for example `RfFrontEndInstrumentHost: valueText`).
- V1 also counts behaviour interlocks and R2 attributes, which the owner decision keeps.

| Vocabulary | Regex | 234cf95d | 88430fe2 | Removed by S4a |
|---|---|---|---|---|
| V1 `{reading}` status | `reading\.status *[!=]== *'(known\|unknown)'` | 136 / 43 files | 133 / 43 | VfoIndicatorRow 1, Cluster 2 |
| V2 observation | `\.state *=== *'current'` | 31 / 16 | 28 / 13 | VfoIndicatorRow, ReceiverInstrumentHost, MetersSurface |
| V3a finite → `''` | `Number\.isFinite\(.*\?.*: *''` | 16 / 9 | 16 / 9 | — |
| V3b null-first → `''` | `(=== *null\|== *null\|=== *undefined) *\? *''` | 30 / 19 | 29 / 18 | Cluster BW text |
| MOR-2705 placeholders | `'---` or `'—'` | 20 | 20 | — |

The V2 base count equals the S3 audit's own count (31 lines in 16 files). [O]

**What remains, by owner:**
- **S4b, observation rule:**
  - `VfoSurface: displayValue`. The design audit put it in S4; S4a did not take it, and the docstring does not mention it.
  - `ReceiverInstrumentCluster: obsText`, `obsState` (which feeds `data-state`, an R2 attribute), `frequencyText`, `frequencyCh`.
  - `meter-renderer-view.ts: levelEvidence` and `toLevelMeterRendererView`, 3 lines. `levelEvidence` also requires a finite value.
- **S4b, `{reading}` value-or-null:** 6 lines in `VfoSurface: standardPanelSections` (frontToggle, rfg, dspToggle, notch, offsetChip ×2), and `ReceiverInstrumentCluster: bandwidthCh` (`filterWidthMax`).
- **S4b, NaN/null:** the `formatValue` functions in CwKeyerInstrumentHost, DspScalarHost and TxAuxScalarHost; `RfFrontEndInstrumentHost: valueText`; and `VfoIndicatorRow`'s S-meter check.
- **S5:** the 16 V3a lines. MobileRadioLayout has 6, TxPanel 2 and CwPanel 2; AmberCockpit, RxAudioPanel, RitXitPanel, EssentialsPanel, FilterSurface and RitXitScanSurface have 1 each.
- **MOR-2704:**
  - the S-meter tile `reading` in `MetersSurface`;
  - `ReceiverInstrumentHost: meterMotionInput`;
  - band (`operational && known`) and `sValue` in `VfoSurface`;
  - `FilterSurface: pbtUsable`;
  - `bar-meter-projector.ts: observed`;
  - ScopeControlsSurface span/speed, 4 places (S3 audit).
- **MOR-2705:** unchanged (20 lines); the S3 audit's list stands.
- **Off the drawing path (verdict C or a prior ruling):**
  - adapters: `panel-adapters.ts` ×2, `radio-view-model-adapter.ts` ×2, `scope-passband-display.ts` ×2;
  - `radio-view-model.ts: validateDisplayObservation`;
  - the projector's `retained`;
  - `SemanticRadioSurfaces.svelte` ×3;
  - PBT: `FilterSurface` ×3 and `pbt-presentation-continuity.ts` ×2 (kept off by the design audit);
  - ARIA text in `lcd-display-helpers.ts`;
  - segmentline `stateText` (design steelman).
- **No owner:** `SpectrumPanel.svelte`'s passband guard. The design audit lists it as a copy, but no slice names it.
- **Disputed:** `FilterSurface: numberOf`. The docstring files it under NaN/null; the code and the design audit say display state.

## Steelman (step 4)
**The case for S4a as it stands:**
- It builds F2 in the shape the S1 audit asked for: one rule, entry points in one module, one table, and no entry point elsewhere that restates `''`.
- Returning `null` rather than text fits the callers. Two observation callers need a value (the digit readout and the owner-ruled RFG rule), and the third needs a predicate.
- `state: string` avoids hand-keeping a list of states across a boundary that lint forbids crossing. Integration tests catch drift at two of the three sites.
- Deferring the NaN/null entry point follows the "No orphans" rule.
- Leaving VfoSurface and the rest of the Cluster keeps the PR at 7 files.

**Where the steelman wins:** behaviour identity, one rule inside the module, the deferral, and the choice of sites.

**Where it loses:** the docstring promises more than the code (Q5), and the table does not pin `observationValue`'s state check (Q4). On typing it half-wins. A literal union of states would be compiler-checked at every call site, so it would not be a hand-kept list (F2).

## Deletions
### D1 — `reading-text.ts: valueText`, default `format = String`: dead
Verdict: dead · Elements: the default only. `readingText`'s own default stays live; e.g. `VfoIndicatorRow: sharedAggregate` calls `readingText(offset)` · Consumers: none · Written / read: 5 calls of the imported `valueText`, all passing a formatter (literal `git grep -F "valueText("`) · Guards: dynamic access none; out-of-repo impossible, since the symbol is new; public API absent from `component-kit-api`; tests do not rely on it · Collateral: none · Depends on: none · Confidence: high · Falsifier: a call with one argument · Fix class: delete, or keep once S4b gives it a caller

### D2 — `reading-text.ts: observationValue`, the `observation === undefined` branch: tests only
Verdict: undetermined, tests only · Consumers: `reading-text.test.ts` (the absent row, and `observationValue(undefined)` in every row) · Production: none of the 3 callers passes `undefined`; `rfGainShown` and `displayFrequency` test `display` first and fall back, and `evidence` is a required field · Guards: as D1 · Collateral: those assertions · Depends on: S4b deciding what "absent" means. Every present caller, and the S4b candidates (the Cluster's `obsText`, `VfoSurface: displayValue`), want the legacy fallback there, not nothing · Confidence: high on the count; whether to keep it is a human decision · Falsifier: an S4b caller for which "absent" must mean nothing · Fix class: delete, or keep and decide in S4b

### D3 — `MetersSurface.svelte: swrLowerScale`, the read/unread conjunct: redundant
Verdict: dead (a redundant conjunct); it predates this PR, which restated it · Elements: `observationValue(projection.evidence) !== null` in the `stateText` guard · Consumers: no observable effect. For SWR, `projectTxMeterPresentation` returns `text: ''` on all three branches, so `stateText` is `''`; `projectLevelMeter` sets `displayText = isObserved ? formatted + stateText : stateText`; the output is the same without the conjunct · Guards: the isolated test kills a narrowing to "current only", and deleting the conjunct does not narrow · Depends on: the projector's `text: ''` rule (MOR-2540, 2026-09-22) · Confidence: medium · Falsifier: an SWR producer with a non-empty `stateText` · Fix class: delete (carry forward; drawing code still deciding known/unknown)

### D4 — `ReceiverInstrumentCluster.svelte`, the comment above `obsText`: false
Verdict: dead prose; it predates this PR (S3 audit Q5) · Depends on: none · Confidence: high · Fix class: delete, together with the `obsText` migration in S4b

## Consolidations
### F1 — value-or-nothing extraction: migration incomplete
Verdict: in-flight; the target exists · Rank: parallel, identical behaviour at every site read · Elements: the entry points versus `VfoSurface: displayValue`; 6 `{reading}` lines in `VfoSurface: standardPanelSections`, including rfg, which computes the same value as `VfoIndicatorRow: rfGainShown`; `ReceiverInstrumentCluster: obsText/obsState/frequencyText/frequencyCh/bandwidthCh`; `meter-renderer-view.ts` ×3 · Consumers: the entry points have 3 + 3 production calls; each copy serves only its own file · Definition site: `primitives/reading-text.ts` · Divergence: none · Prior ruling: design audit Q6 S4; S3 audit · In-flight: this PR · Required surface: exists, except the NaN/null entry point · Depends on: F2, if the type is tightened first · Confidence: high · Falsifier: a recorded reason for leaving VfoSurface out (#3734 file ownership was not checked) · Fix class: consolidate · Actionable: yes, S4b

### F2 — `ValueObservation<T>` is wider than the two types it stands for
Verdict: B · Rank: parallel. The state set is written twice, in the real unions and as string literals in `observationValue`, and nothing in the type ties them together · Consumers: 3 production calls, 2 real types · Divergence: none today · Prior ruling: S1 F2, "primitives would need a structural mirror of it" · In-flight: none · Required surface: a structural type whose `state` is the literal set of both real types (current/stale with a value; unknown/unsupported/idle without). A new or renamed state, or a `DisplayValue`, would then fail to compile at the call site [I] · Depends on: none · Confidence: medium · Falsifier: `DisplayObservation`'s `reason` field breaking assignability (it should not: extra properties are allowed on non-fresh objects) · Fix class: design (small) · Actionable: yes, before S4b adds observation callers

### F3 — `valueText`: one name, and a local copy of the same rule
Verdict: duplicate mechanism · Rank: name-collision · Elements: `reading-text.ts: valueText(value, format)` versus `RfFrontEndInstrumentHost.svelte: valueText(field, value)`, which has 4 call lines; that file imports `readingText` but not `valueText` · Divergence: signature only · Prior ruling: none found · In-flight: S4b · Required surface: the NaN/null entry point · Depends on: none · Confidence: high · Falsifier: none · Fix class: consolidate in S4b · Actionable: S4b

### F4 — the S-meter "value or nothing" check is written four ways
Verdict: C for now, outputs identical · Rank: diverged (text only) · Elements: `ReceiverInstrumentCluster: meterValue` (status only); `ReceiverInstrumentHost: meterMotionInput` (operational, known, finite); `VfoIndicatorRow`'s S-meter `{#if}` (known, finite); the `MetersSurface` tile (`observed()`, finite) · Divergence: none in production. `radio-view-model-adapter.ts` builds `sMeter` through `txAuxField`, which emits `known` only when operational, and `numOrUndef` accepts only finite values [O]; the checks split only after a surface re-projects the field (R1) · In-flight: MOR-2704 · Confidence: high · Fix class: design · Actionable: only under MOR-2704

## Weakest link
D3. Its "dead" verdict rests on another module's rule, that `projectTxMeterPresentation` returns `text: ''` on every branch. Nothing pins that rule at the MetersSurface boundary, and tests one level down do use a non-empty `stateText` (`LinearSMeter.lower-scale.test.ts`: `'IDLE'`). Check first: every producer of `StationLevelMeterFrame<'swr'>` and its `stateText`.

## Cleared
- **One core rule, one delegation.** The module has one core rule, and `readingText` delegates to it.
- **`readingValue` keeps the old predicate exactly.** Its parameter type has no `availability`, so an operational widening cannot be written without a signature change; the R1 hazard is blocked by the type. [I]
- **Behaviour** is identical at all five sites (Q2).
- **H3:** no migrated site reads a NaN/null marker.
- **Purity:** the module still has one import, and it is `import type`.
- **Layering:** the premise holds (Q3).
- **Legitimately local or ruled:** `hasUsableObservation`, `rfGainText` and segmentline `stateText` are verdict C or covered by a prior ruling.
- **Citation:** it resolves.
- **F4:** the outputs agree.
- **No orphaned field:** the MetersSurface change leaves every field with a reader.

## Fix in this PR
1. **`reading-text.ts` module docstring: prose wider than the code (Q5).** CLAUDE.md says delete before narrowing. The claims are:
   - the unpinned call-site lists;
   - "every drawing site that reads it (…)";
   - "It lands in S4b …";
   - "only this module knows …";
   - "restated", and `idle`, in the `ValueObservation` docstring.
2. **`ReceiverInstrumentHost.isolated.test.ts`:** the new comment says the test goes red when `current` is dropped, but the test has no current case (Q4).
3. **`reading-text.test.ts`:** no row pairs a value with a non-value state, so a widened `observationValue` passes (Q4).
4. **`valueText`'s default formatter** has no user (D1). The "No orphans" rule applies.

## Carry forward
- **S4b:**
  - the F1 sites, including the RF-gain twin in VfoSurface, and D4;
  - the NaN/null entry point, with the three `formatValue`s and `RfFrontEndInstrumentHost: valueText` (F3);
  - decide D2 and F2 before adding observation callers;
  - keep `meter-renderer-view.ts`'s finite requirement when migrating it.
- **S5:** the 16 V3a lines in 9 files.
- **MOR-2704:** the gates listed in Q6, and F4.
- **MOR-2705:** unchanged.
- **D3:** the redundant conjunct in `swrLowerScale`.
- **No owner yet:** `SpectrumPanel.svelte`'s passband guard, and where `FilterSurface: numberOf` belongs.
