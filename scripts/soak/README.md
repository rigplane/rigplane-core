# Soak driver

An operated soak for the rigplane web stack against the IC-7610 simulator
(`rigplane_testkit.icom.simulator.MockIcomRadio`). MOR-3236.

The driver starts the full in-process stack — simulator, `IcomRadio`,
`WebServer`, `RigctldServer` — then operates it the way an operator would
for a configurable duration: tune + readback, mode changes, a rigctld
replay (`F`/`f`/`M`/`m`/`t`), forced radio disconnect/connect cycles, and
a WebSocket client subscribed to state and audio the whole time. It can
also operate an already-running server with `--url` (pass
`--rigctld-url` to keep the replay running).

## Running

From the repository root (the simulator stack imports the top-level
`rigplane_testkit` package, which is only importable from the root):

```bash
uv run python -m scripts.soak.soak_driver --simulator --duration 3600
uv run python -m scripts.soak.soak_driver --url http://127.0.0.1:8080 \
    --rigctld-url 127.0.0.1:4532 --duration 3600
```

Exit status is 0 on a PASS verdict, 1 on FAIL. The JSON report goes to
`--report` (default `soak-report.json`); one verdict line is printed to
stdout. Stack logging is at WARNING; set `SOAK`'s logger via standard
`logging` configuration if you need the INFO cycle milestones in a manual
run (`logging.basicConfig(level=logging.INFO)` before importing).

Cadences are flags: `--tune-interval` (2 s), `--mode-interval` (10 s),
`--rigctld-interval` (15 s), `--reconnect-interval` (300 s),
`--metrics-interval` (30 s), `--reconnect-budget` (30 s). Around each
forced reconnect `--fault-drop` / `--fault-duration` install a
`rigplane_testkit.icom.faults.FaultPlan` blackout (drop probability and
window); by default no fault is injected.

## Pass criteria

- **RSS growth** < 50 MB/h: least-squares slope over post-warmup metric
  samples, excluding each reconnect window (a reconnect's allocation
  burst is a bounded transient, not steady-state growth). Below a 600 s
  fitted span the criterion reports `NOT_EVALUATED` — MB-scale RSS
  wobble over seconds cannot bound an hourly rate.
- **FDs and asyncio tasks** within ±5 of the pre-reconnect value after
  each forced reconnect (median of 3 quick probes before the disconnect
  and after recovery + settle, so a request in flight does not read as a
  leak).
- **Zero unrecovered disconnects**: every forced disconnect is back to
  CONNECTED within `--reconnect-budget` seconds.

`NOT_EVALUATED` never fails the verdict; only a measured `FAIL` does.

## Process metrics sampling

Each sample first fetches `GET /api/v1/runtime` (also the connection
state source). When that document carries a `process` object with any of
`rssBytes` / `fds` / `threads`, the sampler uses it (sample source
`runtime.process`). At the time this driver landed the runtime document
had no `process` object, so the fallback is driver-side sampling of the
server PID: `/proc/<pid>/statm` + `/proc/<pid>/fd` +
`/proc/<pid>/status` on Linux, `ps -o rss=` and `ps -M` plus `/dev/fd`
elsewhere (sample source `driver-side`). When the endpoint grows the
`process` object, the sampler switches to it with no driver change —
adjust the key names in `ProcessMetricsSampler.sample` if they differ.
asyncio task counts are only observable with the stack in this process
(simulator mode): every task the driver creates is named with the
`soak-` prefix and the count is everything else on the loop. In `--url`
mode the task criterion reports `NOT_EVALUATED`.

## Repository note

`scripts/*` is ignore-listed in `.gitignore` with per-script whitelist
exceptions; this directory's files are tracked (staged with
`git add -f`). A new file here needs the same treatment or a
`.gitignore` whitelist line.

## Tests and nightly run

`tests/test_soak_driver.py` runs a 20 s smoke (1 s cadences) plus
criterion-math unit tests and the skip-reconnects mutation pin.
`.github/workflows/soak-nightly.yml` runs the 1-hour soak nightly on the
self-hosted Linux runner and uploads the report; see
`docs/internals/soak.md`.
