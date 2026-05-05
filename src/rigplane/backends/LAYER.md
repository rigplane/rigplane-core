# `backends` layer

## Charter

Factory for assembling concrete radio implementations from typed configs
plus per-radio adapters and the multi-protocol discovery utility.
`backends.factory.create_radio(BackendConfig)` is the canonical
entry point — it inspects the config's discriminator, builds the right
transport stack, instantiates the correct radio class, and returns
something that satisfies the `Radio` Protocol from `core`.

## Public API

`backends/__init__.py` exports the minimal assembly surface:

- `BackendConfig` — common base for typed configs.
- `LanBackendConfig`, `SerialBackendConfig`, `YaesuCatBackendConfig` —
  the three currently supported backend kinds.
- `create_radio(config)` — the factory, returns a Protocol-typed radio.

Per-backend packages (`backends/icom7610/`, `backends/ic7300/`,
`backends/ic9700/`, `backends/ic705/`, `backends/yaesu_cat/`) hold
the concrete `Radio` adapters; `backends.discovery.discover()` walks
the LAN for IC-7610-style discoverable radios.

## Allowed dependencies

`core`, `commands`, `profiles`, `audio`, `runtime` (plan §3 matrix row
`backends`). Backends compose `runtime.IcomRadio` (or, for Yaesu CAT,
their own Protocol-implementing class) and feed it the right transport
+ commander stack.

`backends.yaesu_cat.radio` carries one named exception in
`.importlinter`'s `ignore_imports` (`→ rigplane.rigctld.routing`,
`TYPE_CHECKING` only, added by epic #1322's RigctldRoutable work).

## Forbidden patterns

- `from rigplane.web` / `from rigplane.rigctld` / `from rigplane.cli`.
  Backends are below those layers; the inverse is the legal flow.
- Direct instantiation of backend classes from upper layers. Always go
  through `create_radio` so the discriminator + capability assembly is
  consistent.
- Hardcoding rig facts that already live in `profiles`. New rig
  parameters → TOML, not Python.

## Common operations

- **Add a new radio backend** → create `backends/<rig>/` package with
  `radio.py` (the adapter) plus any per-rig poller/commander; register
  the assembly path inside `backends/factory.py`; conform to the
  relevant Capability Protocols (`AudioCapable`, `ScopeCapable`,
  `StatePollable`, `RigctldRoutable`, `UsbAudioCapable` …) so the
  upper layers detect features via `isinstance` rather than backend-id
  branching (epic #1322; see #1323-#1326 for the migration pattern).
  Zero changes required in `web/` / `rigctld/` / `cli/` if the
  Capability Protocols are honoured.
- **Add a `BackendConfig` field** → extend the dataclass in
  `backends/config.py`; route through `create_radio`; cover with
  `tests/test_backends_factory*.py` and the relevant CLI factory tests.
- **Discovery transport change** → `backends/discovery.py`; update the
  CLI's `discover` subcommand (LAN-only) and `tests/test_discovery*.py`.

## See also

- `docs/plans/2026-04-29-modularization-plan.md` §1.2, §1.3 (the
  deviation rationale: why `backends` is its own layer above
  `runtime`), §2.2, §3.
- `core/radio_protocol.py` — Capability Protocols backends implement.
- `tests/test_backends_*.py`, `tests/test_factory*.py`,
  `tests/test_yaesu_*.py`.
