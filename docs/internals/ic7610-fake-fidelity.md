# What the fake IC-7610 reproduces, and what it does not

The two fake IC-7610 personalities — `tests/testkit/ic7610_fakes.py:
ic7610_lan_fake` (LAN/UDP, a `MockIcomRadio`) and `ic7610_usb_fake`
(USB CI-V, a `CivSerialFake`) — let tests exercise the production stack
against a simulator that answers from a recording of the real bench radio.
This page is for contributors who write tests against the fakes and for
maintainers who must know what a green simulator run says about the real
radio. Behaviours backed by the recording carry their fixture source and
the test that fails when they stop being true. The simulator source is
`rigplane_testkit/icom/simulator.py`; the serial fake is
`rigplane_testkit/serial/civ_serial.py`.

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

The opt-in constructor keywords, read from `rigplane_testkit/icom/simulator.py:
MockIcomRadio.__init__`. Each defaults off (or `None`), so a plain
`MockIcomRadio()` keeps the pre-mount behaviour.

- `single_owner=False` — one owning session keyed by `(remote_addr, control
  my_id)`; a foreign conninfo while it is held is busy-rejected, and it is
  freed by the owner's token-remove or the CI-V stream owner's
  OpenClose(close). Pinned by
  `tests/testkit/test_simulator_renew_standby.py::test_second_login_keeps_single_owner_session`
  (hold + busy reject), `::test_production_disconnect_remove_ends_session`
  and `::test_production_soft_disconnect_close_ends_session` (the two free
  paths).
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
  reorder/corrupt/truncate (a simulator knob, not recorded). Pinned by
  `tests/testkit/test_simulator_faults.py::test_no_plan_behaves_as_before`
  (off) and the fault tests below (on).
- `drop_rate=0.0` — probability of dropping each unsolicited frame (only
  with unsolicited_civ) (a simulator knob). Pinned by
  `tests/test_mock_icom_session.py::test_drop_rate_suppresses_unsolicited_frames`.
- `civ_inherited_fills_per_round=0` / `civ_inherited_drop_unanswered_rounds=0`
  — pace an inherited orphan's fills, from one capture (MOR-3418 w50 b).
  Pinned by
  `tests/testkit/test_simulator_civ_orphan.py::test_inherited_one_fill_per_round`
  and `::test_inherited_drop_unanswered_range`.
- `unsolicited_civ=False` — an autonomous transceive-style loop that pushes
  unsolicited freq-report frames to the CI-V client; a simulator scenario,
  not recorded radio behaviour.

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

## Reproduced behaviours (recorded)

Source is the fixture file (and window) or the Linear ticket that holds the
bench evidence; the pinning test fails if the behaviour stops being true.

