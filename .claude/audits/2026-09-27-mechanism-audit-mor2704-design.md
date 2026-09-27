# Mechanism audit: MOR-2704 design, one fail-closed "usable" gate

- **Revision:** `523e2174`. Detached HEAD in its own worktree. `git status --short` is empty.
- **Method:** `.claude/skills/mechanism-audit/SKILL.md`, read in full. I followed steps 0–5 in order and did the 3a sweep and the step-4 steelman before any verdict. Policy: `docs/internals/coordinator-policy.md` in the same worktree.
- **What I ran:** read-only git (including `git log -S`), grep, awk and sed, plus one census script (not tracked). No tests, builds, npm, `gh` or Linear.
- **The report file `tmp/audit_mor2704_design.md` was not written.** My role forbids writes to the audited tree and forbids report files. `CLAUDE.md` ("Agent working rules") also keeps detailed reports outside every worktree, including ignored directories. This message is the whole report, with the summary first.
- **Linear:** not read. The MOR-2704 text and the MOR-2688 ruling are quoted from the dispatch.
- **Paths:** relative to `frontend/src` unless prefixed.
- **Coverage:** every file was read in targeted ranges; none was read in full. The branches of the open PRs were not opened.
- **Labels:** [O] means observed in code, [I] means inference.
- **Text that directs an agent:** none found.

## Summary (≤40 lines)
1. **Count [O].** The ticket's grep `grep -rnE "(export )?const usable = " frontend/src` finds 19 lines: 17 production copies, all in `semantic/`, plus 2 test fixtures. The 17 copies are called on 69 lines:
   - 23 compute `data-observed`;
   - 7 feed value text;
   - the other 39 gate actions, disabled-reason metadata, or status attributes.
2. **Wider census, any name [O].** I counted `.operational` within ±2 lines of a `status === 'known'` test, in non-test files: 37 lines in 31 files.
   - 1 is the original, `canInvoke`.
   - 27 are full copies: the 17 `usable`, 6 `acceptedNumber`/`acceptedBoolean`, 2 inline in `RxAudioInstrumentHost`, `DualSdrFace: preEnabled`, and `vfo-operation-projection.ts: functionOperation`.
   - 9 are "observed" predicates (`operational && known`, no `structural`). They only draw; none gates an action.
3. **Equivalence [O].** Every copy equals `canInvoke({ field })` on every input its types allow. The differences are form only: how `undefined` is handled, `=== true`, and no `blocked` input. The ticket's "no copy differs in behaviour" holds.
4. **The ticket's causal claim is partly refuted [O].** 8 copies (the `*Surface` files, 2026-08-05/06) predate `canInvoke` (96bf5b7e, 2026-09-05). The other 9 (the `*Host` files, 09-06 to 09-08) appeared while it was unexported.
5. **Recommended home [O/I].** Keep the one definition in `primitives/control-instruments/control-instrument-behavior.ts`.
   - Export a field-level `usable` function marked `/** @internal */`, and make `canInvoke` = `blocked !== true && usable(field)`.
   - The kit build strips `@internal` (`stripInternal: true`). `continuous-scalar.svelte.ts` plus the negative asserts in `verify-package.mjs` are the precedent.
   - A new sibling module avoids nothing: it would still ship in the kit tarball.
6. **Import allow-lists [O].** Five surfaces carry import allow-list tests; 3 of them hold a copy.
   - `CwKeyerSurface` already allow-lists the behaviour module.
   - `BandSurface` and `RxAudioSurface` would each gain one allowed import.
   - The behaviour module has no imports today, but nothing pins that. A "no runtime import" test must land first.
7. **Text predicate: recommend option A.** Value text follows value-or-nothing (reading status only); `operational` decides only whether the control acts.
   - Nothing changes on screen today [O]. Every field at every affected text site is built by `txAuxField` or `meterField`, and both emit `known` only when the field is operational.
   - The only producers of "read but not operational" already show the value and refuse the action: `pbt-presentation-continuity.ts: retainedField` and `vfo-operation-projection.ts: functionOperation` [O].
   - No capture state or placeholder-guard state produces that case [O/I].
