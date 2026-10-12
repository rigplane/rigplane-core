# Frontend mutation testing (Stryker)

Stryker mutates the modules listed below and checks whether their own
tests notice each change. The run is informational: it is not a required
check, has no threshold that fails it, and runs only nightly or by hand
(`.github/workflows/stryker-nightly.yml`).

## What is mutated, and by which tests

`frontend/stryker.config.mjs` names the mutated files in `mutate`:

- `frontend/src/lib/runtime/adapters/radio-view-model-adapter.ts`
- `frontend/src/semantic/radio-view-model.ts`
- `frontend/src/lib/runtime/props/panel-props.ts`
- `frontend/src/lib/runtime/adapters/ptt-restore-gate.ts`
- `frontend/src/lib/runtime/adapters/mod-input-gates.ts`
- `frontend/src/lib/runtime/adapters/control-feedback-projection.ts`
- `frontend/src/lib/runtime/adapters/pending-intent-table.ts`
- `frontend/src/lib/runtime/adapters/receiver-feedback.ts`
- `frontend/src/lib/runtime/adapters/level-feedback.ts`
- `frontend/src/lib/runtime/adapters/if-shift-feedback.ts`

`frontend/vitest.stryker.config.ts` names the tests that run against each
mutant in `test.include`:

- `frontend/src/lib/runtime/adapters/__tests__/radio-view-model-adapter.test.ts`
- `frontend/src/semantic/__tests__/radio-view-model.test.ts`
- `frontend/src/lib/runtime/props/__tests__/panel-props.test.ts`
- `frontend/src/lib/runtime/props/__tests__/notch-freq-domain.isolated.test.ts`
- `frontend/src/lib/runtime/props/__tests__/nr-level-projection.isolated.test.ts`
- `frontend/src/lib/runtime/adapters/__tests__/mod-input-auto.isolated.test.ts`
- `frontend/src/lib/runtime/adapters/__tests__/mod-input-tx-guard.isolated.test.ts`
- `frontend/src/lib/runtime/adapters/__tests__/filter-width-command-lifecycle.isolated.test.ts`
- `frontend/src/lib/runtime/adapters/__tests__/dsp-command-feedback.isolated.test.ts`
- `frontend/src/lib/runtime/adapters/__tests__/tx-aux-command-feedback.isolated.test.ts`
- `frontend/src/lib/runtime/adapters/__tests__/rf-sql-command-feedback.isolated.test.ts`
- `frontend/src/lib/runtime/adapters/__tests__/af-rf-power-command-feedback.isolated.test.ts`
- `frontend/src/lib/runtime/adapters/__tests__/cw-level-command-feedback.isolated.test.ts`
- `frontend/src/lib/runtime/adapters/__tests__/pbt-if-shift-command-feedback.isolated.test.ts`
- `frontend/src/lib/runtime/adapters/__tests__/pbt-if-shift-terminal-outcome-retention.isolated.test.ts`
- `frontend/src/lib/runtime/adapters/__tests__/mor1441-pending-discrete.isolated.test.ts`
- `frontend/src/lib/runtime/adapters/__tests__/mor1441-pending-frequency.isolated.test.ts`
- `frontend/src/lib/runtime/adapters/__tests__/mor1519-mode-armed.isolated.test.ts`
- `frontend/src/lib/runtime/adapters/__tests__/mor2111-repeater-conformance.isolated.test.ts`

The two mod-input test files import the `.svelte.ts` wrappers, not
`mod-input-gates.ts` directly; the wrappers call the mutated module for
each gate decision except the opt-in check (`if (!enabled) return`) and
the empty-pending check (`if (!p) return`), which stay in the wrappers.

Other test files in the suite also import `radio-view-model-adapter.ts`,
`radio-view-model.ts` and `panel-props.ts`; `ptt-restore-gate.ts` is
imported only by `tx-adapter.ts`, and `mod-input-gates.ts` only by the two
mod-input wrappers. Those other test files are
not in the Stryker test set, so a mutant reported as survived or without
coverage may still be killed by the full suite.

## Running it

Stryker is not a declared dependency of `frontend/package.json`; install it
without saving, using the versions pinned in the workflow:

```bash
cd frontend
npm ci
npm install --no-save @stryker-mutator/core@10.0.0 @stryker-mutator/vitest-runner@10.0.0
npx stryker run
```

