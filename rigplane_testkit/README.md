# rigplane_testkit

Test utilities for rigplane-core and downstream projects: real-socket radio
simulators that speak the actual wire protocols, so tests exercise the same
UDP code paths as production.

## Contents

- `rigplane_testkit.icom` — `MockIcomRadio`, an asyncio UDP simulator of an
  Icom IC-7610 LAN interface: control-port discovery/login/token/ping, a CI-V
  command port, single-owner session holding with keepalive renewal, and
  fault injection (drop, reorder, stall, delay). The CI-V dispatcher answers
  0x03/0x04/0x05/0x06 plus the selected/unselected receiver vocabulary
  0x25/0x26 (both subcommands; the selected VFO shares the 0x03-0x06 state,
  the unselected VFO has its own fields) and the 0x1A 05 MOD-input and NB
  menu reads the product's startup reader issues (wire-pinned by
  `tests/testkit/test_simulator_civ_vocab.py`). For CI-V sequence numbers
  it models the recorded gap behaviour
  (`tests/fixtures/ic7610/retransmit_gaps.jsonl`, pinned by
  `tests/testkit/test_simulator_civ_gap.py`): per radio session it tracks
  the client's tracked numbers (idle, OpenClose, CI-V DATA) from the
  OpenClose's own header number, holds packets past a gap, fills on an
  idle, OpenClose or CI-V DATA packet carrying a missing number,
  processes held packets in order once nothing is missing, and
  ignores numbers at or below the last processed one — a re-sent
  OpenClose never restarts tracking (MOR-3361), which ends only on a new
  accepted login, a newly claimed conninfo, `drop_session` or reboot —
  while sending the retransmit request twice (one round) for a tracked
  packet held with numbers still missing only when the previous round is
  at least 75 ms old (single form for one missing number, otherwise one
  ranges packet per contiguous missing run), plus a twice every 0.1 s
  repeat, likewise paced, as the fallback for a silent client. `civ_retransmit_requests` counts the request packets sent
  on the CI-V port; `civ_gap_open` is true while any number is missing.
  A re-sent OpenClose still gets its stream-active marker; only
  tracked-packet processing follows the ignore rule.
  The audio port advertised at 0x46 of the status reply is a real
  listener only with `MockIcomRadio(enable_audio=True)` (default off;
  `MockIcomRadio.audio_port`, bound at the CI-V port + 1 when free).
  With the default the reply still advertises the port but nothing
  listens there and nothing answers. When on, the listener speaks the
  same transport handshake as the CI-V port: AYT→IAH,
  AreYouReady echo, ping replies, tracked client numbering — never
  answered with retransmit requests (recorded: zero on the audio port).
  The client's audio OpenClose(open) — the packet
  `runtime/_audio_runtime_mixin.py: _ensure_audio_transport` sends after
  the handshake — starts the RX stream: synthetic PCM at the recorded
  cadence (bursts of two 1388-byte and one 1136-byte packet every 20 ms
  = 3840 payload bytes per burst, both header sequences stepping 1;
  `tests/fixtures/ic7610/audio_stream.json` and
  `audio_rx_headers.jsonl`). Every client audio DATA frame sent while
  the audio stream lives is recorded
  in `audio_tx_frames` (header seq, inner seq at 0x12, payload length,
  arrival time; the client's format is the only reference — the TX path
  is unrecorded). The stream survives a CI-V-only soft reconnect
  (recorded) and stops when the session ends (token-remove/logout,
  `drop_session`, reboot); without an owned session the audio opener's
  own token-remove/control-disconnect still ends it, while a foreign one
  changes nothing. `audio_stream_drop()` stops it keeping the
  session, `audio_port_silence(at, for_seconds)` blacks out only the
  audio port (`audio_rx_frames_sent` counts only packets actually sent),
  and `audio_last_client_seq` tracks the client numbering
  per session.
  Wire-level examples live in `tests/testkit/test_simulator_faults.py`
  and `tests/testkit/test_simulator_drop_session.py`; the audio port is
  pinned by `tests/testkit/test_simulator_audio.py`, including a TX-sink
  run whose frames are built with the production builder
  (`src/rigplane/audio/lan_stream.py`: `build_audio_packet`). The
  CI-V implementation lives in `rigplane_testkit/icom/simulator.py`; the
  audio port in `rigplane_testkit/icom/simulator_audio.py`
  (`AudioPortMixin`).
  A `recorded_answers=` keyword mounts a
  `rigplane_testkit.icom.replay.RecordedAnswerTable`: a request the native
  CI-V handlers NAK — including one with no native handler — is answered
  from the recorded rows before the final NAK, while a native data/ACK
  answer, the `refused_civ_commands` hook and the LAN CI-V echo keep
  precedence (answer order pinned by
  `tests/testkit/test_simulator_recorded_answers.py`).
  `tests/testkit/ic7610_fakes.py: ic7610_lan_fake` builds the simulator
  with the recorded `lan` rows mounted.
  The implementation lives in `rigplane_testkit/icom/simulator.py`.