8. **Related copies.**
   - `acceptedNumber`/`acceptedBoolean` = the gate plus a range check, returning a value or `null`. They should become one definition, but not in `spectrum-toolbar-logic.ts`: a comment in VfoHeader records avoiding a layout → spectrum import.
   - `activeTuneReceiver` is a different mechanism (which receiver a tune goes to), not the gate.
9. **PR plan.**
   - G0: export the gate (3 files).
   - G1–G4: behaviour-identical migrations. G3 edits the allow-listed surfaces and is a safety edit. G4 waits for the MOR-2688 S4b PR.
   - G5–G6: the related copies.
   - T1–T2: the behaviour change, after an owner decision. T2 belongs to MOR-2688.
10. **Dead code.**
    - The `export` on 6 local `usable` has no importer (D1).
    - `data-observed` in ScopeDisplaySurface and CwKeyerInstrumentHost has no CSS consumer (D2, undetermined).

## Premises tested (dispatch claims, treated as hypotheses)
- **P1, "17 files grew copies because `canInvoke` is not exported".**
  - The count is confirmed [O].
  - The cause is refuted for 8 of the 17 [O]. `git log -S "const usable = "` gives the first date per file:
    - the 8 `*Surface` files: 2026-08-05/06;
    - `canInvoke`: 2026-09-05, commit 96bf5b7e "#3233 share action and choice instrument behavior";
    - the 9 `*Host` files: 2026-09-06 to 09-08.
  - The ticket's "exists once" also holds only for the field form of the gate. `canInvokeAction`'s availability form checks no reading [O].
- **P2, "no copy differs in behaviour".** Confirmed for the gate itself (Q2).
  - Inline sites add extra conditions that a migration must keep: authority, target, muted, `isFinite`, callback [O].
- **P3, "`verify-package.mjs` pins the kit surface".** Only partly.
  - What it pins [O]:
    - the four shipped declaration files are present, including `control-instrument-behavior.d.ts`;
    - the root runtime exports are exactly `COMPONENT_KIT_API_VERSION` and `defineComponentKit`;
    - deep subpaths cannot be imported.
  - It never reads the content of `control-instrument-behavior.d.ts` [O].
  - The design audit's R6 says anything added to that file becomes public API. The `@internal` mechanism and the `exports` map contradict that [O].
- **P4, "Five surfaces carry import allow-list tests".** Confirmed [O].
  - `semantic/__tests__/{Antenna,Band,CwKeyer,Memory,RxAudio}Surface.test.ts`.
  - The only other source-specifier pin is a manifest test in `semantic-mobile-migration.component.test.ts`.
- **P5, the listed cases where text and action use different predicates.** All confirmed as code facts [O].
  - No production input tells the two predicates apart at those sites. The exception is PBT, where the split is deliberate (Q4).
- **P6, design audit R1: "for adapter-built fields, status alone equals `usable`".** Confirmed [O].
  - Code under `lib/runtime/adapters/` builds `availability` in exactly 3 places: `txAuxField` (87 calls), `meterField` (6 calls), and the receiver-indicator container.
  - `validateTxAuxField` and `validateMeterField` do not enforce "known implies operational".

## Q1 Inventory
**Counting rule.** A gate site is a production expression (not test, not fixture) that combines a field's `availability.operational` with `reading.status === 'known'`, with or without `structural`, whether named or inline. Commands were run in `frontend/src`:
- **C1:** `grep -rnE "(export )?const usable = " .` gives 19 lines in 19 files. Subtract the 2 fixtures in `primitives/control-instruments/__tests__/` to get 17.
- **C2:** `grep -rn '\.operational' . --include='*.ts' --include='*.svelte' | grep -v __tests__ | grep -v '\.test\.'` gives 92 lines in 42 files.
- **C3:** C2 lines with `status (===|!==) '(known|unknown)'` within ±2 lines (awk) give 37 lines in 31 files. This is a literal search.
- **C4:** `usable(` calls in the 17 files, excluding comments and definitions, give 69 lines.

**A. The 17 `usable` copies.** All test `structural && operational && known` [O].

Key: **A** = action (dispatch, `disabled`, editable), **R** = disabled-reason metadata, **T** = value text, **D** = drawing (`data-observed` italic, status attributes, thumb position).

