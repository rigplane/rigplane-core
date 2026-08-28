# CLAUDE.md — Control Plane

**rigplane** — Python 3.11+ asyncio library + Web UI for Icom transceivers over LAN/USB. Version: see `pyproject.toml`.
Live bench: **IC-7300, FTX-1**. *(IC-7610 retired 2026-08-04; X6200 destroyed by lightning 2026-08-11.)* Context: `docs/PROJECT.md`.

---

## Commands (always `uv run`)

```bash
uv run pytest tests/ --ignore=tests/integration -n auto -q --tb=short --timeout=300 --timeout-method=thread  # standard suite (CI parity, ~2 min)
uv run pytest tests/ -q --tb=short                    # serial, incl. integration hooks (profiling/hardware only)
uv run mypy --strict src/rigplane/web                  # type check (CI gate; see note below)
uv run ruff check src/ tests/ && uv run ruff format src/ tests/  # lint+format
```

Never bare `python` or `pytest`. Worktrees: `uv sync --all-extras` first.

`uv run mypy --strict src/rigplane/web` is the only mypy invocation in
`.github/`. Per-PR (`quick.yml`) it runs **only** when that workflow's
`frontend` path filter matches — `frontend/**`, `src/rigplane/web/**`, or
`quick.yml` itself; it runs unconditionally in `full.yml` and `publish.yml`
(the CI workflows table below gives what triggers those). So a PR touching
only `src/rigplane/runtime/` gets no mypy in CI until `full.yml` next runs.
Whole-tree `uv run mypy src/` was clean (0 errors) at commit `e2dcbe0f`
(MOR-1967). It remains advisory and ungated — nothing runs it in `.github/` —
and whether to add it as a CI gate is a separate, still-open decision.

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
- `skins/` → skin registry + entry points; `SkinId` in `skins/registry.ts` is the list
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

## Sanctioned duplication

Input for the `mechanism-audit` skill and for any review that flags repeated
functionality. Everything listed here is deliberate: report it as sanctioned,
do not open findings against it. Anything NOT listed is fair game.

- **Backwards-compat shims.** The old top-level paths (`rigplane.radio`,
  `rigplane.commander`, `rigplane.rig_loader`, …) are `sys.modules` aliases
  re-exporting their canonical layer location; each shim names its own target,
  and the targets are spread across several layers, not one. Read the shim.
  Re-export lists (`__all__` blocks) are not definition sites.
- **Backend implementations.** Each backend independently satisfies the
  Capability Protocols in `core.radio_protocol`. That is the extension point —
  two backends implementing `set_freq` is the design working, not duplication.
- **Open-core boundary.** Pro consumes rigplane as a separate process over
  HTTP/WebSocket plus a narrow library import surface, named in
  `docs/architecture/open-core-policy.md` ("Today"). What support each of those
  names carries is stated in `docs/api/public-api-surface.md` — read the tier
  there rather than a copy here. This repo cannot see Pro's consumers, so
  nothing on that surface is dead merely because nothing here calls it.
  `frontend/src/lib/local-extensions/` is the separate UI extension host and is
  not part of that Python surface.
- **Skins.** `frontend/src/skins/` — multiple presentations of one state, by
  design. Take the list from `SkinId` in `frontend/src/skins/registry.ts`, not
  from here: at the time of writing it declares six loadable ids, plus the
  persisted legacy alias `amber-lcd` that resolves to `lcd-cockpit`. This
  sentence is a pointer, not a copy — a copy goes stale and then licenses
  findings against whichever skin it forgot.
- **Per-protocol routing.** `RigctldRoutingStrategy` / `RigctldRoutable` in
  `core/radio_protocol.py` is rigctld's declared customization point for vendor
  differences.
- **Divergent TX gates between front-ends.** rigctld is "drop only — no lane"
  (MOR-1881). Web blocks `RAW_CIV`, `SCAN_START`, `ANTENNA_SWITCH`,
  `TUNER_ENGAGE` and `PTT_ON` synchronously (`_WEB_IMMEDIATE_BLOCK_FAMILIES` in
  `web/radio_poller.py`); `PTT_ON` joined that set under MOR-1879 (owner
  re-ruling 2026-08-17) so that web PTT passes the server interlock on equal
  terms rather than relying on the browser reducer. The divergence between the
  two front-ends is the decision, not drift.
