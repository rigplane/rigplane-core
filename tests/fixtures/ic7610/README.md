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

LAN windows were extracted with
`python -m rigplane_testkit.icom.fixtures extract`, then trimmed by the
coordinator to: per window, the first occurrence of every distinct client
request, the radio's byte-identical echo of it and the reply that closes
it; control windows keep every control-port packet that is not a ping or
idle. The USB window (`transport: "usb"`) comes from the serial bridge
recording via `rigplane_testkit.serial.recorder.civ_frames`, then trimmed
to the same first-occurrence pairing rule (on USB the radio does NOT echo
requests, so there is no echo row). Every file passed
`python -m rigplane_testkit.redaction_gate` with a private deny list
(credentials, host names, IPs, radio MAC) before it landed here — the gate
ran over all four files.

Timing figures (`t`, the cadence statistics in `idle_cadence.json`) were
recorded on a loaded host and are not asserted as values anywhere; the
tests assert order, structure and reply classes only. In particular the
token-renew check asserts only that every recorded renew was answered
(`answered_after_s` is not null) and that the simulator's answer is a
0x40-byte control packet of request type 0x05 — it asserts no time bound.

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
the `r2c` row after that is the reply that closes the request. The scope
wave-on/off SETs (`27 10 01`, `27 11 01`) are excluded from that pairing:
their answer is the wave stream itself, which the `scope_wave` cadence
check covers. On USB no `r2c` frame ever equals a client request.

### `control_windows.jsonl`

One JSON object per line with keys `window`, `transport`, `t`, `dir`, `len`, `type`,
`reqtype`: control-port packets other than pings (type 0x07, len 0x15) and
idle (type 0x00, len 0x10). `type` is the u16 LE at offset 0x04, `reqtype`
the byte at 0x15 when `len > 0x15`, else null. No payload bytes are kept.
Windows: `handshake` (one clean connect), `power_cycle` (owner power-cycled
the radio with a client connected), `ethernet_loss_22s`,
`ethernet_loss_83s`, `session_hold` (client killed, re-logins
busy-rejected for ~90 s).

### `audio_headers.jsonl`

The first 20 audio packets per direction: keys `t`, `dir`, `hex`, where
`hex` holds the first 0x18 bytes of the audio-port packet, or the whole
packet when it is shorter.

### `idle_cadence.json`

10-minute idle window statistics: `pings` (per `port:dir:kind`, interval
median/p95/max and span), `token_renewals` (`t` plus `answered_after_s`,
null when unanswered), `retransmit_requests` (per `port:dir`), and
`audio_lengths` (packet length histogram per direction).

### `conformance_ledger.json`

The KNOWN divergences of the simulators from this recording, one flat map
keyed by a stable id:

- LAN answer-class divergences: the request frame hex;
- `handshake_<n>` — positional handshake divergences, `n` counting from 1
  over the recorded handshake sequence (pings/idle dropped, cut before the
  first client token renewal);
- `scope_wave` (the recording holds 5 complete `27 00` wave frames after
  wave-on; the simulator must send at least as many and sends none).

Each value is `{"recorded": ..., "simulator": ..., "ticket": "MOR-3310"}`
with the reply-class spelling documented in the test module
(`nak`, `ack`, `none`, `silence`, or
`data rx=0x..- cmd=0x.. sub=0x..- len=..` where `rx` is `-` for plain
frames and `sub` is `-` when the command carries none).

### `retransmit_gaps.jsonl`

192 rows of the real IC-7610's LAN CI-V sequence-gap behaviour
(recorded 2026-10-12; the evidence behind the simulator's gap tracking
in `MockIcomRadio` and `tests/testkit/test_simulator_civ_gap.py`). One
JSON object per line with keys `window`, `t` (seconds into the window),
`dir` (`c2r` / `r2c`), `kind`, `seq` and `repeat` — consecutive
identical rows are collapsed into one row with a count — plus `frame`
(the CI-V frame hex) on every `civ_data` row, `ranges` (the missing
`[first, last]` pairs, inclusive) on every `retransmit_ranges` row, and
`t_last` on rows that span time. `kind` is `civ_data` (a tracked CI-V
request), `idle` (a tracked 0x10-byte type-0 keepalive),
`retransmit_single` (a 0x10-byte type-0x01 control asking for `seq`) or
`retransmit_ranges` (a longer type-0x01 control with header seq 0 whose
body is little-endian u16 pairs, one per contiguous missing run).
Windows: `gap_lost_buffered` (packet 133 lost, asked twice in single
form at the next tracked packet, resent, answered),
`gap_never_filled` (154–156 never resent: `ranges [[154, 156]]`
repeated about every 0.1 s, each twice, no CI-V answers after),
`gap_filled_by_idle` (131–133 refilled by idle packets carrying those
numbers, narrowing the request to single 132, then the request stalled
through the gap answered after the fill) and
`single_gap_filled_by_idle` (one skipped number refilled by idle).

The serial fake (`rigplane_testkit/serial/civ_serial.py: CivSerialFake`)
takes its CI-V address and `19 00` model id from the resolved profile.
Without a recorded table it answers only a handful of commands (frequency
03/05, mode 04/06, PTT 1C 00, S-meter 15 02, identity 19 00) and NAKs any
other frame addressed to it (a controller's own ACK/NAK, unknown broadcasts
and frames addressed elsewhere get no answer). With the recorded USB table
mounted (`tests/testkit/ic7610_fakes.py: ic7610_usb_fake`), a
Command29-wrapped request addressed to it is answered from the table or
NAKed, never by those commands, and so is an unwrapped one those commands
do not handle (a controller's own ACK/NAK still gets no answer).
