# CLAUDE.md — Control Plane

**rigplane** — Python 3.11+ asyncio library + Web UI for Icom transceivers over LAN/USB. Version: see `pyproject.toml`.
Live bench: **IC-7300, FTX-1**. *(IC-7610 retired 2026-08-04; X6200 destroyed by lightning 2026-08-11.)* Context: `docs/PROJECT.md`.

---

## Commands (always `uv run`)

```bash
uv run pytest tests/ --ignore=tests/integration -n auto -q --tb=short --timeout=300 --timeout-method=thread  # standard suite (CI parity, ~2 min)
uv run pytest tests/ -q --tb=short                    # serial, incl. integration hooks (profiling/hardware only)
uv run mypy src/                                       # type check
uv run ruff check src/ tests/ && uv run ruff format src/ tests/  # lint+format
```

Never bare `python` or `pytest`. Worktrees: `uv sync --all-extras` first.

---

## CI workflows (Actions billing-aware)

Three workflows, tiered by cost:

| Workflow | Trigger | Scope |
|---|---|---|
| `quick.yml` | push/PR to `main` only when `src/**`, `tests/**`, `frontend/**`, `pyproject.toml`, `uv.lock`, `.importlinter`, or `.github/workflows/**` change | Python 3.11 only · ruff · import-linter · pytest (no integration) · frontend block runs **only** if `frontend/**` or `src/rigplane/web/**` changed · badges |
| `full.yml` | cron Mon/Wed/Fri 03:00 UTC + `workflow_dispatch` + push with `[full-ci]` in commit message | Full matrix 3.11/3.12/3.13, everything |
| `publish.yml` | `release: published` | New `validate` job (full matrix) → `build` → `publish`. No publish if validate fails. |

Trigger Full manually: append `[full-ci]` to a commit message, or `gh workflow run "Tests (full matrix)"`.

Don't add per-push matrix builds back without explicit reason — the goal is minimum Actions minutes.

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
- `components-v2/wiring/` → state-adapter + command-bus (adapter layer)
- `components-v2/panels/` + `layout/` → presentation only, NO direct store/transport imports
- `skins/` → skin registry + entry points (desktop-v2, amber-lcd, mobile)
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
- Batch all fixes, run tests once (not per fix)
- One full-suite run per tree state: if the code is unchanged since the last recorded full run (e.g. REGCHECK), reuse that result — do not re-run an identical suite
- Audio tests: `FakeAudioBackend` only — no one-off mocks

---

## Language & Git

User-facing → **Russian**. Code/commits/docs/PR → **English**.
Commits: `feat(#N):` / `fix(#N):` / `refactor:` / `test:` / `docs:` / `chore:`
One change per commit. Full test suite before push.

Documentation under `docs/` cites code as file plus **symbol name**
(`radio.py: IcomRadio.set_frequency`), never a line number — line numbers
rot silently and a stale one is worse than no citation at all. Existing
`file:line` citations are grandfathered in
`.github/scripts/doc-citation-baseline.txt`; the doc-citation-gate CI job
fails on any new one and the baseline may only shrink (regenerate it with
`.github/scripts/check-doc-citations.sh --regenerate`).

### Multi-machine Git hygiene

Development runs across a local laptop and a dev Mac mini, often with several
agents. Before editing:

```bash
git fetch --all --tags --prune
git status --short --branch
python3 ~/.codex/skills/repo-hygiene/scripts/repo_inventory.py --roots /Users/moroz/Projects --summary
```

Rules:

- never work directly on `main`;
- use `codex/<issue-or-task>` for agent work;
- use `git pull --ff-only --tags` only on a clean branch with a normal upstream;
- do not reset, clean, delete, or rebase uncertain work without explicit user
  approval;
- report or snapshot dirty trees before sync.

`main` is protected. RigPlane uses `.github/workflows/agent-review-gate.yml`
as the standard PR automation gate. The workflow passes only after a normal PR
comment contains `Agent Review: PASS` for the current PR head; use the required
commit status `Agent Review Gate` instead of GitHub required approving reviews,
because same-user approval restrictions break automated agent flow.

Non-trivial PRs require independent agent review before merge. The
implementation agent cannot self-review. `Agent Review: PASS` means the PR may
merge once required checks are green, the PASS comment is fresh for the current
head, and the PR is not draft. `Agent Review: BLOCKED` must include concrete
problems, file/line references where applicable, risk, required fixes, and
checks to run. The implementation agent must fix
BLOCKED feedback, push updates, and rerun or wait for checks before merge.

A failed `Agent Review Gate` without BLOCKED feedback usually means no fresh
PASS comment exists for the current head; perform or refresh the review instead
of skipping the PR. Cancelled checks must be rerun with `gh run rerun <run-id>`
or a new push, then watched to completion. Draft PRs must not merge: determine
why the PR is draft, finish the missing work, run `gh pr ready`, then complete
checks and review.

Release branches are exceptional: use `release/<major.minor>` only when a
public release needs stabilization while `main` moves ahead. Tags remain the
published artifact source of truth, and release-branch hotfixes must return to
`main`.

---

## Completion criteria

Work is complete ONLY when ALL pass:
1. `uv run pytest tests/ --ignore=tests/integration -n auto -q --tb=short --timeout=300 --timeout-method=thread` — zero failures
2. `uv run ruff check src/ tests/` — zero violations
3. `git diff` — no unintended changes

Incomplete → continue or FAILED. Never skip.

---

## Agent working rules

**GitHub Project control plane:** non-trivial work should be tracked in
`RigPlane Core Roadmap` (https://github.com/orgs/rigplane/projects/2). Work
from GitHub issues with acceptance criteria, add missing issues to the Project,
and keep fields current while working. See
`docs/internals/github-project-workflow.md`.

Use subagents for large exploration/review — keep the main session lean. The
implementation agent never reviews its own work; independent review and the
Agent Review Gate mechanics are described in Language & Git above.

Subagent roles with pinned models live in `.claude/agents/`: `scout` (haiku,
read-only status/fact collection), `builder` (sonnet, implementation from a
spec), `verifier` (opus, independent review and gate verdicts), `researcher`
(sonnet, read-only exploration with synthesis). Dispatch through these roles
by default; a dispatch outside them must pass an explicit model — never let a
subagent silently inherit the root session's model.

Slash commands for scoped workflows live in `.claude/commands/` (`scan-issues`,
`solve-issue`, `next`, `regression-check`, `generate-tests`, `analyze-failure`,
`refactor`, `decompose-issue`) plus the `release` skill; each file is
self-documenting.

### Guardrails

| Limit | Value |
|-------|-------|
| Files per change | 3 |
| LOC delta | 400 |
| New abstractions/layers | forbidden unless issue requires |
| Speculative improvements | forbidden |

Scope exceeding the guardrails → decompose first (`/decompose-issue`).

### Failure handling

- 2 consecutive failures or no progress → **STOP**, mark FAILED
- Max cycles: 2 execution, 2 review, 2 test-fix. Exceeded → FAILED.
- On FAILED, classify (`invalid_plan` / `impl_error` / `test_failure` /
  `env_issue` / `workflow_violation`) and record the reason in the PR/ticket.

### Workspace lifecycle

Worktrees are ephemeral. Cleanup is mandatory and automatic.
- After PR created or issue marked FAILED/SKIPPED → `git worktree remove <path> --force`
- Never `rm -rf` — always use git worktree commands
- Persist only if explicitly marked for manual review
- On startup: `git worktree prune` to clear orphans

---

## Context hygiene

- Repeated mistakes or inconsistent decisions → `/clear`
- 2+ corrections on same step → session reset
