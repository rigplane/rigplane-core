# Skins Presentation-Boundary Release Gate

Status: MOR-2034 release gate (epic MOR-2035)
Date: 2026-08-31

## The criterion

The owner's stated acceptance criterion for the skins architecture, verbatim:

> I can build a theme with its own S-meters and indicators — up to
> replicating an IC-7610/FTX-1 front panel or drawing my own SDR interface —
> without touching anything below the presentation layer.

Two of the criterion's guarantees are independent, separately-checkable, and
mechanically enforced today by this document's Gates 1 and 2:

1. **Presentation-layer boundary** — a skin (and the panels/layouts it is
   built from) can reach the presentation layer's own exports, and nothing
   below it: no transport, no store, no audio manager, imported directly.
2. **Truth derivation stays out of skins** — a skin presents a value; it
   never computes one. Calibration, unit conversion, and any other "what is
   actually true" derivation happens once, below the presentation layer, and
   every presentation-layer consumer reads the same answer.

That pair does not cover the criterion's other two clauses. "Up to
replicating an IC-7610/FTX-1 front panel or drawing my own SDR interface"
names no mechanism, and none exists — this document does not claim one.
"Without touching anything below the presentation layer" does have a
mechanism for one reading of it (Gate 1's ban on a skin *importing* below
the presentation layer), but making a new skin *selectable* at all requires
editing a file that sits below the presentation layer — that reading is not
enforced, and is false today. Gate 3 records it, with the file that owns the
gap and the ticket tracking it (MOR-2054).

This document is this repo's `docs/internals/*.md` MOR-XXXX-keyed
executable-gate entry (the form `radio-state-pipeline-validation.md` uses for
MOR-348 and `backend-neutral-readiness-gap-register.md` uses for MOR-424) for
Gates 1 and 2 specifically. It does not restate the mechanisms below —
it names them, so a reviewer can re-run the cited test file and get a real
pass/fail, not a prose claim.

A note on scope: this entry could not be placed literally next to Linear's
MOR-1413 (the v3 hardware operator-acceptance gate) or MOR-1102 (the v3
design-language shipping evidence table) — both are Linear-native process
gates (an owner-run hardware sign-off, and a ticket description rewritten as
a finite evidence table) with no file in this repository to extend. This
entry follows the closest verified in-repo precedent instead (the two
`docs/internals/` gates named above), and says so rather than asserting an
adjacency that could not be confirmed.

## Gate 1 — presentation-layer boundary

| Mechanism | File : symbol | What it proves | Honest limit |
|---|---|---|---|
| Import-boundary ESLint rules | `frontend/eslint.config.js`: `FORBIDDEN_RUNTIME_IMPORTS`, `FORBIDDEN_PANEL_IMPORTS`, `FORBIDDEN_SKINS_IMPORTS` | `src/skins/**/*.svelte`, `src/skins/**/*.ts`, and `src/components-v2/panels/**` cannot import `$lib/stores/*`, `$lib/transport/*`, or the audio manager | A lint rule, not a test by itself — see the next row for how it is actually exercised |
| Rules exercised through the real linter | `frontend/src/__tests__/architecture-boundaries.test.ts`: `restrictedImportHits` | Lints synthetic fixture code through `ESLint.lintText` against the real flat config at real skin/panel paths (e.g. `src/skins/sdr-test/SdrTestSkin.svelte`, `src/skins/registry.ts`) — a rule regression here fails the same way `npm run lint` would | Fixture paths need not exist on disk; this proves the *rule*, not that every real file in `skins/` obeys it (no file in the tree currently violates it, but nothing scans the whole tree for a new violation) |
| Both directions proven | same file, e.g. `'rejects a skins .ts file importing $lib/stores/* directly...'` paired with `'allows skins/registry.ts to import layout-mode normalization through the adapter...'` | Within the skins zone, for every forbidden import there is a paired case proving the *legitimate* migrated replacement still passes — a custom skin's own instruments are not collateral damage of this rule | Not file-wide: e.g. the workspace zone's four rejection cases in this same file have no paired `allows …` case |
| Skin registry is exhaustive and its entry points really mount | `frontend/src/skins/__tests__/entrypoints.test.ts`: `SKIN_ENTRYPOINT_COVERAGE` | Every `SkinId` in `frontend/src/skins/registry.ts` mounts its declared component through the same `loadSkin()` `App.svelte` calls | The one `'covered-elsewhere'` entry (`dual-receiver-cockpit`) is a substring check on another test file's source text, not a behavioral one — documented as such in the file's own header, not restated here as more than it is |

