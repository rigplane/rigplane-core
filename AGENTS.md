# AGENTS.md — rigplane-core

## Repo identity

This repository is the public open-core `rigplane` implementation.

- Repository: `rigplane/rigplane-core`
- License: MIT, unless a file says otherwise
- Project board: https://github.com/orgs/rigplane/projects/2

## Public/open-core boundary

Everything in this repository should be safe to publish as open-core work:

- protocol correctness;
- generic radio control features;
- transports and backends;
- public SDK/API improvements;
- generic local web UI improvements;
- tests and docs useful to the community.

Do not add proprietary, customer-specific, hosted-account, premium workflow, or
private service integration code here. Those belong in `rigplane-pro`.

If a task mixes public and proprietary scope, split it:

- generic API/protocol/backend work stays here;
- product workflow, packaging, account/device, and support automation go to
  `rigplane-pro`.

### Hamlib provider boundary

The accepted Core Hamlib integration boundary is an external `rigctld`
process, consumed through the backend-neutral `Radio` and capability protocols.
Direct `libhamlib` binding is deferred unless a future spike proves the need
and includes licensing and crash-containment acceptance criteria.

Core owns the generic provider contract, external rigctld client behavior,
discovery candidate schema, serial inventory/model metadata, safe read-only
probing/ranking, fake rigctld tests, and public CLI/docs. Pro must consume
those Core outputs instead of reimplementing Hamlib probing or ranking. Managed
setup UX, diagnostics/support evidence, packaging/legal decisions, and
redaction-specific support workflows belong in `rigplane-pro`; private
validation matrices and decision records stay in Strategy. See
`docs/internals/hamlib-provider-rollout.md`.

## Linear planning and GitHub execution

For the `RigPlane Core UI Composition Architecture v3` project, the live,
project-specific Linear control-plane contract takes precedence over the legacy
GitHub-Project planning and control-plane language in `CLAUDE.md`. Linear is the
authoritative control plane: it owns the backlog, scope, parent/child relations,
dependencies, priority, milestones, acceptance criteria, and status. Resolve
the Linear owner and its acceptance criteria before starting non-trivial work.

This precedence is limited to control-plane ownership. All other `CLAUDE.md`
commands, architecture, hygiene, protected-main, exact-head `Agent Review
Gate`, and guarded merge rules remain binding. The delivery batching and CI
cadence below supersede older draft-first CI and per-merge `main`-wait language
in `CLAUDE.md`; safety, required checks, and independent review remain binding.

GitHub is the execution plane: branch, commit, PR, diff, checks, independent
review, and merge evidence. Do not create a GitHub planning issue before
resolving the Linear owner. A GitHub issue is optional and allowed only for
atomic, concrete PR-bound scope that links its existing Linear issue; it must
not duplicate planning or dependency tracking.

Planning-only GitHub issues must be retired: record the Linear issue that owns
their scope and close them as superseded, without transferring planning status
back to GitHub. See `docs/internals/github-project-workflow.md` for the
execution-plane checklist and migration rule.

## Delivery batching and CI cadence

The coordinator dispatches a lane once with its Linear owner, current
acceptance criteria and dependencies, exact file lease, and verification
matrix. Within that contract the builder acts autonomously. Escalate only an
actual contract, scope, path-ownership, dependency, or safety collision; routine
implementation, focused checks, PR text, and handoff do not need another lease
round trip.

Batch related changes in one branch and PR when they share one semantic
contract, file lease, and verification matrix. Link every covered child Linear
ticket and reconcile each child explicitly. Do not use batching to combine
unrelated contracts, cross an unleased path, hide a compatibility break, or
weaken TX/PTT and hardware safety boundaries.

A delivery batch may still use several workers. After freezing a small shared
interface, decompose when at least two file-disjoint work packages each contain
material work (normally 20–30 minutes or more) and their integration cost is
lower than the wall time saved. Workers own disjoint files and return logical
commits or artifacts to one named integrator. They do not open separate PRs,
start natural `quick`/`full`/`visual` runs, or request separate final reviews.
The integrator owns the batch branch, combines the work, freezes one candidate,
opens one Ready PR, and obtains one CI/review cycle. Do not decompose a small
tightly coupled invariant, shared-file edits, or sequential generated output.
Good candidates include provider consumers, radios within one profile family,
UI state versus rendering, and Icom versus Yaesu actuators; regenerate visual
baselines only after the integrated rendering has frozen.

