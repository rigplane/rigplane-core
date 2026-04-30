## Context

`src/icom_lan/web/web_startup.py` currently directly instantiates `backends.yaesu_cat.poller` and `backends.yaesu_cat.radio` rather than going through the canonical `backends.factory.create_radio(BackendConfig)` entry point. This violates the layered architecture's `web → backends` rule (web should depend on `backends.factory`'s public surface, not on `backends.yaesu_cat`'s internal modules).

The modularization epic encodes this as a **transitional `web → backends` edge** with two named `ignore_imports` exceptions in the import-linter contract (plan §3.3 + §8.3):

```ini
icom_lan.web.web_startup -> icom_lan.backends.yaesu_cat.poller   ; transitional, see followup
icom_lan.web.web_startup -> icom_lan.backends.yaesu_cat.radio    ; transitional, see followup
```

This issue lands the refactor that lets those two ignores be removed.

References:
- Plan doc §1.3 (deviations from the brief skeleton) — explains why this is provisional.
- Plan doc §3.3 (named ignore exceptions).

**This issue is post-modularization cleanup.** It MUST NOT start until the modularization epic (#1283) is closed.

## Scope

1. **Refactor `src/icom_lan/web/web_startup.py`** so that any `from icom_lan.backends.yaesu_cat import poller, radio` (or equivalent direct-instantiation pattern) is replaced with a call into `icom_lan.backends.factory.create_radio(BackendConfig)` using the appropriate `YaesuCatBackendConfig`.
2. **Remove the two `ignore_imports` entries** from `.importlinter` once the refactor lands and `uv run lint-imports` passes clean.
3. Verify the existing web-server tests still cover the codepath (do not add new tests; if a coverage gap is found, file a separate issue).

## Out of scope

- Removing the layered `web → backends` edge entirely. `web` legitimately depends on `backends` for the factory; the rule already permits that edge. The transitional ignores are about the *yaesu_cat-specific internal modules*, not about `backends` at large.
- Refactoring any other web-startup code paths.
- Adding new features or changing web behaviour.

## Why this is post-modularization

- `web_startup.py` lives under `src/icom_lan/web/` which is **untouched** by every step of the modularization epic (Step 1–13 leave `web/` alone). The transitional edge exists because the discovery turned up the violation; resolving it requires touching `web/`, which is out of scope.
- The factory-based path is more testable (`BackendConfig` is the canonical seam) — but redoing it during the modularization would mix two unrelated changes in the same PR.

## Acceptance (when this work finally happens)

- `web_startup.py` no longer imports from `icom_lan.backends.yaesu_cat.*`; it calls `backends.factory.create_radio(YaesuCatBackendConfig(...))` instead.
- `.importlinter` has the two transitional `ignore_imports` entries removed.
- `uv run lint-imports` passes clean.
- `uv run pytest tests/ -q --tb=short --ignore=tests/integration` passes (test count unchanged).
- `uv run ruff check src/ tests/` clean.
- `uv run mypy src/` clean.

## Labels

- `refactor`, `followup-after-modularization`, `area:web-ui`.
