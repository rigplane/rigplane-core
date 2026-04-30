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

(Issue numbers will be filled in after the sub-issues are created.)

- [ ] (Step 1) Skeleton + lazy-resolution contract test
- [ ] (Step 2) Move `core` foundationals
- [ ] (Step 3) Move `core` contract trio + transport primitives
- [ ] (Step 4) Move `commands` top-level
- [ ] (Step 5) Move `profiles`
- [ ] (Step 6) Move `scope`
- [ ] (Step 7) Move `audio` top-level
- [ ] (Step 8) Move `runtime` part 1 (radio + state)
- [ ] (Step 9) Move `runtime` part 2 (mixins)
- [ ] (Step 10) Move `runtime` part 3 (pollers + control + sync)
- [ ] (Step 11) Move `discovery.py` to `backends/`
- [ ] (Step 12) Move `cli`
- [ ] (Step 13) `import-linter` integration + `LAZY_MAP` cleanup

## Followup issues

- [ ] [Followup] Migrate tests off private internal imports
- [ ] [Followup] Refactor `web_startup` to use `backends.factory`

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
