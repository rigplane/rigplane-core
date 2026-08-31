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
commands, architecture, testing, hygiene, protected-main, PR, check, exact-head
`Agent Review Gate`, and guarded merge rules remain binding.

GitHub is the execution plane: branch, commit, PR, diff, checks, independent
review, and merge evidence. Do not create a GitHub planning issue before
resolving the Linear owner. A GitHub issue is optional and allowed only for
atomic, concrete PR-bound scope that links its existing Linear issue; it must
not duplicate planning or dependency tracking.

Planning-only GitHub issues must be retired: record the Linear issue that owns
their scope and close them as superseded, without transferring planning status
back to GitHub. See `docs/internals/github-project-workflow.md` for the
execution-plane checklist and migration rule.

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
  in each worktree.

Use the global `repo-hygiene` skill for cross-repo inventory and cleanup.

## Protected main and review gate

`main` is protected. Changes should land through PRs.

RigPlane's standard automation gate is `.github/workflows/agent-review-gate.yml`.
It updates the required commit status `Agent Review Gate` on the current PR
head SHA and passes only after a normal PR comment contains `Agent Review:
PASS` for that head. Use this status instead of GitHub required approving
reviews; same-user approval restrictions break automated agent flow.

Every non-trivial PR requires independent agent review before merge. The
implementation agent may not be the review agent.

- `Agent Review: PASS`/`BLOCKED` is the verifier's verdict on the code
  alone: post it as soon as review is done, reporting CI state as found
  (queued, running, or complete with counts) without waiting for CI to
  finish or withholding a verdict because it hasn't. Confirming that
  required checks are actually green at the exact head is a separate step
  the coordinator takes immediately before merging — both conditions still
  gate the merge, just held by two roles instead of one.
- That split exists because CI is a shared, limited resource: `quick.yml`,
  `full.yml`, and `visual.yml` all set `runs-on: [self-hosted, linux, build]`,
  while everything else under `.github/workflows/` runs on `ubuntu-latest`.
  `gh api repos/rigplane/rigplane-core/actions/runners` currently lists a
  single registered runner carrying those labels, shared with
  `rigplane-pro` — a review session that waits on that queue can lose an
  hour or more for nothing.
- On that same shared runner, a test that fails once with no repeat on a
  clean rerun (e.g. `tests/test_mor1499_coalesce_keys.py`, one such case on
  2026-08-30) is more often runner load than a regression; the same test
  failing twice is evidence about the code, not the machine.
- `Agent Review: BLOCKED` must include concrete problems, file/line references
  where applicable, risk, required fixes, and checks to run.
- The implementation agent must address BLOCKED feedback, push updates, and
  rerun or wait for checks before merge.
- A failed `Agent Review Gate` without BLOCKED feedback usually means no fresh
  PASS comment exists for the current head; perform or refresh the review
  instead of skipping the PR.
- A PR based on a branch other than `main` shows a partly-populated check
  list, which is more misleading than an empty one:
  `agent-review-gate.yml`'s `pull_request` trigger carries no `branches:`
  filter and still runs, but the actual test/consistency gates —
  `quick.yml`, `visual.yml`, `consumer-contracts-gate.yml`,
  `doc-citation-gate.yml`, `rebrand-gate.yml`, and `state-types-gate.yml` —
  all scope `pull_request` to `branches: [main]` and do not run at all. It
  reads as "no problems found" when the tests never ran. Retargeting to
  `main` afterwards does not by itself start them: that fires an `edited`
  event, which none of these workflows' triggers list, so only a push (a
  new commit, or otherwise resynchronizing the PR) starts a run.
- Never merge with `--delete-branch` while another PR is based on that
  branch: GitHub auto-closes the child and then refuses to reopen (base
  gone) or retarget (already closed) it. Retarget every child PR onto
  `main` and push before merging the parent. If a child is closed this way
  anyway, rebase its branch onto `main`, open a fresh PR noting which
  closed PR it supersedes, and budget a re-review — the old review
  directives died with the old PR.
- Cancelled checks must be rerun with `gh run rerun <run-id>` or a new push,
  then watched to completion.
- `quick.yml`'s `concurrency.group` key is the workflow name plus
  `github.ref`, `cancel-in-progress: true`: every push to `main` cancels
  whatever `Tests (quick)` run is still queued or running for the merge
  just before it, and queue time is unbounded — a run can sit queued for
  minutes and then be cancelled without ever starting. A cancelled run
  reports success, so a chain of close merges can leave commits on `main`
  that no completed run ever covered.
- Before merging to `main`, check
  `gh run list --workflow=quick.yml --branch=main --limit 3` and wait for
  the current head's run to have started — ideally finished — instead of
  waiting a fixed number of minutes: queue time is unbounded, so a
  wall-clock wait does not work. Do not `gh run rerun` a run cancelled this
  way; rerunning re-enters the same concurrency group and cancels the
  current head's run instead — verify the current head's run, which covers
  every intervening change together. If other work merged while a PR sat
  open, compare `git rev-parse <squash-sha>^{tree}` to
  `git rev-parse <pr-head>^{tree}` before trusting the PR's own green run:
  equal trees mean it already covered that code, unequal means the
  combination was never tested.
- Interim protocol until a fix landing separately makes `cancel-in-progress`
  conditional on the ref: fuse the check and the merge into one shell
  invocation so the gap between them is seconds, not the minutes between a
  monitor firing and an operator acting (substitute the PR number):

  ```bash
  st=$(gh run list --workflow=quick.yml --branch=main --limit 1 --json status --jq '.[0].status')
  if [ "$st" != "completed" ]; then echo "ABORT: new main run in flight ($st)"; exit 1; fi
  gh pr merge <N> --squash --delete-branch
  ```

  This is **not atomic** — roughly one to two seconds remain between the
  status read and the merge landing, a real window under several concurrent
  mergers. What it buys: a stale check can no longer be ignored silently,
  because the merge simply does not run. Treat it as a stopgap: once the
  conditional `cancel-in-progress` change lands, the race it papers over
  stops existing and this guard stops being necessary.
- Draft PRs must not merge. Determine why the PR is draft, finish the missing
  work, run `gh pr ready`, then complete checks and review.

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