## Gate 2 — truth derivation stays out of skins

The counterexample that motivated this gate was found and fixed the same
night as this entry: two independent S-unit derivations disagreed because
`components-v2/panels/meter-utils.ts`'s old `formatSMeter` reimplemented a
hardcoded 6 dB/S-unit ladder instead of calling the shared, table-driven
`smeter-scale.ts`. That ladder happened to match IC-7300's real curve
(uniform) but disagreed with FTX-1's real, non-uniform curve. Consolidated
under MOR-2024; `formatSMeter`'s calibrated branch now defers entirely to
`frontend/src/components-v2/meters/smeter-scale.ts`: `calibratedToSUnit`
(the uncalibrated branch returns `formatRaw`, unchanged).

| Mechanism | File : symbol | What it proves | Honest limit |
|---|---|---|---|
| Meter conformance contract | `frontend/src/components-v2/meters/__tests__/meter-contract.ts`: `METER_REGISTRY` | Census-checks every `.svelte` file directly under `components-v2/meters/` (today: `BarGauge.svelte`, `LinearSMeter.svelte`) is registered and mounts | Explicitly scoped to that one directory by its own file header — not to `skins/` or `components-v2/panels/`, which is where a skin's *own* custom instruments actually live |
| Domain-conformance suite | `frontend/src/components-v2/meters/__tests__/meter-contract.test.ts` | Mounts `LinearSMeter` against a deliberately non-uniform synthetic calibration table and asserts its rendered S-unit/dBm text equals a fresh `calibratedToSUnit`/`calibratedToDbm` call at three probes chosen so no fixed per-S-unit step can pass all three (full reasoning in the file's own header) | Cannot rule out a byte-identical reimplementation of the same interpolation — a DRY concern, not a correctness one, per the same header |
| Typed `data-*` vocabulary for stylesheet authors | `frontend/src/presentation/languages/state-vocabulary.ts` | A skin's stylesheet can key off `RxTxSurface`/`MetersSurface` visual state (`RF_STATES`, `TX_SESSION_STATES`, etc.) from a typed export instead of reverse-engineering it from semantic source, and instead of computing it | Scoped to two of the three files the MOR-2036 owner ruling names (`RxTxSurface.svelte`, `MetersSurface.svelte`); `VfoSurface.svelte` — the ruling's third file — has no stylesheet consumer yet and is not exported here (see the file's own header) |

### Residual found by this ticket, and closed for two of the four call sites found

`METER_REGISTRY`'s census is scoped to `components-v2/meters/`. Grepping
every production (non-test) mention of `smeter-scale.ts` —
`grep -rn "smeter-scale" frontend/src | grep -v __tests__ | grep -v '\.test\.'`
— returns nine lines across six files. Filtering out `LinearSMeter.svelte`
(already inside that directory) and four comment-only lines in
`meter-utils.ts`/`AmberCockpit.svelte` leaves four real call sites outside
it — i.e., in the presentation-layer code a skin author actually writes:

1. `frontend/src/components-v2/panels/lcd/AmberSmeter.svelte` — the
   `amber-lcd` theme's own S-meter, mounted by the `lcd-cockpit`/`lcd-scope`
   skins. Had a dedicated test
   (`components-v2/panels/lcd/__tests__/lcd-components.isolated.test.ts`),
   but its only calibration fixture was uniform (`IC7610_LIKE_S_METER_CAL`,
   6 dB/S-unit throughout, matching IC-7610's real curve) and its only
   exact-text assertion was at the S0 anchor — the same shape of gap
   MOR-2024 fixed in `meter-utils.ts`, just untested here rather than wrong.
2. `frontend/src/components-v2/layout/mobile-layout-logic.ts`'s
   `formatSValue`/`formatDbm` — the `mobile` skin's own S-meter formatting.
   Had a dedicated test
   (`components-v2/layout/__tests__/mobile-layout-logic.test.ts`), with the
   same uniform-fixture shape (`IC7300_LIKE_CAL`, deliberately IC-7300-shaped
   to pin a *different* MOR-2024 fix — the continuous S9+ reading — not this
   one).
3. `frontend/src/components-v2/panels/meter-utils.ts` — `formatSMeter`'s
   calibrated branch imports `calibratedToSUnit`/`isSmeterCalibrated`
   directly. Missing from the original census by oversight, not a coverage
   hole: this is the file MOR-2024 fixed in the first place, and its own
   `meter-utils.test.ts` already carries a non-uniform, FTX-1-anchored
   discrimination suite (`describe('formatSMeter — FTX-1 profile anchors
   (MOR-2024)')`) from that fix. No new test is added here.
4. `frontend/src/skins/sdr-test/SdrVfoScreen.svelte` — initially counted as a
   live instance; re-derived and struck. `SdrVfoScreen.svelte`'s own header
   states it has not been mounted since MOR-1065, kept only "as the
   pre-migration prototype reference pending the MOR-1099 legacy-wrapper
   retirement" — corroborated by `SdrTestSkin.svelte`'s own header, which
   confirms "`SdrVfoScreen.svelte` next door is not mounted — MOR-1065
   replaced this top slot..." It is unreachable dead code, not a live
   presentation-layer consumer, and that unreachability is already
   self-documented — not a new finding.

Grepping *importers* is structurally blind to the defect shape this section
guards against: a file that reimplements the ladder outright imports
nothing from `smeter-scale.ts`. Swept for that shape across production
code; no other file derives S-unit or dBm text locally. One related,
already-reported duplicate exists:
`frontend/src/components-v2/panels/meter-utils.ts: calibratedSmeterToRaw`
hand-copies `frontend/src/components-v2/meters/smeter-scale.ts:
calibratedToRaw`'s knot interpolation instead of calling it — MOR-2024
residue, feeding `sLevel`'s bar geometry rather than display text, not
fixed here.

This PR adds a non-uniform-calibration discrimination suite to both real
test files above (instances 1 and 2), reusing `meter-contract.test.ts`'s own
fixture and probe reasoning against the real mounted component /
plain-function call site, proven both ways during development (a throwaway
hardcoded-6-dB-ladder mutant in each production file, run against the new
tests, then reverted): the mutant failed all three S-unit probes — of six
new cases per file, three S-unit and three dBm — in both files with the
same off-by-one-S-unit pattern the meter-contract.test.ts header predicts;
the real code passes all six.

What is **not** closed by this PR, and is filed as a follow-up rather than
attempted here: `METER_REGISTRY`'s census has no structural mechanism that
would force a *future* skin's new S-meter to get this same discriminating
regression test — closing instances 1 and 2 required a human (or agent) to
notice the gap and hand-write the probes, same as it always has. Widening
the census itself (a directory scope of more than one path, or a third
`MeterValueDomain` shape for multi-source components like `AmberSmeter`) is
a structural change to MOR-2037's own contract and is out of this ticket's
guardrails.

## Gate 3 — authoring reach below the presentation layer (open gap, not enforced)

The criterion's final clause — "without touching anything below the
presentation layer" — has two readings. Gates 1 and 2 above enforce the
*import-direction* reading: a skin's own code may not import transport,
stores, or the audio manager. Read as an *authoring-reach* claim — which
files a skin author must edit to ship a new, selectable skin — no mechanism
enforces it as a blanket guarantee. The bullets below describe the specific
violation this document originally found; MOR-2059 closed that one
specifically (detail after the table) — the general claim still is not
enforced, pending any other site MOR-2054 may still track:

- `frontend/src/presentation/layout-mode.ts` owns the `LayoutMode` union and
  `CANONICAL_LAYOUT_MODES` (moved here from `lib/stores/layout.svelte.ts` by
  MOR-2059 — see below); `normalizeLayoutMode` maps any id outside that set
  to `'auto'` before it can be selected or persisted.
- `frontend/src/skins/registry.ts: resolveSkinId` normalizes the user's
  layout preference through that same function — reached, correctly, via
  `frontend/src/lib/runtime/adapters/layout-mode-adapter.ts`'s re-export
  (which now forwards through `lib/stores/layout.svelte.ts`'s compatibility
  shim to `presentation/layout-mode.ts`), so Gate 1's import-boundary rule
  is not itself violated here — and `frontend/src/App.svelte`'s call to
  `getLayoutMode()` is what feeds `resolveSkinId` its preference. Making a
  new skin id selectable requires adding it to `presentation/layout-mode.ts`
  — as of MOR-2059 that module sits inside the presentation layer and is
  not named by `FORBIDDEN_SKINS_IMPORTS` (Gate 1) at all, so this is no
  longer an edit "below the presentation layer" in either reading of the
  criterion.
- `frontend/src/presentation/workspace/contract.ts: WORKSPACE_LAYOUT_IDS` is
  no longer pinned as a literal (MOR-2059): it now derives directly from
  `CANONICAL_LAYOUT_MODES`, so adding an id in the previous bullet is
  reflected here with no further edit. It was pinned before because
  `contract.ts` could not import `lib/stores/layout.svelte.ts` at all —
  `presentation/workspace/**` is eslint-banned from importing
  `$lib/stores/*` (`frontend/eslint.config.js: FORBIDDEN_WORKSPACE_IMPORTS`,
  inherited from `FORBIDDEN_PANEL_IMPORTS`'s `$lib/stores/*` ban via
  `FORBIDDEN_PRESENTATION_IMPORTS`) — the vocabulary lived in exactly that
  banned path. `presentation/layout-mode.ts` is pure, so that obstacle is
  gone.
- The one pre-normalization exception, `dual-receiver-cockpit`, is QA-only:
  `presentation/layout-mode.ts`'s own comment records that it "can
  therefore never be persisted via setLayoutMode/the workspace and never
  appears in the StatusBar skin selector" — not a general path a real skin
  can use.

| Clause | Mechanism | Verdict |
|---|---|---|
| own S-meters | Gate 1 (import boundary) + Gate 2 (`smeter-scale.ts` delegation) | enforced |
| own indicators | Gate 2's typed `data-*` vocabulary | enforced, scope-limited |
| replicate an IC-7610/FTX-1 front panel; draw an original SDR interface | — | no mechanism; not claimed elsewhere in this document either |
| without touching anything below the presentation layer | — (Gate 1 covers only the import-direction reading) | **not enforced; false today** for a genuinely new, selectable skin |

Tracked as MOR-2054, which named this exact link — a `LayoutMode` union
living in a `$lib/stores/*` zone skins are otherwise forbidden to import —
as one of several hand-maintained or silently-noncovering sites a new skin
must currently clear. **Update (MOR-2059):** this specific instance is now
closed — the vocabulary moved to `frontend/src/presentation/layout-mode.ts`,
inside the presentation layer and off the skins forbidden-import list, and
`lib/stores/layout.svelte.ts` is now a re-export shim an author no longer
needs to touch to add a selectable id. MOR-2054's other named sites (not
enumerated in this document) are unaffected by MOR-2059 and are not
re-verified here — this update corrects only the claim this document itself
made about the `LayoutMode`/`CANONICAL_LAYOUT_MODES` site, not the overall
verdict in the table above, which still depends on sites this document does
not track.

## See also

- `docs/plans/2026-04-12-target-frontend-architecture.md` — the ADR this
  gate closes the loop on.
- `docs/architecture/building-a-skin.md` (MOR-2042) — the skin-authoring
  guide; this document records what is enforced, that one explains how to
  work within it. Its "Only if your skin should be reachable from
  user-facing layout preference" section points at
  `frontend/src/presentation/layout-mode.ts` for the same
  `LayoutMode`/`CANONICAL_LAYOUT_MODES` edit Gate 3 discusses above
  (MOR-2059) — inside the presentation layer, so no boundary-violation
  caveat is needed there any more.
