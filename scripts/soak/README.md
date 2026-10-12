# Soak driver

An operated soak for the rigplane web stack against the IC-7610 simulator
(`rigplane_testkit.icom.simulator.MockIcomRadio`). MOR-3236.

The driver starts the full in-process stack — simulator, `IcomRadio`,
`WebServer`, `RigctldServer` — then operates it the way an operator would
for a configurable duration: tune + readback, mode changes, a rigctld
replay (`F`/`f`/`M`/`m`/`t`), forced radio disconnect/connect cycles, and
a WebSocket client subscribed to state and audio the whole time.

## Running

From the repository root (the simulator stack imports the top-level
`rigplane_testkit` package, which is only importable from the root):

```bash
uv run python -m scripts.soak.soak_driver --duration 3600
```

Exit status is 0 on a PASS verdict, 1 on FAIL. The JSON report goes to
`--report` (default `soak-report.json`); one verdict line is printed to
stdout. The driver logs through the logger named `soak` at WARNING; set
`logging.basicConfig(level=logging.INFO)` in a manual run for the INFO
cycle milestones.

In the simulator soak, cadences are constants in `scripts/soak/soak_driver.py`,
not CLI flags:
`TUNE_INTERVAL_S` (2 s), `MODE_INTERVAL_S` (10 s), `RIGCTLD_INTERVAL_S`
(15 s), `RECONNECT_INTERVAL_S` (300 s), `METRICS_INTERVAL_S` (30 s),
`RECONNECT_BUDGET_S` (30 s), `FAULT_DROP` (0), `FAULT_DURATION_S` (3 s),
`RSS_WARMUP_S` (60 s). Around each forced reconnect `FAULT_DROP` /
`FAULT_DURATION_S` install a `rigplane_testkit.icom.faults.FaultPlan`
blackout (drop probability and window); by default no fault is injected.

## Pass criteria

- **RSS growth** < 50 MiB/h: least-squares slope over post-warmup metric
  samples, excluding each reconnect window (a reconnect's allocation
  burst is a bounded transient, not steady-state growth). Below a 600 s
  fitted span the criterion reports `NOT_EVALUATED` — MiB-scale RSS
  wobble over seconds cannot bound an hourly rate.
- **FDs and asyncio tasks** within ±5 of the pre-reconnect value after
  each forced reconnect (median of 3 quick probes before the disconnect
  and after recovery + settle, so a request in flight does not read as a
  leak). Known gap: a leak of at most 4 per reconnect passes every
  per-reconnect check while still accumulating — about 12 reconnects in
  the 1-hour nightly can add up to ~48 FDs/tasks without failing this
  criterion. Only the RSS slope bounds the total.
- **Zero unrecovered disconnects**: every forced disconnect is back to
  CONNECTED within `RECONNECT_BUDGET_S` seconds. This measures the whole
  stack returning to CONNECTED — the driver's connect POST or the
  stack's own recovery, whichever comes first: after an operator
  disconnect the radio's CI-V watchdog soft-reconnects on its own about
  8 s later (measured 7.6 s on the test host; `rigplane.runtime.radio`
  logs `force_cleanup_civ` then `soft_reconnect: control transport gone,
  doing full connect`), so a budget above that time passes even with a
  dead connect endpoint. A disconnect that never became observable is
  recorded (`disconnectObserved` false) and counts as unrecovered.

`NOT_EVALUATED` never fails the verdict; only a measured `FAIL` does.

## Process metrics sampling

Each field is filled independently per sample: a value from the runtime
document's `process` object wins when present (`rssBytes`, `openFds`,
`asyncioTasks` — the names of PR #4044; `rssBytes` is null only where
neither `/proc/self/statm` nor macOS libproc can report it), and any
field the runtime did not report is measured driver-side for this
process (the stack runs in-process): RSS via `/proc/<pid>/statm` or
`ps -o rss=`, FDs via `/proc/<pid>/fd` or `/dev/fd`, asyncio tasks as
every task on the loop not named with the `soak-` prefix. The
connection column comes
from the state document's live `connection.rigConnected`
(`server.py: WebServer._snapshot_for_delivery` reconciles it per
request), not from the runtime payload's `connection` block, which
repeats the last reconnect-status callback payload until the next one
arrives and can be stale in both directions.

## Observe mode (GET-only, an already running server)

`scripts/soak/observe.py` watches a `rigplane web` that is already running
instead of starting the simulator stack. It sends `GET` to `/api/v1/runtime`
and `/readyz` every `--interval-s` (default `METRICS_INTERVAL_S`) and nothing
else; `GetOnlyClient` refuses any other path, and neither endpoint takes a
credential (`web/api_contract.py: STABLE_HTTP_ENDPOINTS`):

```bash
uv run python -m scripts.soak.soak_driver \
    --observe-url http://127.0.0.1:8098 --duration 28800 --report soak-observe.json
```

The report keeps the simulator report's criteria keys. `rssGrowth` is the
same fit with no reconnect windows excluded. `fdsStability` and
`tasksStability` compare within ±5 across each increase of
`process.reconnectsTotal` and between the first and last up sample after
warm-up. A field that is null throughout leaves its criterion
`NOT_EVALUATED`. `recovery` is judged on the runs of down samples, all
listed in `episodes`; a sample is down when the server did not answer,
`/readyz` says not ready, `station.health.radioLink` is not `connected`, or
neither endpoint reports the link. `--recovery-budget-s` (default 180) bounds an episode;
start and end are sample times, accurate to one interval. PASS needs every
evaluated criterion to pass and at least one evaluated
(`criteriaEvaluated`); SIGINT / SIGTERM write the report and fail it. A
server restart is not separated out: the pid in each sample shows it.

## Repository note

`scripts/*` is ignore-listed in `.gitignore` with per-script whitelist
exceptions; this directory's files are tracked (staged with
`git add -f`). A new file here needs the same treatment or a
`.gitignore` whitelist line.

## Tests and nightly run

`tests/test_soak_driver.py` runs a 20 s smoke (1 s cadences), criterion
math on synthetic samples, sampler per-field merge unit tests, and the
mutation pins: suppressed connect (5 s budget, no injected blackout)
must fail, a disconnect that never happened must not count as
recovered, and a reconnect cycle outliving the run must not wedge it.
A missing `ps` sample must leave the driver-side RSS field None rather
than raise, and the report must be written even when a stack-teardown
`stop()` raises. `.github/workflows/soak-nightly.yml`
runs the 1-hour soak nightly on the self-hosted Linux runner and uploads
the report; see `docs/internals/soak.md`.
