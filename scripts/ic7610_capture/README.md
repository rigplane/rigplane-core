# IC-7610 capture probe driver

Read-only CI-V probe driver for the IC-7610 bench capture session
(Linear MOR-3307, IC-7610 fidelity program). The LAN/USB link is recorded
through `rigplane_testkit/icom/recorder.py: IcomSessionRecorder`; this
driver connects the **production** client through that recorded link and
asks the radio every read-only question the profile declares, once and
paced, so the recording holds the radio's answer class per command —
`data` / `ack` / `nak` / `silence`, with latency. That table is what the
simulator must reproduce (MOR-3297).

## Layout

- `probe_list.py` — pure, importable, tested. `build_probe_list(profile)`
  derives every probe from `rigs/ic7610.toml` through the profile's
  `CommandMap` and `[cmd29]` routes, plus the cmd29-wrapped frequency
  read for both receivers and four deliberately unknown probes (kind
  `"unknown"`) that classify NAK-versus-silence. Sending is gated by an
  allow-list: `build_allowed_bodies(profile)` collects the probe bodies
  with the cmd29 wrapper removed, and `is_forbidden(frame_body, allowed)`
  refuses anything whose unwrapped body is not byte-for-byte in that
  set — every SET carries a value, so no SET body equals a declared GET
  body — or whose command byte is a refused family outright (power
  0x18, PTT/tuner 0x1C, memory write/clear 0x09/0x0B, CW 0x17, scan
  0x0E, set freq/mode 0x05/0x06, send freq/mode 0x00/0x01, MEM select
  0x08), so a mistake in the allow-list cannot key anything. Command
  0x07 is excluded from the probe list and the allow-list because its
  bare and sub-command forms change VFO/receiver selection.
- `probe.py` — the driver. `run_probes(radio, probes, allowed, pace,
  timeout)` is the tested coroutine; the CLI only parses arguments.

## Usage

```bash
# LAN, through the recorder proxy:
python scripts/ic7610_capture/probe.py --backend lan \
  --host <host> --control-port <port> --user <user> \
  --pass-file <file> --model IC-7610 --radio-addr 0x98 \
  --out answers.json [--pace 0.3] [--timeout 1.5] [--limit N]

# USB over a serial bridge:
python scripts/ic7610_capture/probe.py --backend serial \
  --serial-port socket://<host>:<port> --baud 115200 \
  --model IC-7610 --radio-addr 0x98 --out answers.json
```

The password is read from `--pass-file`; the host, user and password are
never printed or logged. Exit code 0 on completion, 3 when connect fails.

## Output

`answers.json` holds `radio`, `backend`, `started_utc`, one entry per
probe (`name`, `frame_hex`, `class`, `latency_ms`, `reply_hex` or
`null`), and a per-class `summary`. One line per probe also goes to
stdout.

The probe loop drives `CivRuntime.execute_civ_transaction`
(`src/rigplane/runtime/_civ_rx.py`) rather than `send_civ_raw`: the
transaction registers a NAK-only waiter, so a CI-V refusal (command
0xFA) settles as a frame (`nak`) instead of running out the answer
window, which keeps `nak` and `silence` distinguishable. A bare ACK
(0xFB) settles no waiter — `core/civ.py: CivRequestTracker.resolve`
skips NAK-only waiters for ACK events — so it is detected through the
radio's public `civ_stats()` counter `ack_orphans` rising across the
answer window and classified `ack`, without reply bytes.

## Tests

`tests/testkit/test_capture_probe.py` pins the exact profile coverage
(the eligible `get_*` set computed straight from the TOML), the
allow-list refusal rows, and the driver's classification against
`rigplane_testkit.icom.MockIcomRadio` over loopback UDP, including a GET
answered with a bare ACK.
