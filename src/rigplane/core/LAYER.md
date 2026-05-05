# `core` layer

## Charter

Foundational layer with no internal dependencies. Holds the wire-protocol
primitives (CI-V framing, transport, auth), base types/exceptions, the
optional-dependency probes, and the abstract Radio Protocol surface that
downstream consumers (incl. `icom-lan-pro`) treat as the stable contract.
Anyone may import from `core`; `core` may import from no other layer.

## Public API

`core/__init__.py` keeps `__all__` empty by design — layers reach into
canonical sub-module paths and the top-level `icom_lan` lazy-loader
(`icom_lan/__init__.py`) re-exports the Tier 1 / Tier 2 surface. Notable
canonical entries:

- `core.types` — `Mode`, `AudioCodec`, `BreakInMode`, `RadioState`,
  `VfoSlotState`, BCD helpers (`bcd_decode`, …).
- `core.radio_protocol` — the `Radio` Protocol plus every `*Capable` /
  `StatePollable` / `StatePoller` / `RigctldRoutable` Protocol used for
  capability detection (see `docs/api/public-api-surface.md`).
- `core.exceptions` — every public exception subclass.
- `core.transport`, `core.auth`, `core.civ`, `core.protocol` — UDP
  transport, authenticator, frame parser, packet-level types.
- `core.capabilities` — Tier-1 `CAP_*` capability constants.
- `core._optional_deps` — `_require_numpy`/`_require_sounddevice`/etc.
  (see #1274).

## Allowed dependencies

None. Plan §3 matrix row `core`: every column is `—`. The single contract
test `tests/contracts/test_lazy_imports.py` plus `import-linter`'s layered
contract police this.

`radio_protocol` carries four named exceptions in `.importlinter`'s
`ignore_imports`: `→ icom_lan.audio_bus` and `→ icom_lan.scope` (both
`TYPE_CHECKING`-only string annotations — see `radio_protocol.py:58`),
plus `→ icom_lan.runtime._poller_types` and `→ icom_lan.rigctld.routing`
(also `TYPE_CHECKING`, added by epic #1322).

## Forbidden patterns

- Any `from icom_lan.<other layer>` import outside a `TYPE_CHECKING`
  block. New runtime imports here would create cycles instantly.
- Side-effecting module-level code (no logger config, no env reads, no
  network/IO at import time).
- Stateful singletons. Types are dataclasses; protocols are pure
  Protocols; helpers are stateless functions.

## Common operations

- **Add a capability protocol** → extend `core/radio_protocol.py`, list
  the symbol in `tests/test_public_api_surface.py`, add to
  `tests/contracts/test_lazy_imports.py` if Tier 1, register in
  `icom_lan/__init__.py` `_LAZY_MAP` if Tier 2 (see #1322 commits).
- **Add an exception** → add to `core/exceptions.py`, ensure it inherits
  from the existing `IcomLanError` hierarchy, expose via the top-level
  shim if it's part of the public Tier 1 surface.
- **Add a transport primitive** → add to `core/transport.py` or a new
  sub-module; verify zero `from icom_lan` imports remain.
- **Touch `core/_optional_deps.py`** → run `uv run pytest
  tests/test_optional_deps.py`; the helper API is shared by every layer
  and breakage cascades.

## See also

- `docs/plans/2026-04-29-modularization-plan.md` §1.2 (charter), §2.2
  (public API), §3 (matrix).
- `docs/api/public-api-surface.md` — Tier 1 / Tier 2 / Tier 3 contract.
- `tests/contracts/test_lazy_imports.py` — public-name regression gate.
- `.importlinter` — layered contract + the four named exceptions.
