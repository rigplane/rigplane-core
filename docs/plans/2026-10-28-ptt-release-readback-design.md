# MOR-3818: Confirm Icom PTT release by a `1C 00` read-back — design

Date: 2026-10-28, revision 4. Status: design only; no implementation or test execution in this change.
Scope: shared Icom CI-V runtime. Source references below were read for this revision; §8 describes intended tests,
not completed validation. The prescribed mechanism has unresolved safety acceptance limits (§9 and §11).

## 1. Summary

After an ambiguous unkey answer, propose one `1C 00` read-back inside `CoreRadio.actuate`. Accept the release only
when that read returns verified RX. An owed-replies FIFO records every dispatched empty-data `1C 00` read, including
fire-and-forget polls. Receive routing consumes this debt independently of request waiters. A confirming read starts
only when the FIFO is empty and orphan quarantine has expired; its own dispatch entry must hold its completing frame.
A time-based whole-packet replay cache in the CI-V receive pump suppresses identical PTT reply packets for
`answer_window + Q` from accepted receipt (4.0 seconds at shipped defaults).

This is conditional attribution, not a request identifier or an absolute no-false-release guarantee. Bounded lifetime,
ordered command delivery/execution and exclusive controller-address use are explicit assumptions below. Reply
reordering is safe under those assumptions; new-header re-emission remains a gap. Owner/coordinator acceptance of
the changed safety criterion is pending. Default implementation delivery is PR-B1 plus the API-only PR-B2 (§10).

## 2. Problem and current behaviour

`src/rigplane/runtime/radio.py: CoreRadio.actuate` sends `ptt_off` at `Priority.FORCE_RELEASE`, awaiting an answer.
A timeout, non-ACK shape, or `ack_unclaimed_at` within one `_civ_get_timeout` before the attempt returns `UNCERTAIN`.
Otherwise `FB` returns `ACCEPTED` and `FA` returns `REJECTED`. These bare answers identify no command;
`src/rigplane/core/civ.py: CivRequestTracker.ack_unclaimed_at` records potential write-answer ambiguity.
Key-down uses a fire-and-forget write and takes no read-back.

`src/rigplane/runtime/managed_tx_state.py: _settle` clears `release_plan` on an accepted FORCE_RECEIVE settlement;
other outcomes retain it. `reduce_managed_tx` refuses a new positive TX intent from RX while release is required. Thus a missing
unkey answer can keep key-down refused even after the radio has returned to RX. The proposed read-back addresses that
ambiguous branch without making key-down wait for confirmation or changing admission semantics.

`src/rigplane/runtime/radio.py: CoreRadio.read_transmit_state` already sends an empty-data `1C 00` read, using
`TX_READ_DEADLINE_SECONDS`. It currently accepts sources in `RADIO_READBACK_SOURCES`, which includes `civ_unsolicited`
(`src/rigplane/core/tx_observation.py: RADIO_READBACK_SOURCES`). This proposal narrows this Icom reader's verification
to the exact directed `poll_response` shape plus the FIFO entry identity. Other backends' readers are outside scope.

## 3. Why the first attempt failed

A response match alone cannot identify the request. `src/rigplane/core/civ.py: CivRequestTracker._matches` checks
command, subcommand, optional receiver and data prefix; it does not check recipient, exact PTT length or value.
`src/rigplane/runtime/_civ_rx.py: CivRuntime._route_civ_frame` accepts the radio's frames addressed to E0 or broadcast.
Consequently a malformed/broadcast response can complete A's waiter while A's genuine directed reply remains in flight.
If completion removed A's debt, that genuine reply could satisfy a later identical waiter B.

Queue-time packet baselines are also insufficient: traffic can arrive during queueing or pacing. LAN transport queues
packets as received, including delayed gap-fill packets and duplicates; receive sequence bookkeeping does not reorder
or suppress their delivery (`src/rigplane/core/transport.py: IcomTransport._handle_data_packet`,
`IcomTransport._record_rx_seq`, `IcomTransport.receive_packet`). Serial envelopes number frames on receipt and wrap
at 16 bits (`src/rigplane/backends/icom7610/drivers/serial_session.py: SerialCivTransport.receive_packet`). Neither
number is evidence that a reply was generated for B. Reply order need not identify B: a later C also samples after
the unkey send under F′ (§4). This revision uses neither sequence freshness nor request finalizers.

## Assumptions

**L — bounded reply lifetime.** A read's replies not received by `dispatched_at + answer_window + Q` are treated as
lost; `answer_window = _civ_get_timeout` and `Q = PTT_REPLY_QUIESCENCE_S = 2.0`. At shipped constructor defaults this
is four seconds from dispatch (`src/rigplane/runtime/radio.py: CoreRadio.__init__`: default `timeout=5.0`,
`_civ_get_timeout=min(timeout, 2.0)`). Expiry occurs only strictly after that bound. This is a policy assumption, not
a measured latency guarantee. Replacing “never report released while transmitting” with this conditional criterion
requires explicit owner/coordinator acceptance. CI-V carries no request IDs from which an absolute guarantee follows.

