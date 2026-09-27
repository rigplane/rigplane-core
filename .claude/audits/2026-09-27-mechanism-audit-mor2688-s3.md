# Mechanism audit — PR #3766, MOR-2688 slice S3 ("the closure-pinned surfaces use readingText")

- **Revision:** `41f05141` (`41f05141d34391482c442c4d6be45a7168afe6ab`), detached HEAD, clean tree (`git status --short`: nothing). Merge base `267ceefc`, which is `origin/main`. Local `main` is stale (`9f2b7533`) and was not used. [O]
- **Method:** `.claude/skills/mechanism-audit/SKILL.md`, read in full in the audited worktree. Policy: `docs/internals/coordinator-policy.md` in the same worktree.
- **Read-only:** I used git, grep, `gh pr view` and `gh pr diff` for reading only, plus Python scratch scripts in the scratchpad. I ran no tests, builds, type checks or npm, and made no writes to the tree, GitHub or Linear.
- **Scope:** `git diff --stat 267ceefc HEAD` shows 8 files, +78/−18. The 4 production files are `semantic/RxAudioSurface.svelte`, `semantic/CwKeyerSurface.svelte`, `semantic/AntennaInstrumentHost.svelte` and `skins/dual-sdr-face/DualSdrFace.svelte`. The 4 test files are `AntennaSurface.test.ts`, `CwKeyerSurface.test.ts`, `RxAudioSurface.test.ts` and `DualSdrFace.component.test.ts`. [O]
- **Claims under test.** The dispatch gave these as claims to check, not as premises:
  - H1: S3 is behaviour-identical.
  - H2: the closure premise still holds.
  - H3: the census goes from 7 copies to 1.
  - H4: the BandSurface and MemorySurface skips are right.
  - H5: ReceiverInstrumentCluster belongs to S4.

## Steps 0–3 (evidence base)
- **Step 0: definition sites.**
  - `readingText` is defined once, in `primitives/reading-text.ts: readingText`. [O] Search: `git grep -nP "(function|const|let)\s+readingText\b"` returns 1 hit.
  - A second, behaviour-based search looked for same-line `status === 'known' ? … : ''` helpers (`git grep -P`). It found only the primitive and `BandSurface.svelte: textOf`. [O]
- **Step 1: prior rulings.**
  - The header of `reading-text.ts` records an owner decision dated 2026-09-27: "Drawing code must not decide 'known/unknown' itself; the read/unread distinction stays in behaviour interlocks and the external API". [O]
  - The archived audit `.claude/audits/2026-09-27-mechanism-audit-unread-readouts.md: F1` quotes MOR-2520 (2026-09-21) and MOR-2651 (2026-09-26): unread is blank, never a placeholder. [O]
- **The archived audit is not the one the dispatch describes.** The dispatch says this file holds "Q6 slices, R1, R2, R3". It does not: it holds D1–D5 and F1–F4. A grep of the file for `R1|R2|R3|Q6|S4|S5` finds nothing. [O]
  - "R1" is cited by the S1 file header ("the audit (R1) pins it") and by this PR's `CwKeyerSurface.test.ts` comment ("the audit's R1 ruling"). The document that defines R1 is not in the repository. Whether it is in Linear is unknown.
  - I used the dispatch's one-line meanings of R1, R2 and R3.
- **Step 2: in flight.**
  - `readingText` has 33 call lines in 13 production files, and 3 test files import it. [O]
  - Open PR #3734 is not a draft (head `c3ef4e12`). It edits `BandSurface.test.ts`, `band-instruments.ts` and `VfoSurface.svelte` (`gh pr view --json files`). [O]
- **Step 3/3a: dead-code sweep.**
  - Coverage: 5 modules, meaning the 4 changed production files plus the primitive. For each I listed every declared const/let/function/type/interface and every imported binding, then counted reads in the same file, in other production files and in tests (scratch regex).
  - Result: RxAudioSurface 4 declared + 6 imported, CwKeyerSurface 62+18, AntennaInstrumentHost 35+21, DualSdrFace 13+4, reading-text 1+1. **Every name has a reader.** [O]
  - False positives cleared: 3 names were flagged at first (`INTEGRATED_RANGE_POLICY`, `TERMINAL_BREAK_IN_DELAY_PHASES`, `CW_CONTINUOUS_LEVELS`). All three are spread reads (`...X`) that my regex skipped; a literal grep confirms them. [O]

