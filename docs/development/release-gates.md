# Release-gate tiers

What the workflows in `.github/workflows/` run at each stage between a branch and a PyPI release, on which runner labels, from which trigger, and whether branch protection requires the result. It covers Core only; Pro documents its own tiers.

Written against `main` at commit `ba2a3a18`. Triggers, jobs and runner labels come from the workflow file named in each row.

## Required and informational

The required status contexts are the ones this command returns:

```bash
gh api repos/rigplane/rigplane-core/branches/main/protection/required_status_checks
```

On 2026-10-12 it returned two contexts, `quick` and `Agent Review Gate`; [base-controlled-required-gates.md](../internals/base-controlled-required-gates.md) describes how each is produced. **Required** below means one of those two, which branch protection requires before a pull request merges; **informational** means it does not. Re-run the command before relying on either word.

## Tiers

| Tier | What runs | Runner labels | Trigger | Status |
|---|---|---|---|---|
| Development | Every pull request to `main`, before merge: the two required contexts and the other per-pull-request checks; `focused.yml` is an optional author tool | `[self-hosted, linux, light]`, `[self-hosted, linux, build]` | `pull_request`, `pull_request_target`, `issue_comment`, `workflow_run`; `workflow_dispatch` for `focused.yml` and `visual.yml` | Required: `quick`, `Agent Review Gate`. All else informational |
| Merge queue | The tree that lands on `main`: `quick.yml` on the batch, with the queue-side counterparts of the two pull-request gates | `[self-hosted, linux, light]`, `[self-hosted, linux, build]` | `merge_group` | Required: `quick`, `Agent Review Gate`, `Evaluate stabilization-freeze PR contract` |
| Integration | On a schedule, on `main`. There is no run on the push that follows the merge queue: the queue already tested that tree (MOR-3878) | `[self-hosted, linux, build]`, `[self-hosted, macOS, ARM64, platform]`, `[self-hosted, macOS, ARM64, bench]` | `schedule` | Informational |
| Release Candidate | `full.yml` on a `v*rc*` tag, or dispatched with `rc=true` | `[self-hosted, linux, build]` | `push` of a tag, `workflow_dispatch` | Informational |
| Release (Stable) | `publish.yml`: `validate` (including the radio-smoke receipt gate), `build`, `publish` | `[self-hosted, linux, build]` | `release` `published`, pre-releases included | Not a status; the `needs` chain gates the PyPI upload |

## Development

Every pull request to `main`, before it merges. `AGENTS.md`, section "Delivery batching and CI cadence", sets which checks a change runs and when: changed-scope tests and lint while developing, then one natural `quick` run on the final candidate.

