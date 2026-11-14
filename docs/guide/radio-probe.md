---
description: Record how your radio answers into one file you can send for support — read-only, nothing uploaded.
---

# Radio Probe

When a maintainer asks "what does your radio answer?", the radio probe
records it. One command asks your radio the questions its profile lists
— frequency, mode, settings — and writes the exact replies into a single
JSON file. You review the file, then decide whether to send it.

## What it promises

- **Read-only.** The probe only READS. It does not change any setting,
  does not transmit, does not start the tuner, and does not write to
  radio memory.
- **Nothing is uploaded.** There is no upload step and no network code
  in the probe. The file stays where you saved it until you send it
  yourself.
- **USB serial only.** Pass the serial device with `--port`. LAN
  support is planned and not in this version.

## Running it

```bash
rigplane probe FT-991A --port /dev/ttyUSB0
```

Find the profile name in `rigplane models` — it is the model column
(`FT-991A`, `IC-7300`, `FTX-1`, ...). Full form with every option:

```bash
rigplane probe <profile> --port DEVICE [--baud N] [--address 0xNN] \
    [--out FILE] [--include-private] [--label TEXT] [--yes]
```

| Option | Meaning |
| --- | --- |
| `--baud N` | Serial speed. Defaults to the profile's own default. |
| `--address 0xNN` | CI-V radio address. Defaults to the profile's address. |
| `--out FILE` | Where to write. Defaults to `<profile>-probe-<date>.json` in the current directory. |
| `--include-private` | Also read stored memory contents and keyer memory. Off by default. |
| `--label TEXT` | A free-text note stored in the file, for example which state the radio was in. |
| `--yes` | Answer both prompts with yes. Needed when there is no terminal to ask in. |

The probe asks twice: once before it starts, once before it saves.
Answer `n` to either and it stops without writing anything. When stdin
is not a terminal (a script, a pipe), it refuses to save unless you
pass `--yes`.

Exit codes: `0` done, `2` bad usage or unknown profile, `3` the port
could not be opened or nothing answered at all.

## What lands in the file

- The questions asked and the radio's exact replies, one row per read.
- What the radio says it is (its identity answer) and whether it
  matches the profile — a mismatch never stops the run.
- Your radio's settings at that moment: frequency, mode, power, and
  the other values the profile reads.
- A before/after snapshot of those settings, and anything that changed
  during the run.

What never lands in the file: your name, IP address, computer name,
the port path, or log files. Memory channels and keyer memory are left
out unless you pass `--include-private` — they can hold callsigns and
stored text, so look through the file before you send it if you do.

## If the radio stays silent

After five unanswered questions in a row the probe stops and says so —
usually the radio is off or the port and speed are wrong. The rows
collected so far are still offered for saving.

## Reference

- File format: [Probe File Contract](../contracts/probe-file-v1.md).
- Screen wording is pinned by exact-string tests over
  `probe/render.py` (`start_screen`, `pre_save_summary`, `closing_line`);
  the run logic lives in `probe/runner.py` (`run_probe`).