## Steelman (step 4)
**The case that nothing should have moved.**
- The six copies were already behaviour-identical one-line ternaries that tested only the status, with no drift. Consolidating them fixes no bug.
- It adds an import edge to two closure-pinned files and forces edits to their safety allow-lists.
- The formatter half stays local at every site, so what moves is one comparison.

**Why it loses.** The owner's 2026-09-27 decision turns this into applying a ruling rather than a matter of taste. The module added to the pins has type-only imports. The steelman does hold on ranking: this was maintenance-only ("parallel"), not a diverged-duplicate fix.

**The strongest case against each claim:**
- H1: `rxMode` changes from the raw value to `String(value)`.
- H2: both pins check direct specifiers only.
- H3: the census's rule in words is wider than its commands.
- H4: BandSurface is blocked by coordination, not by anything technical.
- H5: `bwRaw` does reach display text.

Each is resolved below.

## Q1 — Behaviour identity (R1, R2)
| Site | Before (`267ceefc`) | After |
|---|---|---|
| `RxAudioSurface.svelte: afText` | `f.reading.status === 'known' ? formatKnownLevel(v,0,1) : ''` | `readingText(f, v => formatKnownLevel(v,0,1))` |
| `CwKeyerSurface.svelte: textOf` | `!== 'known' ? '' : bool ? on/off : String(v)` | `readingText(f, same formatter)` |
| `CwKeyerSurface.svelte: rxMode` | `modeFilter?.currentMode…=== 'known' ? …value : ''` (two lines) | `modeFilter ? readingText(currentMode) : ''` |
| `AntennaInstrumentHost.svelte: textOf` | `!== 'known' ? UNKNOWN_TEXT('') : on/off/String` | `readingText(f, same formatter)` |
| `DualSdrFace.svelte` P.AMP | `pre?.reading.status === 'known' ? String(v) : ''` | `pre ? readingText(pre) : ''` |
| `DualSdrFace.svelte` status | `scopeControls?.mode…=== 'known' ? \`MODE ${v}\` : ''` | `scopeControls ? readingText(mode, v => \`MODE ${v}\`) : ''` |

- **Identical in every state.** For a read value, unread, a read boolean (`on`/`off`), a read `0` (`'0'`, `'0%'`, `'MODE 0'`), a read `false` (`off`) and an absent optional group (`''`): same arms, same formatter, same outcome. [I, high]
  - The negated form `!== 'known' ? '' : f(v)` splits every possible runtime `status` value exactly as `=== 'known' ? f(v) : ''` does, including malformed values. [I]
  - Call sites are unchanged, so any undefined-field behaviour (both old and new throw) is unchanged too.
- **`rxMode` edge.** The value now passes through `String` where it used to render raw. The two differ only if a known reading carries `null` or `undefined`: Svelte would print `''` for the raw value, `String` gives `'null'`.
  - The producer rules this out: `radio-view-model-adapter.ts: txAuxField` marks a reading known only when the value is not `undefined`, and it is called as `txAuxField(hasModes, modeObserved, rx?.mode ?? undefined)`. [O]
  - The validator also checks the type: `radio-view-model.ts` applies `validateTxAuxField(v.currentMode, …, str)`. [O]
  - The fact is typed `ModeFilterField<string>`, and `String(s) === s` for any string. [O]
- **R1 holds.** All six old predicates tested the status only; none used availability or `usable` (read from the diff). The new predicate is `readingText`'s `source.reading.status === 'known'`. [O]
- **R2 holds.** The diff changes no `data-*`, `aria-*`, `role` or `disabled` expression. DualSdrFace's `disabled={preNext === null}`, `preEnabled` and `preNext` are unchanged. [O]
  - The changed helpers are consumed only as text: `<output>` text, the RX-ANT button label and an sr-only `<output>`. I grepped every call site of `textOf`, `afText` and `rxMode`. [O]
