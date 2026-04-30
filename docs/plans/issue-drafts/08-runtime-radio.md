## Context

Step 8 of the internal-modularization migration: move the `runtime` part-1 files (`radio.py` — the centerpiece — plus 6 supporting files) into `src/icom_lan/runtime/`. Likely splits 8a/8b at execution time per plan size budget.

Plan section: [§4.1 Step 8 — `runtime` part 1 (radio + state)](https://github.com/morozsm/icom-lan/blob/refactor/modularization-discovery/docs/plans/2026-04-29-modularization-plan.md#step-8--runtime-part-1-radio--state).

## Pre-conditions

Blocked by #1290 (Step 7: audio top-level).

## Scope

Move these 7 files from `src/icom_lan/` into `src/icom_lan/runtime/`:

1. `src/icom_lan/radio.py` → `src/icom_lan/runtime/radio.py`
2. `src/icom_lan/radio_state_snapshot.py` → `src/icom_lan/runtime/radio_state_snapshot.py`
3. `src/icom_lan/radio_initial_state.py` → `src/icom_lan/runtime/radio_initial_state.py`
4. `src/icom_lan/radio_reconnect.py` → `src/icom_lan/runtime/radio_reconnect.py`
5. `src/icom_lan/radios.py` → `src/icom_lan/runtime/radios.py`
6. `src/icom_lan/ic705.py` → `src/icom_lan/runtime/ic705.py`
7. `src/icom_lan/_state_queries.py` → `src/icom_lan/runtime/_state_queries.py`

Add **7 re-export shim files** at the old top-level paths using the plan §5.1 template. The shim at `src/icom_lan/radio.py` is critical — many tests reach into the radio module via the legacy path.

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
  - `uv run python -c "from icom_lan import IcomRadio, Radio"` (Tier 1).
  - `uv run python -c "from icom_lan.radio import IcomRadio"` (legacy path via shim).
  - `uv run python -c "from icom_lan.radios import *"` (legacy path via shim).
  - `uv run python -c "from icom_lan.ic705 import *"` (legacy path via shim).
  - `uv run python -c "from icom_lan.radio_state_snapshot import *"` (legacy path via shim).
  - `uv run python -c "from icom_lan.radio_initial_state import *"` (legacy path via shim).
  - `uv run python -c "from icom_lan.radio_reconnect import *"` (legacy path via shim).
  - `uv run python -c "from icom_lan._state_queries import *"` (legacy path via shim).

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
1. Create branch refactor/modularization-step-8 from main
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
- Confirm `radio.py`'s heavy import surface is preserved unchanged — many tests import from `icom_lan.radio` directly via the shim. The `radio.py` shim is the most-imported one in the suite; verify by `grep -rn 'from icom_lan.radio import' tests/ | wc -l` returning a number consistent with the existing test count.
- Verify `_state_queries` shim is present (it is the one private-name file moving in this step that some test files may reach into directly).
- The 22-occurrence `_connection_state` leak, the 8-occurrence `_civ_rx` leak, and the 4-occurrence `_poller_types` leak (per discovery §6) are NOT addressed by this step — those files move in Step 10. Reviewer of Step 10 must verify those shims; reviewer of Step 8 should NOT see those names move.
