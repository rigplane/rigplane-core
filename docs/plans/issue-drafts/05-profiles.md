## Context

Step 5 of the internal-modularization migration: move `profiles.py` and `rig_loader.py` into `src/icom_lan/profiles/`, leaving silent re-export shims at the old top-level paths.

Plan section: [§4.1 Step 5 — `profiles`](https://github.com/morozsm/icom-lan/blob/refactor/modularization-discovery/docs/plans/2026-04-29-modularization-plan.md#step-5--profiles).

## Pre-conditions

Blocked by #1287 (Step 4: commands top-level).

## Scope

Move these 2 files from `src/icom_lan/` into `src/icom_lan/profiles/`:

1. `src/icom_lan/profiles.py` → `src/icom_lan/profiles/profiles.py` (or keep as `profiles/__init__.py` body — sub-agent's call, but if going that route, the existing `profiles/__init__.py` stub from Step 1 is overwritten and the result must still pass the contract tests).
2. `src/icom_lan/rig_loader.py` → `src/icom_lan/profiles/rig_loader.py`

Add **2 re-export shim files** at the old top-level paths (`src/icom_lan/profiles.py`, `src/icom_lan/rig_loader.py`) using the plan §5.1 template.

**CRITICAL pre-mitigation (plan §6.2 / R3):** the function-local cycle-breaker at `src/icom_lan/profiles.py:266` (`from .rig_loader import …` inside a function body) MUST be preserved verbatim. Do NOT hoist it to module level. Same for the top-level `rig_loader → profiles` import. These two imports together form the runtime cycle-breaker; both must stay exactly where they are.

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
  - `uv run python -c "from icom_lan.profiles import RadioProfile, load_profile_toml"`
  - `uv run python -c "from icom_lan.rig_loader import *"` (legacy path via shim).
  - `uv run python -c "from icom_lan import Radio; r = Radio  # ensure top-level still imports"`

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
1. Create branch refactor/modularization-step-5 from main
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
- **Confirm `profiles.py:266` (now in its new location) still has `from .rig_loader import …` inside a function body, not at top level.** This is the documented cycle-breaker; lifting it to module level reintroduces an import cycle.
- LAZY_MAP target tuples must be UNCHANGED.
- Verify the top-level `rig_loader.py` import of `profiles` is preserved exactly as it was (the other half of the cycle-breaker).