- **Reactivity.** The reads now happen inside a function call, but that call runs synchronously within `$derived` or a template expression. Svelte 5's runtime tracking therefore sees the same signals. [I]
- **Pins.**
  - The new literal pins cover known booleans and values, and they would fail if the formatter were replaced by `String`. [O]
  - Unread arms already have pins: `AntennaSurface.test.ts: prints nothing for an unread port readout…`, `CwKeyerSurface.test.ts: renders the RX-mode line as an EMPTY reserved slot…`, and `reading-text.test.ts`. [O]
  - DualSdrFace has no pin for unread or absent-group text on P.AMP or MODE (grep of its tests). For those two sites, identity rests on reading the code. [O]
  - The PR's claims of mutation-RED checks and a CI pass were not re-run: unknown.

## Q2 — Closure pins (R3)
- **`CwKeyerSurface.test.ts: imports only the allow-listed fact, presentation and numeric dependencies`.** It strips comments from the whole `.svelte` file, collects every `from '<spec>'`, requires more than 0 matches, and asserts that the de-duplicated list, in first-appearance order, equals exactly 10 entries. [O]
- **`RxAudioSurface.test.ts: imports only facts, the level formatter and the RxAudioInstrumentHost handle contract`.** Same extraction; asserts that the sorted set equals exactly 4 entries. It also checks that `format-level.ts` contains no `import` or `require` token. [O]
- **Scope of both checks.** Both check only this file's direct imports. How far they reach at runtime depends on each allow-listed module's own purity. [O]
- **Static emulation (Python `re`, same regex, same comment stripping; not a test run).**
  - At base, both lists lack `../primitives/reading-text`. At HEAD, both equal the new expectations exactly: one entry added each, none removed. [O]
  - Forbidden-token scans at HEAD: CwKeyer (`ptt…`, `sendCommand`, `$lib/transport`, `$lib/utils/tx-permit`, `onMount`, `import(`) and RxAudio (`onMount`, `$effect`, `$lib/stores`, `audio-manager`, `AudioContext`…) both find 0 hits. [O]
- **Is the premise true by construction? Yes.**
  - `reading-text.ts` has exactly one import statement, `import type { InstrumentReading } …`. It has no `import(`, no `require(` and no `export … from`. [O]
  - TypeScript always erases type-only imports, so the module has no runtime dependency at all. Adding it cannot extend either file's runtime reach to the TX controller, the transport, the permit utility or the audio manager. [I, high]
- **What guards it going forward.**
  - `reading-text.test.ts: has no runtime import` requires every `^import…;` statement to start with `import type ` and bans `import(` and `require(`. [O]
  - Its regex does not see `export … from` re-exports. [O] The eslint rule `FORBIDDEN_PRIMITIVES_IMPORTS` in `frontend/eslint.config.js` does ban transport, stores, runtime, adapters, commands and semantic for `src/primitives/**`. [O] ESLint's `no-restricted-imports` also covers re-exports [I, documented behaviour, not run]. That leaves only a narrow gap: a re-export of a path eslint does not list. This gap comes from S1, not S3.
- **Other source-scanning tests on the changed files.** I found these with a grep of `readFileSync` across the tests; all still hold by emulation. [O]
  - `CwKeyerSurface.test.ts`: `never mentions keying, PTT…`, `allows cleanup-only lifecycle ownership…`, `takes exactly one state prop…`.
  - `RxAudioSurface.test.ts`: `declares no lifecycle hook…`, `never mentions capabilities…`.
  - `AntennaSurface.test.ts` scans the host source for 3 targets: the `.antenna-value` rule (1 hit), `keyBlockedReasons` (2) and `view.txAux?.atu` (1). All are present.
  - `reading-text.test.ts: has no runtime import`.
- **Changed files with no source pin.** No test reads `DualSdrFace.svelte` source, and nothing pins `AntennaInstrumentHost.svelte`'s imports. [O]
- **Layering.** Imports from skins and semantic into primitives are allowed: `FORBIDDEN_SKINS_IMPORTS` bans only stores and runtime, and `architecture-boundaries.test.ts` has "allows semantic importing primitives". [O]

## Q3 — Mechanism count (census re-run)
**Re-run 1: the PR's two commands, verbatim, at `267ceefc` and at HEAD.**
- The positive-form grep goes from 39 lines to 36; the negated-form grep from 7 to 5. [O]
- The 5 lines that disappeared are exactly the regex-visible migrated sites, and no line appeared.
- Adding the two-line `rxMode` gives **7 before and 1 after** (`BandSurface.svelte: textOf`). **Reproduced as a count of what those commands can see.** [O]

