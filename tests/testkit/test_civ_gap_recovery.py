"""CI-V sequence-gap recovery end to end: a production ``IcomRadio`` against
the in-order ``MockIcomRadio``.

A fault rule on the simulator's CI-V port drops the next N type-0 datagrams
the client sends (idle, OpenClose and CI-V DATA packets, i.e. the tracked
ones; pings and retransmit requests pass). The next tracked packet the
client sends opens a gap at the simulator, which asks for the missing
numbers. ``tests/testkit/test_simulator_civ_gap.py`` pins the simulator's
side on the wire; these tests read the client's side from its own CI-V
transport and wait, with a bound, for ``civ_gap_open`` to turn False:

* small gap: five lost numbers, all still in the client's ``tx_buffer``;
* wide gap: more than ``MAX_MISSING`` lost numbers, served over several
  request rounds;
* unservable numbers: more than ``BUFSIZE`` lost numbers, so the client's
  ``tx_buffer`` has evicted the oldest ones and the client has nothing to
  resend for them.

Each wait is a poll on an observable with a bound that only guards a hang;
none of the waits is a fixed sleep. The failure messages of the closing wait
and of the asserts carry ``_Recovery.report``.
"""

from __future__ import annotations

import asyncio
import random
import struct
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field

import pytest

from rigplane.core.transport import BUFSIZE, CONTROL_SIZE, MAX_MISSING, IcomTransport
from rigplane.core.types import PacketType
from rigplane.radio import IcomRadio

from rigplane_testkit.icom import FaultPlan, FaultRule, MockIcomRadio

from _perf_helpers import fast_connect

_FREQ_HZ = 14_074_000  # the simulator's default frequency

_SMALL_LOST = 5
_WIDE_LOST = 2 * MAX_MISSING + 20  # three rounds at MAX_MISSING per round
_EVICTED = 20  # lost numbers beyond what the client's tx_buffer can hold
_UNSERVABLE_LOST = BUFSIZE + _EVICTED

_BURST = 50  # tracked packets sent per pass before waiting for the simulator
_POLL_S = 0.01
# Bounds that only guard a hang; they are not expected durations.
_PROGRESS_BUDGET_S = 10.0
_CLOSE_BUDGET_S = 20.0
_READ_BUDGET_S = 10.0


class _DropTracked(FaultRule):
    """Drops the next ``max_packets`` type-0 datagrams; everything else passes.

    Pings and retransmit requests are not type-0, so the rule never counts
    them.
    """

    def decide(
        self, packet: bytes, rng: random.Random, now: float
    ) -> list[tuple[float, bytes]] | None:
        if len(packet) < 6 or _packet_type(packet) != PacketType.DATA:
            return [(0.0, packet)]
        return super().decide(packet, rng, now)


def _packet_type(packet: bytes) -> int:
    return int(struct.unpack_from("<H", packet, 4)[0])


def _header_seq(packet: bytes) -> int:
    return int(struct.unpack_from("<H", packet, 6)[0])


def _is_idle(packet: bytes, seq: int) -> bool:
    """A 0x10-byte type-0 packet carrying ``seq``."""
    return (
        len(packet) == CONTROL_SIZE
        and struct.unpack_from("<I", packet, 0)[0] == CONTROL_SIZE
        and _packet_type(packet) == PacketType.DATA
        and _header_seq(packet) == seq
    )


def _parse_request(data: bytes) -> tuple[bool, list[tuple[int, int]]] | None:
    """``(single, ranges)`` for a radio retransmit request, else ``None``.

    The single form is a 0x10-byte type-0x01 packet whose header seq is the
    missing number; the other form is a type-0x01 packet with header seq 0
    followed by little-endian u16 ``(first, last)`` pairs.
    """
    if len(data) < CONTROL_SIZE or _packet_type(data) != PacketType.CONTROL:
        return None
    if len(data) == CONTROL_SIZE:
        seq = _header_seq(data)
        return True, [(seq, seq)]
    end = CONTROL_SIZE + (len(data) - CONTROL_SIZE) // 4 * 4
    return False, [
        (first, last)
        for first, last in (
            struct.unpack_from("<HH", data, i) for i in range(CONTROL_SIZE, end, 4)
        )
    ]


def _named_numbers(ranges: list[tuple[int, int]]) -> list[int]:
    """The numbers the ranges name: the first MAX_MISSING of each forward range."""
    named: list[int] = []
    for first, last in ranges:
        count = min(((last - first) & 0xFFFF) + 1, MAX_MISSING)
        named.extend((first + offset) & 0xFFFF for offset in range(count))
    return named


@dataclass
class _Reply:
    """One retransmit request the client received, and what it sent back."""

    single: bool
    ranges: list[tuple[int, int]]
    named: list[int]
    buffered: frozenset[int]  # of ``named``, those in tx_buffer on arrival
    sent: list[bytes]  # every datagram the client emitted while handling it

    @property
    def sent_seqs(self) -> list[int]:
        return [_header_seq(packet) for packet in self.sent]


