## Context

Step 2 of the internal-modularization migration: move the 9 foundational `core` files (types, exceptions, auth, transport, civ, protocol, capabilities, env_config, `_optional_deps`) into `src/icom_lan/core/`, leaving silent re-export shims at the old top-level paths.

Plan section: [§4.1 Step 2 — `core` foundationals](https://github.com/morozsm/icom-lan/blob/refactor/modularization-discovery/docs/plans/2026-04-29-modularization-plan.md#step-2--core-foundationals).

## Pre-conditions

Blocked by #1284 (Step 1: skeleton).

## Scope

Move these 9 files from `src/icom_lan/` into `src/icom_lan/core/`:

1. `src/icom_lan/types.py` → `src/icom_lan/core/types.py`
2. `src/icom_lan/exceptions.py` → `src/icom_lan/core/exceptions.py`
3. `src/icom_lan/auth.py` → `src/icom_lan/core/auth.py`
4. `src/icom_lan/transport.py` → `src/icom_lan/core/transport.py`
5. `src/icom_lan/civ.py` → `src/icom_lan/core/civ.py`
6. `src/icom_lan/protocol.py` → `src/icom_lan/core/protocol.py`
7. `src/icom_lan/capabilities.py` → `src/icom_lan/core/capabilities.py`
8. `src/icom_lan/env_config.py` → `src/icom_lan/core/env_config.py`
9. `src/icom_lan/_optional_deps.py` → `src/icom_lan/core/_optional_deps.py`

Add **9 re-export shim files** at the old top-level paths, each using the exact template from plan §5.1:

```python
# Re-export shim for backwards compatibility.
# Canonical location: icom_lan.core.<module>
# Do not add new symbols here — add them at the canonical location.
from icom_lan.core.<module> import *  # noqa: F401, F403
```

If `transport.py` (~500+ LOC) pushes the moved-LOC budget over 500, **split this step into 2a (5 small files) and 2b (transport + civ + protocol)** and file the second sub-issue at execution time per plan §4.1.

## Out of scope

- No behaviour changes whatsoever.
- No new tests for new functionality (Step 1 commits the public-API contract test, but it tests the existing surface).
- No edits to `_LAZY_MAP` (deferred to Step 13; see plan §5.4).
- No imports outside the listed scope, even if a "cleaner" import suggests itself.
- No silent fixups, no "while we're at it" refactors.

## Acceptance criteria

- `uv run pytest tests/ -q --tb=short --ignore=tests/integration` reports **5213 tests** (unchanged from Step 1).
- `uv run ruff check src/ tests/` clean.
- `uv run mypy src/` clean.
- `uv run pytest tests/contracts/test_lazy_imports.py -v` passes (3 tests green).
- Public-import smoke check (each must succeed):
  - `uv run python -c "from icom_lan import Radio, IcomRadio, Mode, AudioCodec, BreakInMode"`
  - `uv run python -c "from icom_lan.transport import UdpTransport, parse_packet, HEADER_SIZE"`
  - `uv run python -c "from icom_lan.civ import build_civ_message"`
  - `uv run python -c "from icom_lan.exceptions import IcomLanError"`
  - `uv run python -c "from icom_lan.auth import Authenticator"`
  - `uv run python -c "from icom_lan.core import types as _t, transport as _tr; print('core OK')"`
- `_optional_deps` has zero `from icom_lan` imports (verifies foundation-tier purity per plan §6 R7).

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
1. Create branch refactor/modularization-step-2 from main
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

- Verify the shim header (the plan §5.1 template, verbatim) is present in every shim file.
- LAZY_MAP target tuples must be UNCHANGED (Step 13 will canonicalise them).
- Verify `_optional_deps.py` (now at `icom_lan/core/_optional_deps.py`) still imports nothing from `icom_lan`.
- Verify atomic-commit policy (plan §5.3): one commit moves files, one commit adds shims (and optionally a third updates internal imports).