| File (form) | Uses [O] |
|---|---|
| `RitXitScanSurface` (exported) | `decodedOffset` → T `ritxit-offset-value`, D thumb, A key handler; `canAdjustOffset` → A; `scanningOn` → T START/STOP, A in the toggle's `disabled`, D ΔF block; D `data-observed` ×2 |
| `ScopeControlsSurface` (exported) | **T only**: `spanText` (2 outputs), inline span (finite row), speed ×2. Actions go through `canInvoke` via `bindActionInstrument` / `createActionRendererSeat` |
| `RxAudioSurface` (exported) | D `data-observed` ×2 |
| `FilterSurface` | R `reasonOf` + the NARROW key's reason; D `presentationOf`, `pbtDisplay`; A `pbtUsable`, width `enabled`, `changePassband`, `passbandInput.enabled` (also gates the scalar's evidence reading) |
| `ScopeDisplaySurface` | D `data-observed` ×1 (no CSS consumer) |
| `CwKeyerInstrumentHost` (accepts `undefined`, `=== true`) [S4b] | A handler + `enabled`; R 'Not yet observed'; D `data-observed` (no CSS) |
| `DspSurface` | R `reasonOf` + manual notch reason; `nrPresentation` → T NR `<output>`, D thumb, A; A handler, `disabled` ×2 |
| `FilterInstrumentHost` (accepts `undefined`) | R `reason` ×7; D `data-data-mode-status` ×2, `data-mod-input-status`; A `disabled` |
| `TxAuxScalarHost` [S4b] | R `reasonText`; A handler, `enabled` |
| `CwKeyerSurface` (exported) | A handlers ×2, `phase`/`breakInDelayEditable`, `disabled` ×2; D `data-observed` ×5 |
| `RfFrontEndInstrumentHost` (accepts `undefined`, `=== true`) [S4b] | A handler, `enabled`; D `data-observed` ×3 |
| `TxAuxFiniteHost` | R `reasonOf`, `reasonTextOf` (title + screen-reader text) |
| `DspScalarHost` (accepts `undefined`) [S4b] | A `enabled` ×2 |
| `RxAudioInstrumentHost` | D `data-observed` ×6 (inline gates are in table B) |
| `DspInstrumentHost` (takes a key: `dsp?.[field]`) | R ×1 (its toggles go through `bindToggleInstrument`) |
| `BandSurface` (exported) | D `data-observed` ×1 |
| `AntennaInstrumentHost` (exported) | A `tunerIdle` (the antenna block); D `data-observed` ×2 |

**B. Other full copies [O].**
- **`acceptedNumber` / `acceptedBoolean`** (the gate plus a range check, returning a value or `null`).
  - `VfoHeader`: A (receiver and dual keys), D `class:active`, T in the `.scope-digest` span, which prints `'\u2014'` when the value is `null`.
  - `ScopeSettingsPopover`: A and D only.
  - `SpectrumToolbar`: A, D, and T for span, speed, ref ×2 and the receiver label.
- **`RxAudioInstrumentHost`:** 2 inline copies inside the AF `enabled` expressions (A).
- **`DualSdrFace: preEnabled`:** A. Its text uses `readingText`.
- **`vfo-operation-projection.ts: functionOperation`:** A. It is folded into `availability.operational`, which `VfoOperationGroup` reads for `disabled`.

**C. Observed-form predicates (`operational && known`), 9 sites, drawing only [O].**
- `MetersSurface: observed`, `StationMeterInstrumentHost: observed`, `HostedFaceInstrumentBridge: observed`.
- `bar-meter-projector.ts: observed` [4b].
- `ReceiverInstrumentHost: meterMotionInput`.
- `VfoSurface`: band `known` lights the lamp while the band text is status-only; `sValue`.
- `radio-display-model.ts: displayValue`, `tx-meter-display.ts: projectTxMeterDisplay`.