- `MockIcomRadio.refused_civ_commands` — an opt-in set of `(cmd, sub)` pairs
  answered with a bare NAK instead of dispatching (default off;
  `refused_count` counts the refused frames), for tests that drive a
  radio-refused write end to end.
- `rigplane_testkit.icom.faults` — `FaultPlan` / `FaultRule`: a declarative,
  seeded (deterministic) fault plan with one rule per port (`control`, `civ`)
  and direction (`to_client`, `from_client`). Each rule can drop (probability
  or every-Nth), duplicate (with inter-copy delay), delay (fixed + jitter),
  reorder (window), corrupt (bit-flip N bytes) and truncate (to K bytes), and
  can be bounded by packet count or duration (e.g. "lose the next 5 control
  packets"). Reorder buffers always flush — on a full window, on a
  `reorder_flush_s` deadline (driven by the simulator when the stream goes
  idle), and when a bound deactivates the rule — so no packet is ever held
  forever. Each stream draws from its own RNG derived from the plan seed
  (`f"{seed}:{port}:{direction}"`), so one stream's traffic cannot change
  another stream's decisions. The plan sits beside the legacy knobs
  (`drop_rate`, `response_delay`, `stall_for`), which are unchanged.
  `MockIcomRadio` also offers scheduled events: `reboot(at, down_for,
  busy_for, notice=False)` — stop answering, forget the session, rotate the
  token, restart CI-V sequence numbering, fence every pre-reboot client off
  the CI-V port until the client whose fresh login's own conninfo was
  accepted opens from the CI-V port its conninfo declared (its IP alone
  when it declared none),
  ignore token packets other than renewals and conninfo packets carrying
  the pre-reboot token, answer busy, then accept a fresh login; with `notice=True` the
  recorded power-off order is replayed first — one unsolicited 0x90 conninfo
  notice (busy=0, "session free"), then ~0.02 s in which discovery and
  control-port pings are still answered (CI-V-port pings from the pre-reboot
  client stay fenced), then silence — `drop_session(at, notice=True)` — free
  the held session, rotate the token, stop unsolicited CI-V, ignore CI-V DATA
  from the dropped client while still answering pings on both ports, send one
  unsolicited 0x90 notice ~0.1 s after the drop, and apply the same
  per-client fresh-login OpenClose fence — and
  `keepalive_silence(at, for_seconds)` — pings are received but not answered.
  Token renewals follow the recorded layout (`_token_renew_reply`): a renewal
  with the current token is answered `00000000` and extends the single-owner
  hold whatever its source (renewal traffic from the holder itself extends
  it too), while a renewal with a token from an ended epoch
  (the owner's token-remove, disconnect, OpenClose close, `free_session`,
  `drop_session`, reboot, or hold expiry — never a bare login) is answered
  `ffffffff`, exactly once. In single-owner mode only the session
  owner's token-remove and the CI-V stream owner's OpenClose close end
  the session (the production client opens and closes the stream from
  its CI-V socket, a different identity from the control socket's
  owner); in default mode a token-remove retires only when it carries
  the live token.
  `civ_standby(on)` is a test-only scenario, not recorded
  behaviour: while on, the CI-V port emits nothing for CI-V DATA (no echo,
  no reply, no retransmit requests) but still advances sequence tracking
  and consumes held gap packets without processing them,
  while CI-V-port pings and the control port keep answering.
  Wire-level examples live in `tests/testkit/test_simulator_faults.py`,
  `tests/testkit/test_simulator_drop_session.py` and
  `tests/testkit/test_simulator_renew_standby.py`.
- `rigplane_testkit.serial` — `CivSerialFake`, a stateful IC-7300 personality
  on an in-memory serial duplex (frequency, mode, filter, PTT, S-meter,
  profile-derived identity, USB CI-V echo). It mounts behind the production
  `SerialCivLink` through its `open_serial_connection` seam and behind
  `open_serial_port` through the `opener` seam, and scripts the serial-side
  faults the link was fixed against: delayed identity, identity only after a
  reconnect, wrong model id, a silent link (MOR-2861), and USB vanish /
  reappear (`ENODEV`, mapped to `serial_not_found`). The
  `recorded_answers=` keyword mounts a
  `rigplane_testkit.icom.replay.RecordedAnswerTable`. For a request
  addressed to the fake, the native handlers keep precedence for unwrapped
  requests, a Command29-wrapped request is answered only from the table or
  NAKed (never unwrapped into a native handler), and any other unknown
  request falls to the table then a NAK (a controller's own ACK/NAK gets no
  reply). The echo default comes from the table's `echo` fact unless
  `echo=` is passed explicitly. The implementation lives
  in `rigplane_testkit/serial/civ_serial.py`.
- `rigplane_testkit.serial.recorder` — `SerialCivRecorder`, a TCP↔serial
  bridge that opens the real USB CI-V device through the production
  `open_serial_port` open (DTR/RTS deasserted) and serves it on
  `socket://HOST:PORT`, so a client pointed at that URL records a session
  with zero production change. Every chunk is appended to JSONL with
  direction and monotonic time — the same keys the LAN recorder writes, no
  redaction (CI-V serial carries no credentials) — flushed per line; one
  TCP client is served at a time and later concurrent connections are
  closed and counted. `civ_frames(path)` reassembles the JSONL back into
  complete `FE FE ... FD` frames per direction. CLI:
  `python -m rigplane_testkit.serial record --device DEV --out FILE
  [--baud 115200] [--listen-host H] [--listen-port P]`.
- `rigplane_testkit.yaesu` — `FakeCatRadio`, a stateful FTX-1-style Yaesu CAT
  personality (frequency, mode, PTT, power, auto-information mode) on a real
  TCP server, reached through pyserial's `socket://` URL handler: the
  production `open_serial_port` open with `device="socket://127.0.0.1:<port>"`
  connects the real `YaesuCatRadio` backend to it with no seam and no mock.
  Replies are formed with the production `format_command` on the profile's
  parse templates, inbound SETs are decoded by `CatCommandParser` compiled
  from the write templates, and the `ID;` answer the connect identity gate
  probes comes from the profile's `[identity].expected_ids`. Scripted faults:
  a garbled reply, `?;` rejection, a silence window, a request echo, and an
  auto-information burst — immediate, or on the next request the fake
  processes, ahead of its reply if it has one (plus AI mode pushing info
  lines on state change).
  The implementation lives in `rigplane_testkit/yaesu/cat_fake.py`.
- `rigplane_testkit.icom.recorder` — `IcomSessionRecorder`, an asyncio UDP
  proxy that sits between a client and a radio (real or simulated) and writes
  every datagram on the control/CI-V/audio ports to JSONL with direction,
  port and monotonic time. The radio-advertised CI-V/audio ports in the
  status packet are rewritten to the proxy's listeners, and the
  client-declared ports in its conninfo packet are rewritten to the proxy's
  pre-bound upstream sockets, which receive the CI-V/audio datagrams a radio
  sends to those declared ports and relay them to the client; login
  credentials, the conninfo device name and the session-token slot of every
  control packet (0x40/0x50/0x60/0x90, both directions) are zeroed before
  anything hits disk and the conninfo GUID/MAC area is replaced with a
  deterministic pseudonym. Listen ports can be pinned for harnesses that
  need known targets (`listen_port` binds control=N, CI-V=N+1, audio=N+2),
  and the radio-facing upstream sockets bind `upstream_port` (+1 for audio)
  on `upstream_host` (default the wildcard), never `listen_host`. CLI:
  `python -m rigplane_testkit.icom record --radio HOST --out FILE
  [--listen-host H] [--listen-port P] [--upstream-port P]
  [--upstream-host H]`.
- `rigplane_testkit.icom.replay` — `Recording.load` plus `ReplayResponder`,
  a pure (socket-free) replayer that answers CI-V requests with the recorded
  reply bytes (latest wins, misses land in `unanswered`) and yields the
  recorded unsolicited frames on their timeline; and `RecordedAnswerTable`,
  the recorded request → reply facts of one civ_pairs transport as a
  stateful answer table keyed on the CI-V body: a data reply that starts
  with the request body registers the read selector; a recorded ACK to a
  request extending an already registered selector marks that selector
  writable, and answering a request that extends writable selectors stores
  the remainder into the longest one's register and ACKs (unless the
  recording has an entry for that exact request); a recorded NAK NAKs that
  exact request; and the `echo` fact is read from the rows (never from the
  transport name). `CivSerialFake(recorded_answers=...)` mounts a table;
  `tests/testkit/ic7610_fakes.py: ic7610_usb_fake` builds the fake IC-7610
  over USB with the recorded `usb_session` table.

## How tests import it

Existing tests keep their imports unchanged: `tests/mock_server.py` is a thin
re-export shim over `rigplane_testkit.icom.simulator`. New code can import
the package directly:

```python
from rigplane_testkit.icom import MockIcomRadio
```

The package is importable under pytest because `pyproject.toml` sets
`[tool.pytest.ini_options] pythonpath = ["."]`.

## Distribution

`rigplane_testkit` is **not** part of the published `rigplane` wheel:
`[tool.hatch.build.targets.wheel]` packages only `src/rigplane` and
the legacy compatibility shim. It exists for development and testing only.

## Planned next

- Audio (RTP) port simulation alongside control and CI-V ports.
- Wiring the replayer's unsolicited timeline into the simulator so recorded
  sessions stream on their recorded cadence (the recorder, the pure
  `ReplayResponder` and the `RecordedAnswerTable` mount — `recorded_answers=`
  on both `CivSerialFake` and `MockIcomRadio` — are in place).
- Extraction of the CI-V dispatcher into `civ_model.py`, deferred from the
  initial move because `_dispatch_civ` and the radio state it mutates are
  methods and attributes of the single `MockIcomRadio` class.
