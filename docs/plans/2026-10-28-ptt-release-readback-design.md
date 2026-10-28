# MOR-3818: Confirm Icom PTT release by a `1C 00` read-back — design

Date: 2026-10-28, revision 2. Status: design only; no code in this change. Scope: Icom CI-V radios, via shared runtime code only.
All behavioural claims are pinned to the simulator-based test plan in §8.

## 1. Summary

When Core releases PTT on an Icom CI-V radio, the unkey's reply is a bare `FB`/`FA` that names no command. An ambiguous
reply settles as `ActuationResult.UNCERTAIN`, the release stays owed, and a key-down 1–3 s later is refused although the
radio already receives (MOR-3810). This design confirms the release with ONE `1C 00` read-back inside the same
`CoreRadio.actuate` call, only when the unkey's answer is ambiguous. Because a `1C 00` reply carries no request id, it
is identified by EXCLUSION: a quiescence ledger observes every outbound `1C 00` READ (D2) and proves that, at dispatch
of the confirming read, no other `1C 00` read is pending or could still have a reply in flight; a LAN transport
duplicate drop (D6) removes byte-identical re-deliveries before they reach the CI-V pump. Anything that breaks the proof
fails closed: the release stays owed, and the authority's existing cadence of one retry per `RETRY_DELAY_S` carries it
to the first attempt dispatched at or after the ledger's horizon (§6). Key-down is unchanged (§5 D4). Implementation
splits into two independent PRs on main, neither stacked: PR-A (D6, LAN duplicate suppression) first, then PR-B (D1–D4,
quiescence ledger + release read-back); §10.

## 2. Problem and current behaviour

`CoreRadio.actuate` (`src/rigplane/runtime/radio.py: CoreRadio.actuate`) sends the unkey (`ptt_off`) at
`Priority.FORCE_RELEASE` (`src/rigplane/commands/commander.py: Priority`) with `wait_response=True` and settles on the
radio's answer, which `CivRuntime._execute_civ_raw` (`src/rigplane/runtime/_civ_rx.py: CivRuntime._execute_civ_raw`)
waits for up to `_civ_get_timeout` (`src/rigplane/runtime/radio.py: CoreRadio.__init__`, `min(timeout, 2.0)`), spent
from the send. The FORCE_RECEIVE branch returns `UNCERTAIN` on no answer (`TimeoutError`), on a reply that is not an ACK
shape (`parse_ack_nak(reply) is None`), or when another write's ACK/NAK was left unclaimed within one `_civ_get_timeout`
before this attempt (`src/rigplane/core/civ.py: CivRequestTracker.ack_unclaimed_at`) — a bare `FB`/`FA` names no
command, so it cannot be attributed to this unkey. Only otherwise: `ACCEPTED` on `FB`, `REJECTED` on `FA`.

In `src/rigplane/runtime/managed_tx_state.py: _settle`, an UNCERTAIN `FORCE_RECEIVE` keeps `release_plan` set, and
`reduce_managed_tx` REJECTS a `PttDown`/`TransmitOn` while `release_required`. The authority retries:
`ManagedTxAuthority._refresh_retry_locked` schedules `_retry_due = clock() + retry_delay_seconds` (default
`RETRY_DELAY_S = 1.0`, `src/rigplane/runtime/managed_tx_authority.py: RETRY_DELAY_S`) and
`ManagedTxAuthority._process_due` reduces a `RetryForceReceive`. Hence the MOR-3810 beta report: a key-down 1–3 s after
a release is refused although the radio already receives.

Yaesu confirms the release by reading transmit state back after the unkey (`src/rigplane/backends/yaesu_cat/radio.py:
actuate`, FORCE_RECEIVE branch calling `read_ptt`); so does the rigctld client
(`src/rigplane/backends/rigctld_client/radio.py: actuate`, calling `_read_ptt`). `CoreRadio.read_transmit_state`
(`src/rigplane/runtime/radio.py`) already issues exactly the needed `1C 00` read; Icom is the only family left on the
ambiguous ACK.

Goal: in the ambiguous-ACK case only, confirm the release with ONE `1C 00` read-back, for every Icom CI-V radio, through
shared runtime code, WITHOUT EVER reporting "released" while the radio still transmits. Key-down does not wait for the
confirmation; the owed window only gets shorter because the confirmation happens inside the same `actuate` call.

## 3. Why the first attempt failed

