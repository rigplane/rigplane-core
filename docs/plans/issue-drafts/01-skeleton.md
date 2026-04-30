## Context

Step 1 of the internal-modularization migration: create the empty package skeleton (`core/`, `profiles/`, `runtime/`, `scope/`, `cli/`) and commit `tests/contracts/test_lazy_imports.py` — the public-API contract test that gates every subsequent step. No source files move in this step.

Plan section: [§4.1 Step 1 — Skeleton + lazy-resolution contract test](https://github.com/morozsm/icom-lan/blob/refactor/modularization-discovery/docs/plans/2026-04-29-modularization-plan.md#step-1--skeleton--lazy-resolution-contract-test).

## Pre-conditions

This is the first step.

## Scope

Create only:

1. `src/icom_lan/core/__init__.py` — stub with module docstring pointing at the future `LAYER.md`.
2. `src/icom_lan/profiles/__init__.py` — stub with docstring.
3. `src/icom_lan/runtime/__init__.py` — stub with docstring.
4. `src/icom_lan/scope/__init__.py` — stub with docstring.
5. `src/icom_lan/cli/__init__.py` — stub with docstring.
6. `tests/contracts/__init__.py` — empty package marker.
7. `tests/contracts/test_lazy_imports.py` — contract test with three functions (`test_tier1_names_resolve`, `test_tier2_lazy_names_resolve`, `test_audio_lazy_names_resolve`) per plan §6.1. The Tier 1 / Tier 2 / audio name lists are transcribed verbatim as Python literals from `docs/plans/discovery-artifacts/init-snapshot.md`. **Do NOT reflect on `icom_lan._LAZY_MAP` at runtime** — the lists are the source of truth.

The 5 new `__init__.py` stubs must be importable but contribute no public symbols (no `__all__`, no re-exports). Their job in this step is only to physically create the directories so that subsequent steps can move files into them.

## Out of scope

- No behaviour changes whatsoever.
- No new tests for new functionality (Step 1 commits the public-API contract test, but it tests the existing surface).
- No edits to `_LAZY_MAP` (deferred to Step 13; see plan §5.4).
- No imports outside the listed scope, even if a "cleaner" import suggests itself.
- No silent fixups, no "while we're at it" refactors.

## Acceptance criteria

- `uv run pytest tests/ -q --tb=short --ignore=tests/integration` reports **5213 tests** (5210 baseline + 3 new contract tests). If the count differs, flag it.
- `uv run ruff check src/ tests/` clean.
- `uv run mypy src/` clean.
- `uv run pytest tests/contracts/test_lazy_imports.py -v` passes with all 3 tests green against the **current pre-migration** layout.
- Smoke check: `uv run python -c "import icom_lan.core, icom_lan.profiles, icom_lan.runtime, icom_lan.scope, icom_lan.cli; print('skeleton OK')"` succeeds (the empty packages import).
- Smoke check (public API stable): `uv run python -c "from icom_lan import Radio, IcomRadio, Mode; print('public API OK')"` succeeds.

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
1. Create branch refactor/modularization-step-1 from main
2. Move/edit only files in scope
3. Add re-export shims for backwards compatibility per the plan (none in this step)
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

- Verify the 5 stub `__init__.py` files contain no `__all__`, no re-exports, no side effects beyond a docstring.
- Verify `tests/contracts/test_lazy_imports.py` uses **hardcoded** Python literal lists, NOT runtime reflection on `_LAZY_MAP`.
- Verify the Tier 1, Tier 2, and audio name lists exactly match `docs/plans/discovery-artifacts/init-snapshot.md`.
- LAZY_MAP target tuples must be UNCHANGED (this step does not touch `icom_lan/__init__.py` or `icom_lan/audio/__init__.py`).