A harmful violation needs no external re-key: A samples RX, its answer is delayed beyond the bound, Core keys the
radio, the subsequent unkey is lost, and B accepts A's stale RX. The release debt can then be cleared falsely.
D6′ retains an accepted packet for the same duration `answer_window + Q`, anchored at receipt rather than dispatch.
Since receipt follows dispatch, retention covers the remaining lifetime of every read that packet could answer.
An exact twin after cache expiry is already beyond that lifetime; L, rather than a shorter cache, is the limit.

**F′ — ordered command delivery and execution.** The radio executes CI-V commands in order received, and each link
delivers our commands to the radio in order sent. This is the baseline assumption of the CI-V write/read sequence,
not an ordering guarantee enforced here. It does not require replies to reach the pump in request order.
`src/rigplane/core/transport.py: IcomTransport.send_tracked` guards local submission, not delivery or execution.
LAN reply arrivals are queued immediately by `IcomTransport._handle_data_packet`; `_record_rx_seq` records gaps and
`_retransmit_loop` requests missing packets without holding later arrivals. `CivRuntime._civ_rx_loop` drains that
arrival queue. A later C may therefore pay B's debt; its sample is also after the unkey send under F′ (§4).

**E — exclusive use of E0.** Only Core may issue requests using `CONTROLLER_ADDR = 0xE0`
(`src/rigplane/commands/_frame.py: CONTROLLER_ADDR`). This proposal cannot enforce that precondition on a shared bus.
A foreign controller using a different address is filtered by routing/D1. A foreign controller using E0 is invisible
to Core's dispatch ledger; its answer is indistinguishable during B. The same address-attribution precondition is
implicit in existing state reads; this design makes it explicit, without claiming the bus enforces it.

## 4. Safety argument

Conditional on L, F′, E and absence of the replay escapes in §9: B is sent only with an empty owed FIFO and no active
orphan quarantine. Earlier reads have either been answered with their replies consumed or are lost under L. Dispatch
appends B before send. Every valid directed `1C 00` reply subsequently paying debt comes from B or a later C; both
requests were sent after unkey. Under F′, a delivered unkey executes before either sample. If unkey was lost, these
post-write reads still sample the resulting state: TX keeps release owed. RX is acceptable from either B or C, subject
to the sampled-state limit in §9. Reply delivery reordering cannot turn these samples into pre-unkey stale RX.

Receive routing stores the paying frame in the oldest entry. D3 also requires `context.entry.answered_by is reply`,
D1 and `poll_response`. A C reply charged to B may pass B's identity check and is harmless for RX acceptance under
the conditions above. A waiter completed by a frame charged to another entry fails closed; this can impair liveness,
but is not stale-sample acceptance. The identity check establishes ledger ownership, not the generating request.

The explicit checks cover a silent earlier read, malformed/broadcast completion of A, missing/cancelled waiters,
fire-and-forget sinks, a pre-dispatch orphan, intervening queue/pacing activity and reconnects retaining old traffic.
They do not prove F′, E on a shared bus, or suppress every replay. A later raw-path read may overlap B without
renewing a global horizon; under F′ its sample remains after the unkey send. Entry identity cannot reject a foreign
or replayed frame already charged to B itself. Current waiter matching is in `CivRequestTracker.resolve`/`_matches`;
the proposed ledger and D3 add ownership checks without changing those matching rules.

## 5. Design

### D1. Strict reply classification — one shape check

Extract the existing inline condition from `src/rigplane/runtime/_civ_rx.py: CivRuntime._observation` into proposed
`src/rigplane/core/civ.py: is_directed_ptt_reply(frame, radio_addr)`: recipient E0, sender `radio_addr`, command `0x1C`,
subcommand `0x00`, exactly one data byte, value `0x00` or `0x01`. Use it in `_observation`, the `note_reply` routing gate,
the reader's acceptance check and D6′'s packet selection. Broadcast remains `civ_unsolicited` and cannot verify B.
Keep this foundational helper independent of `commands/`; compare the protocol address literal `0xE0` and pin its
agreement with `CONTROLLER_ADDR` in the focused test, without adding an upward layer import.
Do not alter `CivRequestTracker._matches` or `resolve`: an invalid matching response may still end a waiter promptly,
but it never pays the FIFO debt and cannot become a verified read-back.

### D2. Receive-owned owed-replies FIFO

