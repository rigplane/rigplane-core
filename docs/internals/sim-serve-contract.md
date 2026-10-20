# Standalone simulator serve contract (MOR-3593)

`python -m rigplane_testkit.sim serve` runs a testkit radio simulator as its
own process with a local HTTP control endpoint. This is the interface the Pro
dogfood scorecard binds to; it will be absorbed into the MOR-3592 simulator
ADR. The launcher is dev tooling: it ships in `rigplane_testkit`, not in the
published `rigplane` wheel.

## Command line

```
python -m rigplane_testkit.sim serve --radio ic7610 [--seed N] \
    [--bind HOST] [--control HOST:PORT] [--trace FILE] [--keepalive-hold-s S] \
    [--username NAME] [--password SECRET]
```

| Flag | Default | Meaning |
| --- | --- | --- |
| `--radio` | (required) | Model to simulate. Choices: `ic7610` (the argparse choices list is the extension point; FTX-1 CAT and IC-7300 serial are planned). |
| `--seed` | `0` | `FaultPlan(seed=...)` for the simulator; makes fault decisions reproducible. |
| `--bind` | `127.0.0.1` | Simulator bind address. `0.0.0.0` is an explicit opt-in to be reachable from another machine. |
| `--control` | `127.0.0.1:0` | Control endpoint address; port 0 = ephemeral, reported on stdout. |
| `--trace` | off | Append the trace as JSON lines to this file. |
| `--keepalive-hold-s` | `85.0` | Dead-session hold; the recorded real IC-7610 holds 83.4–85.9 s. The simulator's constructor default (0.5 s) is for fast unit tests. |
| `--username` | constructor default | Login username the simulator accepts (otherwise `MockIcomRadio`'s fixed test value). |
| `--password` | constructor default | Login password the simulator accepts. Never printed, logged or returned. |

## Startup line

Once the simulator and the control endpoint are listening, exactly one JSON
line is printed to stdout (flushed; nothing else ever goes to stdout — logs go
to stderr), with this key set and order:

```json
{"sim":"rigplane-sim/1","radio":"IC-7610","fidelity":"A","evidence":"simulated",
 "transport":"lan","host":"127.0.0.1","control_port":12345,
 "username":"...","control":"http://127.0.0.1:12345","seed":0}
```

`username` is the login username the simulator accepts (from `--username`,
else the constructor's fixed test value). `control_port` is the simulator's
LAN control port a radio client connects to; `control` is the local HTTP
endpoint described below.

Credentials: the simulator accepts the credentials passed with
`--username`/`--password` (defaults are the constructor's fixed test values;
see `MockIcomRadio`). The password is never printed, logged or returned; a
caller that needs it passes it or uses the documented default.

## Control endpoint

Local HTTP with JSON bodies (aiohttp; handlers run on the simulator's event
loop — one loop, no threads).

- `GET /state` → `{"frequency_hz":int,"mode":int,"ptt":bool,"session_held":bool,"login_count":int}`
- `GET /trace?since=N` → `{"events":[...],"next":int}` — records with `n >= N`.
- `POST /stop` → `{"ok":true}`, then the process shuts down cleanly (same
  path as SIGINT/SIGTERM): control site stopped, simulator stopped, exit 0.
- `POST /event` with `{"event":"<name>", ...args}` → `{"ok":true,...}` or
  HTTP 400 with `{"ok":false,"error":"unsupported","event":...}` for an
  unknown event / `{"ok":false,"error":"invalid_args",...}` for bad arguments.

### Events (all take effect immediately, i.e. `at=0`)

| Event | Arguments | Effect |
| --- | --- | --- |
| `set_frequency` | `hz` (int, 1..9999999999) | `radio.set_frequency(hz)`. |
| `lan_drop` | `for_s` (number > 0) | Network blackout: an exact `for_s`-second window over the control and CI-V UDP streams in both directions. The four fault-plan rules (`control_to_client`, `control_from_client`, `civ_to_client`, `civ_from_client`) are saved and replaced with `FaultRule(drop_probability=1.0)` (the plan's seed is kept); a loop timer restores the saved rules exactly `for_s` seconds after the event. The audio port is silenced for the same window when audio is enabled. |
| `session_drop` | `notice` (bool, default true) | `radio.drop_session(at=0.0, notice=...)`. |
| `power_cycle` | `down_for_s` (number, default 5.0), `notice` (bool, default false) | `radio.reboot(at=0.0, down_for=..., notice=...)`. |
| `keepalive_silence` | `for_s` (number > 0) | `radio.keepalive_silence(at=0.0, for_seconds=...)`. |

A second `lan_drop` while a window is active cancels the pending restore and
schedules a new one `for_s` seconds from the later event; the rules restored
at that later deadline are still the ORIGINAL (pre-blackout) ones, so the
blackout is one continuous window, never staggered.

When a window closes, the trace gains
`{"kind":"event","name":"lan_drop_end","args":{},"result":{"ok":true}}`; the
scorecard computes recovery time from its `t`. That record lands at a real
deadline, so a trace that spans an expired `lan_drop` window is deterministic
only up to where that record falls.

## Trace

Every `POST /event` (accepted or rejected) and process start/stop appends
`{"n":int,"t":seconds-since-start,"kind":"start"|"event"|"stop","name":str,
"args":{...},"result":{...}}`. Records are kept in memory (bounded to 10000;
`n` keeps counting past drops) and, with `--trace FILE`, appended as JSON
lines (`sort_keys`, `separators=(",",":")`), flushed per line.

Determinism rule: the same seed plus the same event sequence produces an
identical trace once `t` is removed. `t` is real elapsed time — the scorecard
needs it to compute recovery times; it is the only non-deterministic field,
and trace records never contain ephemeral ports.

## Evidence labelling

The startup line carries `"evidence":"simulated"`. `fidelity` is the tier of
the model's evidence:

- **A** — replay against a real recording of this exact model (the IC-7610
  LAN simulator has recorded conformance in
  `tests/testkit/test_ic7610_conformance.py`);
- **B** — manual oracles only;
- **C** — profile declarations only.

## Planned

FTX-1 (CAT, `socket://` URL) and IC-7300 (serial) join the `--radio` choices
when their simulators land; everything else in this contract is
model-independent.