**D. Not copies (rest of C2) [O].**
- **Availability-only checks** (`structural && operational`, no reading — the shape of `canInvokeAction`'s availability form):
  - `ActiveReceiverToggle`;
  - `vfo-operation-projection.ts`: `admitted`, `receiverOption`, the dispatch guard;
  - `RfFrontEndInstrumentHost` lane availability;
  - `RxAudioInstrumentHost` `channelGainInput`;
  - `AmberCockpit: handleQsyRecall`.
- **Consumers of an availability that is already computed:** `VfoOperationGroup` (12 `disabled` bindings), `VfoOperationSeatHost`.
- **Receiver-level checks:** `ReceiverInstrumentHost: frequencyDisabled`, `panel-adapters.ts` ×4, `dual-receiver-strips.ts: isOperationalStrip`.
- **Producers, validators and attributes that only pass the flag through.**

## Q2 `canInvoke` / `canInvokeAction`
- **`canInvoke` [O]:** `blocked !== true && field !== undefined && structural && operational && reading.status === 'known'`.
  - It is a type guard: on true it narrows `field.reading` to known.
  - It has no finiteness test.
- **`canInvokeAction` [O]:**
  - With `availability` input: `blocked !== true && availability !== undefined && structural && operational`, with no reading check.
  - Otherwise it calls `canInvoke`.
- Neither is exported.

| Input | `canInvoke({field})` | 12 field-only copies | 4 that accept `undefined` | DspInstrumentHost | 9 observed-form | `accepted*` |
|---|---|---|---|---|---|---|
| field `undefined` | false | the type excludes it | false | false | callers guard | null |
| structural false, operational true, known | false | false | false | false | **true** | null |
| structural true, operational false, known | false | false | false | false | false | null |
| operational true, unknown | false | false | false | false | false | null |
| known `NaN` | true | true | true | true | true (5 callers add `isFinite`) | null |

- The observed form's one divergent row (structural false, operational true) cannot come from the adapter [O]. Both builders compute `operational = structural && …`.
- `=== true` against truthiness makes no difference for booleans. The validator's `bool()` enforces booleans [O].
- No copy diverges from `canInvoke`.

## Q3 Location, name, kit and allow-lists
**Where [O/I].** In `primitives/control-instruments/control-instrument-behavior.ts`, where the gate is already defined.
- **Primitives is the only layer every holder may import** [O]:
  - ADR table: semantic may import "adapter types, primitives"; skins may import "semantic, primitives".
  - eslint: `FORBIDDEN_SEMANTIC_IMPORTS` and `FORBIDDEN_RUNTIME_IMPORTS` do not ban primitives. Only `presentation/workspace(s)` bans it.
- **Kit mechanics** [O]:
  - The kit's `tsconfig.json` includes only `src/index.ts` and sets `stripInternal: true`.
  - `index.ts` type-imports `InstrumentReading` from this module, so its `.d.ts` ships.
  - `package.json` `exports` has only `"."`.
- **A new sibling module saves nothing** [I]. tsc emits a `.d.ts` for every file in the program, so a module that `control-instrument-behavior.ts` imports would ship too. The behaviour module would also gain its first import (it has 0 today [O]).

**Kit pin to add.** Follow the `continuous-scalar.svelte.ts` precedent [O]: `/** @internal */` plus `verify-package.mjs` asserting the shipped declaration does not contain the name, as it already does for `createContinuousScalar(`.

**Name: `usable`.** Declare it as a field-level `function` that accepts `undefined` and narrows `reading` to known.
- The precedent strips `export function` and `export interface`. Whether an `export const` form is stripped is unproven here [I].
- `canInvoke` then delegates to it.
- The 69 call lines stay as they are.
- These 4 comments stay true [O]:
  - the docstring in `disabled-reason.ts`;
  - `ScopeControlsSurface: numberOf`;
  - the `selectedType` comment in `RitXitScanSurface`;
  - `FilterSurface: pbtUsable`.
- **Cost:** the two test fixtures named `usable` must be aliased or renamed wherever a test imports the new export.
- Exporting `canInvoke` itself would reshape every call to `canInvoke({ field })`.

**Allow-lists [O].**

| Pinned file | Holds a copy | Import change |
|---|---|---|
| `BandSurface` | yes (`data-observed` only) | +1 specifier; the expected list is ordered, not sorted |
| `RxAudioSurface` | yes (`data-observed` only) | +1 specifier; the expected list is sorted |
| `CwKeyerSurface` | yes | none; the behaviour module is already allowed |
| `AntennaSurface` | no | none. The copy lives in `AntennaInstrumentHost`, which already reaches the module through `control-instrument-renderer.svelte.ts` |
| `MemorySurface` | no | none |

**What a PR that adds the import must prove.**
1. A "has no runtime import" pin on `control-instrument-behavior.ts`, like the ones in `pressed-of.test.ts` and `reading-text.test.ts`. None exists [O]. It must land first.
2. Each allow-list gains exactly one specifier, with a rationale comment in the MOR-1474 / MOR-2688 S3 style.
3. The lifecycle-hook and forbidden-word tests pass unchanged. The new import line contains none of the forbidden words [O].
4. The imported symbol is a pure predicate.
5. Existing assertions pass unchanged, plus one test with a field that is read but not operational.

Note that the ticket's "every existing gate test passes unchanged" cannot hold literally for this slice: 2 allow-list expectations must change.

## Q4 The text predicate
**Case for B (operational false ⇒ show nothing).**
- The contract in `radio-view-model.ts: Availability` (MOR-977) says a control failing only the operational half "stays present but disabled, degrading to an explicit unknown/disabled state".
- `disabled-reason.ts` defines `operational: false` as "its reading has not arrived yet".
- `txAuxField` treats operational as a precondition of known [O].
- Blanking the value is fail-closed against a future re-projection that marks a field non-operational.
- Plain text sites have no "not live" style. A retained value would look live there.
- The S3 audit ruled out widening.

**Case for A (show the value; operational only decides action).**
- The ruling keeps read/unread "only where it decides behaviour … command gating"; text is drawing.
- `radio-view-model.ts` defines "Operational = usable right now" [O], which is an action concept.
- The only real producer, `pbt-presentation-continuity.ts: retainedField`, exists to show a retained value while refusing action. Its docstring: "never becomes radio truth or a commandable control". `FilterSurface` prints it in the `pbt-value` output [O].
- `VfoOperationGroup` shows state words from the reading while `disabled` follows operational [O].
- Most sites already split this way:
  - REF text;
  - the RIT/XIT readouts;
  - the scan status text;
  - IF shift;
  - the CW keyer and TX-aux scalar readings;
  - `DualSdrFace`'s pre-amp text;
  - `VfoSurface`'s band text.

**Recommendation: A.** Keep the status attributes (`data-observed`, `data-presentation`, the `*-status` attributes) on the gate, so a value that is shown but not live stays marked where a style exists. The premise "known implies operational" is currently unpinned (P6).

**Site by site, under A.** Every field below comes from `txAuxField`/`meterField` [O]. When it is not operational it is also unknown, so it stays blank under both predicates: **no on-screen change on any radio today** [O/I].

| Text site | Field builder [O] | When `operational` is false |
|---|---|---|
| ScopeControls span (`spanText` + inline) and speed ×2 | `deriveScopeControls` → `txAuxField(true, topFieldAvailable('scopeControls.*'))` | IC-7300 and IC-7610 (they declare `"scope"`): disconnected, or the field is not yet read. FTX-1 has no `"scope"`, so there are no scope controls at all [O rigs] |
| RitXit `ritxit-offset-value` text and thumb | `txAuxField(hasRitCap/hasXitCap, offsetObserved, …)` | offset not observed. Which radios have the capability: not checked (unknown) |
| RitXit START/STOP label | `txAuxField` (scan) | `scanning` not observed |
| DspSurface NR `<output>` | `txAuxField(hasNrCap, nrLevelReadable, …)` | `nrLevel` unreadable |
| VfoHeader span/speed digest; SpectrumToolbar span/speed/ref/receiver | the same scope fields | as above; the dual/receiver keys also need `dual_rx`, which IC-7300 lacks [O] |
| Observed family (C) | `meterField` / `txAuxField` (`sMeter`, band) | meter absent, receiver not operational, not yet read, disconnected |

**Captures and the placeholder guard [O/I].**
- The guard states `all-unread` (state `null`), `all-unsupported` and `all-unsupported--reading` all load over the real adapter. `fixtures/assertions.ts` imports `toRadioViewModel`.
- `offenders.json` has 0 entries.
- `connection-loss-stale` is fine: a stale-but-observed field resolves to 'available' in `getFieldAvailability`, so it is operational and known together.
- No fixture drives PBT retention: "pbt" appears only in a capability list and in stubs.

## Q5 The related copies
- **`acceptedNumber` / `acceptedBoolean` [O].**
  - What they are: the gate, verbatim, plus a range check (`Number.isSafeInteger` within [min, max], or `typeof boolean`), returning a value or `null`.
  - One value per field feeds action, the lit state and text at once, so they are coherent. The gate part is the same mechanism as `canInvoke`; the range part is scope-specific and separate.
  - They are three identical copies (a diff after renaming `scopeFacts` to `scopeControls` shows no difference), so the rule of three is met.
  - All three read `toSpectrumAuthority(...).scopeControls`.
  - Where one definition could live:
    - Not `components/spectrum/spectrum-toolbar-logic.ts`. VfoHeader's comment records "to avoid a reverse dependency from layout/ → components/spectrum/".
    - A pure `semantic/` module that calls the exported gate. Precedent: AmberCockpit already imports `semantic/format-level` [O].
    - Not `scope-adapter.ts`: the ADR's list for adapters excludes primitives [O].
- **`activeTuneReceiver`: a separate mechanism [O].**
  - It routes tune commands. It returns receiver 0 or 1, or `null` when:
    - the active receiver is unknown;
    - `receiver.X` is reported capability-unavailable;
    - there is not exactly one active VFO;
    - the frequency is unknown;
    - the slot cannot be tuned.
  - It checks no field's availability-and-reading pair. It is used for action only.
  - The AmberCockpit and EiBiBrowser copies are byte-identical over 20 lines.
  - Its receiver check duplicates `dual-receiver-strips.ts: isOperationalStrip` in intent. The two are equivalent today because the adapter emits only 'capability-unavailable' for `receiver.X` [O].
  - Home: next to `isOperationalStrip` (that file is under a file-scoped purity lint [O]) or in `semantic/`. Neither importer has an eslint ban on those paths [O].

## Q6 PR plan
All paths are under `frontend/`. None of these PRs touches a file in the open PRs.

| Slice | Files | Pins | Allow-listed? |
|---|---|---|---|
| **G0** export the gate | `src/primitives/control-instruments/control-instrument-behavior.ts`, its `__tests__/control-instrument-behavior.test.ts`, `component-kit-api/verify-package.mjs` (3) | edge cases: undefined, structural false, operational false, unknown, NaN; the purity pin; the kit negative assert; existing tests unchanged (fixture renamed) | no |
| **G1** surfaces | `RitXitScanSurface`, `FilterSurface`, `ScopeDisplaySurface`, `DspSurface` + their `.test.ts` (8) | per file, one read-but-not-operational case: action refused; `data-observed`, `data-disabled-reason` and PBT 'retained' unchanged. Text assertions wait for T1 | no. ScopeDisplaySurface gains its first primitives import |
| **G2** hosts | `FilterInstrumentHost`, `TxAuxFiniteHost`, `RxAudioInstrumentHost` (named copy + 2 inline), `DspInstrumentHost`, `AntennaInstrumentHost` + 5 isolated tests (10) | same; `tunerIdle` is the antenna block, so review it as safety-adjacent | no |
| **G3** allow-listed surfaces | `BandSurface`, `RxAudioSurface`, `CwKeyerSurface` + their `.test.ts` (6) | Q3 list; depends on G0's purity pin | **yes, safety edit** |
| **G4** after S4b merges | `TxAuxScalarHost`, `CwKeyerInstrumentHost`, `DspScalarHost`, `RfFrontEndInstrumentHost` + their isolated tests (8) | same | no |
| **G5a** `accepted*` | new shared module + its test, `VfoHeader`, `ScopeSettingsPopover`, `SpectrumToolbar`, `vfo-header.isolated.test.ts`, `ScopeSettingsPopover.authority.isolated.test.ts`, `SpectrumToolbar.component.test.ts` (8) | behaviour identical | no |
| **G5b** `activeTuneReceiver` | shared home + test, `AmberCockpit`, `EiBiBrowser`, `AmberCockpit.qsy-authority.isolated.test.ts`, `EiBiBrowser.authority.isolated.test.ts` (6) | behaviour identical | no |
| **G6** inline copies | `DualSdrFace.svelte`, `vfo-operation-projection.ts` + `DualSdrFace.component.test.ts`, `vfo-operation-projection.test.ts` (4) | behaviour identical | no |
| **T1** behaviour change (option A) | `ScopeControlsSurface` (4 text expressions; its copy is then unused and deleted), `RitXitScanSurface`, `DspSurface` + tests (6) | read-but-not-operational: value shown, control disabled. Depends on the owner's decision, S4b (`reading-text.ts`) and G1 | no |
| **T2** behaviour change (MOR-2688) | table C sites + tests, split into 2 PRs; `bar-meter-projector` after 4b | same | no |

**Acceptance counts.**
- Before [O]:
  - C1 finds 19 lines;
  - C3 finds 37 lines in 31 files;
  - C4 finds 69 lines;
  - `function accepted(Number|Boolean)` has 6 definitions;
  - `function activeTuneReceiver` has 2.
- After the G slices [I]:
  - C1: 1 production definition plus 0–2 fixtures;
  - C3: 10 (the original + table C), then 1 after T2.

## Q7 Met on the way
- **D1:** dead `export` on 6 local `usable` (below).
- **D2:** `data-observed` attributes with no visual consumer (below).
- **Comments that go stale after G2 [O]:** the `RxAudioInstrumentHost` docstring above its `usable` says "Deliberately re-declared rather than imported from `RxAudioSurface.svelte` … (both declare their own `usable`)".
- **Prior audit's R6 [O/I]:** "anything added there becomes public API" is contradicted by `@internal` + `stripInternal` and by the `exports` map.
- **VfoHeader [O]:** the scope digest prints `'\u2014'` for unread span/speed, against the owner rule. It sits in RadioLayout's non-`semanticDeck` branch; whether anyone reaches that branch is unknown.
- **`reading-text.ts` docstring:** "R1 pins it" is an overstatement. S4a already reported it, and the file is in the open S4b PR.

## Deletions
### D1: `export` on 6 local `usable`: dead
- **Verdict:** dead (the `export` modifier only).
- **Elements:** the module-script `export const usable` in RitXitScanSurface, ScopeControlsSurface, RxAudioSurface, CwKeyerSurface, BandSurface and AntennaInstrumentHost.
- **Consumers:** none outside their own files.
- **Written / read:** 6 exports, 0 imports. Literal greps: `import {…usable…}`, `^\s+usable,`, the multi-line import blocks of the 6 test files, and `import * as` of these modules.
- **Guards checked:**
  - dynamic access: none found (the search was literal);
  - out-of-repo: the Pro contract `lib/local-extensions/` has 0 references;
  - public API: the kit's `index.ts` has 0 references to `semantic/`;
  - tests-only: none.
- **Collateral:** none. **Depends on:** F1.
- **Confidence:** high. **Falsifier:** a consumer outside this repo that imports the component module script.
- **Fix class:** delete, as part of G1–G3.

### D2: `data-observed` with no visual consumer: undetermined
- **Verdict:** undetermined.
- **Elements:** `ScopeDisplaySurface` (the SRC indicator) and `CwKeyerInstrumentHost` (its level rows).
- **Consumers:** no CSS anywhere. The rule `[data-observed='false']` exists in only 8 other files [O]. ScopeDisplaySurface's test mentions it; CwKeyerInstrumentHost's tests: unknown.
- **Guards checked:** out-of-repo styling cannot be ruled out.
- **Collateral:** tests. **Depends on:** none.
- **Confidence:** low. **Falsifier:** a Pro or skin stylesheet that targets it.
- **Fix class:** delete, only if that guard is cleared.

## Consolidations
### F1: field gate, "may this control act": 17 copies + 4 inline
- **Verdict:** A, displaced. The migration is incomplete: the target exists but is unexported.
- **Rank:** parallel.
- **Elements:** tables A and B (rows for RxAudioInstrumentHost, DualSdrFace, vfo-operation-projection).
- **Consumers:**
  - each copy: its own file only;
  - `canInvoke` itself: the binders only, which are used in 5, 9 and 10 production files (`bindActionInstrument`, `bindToggleInstrument`, `bindChoiceInstrument`).
- **Definition site:** `control-instrument-behavior.ts: canInvoke`.
- **Divergence:** none (Q2).
- **Prior ruling:** the RxAudioInstrumentHost comment above ("deliberately re-declared"; no date or id). Its reason was to avoid importing the paired surface; a primitives export removes that reason.
- **In-flight:** S4b owns 4 of the copy files.
- **Required surface:** `@internal` export `usable` + a kit negative assert + a purity pin (Q3).
- **Depends on:** none for G0; S4b before G4.
- **Confidence:** high. **Falsifier:** a copy that differs on a typed input, or a kit build that does not strip the `@internal` function.
- **Fix class:** consolidate. **Actionable:** yes.

### F2: text gated by the action gate
- **Verdict:** undetermined; needs an owner decision. Recommendation: A.
- **Rank:** diverged, but latent. REF text shows a read-but-not-operational value where SPAN would not, yet no producer feeds either site that input.
- **Elements:** Q4 table.
- **Prior ruling:** MOR-2688 (2026-09-27, from the dispatch); `pbt-presentation-continuity.ts` docstring; MOR-977 in `radio-view-model.ts`.
- **Required surface:** exists (`reading-text.ts`).
- **Depends on:** the owner's decision, S4b, G1.
- **Confidence:** medium. **Falsifier:** a producer of read-but-not-operational feeding a text site.
- **Fix class:** design. **Actionable:** after the decision.

### F3: `acceptedNumber` / `acceptedBoolean` ×3
- **Verdict:** A. **Rank:** parallel. **Divergence:** none.
- **Prior ruling:** VfoHeader's layout → spectrum avoidance.
- **Required surface:** one pure module that both `components-v2/layout/` and `components/spectrum/` may import.
- **Depends on:** F1 (G0). **Confidence:** medium on the home.
- **Fix class:** consolidate. **Actionable:** yes.

### F4: `activeTuneReceiver` ×2
- **Verdict:** A. It is a separate mechanism from the gate.
- **Rank:** parallel. **Divergence:** none.
- **Required surface:** one pure function over `RadioViewModel`.
- **Depends on:** none. **Confidence:** medium on the home.
- **Fix class:** consolidate. **Actionable:** yes.

### F5: observed drawing predicates ×9
- **Verdict:** C for MOR-2704. They are not the action gate and have no action use; they belong to MOR-2688's value-or-nothing rule.
- **Rank:** parallel.
- **Depends on:** F2, S4b, 4b. **Confidence:** high.
- **Fix class:** consolidate (T2). **Actionable:** outside MOR-2704.

### F6: availability-only checks (table D)
- **Verdict:** C / out of scope.
- **Rank:** parallel.
- **Depends on:** none. **Confidence:** medium.
- **Fix class:** none. **Actionable:** no; not needed for MOR-2704's acceptance.

## Weakest link
F2's claim of "no on-screen change" rests on a literal search for code that re-projects a field as read but not operational. The search found 2 such producers. A producer built through a helper that computes `operational` under other vocabulary would be missed. Check first: every non-test object literal that carries both `reading` and `availability`, in `semantic/`, `components-v2/wiring/`, `skins/` and `component-kits/`.

## Cleared (examined, healthy)
- `canInvoke` / `canInvokeAction`: a single definition. The renderer seats reach it through `bindActionInstrument` [O].
- The adapter ties "known" to "operational" in both field builders [O].
- The FilterSurface PBT split (text shown, action refused) is deliberate and consistent.
- The five allow-list premises hold at runtime. `radio-view-model.ts` imports `tx-permit` as a type only [O].
- No lint plugin references the gate: `radio-authority-eslint-plugin.mjs` has 0 hits [O].
- `vfo-operation-projection` computes the gate once; `VfoOperationGroup`'s 12 bindings consume it and are not copies.
- `disabled-reason.ts` is one wording module with 4 callers.
- `CwKeyerInstrumentHost`, `TxAuxScalarHost` and `DualSdrFace` already split text (status) from action (gate).
- Step-3a sweep of `control-instrument-behavior.ts`:
  - 12 exports: 7 have production readers elsewhere; 5 types are used only in the module's own signatures, which ship in the kit's declaration file;
  - 9 internal names: all referenced.
- The gate's neighbouring helpers in the 17 files all have readers, apart from D1.