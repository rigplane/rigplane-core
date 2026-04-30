## Mission

Restructure `src/icom_lan/` from a flat-with-some-subdirs layout into a strictly-layered internal module structure with explicit dependency contracts, without breaking the public API or any existing tests. This is **internal refactoring only** — no new PyPI packages, no `pyproject.toml` extras changes, no frontend changes, no new features. The end state enables faster onboarding for AI agents and human contributors (clear layer map), safer refactoring (compile-time-equivalent checks via dependency linting), a cleaner mental model in `ARCHITECTURE.md`, and a foundation that makes a possible future move to namespace packages cheap.

## Source documents

- **Orchestrator brief** (out of repo): `/Users/moroz/Projects/icom-lan-research/2026-04-29-internal-modularization-orchestrator.md`
- **Phase 1 discovery doc**: [`docs/plans/2026-04-29-modularization-discovery.md`](https://github.com/morozsm/icom-lan/blob/refactor/modularization-discovery/docs/plans/2026-04-29-modularization-discovery.md)
- **Phase 2 plan doc**: [`docs/plans/2026-04-29-modularization-plan.md`](https://github.com/morozsm/icom-lan/blob/refactor/modularization-discovery/docs/plans/2026-04-29-modularization-plan.md)

## Locked decisions (Phase 1, maintainer-approved)

- **Silent re-export shims** with grep-able header (template in plan §5.1; verbatim `# Re-export shim for backwards compatibility.` comment block, no `DeprecationWarning`).
- **`import-linter`** is the boundary tool, integrated in **Step 13** (after the bulk-move steps, before final import cleanup).
- **Shims for the 43 private-path leaks** (15 test files reach into `_connection_state`, `_civ_rx`, `_poller_types`, etc.) are produced by this effort; a tracked followup migrates the tests off those private paths post-modularization.
- **`icom-lan-pro` three-tier validation**: end-of-Phase-2 paper check (plan §9, complete) → Phase 4 smoke-imports per `audio/`/`dsp/` PR → Phase 5 full downstream test suite as the definition-of-done marker.

## Sub-issues checklist

- [ ] #1284 [Modularization 1/13] Skeleton + lazy-resolution contract test
- [ ] #1285 [Modularization 2/13] Move `core` foundationals
- [ ] #1286 [Modularization 3/13] Move `core` contract trio + transport primitives
- [ ] #1287 [Modularization 4/13] Move `commands` top-level
- [ ] #1288 [Modularization 5/13] Move `profiles`
- [ ] #1289 [Modularization 6/13] Move `scope`
- [ ] #1290 [Modularization 7/13] Move `audio` top-level
- [ ] #1291 [Modularization 8/13] Move `runtime` part 1 (radio + state)
- [ ] #1292 [Modularization 9/13] Move `runtime` part 2 (mixins)
- [ ] #1293 [Modularization 10/13] Move `runtime` part 3 (pollers + control + sync)
- [ ] #1294 [Modularization 11/13] Move `discovery.py` to `backends/`
- [ ] #1295 [Modularization 12/13] Move `cli`
- [ ] #1296 [Modularization 13/13] `import-linter` integration + `LAZY_MAP` cleanup

## Followup issues

These are post-modularization cleanup; they MUST NOT start until this epic is closed.

- [ ] #1297 [Followup] Migrate tests off private internal imports
- [ ] #1298 [Followup] Refactor `web_startup` to use `backends.factory`

## Definition of done — the whole effort

The work is complete when **all** of the following are true:

1. `src/icom_lan/` has the layer structure agreed in Phase 2.
2. Every layer has an `__init__.py` with explicit `__all__`.
3. Every layer has a `LAYER.md` charter.
4. `import-linter` is in pre-commit and CI, with a config matching the plan.
5. All ~5210+ existing tests pass on `main`.
6. No public import path that worked before this effort returns `ImportError` now.
7. `ARCHITECTURE.md` and `CLAUDE.md` are updated.
8. `icom-lan-pro` (downstream) builds and tests cleanly against the new `icom-lan` (verified by maintainer).
9. The epic issue is closed with a summary comment.
10. The maintainer agrees that this brief's mission has been achieved.
