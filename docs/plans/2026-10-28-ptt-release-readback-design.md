# MOR-3818: Confirm Icom PTT release by a `1C 00` read-back — design

Date: 2026-10-28, revision 5. Status: design of record for the implemented groundwork and activation split.
Scope: shared Icom CI-V runtime. Implementation source and existing tests were read on
branch `codex/MOR-3818-ptt-release-activation`.
This documentation revision runs no tests or builds, establishes no hardware validation, and does not change
recorded acceptance decisions.
PR sizes and stacking below are user-supplied delivery facts, separate from source inspection.

## 1. Summary

After an ambiguous unkey answer, `src/rigplane/runtime/radio.py: CoreRadio.actuate` attempts one `1C 00` read-back
and accepts release only for verified RX without a failure. A receive-owned owed-replies FIFO records empty-data
PTT reads sent from Core's controller address to its radio. Its entry is created UNWRITTEN before send and gains
its lifetime timestamp when send finishes. A confirming read requires an empty FIFO and expired orphan quarantine,
then exact dispatch-entry ownership of its completing frame. RX additionally passes a 0.2-second settle window.
`src/rigplane/runtime/_civ_rx.py: CivRuntime._civ_rx_loop` suppresses byte-identical PTT reply packets on LAN only,
for `answer_window + Q` from accepted receipt (4.0 seconds at constructor defaults).

This is conditional attribution, not a request ID or an absolute no-false-release guarantee. Bounded lifetime,
ordered command delivery/execution, and the residual limits of same-address foreign traffic remain explicit (§3–§4,
§9). Orphan quarantine and RX settling enforce observable interference checks, with the lone-foreign-reply limit.

