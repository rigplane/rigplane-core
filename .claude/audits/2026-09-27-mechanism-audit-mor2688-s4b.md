# Mechanism audit: PR #3780, MOR-2688 slice S4b ("the finite-number entry point and the scalar hosts")

- **Revision:** `de35a9b6` (`de35a9b6606937dd4f33039026933fe725d6ba99`), detached HEAD, clean tree. The merge base is `93f78bbc`. [O]
- **Method:** `.claude/skills/mechanism-audit/SKILL.md`, read in full in a detached worktree at the audited revision. I ran steps 0–5 in order, did the 3a sweep of the new module, and wrote the steelman before any verdict. Policy: `docs/internals/coordinator-policy.md` from the same worktree.
- **Delivery:** I did **not** write `tmp/audit_s4b_report.md`. My role forbids any write into the audited tree, so this message is the whole report. The next section is the summary of at most 40 lines that you asked for.
- **Read-only:**
  - Used: git, `git grep`, a literal `find | grep` at HEAD, `gh pr view 3780/3776 --json body` (read only), and one census script (not tracked).
  - Not run: tests, svelte-check, eslint, npm, CI, Linear. Mutation outcomes are reasoned, not run.
- **Scope:** 2 commits, 11 files (5 production, 6 test), +145/−24. [O]
- **Paths:** relative to `frontend/src` unless stated.
- **Not opened:** `rawToPercentDisplay`, `dualParamValuesFromNormX`, component-kit skins, CI results.
- **Text that directs an agent:**
  - `tmp/s4b_task.md` is the builder's dispatch. I used it only as evidence of what the builder was asked.
  - `radio-view-model-guard.ts` tells a maintainer to re-run a build. I did not act on it.
- **Claims under test, not premises:**
  - H1: there is one rule for "nothing gives `''`".
  - H2: behaviour is identical at every migrated site, including RfFrontEnd's null-only check.
  - H3: the builder's claim that no NaN reaches RfFrontEnd in shipped code.
  - H4: the census.
- **Strongest case against H1:** the entry point was not new. `primitives/scalar/continuous-pair.svelte.ts` already defines `const finiteValue` with the same name, signature and body, at the merge base. After this PR there are two. [O]

## Summary (≤40 lines)
1. **Q1, one rule.** For text, yes. `valueText` is the only "nothing gives `''`" expression, and every text site goes through it. [O]
   - The three calls in `DspScalarHost: formatValue` are three uses of the rule, not a copy: none restates a null test or `''`. [O]
   - `finiteValue` itself is a second copy of `continuous-pair.svelte.ts: finiteValue` (F1).
2. **Q2, behaviour.** Identical at all five sites for every typed input: read, null, `0`, negative, NaN, ±Infinity, absent group. [I]
   - Two predicates now also treat `undefined` as nothing: RfFrontEnd's check, and a missing `sMeter` in VfoIndicatorRow. The types exclude both inputs. [O/I]
3. **Q2, NaN claim.** The conclusion holds for the 3 heading outputs, but both reasons given are wrong: [O]
   - `radio-view-model.ts: num` runs only in DEV builds (`radio-view-model-guard.ts: guardRadioViewModel`).
   - Draft values and command-feedback values never pass through `numOrUndef`. They are finite because of other checks.
   - The 4th line (`displayFn`) receives `Number.NaN` from the HBar renderer whenever the value is unread. The in-repo HBar never reads the result for this host's props. [O] Component-kit skins: unknown.
4. **Q3, layering.** Clean. Semantic may import primitives, `reading-text.ts` still has only a type import, and every decision moved toward the layer the design audit chose. [O]
5. **Q4, dead code.** None created. Every import has a reader. [O]
6. **Q5, census.**
   - V3 (`Number.isFinite`): 249/85 → 246/82, which holds. [O]
   - The body leaves out V1 (247/60 → 246/60) and V2 (43/15, unchanged), although the task asked for every vocabulary. [O]
7. **Q6, false prose:**
   - RfFrontEnd docstring: "the NaN/`null` text rule now comes from the core `valueText`". At those lines NaN still formats as "NaN%".
   - The S4a table docstring: "every entry point drives the same cases".
   - The four new pin comments claim more than the pins test.
   - Four PR-body claims (reachability, search line, guardrail rationale, census).
