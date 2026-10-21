# IC-705 recorded fixtures (USB CI-V and LAN)

Data from the real bench IC-705, recorded receive-side only through the
production `rigplane` client behind the serial bridge recorder
(`rigplane_testkit.serial.recorder`). **Data, never edited by hand.** The LAN
(Wi-Fi) half is added in the section "LAN over Wi-Fi". Linear: MOR-3609 (capture), MOR-3605 (personality).

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
- `probe_answers.json` - answer class (`data`/`ack`/`nak`/`silence`/`error`), latency and reply for each of the 107 probes; this is the class table a simulator must reproduce for the radio outcomes `data`, `ack`, `nak` and `silence` (the two silent synthetic probes included). The one `error` row (`get_comp_meter`) is a client-side failure, not radio behaviour, and is excluded from that contract.
- `scope_cadence.json` - per-transport scope baseline (USB CI-V): the radio sweeps 11 segments per sweep at 4.13 sweeps/s (interval median 0.242 s, p95 0.243 s, max 0.247 s); the web scope socket delivered 4.15 frames/s (491-byte frames, interval p95 0.247 s). Feeds the bench scorecard row S10.
- `usb_audio.json` - the radio's USB audio device as the OS and Core see it (class-compliant, 48 kHz, 2 channels). No samples are kept.

## Facts for the personality (MOR-3605)

