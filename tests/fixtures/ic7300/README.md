# IC-7300 recorded conformance fixtures

Data from the real bench IC-7300 (USB CI-V, 2026-10-21), read-only, receive
side only. **Data: never edited by hand.** When
`tests/testkit/test_ic7300_conformance.py` reports a divergence between
`CivSerialFake` (recorded table mounted, `tests/testkit/ic7300_fakes.py`) and
this recording, the fix belongs in the simulator and the matching
`conformance_ledger.json` entry is then deleted (ratchet: the test fails both
for a divergence missing from the ledger and for a ledger entry that no
longer diverges).

## Provenance

| | |
|---|---|
| Radio | Icom IC-7300 (CI-V address 0x94, `19 00` answers `94`), no antenna connected, receiving only |
| Link | USB, Silicon Labs CP210x bridge as COM3 on the Windows host GTR7; **CI-V USB baud rate set to 19200 on the radio** (silent at 115200, 9600, 4800; profile default is 115200 -> MOR-3620) |
| Date | 2026-10-21, 08:30-09:12 EDT |
| Radio firmware | not read (CI-V has no firmware query on this radio; front-panel reading not taken) |
| `probe` window | `scripts/ic7610_capture/probe.py` (profile allow-list GETs + 4 unknown commands) through `SerialCivRecorder` (`python -m rigplane_testkit.serial record`), 168 distinct requests; recorded by the radio-simulators session; raw recording SHA-256 `81cbcbb0afa4b2b971879a9fd236ffad913db6e0bc17fe1e35c3df5cc58d7a8a`, kept in private storage |
| `server_poll` window | Core web server (`rigplane ... web`, serial backend, 19200) driven only through its API, behind the guarded bridge `scripts/ic7300_capture/server_session.py`: 388 s of the server's own polling; 67 distinct requests |
| Trim rule | `python -m rigplane_testkit.serial.fixtures extract`: per window the first occurrence of every distinct request and the reply that closes it; pipelined requests are paired in order; the radio does not echo |
| Redaction | `python -m rigplane_testkit.redaction_gate` clean over all files (no deny strings applied: serial CI-V carries no credentials; no ASCII text appears in any reply) |

## Files

- `civ_pairs.jsonl` - rows `{window, transport:"usb", t, dir, frame}` like the IC-7610 file. Reads the server issues after startup are included (the Core forms of the scope setting reads carry a selector byte, e.g. `27 15 00`, and are all answered).
- `probe_answers.json` - the probe's per-command result: class (`data`/`nak`), frame, latency in ms. 168 probes: 151 data, 17 NAK, 0 silent. Reply latency median 30.9 ms (min 15.7, max 108.8).
- `evidence.json` - facts that cannot be rows of `civ_pairs.jsonl` without teaching the answer table wrong things: the baud sweep, and the scope enable attempt at 19200 (`27 10 01` -> ACK, `27 11 01` -> NAK; no `27 00` frame ever followed; the panel was ON and wave output OFF before and after).
- `conformance_ledger.json` - known divergences of the fake from this recording: reply latency, accepts any baud, and the scope switch commands (`27 10`/`27 11` reads and `27 10 01` are not implemented by the fake; the recorded wave-output enable at 19200 is NAKed by the radio and so is the fake's, which only coincides).

## Commands the radio NAKed (17 of 168, bare forms of the probe)

| Count | Probes | Meaning |
|---|---|---|
| 9 | `get_scope_edge`, `_fixed_edge`, `_hold`, `_mode`, `_ref`, `_span`, `_speed`, `_vbw`, `get_scope_wave` | probe artefact: the probe sends the bare form; Core sends them with a selector byte and gets answers (see `server_poll`); `27 00` is the wave stream itself |
| 2 | `cmd29:get_freq:0/1` | the IC-7300 has no Command29 wrapper (single receiver) |
| 4 | `unknown:1a050999`, `unknown:7f`, `unknown:147e`, `unknown:277f` | the deliberately unknown requests; NAK is the radio's answer to an unknown command |
| 2 | `get_bsr` (`1A 01`), `get_tx_band_edge` (`1E 01`) | declared by `rigs/ic7300.toml`, refused in the bare form probed; needs its own capture |

No command was ignored (0 silent). The server's own startup also tried the
write-shaped `07 00` and, at shutdown, force-receive writes; the bridge dropped
them (recorded as `blocked`, never sent).

## Not captured yet

- Scope wave stream and its frame rate: the radio refuses wave output at 19200 baud. Needs the radio at 115200.
- USB audio sample format and RX air recordings (HF master set): need the antenna and the 115200 setting for the scope.