Development RED and correction loops use focused changed-scope checks on the
Mac mini plus changed-scope lint/type checks. Do not run the full `quick` suite
for an intentional RED or every draft push. In the normal path, push the final
candidate, open or mark the PR Ready once, and let that immutable head receive
one natural `quick` run. `visual` runs only for its affected paths; `full` is
reserved for releases or an explicitly recorded cross-cutting risk. A fresh
independent verifier reviews the exact head and consumes the existing CI
evidence without rerunning suites. A correction changes the head and therefore
gets the one natural required run and fresh exact-head review for that new
candidate.

Movement on `main` alone does not invalidate an unchanged PR head. Refresh the
branch and its checks/review only for an actual merge conflict, a proven
base-sensitive dependency, or a concrete code interaction.

Use `.github/workflows/focused.yml` for development RED/GREEN evidence. It is a
manual, non-required workflow that checks an exact 40-character commit SHA and
accepts only validated JSON arrays of pytest nodeids/files, Ruff paths, and
Vitest files. Dispatch it from the trusted `main` workflow definition, for
example:

```bash
candidate_sha=$(git rev-parse HEAD)
gh workflow run focused.yml --ref main \
  -f revision="$candidate_sha" \
  -f pytest_targets='["tests/test_radio.py::test_frequency"]' \
  -f ruff_targets='["src/rigplane/radio.py","tests/test_radio.py"]' \
  -f vitest_targets='[]'
```

The focused workflow does not replace required PR CI. A final Ready product or
CI-control head still receives its natural `quick` and, when selected by paths,
`visual`.

`quick.yml` keeps three independent path classes. `core` covers backend/source,
tests, profiles, contracts, and Python project metadata; `frontend` covers
`frontend/**` plus `src/rigplane/web/**`; `ci` covers only workflow controls
under `.github/`. A pure frontend change does not run backend pytest, Ruff,
import-linter, or validation goldens. A `src/rigplane/web/**` change selects
both core and frontend because it crosses that boundary. A CI-only change runs
only the workflow parser, injection/path contract tests, and Python control
compilation. Pixel-diff `visual` runs only for actual `frontend/**` changes,
never because `visual.yml` itself changed.

When every changed path is documentation or documentation metadata — including
`docs/**`, Markdown/RST anywhere in the tree, `.claude/**`, and the doc-citation
baseline files — only the GitHub-hosted classifier runs. The required `quick`
job reports a server-side skipped/neutral context and never allocates the Mac
mini. Do not run citation, link, Markdown, product, visual, or full automation;
the exact-head independent review is the substantive gate. Documentation mixed
with code follows the normal checks selected by the code paths.

## Multi-agent Git hygiene

This repo is developed from multiple machines and by multiple agents. Before
editing:

- run `git fetch --all --tags --prune`;
- inspect `git status --short --branch`;
- do not work directly on `main`;
- use `codex/<issue-or-task>` branches for agent work;
- use `git pull --ff-only --tags` only on clean branches with a normal upstream;
- do not reset, clean, delete, or rebase uncertain work without explicit user
  approval;
- do not share or symlink `node_modules` between worktrees — one worktree's
  install emptied another's shared directory this way; run a real `npm ci`
  in each worktree;
