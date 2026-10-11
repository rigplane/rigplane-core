# Frontend mutation testing (Stryker)

Stryker mutates two pure view-model modules and checks whether their own
tests notice each change. The run is informational: it is not a required
check, has no threshold that fails it, and runs only nightly or by hand
(`.github/workflows/stryker-nightly.yml`).

## What is mutated, and by which tests

`frontend/stryker.config.mjs` names the mutated files in `mutate`:

- `frontend/src/lib/runtime/adapters/radio-view-model-adapter.ts`
- `frontend/src/semantic/radio-view-model.ts`

`frontend/vitest.stryker.config.ts` names the tests that run against each
mutant in `test.include`:

- `frontend/src/lib/runtime/adapters/__tests__/radio-view-model-adapter.test.ts`
- `frontend/src/semantic/__tests__/radio-view-model.test.ts`

Other test files in the suite also import these modules. They are not in
the Stryker test set, so a mutant reported as survived or without coverage
may still be killed by the full suite.

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

The following files in `frontend/src/lib/runtime/adapters/` import a store
module (`$lib/stores/*`) or the runtime singleton (`../frontend-runtime`)
and call into it, so what they return or do depends on module-level state,
not only on their arguments. They are not mutated; splitting their pure parts out is follow-up
work.

| File | Imports |
|---|---|
| `audio-adapter.ts` | `runtime` from `../frontend-runtime` |
| `panel-adapters.ts` | `runtime` from `../frontend-runtime`; `$lib/stores/capabilities.svelte`; `$lib/stores/commands.svelte` |
| `tx-adapter.ts` | `runtime` from `../frontend-runtime` |
| `vfo-adapter.ts` | `runtime` from `../frontend-runtime` |
| `mod-input-auto.svelte.ts` | `getRadioState` from `$lib/stores/radio.svelte`; `getCapabilities` from `$lib/stores/capabilities.svelte` |
| `mod-input-tx-guard.svelte.ts` | `getRadioState` from `$lib/stores/radio.svelte`; `getCapabilities` from `$lib/stores/capabilities.svelte` |
| `qsy-history-adapter.ts` | `qsyHistory` from `$lib/stores/qsy-history.svelte` |
| `capabilities-adapter.ts` | `getControlRange`, `getMeterCalibration`, `getMeterRedline` from `$lib/stores/capabilities.svelte` |
| `lcd-chrome-adapter.ts` | getters and setters from `$lib/stores/lcd-contrast.svelte` and `$lib/stores/lcd-display-mode.svelte` |

`layout-mode-adapter.ts` also imports `$lib/stores/layout.svelte`, but only
to re-export `normalizeLayoutMode`, which `presentation/layout-mode.ts`
defines as a function of its argument; it is left out of the table.
