# `profiles` layer

## Charter

Rig profiles, capability matrices, and the TOML rig-config loader.
Profiles are loaded from `rigs/*.toml` — adding a new radio is a TOML
file plus zero Python changes. Encodes the data-driven contract that
runtime/backend layers consult for routing, validation, and UI surfacing.

## Public API

`profiles/__init__.py` exports:

- `RadioProfile` — frozen dataclass: capabilities, modes, filters,
  VFO codes, freq ranges, controls, meter calibrations, keyboard config.
- `ControlSpec`, `RuleSpec`, `MeterCalibrationPoint`,
  `FilterWidthRule`, `FilterWidthSegment` — TypedDict / dataclass shapes
  consumed by runtime adapters.
- `KeyboardBinding`, `KeyboardConfig` — UI-surfaced shortcut config.
- `get_radio_profile(name_or_id)` — registry lookup by model or id.
- `resolve_radio_profile(profile=, model=, radio_addr=)` — unified
  resolution entry; falls back to IC-7610 / first LAN-capable profile.
- `reload_profiles()` — test/dev helper that resets the lazy registry.

`profiles/rig_loader.py` exposes `discover_rigs()` and the TOML schema
parsing internals; consumers go through `profiles.__init__` rather than
reaching into `rig_loader` directly.

## Allowed dependencies

`core`, `commands` (plan §3 matrix row `profiles`). The TOML loader
constructs `CommandSpec`/`CommandMap` from the `[commands]` table, which
forces the dependency on `commands`.

A function-local cycle-breaker import lives at `profiles/__init__.py`
inside `_ensure_loaded()` (`from .rig_loader import discover_rigs`). This
is load-bearing — `rig_loader` imports from the profiles module-level
types. **Do not hoist it to module top-level** (plan §6.2 no-touch list).

## Forbidden patterns

- `from rigplane.runtime`, `from rigplane.audio`, `from rigplane.web` —
  profiles are read-only data; consumers depend on profiles, not the
  reverse.
- Any I/O outside the lazy `_ensure_loaded()` path. Profile lookup must
  not trigger network/disk on hot paths beyond the first load.
- Hardcoded rig facts in Python. New rigs go to `rigs/<rig>.toml`.

## Common operations

- **Add a new radio** → write `rigs/<rig>.toml`, add a regression test
  under `tests/test_rigs_*.py`. No Python changes required.
- **Add a profile field** → extend `RadioProfile` in `profiles/__init__.py`,
  extend the TOML schema in `rig_loader.py`, add a test fixture under
  `tests/test_rig_loader*.py`.
- **Change the registry resolution policy** → edit `resolve_radio_profile`
  in `profiles/__init__.py`; the docstring documents the precedence
  order (explicit > model > civ_addr > IC-7610 default).

## See also

- `docs/plans/2026-04-29-modularization-plan.md` §1.2, §2.2, §3.
- `rigs/*.toml` — actual profile data.
- `profiles/rig_loader.py` — TOML schema validation entry.
- `tests/test_rigs_*.py`, `tests/test_rig_loader*.py` — coverage.
