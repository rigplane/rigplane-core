# `rigctld` layer

## Charter

Hamlib NET rigctld-compatible TCP server. Drop-in replacement for
`rigctld` that bridges the line-based TCP protocol to an `IcomRadio`
instance, so any Hamlib-aware client (WSJT-X, fldigi, JS8Call, etc.)
works without modification.

## Public API

`rigctld/__init__.py` exports:

- `RigctldServer` — async TCP server; `serve_forever()` enters the
  accept loop. Module-level import is wrapped in `try/except
  ImportError` so optional-dep absence (e.g. headless install without
  the rigctld extras) does not break `import icom_lan`.

Internal layout:

- `rigctld/server.py` — accept loop and connection lifecycle.
- `rigctld/handler.py` — per-connection rigctld command dispatcher.
- `rigctld/protocol.py` — line-based protocol parser/formatter.
- `rigctld/routing.py` — backend → rigctld feature mapping
  (`RigctldRoutable` integration; see #1322, #1324).
- `rigctld/poller.py`, `rigctld/state_cache.py` — telemetry plumbing.
- `rigctld/circuit_breaker.py`, `rigctld/contract.py`,
  `rigctld/audit.py`, `rigctld/utils.py` — operational helpers.

## Allowed dependencies

`core`, `commands`, `runtime` (plan §3 matrix row `rigctld`). No
`audio`, no `scope`, no `dsp`, no `profiles` — rigctld is a thin radio
adapter. `rigctld` ⊥ `web` is enforced by `independence-top` in
`.importlinter`.

`rigctld.routing` is named in `.importlinter`'s `ignore_imports` as a
`TYPE_CHECKING`-only target of `core.radio_protocol` and
`backends.yaesu_cat.radio` — both added by epic #1322's RigctldRoutable
introduction (#1324, #688bda03).

## Forbidden patterns

- `from icom_lan.web` — independence contract.
- `from icom_lan.audio` / `from icom_lan.scope` / `from icom_lan.dsp`
  / `from icom_lan.profiles` — outside the matrix.
- Backend-id discriminator branches. Use `isinstance(radio,
  RigctldRoutable)` (#1324). Legacy `set_vfo` overload fallbacks live
  in `rigctld/handler.py` strictly for third-party backends that don't
  implement the receiver-tier protocols (#1192).
- Telemetry or analytics. Open-core hard constraint.

## Common operations

- **Add a rigctld command** → extend the dispatcher in
  `rigctld/handler.py`; map to the relevant Capability Protocol on the
  `runtime.Radio` (do not branch on backend type); cover with
  `tests/test_rigctld_*.py`.
- **Change feature routing** → `rigctld/routing.py` consults the
  `RigctldRoutable` Protocol on the radio; backends opt in by
  implementing it.
- **Touch the protocol parser** → `rigctld/protocol.py`; the rigctld
  line format is stable and cross-tested with WSJT-X / fldigi clients.

## See also

- `docs/plans/2026-04-29-modularization-plan.md` §1.2, §3.
- `core/radio_protocol.py` — `RigctldRoutable` Protocol (#1322).
- `tests/test_rigctld_*.py` — handler + protocol coverage.