| Workflow | Trigger | Runner labels | What it runs | Status |
|---|---|---|---|---|
| `quick.yml` (jobs `quick-frontend`, `quick-python`, `quick`) | `pull_request` to `main` (opened, reopened, synchronize, ready_for_review) and `merge_group`; documentation paths ignored on `pull_request` | `quick-frontend`, `quick-python`: `[self-hosted, linux, build]`, in parallel; `quick` (aggregator): `[self-hosted, linux, light]`; `classify`: `[self-hosted, linux, light]` | Path-selected. `quick-python`: Ruff, import-linter, three `validate --dry-run` golden gates and `pytest tests/` on Python 3.11, and the workflow-control tests. `quick-frontend`: frontend lint, i18n check, type-check, Vitest, build and Playwright steps. `quick` runs no checks: it fails unless every selected half succeeded and every unselected half was skipped | Required (`quick`) |
| `docs-only-quick.yml` | `pull_request_target` to `main` (opened, reopened, synchronize, ready_for_review) | `[self-hosted, linux, light]` | No tests. Publishes the `quick` status as success when every changed file passes `.github/scripts/docs-only-paths.js: allDocumentationFiles` | Required (`quick`), for documentation-only heads |
| `agent-review-gate.yml` | `pull_request` (opened, reopened, synchronize, ready_for_review, labeled, unlabeled), `pull_request_review` (submitted, dismissed), `issue_comment` (created, edited, deleted) and `workflow_dispatch` (input `pr_number`, to re-evaluate after a review thread is resolved) | `[self-hosted, linux, light]` | Publishes `Agent Review Gate`: green when a trusted exact-head `Agent Review: PASS` directive (posted after the local Codex CLI review of that head) exists and no active BLOCKED directive does; the GitHub Codex bot review and its open threads are advisory text only | Required (`Agent Review Gate`) |
| `agent-review-gate-v2.yml`, `quick-v2.yml` | `pull_request_target` to `main`, plus `issue_comment` and `workflow_run` of `Tests (quick)` respectively | `[self-hosted, linux, light]` | Publish the observation-only contexts `Agent Review Gate v2 observe` and `quick-v2-observe` | Informational; [base-controlled-required-gates.md](../internals/base-controlled-required-gates.md) says they must never be configured as required checks |
| `freeze-gate.yml` | `pull_request` (opened, edited, synchronize, reopened, labeled, unlabeled, ready_for_review) | `[self-hosted, linux, light]` | Checks the pull request title, body and labels against the freeze contract in `AGENTS.md`, "Stabilization freeze" | Required (`Evaluate stabilization-freeze PR contract`) |
| `visual.yml` | `pull_request` to `main` when a non-documentation file under `frontend/` changes; `workflow_dispatch` | `changes`: `[self-hosted, linux, light]`; `visual`: `[self-hosted, linux, build]` | Pixel-diff against the approved baselines; the `regenerate` dispatch input renders new baselines and uploads them as an artifact | Informational |
| `consumer-contracts-gate.yml` | `pull_request` to `main`, path-filtered | `[self-hosted, linux, light]` | `scripts/run_consumer_contracts.py`: checks the expectations in `contracts/consumer-expectations/` against the producer surface | Informational |
| `state-types-gate.yml` | `pull_request` to `main`, path-filtered | `[self-hosted, linux, light]` | `scripts/gen_state_types.py --check`: fails when the generated block of `frontend/src/lib/types/state.ts` is stale | Informational |
| `rebrand-gate.yml` | `pull_request` to `main`, path-filtered | `[self-hosted, linux, light]` | `.github/scripts/check-rebrand-allowlist.sh` | Informational |
| `doc-citation-gate.yml` | `pull_request` to `main`, only when `.github/scripts/check-doc-citations.sh` or the workflow file changes | `[self-hosted, linux, light]` | The citation, link and dangling-citation checks of `check-doc-citations.sh` | Informational |
| `focused.yml` (optional, run by the author) | `workflow_dispatch` | `[self-hosted, linux, build]` | Checks out the commit given as the `revision` input and runs only the pytest, Ruff and Vitest targets passed as JSON arrays | Informational; not a required status |

While a pull request is a draft, the self-hosted jobs `quick-frontend`, `quick-python` and `quick` (`quick.yml`) and `visual` (`visual.yml`) are skipped by their `if:` conditions (`github.event.pull_request.draft == false`). They start when the pull request is marked ready for review.

`quick.yml` passes no `-m` to pytest, so `addopts` in `pyproject.toml` (`-m 'not slow and not benchmark'`) deselects the `slow`-marked tests; `tests/test_ci_pytest_invocation.py: test_quick_passes_no_marker_selection` pins that. The ledger meta-tests described in [Test-quality gates](test-quality-gates.md) are ordinary files under `tests/`, so they run inside the `pytest tests/` step of `quick`.

## Merge queue

`quick.yml` also runs on `merge_group`, with the same `classify` job and the same `quick` job as the pull-request run (no second copy). `merge-queue-gates.yml` re-derives the two pull-request-level gates (`Agent Review Gate`, `Evaluate stabilization-freeze PR contract`) for the queue ref. The informational path-filtered gates (`visual.yml`, `consumer-contracts-gate.yml`, `state-types-gate.yml`, `rebrand-gate.yml`, `doc-citation-gate.yml`) do not run on `merge_group`, because that event cannot be path-filtered and they are not required contexts; the scheduled `full.yml` run calls them on `main`.