Add one clock-injected pure class beside `CivRequestTracker` in `src/rigplane/core/civ.py`; own one instance in
`src/rigplane/runtime/_civ_rx.py: CivRuntime.__init__`. Each entry contains
`{dispatched_at, answered_by: CivFrame | None}`. The FIFO contains only entries whose valid reply is still owed;
callers may retain their entry object after it is popped. The ledger also holds an ORPHAN counter and
`quarantine_until`. Use a monotonic injected clock; configure the answer window from the runtime's `_civ_get_timeout`.

- `note_dispatch(frame) -> entry | None`: only command `0x1C`, sub `0x00`, EMPTY data creates an entry. `ptt_on` and
  `ptt_off` carry data and are excluded. Run expiry, append the new entry and return its exact object.
- Invoke immediately BEFORE the transport send at all three sites: `CivRuntime._send_civ_frame_now` and both branches
  of `CivRuntime._execute_civ_raw`. These cover raw transactions and commander execution, including `expect="none"`
  and `wait_response=False`. A send that raises still leaves its entry owed; no rollback based on guessed delivery.
  `_send_civ_frame_now` currently creates a send task: put the hook inside that task's coroutine immediately before
  `send_tracked`, not before task scheduling, so another send cannot slip between accounting and invocation.
- `note_reply(frame)`: call exactly once at `_route_civ_frame` for every D1-valid inbound frame, before tracker
  resolution and before generation-based early returns. First expire old entries. Pop the oldest nonexpired owed
  entry and set `entry.answered_by = frame`, preserving that exact object. With no owed entry, increment ORPHAN and
  set `quarantine_until = now + Q`. Invalid, broadcast, ACK and NAK frames never pop an entry.
- Expire an entry only when `now > dispatched_at + answer_window + Q`, leaving its `answered_by=None`. Expiry is
  evaluated by dispatch, reply and eligibility operations. Equality is still owed. `eligible()` is true only after
  expiry when the owed FIFO is empty and `now >= quarantine_until`; it never waits or sends.
- A normal valid poll reply pops its entry and renews nothing. Timeout, NAK, invalid waiter completion, cancellation,
  sink removal and caller return do not end FIFO ownership. There is no `note_end` and no request finalizer hook.

The separation is necessary for existing polling. `src/rigplane/web/radio_poller.py: RadioPoller._civ` defaults to
`wait_response=False`. `CivRuntime._execute_civ_raw` registers `register_ack(wait=False, response_key=request_key)`
for such reads and returns after sending. `src/rigplane/core/civ.py: CivRequestTracker.resolve` first searches response
waiters; only otherwise does it retire the oldest matching keyed sink. ACK/NAK may also retire an eligible sink.
`unregister_ack_sink`, `drop_ack_sinks`, stale cleanup and generation advance can remove sinks without a data reply.
None of these operations pays ledger debt. An actual valid reply pays it even if the sink no longer exists; a malformed
completion may remove a sink/waiter while leaving A owed until its genuine reply or expiry. `expect="none"` has no
awaited reply at all (`CivRuntime.execute_civ_transaction`); receive routing still owns its ledger entry.

Retain the same ledger across `advance_generation`, reconnect, soft reconnect and `retire_managed_tx_port`; let entries
and quarantine age out. `CivRuntime.start_pump` labels new arrivals with the pump's generation, not a wire request ID.
`src/rigplane/runtime/_control_phase.py: ControlPhaseRuntime.soft_reconnect` can reuse the local CI-V port and resume
send numbering. A reply to A can enter the replacement pump with the current generation. Existing generation checks
still govern tracker/state settlement, but cannot prove wire isolation. There are no old request finalizers to mutate
new entries. A stale pump's D1-valid receive still pays oldest debt or creates quarantine; it never settles a new waiter
through a generation mismatch. D6′ cache state is retained too. Runtime-object destruction is not an isolation proof;
this design's retention covers reuse of the same `CivRuntime`, not a process restart (§9).

### D3. `read_transmit_state` and exact per-call dispatch ownership

Keep the no-argument API and add optional keyword arguments `priority=Priority.NORMAL` and `is_current=None`.
After the existing connection precondition, check `eligible()` FIRST. False returns
`TxStateReading(value=None, failure="not-quiescent")` immediately: no queue, wait or send. An initially eligible
call can become ineligible during queueing, ACK-sink drain or pacing, so repeat this guard immediately before B's
`note_dispatch`; guard, append and send invocation have no intervening await. A failed second guard also sends nothing.