8. **Q7.** The S4b NaN/null list is done. What remains goes to S4d, S5, S6 and MOR-2704. Three V3a lines have no owner.

Fix before merge: D1, D2, the pin comments, the F1 decision, and the PR-body items (see the end). Carry forward: F2, F3, S4d, S5, S6, MOR-2704, the unowned sites, and the low-severity prose.

## Steps 0–3
- **Step 0: definition sites.** Searched with `(function|const|let) +<name>[^A-Za-z0-9_]`. I did not use `\b`, because `git grep \b` returns nothing silently on this host.
  - `valueText` [O]:
    - `primitives/reading-text.ts`: the core rule;
    - `components-v2/meters/smeter-scale.ts`: a local label variable, an unrelated name collision that predates this PR;
    - a helper inside `FilterPanel.isolated.test.ts`.
  - The RfFrontEnd local copy is gone, so S4a's F3 is resolved. [O]
  - `finiteValue` [O]:
    - `primitives/reading-text.ts`: new, exported;
    - `primitives/scalar/continuous-pair.svelte.ts`: module-local, at the merge base, with 3 calls (`canonicalLane` ×2, `effectiveLane` ×1).
  - A second search by behaviour found finite-or-nothing helpers in other layers that parse `unknown` wire data [O]:
    - `commands.svelte.ts: finiteNumber`;
    - `radio-view-model-adapter.ts: numOrUndef`;
    - `scope-adapter.ts: finiteNumber` (a boolean type guard);
    - `continuous-scalar.svelte.ts: finite` (null-or-finite, a validator).
- **Step 1: prior rulings.** [O]
  - Owner ruling of 2026-09-27: drawing code gets a value or nothing.
  - Design audit Q3: "Decision: B, in `primitives/`".
  - Design audit Q6 S4/S5/S6.
  - Design audit Q7 R1. Its actual text asks each migrated site for a test with a value that is read but not operational. The dispatch's wording, that a migration never widens or narrows a predicate, is a paraphrase.
  - The S1 audit F2 warning, quoted by the S4a audit: "It becomes a fork if S4/S5 add entry points in other modules".
- **Step 2: work in flight.** S4c runs in parallel on `reading-text.ts` and `ValueObservation`. [O, from the task file]
- **Step 3: liveness.** Literal `git grep -F "<name>("` over non-test `frontend/src`, excluding the module. Production call lines [O]:

| Export | Call lines | Files | Notes |
|---|---|---|---|
| `valueText` | 10 | 5 | |
| `readingValue` | 5 | 2 | |
| `readingText` | 32 | 13 | |
| `observationValue` | 3 | 3 | |
| `finiteValue` (the export) | 7 | 4 | The raw grep shows 10 lines in 5 files: 3 of them call continuous-pair's own copy |
| `ValueObservation` | 0 outside the module | — | Unchanged; S4c |

  - Out-of-repo readers: no hits in `component-kit-api`, and `local-extensions/` is absent. [O]

## Q1: One rule?
- **Text sites.** The added production lines that contain `''`, `isFinite` or a null test are [O]:
  - `finiteValue`'s body, which is an entry point returning a value, not text;
  - the renamed `canonicalText` in TxAux's accessible-text composition, whose content is unchanged;
  - the VfoIndicatorRow predicate.
- Every text site goes through `valueText` (TxAux 1, CwKeyer 1, Dsp 3, RfFrontEnd 4). The S-meter site produces no text: it picks the meter or the unread shell through the entry points. [O]
- **Guards left in the five touched files.** None of the migrated form. What remains [O]:
  - CwKeyer and Dsp `status()`: `target === null ? '' : …` (the V3b pattern, a suffix guard).
  - RfFrontEnd's three `Number.isFinite` lines are projections, not text: `normalizedToRaw`, `readingOf`, `feedbackLaneOf`.
  - Not NaN/null: TxAux and CwKeyer `canonical()` restate `readingValue` and add a command-feedback branch (V1).
- **Codebase-wide:** not one rule. See F1 and Q7.

## Q2: Behaviour identity

