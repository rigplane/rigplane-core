## Context

Step 6 of the internal-modularization migration: move `scope.py` and `scope_render.py` into `src/icom_lan/scope/`, leaving silent re-export shims at the old top-level paths. This is a tiny step — small surface, no known risks.

Plan section: [§4.1 Step 6 — `scope`](https://github.com/morozsm/icom-lan/blob/refactor/modularization-discovery/docs/plans/2026-04-29-modularization-plan.md#step-6--scope).

## Pre-conditions

Blocked by #1288 (Step 5: profiles).

## Scope

Move these 2 files from `src/icom_lan/` into `src/icom_lan/scope/`:

1. `src/icom_lan/scope.py` → `src/icom_lan/scope/scope.py` (or fold into `scope/__init__.py` body — sub-agent's call).
2. `src/icom_lan/scope_render.py` → `src/icom_lan/scope/scope_render.py`

Add **2 re-export shim files** at the old top-level paths (`src/icom_lan/scope.py`, `src/icom_lan/scope_render.py`) using the plan §5.1 template.

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
  - `uv run python -c "from icom_lan.scope import ScopeFrame, assemble_scope_frame"`
  - `uv run python -c "from icom_lan.scope_render import render_scope_image"` (legacy path via shim).
  - `uv run python -c "from icom_lan import ScopeFixedEdge"` (Tier 1 still resolves).

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
1. Create branch refactor/modularization-step-6 from main
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
- LAZY_MAP target tuples must be UNCHANGED.
- Confirm the `radio_protocol → scope` TYPE_CHECKING-only edge (declared as a stable `ignore_imports` exception in plan §3.3) still resolves correctly through the new path.