Propagate B's entry through an invocation-local private callable context defined in `_civ_rx.py`. It wraps the caller's
currency predicate without changing its truth/exception semantics (`None` means current) and has an initially empty
dispatch-entry slot.
Pass this context as `is_current` through existing `CoreRadio._send_civ_expect` → `CoreRadio._send_civ_raw` →
`CivRuntime._send_civ_raw` → `IcomCommander.send`. The existing queue item stores this callable and `_loop` passes the
same object to `execute_civ_raw` (`src/rigplane/commands/commander.py: _QueueItem`, `IcomCommander.send`,
`IcomCommander._loop`). In `_execute_civ_raw`, recognize this private context, perform the second eligibility guard,
and assign `context.entry = note_dispatch(parsed_frame)` at the send seam. Use a private refusal exception mapped
by the reader to `not-quiescent`. Keep eligibility separate from the callable's currency check: repeated currency
checks must neither append nor delete debt. This needs no commander or generic tracker changes, global “last entry”,
frame reconstruction, or callback lookup by command key. The direct executor path carries the same context.

On completion accept only when `context.entry` exists, `context.entry.answered_by is reply`, D1 passes and the decoded
PTT observation has `source == "poll_response"`. These are cumulative requirements. Invalid completion, an entry
charged with another frame, missing entry, timeout or cancellation cannot verify B. Keep `timeout`, `read-error` and
`unverifiable-provenance` outcomes; add `not-quiescent` for the explicit guard. B remains owed after any incomplete
read until a valid receive or expiry, even though its outer 0.3-second timeout has ended its waiter. Do not change
other readers' observation publication or `CivRequestTracker` matching semantics.

### D4. `CoreRadio.actuate` FORCE_RECEIVE

Only timeout/non-ACK/ambiguous-ACK branches request the new read-back, once, with FORCE_RELEASE priority and the same
currency predicate. Verified `value is False` returns `ACCEPTED`; TX, missing value, any failure or read-back exception
leaves `UNCERTAIN`. Preserve cancellation propagation so the effect lane's deadline handling remains authoritative.
A clean `FB` takes no read; a truly unambiguous `FA` remains `REJECTED`. An ambiguous `FA` follows the ambiguous branch,
and a read-back never itself returns `REJECTED`.

Key-down remains fire-and-forget with no read or wait; upstream admission remains refused while `release_required`.
The new read-back uses the shared runtime for Icom radios. If the profile does not declare `get_transceiver_status`,
leave the ambiguous branch `UNCERTAIN`; use `src/rigplane/profiles/__init__.py: RadioProfile.supports_command`, not a
model-name branch. Both `rigs/ic7610.toml: commands` and `rigs/ic7300.toml: commands` declare that read and the PTT writes.

### D5. Boundaries

No locks, marker commands, per-radio implementation branches, authority/state-machine/effect-lane changes or polling
suspension. Ordinary polling is recorded by the FIFO and is not a rolling global quarantine. Raw bytes in D6′ are used
only for exact replay suppression, never for freshness, ordering or positive attribution. No transport or control-phase
changes. Profiles, public protocol signatures and key-down admission policy retain their existing contracts.

### D6′. Pump-level whole-packet duplicate suppression

In `CivRuntime._civ_rx_loop`, inspect the packet header/raw bytes and parse its frames before any routing. Only packets
containing at least one D1-valid PTT reply enter a runtime-owned cache keyed by the WHOLE raw packet bytes, including
header. Cache the accepted packet's monotonic receive time. If identical bytes recur at age `<= answer_window + Q`,
drop that packet before routing, ledger accounting, raw delivery or waiter settlement; do not renew the timestamp on
a dropped twin. Expire entries only when age `> answer_window + Q` (4.0 seconds at defaults). Parse once and route
the same parsed frame objects for an accepted packet.
No sequence-only key, zero exclusion, fixed-count eviction or scope/control-traffic eviction is permitted. Packets
without a D1-valid PTT reply bypass this cache. A mixed packet is cached/dropped as a whole, not frame by frame.

Retain the cache across pump restart/reconnect within the runtime: clearing it would admit A's already-delivered twin
on the replacement receive path. Whole headers reduce cross-session collisions but do not guarantee uniqueness; a
legitimate identical packet within `answer_window + Q` may be suppressed, costing an answer rather than establishing
fresh evidence. Receive-anchored retention covers every read that packet could answer within its lifetime, and can
outlast the originating read's dispatch-anchored expiry. Memory grows with distinct valid PTT packets in that window.

`rigplane_testkit/icom/simulator.py: MockIcomRadio._handle_civ_data` echoes the original request packet when
`lan_civ_echo` is enabled (the constructor default). Genuine replies use an independent `_civ_seq` in
`MockIcomRadio._wrap_civ`. Echo and reply can share a numeric sequence but differ in addresses/payload bytes; the echo
fails D1 and cannot poison the cache. Zero is included: simulator reply numbering and
`src/rigplane/core/transport.py: IcomTransport._next_send_seq` both wrap with `& 0xFFFF`.
`IcomTransport._record_rx_seq` recognizes modular forward deltas through `0x7FFF`; sparse progress such as
1, 32768, 65535, 1 does not establish full-packet uniqueness. Whole bytes, not that arithmetic, decide D6′.