When the first entry of a merge group has a tree byte-identical to the tree of its pull request head, `quick` already passed on exactly that code: the `classify` job answers `reuse=true` (`.github/scripts/queue-tree-reuse.py`, run from the base commit, not the queued one; it also needs the head's `quick` check run to have succeeded) and the `quick` job is skipped, which satisfies the required context. Entries behind another entry, or on a moved `main`, have a different tree and run in full. Any doubt or API error answers `reuse=false`.

## Integration

On a schedule, against `main`. Every run here is informational and attached to no pull request. No workflow except `docs.yml` (documentation deploy) runs on the push to `main`: every change reaches `main` through the merge queue, whose `merge_group` run tested the exact tree that lands, so a push run only repeated it (MOR-3878).

Scheduled against `main` (each also has `workflow_dispatch`):

| Workflow | Schedule (UTC) | Runner labels | What it runs | Status |
|---|---|---|---|---|
| `full.yml` | Weekdays, every 3 hours from 00:00. A tick is skipped when that commit already passed `full.yml` (the `fresh` job) | `[self-hosted, linux, build]` | Python 3.11, 3.12 and 3.13: frontend checks and build, Ruff on `src/rigplane/web`, mypy strict on the web boundary, import-linter, `pytest tests/` with `slow` deselected; plus the `capture-harness` job; plus, called from the same file, the informational gates `visual.yml`, `state-types-gate.yml`, `consumer-contracts-gate.yml`, `doc-citation-gate.yml` and `rebrand-gate.yml`. Not path-selected. Also updates the tests, version and mypy badges | Informational |
| `platform-subset.yml` | Daily 01:30 | `[self-hosted, macOS, ARM64, platform]` | A fixed list of platform-sensitive test files; see [Platform subset workflow](../internals/ci-platform-subset.md) | Informational |
| `bench-nightly.yml` | Daily 01:30 (weekday and weekend entries) | `[self-hosted, macOS, ARM64, bench]` | Checks of the bench radios; see [Bench nightly](../internals/bench-nightly.md) | Informational |
| `soak-nightly.yml` | Daily 03:17 | `[self-hosted, linux, build]` | A soak of the web stack against the IC-7610 simulator, 3600 s unless the `duration` dispatch input says otherwise; see [Operated soak](../internals/soak.md) | Informational |
| `stryker-nightly.yml` | Daily 03:17 | `[self-hosted, linux, build]` | Stryker mutation run on the frontend view-model modules; see [Frontend mutation testing](mutation-testing-frontend.md) | Informational |
| `mutmut-weekly.yml` | Sunday 22:30 | `[self-hosted, linux, build]` | mutmut sweep configured by `[tool.mutmut]` in `pyproject.toml`; see [Mutation testing](mutation-testing.md) | Informational |

## Release Candidate

The RC tier is `full.yml` on a release-candidate tag (`vX.Y.ZrcN`, pattern `v*rc*`), or one dispatch on `main`:

```bash
gh workflow run full.yml --ref main -f rc=true
```

`rc` is a boolean `workflow_dispatch` input, default false. When it is true, or the run is on a tag, the `Run tests` step passes `-m "not benchmark"`, which replaces the `addopts` selection, so the `slow`-marked tests run as well. Everything else is the scheduled `full.yml` run described above: the `test` job on Python 3.11, 3.12 and 3.13 and the `capture-harness` job, on `[self-hosted, linux, build]`. `tests/test_ci_pytest_invocation.py: test_full_runs_the_slow_tests_only_for_an_rc_dispatch_or_rc_tag` pins the selection for `rc` true, false and unset, and for a tag. The final is a separate version-only commit on top of the candidate (see "rc receipts for a version-only final" below), so the candidate run plus the checker's version-only proof cover the final tree.

The run is a workflow run on `main` or on the tag, not a status on any pull request, and it is informational: no workflow reads its result, and `publish.yml` does not depend on it.

## Release (Stable)

`publish.yml` runs on `release` `published`:

1. `validate` on `[self-hosted, linux, build]`, Python 3.12 and 3.13 (3.11 is not in its matrix). Its first step after checkout is the radio-smoke receipt gate (see below): it downloads `radio-smoke-rx.json` and `radio-smoke-tx.json` from the triggering release with the job's `GITHUB_TOKEN` and runs `scripts/release/check_radio_smoke.py check --tag "$TAG" --git-sha "$GITHUB_SHA"` on them. A missing asset fails the download; a red receipt fails the checker — either way the job is red before the expensive steps start. The rest of the job: frontend `npm run check`, Vitest and build; Ruff check and format check on `src/` and `tests/`; mypy strict on `src/rigplane/web`; import-linter; `pytest tests/ -n auto -m 'not benchmark'`, so `slow`-marked tests run (`test_publish_validate_runs_the_slow_tests` in `tests/test_ci_pytest_invocation.py`).
2. `build`, which `needs` `validate`: frontend build, `uv build`, an installed-profile smoke of the wheel, artifact upload.
3. `publish`, which `needs` `build`: uploads to PyPI through the `pypi` environment. On 2026-10-12 that environment had no protection rules (`gh api repos/rigplane/rigplane-core/environments`), so no approval step sits between `build` and `publish`.

The release exists before this workflow starts, so a red `validate` or `build` stops the PyPI upload and leaves the tag and the GitHub Release in place. `publish.yml` has no `prerelease` condition, so it also runs for a release marked pre-release: on 2026-10-12 `gh release list` showed `v3.0.0b15` as a pre-release, and `gh run list --workflow publish.yml` showed a `release`-event run for it. The only workflow that checks the pre-release flag is `dispatch-downstream.yml`, listed below.

### Radio-smoke receipts

Both receipts are JSON objects written before the release is published and uploaded as release assets — `radio-smoke-rx.json` by the RX-only smoke procedure and `radio-smoke-tx.json` by the live TX smoke (Pro `qa/bench/dogfood/tx_smoke.py`, which keys an IC-705 into a dummy load). The format below is the contract between those producers and `scripts/release/check_radio_smoke.py`; the checker exits non-zero with one line per violation when any rule breaks.

| Field | Fragment | Type | Rule |
|---|---|---|---|
| `schema` | both | number | exactly `1` |
| `kind` | both | string | `"rx"` in the RX fragment, `"tx"` in the TX fragment |
| `core_version` | both | string | the tag version without its leading `v` — or, for a version-only final (see below), the candidate version `X.Y.ZrcN` with the same `X.Y.Z` |
| `git_sha` | both | string | the tagged commit (`GITHUB_SHA` of the release event) — or, for a version-only final, the candidate commit, with both receipts naming the same one |
| `verdict` | both | string | `"PASS"` |
| `generated_at` | both | string | ISO 8601 timestamp, parseable and not in the future |
| `radios` | `rx` | object | non-empty map of radio name to `"PASS"` |
| `sequences` | `tx` | object | exactly the keys `single`, `rekey_150ms`, `rekey_immediate`, `double_cycle`, `force_off_inflight`, each `"PASS"` |
| `stuck_tx` | `tx` | boolean | `false` — no wedged transmitter was observed |
| `release_debt` | `tx` | boolean | `false` — no undrained TX-side work remained |
| `ended_rx` | `tx` | boolean | `true` — a fresh radio-sourced RX observation closed the run |

### rc receipts for a version-only final

Release candidates keep the rc version in the version file (e.g. `3.1.0rc1` in `pyproject.toml`). The final is a separate commit that differs from the candidate only in the version: the `version =` line in `pyproject.toml`, the project's own entry in `uv.lock`, and the heading/compare links of `docs/CHANGELOG.md`. The candidate's radio smoke (the RX+TX receipts) is not re-run for such a version-only final, while `publish.yml`'s `validate` job still runs its own checks (frontend, Ruff, mypy, import-linter, the pytest matrix) on the final commit; any other difference means a new rc.

So a receipt pair produced against the candidate (`core_version` `X.Y.ZrcN`, `git_sha` the candidate commit, tag `vX.Y.ZrcN` on it) also validates for the final tag `vX.Y.Z` — the receipts keep naming what was really tested, and the checker (`scripts/release/check_radio_smoke.py check --tag vX.Y.Z --git-sha FINAL --rx … --tx … --repo …`) proves the version-only diff in the checked-out repo. `publish.yml`'s `validate` job checks out full history with tags (`fetch-depth: 0`) and passes `--repo "$GITHUB_WORKSPACE"` for exactly this. A receipt pair naming the final tag version and commit validates exactly as before, without touching the repo. A mix of one final-matching and one candidate receipt fails.

The candidate path requires all of the following; each failure prints one `rc:`-prefixed line and fails the gate:

- both receipts name the same candidate (`core_version` and `git_sha` equal; the version is `X.Y.ZrcN` with the same `X.Y.Z` as the tag, `N >= 1`, and the sha is 40 hex);
- tag `vX.Y.ZrcN` resolves through `refs/tags/` to the receipt `git_sha` (a same-named branch is not a tag);
- the candidate commit is an ancestor of the final commit;
- `git diff-tree -r --raw --no-renames --no-abbrev -z` candidate-to-final lists only identical-mode (`100644`) modifications of exactly `pyproject.toml`, `uv.lock` and `docs/CHANGELOG.md` — any other path, status, mode change, symlink, or rename fails;
- both revisions of each file are read as raw bytes and must be byte-identical once the single permitted version assignment is normalized away: the `[project]` `version = "..."` line in `pyproject.toml`, the project's own `[[package]]` `version = "..."` line in `uv.lock` (the project name is read from the final `pyproject.toml`, never hard-coded), the `## [` headings and `[name]: http(s)://…` link reference definitions in `docs/CHANGELOG.md` — any other byte difference fails, including comments, whitespace, and binary bodies;
- both sides carry the claimed versions (candidate files say `X.Y.ZrcN`, final files say `X.Y.Z`).

A candidate of a different `X.Y.Z`, a malformed sha, a missing repo/commit/tag, or any git error fails closed. Verdict, timestamps and the per-kind rules are unchanged in both modes.

## Outside the tiers

`docs.yml` and `dispatch-downstream.yml` publish rather than check; the other three start only from a manual or reusable trigger.

| Workflow | Trigger | Runner labels | What it does |
|---|---|---|---|
| `docs.yml` | `push` to `main` when `docs/**`, `mkdocs.yml`, `src/rigplane/**`, the build script or the workflow file change; `workflow_dispatch` | `[self-hosted, linux, light]` | Builds the versioned documentation site, deploys it to GitHub Pages from `main`, then pings IndexNow. No `pull_request` trigger |
| `dispatch-downstream.yml` | `release` `published`, only when the release is not a pre-release and its body lacks `[no-downstream-bump]` | `[self-hosted, linux, light]` | Opens version-bump pull requests in downstream repositories when its app secrets are set |
| `fleet-native-focused.yml` | `workflow_call` only | Selected by its matrix | Native Core CLI checks, run when another workflow calls it |
| `private-core-artifact.yml`, `private-core-b14-artifact.yml` | `workflow_dispatch` only | `[self-hosted, linux, build, X64]` | Build Core artifacts from a commit pinned in the workflow and upload them |

## What no workflow enforces

Live radio smoke **is** enforced: since MOR-3839, `publish.yml`'s `validate` job refuses a release whose `radio-smoke-rx.json` or `radio-smoke-tx.json` assets are missing, malformed, attached to the wrong tag or commit, or carry anything but PASS (see "Radio-smoke receipts" above). What no workflow still enforces: a bench-certification night, a 24-hour soak, or an open-issue count as a condition of an RC dispatch or of `publish.yml`. Those remain operator judgement.