| Site | read | null | `0` | negative | NaN / ±Inf | undefined or absent | Result |
|---|---|---|---|---|---|---|---|
| TxAux `formatValue` | row format | '' | formatted | formatted | '' | '' | identical [I] |
| CwKeyer `formatValue` | `${v} ${unit}` | '' | `0 unit` | formatted | '' | '' | identical [I] |
| Dsp `formatValue` (3 branches) | field label | '' | formatted | formatted | '' | '' | identical [I] |
| VfoIndicatorRow S-meter `{#if}` (`VfoIndicatorRow.svelte:204`) | meter | shell | meter (`!== null`, not truthiness) | meter | shell | missing `sMeter`: old code throws, new shows the shell | identical for typed input [I] |
| RfFrontEnd, 4 lines (546, 547, 580, 589) | `rawToPercent` | '' | formatted | formatted | "NaN%"/"Infinity%" before and after | `undefined`: old "NaN%", new '' | identical for reachable input [I] |

- **`row(field)` now runs before the guard** in TxAux and CwKeyer. This is safe: the field types are built from the tables (`TxAuxLevelField = (typeof TX_AUX_LEVELS)[number][0]`, and the same for `CwContinuousField`), so the `find(...)!` always succeeds and has no side effect. [O]
- **Dsp:** each branch depends only on `field`. `nbLevelPercent` and `nbLevelMax` are still read only for a finite value. [O/I]
- **Where `undefined` could come from at RfFrontEnd:** [O]
  - `pairLaneValue` and `scalarValue` end in `canonical`, which is a number or null (`canonicalOf`, `canonicalLane`).
  - `displayFn` is typed `(v: number) => string`.
- **VfoIndicatorRow's `sMeterValue`** repeats the same pure expression, so inside the block it is never null. [I] It is typed `number | null`, which `LinearSMeter`'s `value: number | null` accepts. The old code narrowed the value to `number` by the compiler; that link between check and value is gone. Low severity.
- **R1:** does not apply to the hosts, whose input is `number | null`. For the S-meter, neither the old nor the new check reads `operational`, and `readingValue`'s parameter has no `availability`. [O/I] `VfoIndicatorRow.test.ts` has no read-but-not-operational case: `known()` is always operational. [O]

**H3, NaN reaching RfFrontEnd, by input:**

| Input | How it arrives | Why it is finite | Goes through `numOrUndef`/`num`? |
|---|---|---|---|
| Reading | `deriveRfFrontEnd` → `txAuxField(…, numOrUndef(rx?.rfGain))` → the host's `readingOf` → `canonicalOf` / `canonicalLane` | `numOrUndef`; `readingOf` and both controllers check again [O] | `numOrUndef` yes; `num` DEV only [O] |
| Command feedback | `getRfSqlControlFeedback` → `projectControlFeedback` → `RF_GAIN_`/`SQUELCH_COMMAND_DESCRIPTOR` → `feedbackLaneOf` | `normalizedLevelCommand` (safe integer 0–255, divided by 255) and `finiteNumber` in [0,1] [O]. `viewOf` passes `feedback.target` through unfiltered. `project` keeps finite values finite and passes non-finite ones through [O] | no |
| Draft | the admission check in `continuous-scalar` (`Number.isFinite(normalized)` plus domain bounds) [O]; the pair's lanes derive from the axis draft [I, not opened] | admission check | no |
| `displayFn` (line 589) | `HBarRenderer: displayValue`: `displayFn(renderedValue)`, or `displayFn(Number.NaN)` when unread [O] | none: NaN is what the renderer sends | no |

- **`displayFn` details** [O except where marked]:
  - RfFrontEnd passes `showValue={false}` and gives neither `valueProjection` nor `accessibility`.
  - `displayValue` is read only under `showValue`, or as `aria-valuetext` when `valueProjection` is set. So its result reaches no DOM text or attribute here.
  - It is probably never even computed, because Svelte 5 computes derived values only when read. [I]
  - `ValueControl` sets `unknownDisplay` only when there is no external binding, so a kit HBar skin (`effectiveAppearance.hbar`) would get `displayFn` and no `unknownDisplay`. Whether any kit skin calls it: unknown.
- **Verdict on H3:** the conclusion holds for three lines [I]. Its two stated reasons are wrong [O]. "None found" misses the renderer's NaN on the `displayFn` line, which nobody sees today. [O/I]
- **The hosts:** the same renderer convention is the live NaN source for TxAux, CwKeyer and Dsp, whose `showValue` is true under an explicit presentation. [O] So `finiteValue`'s NaN branch runs in shipped code. [I] The new pins inject NaN through `canonical()` instead; both paths reach `formatValue`. [O]