The same pump handles serial envelopes. `SerialCivTransport.receive_packet` increments its synthetic counter modulo
65536, and `start_retransmit_loop` is a no-op; serial sequence values must not be described as unique forever.
Ordinary new envelopes usually differ, but wrap or a replacement adapter can repeat bytes. A sequential full wrap
within the default cache window would require 65536 packets / 4 seconds = 16384 packets/second; no receive-rate bound
establishing its impossibility was found in these paths. Sparse LAN progression or reset need not deliver 65536 packets.
These collision/retention limits and new-sequence re-emission remain explicit in §9.

## 6. Time budget

Verified constants: `_civ_get_timeout=min(timeout, 2.0)` with default `timeout=5.0`
(`src/rigplane/runtime/radio.py: CoreRadio.__init__`); `TX_READ_DEADLINE_SECONDS=0.3`
(`src/rigplane/core/tx_observation.py: TX_READ_DEADLINE_SECONDS`); `ATTEMPT_TIMEOUT_S=3.0` and `RETRY_DELAY_S=1.0`
(`src/rigplane/runtime/managed_tx_authority.py: ATTEMPT_TIMEOUT_S`, `RETRY_DELAY_S`).
The 0.3-second outer read timeout includes its commander queueing and pacing, not 0.3 seconds plus those costs
(`src/rigplane/commands/commander.py: IcomCommander.send`). The inner GET answer wait begins after send in
`CivRuntime._execute_civ_raw`. Unkey queue/drain/pacing time precedes its own answer window, so 2.0 + 0.3 is not a
hard total for the entire actuation. Preserve the authority attempt deadline and currency handling; no duration
promise should assume an unbounded unkey queue is free.

One lost read A stops blocking only strictly after `A.dispatched_at + answer_window + Q` (four seconds at defaults),
not after its caller settles plus Q. A valid answer pays its oldest debt sooner. An orphan creates its own Q quarantine.
`ManagedTxAuthority._process_due` clears `_retry_due` when reducing a retry; `_execute` reconciles the settlement and
`_refresh_retry_locked` arms the next retry from the current clock plus one second while release remains retryable.
That is a settlement-relative delay, not a fixed wall-clock retry interval. Convergence is on the first eligible retry
whose B obtains a verified post-unkey RX sample, or earlier on an unambiguous `FB`; it is not necessarily the second retry.

An in-flight poll at a would-be B blocks that B until reply or expiry; a later retry can proceed when otherwise empty.
Answered polls pop entries and do not extend quarantine. `rigs/ic7300.toml: state_acquisition` declares a 1.5-second
cadence with adaptive decay disabled; that cadence alone neither creates a permanent horizon nor proves an idle slot.
The continuous-polling regression must demonstrate the first eligible retry (§8). Arbitrarily continuous overlap or
continued loss offers no unconditional liveness guarantee.

`src/rigplane/core/transport.py: IcomTransport._retransmit_loop` builds/sends requests before incrementing/removing
missing counters. It sends at counts 0, 1, 2, 3 and 4: five request rounds with `RETRANSMIT_PERIOD=0.1`, not four.
This retry schedule is not a maximum delivery-age guarantee and cannot establish L.

## 7. Two-read regression matrix

A is an earlier read; B is the proposed release confirmation after an ambiguous unkey. Entries below are intended
regressions/characterizations, not observed results. “Verified” describes D3's checks, conditional on the assumptions.

