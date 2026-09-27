# Mechanism audit: PR #3790, MOR-2688 slice S4c ("the observation rule is typed and takes its fallback")

- **Revision:** `b9784393` (`b978439331b3e0c1f30aa87d78b810665a49bf62`), detached HEAD, clean tree. Merge base `fffa55c7`. The PR has 6 commits and 8 files (5 production, 3 test), +180/−68. [O]
- **Method:** `.claude/skills/mechanism-audit/SKILL.md`, read in full from a detached worktree at the audited revision. I ran steps 0–5 in order, did the 3a sweep of the touched modules, and wrote the steelman before any verdict. Policy: `docs/internals/coordinator-policy.md` from the same worktree.
- **Read-only:**
  - Used: git, grep and find; `gh pr view 3790/3776` and `gh pr list`, read only; one census script (not tracked).
  - Not run: tests, svelte-check, eslint, npm, the mini, Linear. I did not observe CI. Mutation outcomes are reasoned, not run.
  - I delegated nothing. Model: claude-opus-5-5. Whether the dispatcher selected it explicitly is unknown to me.
- **Coverage:** paths are relative to `frontend/src`.
  - Opened in part: VfoSurface, VfoPanel, `radio-view-model-adapter.ts`, `radio-view-model.ts` and the test files.
  - MetersSurface: its call line only. `ReceiverInstrumentHost.isolated.test.ts`: grep only.
