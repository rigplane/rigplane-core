# CLAUDE.md — Control Plane

**rigplane** v2.0.0 — Python 3.11+ asyncio library + Web UI for Icom transceivers over LAN/USB.
IC-7610 at `192.168.55.40`, CI-V `0x98`. Context: `docs/PROJECT.md`.

---

## Commands (always `uv run`)

```bash
uv run pytest tests/ -q --tb=short                    # all tests
uv run pytest tests/ -q --tb=short --ignore=tests/integration  # skip hw
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
- Audio tests: `FakeAudioBackend` only — no one-off mocks

---

## Language & Git

User-facing → **Russian**. Code/commits/docs/PR → **English**.
Commits: `feat(#N):` / `fix(#N):` / `refactor:` / `test:` / `docs:` / `chore:`
One change per commit. Full test suite before push.

---

## Completion criteria

Work is complete ONLY when ALL pass:
1. `uv run pytest tests/ -q --tb=short` — zero failures
2. `uv run ruff check src/ tests/` — zero violations
3. `git diff` — no unintended changes

Incomplete → continue or FAILED. Never skip.

---

## Autonomous pipeline

**GitHub Project control plane:** non-trivial work should be tracked in
`RigPlane Core Roadmap` (https://github.com/orgs/rigplane/projects/2). Work
from GitHub issues with acceptance criteria, add missing issues to the Project,
and keep fields current while working. See
`docs/internals/github-project-workflow.md`.

Strictly linear. No phase may be skipped or reordered. No exceptions.
State files (`.claude/workflow/*.md`) are the sole source of truth — not memory or reasoning.
CLAUDE.md controls all workflow transitions. Agents must not self-direct transitions.

```
EXPLORE → PLAN → EXECUTE → regression-check → REVIEW → TEST → PR
                                                         ↓ (on FAILED)
                                                    analyze-failure
                                              generate-tests (optional, post-PR)
```

**REVIEW, TEST, and regression-check are mandatory.** Skipping any is `workflow_violation` → STOP + FAILED.
**analyze-failure** runs automatically on every FAILED outcome.

| Command | Action |
|---------|--------|
| `/scan-issues` | score open issues → `.claude/queue/queue.json` |
| `/solve-issue N` | full pipeline for issue #N |
| `/next` | pick highest-priority pending, solve it |
| `/regression-check` | run tests, compare against baseline |
| `/generate-tests` | generate targeted tests for changed code |
| `/analyze-failure` | classify and analyze a pipeline failure |
| `/refactor <target>` | test-safe refactoring (no behavior change, no fast path) |
| `/release [type]` | full release pipeline (precheck → validate → tag → push) |
| `/decompose-issue N` | break epic/large issue into atomic tasks → enqueue |

### Entry conditions (must ALL be true to start)

- Issue has clear expected outcome
- Scope fits guardrails (≤3 files, ≤200 LOC) — if not, `/decompose-issue` first
- No hardware dependency (unless mockable)
- Not an epic or parent issue — only atomic/decomposed tasks
- Otherwise → SKIP or DECOMPOSE

### Fast path

Skip PLAN if ALL true: single file, <20 LOC, no protocol/transport/state, no public API.
Never skip EXPLORE, REVIEW, or TEST.

### Phase state machine

| Phase | Agent | Owns | Gate (ALL required to proceed) |
|-------|-------|------|-------------------------------|
| EXPLORE | researcher | `research.md` | confidence ≥ 0.6 |
| PLAN | planner | `plan.md` | explicit steps written |
| EXECUTE | executor | `progress.md` | implementation done |
| REGCHECK | — | `regression.md` | no new test failures vs baseline |
| REVIEW | reviewer | `review.md` | diff matches plan + no unplanned changes |
| TEST | qa | — | pytest zero + ruff zero + verification ran |

- A phase cannot start until the previous phase gate is satisfied. No shortcuts.
- Each agent has explicit permissions (allowed/forbidden actions) — see agent definitions.
- Each phase writes ONLY its own file. Do not modify other phase files.
- Phase is complete ONLY when its output file is written AND gate condition met.
- Re-read CLAUDE.md before PLAN to prevent drift.
- PLAN is immutable during EXECUTE. Wrong plan → FAIL and restart, do not patch.
- EXECUTE: implement plan exactly. No extras, no refactors, no scope expansion.
- REVIEW: independently compare diff against plan. Do not trust EXECUTE assumptions. Reject deviations.
- TEST: must run after REVIEW, not before. Results must be verified, not assumed.

Definitions: `.claude/agents/{researcher,planner,executor,reviewer,qa}.md`
Use subagents for large exploration/review — keep main session lean.

### Guardrails

| Limit | Value |
|-------|-------|
| Files per change | 3 |
| LOC delta | 200 |
| New abstractions/layers | forbidden unless issue requires |
| Speculative improvements | forbidden |
| Min confidence | 0.6 |

### Failure handling

- 2 consecutive failures or no progress → **STOP**, mark FAILED
- Max cycles: 2 execution, 2 review, 2 test-fix. Exceeded → FAILED.
- On FAILED, classify: `invalid_plan` / `impl_error` / `test_failure` / `env_issue` / `workflow_violation`
- Log classification + reason to `.claude/knowledge/failures.md`
- Load `.claude/knowledge/` ONLY on keyword match or prior failure pattern — not by default

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