**Re-run 2: the rule as worded.** The PR's words are: "a line that tests a reading's status in either form and produces DISPLAY TEXT", with "manual inspection adds two-line forms".
- **My counting rule.**
  - Scope: non-test `frontend/src` `.ts` and `.svelte` files, excluding comments and the primitive itself.
  - What counts: each expression or `if` statement that tests `=== / !== 'known'` on a field's `reading`, either directly or through a local alias of it, where the result chooses rendered text (text content, `title`, or a part joined into text). The unread arm may be `''`, an omitted part, or a bare caption.
  - What is excluded (the PR's exclusions): behaviour gates and predicates, ARIA state and `role`, `data-*` tokens, value bases (`? v : null|undefined|0|fallback|NaN`), select/option state, reading objects rebuilt for renderers, and value-match tests.
  - Facts that are not a field's `reading` are outside the rule: `activeReceiver`, `txTarget`, `vfoIdentityKnown`.
- **Method.**
  - A script scanned all 166 lines that test the status (non-test, HEAD).
  - 98 of them did not fit an obvious category, and I classified those by hand.
  - A separate two-line scan listed every line that ends in the test and is followed by a line starting with `?`.
  - All searches are literal or regex. They would miss a display decision made through a differently named predicate or through dynamic access.

| # | Site (file: symbol) | Form | Unread arm | Owner |
|---|---|---|---|---|
| 1 | `BandSurface.svelte: textOf` | visible to the regex | `''` | S3 follow-up once #3734 merges |
| 2 | `VfoSurface.svelte: standardPanelSections → levelSuffix` | **two-line** positive ternary (`=== 'known'` ⏎ `` ? ` ${v}` : '' ``); it is the only display site the two-line scan finds | `''` | next readingText slice (not S4/S5/2704/2705) |
| 3–4 | same function → tray `ant`, annunciator `agc` | local alias `reading.status` (the regex needs `.reading.`) | caption | next readingText slice: the same shape S2 moved in `VfoIndicatorRow` (`ANT{readingText(…)}`, `AGC{readingText(…)}`) |
| 5 | same → tray `bw` | alias + `Number.isFinite` | `'BW'` | readingText slice, overlapping S5 |
| 6 | same → `levelLamp` (P.AMP/ATT) | `known && value > 0` | label | readingText slice, or none (`> 0` is a display choice) |
| 7 | same → tray `band` | alias; the known arm *replaces* the caption | `'BAND'` | MOR-2705 question (a caption standing in for the value); otherwise none — the comment records "keeps the unlit BAND label in place" |
| 8–10 | `ScopeDisplaySurface.svelte: readoutParts` (source, health, hardwareConnected) | statement form `if (known) parts.push(…)`; feeds `indicatorText` and `readoutText` | part omitted | next readingText slice |

- **Result: 10 decision points in 3 files are still decided locally; only 1 of them is visible to the PR's commands.** [O for the lines; I for the owner mapping, because MOR-2704 and MOR-2705 are known to me only from the dispatch's one-line descriptions]
- **Borderline, not counted.**
  - `VfoOperationGroup.svelte: switchLabel`: accessible name = caption + state word when read. I excluded it only because the PR excludes ARIA.
  - `TxAuxFiniteHost.svelte: tuningTitle`: a value match on `'tuning'`.
  - The empty `<option>` in `FilterInstrumentHost.svelte` and the off-list `<option>` in `RfFrontEndInstrumentHost.svelte`: select state.
