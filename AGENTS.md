# AGENTS.md — rigplane-core

## Mandatory operating-policy startup

At the start of every coordinator task, read
[`docs/internals/coordinator-policy.md`](docs/internals/coordinator-policy.md)
from the exact active worktree before planning or dispatching. Workers and
verifiers must also read it and apply its operating rules while retaining
their assigned roles and ownership boundaries; they do not become coordinators.
Read applicable instructions once per active version, repeating when they
change or a concrete conflict requires it. Do not skip mandatory reads.

The policy governs orchestration, routing, output, CI observation, and session
lifecycle where older `CLAUDE.md`, role, or command wording conflicts. Platform
requirements and current user instructions take precedence. Linear is the
planning authority for all Core development: preserve its scope, acceptance
criteria, dependencies, priority, milestones, and status. GitHub Projects are
not used for Core planning; project membership, fields, and GitHub Projects
OAuth scopes are not prerequisites. Safety, independent exact-head review,
required CI, guarded merge, and hardware acceptance remain binding.

## Repo identity

This repository is the public open-core `rigplane` implementation.

- Repository: `rigplane/rigplane-core`
- License: MIT, unless a file says otherwise

## Stabilization freeze (from 2026-10-13)

Owner decision of 2026-10-11: after Core 3.0.0b15 the repository is in a
stabilization freeze while the hardening program (Linear project
"Stabilization & Hardening 2026-Q4") runs.

Allowed change classes:

- `fix` — a defect, with a test that reproduces it;
- `regression-test`;
- `characterization-test`;
- `testability` — seams, fakes, simulators, recorders;
- `observability` — diagnostics, metrics, logging;
- `ci`;
- `docs`;
- `release`;
- `chore` — dependency pins, tooling.

Prohibited without an exception: new features, speculative refactoring, UI
redesign, new protocols, radios or DSP features, opportunistic cleanup that
widens the regression surface.

Every PR body carries a line `Change class: <class>` with exactly one class
from the list above. `.github/workflows/freeze-gate.yml` checks it and refuses
a `feat` title without the `freeze-exception` label. An exception is the
owner's decision, recorded as the first line of the PR body
(`Freeze exception: <ruling, date>`) together with the label
`freeze-exception`.

`slow`-marked tests must not run on every build; they must run when a release
candidate or a release is cut (owner ruling 2026-10-10).

Every PR names its Linear owner in the body (`Linear: MOR-NNNN`).

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

Linear is the authoritative planning control plane for all Core development:
it owns the backlog, scope, parent/child relations, dependencies, priority,
milestones, acceptance criteria, and status. Resolve the Linear owner and its
acceptance criteria before starting non-trivial work. GitHub Projects are not
used for Core planning; project membership, fields, and GitHub Projects OAuth
scopes are not prerequisites.

This changes only control-plane ownership. All other `CLAUDE.md` commands,
architecture, hygiene, protected-main, exact-head `Agent Review Gate`, and
guarded merge rules remain binding. The delivery batching and CI cadence below
supersede older draft-first CI and per-merge `main`-wait language in `CLAUDE.md`;
safety, required checks, and independent review remain binding.

GitHub is the execution plane: branch, commit, PR, diff, checks, independent
review, and merge evidence. Do not create a GitHub planning issue before
resolving the Linear owner. Ordinary GitHub issues remain available for
customer intake and discussion without a prior Linear ticket. A GitHub
execution issue is optional and allowed only for atomic, concrete PR-bound
scope that links its existing Linear issue; it must not duplicate planning or
dependency tracking.

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
`docs/**` except data files, Markdown/RST anywhere in the tree, `.claude/**`, and
the doc-citation baseline files — no test job runs. `docs-only-quick.yml`
(`pull_request_target`, `ubuntu-latest`) publishes the required `quick` status
as success without a checkout; `quick.yml`'s `paths-ignore` skips such a head,
and for a docs-only head that still reaches it, the `classify` job
(`ubuntu-latest`) sets `docs=true` and the `quick` job is skipped by its `if:`
(`needs.classify.outputs.docs != 'true'`). No self-hosted runner is allocated.
Do not run citation, link, Markdown, product, visual,
or full automation; the exact-head independent review is the substantive gate.
Documentation mixed with code follows the normal checks selected by the code
paths. Data files under `docs/` (`*.json`, `*.toml`, `*.yaml`, `*.yml`) and the
Markdown files that tests read are not documentation here and get the normal
checks (`.github/scripts/classify-quick-paths.py: DOCS_DATA_SUFFIXES`,
`CORE_DOCS_TEST_INPUT_EXACT`).

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