| ID | Sequence / state | Reading and release result | Required interpretation |
|---|---|---|---|
| A1a | A times out but remains owed inside its lifetime; lost unkey | B not sent; `not-quiescent`, UNCERTAIN | Both release debt and unread reply debt remain |
| A1b | A sampled RX; Core keys; unkey is lost; stale A first arrives after expiry during B | May verify stale RX and falsely ACCEPT | L violation; no external re-key is needed |
| A2 | Invalid/broadcast matching response ends A's waiter; genuine A still owed | B not sent; UNCERTAIN | Invalid response never pops A; genuine reply later pays A |
| A3a | A remains owed when B is attempted, with a valid answer/replay about to arrive | B not sent; UNCERTAIN | A reply is charged to A, not a nonexistent B |
| A3b | Answered A's byte-identical packet repeats during B at age <= answer_window + Q, including seq 0 or >256 intervening scope packets | Twin dropped before routing; B awaits an eligible answer | Cache is whole bytes and time-based, anchored at accepted receipt |
| A3c | A answered at 0.01; identical replay at 2.1 while B owed (A dispatch 0) | Twin dropped; the former after-Q gap is closed | Age between Q and answer_window + Q is retained; after-lifetime delivery is covered by L |
| A4 | Actual fire-and-forget poll A still owed, even if its keyed sink was removed | B not sent; UNCERTAIN | Receive-owned debt survives sink lifecycle |
| A5 | A's waiter ends on NAK/cancellation/send exception | A remains owed; B refused until valid reply or expiry | No completion/finalizer shortens debt |
| A6 | A answered once; FIFO empty, no quarantine; B receives own RX/TX | RX ACCEPTED; TX UNCERTAIN | Conditional happy path; ordinary answers renew nothing |
| A7 | A second valid emission follows B's answer | Exact cached twin drops; otherwise if no debt, ORPHAN + Q | If another entry is owed, it is charged there; replay escape remains |
| A8 | Valid orphan arrives before B's final dispatch check | B not sent; `not-quiescent`, UNCERTAIN | Q starts from orphan receipt |
| A9 | Generation/transport replacement with A still owed; A reply arrives through new pump | B refused while debt exists; arrival pays retained A | Current pump label is not a request identity; no reset |
| A10 | Unkey's late FB/FA arrives while B waits, with no other ACK sink | FB is skipped by B's NAK-only waiter; FA can end B as read-error, UNCERTAIN | Neither pays B's data-reply debt; FB may enter orphan ACK backlog |
| E0 | Foreign same-address controller's unseen read replies during B | May verify RX and falsely ACCEPT | E violation; different-address filtering does not cover it |
| FIFO-LAN BENIGN | Read C dispatched after B replies first on LAN | C frame pays B; RX verdict acceptable under L/F′/E and replay exclusions | Identity may pass B or reject a waiter whose frame paid another entry; liveness cost, never stale-sample acceptance under those conditions |
| POLL | Continuous normal polls, missing unkey ACKs, one initially silent read, then valid RX replies | Initial refusal; confirmation on first eligible retry | Test dispatch/settlement timeline, not “second retry” |
| SAFETY | Stale RX while A is owed or orphan quarantine active; or exact cached twin during B within answer_window + Q | B refused or twin dropped; no acceptance from that stale RX | A3c is closed and FIFO-LAN is benign under F′; A1b, E0 and new-header replay remain limits |

## 8. Simulator coverage and test plan

No tests are run by this design-only revision. Implementation uses focused failing-first checks for expected fixes;
residual demonstrations characterize the stated limits instead of asserting a false no-release guarantee. Planned
actuator matrix: IC-7610 LAN, IC-7610 serial and IC-7300 serial/USB. Use existing testkit seams without testkit edits:
`rigplane_testkit/icom/simulator.py: MockIcomRadio` with `stateful_ptt=True`, and
`rigplane_testkit/serial/civ_serial.py: CivSerialFake.__init__` (model-selectable), `CivSerialFake.from_profile` (profile
loader) and `CivSerialFake.open_serial_connection`.
Tests may locally hold/drop/inject replies; a pump-duplicate test must inject whole raw packets, not bypass the pump.

- New `tests/test_ptt_quiescence_ledger.py`: injected-clock table for FIFO pop order, orphan count/quarantine, expiry
  just before/at/after equality, invalid-no-pop, exact `answered_by` object identity, PTT writes excluded, and send
  exceptions retaining debt. Keep pure ledger cases separate from runtime adapters within this file.
- Runtime cases in that file: actual `wait_response=False` PTT polls with valid, malformed and absent responses;
  keyed sink removal, timeout/cancellation and `expect="none"`; all three dispatch sites; raised sends; normal polls
  paying debt without extending quarantine. Exercise a reply completing a waiter but charged to another entry.
  Verify the invocation-local context survives commander queueing, guard failure after pacing, concurrent raw reads
  and outer timeout without binding another call's entry. No finalizer may delete or rewrite a ledger entry.
- Reconnect cases: retain ledger/cache, replace transport and start the pump, then deliver A's old raw reply through
  that replacement transport with its new source generation. Verify B was blocked or A paid first; also inject an
  old reply with no debt and observe quarantine. Separately retain existing stale-generation rejection semantics.
- Pump cases in the same file: exact duplicate at sequence zero; >256 intervening scope packets; echo and genuine
  reply sharing a sequence; sparse modular progression; different full headers; retained cache across restart;
  mixed packets. Assert exact age = answer_window + Q drops, just-after expires, and dropped twins do not renew the
  timestamp. Assert after-Q replay within the full window (A3c) drops. Characterize delivery beyond L and new-sequence
  re-emission reaching B, including lost unkey plus stale RX; these residual demos are not safety enforcement.
