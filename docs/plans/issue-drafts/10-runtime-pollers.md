## Context

Step 10 of the internal-modularization migration: move 11 runtime helpers (pollers, control-phase, sync wrapper, plus several specialty modules) into `src/icom_lan/runtime/`. Likely splits 10a/10b at execution time per plan size budget. This step covers the **highest concentration of tests reaching into private paths** (per discovery §6: `_connection_state` 22 occurrences, `_civ_rx` 8, `_poller_types` 4) — every shim is load-bearing.

Plan section: [§4.1 Step 10 — `runtime` part 3 (pollers + control + sync + helpers)](https://github.com/morozsm/icom-lan/blob/refactor/modularization-discovery/docs/plans/2026-04-29-modularization-plan.md#step-10--runtime-part-3-pollers--control--sync--helpers).

## Pre-conditions

Blocked by #1292 (Step 9: runtime mixins).

## Scope

Move these 11 files from `src/icom_lan/` into `src/icom_lan/runtime/`:

1. `src/icom_lan/_civ_rx.py` → `src/icom_lan/runtime/_civ_rx.py`
2. `src/icom_lan/_connection_state.py` → `src/icom_lan/runtime/_connection_state.py`
3. `src/icom_lan/_control_phase.py` → `src/icom_lan/runtime/_control_phase.py`
4. `src/icom_lan/_poller_types.py` → `src/icom_lan/runtime/_poller_types.py`
5. `src/icom_lan/_audio_recovery.py` → `src/icom_lan/runtime/_audio_recovery.py`
6. `src/icom_lan/sync.py` → `src/icom_lan/runtime/sync.py`
7. `src/icom_lan/profiles_runtime.py` → `src/icom_lan/runtime/profiles_runtime.py`
8. `src/icom_lan/meter_cal.py` → `src/icom_lan/runtime/meter_cal.py`
9. `src/icom_lan/cw_auto_tuner.py` → `src/icom_lan/runtime/cw_auto_tuner.py`
10. `src/icom_lan/startup_checks.py` → `src/icom_lan/runtime/startup_checks.py`
11. `src/icom_lan/proxy.py` → `src/icom_lan/runtime/proxy.py`

Add **11 re-export shim files** at the old top-level paths using the plan §5.1 template.

## Out of scope

- No behaviour changes whatsoever.
- No new tests for new functionality (Step 1 commits the public-API contract test, but it tests the existing surface).
- No edits to `_LAZY_MAP` (deferred to Step 13; see plan §5.4).
- No imports outside the listed scope, even if a "cleaner" import suggests itself.
- No silent fixups, no "while we're at it" refactors.

## Acceptance criteria

- `uv run pytest tests/ -q --tb=short --ignore=tests/integration` reports **5213 tests** (unchanged). The 22 `_connection_state` test occurrences are the canary — any single one failing means the shim is broken.
- `uv run ruff check src/ tests/` clean.
- `uv run mypy src/` clean.
- `uv run pytest tests/contracts/test_lazy_imports.py -v` passes (3 tests green).
- Public-import smoke check (each must succeed):
  - `uv run python -c "from icom_lan._connection_state import *"` (legacy path via shim — heaviest test consumer).
  - `uv run python -c "from icom_lan._civ_rx import *"` (legacy path via shim).
  - `uv run python -c "from icom_lan._poller_types import *"` (legacy path via shim).
  - `uv run python -c "from icom_lan._control_phase import *"` (legacy path via shim).
  - `uv run python -c "from icom_lan._audio_recovery import *"` (legacy path via shim).
  - `uv run python -c "from icom_lan.sync import SyncIcomRadio"`
  - `uv run python -c "from icom_lan.profiles_runtime import *"` (legacy path via shim).
  - `uv run python -c "from icom_lan.meter_cal import *"` (legacy path via shim).
  - `uv run python -c "from icom_lan.cw_auto_tuner import *"` (legacy path via shim).
  - `uv run python -c "from icom_lan.startup_checks import *"` (legacy path via shim).
  - `uv run python -c "from icom_lan.proxy import *"` (legacy path via shim).

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
1. Create branch refactor/modularization-step-10 from main
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
- **The 43 private-path leak shims (per discovery §6) at `_connection_state`, `_civ_rx`, `_poller_types` etc. are the load-bearing acceptance criterion for this step.** Verify each old top-level path resolves the same names it did before the move.
- `_connection_state` shim is especially critical — `tests/test_radio_coverage.py` alone has 13 function-local re-imports (per discovery §6).
