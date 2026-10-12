# Release-gate tiers

What the workflows in `.github/workflows/` run at each stage between a branch and a PyPI release, on which runner labels, from which trigger, and whether branch protection requires the result. It covers Core only; Pro documents its own tiers.

Written against `main` at commit `ba2a3a18`. Triggers, jobs and runner labels come from the workflow file named in each row.

## Required and informational

The required status contexts are the ones this command returns:

```bash
gh api repos/rigplane/rigplane-core/branches/main/protection/required_status_checks
```

On 2026-10-12 it returned two contexts, `quick` and `Agent Review Gate`; [base-controlled-required-gates.md](../internals/base-controlled-required-gates.md) describes how each is produced. **Required** below means one of those two; **informational** means branch protection does not require it. Re-run the command before relying on either word.

## Tiers

| Tier | What runs | Runner labels | Trigger | Status |
|---|---|---|---|---|
| Development | Changed-scope checks chosen by the author; `focused.yml` runs pytest, Ruff and Vitest targets against one commit | `[self-hosted, linux, build]` (`focused.yml`) | The author; `workflow_dispatch` for `focused.yml` | Informational |
| Integration | Per-pull-request and per-merge checks, and scheduled checks of `main` | `ubuntu-latest`, `[self-hosted, linux, build]`, `[self-hosted, macOS, ARM64, platform]`, `[self-hosted, macOS, ARM64, bench]` | `pull_request`, `push` to `main`, `schedule` | Required: `quick`, `Agent Review Gate`. All else informational |
| Release Candidate | `full.yml` dispatched with `rc=true` | `[self-hosted, linux, build]` | `workflow_dispatch` | Informational |
| Release (Stable) | `publish.yml`: `validate`, `build`, `publish` | `ubuntu-latest` | `release` `published`, pre-releases included | Not a status; the `needs` chain gates the PyPI upload |

## Development

`AGENTS.md`, section "Delivery batching and CI cadence", sets which checks a change runs and when: changed-scope tests and lint while developing, then one natural `quick` run on the final candidate.

`focused.yml` is a manual `workflow_dispatch` run that checks out the commit given as the `revision` input and runs only the pytest, Ruff and Vitest targets passed as JSON arrays. It is not a required status.

While a pull request is a draft, the self-hosted jobs `quick` (`quick.yml`) and `visual` (`visual.yml`) are skipped by their `if:` conditions (`github.event.pull_request.draft == false`). They start when the pull request is marked ready for review.

## Integration

Pull request to `main`, and push to `main`:

| Workflow | Trigger | Runner labels | What it runs | Status |
|---|---|---|---|---|
| `quick.yml` (job `quick`) | `pull_request` (opened, reopened, synchronize, ready_for_review) and `push`, both to `main`; documentation paths ignored | `quick`: `[self-hosted, linux, build]`; `classify`: `ubuntu-latest` | Path-selected: Ruff, import-linter, three `validate --dry-run` golden gates and `pytest tests/` on Python 3.11; frontend lint, i18n check, type-check, Vitest, build and Playwright steps; workflow-control tests | Required (`quick`) |
| `docs-only-quick.yml` | `pull_request_target` to `main` (opened, reopened, synchronize, ready_for_review) | `ubuntu-latest` | No tests. Publishes the `quick` status as success when every changed file passes `.github/scripts/docs-only-paths.js: allDocumentationFiles` | Required (`quick`), for documentation-only heads |
| `agent-review-gate.yml` | `pull_request` (opened, reopened, synchronize, ready_for_review) and `issue_comment` (created, edited, deleted) | `ubuntu-latest` | Publishes `Agent Review Gate` from the `Agent Review:` comments on the pull request; rules in `AGENTS.md`, "Protected main and review gate" | Required (`Agent Review Gate`) |
| `agent-review-gate-v2.yml`, `quick-v2.yml` | `pull_request_target` to `main`, plus `issue_comment` and `workflow_run` of `Tests (quick)` respectively | `ubuntu-latest` | Publish the observation-only contexts `Agent Review Gate v2 observe` and `quick-v2-observe` | Informational; [base-controlled-required-gates.md](../internals/base-controlled-required-gates.md) says they must never be configured as required checks |
| `freeze-gate.yml` | `pull_request` (opened, edited, synchronize, reopened, labeled, unlabeled, ready_for_review) | `ubuntu-latest` | Checks the pull request title, body and labels against the freeze contract in `AGENTS.md`, "Stabilization freeze" | Informational (its own comment says advisory) |
| `visual.yml` | `pull_request` and `push` to `main` when a non-documentation file under `frontend/` changes; `workflow_dispatch` | `changes`: `ubuntu-latest`; `visual`: `[self-hosted, linux, build]` | Pixel-diff against the approved baselines; the `regenerate` dispatch input renders new baselines and uploads them as an artifact | Informational |
| `consumer-contracts-gate.yml` | `pull_request` and `push` to `main`, path-filtered | `ubuntu-latest` | `scripts/run_consumer_contracts.py`: checks the expectations in `contracts/consumer-expectations/` against the producer surface | Informational |
| `state-types-gate.yml` | `pull_request` and `push` to `main`, path-filtered | `ubuntu-latest` | `scripts/gen_state_types.py --check`: fails when the generated block of `frontend/src/lib/types/state.ts` is stale | Informational |
| `rebrand-gate.yml` | `pull_request` and `push` to `main`, path-filtered | `ubuntu-latest` | `.github/scripts/check-rebrand-allowlist.sh` | Informational |
| `doc-citation-gate.yml` | `pull_request` and `push` to `main`, only when `.github/scripts/check-doc-citations.sh` or the workflow file changes | `ubuntu-latest` | The citation, link and dangling-citation checks of `check-doc-citations.sh` | Informational |