- Update `tests/test_icom_managed_tx_actuator.py: test_force_receive_settles_on_the_radio_answer`,
  `test_a_late_answer_to_a_timed_out_unkey_does_not_settle_the_next` and
  `test_an_unkey_after_a_dropped_write_sink_stays_uncertain`: ambiguous unkey now attempts B, clean FB sends no B,
  unambiguous FA stays rejected; clean RX confirms, TX/malformed/broadcast/timeout/blocked B does not. Add the §7
  matrix across applicable transports, including foreign same-E0 injection and benign B/C reply reordering under F′;
  cover both C paying B with passing identity and a waiter rejected because its frame paid another entry.
- Update `tests/tx_observation_fakes.py: CIV_NON_ANSWERS`, `civ_transmit_state_reply` and its scripted harness vocabulary
  with broadcast. Update `tests/contracts/test_tx_observation_conformance.py:
  test_a_civ_read_is_not_satisfied_by_an_ack_echo_or_misaddressed_frame` and the literal vocabulary pin so broadcast
  no longer verifies. The fake must deliver the frame to production classification, not reject it itself.
- Adapt `tests/tx_observation_fakes.py: ScriptedLanTransport.send_tracked` to accept keyword-only `is_current=None`.
  D3 passes a non-None private context even for a default read, so the current data-only signature would raise
  `TypeError`. Check the guard before recording/sending or queuing replies: false raises `CommandError`, and a
  predicate exception propagates, matching `src/rigplane/core/transport.py: IcomTransport.send_tracked`. The base
  `tests/test_radio.py: MockTransport.send_tracked` remains data-only; honor the guard in the subclass before calling
  that base method. `ScriptedCivLink.send_written` already supports this guard and needs no signature change.
- Give that LAN fake a per-instance advancing 16-bit reply sequence for each NEW reply, passed explicitly to
  `tests/_helpers.py: wrap_civ_in_udp` (its default is always `seq=1`). Otherwise successive `tx_cat` and `tx_other`
  reads produce identical PTT=1 packets, and D6′ drops the second legitimate reply in
  `tests/contracts/test_tx_observation_conformance.py: test_a_confirmed_rx_and_a_keyed_radio_read_their_values`.
  Keep that repeated-read regression; intentional replay tests must reuse captured exact raw bytes instead of
  allocating a new sequence. These are test-local adaptations in the already-planned fake file, not helper/base edits.
- Convergence: keep normal empty-data PTT polling active, suppress unkey ACKs, make one earlier read silent, then
  restore valid RX replies. Record FIFO eligibility, actual read dispatch and release settlement. A poll in flight
  blocks that attempt; answered polls pay debt; the first otherwise-eligible retry with its own RX must confirm.
  Do not pause polling to manufacture the result or require a particular retry ordinal.

Mandatory new user-API regression file: `tests/integration/test_ptt_release_readback_api.py`. Reuse existing helpers
`tests/integration/test_tx_under_fault_matrix.py: _stand_up_stack`, `_tear_down_stack`, `_http_json`, `_WsKeyClient`.
`_stand_up_stack` is a helper, not a pytest fixture: it creates a real IcomRadio over stateful MockIcomRadio, installs
ManagedTxComposition, starts WebServer and binds the state store. `src/rigplane/runtime/managed_tx_composition.py:
ManagedTxComposition.__init__` creates the real ManagedTxEffectLane and ManagedTxAuthority. A local fixture in the new
file will wrap these helpers and test-local reply barriers; it will retain the real actuator and all those layers.
Use actual localhost HTTP/WS requests and the controller session/key flow, following
`tests/integration/test_multi_session_tx_lanes.py: _Arena.grant`, `_Arena.session` as examples.

Required seam cases: (1) ambiguous unkey followed by clean B RX clears release debt and permits the next user key;
(2) an earlier silent read keeps B refused and next key refused while debt remains; (3) key-down during held confirmation
returns refusal promptly, before releasing the reply barrier. Assert simulator PTT writes, authority debt and API
responses together; control fault timing with events. Use the real LAN stack here; the actuator matrix supplies serial
coverage. `tests/test_web_managed_ptt_rejection.py: _Stack`, `_QueueStack` use FakeLane and a mocked radio, so their
existing private-handler tests are useful admission checks but cannot substitute for this seam regression.

## 9. Residual risk

- L violation can falsely release after a lost unkey: A samples RX, Core keys, unkey is lost, A's first reply arrives
  beyond `dispatch + answer_window + Q` while B is owed. FIFO and identity accept it. No external actor is necessary.
- D6′ uses L's full duration from accepted receipt; exact replay after cache expiry is already beyond the originating
  read's lifetime. There is no shorter-cache acceptance gap; L remains the lifetime limit.