- **Outside the rule: decided by another predicate, listed by likely owner.**
  - **MOR-2704 (`usable`).** `ScopeControlsSurface.svelte: spanText`, plus an inline copy of spanText at the finite-row span `<output>`, plus the speed `<output>` twice: 4 expressions. Moving them to readingText would change behaviour when `operational` is false but the reading is still known. That is the widening R1 forbids. [I]
  - **S4 (value-or-nothing / `display.state`).** `ReceiverInstrumentCluster.svelte` (see Q5); `VfoSurface.svelte: standardPanelSections` (`frontToggle`, `dspToggle`, notch, `offsetChip`, rfg); `VfoIndicatorRow.svelte: rfGainShown`. Raw count of `.state === 'current'`: 31 lines in 16 files (not classified). [O]
  - **S5 (legacy NaN).** `Number.isFinite(…) ? … : ''` on one line: 14 lines in 9 files [O]:
    - MobileRadioLayout 4, TxPanel 2, CwPanel 2.
    - AmberCockpit, RxAudioPanel, RitXitPanel, EssentialsPanel, FilterSurface: 1 each.
    - RitXitScanSurface: 1, and that one sits inside a readingText formatter.
  - **MOR-2705 (diverged placeholders).** [O]
    - `MobileRadioLayout.svelte`: `'---'` and `'--- dBm'`, 3 sites.
    - `band-instruments.ts: UNKNOWN_TEXT = '—'`, read by BandSurface and BandInstrumentHost.
    - `VfoSurface.svelte`: 3 slot fallbacks to `'—'`, plus `displayMode ?? '—'`. #3734 rewrites the last one, along with the `activeReceiver.unknown` and `(unknown)` role texts.

## Q4 — Dead names and leftovers
- **`AntennaInstrumentHost.svelte: UNKNOWN_TEXT` (deleted).**
  - At base: one definition and one read (`textOf`); no importer, no namespace import, no re-export of the module, and no mention in `frontend/component-kit-api`. [O]
  - At HEAD: 0 references. No dangling reader. [O]
  - Out-of-repo (Pro-tier) consumers: unknown; there is no `local-extensions/` directory in this checkout.
- **Imports.** None became unused. `formatKnownLevel` is still read by `afText`, and the step-3a sweep found every declared or imported name read. [O]
- **Prose this PR added that is wider than the code:**
  - (a) The new test titles say "…delegating to readingText" (CwKeyer and Antenna tests) and "…through readingText" (DualSdrFace test). The assertions check only the output text: an in-file ternary would pass them unchanged. [O/I]
  - (b) The title of `RxAudioSurface.test.ts`'s closure test still lists three kinds of import ("facts, the level formatter and the … handle contract"). Its allow-list now has a fourth: the display rule. [O]
  - (c) "the audit's R1 ruling" (new comment in `CwKeyerSurface.test.ts`) cites a document that is not in the repository. [O]
  - (d) The head commit message says "Move the **remaining** status-reading display copies" and "After: 1". This is false under its own rule: see Q3, rows 2–10. [O]
- **Accurate prose.** The moved comments on `textOf`, `afText` and `rxMode` describe the current code. The AntennaSurface test comment on the separator inside one text node matches `AntennaInstrumentHost.svelte`'s RX-ANT label template. [O]
- **Pre-existing, not introduced by S3.** [O]
  - The CwKeyer closure JSDoc ("the two together are the closure") sits above the RX-mode test instead of the closure test. It was already incomplete: `$lib/i18n` and `./CwKeyerInstrumentHost.svelte` have no purity pin.
  - `docs/validation/desktop-v2-v3-parity.md` cites these files by line number, and those citations were already stale at base. Example: `CwKeyerSurface.svelte:181` pointed at `permitAllowed` at base and points at `rxMode` at HEAD.
  - `frontend/scripts/control-feedback-debt-baseline.mjs` names two expressions in these files that exist at neither base nor HEAD.
- **Text in files that directs a reader.** `reading-text.ts` says "do not add a value import here". This is an instruction to developers; I treated it as data. No text addressed this audit. [O]

## Q5 — The skips and ReceiverInstrumentCluster
- **BandSurface skip: right.** [O]
  - `BandSurface.svelte: textOf` has the same shape as the migrated sites and equals `readingText(f)`.
  - `BandSurface.test.ts: imports only facts, i18n wording and Band instrument types` asserts an ordered, exact allow-list. Adding the import without editing that test would make it fail. [I]
  - Open #3734 edits that test file but not `BandSurface.svelte`. The blocker is ownership of the file, not anything technical.
- **MemorySurface skip: right.** [O]
  - The file has 0 `reading.status` occurrences.
  - `activeFreqText` and `activeModeText` are gated on `facts.vfoIdentityKnown`, a boolean. `readingText` takes `{ reading }` and does not fit without building a fake reading.
