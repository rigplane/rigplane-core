Method: `.claude/skills/mechanism-audit/SKILL.md`. I followed it in order, steps 0 to 5 including 3a. I also read `docs/internals/coordinator-policy.md` in the same worktree.

**Revision:** `6eac5b53` (detached, clean tree). Merge base is `f8166086`, which is contained in `origin/main`. The scope is `git diff origin/main...HEAD`, 5 files. I made no edits, ran no tests and did no git writes. On GitHub I only read (`gh pr view 3761`). I could not read Linear, so the MOR-2688 design comment is known to me only through the dispatch summary, and I did not verify it. Labels below: (obs) means observed, (inf) means inferred. Source paths are relative to `frontend/src/`.

**Claims tested, taken from the PR body, not accepted as premises:**
1. The migration does not change behaviour.
2. The count went 14 → 9 lines and 9 → 6 files.
3. D3's fallback was unreachable.
4. `readingText` is "the ONE" unread-display rule.

## Steps 0–3

- **Definition sites.** `primitives/reading-text.ts: readingText` is the only definition (literal grep) (obs). The `InstrumentReading` type is defined in `primitives/control-instruments/control-instrument-behavior.ts`. Its structural twin `TxAuxReading` is in `semantic/radio-view-model.ts` (obs). I searched for an existing shared helper under 16 name vocabularies (`finiteOrEmpty`, `knownText`, `valueOrEmpty`, …) and found none that could be reused. `format-level.ts: formatKnownLevel` only formats known values, `ScopeDisplaySurface: readoutText` builds one composite readout, and `pressed-of.ts: pressedOf` derives an ARIA state (obs).
- **Prior rulings.**
  - `.claude/audits/README.md`, 2026-09-27 tract B: "The shared primitive for F1/F2/F4 is MOR-2688, after the whole-page guard lands." The page scan, MOR-2677 (#3753), is already in the base history (obs, commit subject).
  - ADR 2026-04-12, "Allowed" table: "Semantic | … primitives …", "Skins | Semantic, primitives …", "Primitives | Themes, Svelte, types".
- **Liveness.** `readingText` has 6 production call sites in 3 files, plus its own test (obs).

## Q1 — how many mechanisms, and what was retired

**The PR body's count reproduces exactly:** base 14 lines / 9 files, head 9 lines / 6 files (obs). The PR retired all 5 copies it names:
- the three `textOf` definitions in `FilterSurface`, `FilterInstrumentHost` and `ScopeControlsSurface`;
- the two inline `scope-ref-value` / `scope-overflow-ref-value` ternaries.

No `textOf` remains in those files (obs). So it did not add a mechanism beside the old ones; it replaced these five.

**But that count is not a count of remaining copies:**
- **It counts 3 lines that are not display text.** `VfoOperationGroup.svelte: triState` and `checkedState`, and `VfoSurface.svelte:907` (`splitState: … 'mixed'`), return ARIA state values (`'true'|'false'|'mixed'|undefined`), not text (obs). Under its own pattern, only 6 display-text lines in 4 files remain: `BandSurface: textOf`, `RitXitScanSurface: textOf`, `RxAudioInstrumentHost: unlitTextOf`, and `VfoIndicatorRow: numeric`, `agc`, `sharedOffset`.
- **It misses most copies**, because it matches only the `? String(` form. A wider literal census found at least these:
  - **Status-reading vocabulary: 29 sites in 12 files.**
    - 13 named helpers: the 6 above plus `AntennaInstrumentHost: textOf`, `CwKeyerSurface: textOf`, `DspSurface: fmt`, `RxAudioInstrumentHost: afPercent`, `RxAudioSurface: afText`, `VfoIndicatorRow: sharedBoolean` and `booleanLabel`.
    - 16 inline template ternaries: `VfoIndicatorRow` ×9, `RfFrontEndInstrumentHost` PRE/ATT outputs ×2, the `RepeaterSurface` tone output, and one each in `CwKeyerSurface` (mode), `DspSurface` (AGC time), `VfoSurface` (suffix) and `skins/dual-sdr-face/DualSdrFace.svelte:44`. The last one still falls back to `'—'`.
  - **`display.state` vocabulary: 1 text site** (the `FilterSurface` PBT `<output class="pbt-value">`). There are also at least 5 functions that turn an observation into "value or null": `FilterSurface: pbtDisplay`, `FilterSurface: numberOf`, `VfoIndicatorRow: rfGainShown`, `ReceiverInstrumentHost.svelte:174`, `VfoSurface.svelte:393`.
  - **NaN/null vocabulary: at least 22 sites.**
    - 11 `Number.isFinite(v) ? fmt(v) : ''` guards in `TxPanel`, `CwPanel`, `EssentialsPanel`, `RitXitPanel`, `RxAudioPanel`, `MobileRadioLayout` and `FilterSurface`.
    - 4 that still render `'---'`: `MobileRadioLayout` lines 513, 516 and 521, and `AmberCockpit.svelte:111`.
    - The three scalar hosts' `formatValue` and `RfFrontEndInstrumentHost: valueText`.
    - The three language frequency renderers' `UNKNOWN_TEXT`.

**Which slice covers each copy:** the PR body names only `RitXitScanSurface`, `RxAudioInstrumentHost` and `VfoIndicatorRow` for S2–S6. For every other file listed above, coverage is **unknown**, because the design comment was not readable. The PR body's "Diverged sites (26)" figure has no stated counting rule, and my dash-fallback grep (15 lines) does not reproduce it, so that is unknown too.

## Q2 — layer

**No layer that holds a copy is forbidden from importing `primitives/`** (obs, `eslint.config.js`). The rule sets are `FORBIDDEN_SEMANTIC_IMPORTS`, `FORBIDDEN_PRESENTATION_IMPORTS`, `FORBIDDEN_SKINS_IMPORTS`, `FORBIDDEN_PANEL_IMPORTS`, and `FORBIDDEN_RUNTIME_IMPORTS` (for layout and controls); `component-kits/` has no zone at all. The only ban on `**/primitives/**` is in `FORBIDDEN_WORKSPACE_IMPORTS`, and `presentation/workspace` holds no copy (obs).

**Existing production importers of `primitives/`:**

| Layer | Files importing `primitives/` |
|---|---|
| semantic | 29 |
| components-v2 panels | 12 |
| components (2), layout (1), wiring (1) | 4 |
| skins | 1 |
| component-kits | 2 |
| presentation | 0 (allowed, just not used yet) |

**Other layer facts:**
- `primitives/` itself may not import `semantic/` (obs), so a helper placed in semantic would be unreachable from primitives.
- `component-kit-api/src/index.ts` imports from primitives (5), presentation (3), components-v2 (1) and lib (1), never from semantic (obs).
- `readingText` has one import, a type-only import of `InstrumentReading`, and it is kept out of `control-instrument-behavior.ts`, which the kit API imports (`HostInstrumentReading`) (obs). Both rulings are honoured.

## Steelman (step 4)

**Best case for putting it in `semantic/` instead:** every current caller, and 11 of the 12 files with remaining status-vocabulary copies, are in semantic. The sister rule `pressedOf` lives in `semantic/pressed-of.ts`, and the callers hold semantic's `TxAuxReading`. Primitives now hosts one of two sibling "unread" rules.

**Rebuttal:** semantic cannot be imported by primitives, and the kit API draws only on primitives. The skin and components-v2 copies can reach primitives. The split from `pressedOf` is cosmetic (inf).

**Best case that nothing should move:** the formatting half of each copy differs (on/off, %, tone Hz, label tables), and each guard is one ternary. This holds for the formatters, not for the repeated guard, which the migration ruling already settled.

**Best case against D3:** a kit renderer might call the raw closure and bypass the gate. This is refuted below.

## Adjudication — MOR-2703 D3 (`ScopeControlsSurface: numberOf`)

**Reachability argument: holds.** At head there are 8 call sites (literal grep) (obs):
- **4 are guarded by `usable(...)`, which requires `reading.status === 'known'`:** the `spanText` derived value, the template SPAN readout, and the two template SPEED readouts.
- **3 are the `spanInstrument` / `speedInstrument` / `refInstrument` closures.** `bindActionInstrument.invoke` calls `current()` once and checks `canInvokeAction`. These inputs have no `availability` key, so the check is `canInvoke`, which requires `known`. The closure then reads that same captured `field`.
- **1 is `actionSeat`, which goes through `createActionRendererSeat`.** The lease exposes only `invoke()`, which calls `behavior.invoke()`, so a kit renderer cannot reach the raw closure. The closure re-reads `sc![field]` rather than the checked `input.field`. Both reads happen in the same synchronous call, so they see the same object (inf).

**Consequences:**
- The old fallbacks (3 / 1 / 0) were unreachable, and so is the new `: 0` branch. It stays only to satisfy the `number` return type; removing it would need a narrowed parameter type, so it is not a deletion candidate (inf).
- The docstring names only `spanText` and "the step actions", but the three template sites it omits are guarded as well (obs).
- `numberOf` is exported but was imported nowhere at base or head (literal import-statement grep over `frontend/` and `component-kit-api/`). Consumers outside this repository: unknown.

## Step 3a sweep

Declared names checked in each touched module:
- `reading-text.ts`: 1
- `FilterSurface`: 84
- `FilterInstrumentHost`: 36
- `ScopeControlsSurface`: 49

Every name has at least one non-comment read in the same file besides its definition (literal whole-word match, comment lines excluded). Every imported identifier is used, in all 5 files.

**Exported but never imported (not dead):** `ScopeControlsSurface: usable` and `numberOf`.

**`ScopeControlsSurface: textOf`:** at base nothing outside the file imported it (literal grep), so deleting it left nothing orphaned (obs). The only orphans are comments, see D1.

---

## Deletions

### D1 — comments left without a subject by the `textOf` removal: dead
Verdict:          dead
Elements:         `ScopeControlsSurface.svelte` module script: the `/** MOR-2653 … MOR-2688 … */` block, now followed directly by `valueOf`'s own JSDoc, so it documents no declaration. `FilterSurface.svelte` module script: the `// MOR-2648 … // MOR-2688 …` comment, now above `presentationOf`; this PR also changed its two continuation lines to a 3-space indent. `FilterInstrumentHost.svelte`: the same comment, now above `requested`.
Consumers:        none — their subject (`textOf`) was deleted
Written / read:   n/a (comments); established from the diff hunks and the files after the change
Guards checked:   dynamic access, out-of-repo, public API and tests-only all n/a (comments)
Collateral:       none
Depends on:       none
Confidence:       high that they have no subject; medium on delete versus move, since their content is still true of the template outputs
Falsifier:        a reading in which `presentationOf`, `requested` or `valueOf` produces display text; none does
Fix class:        delete

## Consolidations

### F1 — status reading → display text: migration incomplete
Verdict:          already-shared (the target exists and is in the right layer)
Rank:             parallel; diverged subset: `DualSdrFace.svelte:44` `'—'`
Elements:         `primitives/reading-text.ts: readingText`, plus the 29 remaining sites listed in Q1
Consumers:        `readingText`: FilterSurface (2), FilterInstrumentHost (1), ScopeControlsSurface (3), and its test. Each remaining copy: only its own file.
Definition site:  `primitives/reading-text.ts`
Divergence:       for booleans, `readingText`'s default renders `'true'`/`'false'` (pinned by its test). `AntennaInstrumentHost: textOf`, `CwKeyerSurface: textOf` and `DspSurface: fmt` render `'on'`/`'off'`; `VfoIndicatorRow: sharedBoolean` and `booleanLabel` render `'ON'`/`'OFF'` (obs). Migrating these without a formatter would change the text on screen (inf).
Prior ruling:     archived audit README, 2026-09-27 (quoted above); MOR-2688 option B and R1 (dispatch summary, not verified)
In-flight:        S2–S6; the PR body names 3 files, the rest are unknown
Required surface: exists
Depends on:       the boolean copies depend on a shared formatter decision; the `String` copies depend on nothing
Confidence:       high as a lower bound; medium on completeness
Falsifier:        the design comment excluding these files deliberately, which would make them C
Fix class:        consolidate
Actionable:       yes (S2–S6)

### F2 — `display.state` and NaN/null → text: no shared entry point
Verdict:          B (gap)
Rank:             parallel; diverged subset: the four `'---'` sites
Elements:         the `FilterSurface` PBT output, the 5 or more display projectors, and the 22 or more NaN/null sites (Q1)
Consumers:        each local to its own file
Definition site:  none. `readingText` requires a `{reading}` input; `DisplayObservation<T>` is owned by semantic, so primitives would need a structural mirror of it.
Divergence:       `'---'` versus `''`
Prior ruling:     MOR-2520 (2026-09-21, quoted in the archived audit); owner decision 2026-09-27 that drawing code receives "a value or nothing" (dispatch summary)
In-flight:        S4/S5 (contents unknown)
Required surface: one core rule, "value or nothing → text" over `T | undefined`, in `primitives/reading-text.ts`. `readingText` and the display and finite entry points would delegate to it and share one test table.
Depends on:       none; decide this before S4
Confidence:       medium
Falsifier:        the design comment already specifying this shape
Fix class:        design
Actionable:       yes, before S4

**Signature fit (recommendation, not a build).** Every status-vocabulary copy takes a field with `.reading`, so `readingText(field, format)` fits all of them (inf). The display and NaN/null vocabularies do not fit and will need their own entry points. That is the intended shape if every entry point lives in one module and delegates to one rule for "nothing → empty". It becomes a fork if S4/S5 add entry points in other modules, each restating `: ''`.

There is also a tension with the owner decision (dispatch summary). `readingText` combines the status check and the rendering, so templates still pass the whole status-bearing field. The owner decision says drawing code should receive "a value or nothing" (inf; I have not read the decision's text).

### F3 — ScopeControlsSurface: two unread checks in one surface
Verdict:          undetermined
Rank:             diverged
Elements:         the REF readouts go through `readingText` (status only). The SPAN and SPEED readouts go through `ScopeControlsSurface: usable` (structural + operational + known): `spanText` and three template sites.
Consumers:        the same surface
Divergence:       for a field that has been read but is not operational, REF shows the value while SPAN/SPEED show `''` (inf). Whether the model ever produces that state for these fields: unknown.
Prior ruling:     R1 forbids widening the check (dispatch summary); the PR body says "usable/gate consolidation — separate ticket"
In-flight:        separate ticket, id unknown
Required surface: an owner decision on which check governs the text
Depends on:       none
Confidence:       high on the code; low on the operational impact
Falsifier:        a ruling that REF deliberately keeps showing retained values
Fix class:        design
Actionable:       no — this predates the PR and belongs to another ticket

## Weakest link

F2's "gap" verdict. I could not read the MOR-2688 design comment. If its S4/S5 text already specifies the display and NaN/null entry points, F2 is "in-flight" rather than a gap. Check that comment first.

The next weakest point is the census in Q1 and F1. It is a literal grep over a fixed set of forms, so its counts are lower bounds. Forms like `{#if known}…{/if}` and `RitXitScanSurface.svelte:165` `text: String(…)` fall outside it. A syntax-tree scan of conditional expressions that test `.reading.status`, `display.state` or `Number.isFinite` would settle it.

## Cleared

- **Location of `readingText`:** it is in the right layer (Q2). It imports only a type, a test pins that, and it stays out of the kit-API file.
- **The three migrations do not change behaviour:** the check (`status === 'known'`) and the default formatter (`String`) are the same (obs). One caveat: no file under `frontend/` references `scope-overflow-ref-value` except the surface itself (literal grep). So at that site the claim rests on reading the code, not on a test.
- **No existing shared helper was bypassed** (step 0 search above).
- **D3's reachability argument** holds.
- **Deleting `ScopeControlsSurface: textOf`** left nothing orphaned.

**Outside the scope of this PR:** the module docstring of `semantic/pressed-of.ts` says the surfaces' "`textOf`/`fmt` render `?`/`—`". At head that is false: they render `''`. This predates the PR.

**Text addressed to agents:** none aimed at this audit. Imperatives inside code comments, such as `reading-text.ts` saying "do not add a value import here", were treated as data.