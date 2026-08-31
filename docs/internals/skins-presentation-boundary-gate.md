# Skins Presentation-Boundary Release Gate

Status: MOR-2034 release gate (epic MOR-2035)
Date: 2026-08-31

## The criterion

The owner's stated acceptance criterion for the skins architecture, verbatim:

> I can build a theme with its own S-meters and indicators — up to
> replicating an IC-7610/FTX-1 front panel or drawing my own SDR interface —
> without touching anything below the presentation layer.

This decomposes into two independent, separately-checkable guarantees:

1. **Presentation-layer boundary** — a skin (and the panels/layouts it is
   built from) can reach the presentation layer's own exports, and nothing
   below it: no transport, no store, no audio manager, imported directly.
2. **Truth derivation stays out of skins** — a skin presents a value; it
   never computes one. Calibration, unit conversion, and any other "what is
   actually true" derivation happens once, below the presentation layer, and
   every presentation-layer consumer reads the same answer.

This document is this repo's `docs/internals/*.md` MOR-XXXX-keyed
executable-gate entry (the form `radio-state-pipeline-validation.md` uses for
MOR-348 and `backend-neutral-readiness-gap-register.md` uses for MOR-424) for
these two guarantees specifically. It does not restate the mechanisms below —
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
| Both directions proven | same file, e.g. `'rejects a skins .ts file importing $lib/stores/* directly...'` paired with `'allows skins/registry.ts to import layout-mode normalization through the adapter...'` | For every forbidden import there is a paired case proving the *legitimate* migrated replacement still passes — a custom skin's own instruments are not collateral damage of this rule | — |
| Skin registry is exhaustive and its entry points really mount | `frontend/src/skins/__tests__/entrypoints.test.ts`: `SKIN_ENTRYPOINT_COVERAGE` | Every `SkinId` in `frontend/src/skins/registry.ts` mounts its declared component through the same `loadSkin()` `App.svelte` calls | The one `'covered-elsewhere'` entry (`dual-receiver-cockpit`) is a substring check on another test file's source text, not a behavioral one — documented as such in the file's own header, not restated here as more than it is |

## Gate 2 — truth derivation stays out of skins

The counterexample that motivated this gate was found and fixed the same
night as this entry: two independent S-unit derivations disagreed because
`components-v2/panels/meter-utils.ts`'s old `formatSMeter` reimplemented a
hardcoded 6 dB/S-unit ladder instead of calling the shared, table-driven
`smeter-scale.ts`. That ladder happened to match IC-7300's real curve
(uniform) but disagreed with FTX-1's real, non-uniform curve. Consolidated
under MOR-2024; `formatSMeter` now delegates entirely to
`frontend/src/components-v2/meters/smeter-scale.ts`: `calibratedToSUnit`.

| Mechanism | File : symbol | What it proves | Honest limit |
|---|---|---|---|
| Meter conformance contract | `frontend/src/components-v2/meters/__tests__/meter-contract.ts`: `METER_REGISTRY` | Census-checks every `.svelte` file directly under `components-v2/meters/` (today: `BarGauge.svelte`, `LinearSMeter.svelte`) is registered and mounts | Explicitly scoped to that one directory by its own file header — not to `skins/` or `components-v2/panels/`, which is where a skin's *own* custom instruments actually live |
| Domain-conformance suite | `frontend/src/components-v2/meters/__tests__/meter-contract.test.ts` | Mounts `LinearSMeter` against a deliberately non-uniform synthetic calibration table and asserts its rendered S-unit/dBm text equals a fresh `calibratedToSUnit`/`calibratedToDbm` call at three probes chosen so no fixed per-S-unit step can pass all three (full reasoning in the file's own header) | Cannot rule out a byte-identical reimplementation of the same interpolation — a DRY concern, not a correctness one, per the same header |
| Typed `data-*` vocabulary for stylesheet authors | `frontend/src/presentation/languages/state-vocabulary.ts` | A skin's stylesheet can key off `RxTxSurface`/`MetersSurface` visual state (`RF_STATES`, `TX_SESSION_STATES`, etc.) from a typed export instead of reverse-engineering it from semantic source, and instead of computing it | Scoped to the two files the MOR-2036 owner ruling names; `VfoSurface`'s own vocabulary has no stylesheet consumer yet and is not exported here (see the file's own header) |

### Residual found by this ticket, and closed for two of the three live instances found

`METER_REGISTRY`'s census is scoped to `components-v2/meters/`. Grepping
every production (non-test) importer of `smeter-scale.ts` finds three call
sites outside that directory — i.e., in the presentation-layer code a skin
author actually writes:

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
3. `frontend/src/skins/sdr-test/SdrVfoScreen.svelte` — initially counted as a
   third live instance; re-derived and struck. `SdrTestSkin.svelte`'s own
   header states this component has not been mounted since MOR-1065
   (`RadioLayout` no longer swaps it in), kept only "as the pre-migration
   prototype reference pending the MOR-1099 legacy-wrapper retirement." It
   is unreachable dead code, not a live presentation-layer consumer, and
   that unreachability is already self-documented — not a new finding.

This PR adds a non-uniform-calibration discrimination suite to both real
test files above (instances 1 and 2), reusing `meter-contract.test.ts`'s own
fixture and probe reasoning against the real mounted component /
plain-function call site, proven both ways during development (a throwaway
hardcoded-6-dB-ladder mutant in each production file, run against the new
tests, then reverted): the mutant failed all three new probes in both
files with the same off-by-one-S-unit pattern the meter-contract.test.ts
header predicts; the real code passes all of them.

What is **not** closed by this PR, and is filed as a follow-up rather than
attempted here: `METER_REGISTRY`'s census has no structural mechanism that
would force a *future* skin's new S-meter to get this same discriminating
regression test — closing instances 1 and 2 required a human (or agent) to
notice the gap and hand-write the probes, same as it always has. Widening
the census itself (a directory scope of more than one path, or a third
`MeterValueDomain` shape for multi-source components like `AmberSmeter`) is
a structural change to MOR-2037's own contract and is out of this ticket's
guardrails.

## See also

- `docs/plans/2026-04-12-target-frontend-architecture.md` — the ADR this
  gate closes the loop on.
- `docs/architecture/building-a-skin.md` (MOR-2042) — the skin-authoring
  guide; this document records what is enforced, that one explains how to
  work within it.
