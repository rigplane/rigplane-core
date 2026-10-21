# IC-705 recorded fixtures (USB CI-V)

Data from the real bench IC-705, recorded receive-side only through the
production `rigplane` client behind the serial bridge recorder
(`rigplane_testkit.serial.recorder`). **Data, never edited by hand.** The LAN
(Wi-Fi) half is not here yet: the radio was not on the network when this was
captured (see "Open"). Linear: MOR-3609 (capture), MOR-3605 (personality).

## Provenance

| | |
| --- | --- |
| Date | 2026-10-21, 12:23-12:29 UTC |
| Radio | Icom IC-705, CI-V address 0xA4, USB CI-V port of the radio (not the second serial port), no echo of requests; firmware version not recorded (CI-V has no read for it) |
| Link | radio's USB port, via a USB hub, to the Mac mini bench host, 115200 baud |
| Software | `rigplane` v3.0.0b19 (`dff3447e8`) from a synced worktree, run on the bench host |
| Source | **serial bridge recording** (`SerialCivRecorder`: every byte both ways, with time). Chosen over an API trace because it holds the radio's real bytes, including NAKs and the scope wave stream, while the production client drives the traffic. |
| Radio state | HF, 14.074 MHz USB, VFO A selected, VFO B 7.074 MHz, split off, receiving (PTT state read as RX throughout) |

Capture steps (each behind its own recorder instance):

1. `probe` - `scripts/ic7610_capture/probe.py --backend serial --model IC-705 --radio-addr 0xA4`: the safe subset of `rigs/ic705.toml` getters once, paced 0.3 s (the probe driver deliberately leaves out the 0x07, 0x0E and 0x1C families and `1A 00`: `get_vfo`, `get_scan`, `get_transceiver_status`, `get_tuner_status`, `get_xfc_status`, `get_memory_contents` and similar were NOT asked, so they are untested on hardware), plus four unknown commands (answer class and latency per command: `probe_answers.json`).
2. `scope` - `rigplane --backend serial ... web --no-rigctld --no-discovery` for ~90 s; a client of `/api/v1/ws` and `/api/v1/scope` counted frames for 60 s. The recording keeps the client's startup reads, the scope switches and five complete scope sweeps.
3. `readonly_session` - `rigplane validate --hardware --allow-hardware --read-only`.
4. Probe repeated after steps 2-3 to prove nothing stayed changed: 105 of 107 answers identical, the two differences are the S-meter and the radio clock.

Trim rule, applied with the serial extractor of Core PR #4284 (`rigplane_testkit.serial.fixtures extract`, MOR-3595; not on main when this was written, the rule is stated here so the output can be reproduced by hand): first occurrence of every distinct client request and the reply that closes it; five whole scope sweeps; on this port the radio does NOT echo requests, so there is no echo row. Raw recordings stay private.

Redaction: `python -m rigplane_testkit.redaction_gate` was run over every file here with a private deny list (host names, user names, the radio's USB serial number); clean. The CI-V `1A 05` items in the files are clock, date, UTC offset and a few menu values only; no network item (IP, user, password) was read.

## What the radio wrote (everything the client sent that is not a read)

| Frame body | When | Result |
| --- | --- | --- |
| `07 00` (select VFO A) | server start | ACK; state unchanged (A was already selected) |
| `27 10 01` (scope wave output on) | scope client connected | ACK; was already 01 |
| `27 11 01` / `27 11 00` (scope data output on / back off) | scope client connected / session end | ACK; read back 00 before and after |
| `1C 00 00` (PTT off), `17 FF` (stop CW sending) | server shutdown, production "release TX" step | ACK / NAK; radio was in RX, nothing was transmitted |

No PTT-on, tune, frequency, mode or power command was sent. The shutdown release step is production client behaviour; it is not a transmit.

## Files

- `civ_pairs.jsonl` - keys `window`, `transport` (`usb`), `t`, `dir` (`c2r`/`r2c`), `frame`. Windows `probe` (208 rows), `scope` (203), `readonly_session` (14). Reads `RecordedAnswerTable.from_civ_pairs`.
- `probe_answers.json` - answer class (`data`/`ack`/`nak`/`silence`/`error`), latency and reply for each of the 107 probes; this is the class table a simulator must reproduce, including the two silent synthetic probes.
- `scope_cadence.json` - per-transport scope baseline (USB CI-V): the radio sweeps 11 segments per sweep at 4.13 sweeps/s (interval median 0.242 s, p95 0.243 s, max 0.247 s); the web scope socket delivered 4.15 frames/s (491-byte frames, interval p95 0.247 s). Feeds the bench scorecard row S10.
- `usb_audio.json` - the radio's USB audio device as the OS and Core see it (class-compliant, 48 kHz, 2 channels). No samples are kept.

## Facts for the personality (MOR-3605)

- Answer classes of the 107 probes: 85 data, 19 NAK, 2 silence (`get_command_error_fa`/`get_command_ok_fb`, synthetic), 1 client-side error (`get_comp_meter`, no frame built).
- NAKed: `get_bsr`, `get_freq_offset`, `get_speech`, `get_tx_band_edge`, the `cmd29` forms (single receiver: 0x29 is not understood), the four unknown probes, and the nine `get_scope_*` forms **without a receiver byte**.
- The scope settings answer when asked with the receiver byte (`27 14 00`, `27 15 00`, `27 16 00`, ... in the `scope` window), and `27 10` / `27 11` answer bare. The profile's bare `get_scope_*` forms are therefore not what this radio accepts (ledger material for the conformance replay).
- On USB the radio does not echo requests and sent no unsolicited frames in 90 s idle although CI-V transceive reads back ON (`1A 05 01 31` = 01).

## Open (needs the owner or later)

- **LAN / Wi-Fi capture**: `rigplane discover` found no IC-705 on the network. Needs the radio's Wi-Fi/Network Control switched on with a network user; then control/CI-V/audio phases, login, keep-alive cadence and RX audio layout are captured the same way.
- **VHF/UHF reads**: the radio sat on HF, so duplex offset (`0C`), DCS and FM-width reads on a VHF/UHF FM channel are not covered (`get_freq_offset` NAKs on HF). Needs the owner to put VFO A on a VHF or UHF FM channel; no frequency or mode change is made by the capture itself.
- **USB audio samples**: the capture host denies microphone access to processes started over ssh (a webcam microphone reads exact zero too), so no RX audio over USB was recorded; the `usb_audio.json` zero probe says nothing about the radio. Needs the microphone permission granted on the host, or the LAN audio path.
- Firmware version.