`quick.yml` passes no `-m` to pytest, so `addopts` in `pyproject.toml` (`-m 'not slow and not benchmark'`) deselects the `slow`-marked tests; `tests/test_ci_pytest_invocation.py: test_quick_passes_no_marker_selection` pins that. The ledger meta-tests described in [Test-quality gates](test-quality-gates.md) are ordinary files under `tests/`, so they run inside the `pytest tests/` step of `quick`.

Scheduled against `main` (each also has `workflow_dispatch`):

| Workflow | Trigger (times in UTC) | Runner labels | What it runs | Status |
|---|---|---|---|---|
| `full.yml` | Mon, Wed, Fri 03:00; also `push` to `main` when the head commit message contains `[full-ci]` | `[self-hosted, linux, build]` | Python 3.11, 3.12 and 3.13: frontend checks and build, Ruff on `src/rigplane/web`, mypy strict on the web boundary, import-linter, `pytest tests/` with `slow` deselected; plus the `capture-harness` job. Not path-selected | Informational |
| `platform-subset.yml` | Daily 01:30 | `[self-hosted, macOS, ARM64, platform]` | A fixed list of platform-sensitive test files; see [Platform subset workflow](../internals/ci-platform-subset.md) | Informational |
| `bench-nightly.yml` | Daily 01:30 (weekday and weekend entries) | `[self-hosted, macOS, ARM64, bench]` | Checks of the bench radios; see [Bench nightly](../internals/bench-nightly.md) | Informational |
| `soak-nightly.yml` | Daily 03:17 | `[self-hosted, linux, build]` | A soak of the web stack against the IC-7610 simulator, 3600 s unless the `duration` dispatch input says otherwise; see [Operated soak](../internals/soak.md) | Informational |
| `stryker-nightly.yml` | Daily 03:17 | `[self-hosted, linux, build]` | Stryker mutation run on the frontend view-model modules; see [Frontend mutation testing](mutation-testing-frontend.md) | Informational |
| `mutmut-weekly.yml` | Sunday 22:30 | `[self-hosted, linux, build]` | mutmut sweep configured by `[tool.mutmut]` in `pyproject.toml`; see [Mutation testing](mutation-testing.md) | Informational |

## Release Candidate

The RC tier is one dispatch of `full.yml` on `main`:

```bash
gh workflow run full.yml --ref main -f rc=true
```

`rc` is a boolean `workflow_dispatch` input, default false. When it is true the `Run tests` step passes `-m "not benchmark"`, which replaces the `addopts` selection, so the `slow`-marked tests run as well. Everything else is the scheduled `full.yml` run described above: the `test` job on Python 3.11, 3.12 and 3.13 and the `capture-harness` job, on `[self-hosted, linux, build]`. `tests/test_ci_pytest_invocation.py: test_full_runs_the_slow_tests_only_for_an_rc_dispatch` pins the selection for `rc` true, false and unset.

The run is a workflow run on `main`, not a status on any pull request, and it is informational: no workflow reads its result, and `publish.yml` does not depend on it.

## Release (Stable)

`publish.yml` runs on `release` `published`:

1. `validate` on `ubuntu-latest`, Python 3.12 and 3.13 (3.11 is not in its matrix): frontend `npm run check`, Vitest and build; Ruff check and format check on `src/` and `tests/`; mypy strict on `src/rigplane/web`; import-linter; `pytest tests/ -n auto -m 'not benchmark'`, so `slow`-marked tests run (`test_publish_validate_runs_the_slow_tests` in `tests/test_ci_pytest_invocation.py`).
2. `build`, which `needs` `validate`: frontend build, `uv build`, an installed-profile smoke of the wheel, artifact upload.
3. `publish`, which `needs` `build`: uploads to PyPI through the `pypi` environment. On 2026-10-12 that environment had no protection rules (`gh api repos/rigplane/rigplane-core/environments`), so no approval step sits between `build` and `publish`.

The release exists before this workflow starts, so a red `validate` or `build` stops the PyPI upload and leaves the tag and the GitHub Release in place. `publish.yml` has no `prerelease` condition, so it also runs for a release marked pre-release: on 2026-10-12 `gh release list` showed `v3.0.0b15` as a pre-release, and `gh run list --workflow publish.yml` showed a `release`-event run for it. The only workflow that checks the pre-release flag is `dispatch-downstream.yml`, listed below.

## Outside the tiers

`docs.yml` and `dispatch-downstream.yml` publish rather than check; the other three start only from a manual or reusable trigger.

| Workflow | Trigger | Runner labels | What it does |
|---|---|---|---|
| `docs.yml` | `push` to `main` when `docs/**`, `mkdocs.yml`, `src/rigplane/**`, the build script or the workflow file change; `workflow_dispatch` | `ubuntu-latest` | Builds the versioned documentation site, deploys it to GitHub Pages from `main`, then pings IndexNow. No `pull_request` trigger |
| `dispatch-downstream.yml` | `release` `published`, only when the release is not a pre-release and its body lacks `[no-downstream-bump]` | `ubuntu-latest` | Opens version-bump pull requests in downstream repositories when its app secrets are set |
| `fleet-native-focused.yml` | `workflow_call` only | Selected by its matrix | Native Core CLI checks, run when another workflow calls it |
| `private-core-artifact.yml`, `private-core-b14-artifact.yml` | `workflow_dispatch` only | `[self-hosted, linux, build, X64]` | Build Core artifacts from a commit pinned in the workflow and upload them |

## What no workflow enforces

No workflow in `.github/workflows/` makes a bench-certification night, a 24-hour soak or an open-issue count a condition of an RC dispatch or of `publish.yml`.
