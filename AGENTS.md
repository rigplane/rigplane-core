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

## GitHub Project workflow

Use the GitHub Project as the development control plane for non-trivial work.

- Project: `RigPlane Core Roadmap`
- URL: https://github.com/orgs/rigplane/projects/2
- Repository: `rigplane/rigplane-core`

Default rule for agents:

- Do not start non-trivial implementation from chat context alone.
- Work from a GitHub issue that has acceptance criteria.
- Add the issue to `RigPlane Core Roadmap` if it is missing.
- Keep Project fields current while working:
  - `Status`: `Todo`, `In Progress`, `Done`
  - `WorkType`: `epic`, `feature`, `spike`, `bug`, `debt`, `docs`, `release`
  - `Area`: `api`, `protocol`, `transport`, `rigctld`, `audio`,
    `radio-models`, `web-ui`, `cli`, `docs`, `ci`, `release`, `architecture`
  - `Priority`: `P0`, `P1`, `P2`, `P3`
  - `Phase`: `inbox`, `spec`, `alpha`, `beta`, `stable`, `post-release`,
    `backlog`
  - `Owner`: `human`, `codex`, `mixed`
  - `Risk`: `low`, `medium`, `high`
  - `Size`: `S`, `M`, `L`
- Treat issue bodies as the source of truth for requirements, acceptance
  criteria, compatibility decisions, and test expectations.
- Treat Project fields as routing/status metadata only.
- Before opening a PR, confirm the linked issue is in the Project and update
  `Status` to `In Progress` or `Done` as appropriate.

See `docs/internals/github-project-workflow.md` for the exact CLI and UI
workflow.

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

Every non-trivial PR requires independent agent review before merge. The
implementation agent may not be the review agent. The review must be visible in
the PR and include either `Agent Review: PASS` or `Agent Review: BLOCKED`.

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