| Behaviour | Source | Pinning test |
|---|---|---|
| LAN handshake: discovery/login/token exchange matches the recording through step 7; the entries in the ledger diverge (see the ledger) | `control_windows.jsonl` `handshake` | `tests/testkit/test_ic7610_conformance.py::test_handshake_structure_matches_ledger` |
| Token renew answered with the recorded 0x40/0x05 layout; live token `00000000`, retired token `ffffffff`, exactly once | `idle_cadence.json` + MOR-3349 | `tests/testkit/test_simulator_renew_standby.py::test_live_renewal_reply_layout`, `::test_retired_token_renewal_rejected_exactly_once` |
| CI-V echo on LAN (byte-identical before every reply), none on USB | `civ_pairs.jsonl` (echo rows) | `tests/testkit/test_ic7610_conformance.py::test_lan_civ_echo_divergence`, `::test_usb_replay_divergences_match_ledger` |
| Recorded reply classes (NAK / data / ACK) per recorded request, on LAN and USB | `civ_pairs.jsonl` | `tests/testkit/test_ic7610_conformance.py::test_lan_answer_classes_match_ledger`, `::test_usb_replay_divergences_match_ledger` |
| Recorded table answers requests the native handlers NAK; native state wins; the refusal hook NAKs first | `civ_pairs.jsonl` | `tests/testkit/test_simulator_recorded_answers.py::test_recorded_split_read_and_write_answer_from_the_table`, `::test_native_state_wins_over_the_recording`, `::test_refused_command_still_naks_over_the_table` |
| Single-owner session hold + busy reject (`0xFFFFFFFF`) | `control_windows.jsonl` `session_hold` | `tests/testkit/test_simulator_renew_standby.py::test_second_login_keeps_single_owner_session` |
| Drop / power-off notice: one unsolicited `0x90` busy=0 ~0.1 s after the drop; pings still answered on both ports; CI-V DATA from the dropped client fenced | `control_windows.jsonl` `power_cycle`; MOR-2625/2627 | `tests/testkit/test_simulator_drop_session.py::test_drop_session_sends_one_notice_classified_session_free`, `::test_pings_answered_on_both_ports_after_drop`, `::test_civ_data_from_dropped_client_gets_no_answer` |
| Power-off order: notice, discovery grace, then silence | `control_windows.jsonl` `power_cycle` | `tests/testkit/test_simulator_drop_session.py::test_reboot_notice_then_discovery_grace_then_silence` |
| CI-V gap retransmit: single/ranges request twice per round, ~0.1 s repeat, >=70 ms round spacing | `retransmit_gaps.jsonl` | `tests/testkit/test_simulator_civ_gap.py::test_lost_tracked_packet_gets_single_requests_then_fill`, `::test_round_rate_matches_recording` |
| Orphan CI-V stream state after an unclean end (inherit frontier + gap list; clean end/reboot/expiry clears) | MOR-3418 | `tests/testkit/test_simulator_civ_orphan.py::test_inherited_gap_ignores_behind_and_requests_missing`, `::test_clean_logout_clears_orphan`, `::test_reboot_clears_orphan`, `::test_unclaimed_expiry_starts_clean` |
| Audio port (opt-in): recorded stereo burst cadence, zero retransmit requests, RX survives CI-V soft reconnect | `audio_stream.json` / `audio_rx_headers.jsonl` | `tests/testkit/test_simulator_audio.py::test_rx_stream_matches_recorded_layout_and_cadence`, `::test_no_retransmit_requests_from_radio_despite_client_gap`, `::test_rx_keeps_flowing_across_civ_soft_reconnect` |
| Receiver select `07 D0/D1/D2` | `civ_pairs.jsonl` `vfo_select` | `tests/testkit/test_simulator_receiver_select.py::test_recorded_vfo_select_replies_are_byte_identical` |

The at-scale check behind the "answer classes" and "echo" rows is the
conformance replay through `ic7610_lan_fake` / `ic7610_usb_fake`
(`tests/testkit/test_ic7610_conformance.py`), which classifies each
replayed request's reply and ratchets against `conformance_ledger.json`.

## Simulator-only behaviours (no recording behind them)

- Stateful PTT readback (opt-in, UNRECORDED — TX is never actuated on the
  bench): SET flips state, GET reads it, and `ptt_on_session_loss="release"`
  unkeys at session end. Pinned by
  `tests/testkit/test_simulator_stateful_ptt.py::test_stateful_set_and_get_round_trip`
  and `::test_release_unkeys_at_session_end`.
- Fault plans (drop/duplicate/reorder/corrupt/truncate). Pinned by
  `tests/testkit/test_simulator_faults.py::test_drop_bounded_to_next_packet`,
  `::test_duplicate_sends_two_identical_copies`,
  `::test_corrupt_flips_bytes_on_the_wire`,
  `::test_truncate_cuts_reply_on_the_wire`, `::test_reorder_permutes_replies`.
- Scheduled events (`reboot`, `drop_session`, `keepalive_silence`) driving the
  production reconnect. Pinned by
  `tests/testkit/test_simulator_events.py::test_reboot_mid_session_client_reconnects_with_new_token`,
  `::test_keepalive_silence_longer_than_watchdog_fires_link_down`,
  `tests/testkit/test_reconnect_storm.py::test_session_drop_with_notice_recovers_with_one_login`.
- The RX audio payload is synthetic: a byte ramp (`simulator_audio.py:
  _PCM_RAMP`; no on-air samples are committed), pinned by
  `tests/testkit/test_simulator_audio.py::test_rx_stream_matches_recorded_layout_and_cadence`.