Locally Stryker works in a sandbox copy under `frontend/.stryker-tmp/`.
The workflow adds `--inPlace`, which writes mutants into the checkout
itself; an interrupted in-place run can leave instrumented source behind,
so use it only in a throwaway checkout. Reports are written to
`frontend/reports/mutation/` (`mutation.html`, `mutation.json`); the
workflow uploads both as the `stryker-report-<run id>-<attempt>` artifact.

A full run of this configuration took 52 minutes (3118 s) on 2026-10-12
with `npx stryker run --concurrency 4` (Stryker core and vitest-runner 10.0.0)
on a 16-core Linux test host: 7112 mutants across the ten mutated files.
The workflow's `timeout-minutes` is 120 because the CI runner's speed for
this run has not been measured.

## Reading a survivor

Open `mutation.html` and select a file. Each mutant has a status:

- **Killed** — at least one test failed with the mutant in place.
- **Survived** — the tests that cover the mutated line all passed. The
  clear-text log in the job output prints the original and mutated lines
  and the tests that ran; the HTML report marks the mutant in the source. Either
  a test is missing an assertion on that behaviour, or the mutation is
  equivalent (it cannot change any observable result); decide which before
  writing a test.
- **No coverage** — no test in the Stryker test set executes that code.
- **Timeout** — the mutant made a test run past `timeoutMS`, usually an
  endless loop; Stryker counts it as detected.

`mutation.json` holds the same results in machine-readable form.

## Adapters that are not pure

Two adapters in the table below read the runtime singleton only to feed
`props/panel-props.ts`: `audio-adapter.ts`'s `deriveRxAudioProps` passes
runtime state to `toRxAudioProps`, and `vfo-adapter.ts`'s `deriveMainVfo`,
`deriveSubVfo` and `deriveVfoOps` pass it to `toVfoProps` and
`toVfoOpsProps`. Their state→props mapping therefore runs inside
`panel-props.ts`, which the Stryker run mutates.

The following files in `frontend/src/lib/runtime/adapters/` import a store
module (`$lib/stores/*`) or the runtime singleton (`../frontend-runtime`)
and call into it, so what they return or do depends on module-level state,
not only on their arguments. A row names the pure module that is mutated in
its place where one exists; the rest of each file is not mutated.

| File | Imports |
|---|---|
| `audio-adapter.ts` | `runtime` from `../frontend-runtime` |
| `panel-adapters.ts` | `runtime` from `../frontend-runtime`; `$lib/stores/capabilities.svelte`; `$lib/stores/commands.svelte`. The pure `control-feedback-projection.ts`, `pending-intent-table.ts`, `receiver-feedback.ts`, `level-feedback.ts` and `if-shift-feedback.ts` were split out of it and are mutated |
| `tx-adapter.ts` | `runtime` from `../frontend-runtime`. Its restore-gate helpers (`markerIsStrictlyNewer`, `canRestoreModAfterConfirmedOff`) are the pure `ptt-restore-gate.ts`, which is mutated; the local-audio attempt lifecycle over module state is not |
| `vfo-adapter.ts` | `runtime` from `../frontend-runtime` |
| `mod-input-auto.svelte.ts` | `getRadioState` from `$lib/stores/radio.svelte`; `getCapabilities` from `$lib/stores/capabilities.svelte` — gate logic lives in `mod-input-gates.ts` (mutated); only the store reads, `localStorage`, the `$state` opt-in flag, the memory-only `pending` latch (a plain `let`) with its consume-once logic in `restoreModInputAfterTx`, and the dispatch stay in the wrapper |
| `mod-input-tx-guard.svelte.ts` | `getRadioState` from `$lib/stores/radio.svelte`; `getCapabilities` from `$lib/stores/capabilities.svelte` — gate logic lives in `mod-input-gates.ts` (mutated); only the store reads, the `$state` armed latch and the dispatch stay in the wrapper |
| `qsy-history-adapter.ts` | `qsyHistory` from `$lib/stores/qsy-history.svelte` |
| `capabilities-adapter.ts` | `getControlRange`, `getMeterCalibration`, `getMeterRedline` from `$lib/stores/capabilities.svelte` |
| `lcd-chrome-adapter.ts` | getters and setters from `$lib/stores/lcd-contrast.svelte` and `$lib/stores/lcd-display-mode.svelte` |

`layout-mode-adapter.ts` also imports `$lib/stores/layout.svelte`, but only
to re-export `normalizeLayoutMode`, which `presentation/layout-mode.ts`
defines as a function of its argument; it is left out of the table.
