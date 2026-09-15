# CLAUDE.md — Engineering and workflow

**rigplane** — Python 3.11+ asyncio library + Web UI for Icom transceivers over LAN/USB. Version: see `pyproject.toml`.
Live bench: **IC-7300, FTX-1**. *(IC-7610 retired 2026-08-04; X6200 destroyed by lightning 2026-08-11.)* Context: `docs/PROJECT.md`.

---

Operating policy: read `docs/internals/coordinator-policy.md` under the
mandatory startup rule in `AGENTS.md`. Its scoped precedence reconciles legacy
orchestration defaults; all engineering and safety rules below still apply.

## Commands (always `uv run`)

```bash
uv run pytest tests/test_radio.py -q --tb=short  # choose the affected test paths
uv run mypy --strict src/rigplane/web            # when this scope requires it
uv run ruff check <changed-python-paths>         # scope to the candidate
uv run ruff format --check <changed-python-paths>
```

For Python project commands, use `uv run`, never bare `python` or `pytest`.
Before Python development checks in a new worktree, use `uv sync --all-extras`.
Documentation-only work needs no dependency installation or check automation.

Choose commands from the task's verification matrix and the path contract in
`AGENTS.md`; command examples are not instructions to run every check. Full
suite, matrix, and frontend selection belong to the current workflow files.
Do not treat a skipped check as verification of an unaffected scope.

---

## CI workflows

`AGENTS.md` Delivery batching and CI cadence is the authoritative run-selection
contract. Use focused changed-scope development checks, then one natural
required run for the final Ready candidate. Run `visual` for affected paths;
reserve `full` for releases or an explicitly recorded cross-cutting risk.
Documentation-only work uses readback/diff proof and independent review, without
citation, link, Markdown, product, visual, or full automation. The server-side
classifier and required skipped/neutral context are described in `AGENTS.md`.

Review and CI proceed in parallel against the frozen head. One observer owns
each run and reports meaningful changes; builder, verifier, and coordinator
consume that evidence rather than duplicate checks. The coordinator verifies
all required statuses at the exact head immediately before a guarded merge.
A changed head needs its own required run and fresh review verdict.

---

## Architecture

**Layering (enforce):**
- Consumers → `radio_protocol.Radio` → `backends.factory` → CoreRadio → transport
- Web/rigctld must never call transport directly
- Backends must never import from `web/` or `rigctld/`
- New commands → `commands/` + `command_map.py` + `commander.py`
- New public API → `radio_protocol.py` first, then backend
- No new abstractions, layers, or refactors unless the issue explicitly requires it

**Hard protocol rules:**
- cmd29 does NOT work for freq/mode (`0x05`/`0x06`) on IC-7610
- Keep-alive: ~500ms control, ~100ms audio — never weaken
- MagicMock hides signature bugs — verify against real dataclasses

**Frontend layering (enforce):**
- `lib/runtime/` → singleton FrontendRuntime, wraps stores + transport + audio
- `lib/runtime/adapters/` → pure functions mapping runtime state → component props
- `components-v2/wiring/` → where the pure surfaces meet live state: derives view models via `lib/runtime/adapters/` and turns surface callbacks into commands
- `skins/` → skin registry + entry points (see `SkinId` in `skins/registry.ts` for the current list; `amber-lcd` is a legacy persisted-preference alias, not a live entry point)
- eslint `no-restricted-imports` enforces: panels/layouts cannot import `$lib/transport/*` or `$lib/audio/audio-manager`
- ADR: `docs/plans/2026-04-12-target-frontend-architecture.md`

**Open-core constraints:** see `docs/architecture/open-core-policy.md` — no telemetry, headless sacred, no hollowing out, Pro boundary at Radio protocol + `local-extensions/`.

---

## Layer boundaries

`src/rigplane/` is organised into 11 layered packages with `import-linter`-enforced boundaries (config at repo root `.importlinter`; full matrix in `docs/plans/2026-04-29-modularization-plan.md` §1, §3; per-layer charters in `src/rigplane/<layer>/LAYER.md`).

Layers (top → bottom; higher = more dependent):

| Layer | Purpose |
|---|---|
| `cli/` | Command-line entrypoints |
| `web/`, `rigctld/` | UI servers (siblings — independent) |
| `backends/` | Factory + per-radio assembly |
| `runtime/` | IcomRadio + state + mixins + pollers |
| `profiles/`, `audio/` | Rig profiles · audio subsystem (siblings) |
| `commands/`, `scope/`, `dsp/` | CI-V builders · scope · DSP (siblings) |
| `core/` | Foundational: types, transport, civ, contracts |