The first attempt (core#4377 history, commits `35201406a` and `ef5b2b6c1`) took two steps. `35201406a` ("confirm PTT
release by read-back") made the ambiguous branch call `read_transmit_state(priority=..., is_current=...)` and return
`ACCEPTED` iff `reading.value is False and reading.verified_readback`; `ef5b2b6c1` ("accept a PTT read-back only when it
is fresh and exact") then tried to prove freshness with abandon markers (`CivRequestTracker._response_unclaimed_at`, a
per-`(command, sub)` stamp written when a response waiter was unregistered unanswered) and LAN packet-number high-water
marks (`CivRuntime._rx_seq_high`, `_received_frame_seq`, `received_after`). Two review rounds kept finding races,
because a `1C 00` reply carries NO request id. The two races, verbatim:

1. A broadcast/malformed `1C 00` frame completes read A without marking A abandoned; then A's real reply arrives during
   release read B (the serial transport numbers packets on receipt, so packet numbers prove nothing there).
2. The packet-number baseline was captured before the command was queued, so with read A still active a replayed packet
   looks fresh to read B.

Race 1 falls out of the code: `CivRequestTracker._matches` and `CivRequestTracker.resolve` (`src/rigplane/core/civ.py`)
match a response waiter on `(command, sub, receiver, data_prefix)` only — no `to_addr`, no length or value check — and
`CivRuntime._route_civ_frame` (`src/rigplane/runtime/_civ_rx.py`) accepts frames from the radio address with `to_addr`
in `(CONTROLLER_ADDR, 0x00)`. So a broadcast `1C 00` frame completes A's waiter normally; the abandon marker was written
only on an *unanswered* unregister, so A is never marked, and A's real reply later matches B's identical key. Race 2:
the ef5b2b6c1 baseline (`_rx_seq_high`) was captured before queueing, so a replayed packet above the stale baseline
passed `received_after` even while an earlier read was still active. Packet numbers are unusable FOR FRESHNESS in
principle: the LAN transport re-requests missing packets every `RETRANSMIT_PERIOD = 0.1` s, giving up after 4 tries
(`src/rigplane/core/transport.py: IcomTransport._retransmit_loop`), so an old reply can be delivered late with an old
sequence number; `IcomTransport._handle_data_packet` queues every data packet while `IcomTransport._record_rx_seq`
ignores a duplicate (`delta == 0`) without dropping it, so duplicates and replays reach the CI-V pump; and serial
transports synthesize packet numbers on receipt (`SerialCivTransport.receive_packet`,
`src/rigplane/backends/icom7610/drivers/serial_session.py`). D5/D6 state the only use packet numbers keep.

One more fact: today `CoreRadio.read_transmit_state` accepts any observation whose source is in `RADIO_READBACK_SOURCES`
(`src/rigplane/core/tx_observation.py`), which contains `"civ_unsolicited"` — and `CivRuntime._observation`
(`src/rigplane/runtime/_civ_rx.py`) assigns `civ_unsolicited` to any frame with `to_addr == 0x00`, so a broadcast
`1C 00` frame would today be accepted as a verified read-back. The strict shape check D1 extracts already exists in the
same function (§5 D1).

## 4. Safety argument

Only a reading of RECEIVE can cause harm: a false "released" clears the release debt and admits a key-down over a radio
that still transmits. A stale TX reading can only keep the release owed (UNCERTAIN), which is safe.

An RX reading proves the release only if the reply was generated after the radio executed the unkey. The radio executes
CI-V commands in arrival order, so the reply to a `1C 00` request dispatched AFTER the unkey is such a reply — provided
the reply we accept is really the answer to THAT request. The whole problem is therefore to identify "the reply to
request R" without a request id; we identify it by EXCLUSION: R's reply is the only valid directed `1C 00` reply that
can arrive in R's window, because at R's dispatch nothing else was pending or possibly in flight and nothing else was
dispatched during R's window. Everything that breaks the exclusion is observable on this side (dispatches, completions,
timeouts, generation changes), so the design fails closed on exactly those events.

## 5. Design

### D1. Strict reply classification — ONE shape check

The condition under which `CivRuntime._observation` assigns the `poll_response` source is today written inline
(`src/rigplane/runtime/_civ_rx.py: CivRuntime._observation`): `to_addr == CONTROLLER_ADDR`, `from_addr == radio_addr`,
command `0x1C`, sub `0x00`, data length exactly 1, value `0x00` or `0x01`. The design extracts exactly that condition
into ONE pure function, `is_directed_ptt_reply(frame, radio_addr)`, homed in `src/rigplane/core/civ.py` next to
`CivRequestTracker` and the ledger, and gives it three callers:

- `CivRuntime._observation`, replacing the inline condition (no behaviour change there);
- the ledger's completion classification in the read branch of `CivRuntime._execute_civ_raw`, which already holds the
  completing frame as `pending.result()` — `replied-valid` iff `is_directed_ptt_reply` passes on it, else
  `replied-invalid` (on the raw-transaction path the completing frame is `RawCivTransactionResult.frame`);
- the `note_reply` gate in `CivRuntime._route_civ_frame` (D2): an inbound frame counts as a valid directed `1C 00`
  reply iff the same function passes.

Placement: the check is done by the READER on the completed frame, plus the ledger note — not by a predicate inside
`CivRequestTracker._matches`, a static matcher shared by every command's response waiters and by ACK-sink retirement
(via `CivRequestTracker.resolve`); threading a per-command predicate through it would change shared semantics for all
commands to serve one. The reader already classifies the completed frame by source, so requiring
`source == "poll_response"` there IS the D1 check. `resolve` still completes the waiter on a key-matching-but-invalid
frame — the read ends promptly as unverifiable instead of waiting out the answer window — and the completing frame goes
to the ledger at `note_end` (D2), which records the non-clean outcome: an invalid completion ends the read as
unverifiable and counts against D2's rule (c). `CoreRadio.read_transmit_state` must accept `poll_response` only, never
`civ_unsolicited` (today it accepts any source in `RADIO_READBACK_SOURCES`).

### D2. A ledger of `1C 00` reads ("quiescence ledger")