@dataclass
class _Wire:
    """What the client's CI-V transport sent, and the requests it received."""

    sent: list[bytes] = field(default_factory=list)
    # The first type-0 datagram sent with each header sequence number.
    originals: dict[int, bytes] = field(default_factory=dict)
    replies: list[_Reply] = field(default_factory=list)


def _instrument(transport: IcomTransport) -> _Wire:
    """Record every datagram ``transport`` sends and what each request drew."""
    wire = _Wire()
    raw_send = transport._raw_send
    handle_packet = transport._handle_packet

    def recording_send(data: bytes) -> None:
        wire.sent.append(data)
        if len(data) >= 8 and _packet_type(data) == PacketType.DATA:
            wire.originals.setdefault(_header_seq(data), data)
        raw_send(data)

    def recording_handle(data: bytes) -> None:
        parsed = _parse_request(data)
        if parsed is None:
            handle_packet(data)
            return
        single, ranges = parsed
        named = _named_numbers(ranges)
        buffered = frozenset(n for n in named if n in transport.tx_buffer)
        start = len(wire.sent)
        handle_packet(data)
        wire.replies.append(_Reply(single, ranges, named, buffered, wire.sent[start:]))

    transport._raw_send = recording_send  # type: ignore[method-assign]
    transport._handle_packet = recording_handle  # type: ignore[method-assign]
    return wire


async def _until(
    done: Callable[[], bool],
    budget_s: float,
    what: str,
    report: Callable[[], str],
) -> None:
    deadline = time.monotonic() + budget_s
    while not done():
        if time.monotonic() >= deadline:
            pytest.fail(f"{what}: not reached within {budget_s} s\n{report()}")
        await asyncio.sleep(_POLL_S)


@dataclass
class _Recovery:
    """What one loss-and-recovery scenario observed."""

    lost: int
    wire: _Wire
    requests: int = 0  # retransmit request packets the simulator sent
    closed_after_s: float = 0.0  # from arming the loss to the gap closing
    logins: int = 0  # accepted logins since the loss was armed
    opens: int = 0  # OpenClose(open) packets since the loss was armed
    gap_open_after: bool = True
    freq: int | None = None

    def run(self) -> tuple[int, int]:
        """The ``(first, last)`` run the radio's first request named."""
        first_request = self.wire.replies[0]
        assert not first_request.single, self.report()
        assert len(first_request.ranges) == 1, self.report()
        return first_request.ranges[0]

    def report(self) -> str:
        lines = [
            f"lost={self.lost} requests={self.requests} "
            f"closed_after_s={self.closed_after_s:.3f} logins={self.logins} "
            f"openclose_opens={self.opens} gap_open_after={self.gap_open_after} "
            f"freq={self.freq!r}",
        ]
        for i, reply in enumerate(self.wire.replies[:12]):
            lines.append(
                f"reply {i}: single={reply.single} ranges={reply.ranges} "
                f"named={len(reply.named)} buffered={len(reply.buffered)} "
                f"sent={len(reply.sent)}"
            )
        if len(self.wire.replies) > 12:
            lines.append(f"... {len(self.wire.replies) - 12} more replies")
        return "\n".join(lines)


async def _lose_tracked(
    sim: MockIcomRadio, transport: IcomTransport, count: int
) -> None:
    """Send ``count`` tracked idles and have the simulator drop all of them.

    The idles are what the transport's own idle loop sends. They go out in
    bursts of at most ``_BURST``, each followed by a wait until the
    simulator has read it.
    """
    rule = _DropTracked(drop_probability=1.0, max_packets=count)
    sim.fault_plan = FaultPlan(civ_from_client=rule)
    sent = 0
    while sent < count:
        for _ in range(min(_BURST, count - sent)):
            await transport.send_tracked(transport._build_control(ptype=0x00, seq=0))
            sent += 1
        target = sent
        await _until(
            lambda: rule.matched >= target,
            _PROGRESS_BUDGET_S,
            f"the simulator to read {target} tracked packets",
            lambda: f"matched={rule.matched} dropped={rule.dropped}",
        )
    assert rule.dropped == count


async def _lose_and_recover(
    sim: MockIcomRadio, radio: IcomRadio, lost: int
) -> _Recovery:
    transport = radio._civ_transport
    assert transport is not None
    recovery = _Recovery(lost=lost, wire=_instrument(transport))
    logins0, opens0 = sim.login_count, sim.openclose_open_count
    requests0 = sim.civ_retransmit_requests
    started = time.monotonic()

    await _lose_tracked(sim, transport, lost)

    def closed() -> bool:
        recovery.requests = sim.civ_retransmit_requests - requests0
        return (
            recovery.requests > 0
            and not sim.civ_gap_open
            and len(recovery.wire.replies) == recovery.requests
        )

    await _until(
        closed,
        _CLOSE_BUDGET_S,
        "the gap to be requested, answered and closed",
        recovery.report,
    )
    recovery.closed_after_s = time.monotonic() - started
    sim.fault_plan = None
    recovery.freq = await asyncio.wait_for(radio.get_freq(), _READ_BUDGET_S)
    recovery.gap_open_after = sim.civ_gap_open
    recovery.logins = sim.login_count - logins0
    recovery.opens = sim.openclose_open_count - opens0
    return recovery