## Q3: Layering
- **New imports:** `../primitives/reading-text` in TxAux, CwKeyer and Dsp; names added to the existing imports in VfoIndicatorRow and RfFrontEnd. `eslint.config.js: FORBIDDEN_SEMANTIC_IMPORTS` bans only `skins`. [O]
- **`reading-text.ts`** still has a single `import type`. [O]
- **Where decisions moved:** from semantic into primitives, the layer the design audit chose, or from a file-local helper into the core rule. None moved into a layer that cannot own it. [O]
- **The one open question** (F1) is duplication inside one layer, not displacement.

## Q4: Dead code (3a sweep)
- **`reading-text.ts`:** 5 functions, 1 type, 0 constants, 0 fields. Liveness is in step 3. [O]
- **Touched files:** every imported name has a reader. The deleted local `valueText` had 4 callers, all rewired. `RfFrontEndLevelField` is still used. `{@const canonicalText}` is read. [O]
- **Nothing newly dead.** S4a's D1 (`valueText`'s default formatter) was already gone at the base. S4a's D2 (`observationValue`'s `undefined` branch, used only by tests) is unchanged. [O]
- **Search method:** literal only. Dynamic access was not searched; ES named imports would not use it.

## Q5: Census
- **Rule:** the quoted rule matches #3776's body word for word. [O]
- **Method:** `git grep -cE` at both revisions, filtered to that rule; at HEAD also the literal `find … | xargs grep -cE`. The two methods agree. [O]

| S4a PR pattern | 93f78bbc | de35a9b6 | PR body |
|---|---|---|---|
| V1 `status ?[!=]== ?'(known\|unknown)'` | 247 / 60 | 246 / 60 | missing |
| V2 `state ?[!=]== ?'(current\|stale)'` | 43 / 15 | 43 / 15 | missing |
| V3 `Number\.isFinite` | 249 / 85 | 246 / 82 | holds |

- **Per file:** each of the four hosts goes 1 → 0 on V3, and `reading-text.ts` 0 → 1. VfoIndicatorRow goes 5 → 4 on V1. [O]
- **Supplementary, using the S4a audit's rule** [O]:
  - V3a: 16/9, unchanged.
  - V3b: 30/19, unchanged.
  - Early-return `isFinite(…) … return '';`: 6 → 3.
  - The deleted RfFrontEnd guard spanned two lines, so no line-based pattern counts it.

## Q6: Prose
- **False: `RfFrontEndInstrumentHost.svelte`, the docstring now above `pairLaneValue`.** It says "the NaN/`null` text rule now comes from the core `valueText`". The core rule maps only null/undefined to `''`, and NaN formats as "NaN%". Keeping that was the deliberate choice of this slice. [O]
  - Wider than the code: "to read values". It also applies to drafts and pending targets.
  - The function it documented was deleted, so the docstring now sits above `pairLaneValue`, which returns a number (D1).
- **False, outside the diff: `reading-text.test.ts`, the docstring above `TABLE`.** "the ONE shared table: every entry point drives the same cases". `finiteValue` runs only through `FINITE_TABLE` (D2). [O]
- **Wider than the tests: the four new pin comments.** They say "red under a `finiteValue` mutation that accepts a marker value". Each pin feeds only NaN, so a mutation that accepts ±Infinity but not NaN leaves them green; only the shared table catches it. [I]
- **Low: `reading-text.ts`, the list entry and the docstring "NaN/null/Infinity marker".** No production code uses ±Infinity as a marker. A literal search for `Infinity` finds 2 code lines, a min-distance seed and a timestamp. [O]
- **True:** the host comments in TxAux, CwKeyer and Dsp; the S4b table docstring. [O/I]
- **PR body, false or wider than the code:**
  - The reachability paragraph (Q2).
  - The search line: it omits the same-named `continuous-pair: finiteValue`. [O]
  - The guardrail rationale "…and the captures that show it": the PR changes no capture. [O]
  - The census leaves out V1 and V2. [O]
- **PR body, low:**
  - "null-only predicate kept exactly": the core check also treats `undefined` as nothing.
  - "Existing literal pins cover it":
    - the `displayFn` line has no pin;
    - the scalar output is pinned in `RfFrontEndSurface.test.ts` (`''`, `80%`), which the body does not cite;
    - `100%` belongs to the compatibility-reading state, not to command feedback. [O]