- A re-emitted reply with different header bytes/new sequence is indistinguishable from a first delivery to this
  cache. With no owed read it becomes an orphan and blocks later B; while B is owed it can be charged to B and harm
  release confirmation. Serial re-emission also receives a new synthetic envelope. No code claim establishes that
  radios never re-emit this way. A safe proof must exclude these emissions; this design does not enforce that exclusion.
- F′ remains assumed: radio command execution order and in-order delivery of our commands to the radio are not
  established here. Reordered B/C replies alone do not defeat the conditional safety argument.
- E is not enforced. Another E0 controller can contribute an unseen reply during B; an address check cannot reject it.
- Cache byte collisions after sequence wrap/reset can drop a genuine reply. No enforced receive-rate ceiling was
  found; 16384 packets/second is arithmetic for a sequential four-second full wrap, not an operational bound. Sparse
  numbering and adapter reset invalidate a packet-count uniqueness argument. Time-only retention also has a memory
  cost proportional to distinct valid PTT packets in answer_window + Q; scope traffic must not evict its evidence.
- Retention is within one runtime instance. A process/new-runtime replacement does not inherit this ledger/cache and
  has no demonstrated isolation from old traffic. No startup isolation guarantee is added by this proposal.
- Verified read-back reports a sampled state; an external key after that sample remains outside the confirmation's
  causal window. Continued loss/overlap can keep release owed indefinitely; bounded retries are not guaranteed success.

## 10. Files expected to change

Default delivery is PR-B1 (everything except the new API seam file: about 810 changed lines in seven files), then
PR-B2 (`tests/integration/test_ptt_release_readback_api.py` only: about 180 lines in one file). Merge PR-B2 after PR-B1,
based on main, never stacked. If the measured COMPLETE eight-file diff is under 900 changed lines, use one
implementation PR-B instead. Estimates below count additions plus deletions and are planning budgets, not a measured
diff. The single semantic unit is release attribution from wire accounting through API outcome.

| File | Work | Changed-line budget |
|---|---|---:|
| `src/rigplane/core/civ.py` | Shared D1 helper, clock-injected FIFO, entries and expiry | 90 |
| `src/rigplane/runtime/_civ_rx.py` | Three dispatch hooks, receive hook, local context and pump replay cache | 160 |
| `src/rigplane/runtime/radio.py` | D3 reader and D4 ambiguous-release branch | 80 |
| `tests/test_ptt_quiescence_ledger.py` (new) | Pure table plus runtime/sink/reconnect/pump/duplicate regressions | 270 |
| `tests/test_icom_managed_tx_actuator.py` | Updated ambiguity assertions, transport matrix and convergence | 150 |
| `tests/tx_observation_fakes.py` | Broadcast vocabulary, LAN currency guard and advancing reply sequence | 40 |
| `tests/contracts/test_tx_observation_conformance.py` | Broadcast conformance and vocabulary pin | 20 |
| `tests/integration/test_ptt_release_readback_api.py` (new) | Local fixture using existing real stack; three API cases | 180 |
| **Total** | **Eight files; expected budget, to measure during implementation** | **990** |

PR-B1, or a qualifying single PR-B, exceeds the soft 6-file/600-line threshold; its body must explain the one semantic
contract and shared verification matrix. Hard ceilings are 10 files and 1000 changed lines. The default split leaves
room for complete regressions before approaching that ceiling; all ledger, pump/duplicate, actuator and conformance
work stays together in PR-B1. PR-B2 adds the real API seam. Overall acceptance remains incomplete until all required
scope lands. Do not omit scenarios or compress code to report a compliant count. No separate transport PR,
transport flag or control-phase edit is proposed. This design-only revision changes only this Markdown file.

## 11. Open questions

The mechanism is fixed for this proposal: receive-owned FIFO, Q=2.0, exact dispatch entry, strict D1, pump whole-packet
cache lasting answer_window + Q and unchanged key-down admission. There is no authorization to implement code here.
Pending owner/coordinator decisions are acceptance decisions:

1. Explicitly accept or reject the change from absolute no-false-release wording to the L/F′/E-conditional criterion.
   L, ordered command delivery/radio execution and exclusive E0 use are assumptions, not guarantees enforced here.
2. Resolve acceptance of new-header re-emission, same-address foreign replies and the other true limits in §9.
   A3c is closed by full-duration retention; B/C reply reordering is benign under F′ and the stated exclusions.
   Document review does not accept the remaining risks or establish the original absolute goal.
3. Measure the complete eight-file implementation diff: use the default non-stacked PR-B1/PR-B2 split in §10, or
   one PR-B only if that complete diff is under 900 changed lines. All required scope must land for acceptance.

Independent review of this revision and explicit safety-criterion acceptance precede implementation. No hardware
validation, absolute safety verdict or unconditional retry-convergence claim is made by this design.
