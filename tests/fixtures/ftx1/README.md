# FTX-1 recorded CAT conformance fixtures

Data extracted from private recordings of the real bench FTX-1 (USB CAT,
RX only, 2026-10-21). These files are **data**: never edited by hand. When
`tests/testkit/test_ftx1_conformance.py` reports a divergence between the
simulator (`FakeCatRadio` with the recording mounted) and this recording,
the fix belongs in the simulator (ticket MOR-3602) and the matching
`conformance_ledger.json` entry is then deleted. The test fails both when a
live divergence is missing from the ledger and when a ledger entry no longer
diverges (the ratchet).

## Provenance

- Radio: Yaesu FTX-1, answers `ID0840;` (the profile's `[identity].expected_ids`).
  Firmware version: **not readable over CAT** and the front panel was not
  inspected (remote, RX-only session) — recorded as unknown, not guessed.
- Link: USB, `YAESU HRI USB I/F` (Silicon Labs CP2105 dual UART), enhanced
  port `/dev/cu.usbserial-01AE340D0`, 38400 baud, Mac mini M4 Pro (macOS),
  no antenna connected.
- Date: 2026-10-21, 12:41-13:00 UTC.
- Core: base `dff3447e8` (main) plus the capture-tool branch
  (`codex/ftx1-cat-recorder`, PR #4283); server version `3.0.0b19`.
- Radio state while captured (restored and verified identical afterwards):
  main VFO A 147.225 MHz FM (`FA147225000;`, `MD04;`), sub 52.9 MHz, `PS1`,
  `AI0`, `TX0`, memory channel field 25 in `IF`.
- Safety: every capture ran through the read-only bridge guard
  (`rigplane_testkit.yaesu.CatReadOnlyGuard`); no write, PTT or TX line
  reached the radio during the `server_session` and `probe` windows.

## Files

### `cat_pairs.jsonl`

One JSON object per line: `window`, `transport` (`usb`), `t`, `dir`
(`c2r` / `r2c`), `line` (the CAT line with its `;`). Trim rule: per window,
the first occurrence of every distinct READ request and the reply that
closes it (`python -m rigplane_testkit.yaesu.fixtures extract`).

- `server_session` — a real Core server (`rigplane --backend yaesu-cat`)
  polling the radio for 10 minutes: 38,827 requests, 57 distinct. The radio
  answered every one with data (no `?;`, no silence), median latency 8-10 ms.
- `probe` — every one of the 72 READ lines the profile declares
  (`scripts/ftx1_capture/probe.py`), once, paced 0.3 s: 71 data, 1 refusal
  (`RA0;` answered `?;`), 0 silence.
- `contact` — the first read-only contact (`ID;`, `IF;`, `FA;`, `MD0;`) and
  a 30-command state snapshot.

### Findings the fixtures do not carry (writes are never fixtures)

- In the captured state the radio answered **every `FA` SET with `?;`**
  (same value, 2 m and HF values, FM and USB mode); `MD0` SETs were accepted
  (the change shows in `IF;`). The fake accepts `FA` sets silently — a known
  divergence of the write path (MOR-3602).
- **AGC state of the capture: MAIN AGC was OFF** (`GT00;` in every snapshot
  and in the 10-minute session; SUB read `GT14;` = AUTO). `SM0;` (MAIN
  S-meter) answered `SM0000;` for the whole capture, and so did `SM1;` (SUB,
  AGC AUTO, no antenna). The likely cause of the MAIN reading is AGC OFF
  (on most rigs the S-meter is driven by AGC) — not proven: no signal was
  present on SUB either. The simulator should reproduce "MAIN S-meter stays
  0 while MAIN AGC is OFF" (pinned for the recording by
  `test_recording_pins_smeter_zero_with_main_agc_off`); the AGC setting was
  not changed.
- Calibration clips (mode-only, current frequency) are private audio, not in
  this repository.

### `conformance_ledger.json`

Divergences between the fake and the recording, each with a ticket. Delete
an entry when the fake is fixed.