### Review verdict: local Codex CLI review

Owner decision 2026-10-21 (supersedes the Codex-primary gate of MOR-3616):
the merge verdict comes from a LOCAL review of the exact head, never from
waiting for GitHub. The builder runs `tmp/pre-review.sh` before pushing (Codex
CLI on the owner's ChatGPT subscription, read-only, exhaustive). When it prints
`PRE-REVIEW: CLEAN` for the pushed head SHA, the lane script `pro-gate.sh` (or
the coordinator) posts the exact-head `Agent Review: PASS <head sha>` directive
with the model, the number of rounds and "local Codex CLI pre-review". The
reviewer's model family differs from the builder's: Codex reviews what Kimi,
Claude, GLM or Spark built; if Codex built the change, Spark reviews it. A new
push needs a new local verdict for the new head.

`Agent Review Gate` is green when a valid, trusted, non-minimized exact-head
`PASS` directive exists (as described above) and no active `BLOCKED` directive
for the head exists. Codex's GitHub review (`chatgpt-codex-connector[bot]`) is
advisory and asynchronous: it still reviews when a PR is opened or a draft is
marked ready and answers `@codex review`, its review and open P0-P2 threads show
in the status text, and nobody waits for it. If it later finds a P0 or P1 (or a
behaviour-changing P2) on a PR, the owning session opens a fix PR; that is part of
its done. The severity policy in "Code Review Rules" says what must be fixed and
what is deferred (`deferred to MOR-xxxx`, resolve the thread); the gate does not
read those threads.

A trusted, active (or malformed) `BLOCKED` directive for the head still wins and
the status is red. If anything fails to evaluate (an API or GraphQL error), the
status is red as well; there is no silent pass. While no PASS exists the status
reads "Waiting for the local review PASS". After a comment edit or other change
that fires no workflow event, re-run the gate:
`gh workflow run agent-review-gate.yml -f pr_number=N`. The `built-by-gpt` label
no longer changes the verdict; the lane script picks the reviewer family.

Every non-trivial PR requires independent agent review before merge. The
implementation agent may not be the review agent.

- The PASS/BLOCKED token in an exact-head directive is the verifier's verdict
  on the code alone: post it as soon as review is done, reporting CI state as
  found (queued, running, or complete with counts) without waiting for CI to
  finish or withholding a verdict because it hasn't. Confirming that required
  checks are actually green at the exact head is a separate step the
  coordinator takes immediately before merging — both conditions still gate
  the merge, just held by two roles instead of one.
- That split exists because CI is a shared, limited resource: the
  self-hosted workflows queue behind a small shared pool of this
  repository's own `[self-hosted, linux, build]` runners — see the runner
  labels in `.github/workflows/` and
  [`docs/development/release-gates.md`](docs/development/release-gates.md);
  the labels, not a count, are the contract, and the pool changes over
  time. The macOS bench/platform workflows use their own labels.
  `rigplane-pro`'s runners are separate registrations and cannot pick up
  this repository's jobs. A review session that waits on that queue can
  still lose an hour or more for nothing.
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
  means no fresh, valid exact-head PASS directive exists for the current head:
  run `pro-gate.sh` (local review of the head) instead of skipping the PR.
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
- `quick.yml` has no `push` trigger (MOR-3878): the merge queue's `merge_group`
  run tests the tree that lands on `main` (a squash keeps the tree), so no
  `quick` run follows the merge. The queued-run cancellation on `main`
  measured in MOR-2048 cannot happen to it any more. The scheduled
  `full.yml` run (every 3 hours on weekdays, skipped when that commit
  already passed) and `docs.yml` are what runs on `main` itself.
- The merge operator may train-merge file-disjoint PRs whose immutable heads
  each have an exact-head PASS and every required PR check green. Guard every
  merge with `--match-head-commit`. The merge queue's `merge_group` `quick`
  run on the batch head is the aggregate run; there is no `main` quick run to
  wait for or rerun after the merge. An actual conflict, proven interaction
  between train members, or failure on that queue run stops the train for
  diagnosis.
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

## Code Review Rules

Rules for Codex code review on GitHub. Builders: the same classes are the
pre-push checklist in
[`docs/development/review-checklist.md`](docs/development/review-checklist.md).

### One pass, everything at once

- Report every finding that must be fixed (P0, P1 and behaviour-changing P2,
  see the severity policy below) in a single review pass. Do not stop at the
  first few; every finding left for a later pass costs the team another 15-30
  minute round. There is no cap on the number of findings.
- Group findings by file. Give `file:line`, the concrete input or sequence that
  breaks, and the concrete fix.
- One comment per root cause. When the same defect appears in several places,
  report it once and list the places.
- Review the lines added by fix commits with the same rigor as the first
  commit, and search for siblings of a finding (the same defect class in a
  second code path) in the same pass.
- Do not repeat a finding that was fixed or that a thread already answered
  with evidence.

### Severity policy

- Raise P0 and P1. Raise a P2 only when it changes behaviour: a wrong result or
  verdict, data loss, state left dirty, a hang, a security or radio-transmit
  safety defect, or a false claim about safety or support in user-facing
  documentation.
- Do not raise documentation wording, comments, PR-body phrasing, naming,
  style, formatting, test-only polish, or robustness nits in internal test and
  bench tooling that cannot change a verdict or leave state dirty. If you must
  mention one, mark it P3.
- For internal test and bench tooling, do not harden against attackers who do
  not exist in the deployment.

### Defect classes to check on every change

- Untrusted or dynamic input: wrong types, NaN, path escapes, IPv6 brackets,
  stale or truncated device replies, validation applied to one path but not its
  siblings.
- Failure paths: an error that becomes a traceback instead of the documented
  outcome; one failed step that skips the remaining restore or cleanup steps.
- State left dirty on interrupt: SIGTERM or cancellation between two
  statements, handlers installed late, `BaseException` bypassing `except`.
- Concurrency and cancellation: two instances at once, stale lock or reused
  PID, cancel before start, fire-and-forget followed by a dependent step.
- Time: fixed sleeps as synchronization, measurement windows that start at the
  wrong moment, timeouts shorter than the callee's own bounds.
- Verdicts that fail open: `|| true`, empty or unmeasurable results counted as
  PASS, `any()` where `all()` is meant, success without observed evidence.
- Contract fidelity: code that disagrees with the pinned contract, schema,
  profile or fixture; sibling files (docs, allow-lists, label lists, tests that
  pin literals) that must change with the change.
- Restart, upgrade and rerun behavior of services and deploy code.

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
- Pre-push review: walk `docs/development/review-checklist.md` against your
  diff and run `tmp/pre-review.sh` (a local exhaustive Codex pass; the lane
  scripts put it in the worktree, see the checklist if it is missing) before
  the first push and before every re-push, until it prints
  `PRE-REVIEW: CLEAN`. Target at most 500 changed lines per PR, with the
  exceptions and limits stated in the checklist; split larger work.
- Keep hardware-dependent work mockable where possible; otherwise mark the
  issue as requiring human/hardware validation.
