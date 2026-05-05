# `runtime` layer

## Charter

High-level radio orchestration: `IcomRadio` and its mixins (audio, dual
RX, scope, shared-state, runtime protocols), state cache, state queries,
pollers, command queue runtime, audio recovery on reconnect, sync
wrapper, and per-rig runtime helpers (IC-705 data profile, meter
calibration, CW auto-tuner, startup checks). This is the layer that
turns wire-level primitives into the user-visible Radio object.

## Public API

`runtime/__init__.py` keeps `__all__` empty by design (plan §2.2). The
top-level `rigplane` lazy-loader and the legacy top-level shims surface
the canonical types to consumers; layer-internal callers reach the
sub-modules directly:

- `runtime.radio.IcomRadio` — the async high-level API (Tier 1).
- `runtime.sync.IcomRadio` — the synchronous wrapper.
- `runtime.radio_state_snapshot.{snapshot_state, restore_state}`,
  `runtime.radio_initial_state._fetch_initial_state`,
  `runtime.radio_reconnect.*` — orchestration helpers extracted from
  the historic `radio.py` god-object (#1258, #1259, #1260).
- `runtime._poller_types`, `runtime._civ_rx`, `runtime._connection_state`,
  `runtime._control_phase`, `runtime._audio_recovery` — internal
  building blocks; 43 test files reach in via private paths and depend
  on the migration shims.
- `runtime._audio_runtime_mixin`, `runtime._scope_runtime`,
  `runtime._dual_rx_runtime`, `runtime._shared_state_runtime`,
  `runtime._runtime_protocols` — mixins composed onto `IcomRadio`.
- `runtime.profiles_runtime`, `runtime.meter_cal`,
  `runtime.cw_auto_tuner`, `runtime.startup_checks`, `runtime.proxy`,
  `runtime.ic705`, `runtime.radios` — per-rig and helper utilities.

## Allowed dependencies

`core`, `commands`, `profiles`, `audio`, `scope` (plan §3 matrix row
`runtime`). Note the deviation from the brief skeleton: `runtime`
legitimately needs `audio` and `scope` because the IcomRadio mixins
construct audio backends and the scope assembler at runtime
(plan §1.3).

## Forbidden patterns

- `from rigplane.backends` — backends compose runtime, not the reverse.
  The backend factory is the assembly seam; `IcomRadio` does not know
  which factory built it.
- `from rigplane.web` / `from rigplane.rigctld` / `from rigplane.cli` —
  these are upper-tier consumers.
- `from rigplane.dsp` directly — DSP processing flows through `audio`,
  which composes `dsp` internally.
- New cross-layer imports without checking the matrix; `import-linter`
  will catch them at CI but do not commit them.

## Common operations

- **Add a high-level Radio method** → declare on the corresponding
  Capability Protocol in `core.radio_protocol` first, implement on
  `runtime.radio.IcomRadio`, expose via `runtime.sync.IcomRadio`,
  cover with `tests/test_radio*.py`. Web / rigctld surfaces consume
  via `isinstance(radio, FooCapable)`.
- **Add a poller** → conform to the `StatePoller` Protocol
  (`core.radio_protocol`); register via `IcomRadio.create_state_poller`
  per `StatePollable` so `web_startup` discovers it generically (#1298,
  #1323).
- **Touch `_civ_rx._update_radio_state_from_frame`** → it is the
  table-driven dispatch (`_HANDLERS`); add a private handler, not an
  if/elif branch (#1257). Run `tests/test_civ_rx_dispatch_golden.py`.
- **Refactor a `radio.py` mixin** → preserve the public Radio Protocol
  surface; verify `tests/test_public_api_surface.py` and
  `tests/contracts/test_lazy_imports.py` still pass.

## See also

- `docs/plans/2026-04-29-modularization-plan.md` §1.2, §1.3 (deviation
  rationale), §2.2, §3.
- `core/radio_protocol.py` — every Capability Protocol the runtime
  implements.
- `tests/test_radio*.py`, `tests/contracts/`, `tests/test_civ_rx_*.py`.
- `runtime/sync.py` — the synchronous facade.
