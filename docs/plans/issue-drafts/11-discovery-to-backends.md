## Context

Step 11 of the internal-modularization migration: move `discovery.py` (the multi-protocol radio discovery utility) into `src/icom_lan/backends/discovery.py`. The `backends/__init__.py` re-exports the public entrypoint so consumers calling the discovery helper through the backends package keep working.

Plan section: [§4.1 Step 11 — `discovery → backends/`](https://github.com/morozsm/icom-lan/blob/refactor/modularization-discovery/docs/plans/2026-04-29-modularization-plan.md#step-11--discovery--backends).

## Pre-conditions

Blocked by #1293 (Step 10: runtime part 3).

## Scope

Move 1 file:

1. `src/icom_lan/discovery.py` → `src/icom_lan/backends/discovery.py`

Add **1 re-export shim file** at `src/icom_lan/discovery.py` using the plan §5.1 template. Verify (and if necessary update) `src/icom_lan/backends/__init__.py` so that the public entrypoint (`discover_backends` or whatever it is named) is exposed via `from icom_lan.backends import …`.

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
  - `uv run python -c "from icom_lan.discovery import *"` (legacy path via shim).
  - `uv run python -c "from icom_lan.backends.discovery import *"` (canonical).

## Implementation prompt for the sub-agent

```
You are implementing one step of the icom-lan internal modularization
work. Read these references first:
- /Users/moroz/Projects/icom-lan-research/2026-04-29-internal-modularization-orchestrator.md
- docs/plans/2026-04-29-modularization-plan.md
- The full text of this issue, especially the Scope and Acceptance
  Criteria sections

Your scope is exactly the files listed in Scope. You may not modify
any other file. You may not change runtime behavior. You may not add
tests for new functionality.

Workflow:
1. Create branch refactor/modularization-step-11 from main
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

- Verify the shim header (plan §5.1 template, verbatim) is present in the shim file.
- LAZY_MAP target tuples must be UNCHANGED.
- Confirm `backends/__init__.py` exposes the discovery helper canonically.
- Confirm CLI's `discover` subcommand still works end-to-end (per the existing CLI tests).
