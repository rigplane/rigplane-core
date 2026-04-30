## Context

The internal-modularization effort (epic + 13 step issues) ships **silent re-export shims** at the old top-level paths so that the 43 private-internal-symbol imports across 15 test files keep working. That decision is correct for the migration itself (it isolates the move from the test rewrite), but it leaves technical debt: tests in this repo continue to import from `icom_lan._<private>` paths long after the canonical homes are settled.

**This issue is post-modularization cleanup.** It MUST NOT start until the modularization epic (#1283) is closed.

References:
- Discovery doc §6 — [`docs/plans/2026-04-29-modularization-discovery.md` §6 Internal-symbol leaks](https://github.com/morozsm/icom-lan/blob/refactor/modularization-discovery/docs/plans/2026-04-29-modularization-discovery.md#internal-symbol-leaks-private-paths-used-by-external-code).
- Plan doc §0 (locked decision 3) and §6 R2.

## Scope

Rewrite test imports across the 15 affected files so that they go through the **canonical** post-migration paths (e.g. `from icom_lan.runtime._connection_state import …`) instead of the legacy top-level shims (e.g. `from icom_lan._connection_state import …`).

**Worst offenders** (per discovery §6):

| Old path | Files | Total occurrences |
|---|---:|---:|
| `icom_lan._connection_state` | 5 (incl. `tests/test_radio_coverage.py` with 13 function-local re-imports) | 22 |
| `icom_lan._civ_rx` | 4 | 8 |
| `icom_lan._poller_types` | 1 | 4 |
| (others — full list in discovery §6) | balance | 9 |

**Total surface: 15 test files, 43 import sites.**

## Out of scope

- Removing the silent re-export shims at the old top-level paths. Those stay as the backwards-compatibility surface for any out-of-tree consumer that may also have reached into private paths. Removal is a separate decision — file a fresh issue if appropriate after this work lands.
- Adding new tests, refactoring tested behaviour, or relocating test files. This is import-rewrite-only.
- Touching `icom-lan-pro` (downstream uses zero private paths per discovery §6).

## Why this is post-modularization

- Some private symbols may need a new public-facing entry point (e.g. a helper that tests import via `_connection_state` may need to be exposed through `icom_lan.runtime` or kept private with the test using the canonical underscored path). That decision belongs to a fresh look at the test surface, not to the migration.
- The migration epic's acceptance criterion is "all tests pass unchanged via shims" — touching the tests during the migration would conflate two failure modes (import-rewrite errors vs migration-shim errors) and slow down debugging on the bulk-move steps.
- The shims have no runtime cost; deferring is cheap.

## Acceptance (when this work finally happens)

- All 43 private-path imports across the 15 test files migrated to the canonical paths.
- Full test suite passes: `uv run pytest tests/ -q --tb=short --ignore=tests/integration` (test count unchanged).
- `uv run ruff check src/ tests/` clean.
- `uv run mypy src/` clean (this issue does not modify `src/`, so mypy should be unaffected).

## Labels

- `refactor`, `followup-after-modularization`, `testing`.
