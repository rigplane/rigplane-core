# What the fake IC-7610 reproduces, and what it does not

The two fake IC-7610 personalities — `tests/testkit/ic7610_fakes.py:
ic7610_lan_fake` (LAN/UDP, a `MockIcomRadio`) and `ic7610_usb_fake`
(USB CI-V, a `CivSerialFake`) — let tests exercise the production stack
against a simulator that answers from a recording of the real bench radio.
This page is for contributors who write tests against the fakes and for
maintainers who must know what a green simulator run says about the real
radio. Every claim below is tied to the fixture file that holds the
recorded data, or to the test that fails when the claim stops being true.
The simulator source is `rigplane_testkit/icom/simulator.py`; the serial
fake is `rigplane_testkit/serial/civ_serial.py`.

## The two fakes

`ic7610_lan_fake(**kw)` builds `MockIcomRadio(recorded_answers=_answer_table("lan"),
**kw)` with `dual_receiver` defaulted True, so the LAN fake models MAIN/SUB
receiver select by default; a caller can still override the keyword
(`tests/testkit/ic7610_fakes.py: ic7610_lan_fake`). `ic7610_usb_fake(**kw)`
builds `CivSerialFake(model="IC-7610", recorded_answers=_answer_table("usb"),
**kw)` (`tests/testkit/ic7610_fakes.py: ic7610_usb_fake`). Both tables are
built by `RecordedAnswerTable.from_civ_pairs` from the per-transport rows of
`tests/fixtures/ic7610/civ_pairs.jsonl` (`tests/testkit/ic7610_fakes.py:
_answer_table`).

### Opt-in `MockIcomRadio` behaviours, off unless a test turns them on

Each keyword models one recorded behaviour and defaults off (or `None`),
so a plain `MockIcomRadio()` keeps the pre-mount behaviour. The opt-in
list, read from `rigplane_testkit/icom/simulator.py: MockIcomRadio.__init__`:

- `single_owner=False` — one owning session keyed by `(remote_addr, control
  my_id)`; a foreign conninfo while it is held is busy-rejected, and it is
  freed by the owner's token-remove or the CI-V stream owner's
  OpenClose(close). Pinned by
  `tests/testkit/test_simulator_renew_standby.py::test_second_login_keeps_single_owner_session`.
- `enable_audio=False` — bind the LAN audio-port listener; off, the status
  reply still advertises an audio port but nothing answers there. Pinned by
  `tests/testkit/test_simulator_audio.py::test_default_audio_port_answers_nothing`.
- `stateful_ptt=False` — PTT SET/GET round trip; off, `1C 00` GET always
  answers OFF and every SET NAKs. Pinned by
  `tests/testkit/test_simulator_stateful_ptt.py::test_default_off_get_answers_off_and_set_naks`.
- `capture_civ_frames=False` — record inbound CI-V frames addressed to the
  radio. Pinned by
  `tests/testkit/test_simulator_civ_capture.py::test_capture_off_by_default_records_nothing`.
- `civ_state_survives_unclean_end=False` — keep a dead session's CI-V stream
  frontier/gap list as orphan state for the next opener. Pinned by
  `tests/testkit/test_simulator_civ_orphan.py::test_default_off_starts_clean`.
- `dual_receiver=False` — native `07 D0/D1/D2` receiver select. Pinned by
  `tests/testkit/test_simulator_receiver_select.py::test_receiver_select_off_by_default`
  (off) and `::test_lan_fake_dual_receiver_default` (on for the LAN fake).
- `recorded_answers=None` — mount the recorded answer table before the
  final NAK. Pinned by
  `tests/testkit/test_simulator_recorded_answers.py::test_without_a_table_the_split_read_still_naks`.
- `fault_plan=None` — declarative per-port/per-direction drop/duplicate/
  delay/reorder/corrupt/truncate. Pinned by
  `tests/testkit/test_simulator_faults.py::test_no_plan_behaves_as_before`.
- `unsolicited_civ=False` — an autonomous transceive loop. This is NOT
  recorded radio behaviour: the real radio broadcasts `00`/`01` frames to
  `0x00`, the simulator's loop sends `03` (MOR-3310).

`refused_civ_commands` is an opt-in instance attribute (not a constructor
keyword) that NAKs a `(cmd, sub)` pair before any handler; pinned by
`tests/testkit/test_simulator_recorded_answers.py::test_refused_command_still_naks_over_the_table`.
`ptt_on_session_loss` (`"hold"`/`"release"`) matters only with
`stateful_ptt`; the release unkey is pinned by
`tests/testkit/test_simulator_stateful_ptt.py::test_release_unkeys_at_session_end`
and the hold by
`tests/testkit/test_simulator_stateful_ptt.py::test_hold_keeps_ptt_across_the_drop_until_a_client_unkeys`.

