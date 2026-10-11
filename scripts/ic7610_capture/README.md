# IC-7610 capture probe driver

Read-only CI-V probe driver for the IC-7610 bench capture session
(Linear MOR-3307, IC-7610 fidelity program). The LAN/USB link is recorded
through `rigplane_testkit/icom/recorder.py: IcomSessionRecorder`; this
driver connects the **production** client through that recorded link and
asks the radio every read-only question the profile declares, once and
paced, so the recording holds the radio's answer class per command —
`data` / `nak` / `silence`, with latency. That table is what the
simulator must reproduce (MOR-3297).

## Layout

- `probe_list.py` — pure, importable, tested. `build_probe_list(profile)`
  derives every probe from `rigs/ic7610.toml` through the profile's
  `CommandMap` and `[cmd29]` routes; `is_forbidden(frame_body)` is the
  byte-level refusal line (power 0x18, PTT/tuner 0x1C, memory 0x09/0x0B/
  0x1A 00, CW 0x17, scan 0x0E). Only `get_*` commands are taken, and the
  deliberately unknown probes (`kind: "unknown"`) exist to classify
  NAK-versus-silence.
- `probe.py` — the driver. `run_probes(radio, probes, pace, timeout)` is
  the tested coroutine; the CLI only parses arguments. Each frame is
  re-checked with `is_forbidden` before it is sent.

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
transaction registers a NAK waiter, so a CI-V refusal (command 0xFA)
surfaces as a frame instead of running out the answer window as a
timeout, which keeps `nak` and `silence` distinguishable.

## Tests

`tests/testkit/test_capture_probe.py` pins the profile coverage, the
exclusion rules, and the driver's classification against
`rigplane_testkit.icom.MockIcomRadio` over loopback UDP.