- **PR body, true:**
  - `finiteValue`'s description.
  - The pins: `80%`, `600 Hz`, `64`, the unread shell.
  - The VOX-delay formatter exists.
  - `offenders.json`, `data-*`, `aria-*`, `role` and `disabled` are untouched. [O]
- **Not checked:** the reported suite, eslint, svelte-check and mutation results. I re-ran none of them.

## Q7: What remains for the NaN/null vocabulary (updating S4a's list)
- **S4b:** closed. All five S4a items are done; RfFrontEnd keeps its null-only check by instruction. [O]
- **S4c:** nothing for NaN/null. `VfoSurface: sValue` (operational, known and finite) sits in S4c's file but belongs to MOR-2704. [O]
- **S4d:**
  - `meter-renderer-view.ts`: the "known and finite" and `evidence.value` checks (`levelEvidence` and nearby).
  - `MetersSurface.svelte`: the S-meter tile's `reading`. Its finite half is S4d or S5; its operational half is MOR-2704. [O]
- **S5:** the design audit's list [O, except SpectrumToolbar, which I did not recount]:
  - 13 of the 16 V3a lines;
  - FilterPanel ×2 and VfoPanel;
  - the two-line `RfFrontEnd.svelte: displayRfGain`;
  - SpectrumToolbar ×3.