- a `gh pr merge --delete-branch` run from inside a worktree can exit
  non-zero even after the merge itself succeeded: the remote merge and
  branch deletion happen first, over the API, and only then does the CLI
  try to update the local branch — which fails if that branch is checked
  out in this or any other worktree. Before retrying a reported failure,
  check the PR's actual state (`gh pr view <N> --json
  state,mergedAt,mergeCommit`) rather than assuming the merge did not
  happen.

Use the global `repo-hygiene` skill for cross-repo inventory and cleanup.

## Protected main and review gate

`main` is protected. Changes should land through PRs.

RigPlane's standard automation gate is `.github/workflows/agent-review-gate.yml`.
It updates the required commit status `Agent Review Gate` on the current PR
head SHA. The gate accepts a verdict only when the first non-blank line of a
trusted, non-minimized PR comment is exactly one of these forms, with the
placeholder replaced by the PR's current 40-character lowercase head SHA:

```text
Agent Review: PASS <40-character-lowercase-head-sha>
Agent Review: BLOCKED <40-character-lowercase-head-sha>
```

A BLOCKED directive must put concrete findings, file/line references where
applicable, risk, required fixes, and checks to run on subsequent lines. A
missing, short, uppercase, or stale SHA is not a directive. Use this status
instead of GitHub required approving reviews; same-user approval restrictions
break automated agent flow.

Every non-trivial PR requires independent agent review before merge. The
implementation agent may not be the review agent.

- The PASS/BLOCKED token in an exact-head directive is the verifier's verdict
  on the code alone: post it as soon as review is done, reporting CI state as
  found (queued, running, or complete with counts) without waiting for CI to
  finish or withholding a verdict because it hasn't. Confirming that required
  checks are actually green at the exact head is a separate step the
  coordinator takes immediately before merging — both conditions still gate
  the merge, just held by two roles instead of one.
- That split exists because CI is a shared, limited resource: `quick.yml`,
  `full.yml`, and `visual.yml` all set `runs-on: [self-hosted, linux, build]`,
  while everything else under `.github/workflows/` runs on `ubuntu-latest`.
  `gh api repos/rigplane/rigplane-core/actions/runners` currently lists
  exactly one registered runner carrying those labels, scoped to this
  repository alone — `rigplane-pro`'s runners are separate registrations
  and cannot pick up this repository's jobs. The contention is narrower
  than a shared queue: this repository's three self-hosted workflows
  serialize behind a single runner of its own, and a review session that
  waits on that queue can still lose an hour or more for nothing.
- Do not rerun an entire suite merely to classify a failure. Diagnose it from
  the existing artifact and a focused reproduction on the Mac mini. A repaired
  final head receives its natural required workflow run; a full-suite rerun is
  reserved for concrete evidence of runner failure.
- The implementation agent must address BLOCKED feedback, push updates, and
  rerun or wait for checks before merge.
- A PASS may still carry corrections, marked REQUIRED BEFORE MERGE or MANDATORY
  SQUASH-BODY CORRECTION: findings real enough to state but not fixable by a
  commit — a false claim in the PR body or a comment, or one in a pushed commit
  message that the squash body will carry onto `main`. No workflow checks them;
  nothing reads a PR body, and the gate matches its exact, SHA-bound directive
  pattern against only the first non-blank line of a comment.
  Whoever merges must apply them before merge anyway — editing the body or
  comment, or writing the corrected squash body at merge — and say in the PR
  that it did.
- A failed `Agent Review Gate` without valid exact-head BLOCKED feedback usually
  means no fresh, valid exact-head PASS directive exists; perform or refresh
  the review instead of skipping the PR.
- A PR based on a branch other than `main` shows a partly-populated check
  list, which is more misleading than an empty one:
  `agent-review-gate.yml`'s `pull_request` trigger carries no `branches:`
  filter and still runs, but the actual test/consistency gates —
  `quick.yml`, `visual.yml`, `consumer-contracts-gate.yml`,
  `doc-citation-gate.yml`, `rebrand-gate.yml`, and `state-types-gate.yml` —
  all scope `pull_request` to `branches: [main]` and do not run at all. It
  reads as "no problems found" when the tests never ran. Retargeting to
  `main` afterwards does not by itself start them: that fires an `edited`
  event, which none of these workflows' triggers list. A push (a new
  commit) starts them, and so does closing and reopening the PR — none of
  these six workflows lists an explicit `types:` filter on its
  `pull_request` trigger, so all of them fall back to GitHub's default
  `[opened, synchronize, reopened]`, and `reopened` fires on reopen.
- Never merge with `--delete-branch` while another PR is based on that
  branch: GitHub auto-closes the child and then refuses to reopen (base
  gone) or retarget (already closed) it. Retarget every child PR onto
  `main` and push before merging the parent. If a child is closed this way
  anyway, rebase its branch onto `main`, open a fresh PR noting which
  closed PR it supersedes, and budget a re-review — the old review
  directives died with the old PR.
- Cancelled checks on a PR branch must be rerun with `gh run rerun
  <run-id>` or a new push, then watched to completion. On `main` itself,
  do not rerun this way — see below.
- A commit can look unverified for two different reasons that report
  opposite signatures — check which one applies before trusting a green
  commit or ignoring a red one. A gate whose run is skipped because the
  change touched none of its watched paths reports `success` (or is simply
  absent from the check list): green, but unverified, and expected. A
  cancelled run is different: a check run with conclusion `CANCELLED`
  makes the commit's overall status-check state `FAILURE` — **red**, not
  green — and nothing re-queues it automatically. Verified directly
  against the Actions API: four `main` commits from 2026-08-30 whose
  `Tests (quick)` run was cancelled each carry an overall `FAILURE` state.
  A red commit on `main` from a cancelled run needs a person to look at
  it, because nothing else will.
- `quick.yml` keys its `concurrency.group` on the workflow name plus
  `github.ref`, so every push to `main` shares one group with every other
  push to `main`. Within one group, GitHub keeps at most one *queued*
  (not-yet-started) run: a push that arrives while the previous push's run
  is still waiting for a runner cancels that waiting run outright — it
  never reaches a runner (empty `runner_name`, no steps recorded). This
  happens regardless of the group's `cancel-in-progress` setting, which
  only governs whether an *already-started* run is cancelled by a later
  push, not whether a *queued* one is. All four cancelled `main` runs
  sampled on 2026-08-30 died this way — queued, not mid-run. Measured and
  tracked as MOR-2048; see that ticket rather than re-deriving the counts.
- The merge operator may train-merge file-disjoint PRs whose immutable heads
  each have an exact-head PASS and every required PR check green. Guard every
  merge with `--match-head-commit`. Do not wait for or rerun intermediate
  `main` quick runs: queued intermediate runs may be displaced by the next
  train merge and are not acceptance evidence. After the final merge, require
  one aggregate `main` quick on the final batch head. An actual conflict,
  proven interaction between train members, or failure on that final aggregate
  head stops the train for diagnosis. This keeps exact-head protection while
  avoiding the queued-run churn tracked in MOR-2048.
- After a merge, if a run on `main` looks cancelled or otherwise
  unverified and you want to know whether an earlier PR run already
  covered the code that landed, compare
  `git rev-parse <squash-sha>^{tree}` to `git rev-parse <pr-head>^{tree}`:
  equal trees mean it already covered that code, unequal means the
  combination was never tested. Treat this as a strong heuristic, not an
  identity — a PR's own check runs against `refs/pull/N/merge` (the PR
  head merged onto the base at test time), not against the PR head commit
  by itself.
- Read the gate's verdict from the commit status — `gh pr checks <n>`, the
  `Agent Review Gate` row, or `gh api repos/<owner>/<repo>/commits/<sha>/status`
  — never from a run list. A run list shows the publisher job
  (`Update Agent Review Gate status`) under the workflow's name, and it is
  green when it has successfully published a refusal; and a run triggered
  by `issue_comment` carries `headBranch=main`, so `gh run list --branch
  <feature>` cannot show it and its silence proves nothing about whether the
  gate re-ran.
- Draft PRs must not merge and do not run the expensive self-hosted `quick` or
  `visual` jobs. Finish focused development checks first, then open or mark the
  final candidate Ready; the `ready_for_review` event starts its required CI.

## Release branches

Use release branches only when a public/core release needs stabilization while
`main` continues moving. Tags remain the source of truth for published
artifacts. Hotfixes made on a release branch must be merged or cherry-picked
back to `main`.

## Engineering rules

- Follow `CLAUDE.md` for commands, architecture, testing, and workflow gates.
- Keep public API compatibility explicit. If a change breaks API, CLI, config,
  rigctld wire behavior, or docs, call it out in the issue and PR.
- Prefer tests before implementation for bugs and behavior changes.
- Do not add new layers, abstractions, or broad refactors unless the issue
  explicitly requires it.
- Keep hardware-dependent work mockable where possible; otherwise mark the
  issue as requiring human/hardware validation.
