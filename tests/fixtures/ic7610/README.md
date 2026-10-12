# IC-7610 recorded conformance fixtures

Data extracted from private recordings of the real bench IC-7610 (LAN and
USB CI-V sessions, 2026-10-11/12). These files are **data**: never edited
by hand. When `tests/testkit/test_ic7610_conformance.py` reports a
divergence between a simulator (`MockIcomRadio`, `CivSerialFake`) and this
recording, the fix belongs in the simulator (ticket MOR-3310) — then the
matching `conformance_ledger.json` entry is deleted. The tests fail both
when a live divergence is missing from the ledger and when a ledger entry
no longer diverges (the ratchet).

## Provenance and pipeline

Recorded on the bench radio over LAN and over the radio's USB CI-V port
(19200 baud) through `rigplane_testkit/icom/recorder.py:
IcomSessionRecorder` (LAN) and `rigplane_testkit/serial/recorder.py:
SerialCivRecorder` (USB), then reduced with
`python -m rigplane_testkit.icom.fixtures extract` and trimmed by the
coordinator: LAN windows keep, per window, the first occurrence of every
distinct client request, the radio's byte-identical echo of it and the
reply that closes it; control windows keep every control-port packet that
is not a ping or idle. Every file passed
`python -m rigplane_testkit.redaction_gate` with a private deny list
(credentials, host names, IPs, radio MAC) before it landed here.

Timing figures (`t`, the cadence statistics in `idle_cadence.json`) were
recorded on a loaded host and are not asserted as values anywhere; the
tests assert order, structure and reply classes only.

## Files

### `civ_pairs.jsonl`

One JSON object per line with keys `window`, `transport`, `t`, `dir`,
`frame`:

- `window` — `probe` (read-only probe of every profile GET, cmd29-wrapped
  forms, unknown commands), `readonly_session` (`rigplane validate
  --read-only`), `rx_write_restore` (`rigplane validate` receive-side
  write-and-restore: SET + ACK/NAK pairs), `scope` (scope setting reads +
  5 wave sweeps), all with `transport: "lan"`; and `usb_session`
  (`transport: "usb"`: the same status reads, probe list and read-only
  validate over the radio's USB CI-V port — on USB the radio does NOT
  echo requests).
- `dir` — `c2r` (client to radio) or `r2c` (radio to client).
- `frame` — the CI-V frame bytes (`FE FE .. FD`), hex.
- `t` — seconds since recording start (rounded; not asserted).

On LAN every `c2r` row is followed by the radio's byte-identical echo row;
the `r2c` row after that is the reply that closes the request (absent for
the scope wave-on requests, whose answer is the wave stream itself). On
USB no `r2c` frame ever equals a client request.

### `control_windows.jsonl`

One JSON object per line with keys `window`, `t`, `dir`, `len`, `type`,
`reqtype`: control-port packets other than pings (type 0x07, len 0x15) and
idle (type 0x00, len 0x10). `type` is the u16 LE at offset 0x04, `reqtype`
the byte at 0x15 when `len > 0x15`, else null. No payload bytes are kept.
Windows: `handshake` (one clean connect), `power_cycle` (owner power-cycled
the radio with a client connected), `ethernet_loss_22s`,
`ethernet_loss_83s`, `session_hold` (client killed, re-logins
busy-rejected for ~90 s).

### `audio_headers.jsonl`

The first 20 audio packets per direction, header bytes only (0x18 bytes):
keys `t`, `dir`, `hex`.

### `idle_cadence.json`

10-minute idle window statistics: `pings` (per `port:dir:kind`, interval
median/p95/max and span), `token_renewals` (`t` plus `answered_after_s`,
null when unanswered), `retransmit_requests` (per `port:dir`), and
`audio_lengths` (packet length histogram per direction).

### `conformance_ledger.json`

The KNOWN divergences of the simulators from this recording, one flat map
keyed by a stable id:

- LAN answer-class divergences: the request frame hex;
- serial-fake divergences: `usb:` + the request frame hex, plus
  `usb_echo_default` (the fake echoes by default; the radio does not on
  USB);
- `lan_civ_echo` (the radio echoes every LAN request; the simulator does
  not);
- `handshake_<n>` — positional handshake divergences, `n` counting from 1
  over the recorded handshake sequence (pings/idle dropped, cut before the
  first token renewal);
- `token_renew_reply`, `scope_wave`.

Each value is `{"recorded": ..., "simulator": ..., "ticket": "MOR-3310"}`
with the reply-class spelling documented in the test module
(`nak`, `ack`, `none`, `silence`, or
`data cmd=0x.. sub=0x..- len=..` where `sub` is `-` when the command
carries none).