- **No owner** (corrects the S4a audit's "S5: 16 lines") [O]:
  - `AmberCockpit.svelte: ritOffsetLabel`;
  - `FilterSurface.svelte: formatExactWidth`;
  - `RitXitScanSurface.svelte: signedOffset`, which is exactly the `valueText(finiteValue(readingValue(f)))` shape.
- **S6 (optional in the design audit):** the renderers' `unknownDisplay ?? (displayFn ? displayFn(Number.NaN) : '')`, repeated in 5 files (F2).
- **MOR-2704:** `ReceiverInstrumentHost: meterMotionInput`, `VfoSurface: sValue`, and the MetersSurface tile's operational half.

## Steelman (step 4)
- **For placement:** the design audit put every entry point in one primitives module, and the task named that module.
- **For keeping continuous-pair's copy:** the pair is a behaviour controller. Its `canonicalLane` decides behaviour, which the owner ruling of 2026-09-27 keeps apart from drawing. Importing the display-text module there would tie behaviour to drawing. With two copies, the rule of three says do not abstract.
- **For RfFrontEnd:** the null-only check was kept by instruction, and it keeps behaviour identical.
- **For the code shape:** the Dsp calls are forced by one formatter per field, and the repeated S-meter expression is forced by where Svelte allows `{@const}`.
- **Result:** the steelman wins on placement, identity, the RfFrontEnd check and code shape. It loses on the search record, because the user's rule "Close enough is reuse" asks the PR to name the twin and say why it does not fit. It also loses on prose.

## Deletions
### D1: `RfFrontEndInstrumentHost.svelte`, the docstring above `pairLaneValue`
- **Verdict:** dead prose. Its function was deleted, and its NaN clause is false.
- **Consumers:** none.
- **Guards checked:** not applicable.
- **Collateral:** none.
- **Depends on:** none.
- **Confidence:** high.
- **Falsifier:** a NaN-to-`''` rule at those lines.
- **Fix class:** delete, following the owner's ruling of 2026-08-31 to delete before narrowing.

### D2: `reading-text.test.ts`, the docstring above `TABLE`, clause "every entry point"
- **Verdict:** dead prose, false since this PR.
- **Consumers:** none.
- **Depends on:** none.
- **Confidence:** high.
- **Falsifier:** `finiteValue` rows inside `TABLE`.
- **Fix class:** delete.

## Consolidations
### F1: finite-or-nothing extraction, two `finiteValue`s with one name in `primitives/`
- **Verdict:** undetermined between A and C.
- **Rank:** parallel copies, identical behaviour, sharing one name.
- **Elements:**
  - `reading-text.ts: finiteValue`: 7 production call lines in 4 files;
  - `continuous-pair.svelte.ts: finiteValue`: 3 call lines in its own module (`canonicalLane` ×2, `effectiveLane` ×1). [O]
- **Divergence:** none. Same signature and expression; one is a `function`, the other an arrow. [O]
- **Prior ruling:** design audit Q3; S1 F2; owner ruling of 2026-09-27.
- **In-flight:** none.
- **Required surface:** exists (the export).
- **Depends on:** none.
- **Confidence:** high on the facts; the verdict is a design call.
- **Falsifier:** a recorded rule that behaviour primitives must not import the display module (then C).
- **Fix class:** consolidate, or none.
- **Actionable:** yes. Needs a decision before merge.

### F2: the NaN marker, produced by the renderers and absorbed by the hosts
- **Verdict:** B, covered by design S6.
- **Rank:** parallel (5 identical renderer defaults).
- **Consumers:** the hosts' `finiteValue`, and RfFrontEnd's null-only `displayFn`.
- **Divergence:** RfFrontEnd would print "NaN%" if its `displayFn` result were ever rendered.
- **In-flight:** none.
- **Depends on:** S5.
- **Confidence:** medium.
- **Falsifier:** a shipped kit skin calling `displayFn(NaN)`.
- **Fix class:** design.
- **Actionable:** S6.

### F3: `status()` suffix builders diverge, existing before this PR
- **Verdict:** C for now.
- **Rank:** diverged.
- **Elements:** `TxAuxScalarHost: status` drops `; confirmed` when the value is empty. `CwKeyerInstrumentHost: status` and `DspScalarHost: status` always append `; confirmed ` followed by `''`. [O]
- **Reachability:** `panel-adapters.ts: projectControlFeedback` emits `phase: 'unavailable'` with `confirmed: null`. [O] Whether CwKeyer and Dsp feedback comes from that producer: unknown.
- **Depends on:** none.
- **Confidence:** medium.
- **Fix class:** consolidate.
- **Actionable:** carry forward; no owner.

## Weakest link
H3's "not visible today" for the `displayFn` line. It rests on no shipped component-kit HBar skin evaluating `displayFn`. Check first: the `hbar` entry of every shipped appearance reachable through `ValueControl: effectiveAppearance`.

## Cleared
- One core text rule, and the PR adds no copy of it.
- Behaviour identity at all five sites. The S-meter check still draws at a reading of `0`.
- RfFrontEnd's observable null-only check (NaN and ±Infinity still format).
- S4a F3 is resolved.
- Layering and module purity.
- Imports and liveness.
- The V3 census numbers.
- The pins: each new pin goes red under a mutation that accepts NaN, and `FINITE_TABLE` has a row for each marker value. [I]
- `data-*`, `aria-*`, `role` and `disabled` are untouched.
- The `{@const}` placement is legal.

## Fix in this PR
1. D1: the RfFrontEnd docstring's false NaN clause, now attached to the wrong function.
2. D2: the `TABLE` docstring's claim that every entry point drives it.
3. The four new pin comments claim "a marker value"; the pins cover NaN only.
4. F1: decide whether there is one `finiteValue` or two. Either way, the search line must name `continuous-pair.svelte.ts: finiteValue` and give the reason.
5. PR body:
   - the reachability paragraph: `num` runs in DEV only, and the renderer sends NaN to `displayFn`;
   - the guardrail rationale's "captures";
   - the census must add V1 (247/60 → 246/60) and V2 (43/15, unchanged).

## Carry forward
- **S4d:** `meter-renderer-view.ts` and the MetersSurface tile.
- **S5:** the design audit's list.
- **Unowned V3a sites:** `ritOffsetLabel`, `formatExactWidth`, `signedOffset`.
- **S6 / F2:** the renderers' NaN convention. Fixing it also removes RfFrontEnd's hidden "NaN%" on the `displayFn` line.
- **MOR-2704:** its three gates.
- **F3.**
- **Value-or-nothing copies (V1):** TxAux and CwKeyer `canonical()`.
- **From S4a:** D2, and the `ValueObservation` type (S4c).
- **R1:** the S-meter test with a read but not operational value.
- **Low prose:** "Infinity marker", "kept exactly", "pins cover it".

Key files (absolute):
- `frontend/src/primitives/reading-text.ts`
- `frontend/src/primitives/scalar/continuous-pair.svelte.ts`
- `frontend/src/semantic/RfFrontEndInstrumentHost.svelte`
- `frontend/src/primitives/__tests__/reading-text.test.ts`
- `frontend/src/components-v2/controls/value-control/HBarRenderer.svelte`
- `frontend/src/components-v2/wiring/radio-view-model-guard.ts`