- **Text directing an agent:** none found. I used `tmp/s4c_task.md` (the builder's dispatch; `/tmp/` is gitignored) and the PR body only as evidence.
- **Prior ruling, applied and not re-litigated:** "an absent observation means no display projection; the caller's other source decides". Source: `tmp/s4c_task.md`, item 2. I did not read Linear.
- **Claims under test:**
  - H1: one rule in the touched files, and no new copy.
  - H2: the union matches the real types, and the tie test fails in CI.
  - H3: `absent` is used only for "no projection", with the same fallback values as before.
  - H4: behaviour is identical in every state.
  - H5: layering is clean.
  - H6: the census holds.
- **Strongest case against:**
  - Identity at the Cluster rests on reading the code alone. No test asserts any Cluster output with a value present, or any `data-state`. So the PR's evidence ("suite green") cannot fail there.
  - The new comment citations name an audit that is not in the tracked tree.

## Summary (≤40 lines)
1. **Q1, one rule. Holds for the touched files.** [O]
   - All 15 production `observationValue(` call lines now make the observation → value decision (3 at the base).
   - The Cluster's 4 current/stale lines and `VfoSurface: displayValue` are gone. The PR adds no copy.
   - Current/stale tests remaining in the touched files decide `disabled` (`RIH: frequencyDisabled`, `VfoSurface: readoutDisabled`) or are status outputs (`data-state`-style attributes) that design audit R2 keeps verbatim. The classification is [I].
   - Still elsewhere:
     - S4d: `meter-renderer-view.ts` ×3.
     - S5: VfoPanel ×2. The census regex cannot see these lines.
     - No owner: SpectrumPanel ×4 and `FilterSurface: numberOf`.
     - Under prior rulings: adapters, PBT and segmentline.
2. **Q2, the type. The literal union equals both real types exactly:** same state names, and the same states carry `value`. [O]
   - The tie assigns each whole union, so a renamed state on either side, or a `DisplayValue`, fails `npm run check`. [I, not run]
   - The file is inside CI's type check [O]: `quick.yml` runs `npm run check`, which runs `svelte-check --tsconfig ./tsconfig.app.json`. That config includes `src/**/*.ts` and excludes nothing.
   - The tie is one-directional. [I]
3. **Q3, the fallback.**
   - `absent` is returned only for `undefined`. [O]
   - Every caller passes exactly its old fallback. [O]
   - 13 of 15 calls pass it; the 2 that rely on the default are correct. [O]
   - The production adapter always sets `display`, so every non-null fallback is reached only by test fixtures and by type-legal external producers. [O/I]
4. **Q4, behaviour identity.**
   - Identical at every site for every input a producer can build. `obsState` stays exactly `'current' | 'unknown'`. [I]
   - Pins: VfoIndicatorRow and VfoSurface are pinned. RIH's absent path is unreachable in its test harness.
   - **The Cluster has no pin.** If `observationValue` ignored `absent`, MAIN's frequency, mode and filter in the `DualSdrFace.component.test.ts: view()` fixture would go blank, and every test would stay green. [O test text; I outcome]
5. **Q5, layering. Clean.** [O] No primitives file imports semantic. The tie test sits where both imports are legal.
6. **Q6, dead code. None created.** [O]
   - The sweep of 5 modules (181 definition matches) finds no name without a reader.
   - S4a's D2 branch is now live; its non-null fallbacks are reached only by fixtures (D2).
   - `?? null` became type-redundant (D3).
   - D4 is a test comment that predates this PR.
7. **Q7, census. Holds exactly, per file too.** [O] V1 246/60 → 239/59; V2 42/15 → 37/14; V3 245/82 unchanged.
8. **Q8, prose.**
   - Three new citations ("the audit's D2", "(audit F2)", "(audit Q4)") point to an untracked audit. In the tracked tree they resolve to unrelated findings (D1).
   - The PR body has five problems:
     - the guardrail rationale is false for this PR, the same defect as in S4b;
     - it says #3782 shares a file this PR does not touch;
     - it says "widened" for a type that was narrowed;
     - its list of RF-gain pins that fail under the mutation is wrong;
     - its evidence for the identity claim does not cover the Cluster.

**Fix before merge:**
1. Cluster literal pins.
2. The three citations.
3. PR-body corrections.

**Carry forward:** S4d, S5, sites with no owner, MOR-2704, the `display?` question, the RFG rule written twice, the RIH/VfoSurface lock divergence, low-severity prose.

## Q1 — One rule
- **All 15 production calls make the decision.** [O] Literal `git grep -n "observationValue("` at the head:
  - RIH 1 (`displayFrequency`) and VfoIndicatorRow 1 (`rfGainShown`);
  - VfoSurface 8: `frequencyDisplay`, `displayModeOrFilter`, the `standardPanelSections` rfg block, `displayHz`/`displayMode`/`displayFilter`, the slot-choice `frequencyText`, and the VfoPanel `displayHz` prop;
  - Cluster 4: `obsText`, `obsState`, `frequencyText`, `frequencyCh`;
  - MetersSurface 1, unchanged.
- **No local current/stale value test remains in the touched files.** [O] V2 per file: Cluster 4 → 0, VfoSurface 3 → 2.
  - What remains are lock interlocks [O lines; classification I]:
    - `VfoSurface: readoutDisabled` (2 lines);
    - `RIH: frequencyDisabled` (2 lines).
  - The owner decision of 2026-09-27 keeps status in behaviour decisions.
- **Other status reads in the touched files yield no value.** [O]
  - `unsupported` gates: `VfoSurface: displayModeOrFilter`, the rfg block, and VfoIndicatorRow's `{#if}`.
  - Status outputs that R2 keeps verbatim (see F3).
- **No new copy.** [O] Added production lines contain no current/stale comparison and no `''`-for-nothing expression (diff grep).
  - The only new status mapping is `obsState`'s `!== null ? 'current' : 'unknown'`, which feeds `data-state`.
- **Remaining V2 lines elsewhere (37 lines, 14 files)** [O lines; owners from the design, S4a and S4b audits]:
  - S4d: `meter-renderer-view.ts` ×3.
  - Adapters, verdict C: panel-adapters ×2, `radio-view-model-adapter.ts` ×2, `scope-passband-display.ts` ×6.
  - PBT, kept off the no-change path by the design audit: FilterSurface ×5 (including `numberOf`, which S4a calls disputed), `pbt-presentation-continuity.ts` ×3, SemanticRadioSurfaces ×3.
  - No owner: SpectrumPanel ×4.
  - Covered by rulings: segmentline `lcd-display-helpers.ts` ×2, the projector's `retained`, `radio-view-model.ts: validateDisplayObservation`.
  - Not counted by the census: `VfoPanel.svelte` `frequencyState === 'current'` ×2 (see Q7). These are behaviour gates (the entry button and `disabled`), and S5 owns VfoPanel.
- **Verdict on H1:** true for the touched files; not true of the codebase.

## Q2 — The type
- **Exact match.** [O] Sources: `radio-view-model.ts: DisplayObservation`, `bar-meter-projector.ts: LevelMeterEvidence`, `reading-text.ts: ValueObservation`.
  - Value-carrying: current and stale in all three.
  - No value: unknown (DisplayObservation's carries `reason`) and unsupported, plus idle from LevelMeterEvidence.
  - The union of both real types is exactly the restated set.
- **The tie fails as claimed.** [I, TS semantics, not run]
  - Both tests type a function parameter as the whole real union. A rename on either side, or a new state on the real side, breaks assignability.
  - `DisplayValue<number>`: the declared `known` literal narrows the variable to that member, which no member of the union accepts, so the `@ts-expect-error` is used up.
  - If the union ever accepted `known` (or `state: string`), TypeScript's "unused `@ts-expect-error`" error (TS2578) fails the check.
  - A `// @ts-expect-error` followed by further `//` lines still applies to the code line after them.
- **Limit: the tie is one-directional (real ⊆ restated).** [I]
  - A state added only to `ValueObservation` compiles.
  - So does an existing no-value state that starts carrying `value`, because extra properties are allowed. That case is still consistent with "the state decides".
- **The tie is in CI's scope.** [O]
  - `package.json` `check` is `svelte-check --tsconfig ./tsconfig.app.json && tsc …`.
  - `tsconfig.app.json` includes `src/**/*.ts` and has no `exclude`. The base `@tsconfig/svelte` 5.0.8 (lockfile) has no `exclude` either.
  - `quick.yml` step "Frontend install + check + tests + build" runs `npm run check` when `classify-quick-paths.py: is_frontend` is true, which it is for any `frontend/` path. `full.yml` and `publish.yml` run it too.
  - vitest has no `typecheck` setting (`vite.config.ts`). So the file's runtime asserts are trivially true, and its only teeth are svelte-check's. That is enough, because svelte-check runs in CI.
  - Supporting evidence: commits 8e496d90, c763989c and 958d5c4d fix type errors in `.test.ts` files "for svelte-check". [O]
- **The PR's red run does not isolate the tie.** [I] Renaming `'stale'` in `radio-view-model.ts` also breaks other code, for example `display-observation.ts: qualifyEvidence`, which returns `'stale'` as a `DisplayObservation`. Today the tie's unique content is the `DisplayValue` exclusion, plus cover for future callers.

## Q3 — The fallback as a mechanism
- **`absent` only for "no projection".** `reading-text.ts: observationValue` returns `absent` only when `observation === undefined`; a present observation cannot reach it. [O]
  - The adapter represents "unknown" as a present `{state:'unknown', reason}`, never as `undefined`: see `qualifyDisplayObservation` and the unknown-slot literal in `toRadioViewModel`. [O]
- **Every fallback equals the old one.** [O]

| Caller | Old fallback | New fallback |
|---|---|---|
| `rfGainShown` | `readingValue(field)` | same |
| `displayFrequency` | `record.frequencyHz` | same |
| VfoSurface's 7 former `displayValue` calls | the same strict argument | same |
| VfoSurface rfg | the inline known-or-null | `readingValue(indicator.rfGain)`, same value |
| `obsText`, `obsState`, `frequencyText` | `legacy` when not nullish | `legacy ?? null` |

- **Load-bearing:** 13 of 15 calls pass `absent`. [O] The two defaults are correct:
  - `MetersSurface: swrLowerScale` passes `evidence`, which is required and never `undefined`.
  - `Cluster: frequencyCh` wants the observed value only; the line before already pushes the strict value, as the old code did.
- **Reach in production:** every non-null fallback is unreachable through the production adapter (D2). [O/I]
- **Residual:** the type only requires `T`, so nothing stops a future caller from passing a glyph as `absent`. No caller does. [I]

## Q4 — Behaviour identity
| Site | current | stale | present unknown / unsupported | absent observation | absent group | `0` |
|---|---|---|---|---|---|---|
| `rfGainShown` | v | v | null (unsupported is gated before the call) | strict or null | no indicator → no call | 0 |
| `displayFrequency` | v | v | null | `frequencyHz` | `record` null → null (guard kept) | 0 |
| VfoSurface, 8 calls | v | v | null (unsupported gated in `displayModeOrFilter` and rfg, unchanged) | strict | `vfo`/`dominant` guards unchanged | 0 |
| VfoSurface, 6 `{reading}` lines | known → value, else null | — | — | — | typed parameters | 0 and `false` kept |
| `obsText` / `frequencyText` | text | text | `''` | `String(legacy)` / localized, or `''` | `vfo` undefined → `''` | `'0'` |
| `obsState` | current | current | unknown | current if legacy set, else unknown | unknown | current |
| `frequencyCh` / `bandwidthCh` | pushes v | pushes v | nothing | nothing from display | skipped | pushes 0 |

- **Identical for every input a producer can build.** [I, reasoned] The code differs only for type-illegal inputs:
  - a current or stale observation with a nullish `value` (old: `undefined`, `'undefined'` or a throw; new: null);
  - a `known` reading whose value is null (`bandwidthCh`).
  - `qualifyEvidence` emits current/stale only for finite numbers, booleans and non-empty strings. [O]
- **`obsState`'s output set is exactly `'current' | 'unknown'`.** Its return type and ternary are unchanged. [O]
- **Reading evaluated eagerly:** `readingValue(field)` and `record.frequencyHz` are now read even when the display is present. Both are pure. [I]
- **Pins:**
  - VfoIndicatorRow: the no-display rows, and "does not display a strict fallback default when explicit observation is unknown". [O]
  - VfoSurface: the new chip and RFG pins. `semantic/fixtures/topologies.ts` contains no `display` at all, so the existing tests run the fallback path. [O]
  - RIH: its harness feeds adapter output (S4a audit, Q3), so the absent path is unreachable there.
  - **Cluster: no pin.** A literal search of `frontend/src` and `frontend/tests` for `data-frequency|data-badge|data-receiver-cluster`, and for `7,070|LSB|data-state` in the files that mount the face, finds only the unread-state `''` and width pins. [O]
    - Nothing asserts `data-state`, a present observation, or `view()`'s MAIN legacy values (7,070,000 / LSB / FIL2, with no display). [O]
    - `modeFilterView` uses `filterWidthMax: unknown()`, so `bandwidthCh`'s known path is unexercised. [O]

## Q5 — Displacement and layering
- **Imports:** `reading-text.ts` has one import, `import type` from `./control-instruments/…`. A literal grep of `primitives/` for `semantic/` in any import form, including `import(`, finds 0. [O]
- **Direction:** VfoSurface's local `displayValue` moved to the primitive, the layer the design audit chose (Q3, decision B). [O]
- **Tie placement:** `eslint.config.js: FORBIDDEN_PRIMITIVES_IMPORTS` applies to `src/primitives/**`, tests included. So the tie must live in `semantic/__tests__/`, where it is. [O]
- Nothing moved against the layer map. [O]

## Q6 — Dead code
- **3a sweep of 5 modules.** [O] Counts of `function`/`const`/`let` definitions: VfoSurface 88, RIH 53, Cluster 19, VfoIndicatorRow 16, `reading-text.ts` 5 (181 matches).
  - No name has zero readers. The 2 single-occurrence hits are words in comments ("changed", "is").
  - All 5 `reading-text.ts` functions have production readers. `ValueObservation` has none outside its module, where it is `observationValue`'s parameter type. [O, `git grep -w`]
- **The deleted helper leaves nothing behind:** no mention of the VfoSurface `displayValue` remains in `frontend/src` or `docs`. [O]
- **The old `display === undefined` branches are gone.** [O] The presence tests that remain decide behaviour: `hasDigitReadout`, `readoutDisabled`, `frequencyDisabled`.
- **S4a's D2:** see D2 below. **`?? null`:** see D3.

## Q7 — Census
- **The rule is quoted correctly.** The PR body's rule matches the #3776 body verbatim. [O]
- **Recount:** I replicated the rule over the tracked trees at both revisions (`git ls-tree` + `grep -cE`). At the head I also ran the literal `find` for V2 and got 37/14; there are no ignored or untracked files under `frontend/src`. [O]

| Vocabulary | Base | Head |
|---|---|---|
| V1 | 246/60 | 239/59 |
| V2 | 42/15 | 37/14 |
| V3 | 245/82 | 245/82 |

- **Per-file deltas match the body exactly:** VfoSurface V1 28 → 22 and V2 3 → 2; Cluster V1 1 → 0 and V2 4 → 0. [O]
- **Blind spots of the rule:** it is case-sensitive, so it misses `frequencyState === …` (VfoPanel ×2), and it is single-line only. [O]

## Q8 — Prose
Comments and docstrings the diff adds or changes:
- **True:** [O]
  - `reading-text.ts`: the `ValueObservation` and `observationValue` docstrings;
  - the RIH `displayFrequency` comment;
  - the VfoIndicatorRow `rfGainShown` docstring;
  - the VfoSurface `displayModeOrFilter` addition;
  - the Cluster comment (D4's false comment is deleted);
  - the observation-block comment in the table test;
  - the RFG fixture lines in the VfoSurface test.
- **The three citations resolve to the wrong findings (D1).**
- **Low severity:**
  - the carryValue comment's "the pin must survive the entry point widening" reads like a surviving mutant;
  - "SUB mirrors" in the fixture comment: notch is `off` on MAIN and `auto` on SUB;
  - the tie docstring says "DisplayValue … fails to compile"; in fact the expectation of failure is what fails if it compiles.
- **Existing assertions unchanged:** the only edits to existing test lines are the describe title and one type cast. [O]

PR body:
- **Guardrail line:** 8 files is below the 10-file ceiling, so no exception exists. The soft threshold (6 files) needs a reason why this is one unit of work. The given reason, "one placeholder rule … and the captures that show it", is false here: the PR changes no captures and no placeholder rule. The S4b audit flagged the same phrase. [O]
- **"PR #3782 shares … `ReceiverInstrumentHost.isolated.test.ts`":** this PR does not touch that file. [O]
- **"`ValueObservation<T>` … widened here":** it was narrowed. [O]
- **The `rfGainShown` pin list names "raw 254, max 1" as failing under the mutation.** Those rows expect `''` and cannot fail; the body's own run reports 3. [O/I]
- **"Identical in every state … the full mini frontend suite passes":** the cited evidence cannot fail for the Cluster's non-empty states (Q4). [O]
- **"every record gets a `display` from `qualifyDisplayObservation`":** unknown slots get a literal instead. [O] Low.
- **"incl. the new chip pins":** by reading, only the RFG test depends on `absent`. [I] Low.
- **Cannot verify:** the mini runs, and the "mini's `fe` run" stage; `rp-remote-test.sh` is not tracked in the repo.

## Steelman (step 4)
- **The case for the PR as it stands:**
  - The rule sits in the only layer every drawing consumer may import.
  - `absent` replaces the per-caller `display === undefined ?` branches with one decided rule, and by construction it cannot hide a present unknown.
  - The remaining status reads in the touched files are behaviour locks or status outputs that the owner ruling and R2 keep.
  - A restated union plus a tie test is the only legal way to type the primitive without a primitives → semantic import.
  - The shared table pins the entry point, so a Cluster pin adds little.
- **It wins on:** layering, one rule, the type, the fallback semantics and the census.
- **It loses on:**
  - the task's own acceptance rule, "a literal pin where a migrated site has none, which fails under a mutation of the entry point", for the Cluster;
  - the citations;
  - the PR body.

## Deletions
### D1 — The new comment citations "the audit's D2", "(audit F2)", "(audit Q4)": wrong referent
- **Verdict:** dead (prose)
- **Elements:**
  - the `reading-text.ts` module docstring, `observationValue` bullet;
  - `reading-text-type-tie.test.ts`, the `@ts-expect-error` comment;
  - `reading-text.test.ts`, the carryValue comment.
- **Consumers:** none
- **Written / read:**
  - The intended source, the S4a audit, exists only as `tmp/audit_s4a_report.md`, and `.gitignore` lists `/tmp/`.
  - The tracked MOR-2688 audits are design, s1 and s2. The same docstring names the design audit by path. That audit's D2 is the AntennaSurface re-exports, its F2 is the percent-display guard, and its Q4 is "Behaviour that must keep the status". s1's and s2's F2s are also different findings. [O]
- **Guards checked:** n/a
- **Collateral:** none
- **Depends on:** none
- **Confidence:** high
- **Falsifier:** an S4a audit archived in `.claude/audits/` and named by the citations.
- **Fix class:** delete (owner ruling of 2026-08-31: delete before narrowing), or name a tracked file.

### D2 — S4a's D2, `observationValue`'s `observation === undefined` branch: now live; non-null fallbacks are fixture-only here
- **Verdict:** undetermined
- **Elements:** the branch itself, plus the 13 non-null `absent` arguments.
- **Consumers:**
  - Production: the Cluster when `vfo` is undefined, in which case `absent` is null as well. Whether a live face renders that state: unknown.
  - Tests: the table; every fixture record without `display` (topologies, also used by `tests/e2e/visual/visual-baselines.spec.ts`); DualSdrFace `view()`; VfoIndicatorRow `known(value)`.
- **Written / read:** [O]
  - `radio-view-model-adapter.ts: toRadioViewModel` gives every VFO record a `display`.
  - `deriveReceiverIndicators` gives every indicator's `rfGain` a `display`.
  - `dual-receiver-strips.ts: forSlot` passes indicators through unchanged.
  - The only other literal producers are the topology fixtures (tests only) and `radio-view-model.ts: validateVfo`, which keeps `display` when present.
- **Guards checked (literal search):**
  - dynamic access: none found;
  - out-of-repo: `local-extensions/` is absent and the Pro tier is unknown;
  - public API: `display?` is optional in the type and in the validator; `component-kit-api` names none of these types;
  - tests-only: yes, for the non-null fallback.
- **Collateral:** n/a
- **Depends on:** a decision whether `display` becomes required.
- **Confidence:** medium
- **Falsifier:** any production producer of a record, or of an indicator `rfGain`, without `display`.
- **Fix class:** none in this PR.

### D3 — `observationValue`'s `?? null`: type-redundant after the literal union
- **Verdict:** undetermined, a defensive guard.
- **Why:** `value: T` is now required, and `qualifyEvidence` never emits a nullish value. It matters only for untyped inputs. [O/I]
- **Depends on:** none
- **Confidence:** medium
- **Fix class:** none required.

### D4 — `DualSdrFace.component.test.ts`: the comment "known state — 'LSB' lights the still-reserved 'WIDE-F3' slot." is false, and predates this PR
- **Verdict:** dead prose. The test makes no such check. It landed in 990ca4dc (#3760), per `git log -S`. [O]
- **Depends on:** none
- **Confidence:** high
- **Fix class:** delete, or make it true with Fix 1.

## Consolidations
### F1 — Observation → value-or-nothing: complete in the touched files
- **Verdict:** already shared here; still in flight elsewhere.
- **Rank:** parallel
- **Elements and consumers:** `observationValue`, with 15 call lines in 5 files, against the remaining copies listed in Q1.
- **Definition site:** `primitives/reading-text.ts`
- **Divergence:** none at the migrated sites.
- **Prior ruling:** design audit Q3 and Q6; owner decision of 2026-09-27.
- **In-flight:** S4d, S5.
- **Required surface:** exists.
- **Depends on:** none
- **Confidence:** high
- **Falsifier:** a value decision I classified as behaviour or status output.
- **Fix class:** consolidate. **Actionable:** in S4d and S5.

### F2 — `ValueObservation`: S4a's F2 is closed
- **Verdict:** already shared.
- **Rank:** parallel. The state set is still written twice, but the compiler now ties the two copies together.
- **Divergence:** none.
- **Residual:** the tie is one-directional (Q2).
- **Confidence:** high on the config; medium on TypeScript behaviour.
- **Falsifier:** svelte-check not checking `.test.ts` files under `tsconfig.app.json`.
- **Fix class:** none.

### F3 — Status twins of the absent rule stay local
- **Verdict:** C
- **Rank:** diverged, by design.
- **Elements:**
  - VfoSurface: the `frequencyState={…state ?? (frequencyHz == null ? 'unknown' : 'current')}` prop and `data-display-state`;
  - VfoIndicatorRow: `data-display-state={display?.state ?? (known ? 'current' : 'unknown')}`;
  - Cluster: `obsState`, which collapses stale to current.
- **Prior ruling:** design audit R2 (keep these outputs verbatim).
- **Fix class:** none. **Actionable:** no.

### F4 — The RFG rule is written twice
- **Verdict:** C for now.
- **Rank:** parallel, identical behaviour.
- **Elements:** `VfoIndicatorRow: rfGainShown` + `rfGainText`, against the VfoSurface `standardPanelSections` rfg block. Both now use the same `observationValue(display, readingValue(field))`, then `levelFormatsBelowMax` + `formatKnownLevel` over `RF_FRONT_END_LEVELS[0]`. [O]
- **Prior ruling:** owner, 2026-09-23.
- **Actionable:** no. Two sites do not meet the rule of three.

### F5 — The frequency lock diverges when `display` is absent (predates this PR)
- **Verdict:** C
- **Rank:** diverged; unreachable through the adapter.
- **Elements:** `RIH: frequencyDisabled` locks (`undefined !== 'current' && …`); `VfoSurface: readoutDisabled` does not. [O]
- **Prose:** RIH's comment says "the same rule `VfoSurface.svelte: readoutDisabled` applies", which is false for this case.
- **Actionable:** low.

## Weakest link
D2's claim that the non-null `absent` fallbacks never occur in production. It rests on producers I cannot see. Check first:
- any producer of `RadioViewModel.vfos` or `receiverIndicators` outside `toRadioViewModel` and `deriveReceiverIndicators`, including the Pro tier;
- whether DualSdrFace mounts a cluster for a receiver that has no VFO record.

## Cleared
- One rule in the touched files, with no new copy.
- The union matches both real types exactly.
- The tie is inside CI's type check, and fails as claimed.
- `absent` never falls back for a present observation, and every caller's fallback is unchanged.
- Behaviour is identical at every site; `obsState`'s output set is unchanged.
- Layering and module purity.
- The census.
- D4 (the Cluster comment) is deleted; the `displayValue` deletion leaves no dangling reference.
- The 3a sweep is clean.
- VfoIndicatorRow and VfoSurface are pinned; RIH's pin claim is defensible.

## Fix in this PR
1. **Cluster literal pins (test only; 9 of 10 files).** `obsText`, `obsState` (`data-state`), `frequencyText`, `frequencyCh` and `bandwidthCh` have no pin in any state where a value is present. Required states:
   - absent observation with a legacy value;
   - current or stale;
   - present unknown with a legacy value.
   - Each must fail when `observationValue` ignores `absent`.
   - No open PR touches `skins/dual-sdr-face/`. [O]
2. **D1:** the three citations.
3. **PR body** (no new head):
   - replace the guardrail rationale with a true one-unit statement;
   - the #3782 shared-file statement;
   - "widened";
   - the list of RF-gain pins that fail under the mutation;
   - keep the identity claim's evidence within what is pinned, or cite Fix 1.

## Carry forward
- **S4d:**
  - `meter-renderer-view.ts` ×3, which also needs the finite check;
  - D3 of S4a: `MetersSurface: swrLowerScale`, S4a's weakest link;
  - the MetersSurface S-meter tile's finite half.
- **S5:** VfoPanel's `frequencyState` gates (outside the census) and the legacy panel guards.
- **MOR-2704:** `VfoSurface: sValue` and its band `operational && known`; `RIH: meterMotionInput`; the MetersSurface tile's operational half.
- **No owner:**
  - SpectrumPanel's passband;
  - `FilterSurface: numberOf`;
  - VfoSurface's lamp chips (band, ANT, BW, AGC, `levelLamp`, `levelSuffix`, split), which still read `reading.status` in drawing code;
  - S4b's unowned V3 sites;
  - S6;
  - the `status()` suffix builders.
- **Design decisions:**
  - whether `display` becomes required (D2);
  - F4 once a third RFG site exists;
  - F5 and its comment;
  - D3.
- **Tests:** "a present observation never falls back" is pinned for `unknown` only, not for `unsupported` or `idle`; both are unreachable at the current callers. D4.
- **Low-severity prose:** the Q8 items marked low.

Key files:
- `frontend/src/primitives/reading-text.ts`
- `frontend/src/semantic/__tests__/reading-text-type-tie.test.ts`
- `frontend/src/skins/dual-sdr-face/ReceiverInstrumentCluster.svelte`
- `frontend/src/skins/dual-sdr-face/__tests__/DualSdrFace.component.test.ts`
- `frontend/tsconfig.app.json`
- `.github/workflows/quick.yml`
- `frontend/src/lib/runtime/adapters/radio-view-model-adapter.ts`