When making changes:
- Adding a new radio backend → conform to the relevant Capability Protocols in `core.radio_protocol` (`AudioCapable`, `StatePollable`, `RigctldRoutable`, `UsbAudioCapable`, …); zero upper-layer changes if the protocols are honoured.
- New cross-layer imports must respect the matrix; if a sensible-looking import is rejected by the linter, the file is in the wrong layer.
- Run `uv run lint-imports` before committing significant structural changes (CI gates every PR anyway).
- Backwards compatibility: old top-level paths (`rigplane.radio`, `rigplane.commander`, `rigplane.rig_loader`, …) keep working via `sys.modules`-aliased re-export shims; new code SHOULD use canonical paths (`rigplane.runtime.radio`, etc.).

---

## Testing

- TDD — test first, implement second
- Use focused checks for development and corrections; broaden only for a new failure, changed scope, or concrete risk
- Required suites belong to CI under the path selection in `AGENTS.md`; reuse exact-head results rather than rerunning them in each role or phase
- Audio tests: `FakeAudioBackend` only — no one-off mocks
- Prose is a claim, and claims get checked. For every comment, docstring and document sentence a change adds or touches, ask: could this be false without any test failing? If so, delete it — or, only where the sentence must exist, narrow it until it is true and tie it to something that fails when it stops being true (a named constant, a named test, a parsed structure) — a guarantee stated wider than the code is worse than none, because the next reader stops checking. A claim about what a future change will do belongs in the ticket (MOR-1958). `builder.md` points here.
  - **Delete before you narrow; never weaken.** Deletion is the default for a claim that is false, stale or ambiguous, because correcting prose is expensive: a missing sentence sends the next reader to the code, where a wrong one stops them looking. Narrow instead only where the sentence must exist — a document written for a reader who does not have the code in front of them, as `docs/architecture/building-a-skin.md` is — and then only with the narrower version established, with a command actually run or a file actually read. Never weaken a claim you cannot establish: a deleted sentence cannot be wrong, and a softened one still can be. Owner ruling, 2026-08-31, reversing the priority this bullet set when it was added.
  - **Every claim carries how it was established.** When a change states a corrected value, a measurement, or a citation — in a comment, a document, a PR body or a report — name the command actually run, or the file actually read, that established it. A corrected value that is also wrong is worse than the original it replaced.
  - **Review a correction's own new prose separately.** When a change fixes a false statement, read the prose it added as its own review pass, not only as part of the whole diff.

---

## Language & Git

User-facing → **Russian**. Code/commits/docs/PR → **English**.
Commits: `feat(#N):` / `fix(#N):` / `refactor:` / `test:` / `docs:` / `chore:`
One change per commit.

Documentation under `docs/` cites code as file plus **symbol name**
(`radio.py: IcomRadio.set_frequency`), never a new line-number citation. Relative
Markdown links must point to the intended tracked document. For existing
citation/link baselines and their parser, read
`.github/scripts/check-doc-citations.sh` when that implementation is relevant.
The documentation-only exception in `AGENTS.md` governs automation: use manual
readback and independent review rather than running citation or link checks.

### Multi-machine Git hygiene

Development runs across a local laptop and a dev Mac mini, often with several
agents. Before editing:

```bash
git fetch --all --tags --prune
git status --short --branch
```

Rules:

- never work directly on `main`;
- use `codex/<issue-or-task>` for agent work;
- use `git pull --ff-only --tags` only on a clean branch with a normal upstream;
- do not reset, clean, delete, or rebase uncertain work without explicit user
  approval;
- report or snapshot dirty trees before sync.

`main` is protected. Non-trivial PRs require an independent agent review
before merge; the implementation agent never reviews its own work.

The gate is `.github/workflows/agent-review-gate.yml`, parsed by
`.github/scripts/agent-review-gate.js`. It considers only non-minimized
comments whose author association is OWNER, MEMBER or COLLABORATOR, and
matches the **first non-blank line** of each against exactly this pattern:

```js
const DIRECTIVE_PATTERN = /^Agent Review: (PASS|BLOCKED) ([0-9a-f]{40})$/u;
```

The captured SHA must equal the PR's current head. Anything else — a short,
missing or stale SHA, or any non-blank line above the directive — parses as no
directive and leaves the status red; so does a BLOCKED directive with no
justification text below it, which the gate reports as malformed.

A BLOCKED comment naming one instance is a review of its class: enumerate
every place the same shape occurs, fix what the change's guardrails cover,
and report the rest instead of expanding scope to fix it.

Review policy — PASS/BLOCKED semantics, freshness, draft PRs, rerunning
cancelled checks — and release branches (named `release/<major.minor>`) are in
`AGENTS.md`; the merge procedure is in `docs/internals/github-project-workflow.md`.

---

## Completion criteria

Work is complete when its acceptance criteria and applicable gates pass:

1. Required checks for the exact candidate satisfy the `AGENTS.md` path contract;
   a skipped/non-applicable check is reported as such, never as executed proof.
2. Independent exact-head review is accepted, with required prose corrections
   applied before merge and all required statuses checked immediately before it.
