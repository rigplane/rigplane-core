# IC-7300 bench capture (receive side only)

Tooling for the read-only USB CI-V capture of the bench IC-7300 (Linear
MOR-3595, simulator epic MOR-3496). The IC-7610 capture
(`scripts/ic7610_capture/`) is the model; its probe list and driver are reused
unchanged for this radio (`--model IC-7300 --radio-addr 0x94`).

## Pieces

- `bridge.py` — `SerialCivRecorder` (TCP↔serial bridge, JSONL) with a client
  frame guard (`CivFrameGuard` on the recorder's `client_filter`). A client pointed at `socket://HOST:PORT` reaches the radio only
  with frames the guard accepts: every `get_*` body of `rigs/ic7300.toml`
  (minus the refused families PTT/tuner/power/set-frequency/VFO/memory
  write/CW/scan, same allow-list as the IC-7610 probe) and the four scope
  switches `27 10 00/01`, `27 11 00/01`. Anything else is dropped before the
  serial port and recorded as `blocked`; the exit summary counts blocked
  frames per command byte.
- `scope_session.py` — production client through the bridge: read the scope
  panel/wave-output state (reads), enable the scope, count completed scope
  frames for N seconds, restore the exact state read, read it back. Output is
  the per-radio scope-rate baseline (frames/s, interval median/p95/max).
- `../ic7610_capture/probe.py` — the paced read-only probe of every profile
  GET plus four unknown commands (answer class per command and latency).
- `python -m rigplane_testkit.serial.fixtures extract` — trims a bridge
  recording into `civ_pairs.jsonl` rows (first occurrence per request, echo,
  closing reply; unsolicited frames summarised in `unsolicited.jsonl`).

## Runbook (Mac mini, one bridge per window)

```bash
# 0. bench lock, TX guard, state snapshot (frequency, mode, power) before
scripts/bench/bench-lock.sh acquire ic7300 "$TOKEN"
# 1. bridge for the window (device name from `ls /dev/cu.*`)
python scripts/ic7300_capture/bridge.py --device DEV --out probe.rec.jsonl
# 2. in another shell, drive it through the bridge
python scripts/ic7610_capture/probe.py --backend serial \
  --serial-port socket://127.0.0.1:PORT --baud 115200 \
  --model IC-7300 --radio-addr 0x94 --out probe_answers.json
# 3. stop the bridge (SIGINT), repeat for readonly_session / scope / ...
# 4. extract + redact-check
python -m rigplane_testkit.serial.fixtures extract --in probe.rec.jsonl \
  --window probe --radio-addr 0x94 --out-dir tests/fixtures/ic7300
python -m rigplane_testkit.redaction_gate --deny-file DENY tests/fixtures/ic7300/*.json*
# 5. snapshot after, compare with before, release the bench lock
```

Raw recordings stay outside the repo.

## HF air set (RX audio + scope + ground truth)

`air_capture.py --clip ft8|cw|ssb --device DEV --out-dir PRIVATE_DIR` tunes the
radio, records the radio's USB audio in its native format (device default
rate/channels, s16) to a WAV, runs the scope, records the raw CI-V (scope
frames included) through the guarded bridge, then restores and reads back the
frequency, mode, data mode, filter and scope state. Writes allowed through the
bridge for this tool: set frequency/mode/data mode, scope span/centre mode,
scope switches; each one is listed in the clip manifest. FT8 clips start on a
UTC 15 s slot. `ft8_truth.py MANIFEST` slices the WAV into slots at 12 kHz and
decodes them with jt9 (the decode list holds third-party callsigns and stays
private). Outputs go to a private directory (NAS), referenced in the repo only
by SHA-256 from the manifest.
