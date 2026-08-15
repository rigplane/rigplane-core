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

For the Linear-authoritative RigPlane programs, Linear is the authoritative
control plane. It owns the backlog, scope, parent/child relations,
dependencies, priority, milestones, acceptance criteria, and status. Resolve
the Linear owner and its acceptance criteria before starting non-trivial work.

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
  approval.

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

- `Agent Review: PASS` means the PR may merge once required checks are green,
  the PASS comment is fresh for the current head, and the PR is not draft.
- `Agent Review: BLOCKED` must include concrete problems, file/line references
  where applicable, risk, required fixes, and checks to run.
- The implementation agent must address BLOCKED feedback, push updates, and
  rerun or wait for checks before merge.
- A failed `Agent Review Gate` without BLOCKED feedback usually means no fresh
  PASS comment exists for the current head; perform or refresh the review
  instead of skipping the PR.
- Cancelled checks must be rerun with `gh run rerun <run-id>` or a new push,
  then watched to completion.
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