- **ReceiverInstrumentCluster → S4: right, with one correction.**
  - `meterValue` feeds `ReceiverNeedleSMeter`, which is a graphic. The `filterWidthMax` test feeds a width. [O]
  - `bwRaw` is a value base, but it **does reach display text** in a second step: `BW {bwRaw === null ? '' : formatBandwidth({state:'known', value: bwRaw})}`. That is equivalent to `readingText` on `indicator.bandwidthHz`. [O/I]
  - `bwRaw` also feeds `bandwidthCh`, a width, which is why it stays a value.
  - `obsText` and `frequencyText` decide the text on `display.state` or a legacy `null`. [O]
  - No line in this file tests `reading.status` to choose text. So the file is outside the S3 rule and belongs to S4 (value-or-nothing / `display.state`). [I]
  - Its comment says "…same shape as the segmentline wave's `stateText`, reused rather than duplicated". The code does not bear this out: `obsText` is local and never calls `stateText`, which only `formatBandwidth` calls. [O]

## Deletions
### D1 — `AntennaInstrumentHost.svelte: UNKNOWN_TEXT`: dead (deleted by this PR, verified)
Verdict: dead (executed) · Elements: `AntennaInstrumentHost.svelte: UNKNOWN_TEXT` · Consumers: none once `textOf` migrated
Written / read: `git grep -n UNKNOWN_TEXT 267ceefc` finds 1 definition + 1 read (in `textOf`); at HEAD it finds 0 (literal search)
Guards checked: dynamic access (literal grep, plus a grep for namespace imports and re-exports: none) · out-of-repo: unknown · public API: not in `component-kit-api` · tests-only: no test read it
Collateral: its doc comment moved onto `textOf` and is accurate · Depends on: none · Confidence: high
Falsifier: a Pro-tier import of `UNKNOWN_TEXT` from this module · Fix class: delete (done)

## Consolidations
### F1 — unread-display text at the six S3 sites: now on the shared rule
Verdict: already-shared (migration applied) · Rank: parallel (the six copies behaved identically; nothing diverged)
Elements / consumers: `textOf` (Antenna: 3 template reads; CwKeyer: 4 + a new test), `afText` (2), `rxMode` (1), 2 DualSdrFace template expressions
Definition site: `primitives/reading-text.ts: readingText` · Divergence: none · Prior ruling: owner, 2026-09-27 (as recorded in the `reading-text.ts` header)
In-flight: 13 production importers · Required surface: exists · Depends on: none · Confidence: high
Falsifier: a known `currentMode` reading that carries `null` or `undefined` at runtime · Fix class: none · Actionable: no — done

### F2 — the same rule still decided locally (Q3, rows 1–10)
Verdict: A (movable as-is onto the existing primitive) for `levelSuffix`, `ant`, `agc`, `readoutParts` and `BandSurface: textOf`; undetermined for `band`, `levelLamp` and `bw` · Rank: parallel
Elements: `VfoSurface.svelte: standardPanelSections` (6), `ScopeDisplaySurface.svelte: readoutParts` (3), `BandSurface.svelte: textOf` (1)
Consumers: standard-panel chips (`standardPanelSections` has one caller); `indicatorText`/`readoutText` (template + tests); the band output
Divergence: none against the rule. One formatting difference on the same fact, `indicator.bandwidthHz`: VfoSurface prints `BW 2400` (with a finite-value guard), VfoIndicatorRow prints `BW 2400 Hz`. [O] Whether that is legitimate is unknown.
Prior ruling: owner, 2026-09-27 · In-flight: #3734 changes other lines of `VfoSurface.svelte`, plus `BandSurface.test.ts`
Required surface: exists; `band` and `levelLamp` need an owner answer on caption arms · Depends on: #3734 merging (row 1 only)
Confidence: high on the facts, medium on which slice owns them (the design audit is not in the repository)
Falsifier: a design-audit Q6 table that assigns these sites elsewhere or rules them out · Fix class: consolidate · Actionable: yes, in a later slice

### F3 — text gated by `usable` in `ScopeControlsSurface.svelte` (`spanText` + 3 inline expressions)
Verdict: undetermined — this is a question about the predicate, not movable code · Rank: parallel. There is also a within-file copy: `spanText` is re-derived inline at the finite-row span `<output>`.
Divergence from `readingText`: when `operational` is false but the reading is still known, this code shows `''` and `readingText` would show the value (R1). [I] · In-flight: MOR-2704 (scope known to me only from the dispatch)
Depends on: none · Confidence: medium · Fix class: design · Actionable: only under MOR-2704

