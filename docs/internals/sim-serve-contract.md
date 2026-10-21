# Standalone simulator serve contract (MOR-3593)

`python -m rigplane_testkit.sim serve` runs a testkit radio simulator as its
own process with a local HTTP control endpoint. The launcher is dev tooling:
it ships in `rigplane_testkit`, not in the published `rigplane` wheel.

## Command line

`python -m rigplane_testkit.sim serve --radio ic7610 [flags]`

| Flag | Default | Meaning |
| --- | --- | --- |
| `--radio` | (required) | Model to simulate. Choices: `ic7610`, `ic7300`. |
| `--seed` | `0` | `FaultPlan(seed=...)` for the simulator; makes fault decisions reproducible. |
| `--bind` | `127.0.0.1` | Simulator bind address. `0.0.0.0` is an explicit opt-in to be reachable from another machine. For `ic7300`: the serial fake's TCP listener bind. |
| `--control` | `127.0.0.1:0` | Control endpoint `HOST:PORT`; IPv6 literals bracketed as `[host]:port` (a bare literal is a CLI error). Port 0 = ephemeral, reported on stdout. |
| `--trace` | off | Append the trace as JSON lines to this file. |
| `--keepalive-hold-s` | `85.0` | Dead-session hold; the recorded real IC-7610 holds 83.4–85.9 s. The simulator's constructor default (0.5 s) is for fast unit tests. |
| `--username` | constructor default | Login username the simulator accepts (otherwise `MockIcomRadio`'s fixed test value). |
| `--password` | constructor default | Login password the simulator accepts. Never printed, logged or returned. |

With `--radio ic7300` the IC-7610-only flags (`--keepalive-hold-s`,
`--username`, `--password`) are accepted and ignored.

## Startup line

Once the simulator and the control endpoint are listening, exactly one JSON
line is printed to stdout (nothing else ever goes there — logs go to stderr),
with this key set and order:

```json
{"sim":"rigplane-sim/1","radio":"IC-7610","fidelity":"A","evidence":"simulated",
 "transport":"lan","host":"127.0.0.1","control_port":12345,
 "username":"...","control":"http://127.0.0.1:12345","seed":0}
```

(`username` from `--username`, else the fixed test value; `control_port` is
the radio LAN port a client connects to; `control` is the HTTP endpoint
below.)

## Control endpoint

Local HTTP with JSON bodies (aiohttp; handlers run on the simulator's event
loop — one loop, no threads).

- `GET /state` → `{"frequency_hz":int,"mode":int,"ptt":bool,"session_held":bool,"login_count":int}`
- `GET /trace?since=N` → `{"events":[...],"next":int}` — records with `n >= N`.
- `POST /stop` → `{"ok":true}`, then a clean shutdown (same path as
  SIGINT/SIGTERM); a second `POST /stop` before it completes is harmless.
- `POST /event` with `{"event":"<name>", ...args}` → `{"ok":true,...}`, or
  HTTP 400 `{"ok":false,"error":"unsupported"|"invalid_args",...}` for an
  unknown event / bad arguments.

### Events (all take effect immediately, i.e. `at=0`)

| Event | Arguments | Effect |
| --- | --- | --- |
| `set_frequency` | `hz` (int, 1..9999999999) | `radio.set_frequency(hz)`. |
| `session_drop` | `notice` (bool, default true) | `radio.drop_session(at=0.0, notice=...)`. |
| `power_cycle` | `down_for_s` (finite number, default 5.0), `notice` (bool, default false) | `radio.reboot(at=0.0, down_for=..., notice=...)`. |
| `keepalive_silence` | `for_s` (finite number > 0) | `radio.keepalive_silence(at=0.0, for_seconds=...)`. |

Numeric arguments must be finite: `NaN`, `Infinity`, `-Infinity` (which
permissive JSON parsers accept) are rejected as `invalid_args`, as are
booleans and strings.

## Serial radios (`--radio ic7300`, MOR-3594)

The IC-7300 is served by the stateful serial CI-V fake (`rigplane_testkit/serial/civ_serial.py`) on
real endpoints: a TCP listener usable through pyserial's `socket://` URL — the same open the
production link performs — and, on macOS and Linux, a pseudo-terminal slave path a serial app opens
like a device node. On Windows there is no PTY serial simulation: `pty` is `null` and the TCP
endpoint keeps serving; use `socket://` and test whether the target app accepts it. During a
`usb_vanish` window the PTY's fake connection is dropped while the slave path stays open: an app can
still open it, and reads return no data until the reappear (bytes written inside the window are
dropped; the first bytes after it reopen the fake connection). On TCP, live clients are dropped
(EOF), and a client connecting while vanished is accepted and closed immediately — the open itself
does not fail.

Startup line: same envelope, but `device` (the `socket://` URL) and `pty` (the slave path, or
`null`) replace `host`/`control_port`/`username`.

`GET /state` → `{"frequency_hz":int,"mode":int,"ptt":bool,"s_meter":int,"open_count":int,"vanished":bool}`.

### Events (all take effect immediately)

| Event | Arguments | Effect |
| --- | --- | --- |
| `set_frequency` | `hz` (int, 1..9999999999) | Sets the fake's frequency. |
| `usb_vanish` | optional `for_s` (finite number > 0) | The device disappears: live TCP clients are dropped (EOF) and a client connecting while it is gone is accepted and closed at once until it reappears (the open does not fail; see the PTY wording above). With `for_s` the reappear is scheduled that far out (cancelled on shutdown) and traced as a `usb_reappear` record when it fires. |
| `usb_reappear` | none | The device opens again immediately. |
| `silence` | `for_s` (finite number > 0) | The port stays open and nothing answers for the window. |

These events validate through the same code path as the IC-7610 ones: a body that cannot be parsed
or sanitised, or names no known event, never reaches a handler, and each handler validates its
arguments before changing anything (400 `invalid_args` on rejection), so a rejected body changes
nothing. IC-7610-only events answer `unsupported`.

## Trace

Every `POST /event` (accepted or rejected) and process start/stop appends
`{"n":int,"t":seconds-since-start,"kind":"start"|"event"|"stop","name":str,
"args":{...},"result":{...}}`. Records are kept in memory (bounded to 10000;
`n` keeps counting past drops) and, with `--trace FILE`, appended as JSON
lines (`sort_keys`, `separators=(",",":")`), flushed per line — including
the final `stop` record written during shutdown.

`t` (real elapsed seconds) is the only time field; records appended by a
restore timer (`usb_reappear` after `usb_vanish` with `for_s`) appear at
their deadline unless an explicit `usb_reappear` cancelled the timer
first, so the trace is a log of what happened, not a replay script. The
server itself adds no ephemeral ports to trace
records (the start record carries the `--control` hint, port 0 included);
extra keys a client sends in an event body are recorded in `args` with
non-finite floats (from permissively parsed `NaN`/`Infinity` literals)
replaced by their string spellings — the snapshot, the `--trace` file and
every HTTP response stay strict JSON. Sanitising recurses per nesting
level: past the Python recursion limit the event is rejected with 400
`invalid_args` (traced with empty `args`) before any handler runs.

## Evidence labelling

The startup line carries `"evidence":"simulated"`. `fidelity` is the tier of
the model's evidence: **A** — replay against a real recording of this exact
model (the IC-7610 simulator has recorded conformance);
**B** — manual oracles only; **C** — profile declarations only.

Planned: `lan_drop` (exact N-second UDP blackout), tracked in MOR-3593.
