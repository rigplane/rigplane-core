## Context

Step 4 of the internal-modularization migration: move the 3 top-level `commands` files (`commander`, `command_map`, `command_spec`) into `src/rigplane/commands/`, leaving silent re-export shims at the old top-level paths. The `commands/` subpackage already exists; this step expands it.

Plan section: [§4.1 Step 4 — `commands` top-level](https://github.com/rigplane/rigplane-core/blob/refactor/modularization-discovery/docs/plans/2026-04-29-modularization-plan.md#step-4--commands-top-level).

## Pre-conditions

Blocked by #1286 (Step 3: core contract trio).

## Scope

Move these 3 files from `src/rigplane/` into `src/rigplane/commands/`:

1. `src/rigplane/commander.py` → `src/rigplane/commands/commander.py`
2. `src/rigplane/command_map.py` → `src/rigplane/commands/command_map.py`
3. `src/rigplane/command_spec.py` → `src/rigplane/commands/command_spec.py`

Add **3 re-export shim files** at the old top-level paths using the plan §5.1 template. The Tier 2 lazy names `IcomCommander` and `Priority` must continue to resolve through `from rigplane import IcomCommander, Priority`.

If the existing `commands/__init__.py` needs name-collision resolution with the moved files, that's in scope; otherwise leave it untouched (LAZY_MAP cleanup is Step 13).

## Out of scope

- No behaviour changes whatsoever.
- No new tests for new functionality (Step 1 commits the public-API contract test, but it tests the existing surface).
- No edits to `_LAZY_MAP` (deferred to Step 13; see plan §5.4).
- No imports outside the listed scope, even if a "cleaner" import suggests itself.
- No silent fixups, no "while we're at it" refactors.

## Acceptance criteria

- `uv run pytest tests/ -q --tb=short --ignore=tests/integration` reports **5213 tests** (unchanged).
- `uv run ruff check src/ tests/` clean.
- `uv run mypy src/` clean.
- `uv run pytest tests/contracts/test_lazy_imports.py -v` passes (3 tests green).
- Public-import smoke check (each must succeed):
  - `uv run python -c "from rigplane import IcomCommander, Priority"` (Tier 2 lazy).
  - `uv run python -c "from rigplane.commander import IcomCommander, Priority"` (legacy path via shim).
  - `uv run python -c "from rigplane.command_map import CommandMap"`
  - `uv run python -c "from rigplane.command_spec import CommandSpec"`
  - `uv run python -c "from rigplane.commands import CommandMap, CommandSpec"` (subpackage re-export, if currently exposed).

## Implementation prompt for the sub-agent

```
You are implementing one step of the rigplane internal modularization
work. Read these references first:
- /Users/moroz/Projects/rigplane-research/2026-04-29-internal-modularization-orchestrator.md
- docs/plans/2026-04-29-modularization-plan.md
- The full text of this issue, especially the Scope and Acceptance
  Criteria sections

Your scope is exactly the files listed in Scope. You may not modify
any other file. You may not change runtime behavior. You may not add
tests for new functionality.

Workflow:
1. Create branch refactor/modularization-step-4 from main
2. Move/edit only files in scope
3. Add re-export shims for backwards compatibility per the plan
4. Run pytest — must pass
5. Run mypy — must not introduce new errors
6. Run ruff check — must not introduce new errors
7. Run lint-imports — skip (not yet integrated; Step 13 introduces it)
8. Commit in atomic semantic commits per the plan
9. Push branch, open PR linking to this issue
10. PR description must follow the template in the orchestrator brief

Constraints: Do not modify any file outside the Scope list. Do not
change behaviour. Do not add tests for new functionality.

If anything is unclear or any check fails for non-obvious reasons,
stop and ask via PR comment. Do not guess.
```

## Reviewer note

- Verify the shim header (plan §5.1 template, verbatim) is present in every shim file.
- LAZY_MAP target tuples must be UNCHANGED — Tier 2 lazy resolution of `IcomCommander`/`Priority` happens through the shim, not via map rewrite.
- Verify no name collision was created between `commands/__init__.py` existing exports and the newly moved `commands/commander.py`/`command_map.py`/`command_spec.py`.
