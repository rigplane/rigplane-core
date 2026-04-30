## Context

Step 12 of the internal-modularization migration: move `cli.py` into `src/icom_lan/cli/`. The `__main__.py` placement decision is in this step's PR — recommendation per plan §4.1: keep `__main__.py` at top level for `python -m icom_lan` discoverability, with one delegating line `from icom_lan.cli import main; main()`.

Plan section: [§4.1 Step 12 — `cli`](https://github.com/morozsm/icom-lan/blob/refactor/modularization-discovery/docs/plans/2026-04-29-modularization-plan.md#step-12--cli).

## Pre-conditions

Blocked by #1294 (Step 11: discovery to backends).

## Scope

Move 1 file (and decide the `__main__.py` question):

1. `src/icom_lan/cli.py` → `src/icom_lan/cli/cli.py` (or fold into `cli/__init__.py` body — sub-agent's call).
2. Decision noted in the PR description: does `__main__.py` move into `cli/`, or stay top-level? Recommendation: keep top-level; if it stays, `src/icom_lan/__main__.py` is updated (1-line change) to call `from icom_lan.cli import main; main()`.

Add **1 re-export shim file** at `src/icom_lan/cli.py` using the plan §5.1 template (so the legacy `from icom_lan.cli import …` import continues to work — note: if the file becomes a package, the shim approach changes; sub-agent decides between (a) `cli.py` shim file alongside the new `cli/` package or (b) since you can't have both a `cli.py` and a `cli/` package with the same name, fold `cli.py`'s body into `cli/__init__.py` directly and skip the shim).

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
- Public-import + entrypoint smoke check (each must succeed):
  - `uv run python -c "from icom_lan.cli import main"` (canonical).
  - `uv run python -m icom_lan --help` (`__main__.py` entrypoint resolves; CLI prints help and exits 0).
  - `uv run icom-lan --help` (console-script entrypoint per `pyproject.toml`).

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
1. Create branch refactor/modularization-step-12 from main
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

- Verify the shim header (plan §5.1 template, verbatim) is present in any shim file added.
- LAZY_MAP target tuples must be UNCHANGED.
- Verify the `__main__.py` decision is documented clearly in the PR description.
- Verify the `icom-lan` console script (defined in `pyproject.toml`) still resolves — the entry point may need its target updated, BUT only if the path actually moved; do not edit `pyproject.toml` opportunistically.
