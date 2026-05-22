---
robots: noindex, follow
---

# CLI design and technology choices

## Current implementation

The CLI is implemented in a single module, `src/rigplane/cli.py`, using the **stdlib `argparse`** only (~1860 lines). There are no runtime dependencies for the CLI beyond the library itself.

- **Parser:** One root parser and subparsers for commands (`status`, `freq`, `mode`, `audio`, `web`, `serve`, etc.). Global options (`--host`, `--user`, `--pass`, `--backend`, `--serial-port`, …) are defined once and shared.
- **Dispatch:** After parsing, `_run()` builds a backend config, calls `create_radio(config)`, then runs the selected command with `async with radio:` and a long `if/elif` chain.
- **Backend:** The CLI already uses `create_radio(LanBackendConfig | SerialBackendConfig)` and supports both LAN and serial backends. Commands are intended to work with the abstract `Radio` protocol; some handlers were still typed as `IcomRadio` for historical reasons.

**Verdict:** The implementation is **adequate and maintainable**. It is consistent with the project’s “zero dependencies for core” rule, works correctly, and is well covered by tests. The main improvement is to **depend only on `Radio` and capability protocols** (e.g. `AudioCapable`, `ScopeCapable`) instead of the concrete `IcomRadio` class, so the CLI stays backend-agnostic.

---

## Should we switch to Click or Typer?

### Option A: Keep argparse (recommended for now)

- **Pros:** No new dependency, no behavior change for users, same process as today. Refactoring to `Radio` + capability checks is a small, low-risk change.
- **Cons:** Large file, manual validation, no first-class Pydantic or typed config objects.

### Option B: Migrate to Typer (or Click) later

- **Typer** (built on Click) gives:
  - Type hints for options/arguments and automatic validation.
  - Optional **Pydantic** integration for settings (e.g. env vars, config file).
  - Cleaner subcommand structure via decorators.
- **Cost:** New runtime dependency (`typer` → `click`). The CLI would either require `pip install rigplane[cli]` with typer, or the dependency would be added to the default install (common for apps with a rich CLI).
- **Recommendation:** Treat this as an **optional follow-up**, not a prerequisite for the Radio/capability refactor. First complete the refactor to `Radio` + capability protocols with the current argparse CLI; then, if desired, plan a separate change to introduce Typer (e.g. split subcommands into modules, use Pydantic for config) without changing user-facing behavior.

### Option C: Click without Typer

- **Click** alone is widely used and stable but does not use type hints for CLI signature; you can still add Pydantic in your own code. Typer is generally a better fit for type-hint–first projects.

---

## Summary

- **Current CLI:** Normal and fine; refactor to `Radio` + capability protocols without changing the CLI framework.
- **Modern libraries:** A move to **Typer** (with optional Pydantic) is reasonable as a **later** step if we want nicer structure and validation; it is not required for correctness or for the issue 207 refactor.
