## Context

Step 13 of the internal-modularization migration: introduce `import-linter` for boundary enforcement and rewrite `_LAZY_MAP` target tuples in `icom_lan/__init__.py` and `icom_lan/audio/__init__.py` to point at the canonical post-migration paths. **No source-code moves** in this step — it is the closing tooling step.

Plan sections: [§4.1 Step 13 — `import-linter` integration + `LAZY_MAP` cleanup](https://github.com/morozsm/icom-lan/blob/refactor/modularization-discovery/docs/plans/2026-04-29-modularization-plan.md#step-13--import-linter-integration--lazy_map-cleanup) and [§8 Tooling integration plan](https://github.com/morozsm/icom-lan/blob/refactor/modularization-discovery/docs/plans/2026-04-29-modularization-plan.md#8-tooling-integration-plan).

## Pre-conditions

Blocked by #1295 (Step 12: cli).

## Scope

1. **Add `import-linter` to dev dependencies** in `pyproject.toml` under `[dependency-groups] dev` (NOT `[project] dependencies`). Do not change any other extras structure.
2. **Commit `.importlinter`** at repo root with the contract from plan §8.3 (one `[importlinter:contract:layers]` block + three `[importlinter:contract:independence-*]` blocks for sibling-purity belt-and-braces). The four named `ignore_imports` entries from plan §3.3 are present verbatim.
3. **Wire `lint-imports` into CI** — add a job/step that runs `uv run lint-imports`. Pre-commit hook is deferred to a follow-up issue per plan §8.1.
4. **Rewrite `_LAZY_MAP` target tuples** in:
   - `src/icom_lan/__init__.py`
   - `src/icom_lan/audio/__init__.py`
   …so that every Tier 2 lazy entry points at the **canonical** (post-migration) module path, not the legacy top-level path. Tier 1 eager imports are likewise updated to canonical paths if any still reference the old locations.
5. **No file moves.** No new shims. No source edits beyond the LAZY_MAP rewrite and the `pyproject.toml` / `.importlinter` / CI-config additions.

## Out of scope

- No behaviour changes whatsoever.
- No new tests for new functionality (Step 1 commits the public-API contract test, but it tests the existing surface).
- No removal of the existing re-export shims (they stay as the backwards-compatibility surface).
- No imports outside the listed scope, even if a "cleaner" import suggests itself.
- No silent fixups, no "while we're at it" refactors.
- Pre-commit hook integration (deferred to a follow-up).

## Acceptance criteria

- `uv run pytest tests/ -q --tb=short --ignore=tests/integration` reports **5213 tests** (unchanged).
- `uv run ruff check src/ tests/` clean.
- `uv run mypy src/` clean.
- `uv run pytest tests/contracts/test_lazy_imports.py -v` passes (3 tests green) — every Tier 1 + Tier 2 + audio name still resolves after LAZY_MAP rewrite.
- **`uv run lint-imports` passes** — the layered contract holds, and only the 4 named `ignore_imports` exceptions from plan §3.3 are declared.
- Public-import smoke check (each must succeed):
  - `uv run python -c "from icom_lan import Radio, IcomRadio, Mode, IcomCommander, Priority"` (Tier 1 + Tier 2 lazy via canonical paths).
  - `uv run python -c "from icom_lan.audio import AudioBus, AudioBridge"` (audio LAZY_MAP via canonical paths).
  - `uv run python -c "from icom_lan.audio import backend, dsp"` (icom-lan-pro contract).
  - `uv run python -c "from icom_lan.dsp import pipeline, exceptions"` (icom-lan-pro contract).
  - `uv run python -c "from icom_lan.dsp.nodes import base"` (icom-lan-pro contract).

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
1. Create branch refactor/modularization-step-13 from main
2. Move/edit only files in scope
3. Add re-export shims for backwards compatibility per the plan (none in this step)
4. Run pytest — must pass
5. Run mypy — must not introduce new errors
6. Run ruff check — must not introduce new errors
7. Run lint-imports — must pass (this step introduces it)
8. Commit in atomic semantic commits per the plan
9. Push branch, open PR linking to this issue
10. PR description must follow the template in the orchestrator brief

Constraints: Do not modify any file outside the Scope list. Do not
change behaviour. Do not add tests for new functionality.

If anything is unclear or any check fails for non-obvious reasons,
stop and ask via PR comment. Do not guess.
```

## Reviewer note

- **Confirm the import-linter contract matches plan §8.3 exactly** — one `[importlinter:contract:layers]` block + three `[importlinter:contract:independence-*]` blocks.
- **Confirm `ignore_imports` lists exactly the 4 named exceptions from plan §3.3, verbatim:**
  - `icom_lan.radio_protocol -> icom_lan.audio_bus`
  - `icom_lan.radio_protocol -> icom_lan.scope`
  - `icom_lan.web.web_startup -> icom_lan.backends.yaesu_cat.poller`
  - `icom_lan.web.web_startup -> icom_lan.backends.yaesu_cat.radio`
- **LAZY_MAP target tuples must now be CANONICAL** (post-migration paths). This is the one step where the LAZY_MAP changes; verify each entry points at the new home and the contract test still passes.
- Confirm `import-linter` is added under `[dependency-groups] dev` (not `[project] dependencies`) — runtime install footprint must be unchanged.
- Confirm CI runs `uv run lint-imports`, not `uvx --with-editable . --from import-linter lint-imports` (per plan §8.1).