Delivery is split: [core#4388](https://github.com/rigplane/rigplane-core/pull/4388) is groundwork, based on `main`,
921 changed lines in seven files. [core#4389](https://github.com/rigplane/rigplane-core/pull/4389) activates
`CoreRadio.actuate` with the actuator matrix and real API regression, approximately 565 changed lines in three files.
It is stacked on #4388 and must be retargeted to `main` after #4388 merges (§10–§11).

## 2. Problem and resulting behaviour

`src/rigplane/runtime/radio.py: CoreRadio.actuate` sends `ptt_off` at `Priority.FORCE_RELEASE`, awaiting an answer.
A timeout, non-ACK shape, or recent `ack_unclaimed_at` makes the answer ambiguous. That branch now attempts read-back
if the profile supports it. A clean `FB` remains ACCEPTED without reading; an unambiguous `FA` remains REJECTED.
Bare answers identify no command; `src/rigplane/core/civ.py: CivRequestTracker.ack_unclaimed_at` tracks ambiguity.
Key-down still uses a fire-and-forget write, without this read-back.

`src/rigplane/runtime/managed_tx_state.py: _settle` clears `release_plan` on accepted FORCE_RECEIVE settlement;
other outcomes retain it. `reduce_managed_tx` refuses positive TX intent from RX while release is required. Thus a
missing unkey answer could keep key-down refused after the radio returned to RX. Read-back addresses that ambiguous
branch without changing the reducer's admission rules.

`src/rigplane/runtime/radio.py: CoreRadio.read_transmit_state` now requires the exact directed `poll_response`
shape and FIFO entry identity. The broader `src/rigplane/core/tx_observation.py: RADIO_READBACK_SOURCES` still includes
`civ_unsolicited`; this Icom reader's stricter acceptance does not redefine other backends' sources.

## 3. Attribution problem and assumptions

A response match alone cannot identify its request. `src/rigplane/core/civ.py: CivRequestTracker._matches` matches
command/subcommand and optional receiver/data prefix, without exact directed PTT shape. Consequently invalid or
broadcast completion of A's waiter must not remove A's reply debt: its genuine reply may still arrive during B.
Receive-owned accounting is independent of waiter completion (§5 D2).

Sequence freshness is not attribution. `src/rigplane/core/transport.py: IcomTransport._handle_data_packet` and
`IcomTransport._record_rx_seq` track incoming sequence gaps without holding later replies for ordered delivery.
`src/rigplane/backends/icom7610/drivers/serial_session.py: SerialCivTransport.receive_packet` synthesizes a wrapping
16-bit envelope counter on receipt. Neither identifies the request that generated a reply.

**L — bounded reply lifetime.** Replies not received by `written_at + answer_window + Q` are treated as lost.
Here `written_at` is the entry's `dispatched_at` set by `note_written`, not the start of an awaited send;
`answer_window = _civ_get_timeout` and `Q = PTT_REPLY_QUIESCENCE_S = 2.0`
(`src/rigplane/core/civ.py: PttReplyLedger.note_written`, `PttReplyLedger._expire`, `PTT_REPLY_QUIESCENCE_S`).
UNWRITTEN entries have no expiry. Equality remains owed; expiration requires strictly greater age.
Constructor defaults give four seconds from write completion (`src/rigplane/runtime/radio.py: CoreRadio.__init__`,
default `timeout=5.0`, `_civ_get_timeout=min(timeout, 2.0)`). This is a policy assumption, not measured latency.

A harmful violation needs no external re-key: A samples RX, Core keys, the next unkey is lost, then A's delayed first
reply arrives beyond its lifetime during B. That stale RX can be accepted. LAN cache retention is anchored at accepted
receipt, for the same full duration; exact twins within that retention are suppressed (§5 D6), but L remains a limit.

**F′ — ordered command delivery and execution.** Each link delivers Core's commands in send order and the radio
executes them in receive order. This is an assumption, not an enforcement guarantee:
`src/rigplane/core/transport.py: IcomTransport.send_tracked` guards local submission only. Reply arrivals can reorder.
A later C paying B's debt is benign under F′ because both reads follow the unkey send (§4).

**E — same-address interference checks and residual.** `src/rigplane/runtime/_civ_rx.py:
CivRuntime._note_ptt_dispatch` only accounts Core-addressed requests; D1 filters replies addressed elsewhere.
Unexplained valid replies with no debt increment the orphan counter and quarantine future reads. D3 also rejects RX
if that count changes between its baseline and the end of settling. These checks enforce observed interference before
B and during settling. They cannot establish exclusive use of E0: a lone foreign same-address reply can pay B while
Core's own reply is lost or silent throughout settling. A reply paying other concurrent debt also does not increment
the orphan counter. Retain these limits rather than claiming either complete exclusivity or no enforcement.

## 4. Conditional safety argument

B is dispatched only with an empty owed FIFO and no active orphan quarantine. Earlier written reads have been paid
or expired under L; an UNWRITTEN read cannot silently expire. B's entry exists before send. A valid reply subsequently
paying B comes from B or a later C under the lifetime, ordering, and foreign/replay exclusions. Both samples follow
the unkey send under F′. If unkey was lost, these samples still observe the resulting radio state: TX retains release
debt. Reply reordering alone cannot turn these samples into pre-unkey stale RX.

`src/rigplane/runtime/_civ_rx.py: CivRuntime._route_civ_frame` stores the exact paying frame in the oldest entry.
`src/rigplane/runtime/radio.py: CoreRadio.read_transmit_state` requires `context.entry.answered_by is reply`, D1,
and `poll_response`. A C frame charged to B can pass B's identity check; that check establishes ledger ownership,
not its generating request. A waiter whose frame paid another entry fails closed, potentially impairing liveness.
After RX settling, session/currency and orphan checks must still pass (§5 D3).

The mechanism covers owed silent reads, malformed/broadcast waiter completion, missing/cancelled waiters, polls
whose sinks disappear, pre-dispatch orphans, queue/pacing interference and reuse of a runtime across reconnect.
It does not prove L or F′, identify a lone foreign reply paying B, or reject every replay. These are acceptance limits,
not implementation omissions to conceal (§9).

## 5. Design of record

### D1. Strict directed PTT reply classification

`src/rigplane/core/civ.py: is_directed_ptt_reply` requires recipient E0, sender `radio_addr`, command `0x1C`,
subcommand `0x00`, exactly one byte, value `0x00` or `0x01`. The core helper uses literal `0xE0` without an upward
commands import. `tests/test_ptt_quiescence_ledger.py: test_directed_shape_and_controller_address` pins agreement
with `CONTROLLER_ADDR` and rejects invalid shapes.

The helper governs `src/rigplane/runtime/_civ_rx.py: CivRuntime._observation`, `CivRuntime._route_civ_frame`,
`CivRuntime._civ_rx_loop`, and `src/rigplane/runtime/radio.py: CoreRadio.read_transmit_state`. Broadcast remains
`civ_unsolicited`, cannot pay debt and cannot verify the reader. Generic tracker matching remains broader than D1.

### D2. Receive-owned FIFO and write-completion lifetime

`src/rigplane/core/civ.py: OwedReplyEntry` contains `dispatched_at: float | None` and `answered_by: CivFrame | None`.
`PttReplyLedger` uses an injected monotonic clock and owns the FIFO, orphan count and quarantine deadline.
`src/rigplane/runtime/_civ_rx.py: CivRuntime._ptt_reply_ledger` lazily creates one ledger using `_civ_get_timeout`.

- `PttReplyLedger.note_dispatch` creates an UNWRITTEN entry (`dispatched_at=None`) only for empty-data `1C 00`.
  `CivRuntime._note_ptt_dispatch` additionally requires destination `radio_addr` and source `CONTROLLER_ADDR`.
  PTT writes carry data and create no debt; foreign-radio/controller requests create none.
- One wrapper, `CivRuntime._send_ptt_accounted`, accounts immediately before invoking `send_tracked`.
  `CivRuntime._send_civ_frame_now` calls it inside its send task; both branches of `CivRuntime._execute_civ_raw`
  call the same wrapper. This covers direct transactions, awaited commands and fire-and-forget reads.
- While `send_tracked` is pending, UNWRITTEN debt does not expire, even during a long serial drain.
  `PttReplyLedger.note_reply` can still pop that oldest UNWRITTEN entry and store the exact frame.
- On send completion, `PttReplyLedger.note_written` stamps `dispatched_at` once. A transport `CommandError`
  is treated as pre-wire refusal and `discard` removes its retained debt. The wrapper's `finally` also calls
  `note_written` on that discarded object; it does not restore debt. Every other failure or cancellation stamps
  retained debt fail closed, since transmission may have occurred. No guessed-delivery rollback applies.
- `CivRuntime._route_civ_frame` calls `note_reply` exactly once for every D1-valid frame, before generation-based
  routing returns or tracker settlement. It first expires written entries, pops the oldest surviving entry and
  stores `answered_by`. With no owed entry it increments ORPHAN and sets `quarantine_until=now+Q`.
  Invalid/broadcast frames and ACK/NAK do not pay debt.
- `PttReplyLedger._expire` removes written entries only strictly after `dispatched_at + answer_window + Q`;
  UNWRITTEN entries survive. `eligible` expires first and requires no debt plus `now >= quarantine_until`.
  Dispatch, reply and eligibility operations evaluate expiry. Normal paid replies do not renew quarantine.

Timeout, NAK, invalid completion, sink removal and caller return do not pay debt. There is no request-finalizer debt
removal. This separation is exercised by `tests/test_ptt_quiescence_ledger.py:
test_poll_debt_outlives_waiters_and_sinks`, including actual `wait_response=False` and `expect="none"` paths.

`src/rigplane/runtime/_civ_rx.py: CivRuntime.advance_generation`, `CivRuntime.retire_managed_tx_port` and
`CivRuntime.stop_pump` do not reset the ledger/cache. `_route_civ_frame` accounts even stale-generation valid traffic
before rejecting it for current waiter/state settlement. A replacement pump's generation is not a wire request ID.
Retention covers reuse of one runtime, not process/new-runtime replacement (§9).

### D3. Reader ownership and RX settle window

`src/rigplane/runtime/radio.py: CoreRadio.read_transmit_state` retains the no-argument API and accepts keyword-only
`priority=Priority.NORMAL`, `is_current=None`. After connection checking, an ineligible ledger returns
`TxStateReading(value=None, failure="not-quiescent")` immediately without queueing or sending.
It records the runtime, epoch, transport and orphan-count baseline before sending.

`src/rigplane/runtime/_civ_rx.py: _PttReadContext` wraps the predicate and stores this invocation's entry.
`CoreRadio._send_civ_expect` passes it through `CivRuntime._send_civ_raw`; the commander preserves the callable
(`src/rigplane/commands/commander.py: IcomCommander.send`, `IcomCommander._loop`). The direct executor also carries it.
`CivRuntime._note_ptt_dispatch` rechecks eligibility at the final accounting seam after queueing/drain/pacing, then
assigns `context.entry`. Guard, append and send invocation have no intervening await. `_PttNotQuiescent` maps to
`not-quiescent`; callable currency checks do not themselves append or remove debt.

Acceptance cumulatively requires that entry, exact `answered_by is reply`, D1 and decoded `poll_response`.
Missing/wrong ownership or shape returns `unverifiable-provenance`; timeout and command errors retain their failure
outcomes. A timed-out sent read stays owed until reply or expiry.

For RX only, `CoreRadio.read_transmit_state` waits `CoreRadio._PTT_READBACK_SETTLE_S = 0.2` after its answer.
It then rechecks runtime identity, epoch, transport identity, connection and the caller predicate; a false check returns
`read-error`. Predicate exceptions retain their propagation semantics. A changed orphan count returns `unverifiable-provenance`. An extra valid reply without debt is thus
caught even after B's answer has arrived. TX readings take no settle delay. The window observes interference; it is
not another query or proof against a lone foreign reply whose counterpart stays silent (§3 E).

### D4. FORCE_RECEIVE activation

`src/rigplane/runtime/radio.py: CoreRadio.actuate` attempts read-back once only on timeout/non-ACK/ambiguous-answer
branches, at FORCE_RELEASE priority with the same currency predicate. It gates the attempt on
`self._profile.supports_command("get_transceiver_status")` (`src/rigplane/profiles/__init__.py:
RadioProfile.supports_command`); no model-name branch is used. The command is declared in
`rigs/ic7610.toml: commands` and `rigs/ic7300.toml: commands` alongside PTT writes.

Verified `value is False` with no failure yields ACCEPTED. TX, missing value or failure remains UNCERTAIN.
Read-back exceptions derived from `Exception` are debug-logged and remain UNCERTAIN; cancellation propagates.
Unsupported read-back also remains UNCERTAIN. Clean FB sends no read; unambiguous FA stays REJECTED. Ambiguous FA
can be confirmed by RX; read-back itself never returns REJECTED. Key-down remains fire-and-forget.

### D5. Boundaries

MOR-3818 changes attribution and ambiguous-release confirmation, with no new marker command, polling suspension,
per-model actuator or new authority admission policy. Wire bytes in D6 serve only exact replay suppression.

#### Stuck-TX backstop

Existing MOR-3537 provides a separate stuck-TX backstop:
`src/rigplane/runtime/managed_tx_authority.py: RadioTxProbe`, `ManagedTxAuthority._radio_tx_origin_locked`,
`ManagedTxAuthority._radio_tx_refusal_locked`, and
`src/rigplane/runtime/managed_tx_composition.py: ManagedTxComposition.radio_tx_since` consult observed radio TX
when Core holds and owes none. Fresh TX reported after release refuses every TX start with `radio_tx_unreleased`
or `radio_tx_unowned`, according to origin. `ManagedTxAuthority._transition_locked` handles `force_off` independently;
it remains ungated. This refusal path does not automatically re-unkey. It depends on observed TX and cannot make a
false read-back verdict impossible or replace §9's residuals.

### D6. LAN-only whole-packet duplicate suppression

`src/rigplane/runtime/_civ_rx.py: CivRuntime._civ_rx_loop` parses packet frames and applies the cache only when
`isinstance(receiving_transport, IcomTransport)` and a D1-valid PTT reply is present. The key is the WHOLE raw packet,
including header. Accepted receipt stamps time. An identical twin at age `<= answer_window + Q` is dropped before raw
delivery, ledger accounting or routing; it does not renew the timestamp. Expiry requires age strictly greater than
that duration. Packets without D1-valid PTT bypass caching; mixed packets are suppressed as a whole.

Cache state survives pump restart within the runtime. There is no sequence-only key, exclusion of zero, count-based
eviction or scope-traffic eviction. A legitimate identical LAN packet in the retention window may be dropped,
costing liveness. Distinct cached packets consume memory proportional to traffic within that window.

Serial deliberately bypasses this cache and uses ledger/settle enforcement only.
`src/rigplane/backends/icom7610/drivers/serial_session.py: SerialCivTransport.__init__` initializes `ping_seq=0` for
each adapter; `SerialCivTransport.receive_packet` increments it modulo 65536 when synthesizing envelopes. A new adapter
can therefore reproduce a previous envelope for a legitimate reply. Serial replay protection must not be inferred
from these synthetic bytes. A3b/A3c are LAN-only (§7–§9).

## 6. Time budget and retry convergence

Verified defaults: `src/rigplane/runtime/radio.py: CoreRadio.__init__` caps `_civ_get_timeout` at 2.0 seconds;
`src/rigplane/core/tx_observation.py: TX_READ_DEADLINE_SECONDS` is 0.3 seconds;
`CoreRadio._PTT_READBACK_SETTLE_S` is 0.2 seconds; and
`src/rigplane/runtime/managed_tx_authority.py: ATTEMPT_TIMEOUT_S` is 3.0 seconds, `RETRY_DELAY_S` is 1.0 second.
The unkey answer window plus read deadline plus RX settle is `2.0 + 0.3 + 0.2 = 2.5 < 3.0` seconds.

That arithmetic is not a hard actuation bound: unkey queue/drain/pacing and send completion precede its answer wait
(`src/rigplane/runtime/_civ_rx.py: CivRuntime._execute_civ_raw`). The read's 0.3-second deadline includes its queue
and execution wait (`CivRuntime._send_civ_raw`, `src/rigplane/commands/commander.py: IcomCommander.send`), while its
RX settle follows that read wait (`CoreRadio.read_transmit_state`). Preserve the authority deadline/currency checks;
queue costs can consume the nominal 0.5-second margin.

One silent written read ceases blocking only strictly after its write-completion timestamp plus lifetime; an
UNWRITTEN read remains blocking until send completion/failure/cancellation stamps it, pre-wire refusal discards it,
or a valid reply pays it. An orphan supplies its own Q quarantine. Answered polls pay debt without renewing it.
`src/rigplane/runtime/managed_tx_authority.py: ManagedTxAuthority._process_due` clears the due retry before execution;
`ManagedTxAuthority._refresh_retry_locked` arms a retry from current time plus the delay while release is retryable.
Convergence is on the first otherwise-eligible retry obtaining verified RX, or sooner on clean FB, not necessarily
“the second retry.” Continuous overlap or continuing loss offers no unconditional liveness guarantee.

`src/rigplane/core/transport.py: IcomTransport._retransmit_loop` sends before incrementing/removing missing counters:
counts 0 through 4 yield five request rounds. Retransmission scheduling does not establish a maximum reply lifetime.

## 7. Two-read regression and residual matrix

A is an earlier read; B confirms ambiguous unkey. This matrix summarizes source scenarios and conditional results,
not test execution. Existing coverage is indexed in §8.

| ID | Sequence / state | Result / interpretation |
|---|---|---|
| A1a | A silent and owed, including UNWRITTEN | B not sent; not-quiescent, UNCERTAIN |
| A1b | A sampled RX; Core keys; unkey lost; first A reply beyond L pays B | Stale RX may ACCEPT; lifetime residual |
| A2 | Invalid/broadcast completion ends A's waiter | Debt survives; B refused until valid payment or expiry |
| A3a | A owed when B attempted | B refused; arriving valid reply pays A |
| A3b, LAN only | Answered A's exact packet repeats during B within lifetime retention | Twin dropped, including seq zero / scope traffic |
| A3c, LAN only | A twin arrives after Q but within answer_window + Q from receipt | Twin still dropped; no shorter-cache gap |
| A4 | Poll A's keyed sink removed | Receive-owned debt still blocks B |
| A5 | NAK/cancel/non-CommandError send failure | Debt remains until valid reply or expiry; pre-wire CommandError discards |
| A6 | Empty FIFO, no quarantine, own valid B answer | RX ACCEPTED after settle; TX UNCERTAIN |
| A7 | Extra valid emission after B's answer | Cached LAN twin drops; otherwise no debt means ORPHAN; RX fails if seen before settling ends |
| A8 | Valid orphan before B's final dispatch guard | No B send; Q quarantine |
| A9 | Generation/transport changes while A owed | Debt retained; old valid arrival accounts before generation rejection |
| A10 | Late unkey FB/FA while B waits | Neither pays data debt; cannot verify RX |
| E checks | Orphan before dispatch or unexplained extra reply before settle ends | B refused or RX unverifiable-provenance |
| E0 residual | Lone foreign E0 reply pays B; own reply silent through settle | May ACCEPT; same-address attribution remains limited |
| FIFO-LAN benign | Later C's post-unkey sample pays B first | Can ACCEPT under F′; wrong-entry waiter otherwise fails closed |
| POLL | Silent earlier read, polling continues, then valid replies | Confirm on first eligible retry, no fixed ordinal |
| Replay residual | Different-header LAN or re-emitted serial answer pays B | May ACCEPT; exact-byte LAN cache cannot identify it |

## 8. Existing source coverage

No tests or builds are run for this documentation revision. The following tests exist in the inspected implementation;
their assertions describe coverage, not a passing CI, hardware or failing-first history claim.

- `tests/test_ptt_quiescence_ledger.py: test_directed_shape_and_controller_address`,
  `test_fifo_identity_orphan_and_writes`, `test_expiry_is_strict_and_has_no_answer` cover D1, FIFO identity,
  UNWRITTEN payment, orphan/quarantine and strict expiry boundaries.
  `test_serial_reply_during_drain` uses IC-7610 and IC-7300 serial fakes to hold drain while receiving a valid reply.
- The same file's `test_poll_debt_outlives_waiters_and_sinks`,
  `test_invocation_context_survives_queue_and_timeout`, `test_unwritten_debt_survives_queue_delay`, and
  `test_waiter_completion_charged_to_another_entry` cover sink/waiter independence, raw/no-wait sends, failure,
  cancellation, foreign request exclusion, queue/pacing guards, and invocation ownership.
- Its `test_pump_replay_cache` covers zero, scope traffic, echo, boundary, after-Q, mixed packets, restart, changed
  headers, sparse sequence progression and beyond-lifetime delivery. The serial variant verifies cache bypass.
  `test_reconnect_retains_debt_and_stale_generation_accounting` checks retained debt and stale-generation accounting;
  `test_reader_settle_checks_receive_pump_and_session` checks clean settle, extra orphan, epoch and transport changes.
- `tests/test_icom_managed_tx_actuator.py: release_radio`, `test_two_read_release_matrix` cover IC-7610 LAN,
  IC-7610 serial and IC-7300 serial with production actuation. Cached A3b/A3c rows run only on LAN. The matrix includes
  clean/ambiguous answers, unsupported read profiles, E enforcement/residual, lifetime/new-header residuals and B/C
  reordering. `test_normal_polling_confirms_on_first_eligible_retry` keeps normal polling active while testing refusal
  then the first dispatched confirmation with RX; it does not require a fixed retry ordinal.
- `tests/contracts/test_tx_observation_conformance.py: test_scripted_answer_vocabulary_pin`,
  `test_a_civ_read_is_not_satisfied_by_an_ack_echo_or_misaddressed_frame`, and
  `test_a_confirmed_rx_and_a_keyed_radio_read_their_values` include broadcast rejection and repeated valid reads.
  `tests/tx_observation_fakes.py: CIV_NON_ANSWERS`, `civ_transmit_state_reply` supply the broadcast shape to production
  classification. `ScriptedLanTransport.send_tracked` honors keyword-only currency before send and assigns each new
  reply an advancing 16-bit sequence, avoiding accidental identical fake packets; intentional replay reuses bytes.
- `tests/test_radio.py: _ptt_response` accepts an explicit sequence. In
  `test_ptt_authority_rejects_old_rx_pump_generation`, the replacement pump receives `seq=2`, avoiding an exact twin
  of the deliberately old-generation packet. The LAN replay cache must not obscure the generation assertion.
- `tests/integration/test_ptt_release_readback_api.py: api`, `test_release_readback_through_http_and_websocket`
  use the existing real-stack helper `tests/integration/test_tx_under_fault_matrix.py: _stand_up_stack`, with real
  radio/managed authority/effect lane and localhost HTTP/WS. They assert simulator writes, release debt and next-key
  outcomes for clean RX, earlier silent debt, and prompt key refusal during held confirmation. The actuator matrix
  supplies serial coverage; this API regression is LAN. Test-local deadlines/settle are shortened or overridden,
  so those tests alone do not validate production wall-clock margins (§6).

## 9. Residual risk

- L violation can falsely release after lost unkey without any external actor: A's stale first RX reply pays B beyond
  its written lifetime. UNWRITTEN accounting closes the premature-expiry-during-send gap, not arbitrary late delivery.
- LAN exact twins are suppressed through the full receipt-anchored lifetime, including after Q. After cache expiry
  there is no exact-byte protection. Different-header/new-sequence LAN re-emission bypasses the key; serial has no
  packet cache because synthetic envelopes reset/repeat. If B is owed, these replies may pay B and falsely confirm.
- Orphan quarantine plus RX settle enforce detectable same-address interference. They cannot reject a lone foreign
  RX reply paying B when Core's own reply remains silent in the window, or detect every extra reply charged to other
  concurrent debt. An address check alone cannot establish exclusive E0 use.
- F′ is assumed, not proved: reordered command delivery/execution is outside the conditional argument. Reordering
  B/C reply arrivals alone is benign under F′ and the lifetime/foreign/replay exclusions.
- LAN whole-byte collisions within retention can suppress a legitimate reply, costing liveness. Wrap/reset and sparse
  sequence progression preclude treating sequence values as globally unique. Cache memory scales with distinct valid
  PTT packets in the window; unrelated scope traffic does not evict evidence.
- Retention is within one runtime. New-runtime/process replacement does not inherit ledger/cache and adds no startup
  isolation proof. A valid reply on a new pump can still belong to an earlier request.
- Verified read-back reports sampled state. External keying after the sample is outside its causal window; the settle
  checks observe unexplained replies/session changes, not continuous RF state. Continued loss or overlap can leave
  release owed indefinitely. MOR-3537's observed-TX backstop (§5 D5) does not erase these limits.

## 10. Implementation delivery split

The user-supplied delivery facts replace revision 4's single-PR estimate and non-stacked contingency:

| PR | Scope | Base / integration | Reported diff |
|---|---|---|---|
| core#4388 | Groundwork: classifier, ledger/send hooks, reader/settle, LAN cache and focused supporting regressions | main | 921 changed lines, seven files |
| core#4389 | CoreRadio.actuate activation, actuator matrix/convergence, real HTTP/WS API regression | Stacked on #4388; retarget main after #4388 merges | Approximately 565 changed lines, three files |

Groundwork paths: `src/rigplane/core/civ.py`, `src/rigplane/runtime/_civ_rx.py`,
`src/rigplane/runtime/radio.py`, `tests/test_ptt_quiescence_ledger.py`, `tests/tx_observation_fakes.py`,
`tests/contracts/test_tx_observation_conformance.py`, `tests/test_radio.py`.
Activation paths: `src/rigplane/runtime/radio.py`, `tests/test_icom_managed_tx_actuator.py`,
`tests/integration/test_ptt_release_readback_api.py`. The shared radio path is sequential groundwork then activation,
not independently owned concurrent edits. The activation stays together with its actuator and API regressions.
These reported sizes are delivery facts, not new budgets or permission to omit scenarios.

This revision changes only this design file. It authorizes no product edits, test/build runs, radio operations,
merge or publication. Source inspection is separate from CI and hardware evidence.

## 11. Acceptance and remaining gates

The implemented mechanism is receive-owned FIFO with write-completion lifetime, Q=2.0, exact entry ownership,
strict D1, LAN-only full-duration packet cache, RX settle/session/orphan checks and unchanged key-down admission.
This record no longer describes implementation as future work or requires a new size-based delivery decision.

1. Independently review this revision against the implementation. Document consistency is not safety-criterion acceptance.
2. Record the supplied coordinator-accepted E condition: orphan quarantine and RX settle enforce observed same-address
   interference, except a lone foreign reply whose counterpart stays silent throughout the window. This records that
   condition only; L/F′ assumptions, replay and lifetime residuals in §9 remain, without an absolute safety claim.
3. Preserve the actual #4388 → #4389 dependency; retarget #4389 to main after #4388 merges. Required exact-head review
   and CI evidence remain separate merge gates under repository policy; this source review does not satisfy them.
4. Keep any owner-present physical-radio validation separate. No physical-radio validation, unconditional convergence
   or permission to merge/publish is asserted by this design revision.