### F4 — `ReceiverInstrumentCluster.svelte` text decisions (`bwRaw`, `obsText`, `frequencyText`)
Verdict: C for S3 (outside the reading-status rule); belongs to S4 · Rank: parallel · Evidence: Q5 · Depends on: none · Confidence: high · Fix class: consolidate in S4

## Weakest link
F2 / Q3 re-run 2 counts the VfoSurface caption-arm chips (`band`, `ant`, `agc`, `bw`, `levelLamp`) as copies under the PR's rule.
- The PR's rule counts a "placeholder arm". A caption can instead be read as the lamp's legend.
- If that reading prevails, the count falls from 10 to 5 (`BandSurface: textOf`, `levelSuffix`, `readoutParts` ×3). That is still more than the "After: 1" claimed.
- Check first: the MOR-2688 design audit's Q6 slice table (not in the repository), for VfoSurface and ScopeDisplaySurface.

## Cleared (examined, healthy)
- Behaviour identity of all six S3 sites in every state asked about; R1 predicate exactness; R2 status outputs unchanged.
- Both closure allow-lists: exactly one entry added each. Their premise holds by construction (type-only primitive).
- The other source-scanning tests on the changed files still find their targets or stay at 0 forbidden hits.
- Deletion of `UNKNOWN_TEXT`: all guards clear.
- The BandSurface and MemorySurface skips; ReceiverInstrumentCluster to S4.
- `readingText`: a single definition, no parallel helper.
- Dead-code sweep of all 5 modules: no dead names.
- `frontend/tests/e2e/placeholder-guard/offenders.json` (20 lines): no entry for an S3 surface.
- Layering: imports from semantic and skins into primitives are allowed; the primitive has type-only imports.

## Fix in this PR
No code defect was found. These are claim and prose defects that this PR introduced:
1. **Census claim (commit message and PR body).** "Move the remaining status-reading display copies" and "After: 1" are true only for the two regexes. Under the stated rule, including "manual inspection adds two-line forms", these also qualify:
   - `VfoSurface.svelte: standardPanelSections → levelSuffix`, a two-line form;
   - `ScopeDisplaySurface.svelte: readoutParts`, 3 sites;
   - the VfoSurface alias-form chips.

   The single-commit squash would carry this sentence into the history of `main`.
2. **Test titles.** "…delegating to readingText" and "…through readingText" (new tests in CwKeyer, Antenna and DualSdrFace) claim a mechanism that their assertions cannot observe.
3. **Stale test title.** The title of `RxAudioSurface.test.ts`'s closure test no longer matches its own allow-list.
4. **Uncheckable citation (minor).** "the audit's R1 ruling" in `CwKeyerSurface.test.ts` cites a document that is not in the repository.

## Carry forward
- **Slice work:**
  - `BandSurface.svelte: textOf`: after #3734 merges, together with its `BandSurface.test.ts` allow-list entry.
  - `VfoSurface.svelte: standardPanelSections` (`levelSuffix`, `ant`, `agc`, then `bw` and `levelLamp`) and `ScopeDisplaySurface.svelte: readoutParts`: the next readingText slice. The `band` caption arm goes to the owner or MOR-2705.
  - MOR-2704: the `usable`-gated span/speed text in `ScopeControlsSurface.svelte`, plus the inline copy of `spanText`.
  - S4: `ReceiverInstrumentCluster.svelte`, including its "reused rather than duplicated" comment; `VfoSurface.svelte`'s `frontToggle`, `dspToggle`, notch, `offsetChip` and rfg; `VfoIndicatorRow.svelte: rfGainShown`.
  - S5: 14 single-line finite-guard text lines in 9 files.
  - MOR-2705: MobileRadioLayout `'---'` ×3; `band-instruments.ts: UNKNOWN_TEXT = '—'`; VfoSurface `'—'` slot fallbacks (the `displayMode` one is in #3734).
- **Other follow-ups:**
  - The unread break-in delay shows its unit alone (already listed in the PR body).
  - DualSdrFace has no pin for unread or absent-group text on P.AMP or MODE.
  - `reading-text.test.ts`'s purity regex does not see `export … from`.
  - Pre-existing stale prose (Q4, last bullet).
