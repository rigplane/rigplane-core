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
- `rigplane_testkit.serial` — `CivSerialFake`, a stateful IC-7300 personality
  on an in-memory serial duplex (frequency, mode, filter, PTT, S-meter,
  profile-derived identity, USB CI-V echo). It mounts behind the production
  `SerialCivLink` through its `open_serial_connection` seam and behind
  `open_serial_port` through the `opener` seam, and scripts the serial-side
  faults the link was fixed against: delayed identity, identity only after a
  reconnect, wrong model id, a silent link (MOR-2861), and USB vanish /
  reappear (`ENODEV`, mapped to `serial_not_found`). The implementation lives
  in `rigplane_testkit/serial/civ_serial.py`.

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
- A per-port `FaultPlan` so fault schedules are declarative and composable.
- A recorder/replayer that captures real radio sessions and replays them
  through the same simulator surface.
- Extraction of the CI-V dispatcher into `civ_model.py`, deferred from the
  initial move because `_dispatch_civ` and the radio state it mutates are
  methods and attributes of the single `MockIcomRadio` class.