One small, clock-injected, pure class — home: `src/rigplane/core/civ.py` next to `CivRequestTracker`; one instance owned
by `CivRuntime` (`src/rigplane/runtime/_civ_rx.py: CivRuntime`). Unlike `CivRequestTracker` (which calls
`time.monotonic()` directly), the ledger takes the clock in its constructor so it is unit-testable without sleeps.

A LEDGER REQUEST is a CI-V frame with command `0x1C`, sub `0x00` and EMPTY data — a read, the shape
`CoreRadio.read_transmit_state` and `_request_authoritative_ptt_read` build (`build_civ_frame(radio_addr,
CONTROLLER_ADDR, 0x1C, sub=0x00)`, `src/rigplane/runtime/radio.py`). The `ptt_on`/`ptt_off` writes are NOT ledger
requests: they carry one data byte (the `ptt_on = [0x1C, 0x00, 0x01]` / `ptt_off = [0x1C, 0x00, 0x00]` keys of the
`[commands]` table in `rigs/ic7610.toml` and `rigs/ic7300.toml`; builders `src/rigplane/commands/ptt.py`), take the
ACK/NAK branch of `CivRuntime._execute_civ_raw` (`CivRuntime._civ_expects_response` returns False for a `1C 00` frame
with data, so the write registers a plain ACK waiter and settles on `FB`/`FA`), and must never enter the ledger —
otherwise the unkey itself would poison the read-back that follows it.

Coverage: every outbound CI-V frame passes `CivRuntime._wrap_civ` at exactly three send sites —
`CivRuntime._send_civ_frame_now` and the two branches of `CivRuntime._execute_civ_raw` (fire-and-forget and
response-waiting). The commander path funnels here because `IcomCommander` is constructed over
`CivRuntime.execute_civ_raw` (`CivRuntime.start_worker`); the raw path because `CivRuntime.execute_civ_transaction`
sends via `_send_civ_frame_now`. Serial backends ride the same sites (`src/rigplane/backends/_icom_serial_base.py`; the
serial session driver accepts runtime `_civ_rx._wrap_civ` output,
`src/rigplane/backends/icom7610/drivers/serial_session.py: _wrap_civ_frame`). Hooks:

- `note_dispatch(frame)` immediately BEFORE the `transport.send_tracked(...)` call at each site, for ledger requests
  only (the empty-data test above) — the baseline is bound at wire dispatch, not at queue time; a send that raises
  counts as dispatched (fail closed: the frame may have reached the wire).
- `note_end(outcome)` in the existing `finally` of each request; `outcome` is one of `replied-valid` (D1 passed),
  `replied-invalid` (key-matching but D1-failing completion), `timeout`, `nak`, `cancelled`, `send-failed`.
- `note_reply(frame)` once per inbound frame passing `is_directed_ptt_reply` at `CivRuntime._route_civ_frame` — the
  inbound counterpart that makes rule (c) below checkable and fails closed on a reply matching no pending request.

Per request the ledger tracks dispatch time, whether it is still clean, and end time; globally it tracks the pending set
and `horizon` — the monotonic time before which a late reply from an earlier request may still arrive. Rules:

- A request is CLEAN iff (a) at its dispatch no other ledger request was pending and `now >= horizon`; (b) no other
  ledger request was dispatched during its window; (c) it ended by receiving exactly one valid reply (D1).
- A request that is not clean (timeout, cancel, NAK, malformed/foreign completion, overlap, dispatched inside the
  horizon, generation change) sets `horizon = max(horizon, end_time + Q)`, `Q = PTT_REPLY_QUIESCENCE_S = 2.0`. A valid
  reply observed while NO ledger request is pending (a late or foreign twin) also sets `horizon = max(horizon, now +
  Q)`: an unaccounted reply means the bus state is not understood.
- Induction: a clean request's reply is its own, because at dispatch nothing else was pending or possibly in flight and
  nothing else was sent during the window. A request dispatched inside a horizon or overlapping another is never
  trusted, even if it receives a valid-looking reply, and it extends the horizon.
- `Q` is ONE FURTHER ANSWER WINDOW and stays a policy bound, not a physical latency guarantee: a reply that has not
  arrived within one extra answer window after its request was abandoned is treated as lost. The value equals the
  default `_civ_get_timeout` (`src/rigplane/runtime/radio.py: CoreRadio.__init__`, `min(timeout, 2.0)` with default
  `timeout = 5.0`). Code facts: a request's answer window is fully spent before abandonment (timeout path in
  `CivRuntime._execute_civ_raw`), so a late reply necessarily arrives after the request's end; LAN re-delivery of a lost
  reply is abandoned after 4 × `RETRANSMIT_PERIOD` ≈ 0.4 s (`IcomTransport._retransmit_loop`). The retry arithmetic
  against this horizon is redone in §6; the residual of a first delivery delayed beyond `Q` is in §9.
- Reset: the ledger clears on `CivRuntime.advance_generation` (`src/rigplane/runtime/_civ_rx.py`), which advances
  `_civ_epoch` via `CivRequestTracker.advance_generation` and fails all pending waiters; the reconnect loops call it
  (`src/rigplane/runtime/radio_reconnect.py: watchdog_loop`, `reconnect_loop`), and `CivRuntime.retire_managed_tx_port`
  calls it on managed-port retirement. A new session cannot receive the old session's frames: `CivRuntime._civ_rx_loop`
  is started per session with a `source_generation` (`CivRuntime.start_pump`), `_route_civ_frame` drops state-cache
  updates on a generation mismatch, and `CivRequestTracker.resolve` refuses stale-generation settlements.

