## Context

Step 3 of the internal-modularization migration: move the `core` contract trio (`radio_protocol`, `radio_state`, `_state_cache`) plus transport-adjacent primitives (`_queue_pressure`, `_bounded_queue`) into `src/icom_lan/core/`, leaving silent re-export shims at the old top-level paths.

Plan section: [§4.1 Step 3 — `core` contract trio + transport primitives](https://github.com/morozsm/icom-lan/blob/refactor/modularization-discovery/docs/plans/2026-04-29-modularization-plan.md#step-3--core-contract-trio--transport-primitives).

## Pre-conditions

Blocked by #1285 (Step 2: core foundationals).

## Scope

Move these 5 files from `src/icom_lan/` into `src/icom_lan/core/`:

1. `src/icom_lan/radio_protocol.py` → `src/icom_lan/core/radio_protocol.py`
2. `src/icom_lan/radio_state.py` → `src/icom_lan/core/radio_state.py`
3. `src/icom_lan/_state_cache.py` → `src/icom_lan/core/_state_cache.py`
4. `src/icom_lan/_queue_pressure.py` → `src/icom_lan/core/_queue_pressure.py`
5. `src/icom_lan/_bounded_queue.py` → `src/icom_lan/core/_bounded_queue.py`

Add **5 re-export shim files** at the old top-level paths using the plan §5.1 template.

The TYPE_CHECKING-only imports in `radio_protocol.py` (`audio_bus`, `scope`) are preserved verbatim — they do not execute at import time and remain inside `if TYPE_CHECKING:` blocks. Step 13's `import-linter` config declares them as named `ignore_imports` exceptions.

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
  - `uv run python -c "from icom_lan import Radio"` (Tier 1).
  - `uv run python -c "from icom_lan.radio_protocol import Radio"`
  - `uv run python -c "from icom_lan.radio_state import RadioState, VfoSlotState"`
  - `uv run python -c "from icom_lan._state_cache import StateCache"`
  - `uv run python -c "from icom_lan._queue_pressure import *"` (shim re-exports)
  - `uv run python -c "from icom_lan._bounded_queue import *"` (shim re-exports)

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
1. Create branch refactor/modularization-step-3 from main
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
- Verify the TYPE_CHECKING block in the moved `radio_protocol.py` is intact and has not been hoisted to top-level imports.
- Verify all 32 Tier 1 + 28 Tier 2 names in `tests/contracts/test_lazy_imports.py` resolve.