## The recorded data

`tests/fixtures/ic7610/README.md` documents every file; the summaries below
restate only what that file says.

- `civ_pairs.jsonl` — request/echo/reply rows per window (`probe`,
  `readonly_session`, `rx_write_restore`, `scope`, `vfo_select` on LAN;
  `usb_session` on USB), with `dir`, `frame`, `window`, `transport`, `t`.
- `control_windows.jsonl` — control-port packets other than pings/idle
  (`handshake`, `power_cycle`, `ethernet_loss_22s`, `ethernet_loss_83s`,
  `session_hold`).
- `audio_headers.jsonl` — first 20 audio packets per direction.
- `audio_stream.json` / `audio_rx_headers.jsonl` — the RX half of the LAN
  audio port (stereo 150 packets/s, 1388+1388+1136 per 20 ms burst).
- `idle_cadence.json` — ping/token-renew/audio-length statistics of a
  10-minute idle window.
- `conformance_ledger.json` — the KNOWN divergences of the simulators from
  the recording, each carrying `ticket: "MOR-3310"`, ratcheted by
  `tests/testkit/test_ic7610_conformance.py`.
- `retransmit_gaps.jsonl` — the CI-V sequence-gap retransmit behaviour,
  pinned by `tests/testkit/test_simulator_civ_gap.py`.

## Reproduced behaviours

Source cites the fixture file (and window) or the Linear ticket that holds
the bench evidence; the pinning test fails if the behaviour stops being
true.

| Behaviour | Source | Pinning test |
|---|---|---|
| LAN login → status/token/conninfo handshake (discovery, login reply, token ack, conninfo, status) | `control_windows.jsonl` `handshake` | `tests/testkit/test_ic7610_conformance.py::test_handshake_structure_matches_ledger` |
| Token renew answered with the recorded 0x40/0x05 layout; live token `00000000`, retired token `ffffffff`, exactly once | `idle_cadence.json` + MOR-3349 | `tests/testkit/test_simulator_renew_standby.py::test_live_renewal_reply_layout` |
| CI-V echo on LAN (byte-identical before every reply), no echo on USB | `civ_pairs.jsonl` (echo rows) | `tests/testkit/test_ic7610_conformance.py::test_lan_civ_echo_divergence` |
| Recorded answer classes (NAK vs data vs ACK) per recorded request, on LAN and USB | `civ_pairs.jsonl` | `tests/testkit/test_ic7610_conformance.py::test_lan_answer_classes_match_ledger` |
| Recorded table answers requests the native handlers NAK; native state wins, refusal hook NAKs first | `civ_pairs.jsonl` | `tests/testkit/test_simulator_recorded_answers.py::test_recorded_split_read_and_write_answer_from_the_table` |
| Single-owner session hold + busy reject (`0xFFFFFFFF`) | `control_windows.jsonl` `session_hold` | `tests/testkit/test_simulator_renew_standby.py::test_second_login_keeps_single_owner_session` |
| Drop / power-off notices: unsolicited `0x90` busy=0, then pings still answered, then CI-V DATA fenced | `control_windows.jsonl` `power_cycle`; MOR-2625/2627 | `tests/testkit/test_simulator_drop_session.py::test_drop_session_sends_one_notice_classified_session_free` |
| Power-off order: notice, ~0.02 s discovery grace, then silence | `control_windows.jsonl` `power_cycle` | `tests/testkit/test_simulator_drop_session.py::test_reboot_notice_then_discovery_grace_then_silence` |
| CI-V gap retransmit cadence: single/ranges request twice per round, ~0.1 s repeat, >=75 ms round spacing | `retransmit_gaps.jsonl` | `tests/testkit/test_simulator_civ_gap.py::test_lost_tracked_packet_gets_single_requests_then_fill` |
| Orphan CI-V stream state after an unclean end (inherit frontier + gap list, clean end/reboot/expiry clears) | MOR-3418 | `tests/testkit/test_simulator_civ_orphan.py::test_inherited_gap_ignores_behind_and_requests_missing` |
| Audio port (opt-in): recorded stereo burst cadence, transport handshake, zero retransmit requests, RX survives CI-V soft reconnect | `audio_stream.json` / `audio_rx_headers.jsonl` | `tests/testkit/test_simulator_audio.py::test_rx_stream_matches_recorded_layout_and_cadence` |
| Receiver select `07 D0/D1/D2` swaps per-receiver frequency/mode/DATA-mode/filter; `03`/`04`/`05`/`06`/`25`/`26` follow the selection | `civ_pairs.jsonl` `vfo_select` | `tests/testkit/test_simulator_receiver_select.py::test_recorded_vfo_select_replies_are_byte_identical` |
| Stateful PTT readback (opt-in): SET flips state, GET reads it, session-end unkey | MOR-3251 | `tests/testkit/test_simulator_stateful_ptt.py::test_stateful_set_and_get_round_trip` |
| Fault plans (drop/duplicate/delay/reorder/corrupt/truncate) | n/a (simulator knob) | `tests/testkit/test_simulator_faults.py::test_echo_precedes_every_reply_by_default` |
| Events: reboot, drop_session, keepalive-silence drive the production reconnect | `control_windows.jsonl` | `tests/testkit/test_simulator_events.py::test_reboot_mid_session_client_reconnects_with_new_token` |