def _assert_recovered_in_session(recovery: _Recovery) -> tuple[int, int]:
    """Checks every scenario shares; returns the run the radio asked for."""
    report = recovery.report()
    # Recovery came from retransmission, not from a new session: a new
    # session would restart the simulator's tracking and close the gap
    # without a single resend.
    assert recovery.logins == 0, report
    assert recovery.opens == 0, report
    assert recovery.gap_open_after is False, report
    assert recovery.freq == _FREQ_HZ, report

    first, last = recovery.run()
    # The radio named exactly the numbers that were lost, each of which the
    # client really sent.
    assert last - first + 1 == recovery.lost, report
    assert all(n in recovery.wire.originals for n in range(first, last + 1)), report
    # Every request drew exactly the numbers it named -- nothing outside the
    # requested ranges, at most MAX_MISSING per range -- and together the
    # replies covered the whole lost run.
    for reply in recovery.wire.replies:
        assert sorted(reply.sent_seqs) == sorted(reply.named), report
    answered = {n for reply in recovery.wire.replies for n in reply.sent_seqs}
    assert answered == set(range(first, last + 1)), report
    return first, last


@pytest.fixture
async def sim() -> AsyncGenerator[MockIcomRadio]:
    server = MockIcomRadio()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def radio(sim: MockIcomRadio) -> AsyncGenerator[IcomRadio]:
    client = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        model="IC-7610",
        timeout=5.0,
    )
    with fast_connect():
        await client.connect()
    assert client.connected
    try:
        yield client
    finally:
        await asyncio.wait_for(client.disconnect(), 10.0)


async def test_small_gap_is_resent_from_the_client_buffer(
    sim: MockIcomRadio, radio: IcomRadio
) -> None:
    recovery = await _lose_and_recover(sim, radio, _SMALL_LOST)
    report = recovery.report()
    first, last = _assert_recovered_in_session(recovery)

    assert recovery.requests >= 2, report  # one round is two request packets
    # Every named number was still buffered, so every reply is the packet
    # the client sent the first time.
    for reply in recovery.wire.replies:
        assert reply.buffered == frozenset(reply.named), report
        by_seq = {_header_seq(packet): packet for packet in reply.sent}
        assert all(by_seq[n] == recovery.wire.originals[n] for n in reply.named), report
    assert recovery.wire.replies[0].named == list(range(first, last + 1)), report


async def test_wide_gap_is_served_over_several_request_rounds(
    sim: MockIcomRadio, radio: IcomRadio
) -> None:
    recovery = await _lose_and_recover(sim, radio, _WIDE_LOST)
    report = recovery.report()
    first, _ = _assert_recovered_in_session(recovery)

    # More than MAX_MISSING numbers were lost and one request is answered
    # with at most MAX_MISSING of them, so it takes at least
    # ceil(lost / MAX_MISSING) rounds of two request packets each. The first
    # request is answered with the first MAX_MISSING numbers of the run, not
    # with the whole run.
    rounds_needed = -(-_WIDE_LOST // MAX_MISSING)
    assert _WIDE_LOST > MAX_MISSING and rounds_needed >= 3, report
    assert recovery.requests >= 2 * rounds_needed, report
    assert sorted(recovery.wire.replies[0].sent_seqs) == list(
        range(first, first + MAX_MISSING)
    ), report
    assert all(len(reply.sent) <= MAX_MISSING for reply in recovery.wire.replies), (
        report
    )
    # Nothing was evicted: every named number was still in the buffer.
    assert all(
        reply.buffered == frozenset(reply.named) for reply in recovery.wire.replies
    ), report


async def test_unservable_numbers_are_answered_with_idle_fills(
    sim: MockIcomRadio, radio: IcomRadio
) -> None:
    recovery = await _lose_and_recover(sim, radio, _UNSERVABLE_LOST)
    report = recovery.report()
    _assert_recovered_in_session(recovery)

    # tx_buffer holds at most BUFSIZE packets and evicts the oldest first, so
    # at least _EVICTED of the oldest lost numbers were gone when a request
    # first named them. For those the client has nothing to resend and
    # answers with an idle carrying the number.
    unbuffered = [
        n
        for reply in recovery.wire.replies
        for n in reply.named
        if n not in reply.buffered
    ]
    assert len(set(unbuffered)) >= _EVICTED, report
    for reply in recovery.wire.replies:
        by_seq = {_header_seq(packet): packet for packet in reply.sent}
        assert all(
            _is_idle(by_seq[n], n) for n in reply.named if n not in reply.buffered
        ), report