- Answer classes of the 107 probes: 85 data, 19 NAK, 2 silence (`get_command_error_fa`/`get_command_ok_fb`, synthetic), 1 client-side error (`get_comp_meter`: the frame was built, the client's transaction raised after 0.1 ms, so whether it reached the radio is not established).
- NAKed: `get_bsr`, `get_freq_offset`, `get_speech`, `get_tx_band_edge`, the `cmd29` forms (single receiver: 0x29 is not understood), the four unknown probes, and the nine `get_scope_*` forms **without a receiver byte**.
- The scope settings answer when asked with the receiver byte (`27 14 00`, `27 15 00`, `27 16 00`, ... in the `scope` window), and `27 10` / `27 11` answer bare. The profile's bare `get_scope_*` forms are therefore not what this radio accepts (ledger material for the conformance replay).
- On USB the radio does not echo requests and sent no unsolicited frames in 90 s idle although CI-V transceive reads back ON (`1A 05 01 31` = 01).

## VHF/UHF reads (2026-10-21, ~13:45-14:00 UTC)

Same probe, same bridge, after tuning with `set_mode FM` + `set_freq` only (no antenna; frequency, mode and AGC memory were restored and verified, see below): 145.500 MHz and 433.500 MHz.
`civ_pairs_vhf_uhf.jsonl` (windows `probe_2m_fm_145500`, `probe_70cm_fm_433500`, same keys and `transport: "usb"` as `civ_pairs.jsonl`) keeps only the requests whose answer differs from the HF probe, ignoring the S-meter, the radio clock and the AF level knob; it is kept apart from `civ_pairs.jsonl` because the same request has different answers per band, so the two must not be mounted as one answer table. `vhf_uhf_state.json` holds the server-API state read on each band.

What the radio showed, FM versus the HF (USB) probe:

- Same on every band: `get_freq_offset` (`0D`) still NAKs, so no duplex offset is readable by that command; `0F` read `00` on both (the server state shows repeater shift 0, split off); tone type off, tone and TSQL frequency 88.5 Hz; `get_dtcs` answered the same as on HF. The radio held whatever repeater settings it already had, and those read as off/default, so offset, tone and DCS values for a configured repeater channel are still unrecorded.
- Reads that stop working in FM (NAK): `get_agc_time_constant`, `get_filter_shape`, `get_filter_width` (FM width is fixed), `get_manual_notch_width`, `get_pbt_inner`, `get_pbt_outer`. Total NAK 25 versus 19 on HF.
- Changes with the band: mode `04 05 01` / selected mode `26 00 05 00 01` (FM), AGC `16 12 01` (fast), tuning step `10 11`, `get_s_meter_sql_status` and `get_various_squelch` (the squelch status bit is 0 in FM), band edge (`02`) on 70 cm (2 m answered like HF).
- After restoring 14.074 MHz USB, the probe again matched the first one except S-meter, clock and the AF level (the owner turned the knob between runs).

## LAN over Wi-Fi (2026-10-21, ~14:10-14:16 UTC)

The radio on the bench network over Wi-Fi (network control on), recorded through the redacting UDP proxy `python -m rigplane_testkit.icom record` (login, token, device name, computer name and IP are masked in the recording; the credentials were read from the secret store at run time and never written down), driven by the production client from the Mac mini. One login attempt per phase; RX only. Same three steps as USB: the probe (107 requests, `lan_probe`), a 100 s `rigplane web` session with the scope and audio sockets open (`lan_scope`, plus the control/audio files), and the probe again.

- `civ_pairs.jsonl`: rows with `transport: "lan"`, windows `lan_probe` and `lan_scope` (first occurrence of each request, the radio's byte-identical **echo** of it and the reply that closes it, regrouped so each request is followed by its echo and reply even though the client pipelines requests; five whole scope sweeps; `RecordedAnswerTable.from_civ_pairs(rows, "lan")` reproduces all 107 probe answers, pinned by a test). The client wrote the same non-read requests as on USB (VFO A select, scope output switches, PTT off and CW cancel at shutdown); nothing else.
- `probe_answers_lan.json`: answer class and latency per probe. The answer CLASSES (data/NAK/silence) are the same on LAN and USB for 106 of 107 requests; the exception is `get_comp_meter` (`15 14`), which answers data (`15 14 00 00`) on LAN while the USB run recorded a client-side error. Reply VALUES are not identical across the two runs: meters, the radio clock and the AF level knob differ (`get_s_meter`, `get_vd_meter`, `get_system_time`, `get_af_level`), so do not treat the two files as one value table. Latency: data replies median 11.4 ms over LAN (p95 33.5 ms), 3.3 ms over USB.
- Scope: over LAN a sweep is ONE `27 00` frame (sequence 1 of 1, 497 bytes), 30.0 sweeps/s (interval median 0.033 s, p95 0.046 s, max 0.137 s); the web scope socket delivered 30.1 frames/s (`scope_cadence.json`, key `lan`). Over USB the same radio sends 11 segments per sweep at 4.13 sweeps/s.
- `control_windows.jsonl`: the handshake window (control packets that are not pings or idle), no payload bytes. `lan_session_cadence.json`: ping, token-renew and retransmit statistics of the 100 s session (a session with scope and audio running, not an idle window); the one token renewal was answered after 0.01 s, no retransmit requests.
- RX audio on the radio's audio port (`audio_stream.json`, `audio_headers.jsonl`, `audio_rx_headers.jsonl`): each 20 ms burst is two packets of 1388 and 580 bytes (0x18-byte header + 1364 and 556 payload bytes = 1920 bytes = 960 samples of PCM 1 channel 16-bit at 48 kHz), 50 bursts/s; sequences at 0x06 and 0x12 step 1; the BE u16 at 0x10 is 0x8106 on the first packet of a burst and 0x8006 on the second. No samples are kept. The audio WebSocket of the server delivered Opus 48 kHz mono 20 ms frames (the server's transcode), not this native format.

## Open (needs the owner or later)

- **Configured repeater channel**: offset, tone and DCS values are only recorded at factory defaults; a memory channel with duplex, offset and CTCSS/DCS would give real values.
- **USB audio samples**: none in this directory. Over ssh the host returned digital silence (macOS microphone permission), so `usb_audio.json` only records the device format; real USB audio of this radio was recorded later through a Terminal-launched job, kept private (callsigns on the air) and referenced by SHA-256, not stored here.
- Firmware version.