3. Diff/readback shows only intended changes; installation, visual, and hardware
   acceptance remain separate when required by the task.

Do not rerun repository-wide Ruff or suites merely to complete this list.
Documentation-only work follows its explicit `AGENTS.md` exception.

---

## Agent working rules

Resolve the authoritative planning owner before non-trivial work. For the v3
project, `AGENTS.md` delegates scope, acceptance, dependencies, and status to
Linear; GitHub holds execution evidence. Do not duplicate that planning in a
GitHub issue. Use `docs/internals/github-project-workflow.md` for delivery.

Coordinators resume from the existing private Linear checkpoint when relevant;
workers use their assigned contract. Check known artifact paths, not recursive
handoff inventories. Checkpoint versions, operational notes, detailed reports,
and handoff artifacts stay outside every repository worktree, including
ignored directories. See `docs/internals/coordinator-policy.md` for authority,
output limits, model selection, and ACK → RELEASE → ACCEPT ownership transfer.

Roles in `.claude/agents/` describe responsibilities and tool boundaries, not
mandatory staffing or universal model tiers. Select the smallest capable
available model and explicit effort through a supported per-dispatch setting;
never silently inherit a costly root model for a worker. A narrow command or
lookup belongs to an ordinary tool call. Delegate material implementation and
independent reasoning when it saves work; independent review must always use a
reviewer who did not author the change. Reading coordinator policy never
changes a builder or verifier into a new coordinator.

Slash commands in `.claude/commands/` and relevant skills give scoped methods.
Their legacy ordering, retries, routing, cleanup, and observation wording is
subject to the operating policy, not an exception to it. Method-specific
correctness, scope, and safety requirements remain binding.

### Delivery sequence

Explore enough to ground the plan, resolve acceptance and file ownership,
implement with focused checks, freeze one candidate, then obtain independent
review and required CI in parallel. Integrate and verify before guarded merge.
Usually one builder and one independent verifier are enough. Multiple builders
need material disjoint scopes under the batch contract in `AGENTS.md`.

For behavioral regression claims, capture relevant existing baseline evidence
before implementation in private working notes. Confirm that its compared
paths have not changed before reusing it; a skipped path filter gives no test
counts. Compare behavior and failures, not counts alone. If evidence is absent,
record the gap and choose a focused check; do not launch a full suite to fill a
ceremonial phase. Documentation-only edits need no test baseline.

Finish focused development checks, push the final candidate, and open or mark
one PR Ready against `main`. Dispatch review immediately on that frozen head;
the verifier returns a code verdict without waiting for CI. A correction gets
delta review plus its required new-head CI; broaden review only for affected
dependencies or concrete interaction risk. Main movement alone is not a reason
to repeat review or checks. The coordinator consumes the single observer's
CI evidence and verifies exact-head required statuses immediately before merge.

### Guardrails

Size is measured per PR, at the head you push; "changed lines" is additions +
deletions. Each pair below is two independent limits: crossing **either**
number crosses that guardrail.

| Guardrail | Value | Effect |
|---|---|---|
| **Hard ceiling** | 10 files · 1000 changed lines | Do not cross; decompose first (`/decompose-issue`). Not author-waivable — the owner grants an exception, or the coordinating session does under the owner's delegation of 2026-09-03 (file count only; the line ceiling stays the owner's); either way the exception is recorded as the first line of the PR body before merge, with the count stated as `N code + M regenerated baselines = K files` (the PR's file list at the head, PNGs included). |
| **Soft threshold** | 6 files · 600 changed lines | Forbids nothing; the PR body must say why this is one unit of work. |
| New abstractions/layers | forbidden unless issue requires | |
| Speculative improvements | forbidden | |

### Failure handling

After two unsuccessful attempts without new evidence, stop repeating that
approach: classify the failure (`invalid_plan`, `impl_error`, `test_failure`,
`env_issue`, or `workflow_violation`), then change the hypothesis, escalate the
bounded problem, or report an external blocker. A failed build alone does not
justify model escalation. Continue useful in-scope work; arbitrary retry counts
do not force abandonment. Real scope, permission, or safety collisions stop
dependent action until resolved.

### Workspace lifecycle and context

Preserve active or uncertain worktrees and processes. PR creation, failure, or
session rotation is not permission for forced cleanup. Verify released
ownership, retained work/evidence, clean state, and existing cleanup authority
before removing a worktree; do not automatically prune on startup.

At semantic milestones, assess repeated loads and context degradation against
rehydration cost. Compactions, context size, or two corrections alone do not
require `/clear` or a reset. Keep cumulative input, cached input, context
occupancy, and remaining allowance distinct. Supported succession requires the
versioned private checkpoint and ordered ownership protocol in
`docs/internals/coordinator-policy.md`; policy adoption does not activate a
lifecycle handler.