- **Audit method cache.** `.claude/skills/mechanism-audit/SKILL.md` may be present
  in a working tree but is **git-ignored** — it is a local cache of
  `~/.claude/skills/mechanism-audit/SKILL.md`, which stays the single versioned
  copy. It exists because on 2026-08-28 a dispatched run could not read the
  global path — the sandbox refused the mount — and improvised a method instead
  of saying so. Whether a given sandbox can reach `~/.claude/` is not guaranteed
  either way, so the role tries the cache first and the global path second.
  Regenerate per machine:

  ```bash
  mkdir -p .claude/skills/mechanism-audit
  cp ~/.claude/skills/mechanism-audit/SKILL.md .claude/skills/mechanism-audit/SKILL.md
  ```

  Never edit the cache — edits there are invisible to git and will be overwritten.
  If it is stale or missing the `auditor` role stops rather than improvising, so a
  forgotten refresh fails loudly rather than producing a wrong audit.

Keep this list current. An entry added here retires a class of false positives
permanently; an entry that has stopped being true silences a real finding.

---

## Testing

- TDD — test first, implement second
- Batch all fixes, run tests once (not per fix)
- One full-suite run per tree state: if the code is unchanged since the last recorded full run (e.g. REGCHECK), reuse that result — do not re-run an identical suite
- Audio tests: `FakeAudioBackend` only — no one-off mocks
- Prose is a claim, and claims get checked. For every comment, docstring and document sentence a change adds or touches, ask: could this be false without any test failing? If so, narrow it until it is true, tie it to something that fails when it stops being true (a named constant, a named test, a parsed structure), or delete it — a guarantee stated wider than the code is worse than none, because the next reader stops checking. A claim about what a future change will do belongs in the ticket (MOR-1958). `builder.md` and `verifier.md` point here.

---

## Language & Git

User-facing → **Russian**. Code/commits/docs/PR → **English**.
Commits: `feat(#N):` / `fix(#N):` / `refactor:` / `test:` / `docs:` / `chore:`
One change per commit. Full test suite before push.

Documentation under `docs/` cites code as file plus **symbol name**
(`radio.py: IcomRadio.set_frequency`), never a line number — line numbers
rot silently and a stale one is worse than no citation at all. Existing
`file:line` citations are grandfathered, by exact (file, citation) pair, in
`.github/scripts/doc-citation-baseline.txt`; the doc-citation-gate CI job
fails on any citation not in that baseline, and separately fails if the
baseline itself grew relative to the merge base — that second check reads
git history directly, so it holds even if the baseline file was hand-edited
to match a new citation. To shrink the baseline after converting a citation,
run `.github/scripts/check-doc-citations.sh --regenerate`.

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

**Session handoff:** the previous session's state lives in the Linear document
*Session handoff — rigplane-core*
(https://linear.app/morozsm/document/session-handoff-rigplane-core-9775d5570683).
Read it first; rewrite it last. Never keep session-handoff state in this
repository — it is public.

Use subagents — keep the main session lean. The session that takes the work is
a coordinator: it plans and dispatches, and does not implement. The
implementation agent never reviews its own work (Language & Git above).

Subagent roles with pinned models live in `.claude/agents/`: `scout` (haiku,
read-only status/fact collection), `builder` (sonnet, implementation from a
spec), `verifier` (opus, independent review and gate verdicts), `researcher`
(sonnet, read-only exploration with synthesis), `auditor` (opus, read-only
adjudication of duplication, displacement and dead code). Dispatch through these
roles by default; a dispatch outside them must pass an explicit model — never let
a subagent silently inherit the root session's model.

`auditor` carries no method of its own and refuses to run without one. It reads
the git-ignored cache described under "Sanctioned duplication"; a dispatch may
supply a method inline instead. The refusal is deliberate: on 2026-08-28 a
dispatched run could not read the global skill path — the sandbox refused the
mount — and improvised a method without saying so.

Slash commands for scoped workflows live in `.claude/commands/` (`audit-ui`,
`decompose-issue`, `generate-tests`, `next`, `refactor`, `regression-check`,
`scan-issues`, `solve-issue`) plus the `release` skill in `.claude/skills/`;
each file is self-documenting.

### Guardrails

Size is measured per PR, at the head you push; "changed lines" is additions +
deletions. Each pair below is two independent limits: crossing **either**
number crosses that guardrail.

| Guardrail | Value | Effect |
|---|---|---|
| **Hard ceiling** | 10 files · 1000 changed lines | Do not cross; decompose first (`/decompose-issue`). Not author-waivable — only the owner grants an exception, in the PR, before merge. |
| **Soft threshold** | 6 files · 600 changed lines | Forbids nothing; the PR body must say why this is one unit of work. |
| New abstractions/layers | forbidden unless issue requires | |
| Speculative improvements | forbidden | |

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