The at-scale check behind the "answer classes" and "echo" rows is the
conformance replay through `ic7610_lan_fake` / `ic7610_usb_fake`
(`tests/testkit/test_ic7610_conformance.py`), which classifies each
replayed request's reply and ratchets against `conformance_ledger.json`.

## Deliberately different

The simulator accelerates one recorded constant so tests do not wait out a
real radio's hold.

- Session hold after a dead client: `rigplane_testkit/icom/simulator.py:
  _DEFAULT_KEEPALIVE_HOLD_S` is `0.5` s; the recorded IC-7610 holds the
  dead client's session for 83.4–85.9 s (MOR-3391, stated in the constant's
  comment). It is the default of the `keepalive_hold_s` keyword; the one
  slow timing test passes a near-real value.

The other timing constants are recorded values, not speed-ups: the CI-V gap
round period and 75 ms minimum spacing (`_CIV_GAP_RETRANSMIT_PERIOD_S`,
`_CIV_GAP_ROUND_MIN_SPACING_S`), the ~0.1 s drop-notice delay
(`_DROP_NOTICE_DELAY_S`), the ~0.02 s power-off grace window
(`_POWER_OFF_NOTICE_WINDOW_S`), and the 126–150 s orphan expiry
(`ORPHAN_CIV_STATE_EXPIRY_S`, MOR-3418). The `tests/fixtures/ic7610/README.md`
notes that recorded `t` timings are not asserted as values anywhere.

## Not reproduced

- **Scope.** The simulator does not answer `0x27` at all
  (`rigplane_testkit/icom/simulator.py: MockIcomRadio._dispatch_civ_native`
  carries a scope TODO and NAKs). The `scope_wave` ledger entry records that
  the real radio sends 5 complete `27 00` wave frames after wave-on and the
  simulator sends none; ratcheted by
  `tests/testkit/test_ic7610_conformance.py::test_scope_wave_decodes_and_cadence_matches_ledger`.
  Per-receiver scope frame cadence (MAIN 8.33 frames/s at speed mid, SUB
  29.4/s at speed fast, each following its receiver's scope speed) and the
  per-receiver scope-setting reads (`27 14/15/16/19/1A/1D/1F` with the
  receiver byte) are recorded on the bench but not modelled (MOR-3310).
- **Audio codec cadence.** The simulated RX stream is always the recorded
  stereo shape (150 packets/s, bursts of 1388+1388+1136 bytes every 20 ms);
  the mono `PCM_1CH_16BIT` cadence the radio accepts at conninfo (100
  packets/s, 1364+556 per 20 ms) is not modelled, and there is no codec
  negotiation in the simulator (MOR-3310).
- **TX, tuner, power actuation.** These are never actuated on the bench
  (bench rule), so they are never recorded and never modelled from
  recordings. The simulator models the PTT CI-V write/readback only
  (`stateful_ptt`), and the unrecorded `ptt_on_session_loss` choice is
  covered under both values by
  `tests/testkit/test_simulator_stateful_ptt.py::test_release_unkeys_at_session_end`
  and `::test_hold_keeps_ptt_across_the_drop_until_a_client_unkeys`.

The remaining known divergences are the per-command entries in
`tests/fixtures/ic7610/conformance_ledger.json` (LAN answer classes and the
`handshake_*` positional divergences, including the unmodelled `0xA8`
setup packet). They are listed in that file, not repeated here, and
ratcheted by `tests/testkit/test_ic7610_conformance.py`; the fix belongs to
the simulator under MOR-3310.

## How to extend

Record on the bench through the testkit recorder → redact (`python -m
rigplane_testkit.redaction_gate`) → add the conformance row/ledger entry →
teach the simulator → add the pinning test. The extraction pipeline and
trimming rules are in `tests/fixtures/ic7610/README.md`.
