## Context

Step 9 of the internal-modularization migration: move the 5 `_*_runtime_mixin` / `_runtime_*` files into `src/icom_lan/runtime/`, leaving silent re-export shims at the old top-level paths. Mixins are internal-only; tests reach them via private paths covered by the shims.

Plan section: [§4.1 Step 9 — `runtime` part 2 (mixins)](https://github.com/morozsm/icom-lan/blob/refactor/modularization-discovery/docs/plans/2026-04-29-modularization-plan.md#step-9--runtime-part-2-mixins).

## Pre-conditions

Blocked by #1291 (Step 8: runtime part 1).

## Scope

Move these 5 files from `src/icom_lan/` into `src/icom_lan/runtime/`:

1. `src/icom_lan/_audio_runtime_mixin.py` → `src/icom_lan/runtime/_audio_runtime_mixin.py`
2. `src/icom_lan/_dual_rx_runtime.py` → `src/icom_lan/runtime/_dual_rx_runtime.py`
3. `src/icom_lan/_scope_runtime.py` → `src/icom_lan/runtime/_scope_runtime.py`
4. `src/icom_lan/_shared_state_runtime.py` → `src/icom_lan/runtime/_shared_state_runtime.py`
5. `src/icom_lan/_runtime_protocols.py` → `src/icom_lan/runtime/_runtime_protocols.py`

Add **5 re-export shim files** at the old top-level paths using the plan §5.1 template.

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
  - `uv run python -c "from icom_lan._audio_runtime_mixin import *"` (legacy path via shim).
  - `uv run python -c "from icom_lan._dual_rx_runtime import *"` (legacy path via shim).
  - `uv run python -c "from icom_lan._scope_runtime import *"` (legacy path via shim).
  - `uv run python -c "from icom_lan._shared_state_runtime import *"` (legacy path via shim).
  - `uv run python -c "from icom_lan._runtime_protocols import *"` (legacy path via shim).
  - `uv run python -c "from icom_lan import IcomRadio; r = IcomRadio  # mixin composition still resolves"`

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
1. Create branch refactor/modularization-step-9 from main
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
- Confirm `IcomRadio`'s mixin composition still works after the move — the class is built from these mixins; if any one fails to import, instantiation breaks.
- Confirm no test file's private-path import to any of the 5 mixin modules has been broken.
