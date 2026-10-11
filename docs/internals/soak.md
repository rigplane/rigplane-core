# Operated soak (MOR-3236)

A nightly 1-hour soak operates the real web stack — `WebServer`,
`RigctldServer`, `IcomRadio` — against the IC-7610 simulator
(`rigplane_testkit.icom.simulator.MockIcomRadio`) from one process:
tune + readback every 2 s, mode changes every 10 s, a rigctld replay
(`F`/`f`/`M`/`m`/`t`) every 15 s, a forced radio disconnect/connect every
5 min, with a WebSocket client subscribed to state and audio throughout.
The driver lives in `scripts/soak/soak_driver.py` (usage and flag
reference in `scripts/soak/README.md`); the workflow is
`.github/workflows/soak-nightly.yml`, informational and not a required
check, with a `workflow_dispatch` duration input for smoke runs.

## Pass criteria and exit status

The report (`--report`, JSON) plus a one-line verdict; the process exits
non-zero on failure.

- RSS growth < 50 MB/h, least-squares slope over post-warmup samples
  with reconnect windows excluded (`scripts/soak/soak_driver.py`:
  `evaluate_report`). Below a 600 s fitted span the criterion reports
  `NOT_EVALUATED` rather than guessing from noise.
- FDs and asyncio tasks within ±5 of the pre-reconnect value after each
  forced reconnect (`scripts/soak/soak_driver.py`:
  `_stability_probe` medians the two sides).
- Zero unrecovered disconnects: every forced disconnect returns to
  CONNECTED within the configured budget
  (`scripts/soak/soak_driver.py`: `_do_connect_until_recovered`).

## Metrics sampler

`GET /api/v1/runtime` is fetched for every sample. This head's runtime
document has no `process` object, so the sampler measures the server PID
from the driver side (`/proc` on Linux, `ps` and `/dev/fd` elsewhere)
and is structured to prefer a runtime `process` object
(`rssBytes`/`fds`/`threads`) once one exists. asyncio task counts come
from the local loop in simulator mode only; against a running server
(`--url`) that criterion reports `NOT_EVALUATED`.

## Tests

`tests/test_soak_driver.py`: a 20 s smoke at 1 s cadences (report shape,
green verdict, driver-side sampling), criterion-math unit tests over
synthetic one-hour samples, and the ticket's mutation pin — a driver
that skips the reconnect half under a `FaultPlan` drop-100 %/3 s blackout
must report an unrecovered disconnect and a FAIL verdict.
