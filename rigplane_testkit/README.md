# rigplane_testkit

Test utilities for rigplane-core and downstream projects: real-socket radio
simulators that speak the actual wire protocols, so tests exercise the same
UDP code paths as production.

## Contents

- `rigplane_testkit.icom` — `MockIcomRadio`, an asyncio UDP simulator of an
  Icom IC-7610 LAN interface: control-port discovery/login/token/ping, a CI-V
  command port, single-owner session holding with keepalive renewal, and
  fault injection (drop, reorder, stall, delay). The implementation lives in
  `rigplane_testkit/icom/simulator.py` and was moved unchanged from
  `tests/mock_server.py`.
- `rigplane_testkit.icom.faults` — `FaultPlan` / `FaultRule`: a declarative,
  seeded (deterministic) fault plan with one rule per port (`control`, `civ`)
  and direction (`to_client`, `from_client`). Each rule can drop (probability
  or every-Nth), duplicate (with inter-copy delay), delay (fixed + jitter),
  reorder (window), corrupt (bit-flip N bytes) and truncate (to K bytes), and
  can be bounded by packet count or duration (e.g. "lose the next 5 control
  packets"). Reorder buffers always flush — on a full window, on a
  `reorder_flush_s` deadline (driven by the simulator when the stream goes
  idle), and when a bound deactivates the rule — so no packet is ever held
  forever. Each stream draws from its own RNG derived from the plan seed
  (`f"{seed}:{port}:{direction}"`), so one stream's traffic cannot change
  another stream's decisions. The plan sits beside the legacy knobs
  (`drop_rate`, `reorder_window`, `response_delay`, `stall_for`), which are
  unchanged.
  `MockIcomRadio` also offers scheduled events: `reboot(at, down_for,
  busy_for)` — stop answering, forget the session, rotate the token, restart
  CI-V sequence numbering, fence the stale client off the CI-V port until it
  completes a fresh OpenClose(open), answer busy, then accept a fresh
  login — and
  `keepalive_silence(at, for_seconds)` — pings are received but not answered.
  Wire-level examples live in `tests/testkit/test_simulator_faults.py`.

## How tests import it

Existing tests keep their imports unchanged: `tests/mock_server.py` is a thin
re-export shim over `rigplane_testkit.icom.simulator`. New code can import
the package directly:

```python
from rigplane_testkit.icom import MockIcomRadio
```

The package is importable under pytest because `pyproject.toml` sets
`[tool.pytest.ini_options] pythonpath = ["."]`.

## Distribution

`rigplane_testkit` is **not** part of the published `rigplane` wheel:
`[tool.hatch.build.targets.wheel]` packages only `src/rigplane` and
the legacy compatibility shim. It exists for development and testing only.

## Planned next

- Audio (RTP) port simulation alongside control and CI-V ports.
- A recorder/replayer that captures real radio sessions and replays them
  through the same simulator surface.
- Extraction of the CI-V dispatcher into `civ_model.py`, deferred from the
  initial move because `_dispatch_civ` and the radio state it mutates are
  methods and attributes of the single `MockIcomRadio` class.