- Writes to the selected/unselected frequency/mode commands
  (`05`/`06`) land on the currently selected receiver —
  native, not in the recorded `vfo_select` window. Pinned by
  `tests/testkit/test_simulator_receiver_select.py::test_frequency_write_while_sub_selected_lands_on_sub`,
  `::test_mode_write_while_sub_selected_lands_on_sub`,
  `::test_data_mode_flag_follows_receiver_select`.
- The unselected receiver defaults to a fresh-band setup — a simulator
  choice, not a recorded factory state
  (`simulator.py: MockIcomRadio.__init__` `_unselected_*`).

## Deliberately different

The simulator accelerates one recorded constant so tests do not wait out a
real radio's hold.

- Session hold after a dead client: `simulator.py: _DEFAULT_KEEPALIVE_HOLD_S`
  is 0.5 s; the recorded IC-7610 holds the dead client's session for
  83.4-85.9 s (MOR-3391, stated in the constant's comment). It is the
  default of the `keepalive_hold_s` keyword.

The remaining timing constants are not speed-ups, but they are simulator
constants that approximate a recorded timing (named in each one's comment),
recorded values: the ~0.1 s drop-notice delay
(`_DROP_NOTICE_DELAY_S`), the CI-V gap round period and round spacing
(`_CIV_GAP_RETRANSMIT_PERIOD_S`, `_CIV_GAP_ROUND_MIN_SPACING_S` — 75 ms,
slightly under the recorded 77 ms minimum), the power-off grace
(`_POWER_OFF_NOTICE_WINDOW_S`, ~0.02 s — the power-off-order test checks a
grace before silence), and the orphan expiry
(`ORPHAN_CIV_STATE_EXPIRY_S`, 140 s inside the recorded 126-150 s bracket,
MOR-3418).

## Not reproduced

- **Scope wave and per-receiver scope cadence.** There is no native scope
  handler: `0x27` NAKs natively (`simulator.py: MockIcomRadio._dispatch_civ_native`,
  scope TODO), and the simulator sends no scope wave (plain MockIcomRadio()) — the recording holds 5
  complete `27 00` wave frames after wave-on, the simulator none (`scope_wave`
  ledger entry, ratcheted by
  `tests/testkit/test_ic7610_conformance.py::test_scope_wave_decodes_and_cadence_matches_ledger`).
  The 13 recorded scope-setting reads ARE answered, from the recorded LAN
  table mounted by `ic7610_lan_fake` (replayed by
  `::test_lan_answer_classes_match_ledger`), not natively. Per-receiver scope
  frame cadence and per-receiver scope-setting reads are not modelled
  (MOR-3310).
- **RX audio codec.** The RX stream is always the recorded stereo shape; the
  mono `PCM_1CH_16BIT` RX cadence the radio accepts at conninfo is not
  modelled, and the simulator does not negotiate the RX codec (MOR-3310).
- **TX, tuner and power actuation.** These are never actuated on the bench
  (bench rule), so they are never recorded and never modelled from
  recordings. The simulator's TX-adjacent pieces — the opt-in stateful PTT
  readback, the TX audio sink (`simulator_audio.py`, pinned by
  `tests/testkit/test_simulator_audio.py::test_tx_sink_records_client_frames_and_numbering`),
  the native `14 0A` RF-power SET and static SWR/ALC readbacks — are
  simulator choices.
- **The ledger.** The known divergences are the entries in the ledger in
  `tests/fixtures/ic7610/conformance_ledger.json` (`handshake_8` through
  `handshake_12`, plus `scope_wave`); see the file and the ratcheting test
  `tests/testkit/test_ic7610_conformance.py`. The fix belongs to the
  simulator under MOR-3310.

## How to extend

Record on the bench through the testkit recorder → redact (`python -m
rigplane_testkit.redaction_gate`) → add the conformance row/ledger entry →
teach the simulator → add the pinning test. The extraction pipeline and
trimming rules are in `tests/fixtures/ic7610/README.md`.