Overlap discipline: `IcomCommander` (`src/rigplane/commands/commander.py: IcomCommander`) already serializes the normal
path — one worker task, one item at a time, priority-ordered — so overlap can arise only from the raw-transaction path
(`CivRuntime.execute_civ_transaction`, including `CivRuntime.request_authoritative_ptt_read` and `write_managed_ptt`)
and from raw user sends, which bypass the commander. The ledger OBSERVES those dispatches and fails closed; it adds no
lock. `CivRuntime._ptt_read_lock` / `_active_ptt_read` cover only `request_authoritative_ptt_read`; the commander path
(`read_transmit_state` → `_send_civ_expect` → `IcomCommander` → `_execute_civ_raw`) never takes that lock, so it cannot
be the exclusion mechanism. The ledger is the single mechanism; the managed-observer read keeps its own lock for its
own job (`CivRuntime._ptt_read_is_current`).

### D3. `read_transmit_state` (single entry, all callers)

`CoreRadio.read_transmit_state` keeps its signature plus optional `priority` and `is_current` keyword arguments (the
shape the abandoned first attempt already used), so the unkey's read-back rides the unkey's `Priority.FORCE_RELEASE` and
its currency predicate through `CoreRadio._send_civ_expect`. It returns `TxStateReading(..., verified_readback=True)`
(`src/rigplane/core/tx_observation.py: TxStateReading`) only when the ledger says the request was clean AND the reply
passed D1 (`poll_response` source). Otherwise `TxStateReading(value=None, failure=...)` with distinct tags: `"timeout"`
and `"read-error"` (existing), `"unverifiable-provenance"` (existing tag, now also covering a D1-failing or
ledger-unclean completion), and `"not-quiescent"` (new): if the ledger is not quiescent when the read would be
dispatched, the read is NOT sent and does not wait — it returns `"not-quiescent"` at once. That is fail closed without
stalling the release: the authority keeps retrying once per `RETRY_DELAY_S` while the release stays owed
(`ManagedTxAuthority._process_due` / `_refresh_retry_locked`), and the first retry dispatched at or after the horizon is
eligible to be clean (§6).

Callers of `read_transmit_state` in `src/` and the effect on each:

- `src/rigplane/validation/hardware.py` (conformance check): unchanged call shape (new kwargs default); stricter
  acceptance means a broadcast-shaped answer no longer counts as verified — the conformance fakes are updated (§8).
- `CoreRadio.actuate` FORCE_RECEIVE ambiguous branch: new caller (D4).
- `src/rigplane/backends/yaesu_cat/observations.py` and the protocol member `TransmitStateReadable.read_transmit_state`
  (`src/rigplane/core/radio_protocol.py`): the Yaesu method is a different one; optional kwargs keep both compatible.

### D4. `CoreRadio.actuate` FORCE_RECEIVE

Only the ambiguous branch changes (no answer, non-ACK reply, or an `ack_unclaimed_at` within one `_civ_get_timeout`):
call `read_transmit_state` once with the unkey's `priority` and `is_current`; return `ACCEPTED` only if `reading.value
is False and reading.verified_readback`. Every other outcome — value `True`, any failure tag including
`"not-quiescent"`, or an exception — stays `UNCERTAIN`, keeping the release owed
(`src/rigplane/runtime/managed_tx_state.py: _settle`). A read-back NEVER produces `REJECTED`; the unkey's own `FA`
remains the only `REJECTED`. The clean-ACK path (`FB` with no unclaimed marker) is unchanged and takes no read-back. The
unkey write itself is not a ledger request (D2); what its late `FB`/`FA` does to a read-back in flight is row A10.

Key-down (`PTT_ON`/`TRANSMIT_ON`) is unchanged: fire-and-forget send, `ACCEPTED`, no read-back, no wait; admission is
still refused upstream while `release_required` (`reduce_managed_tx`). The refusal-text work from core#4377 already
covers the user-facing wording: `_PTT_RELEASING` ("the previous transmission is still being released") in
`src/rigplane/web/handlers/control.py`, pinned by `tests/test_web_managed_ptt_rejection.py`. This design does not touch
it; it only shortens how long that refusal is true.

### D5. What this design explicitly does NOT do

- No packet-number or sequence use for FRESHNESS or attribution (§3 shows why that cannot work). Sequence numbers are
  used ONLY for exact-duplicate suppression at the LAN transport (D6); the ledger never sees a sequence number.
- No marker or probe commands on the bus; the only new traffic is the one `1C 00` read-back in the ambiguous branch.
- No new locks; the ledger is observational and fails closed.
- No change to `ManagedTxState`, `reduce_managed_tx`, the effect lane, or `ManagedTxAuthority` (including its timing
  constants).
- No per-radio branch, no `if model == ...`. The only profile input is whether the profile's CI-V command map carries
  the transceiver-status read (the `get_transceiver_status = [0x1C, 0x00]` key of the `[commands]` table, present in
  `rigs/ic7610.toml` and `rigs/ic7300.toml`; lookup via `RadioProfile.supports_command`,
  `src/rigplane/profiles/__init__.py`). A profile without that mapping skips the read-back and the ambiguous branch
  stays UNCERTAIN — exactly today's behaviour (fail closed). Independently, the managed-TX arming probe
  (`src/rigplane/runtime/radio.py: CoreRadio._managed_tx_provider_answers_ptt`) already declines to arm managed TX on a
  rig that does not answer `1C 00`.

### D6. LAN duplicate suppression (exact re-deliveries only)

Facts (`src/rigplane/core/transport.py`): `IcomTransport._handle_data_packet` calls `_record_rx_seq(seq)` for every
ptype-0x00 packet with `seq != 0` and then queues the packet for the consumer; `_record_rx_seq` returns early on
`delta == 0` (a duplicate) WITHOUT dropping anything, so a byte-identical second copy reaches the CI-V pump. Gaps are
tracked in `rx_missing` (seq → retry count); `_retransmit_loop` asks the radio to re-send them every
`RETRANSMIT_PERIOD = 0.1` s and gives up on an entry after 4 tries.

Design — a MINIMAL exact-duplicate drop on the CI-V transport only:

- A new per-instance flag `IcomTransport._drop_rx_duplicates` (default False) is set True where the CI-V transport is
  built — the two `h._civ_transport = IcomTransport()` construction sites, in `_connect_once` and in `soft_reconnect`
  (`src/rigplane/runtime/_control_phase.py: _connect_once`, `soft_reconnect`; per-instance attribute assignment, like
  the existing `_discard_data_packets`) — so the control and audio transports keep today's behaviour byte-for-byte. The
  control transport discards data packets after setup anyway (`_discard_data_packets` is toggled in `_connect_once` once
  the handshake completes, where the conninfo/token callbacks `_on_conninfo_notice` / `_on_token_reply` are also
  registered), and the audio transport feeds the audio stream; none needs the drop. Scope chunks ride the CI-V queue
  (consumed by `CivRuntime._civ_rx_loop`, `_shed_scope_backlog`); an exact duplicate chunk is a byte-identical
  re-delivery and is dropped with everything else.
- With the flag set, `_handle_data_packet` keeps a bounded recent-delivered window (FIFO set of the last 256 delivered
  sequence numbers; wrap-safe because 256 ≪ 2^15, the forward half of the 16-bit space `_record_rx_seq` itself uses). A
  ptype-0x00 packet with `seq != 0` whose sequence number is already in the window is dropped right after
  `_record_rx_seq`, BEFORE the conninfo/token/discard/queue branches; otherwise its sequence enters the window and the
  packet passes exactly as today. A packet that fills a gap in `rx_missing` is a FIRST delivery — `rx_missing` holds
  only skipped (never delivered) sequence numbers, so a gap filler cannot be in the window — and is passed on once; its
  later retransmit twin is dropped.

This is duplicate suppression, a transport hygiene rule, NOT a freshness proof: the ledger never sees a sequence
number, and D6 says nothing about WHEN the dropped frame was generated. Serial has no duplicates to drop:
`SerialCivTransport.receive_packet` wraps each link frame exactly once under a synthesized, strictly incrementing
`seq`, and `SerialCivTransport.start_retransmit_loop` is a no-op — D6 is LAN-only; serial relies on the ledger alone.

## 6. Time budget

Bounding constants: `ATTEMPT_TIMEOUT_S = 3.0` (`src/rigplane/runtime/managed_tx_authority.py: ATTEMPT_TIMEOUT_S`, one
attempt's deadline, set in `ManagedTxAuthority._execute`); `_civ_get_timeout = min(timeout, 2.0)`
(`src/rigplane/runtime/radio.py: CoreRadio.__init__`, the unkey's answer window, spent from the send in
`CivRuntime._execute_civ_raw`); `TX_READ_DEADLINE_SECONDS = 0.3` (`src/rigplane/core/tx_observation.py:
TX_READ_DEADLINE_SECONDS`, the outer deadline `read_transmit_state` passes to `_send_civ_expect`).

Worst case inside one `actuate`: the unkey waits out a full `_civ_get_timeout` (2.0 s at shipped defaults) before the
branch is known ambiguous, then the read-back costs at most 0.3 s plus commander queueing and the `_civ_min_interval`
pacing gap (explicitly not charged to the answer window in `CivRuntime._execute_civ_raw`). Total ≈ 2.3 s plus pacing —
inside the 3.0 s attempt budget at shipped defaults. If the budget ever does not fit, nothing new happens:
attempt-deadline handling is unchanged, the read-back's `is_current` stops matching, the release stays owed, and the
authority retries after `RETRY_DELAY_S`.

Retry-vs-horizon arithmetic, redone for `Q = 2.0`. A non-clean read ends at `end_time` and sets
`horizon = end_time + 2.0`. The settlement reaches the authority no earlier than `end_time`; `_refresh_retry_locked`
then schedules `_retry_due = clock() + RETRY_DELAY_S`, i.e. no earlier than `end_time + 1.0` — INSIDE the horizon. That
first retry re-sends the unkey (a write, not a ledger request); if its answer is again ambiguous, the read-back
consults the ledger, finds it non-quiescent, and returns `"not-quiescent"` without dispatching: UNCERTAIN, release still
owed. The cadence is the one the code runs today: `_process_due` clears `_retry_due = None` as it reduces the
`RetryForceReceive`, and `_refresh_retry_locked` — re-run after every settlement in `ManagedTxAuthority._execute` —
arms `clock() + RETRY_DELAY_S` again whenever `_release_is_retryable_locked()` still holds, so a still-owed release
keeps being retried once per `RETRY_DELAY_S` until it confirms. The SECOND retry is therefore due no earlier than
`end_time + 2.0 = horizon`, and commander queueing plus the `_civ_min_interval` pacing gap push the actual dispatch
later still. Convergence: the release confirms on the first retry that dispatches at or after the horizon — at shipped
defaults the second retry — or EARLIER on any retry whose re-sent unkey draws a clean `FB`, exactly as today. Net
effect: the same retry cadence as today, at most one extra `RETRY_DELAY_S` tick.

## 7. Two-read regression matrix

Read A is an earlier `1C 00` read; read B is the release read-back from the ambiguous unkey branch. "Reading" is B's
`TxStateReading`; "Result" is what `CoreRadio.actuate` then returns. All rows run against the §8 simulators.

| # | What happened to A | Ledger state at B's dispatch | B's reading | Result | Reason |
|---|---|---|---|---|---|
| A1a | A timed out; its real reply would arrive during B, inside the horizon | horizon = A.end + Q not yet reached | `value=None`, `"not-quiescent"` (B not sent) | UNCERTAIN | D2 horizon; D3 gate |
| A1b | A timed out; its real reply arrives during B, after the horizon | B clean; A's late reply completes B | A's stale value, verified | ACCEPTED iff value RX | Induction on Q — the policy-bound case (§9) |
| A2 | A broadcast/malformed `1C 00` frame completes A (key matches, D1 fails); A's real reply arrives during B | A ended non-clean (`replied-invalid`); horizon covers B | `"not-quiescent"` | UNCERTAIN | D1 foreign completion feeds D2; closes found race 1 |
| A3a | A duplicated/replayed reply packet (LAN) arrives while A is still pending | B is never dispatched while A is pending | `"not-quiescent"` | UNCERTAIN | D2 rule (a) overlap; closes found race 2 |
| A3b | Byte-identical duplicate of a cleanly-ended A's reply overtakes B's own reply (LAN) | B clean; the twin reuses A's already-delivered sequence number | B unaffected — the twin is dropped before queueing | per B's own reply | D6 drop at transport; residual only for a twin with a never-delivered sequence number (§9) |
| A4 | A (a poller read) still pending when B would be dispatched | overlap: B not clean; if forced out anyway (raw path), BOTH A and B are untrusted and both extend the horizon | `"not-quiescent"` (release path never sends B) | UNCERTAIN | D2 rules (a)/(b); D3 gate |
| A5 | A ended by NAK | horizon = A.end + Q | `"not-quiescent"` inside horizon; clean after | UNCERTAIN inside horizon | D2 non-clean end |
| A6 | A answered cleanly long ago | quiescent | own reply, verified | ACCEPTED iff RX — the happy path | D2 clean induction |
| A7 | Two valid replies arrive for one B | a re-delivered copy (same sequence number) is dropped at transport; a genuine SECOND emission is observed with no pending request → horizon set for the NEXT read | first reply trusted iff B clean | ACCEPTED iff first reply RX and B clean | D6 + D2 rule (c) / `note_reply` orphan rule |
| A8 | A stray valid reply arrives just before B's dispatch | orphan reply sets horizon = now + Q | `"not-quiescent"` | UNCERTAIN | D2 orphan-reply rule |
| A9 | Generation change between A and B (`CivRuntime.advance_generation`) | ledger reset; stale-generation frames cannot settle B (`CivRequestTracker.resolve` generation check) | B judged in the new generation only | per B's own clean outcome | D2 reset rule |
| A10 | The unkey's own `FB`/`FA` arrives while B (a read) is pending | B is a read: `FB` is declined by B's NAK-only waiter and parked in the orphan ACK backlog (`CivRequestTracker.resolve`); `FA` settles that waiter | `FB`: B unaffected, keeps waiting for its reply; `FA`: `CommandRejectedError`, `note_end(nak)` | `FB`: per B's own reply; `FA`: UNCERTAIN, horizon extended | read branch of `CivRuntime._execute_civ_raw` + D2 |
| SAFETY | A stale RX reply (from a timed-out or foreign-completed A) is delivered while the radio is actually transmitting | horizon (or overlap) covers B's dispatch | `"not-quiescent"` | UNCERTAIN — release stays owed | The case the design exists for; only escapes are A1b and the shrunk A3b (§9) |

After this revision the only cases not excluded are A1b — a FIRST delivery arriving later than `Q` after its request
ended — and the shrunk A3b — a re-emitted reply under a sequence number never delivered; both need an external
re-keying between unkey and read-back to do harm. The owner-level backstop is unchanged: `_settle` clears `release_plan`
only when the FORCE_RECEIVE settlement is ACCEPTED (`src/rigplane/runtime/managed_tx_state.py: _settle`), so every
unresolved doubt keeps the release owed and the retry cadence running. Cell A3b's "B unaffected" is the one safety
statement not self-contained in PR-B: the twin is dropped by PR-A (D6), so it holds only once PR-A is merged; every
other row is enforced by the ledger and the D1 gate alone.

## 8. Simulator coverage and test plan

Tests are failing-first: each new test lands red against current code before the implementation. Coverage matrix:
IC-7610 LAN, IC-7610 serial, IC-7300 serial/USB — the new actuator tests are parametrized over all three columns.

- LAN (IC-7610): `rigplane_testkit/icom/simulator.py: MockIcomRadio` with `stateful_ptt=True` (the `1C 00` GET answers
  simulated PTT; SET records `ptt_set_writes`), via the `tests/conftest.py` fixtures (`mock_radio`, `connected_radio`).
  `rigplane_testkit/icom/faults.py: FaultRule` covers delay (`delay_s`/`jitter_s`), drop
  (`drop_probability`/`drop_every`), duplicate (`duplicate_probability`/`duplicate_delay_s`), malformed
  (`corrupt_probability`/`corrupt_bytes`, `truncate_probability`/`truncate_to`); `MockIcomRadio.civ_standby` /
  `refused_civ_commands` cover silence and NAK.
- Broadcast / malformed / stray `1C 00` frames (both transports): inject straight into the runtime's ingress,
  `await radio._civ_runtime._route_civ_frame(frame, generation=radio._civ_epoch)` — the existing
  `civ_ptt_reply_frame` / `answer_ptt_reread` pattern of `tests/integration/_ptt_reread_fixtures.py`. It works on a
  serial-backed radio too: the serial backends (`_IcomSerialRadioBase`, subclassed by `Ic7300SerialRadio` and
  `Icom7610SerialRadio`, `src/rigplane/backends/_icom_serial_base.py`) run the same `CivRuntime` and pump
  (`CoreRadio.__init__`), and `SerialCivTransport.receive_packet` wraps each link frame in the envelope the pump
  unwraps — `_route_civ_frame` is the shared ingress; the frame must carry the radio's address and the current
  `_civ_epoch`.
- Serial (`Icom7610SerialRadio`, `Ic7300SerialRadio`): `rigplane_testkit/serial/civ_serial.py: CivSerialFake` (stateful
  `1C 00` GET/SET, `from_profile`) via the opener seam `CivSerialFake.open_serial_connection` (as in
  `tests/testkit/test_serial_release_retry_cadence.py`). Delay rows: `reply_latency_s` (+ `delay_identity_answer`);
  drop rows: `CivSerialFake.silence`; duplicate/broadcast/malformed rows: the `_route_civ_frame` injection above —
  `CivSerialFake` has no such hooks and needs none.
- Transport (pure unit tests in `tests/test_transport.py`, next to `TestRxSequence` / `TestRetransmitLoop`): a
  duplicate data packet with an already-delivered sequence number reaches the consumer exactly once; a retransmit
  filling a real `rx_missing` gap is delivered once (its later twin dropped); window wrap-around in the 16-bit sequence
  space; the drop is inert unless `_drop_rx_duplicates` is set.
- Observation layer: `tests/tx_observation_fakes.py` (`ScriptedCivLink`, `ScriptedLanTransport`,
  `TxObservationHarness`): the vocabulary gains a broadcast (`to_addr == 0x00`, `civ_unsolicited`) answer that must FAIL
  verification under D1.

Existing tests that will change (PR-B only): `tests/test_icom_managed_tx_actuator.py` — the MOR-2860 ambiguity pins
(`test_a_late_answer_to_a_timed_out_unkey_does_not_settle_the_next`,
`test_an_unkey_after_a_dropped_write_sink_stays_uncertain`, pinning `ack_unclaimed_at` behaviourally) and
`test_force_receive_settles_on_the_radio_answer`: the ambiguous branch now issues a read-back — a clean RX answer
confirms the release, a poisoned one (per the matrix) keeps UNCERTAIN; new rows parametrized over the three columns.
And `tests/tx_observation_fakes.py` + `tests/contracts/test_tx_observation_conformance.py` — INV-13 (`CIV_NON_ANSWERS`,
pinned by `test_a_civ_read_is_not_satisfied_by_an_ack_echo_or_misaddressed_frame`) covers ACK, setter-echo and
foreign-address answers; it gains the broadcast shape, which must no longer yield `verified_readback=True`.

Unchanged by this plan: `tests/test_raw_civ_transaction.py` (`test_refused_managed_ptt_write_leaves_the_next_read_authoritative`,
`test_authoritative_ptt_read_waits_out_its_own_refusal` exercise the overlap/abandonment the ledger observes and must
keep passing as-is) and `tests/test_web_managed_ptt_rejection.py` (the `_PTT_RELEASING` pin and
`test_key_down_is_admitted_once_the_previous_release_settles` stay green). No `rigplane_testkit/` file changes.

New pure unit tests for the ledger (new `tests/test_ptt_quiescence_ledger.py`, injected clock, no sleeps): clean round
trip; a `ptt_on`/`ptt_off` write is NOT a ledger request; dispatch inside horizon; overlap in both orders; timeout /
cancel / NAK / invalid-completion ends and horizon extension (`is_directed_ptt_reply` decides); the unkey's `FB`
parked, `FA` ends the read as `nak` (A10); orphan reply extends horizon; horizon expiry admits a clean request;
generation reset; a raising send counts as dispatched.

## 9. Residual risk

- `Q = 2.0` is a policy bound, not a measured radio latency. A `1C 00` reply whose FIRST delivery is delayed more than
  `Q` past the end of the request it answers (A1b; same shape via A2/A5 after the horizon) is indistinguishable from B's
  own reply — nothing on the wire names the request. If such a stale RX reply also overtakes the radio's real answer and
  the radio was re-keyed (mic/VOX/other client) in between, B could confirm a release while the radio transmits. The
  design shrinks this to the over-`Q` tail: retransmit assistance ends after 4 × `RETRANSMIT_PERIOD` ≈ 0.4 s
  (`IcomTransport._retransmit_loop`), and every in-window case (A1a, A2, A3a, A4, A5, A8, A10) fails closed.
- A3b after D6: a byte-identical duplicate that reuses the already-delivered sequence number is dropped at the
  transport. That drop is PR-A's contribution — PR-B alone leaves the twin reaching the CI-V pump (§7). What remains is
  a re-emitted reply carrying a sequence number never delivered, which is indistinguishable from a first delivery;
  whether a radio emits byte-identical re-issues under a new sequence number is not something this codebase can answer,
  so the residual stands, LAN-only, with its harm precondition (an external re-keying between unkey and read-back). An
  optional hardening (clean reads also extend the horizon) is open question 1.
- The ledger observes only this runtime's `1C 00` reads. A second CI-V controller's reads are invisible to it: a reply
  directed at the other controller's address fails the `to_addr in (CONTROLLER_ADDR, 0x00)` gate in
  `CivRuntime._route_civ_frame` — and D1's directed check — so it is ignored, not treated as an orphan. What the ledger
  cannot see is that controller's WRITES; a re-keying by any external actor is the residual the first bullet describes.

## 10. Files expected to change

Repo budget: hard ceiling 10 files and 1000 changed lines per PR (soft threshold 6 files / 600 lines). The work splits
into TWO independent implementation PRs, both based on main, neither stacked on the other — a merge of a base PR kills a
stacked child. PR-A merges first: it is smaller and lower-risk, and PR-B's safety statement for matrix cell A3b (§7)
depends on PR-A being merged. PR-A works with or without PR-B.

### PR-A — LAN exact-duplicate suppression (D6)

| File | Symbols | Est. lines |
|---|---|---|
| `src/rigplane/core/transport.py` | `IcomTransport._drop_rx_duplicates` flag, recent-delivered window, exact-duplicate drop in `_handle_data_packet` | +40 |
| `src/rigplane/runtime/_control_phase.py` | set `_drop_rx_duplicates` at the two `_civ_transport` construction sites (`_connect_once`, `soft_reconnect`) | +2 |
| `tests/test_transport.py` | D6 duplicate-drop unit tests (§8) | +60 |

PR-A total: 3 files, ~100 changed lines.

### PR-B — quiescence ledger + release read-back (D1–D4)

| File | Symbols | Est. lines |
|---|---|---|
| `src/rigplane/core/civ.py` | `is_directed_ptt_reply` pure function; new ledger class next to `CivRequestTracker` (clock-injected; `note_dispatch` / `note_end` / `note_reply`; clean/horizon rules) | +160 |
| `src/rigplane/runtime/_civ_rx.py` | `CivRuntime.__init__` (own the ledger), `_observation` (use `is_directed_ptt_reply`), `_send_civ_frame_now` + both `_execute_civ_raw` branches (dispatch/end hooks), `execute_civ_transaction` (end hook), `_route_civ_frame` (`note_reply`), `advance_generation` (reset) | +100 |
| `src/rigplane/runtime/radio.py` | `CoreRadio.read_transmit_state` (priority/is_current, `poll_response`-only, ledger consult, `"not-quiescent"`), `CoreRadio.actuate` (ambiguous-branch read-back) | +70 |
| `tests/test_ptt_quiescence_ledger.py` (new) | injected-clock unit table (§8) | +170 |
| `tests/test_icom_managed_tx_actuator.py` | updated pins + matrix rows parametrized over IC-7610 LAN, IC-7610 serial, IC-7300 serial (§8) | +120 |
| `tests/tx_observation_fakes.py` | broadcast-shaped answer in the scripted vocabulary + harness support (§8) | +40 |
| `tests/contracts/test_tx_observation_conformance.py` | INV-13 parametrization gains the broadcast shape (§8) | +10 |

PR-B total: 7 files, ~670 changed lines.

Neither PR touches `managed_tx_state.py`, `managed_tx_authority.py`, `managed_tx_effect_service.py`, the web handlers,
any profile TOML, or anything under `rigplane_testkit/` (§8 drives every matrix row with existing hooks).

## 11. Open questions

1. Should a CLEAN read also extend the horizon (end + Q)? With D6 it would cover only the shrunk A3b residual (a
   re-emitted reply under a never-delivered sequence number arriving within `Q` of A's end), but it makes
   `"not-quiescent"` more likely under active background PTT polling. Owner decision; the default keeps the horizon
   non-clean-only.
2. Should `CivRuntime.request_authoritative_ptt_read` publish only ledger-clean replies? It relies on its own
   binding/currency checks (`CivRuntime._ptt_read_is_current`); not required for the release path.
3. Is `Q = 2.0` right long-term, or should it derive from `_civ_get_timeout` (whose default it equals)? Kept a named constant.

Explicitly NOT an open question here: whether key-down should wait for the release confirmation. That stays an owner
decision and is not implemented; key-down remains refused while a release is owed.
