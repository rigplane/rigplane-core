"""MOR-4384 on the LAN path: answers reach the tracker once, in radio order.

Real ``IcomCivTransport`` (sequence-ordered ingress, MOR-4205), the
production CI-V pump and runtime, the real request tracker. The radio side
is ``LanRadioFake``: it numbers the radio's datagrams and hands them to the
transport's real datagram entry point in the order a test picks, and serves
the transport's retransmit requests when told to.

1. Inbound gap: an answer behind a missing datagram waits until the gap is
   filled; when the transport gives the gap up, the late datagram is
   dropped and never delivered after the later one.
2. Inbound duplicate: a resent FB reaches the tracker once; the next write
   is settled by its own answer, not by the duplicate.
3. Outbound reorder: a raw transaction's write K and the worker's write K+1
   are answered by the radio in the order K+1, K (as when K was lost and
   resent). No blocking caller fails early and each write gets exactly one
   answer; when the two answers differ, they are swapped -- the stated
   off-by-one residual (PR #4555), pinned here as a tested fact.

Time runs on the shared virtual clock.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass

import pytest

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.core.exceptions import TimeoutError as CivTimeoutError
from rigplane.core.transport import (
    RETRANSMIT_PERIOD,
    ConnectionState,
    IcomCivTransport,
    RxOrderStats,
)
from rigplane.radio import IcomRadio
from rigplane.testing.timing import VirtualClock
from rigplane.types import CivFrame

from _helpers import CIV_HEADER_SIZE
from lan_reorder_fake import LanRadioFake

pytestmark = pytest.mark.timeout(20)

_ADDR = 0x98  # IC-7610
#: The transport asks for a missing datagram on each tick and gives it up
#: after its fifth request: well past this it is gone.
_BUDGET = 6 * RETRANSMIT_PERIOD
#: The CI-V answer window; longer than the give-up budget.
_WINDOW = 10.0


def _write(level: int) -> bytes:
    return build_civ_frame(
        _ADDR, CONTROLLER_ADDR, 0x14, sub=0x02, data=bytes([0, level])
    )


def _fb() -> bytes:
    return build_civ_frame(CONTROLLER_ADDR, _ADDR, 0xFB)


def _fa() -> bytes:
    return build_civ_frame(CONTROLLER_ADDR, _ADDR, 0xFA)


@dataclass
class _Lan:
    radio: IcomRadio
    transport: IcomCivTransport
    lan: LanRadioFake
    sent: list[bytes]

    async def sent_count(self, count: int) -> None:
        """Wait until ``count`` CI-V frames have left this side."""
        while len(self.sent) < count:
            await asyncio.sleep(0)

    def blocking_write(self, level: int) -> asyncio.Task[CivFrame | None]:
        return asyncio.ensure_future(
            self.radio._civ_runtime._execute_civ_raw(_write(level))
        )


@pytest.fixture
async def lan(virtual_clock: VirtualClock) -> AsyncIterator[_Lan]:
    radio = IcomRadio("192.168.1.100", timeout=_WINDOW, model="IC-7610")
    radio._civ_get_timeout = _WINDOW
    radio._civ_min_interval = 0.0
    transport = IcomCivTransport()
    transport.state = ConnectionState.CONNECTED
    radio._civ_transport = transport
    radio._radio_addr = _ADDR
    fake = LanRadioFake(transport, first_seq=9)
    sent: list[bytes] = []
    to_radio = transport._raw_send

    def record(data: bytes) -> None:
        if len(data) > CIV_HEADER_SIZE:
            sent.append(bytes(data))
        to_radio(data)

    transport._raw_send = record
    transport.start_retransmit_loop()
    radio._civ_runtime.start_pump()
    fake.deliver(fake.idle())
    try:
        yield _Lan(radio, transport, fake, sent)
    finally:
        transport.state = ConnectionState.DISCONNECTED
        radio._civ_transport = None
        for task in (transport._retransmit_task, radio._civ_rx_task):
            if task is not None:
                task.cancel()


async def _settle(clock: VirtualClock) -> None:
    await clock.elapse(RETRANSMIT_PERIOD / 4)


async def test_lan_gap_holds_the_later_answer_until_it_is_filled(
    lan: _Lan, virtual_clock: VirtualClock
) -> None:
    """(1) Write sink A's FB is lost; blocking write B's FA arrives first.

    B's FA must not reach the tracker before A's FB: it would land on the
    oldest entry (A) and leave B for A's FB, a false success. It waits
    for the resend, then each answer settles its own write.
    """
    tracker = lan.radio._civ_request_tracker
    await lan.radio._civ_runtime._execute_civ_raw(_write(1), wait_response=False)
    b = lan.blocking_write(2)
    await lan.sent_count(2)
    fb_a, fa_b = lan.lan.packet(_fb()), lan.lan.packet(_fa())

    lan.lan.deliver(fa_b)
    await _settle(virtual_clock)
    assert not b.done(), "B's answer overtook the missing datagram"
    assert tracker.ack_sink_count == 1

    lan.lan.serve_retransmits = True
    await virtual_clock.elapse(RETRANSMIT_PERIOD)

    assert fb_a in lan.lan.retransmit_requests
    result = await b
    assert result is not None and result.command == 0xFA
    assert tracker.ack_sink_count == 0
    assert lan.transport.rx_order_stats == RxOrderStats(reordered=1)


async def test_lan_gap_given_up_drops_the_late_datagram(
    lan: _Lan, virtual_clock: VirtualClock
) -> None:
    """(1) The gap is never resent: the transport gives it up.

    Then B's FA is delivered and lands on the oldest open entry, sink A
    (the stated one-loss off-by-one); B waits. A's FB resent after the
    give-up is late: it is dropped, never delivered after B's FA, so B is
    never reported a success -- it ends at its own window.
    """
    tracker = lan.radio._civ_request_tracker
    await lan.radio._civ_runtime._execute_civ_raw(_write(1), wait_response=False)
    b = lan.blocking_write(2)
    await lan.sent_count(2)
    fb_a, fa_b = lan.lan.packet(_fb()), lan.lan.packet(_fa())

    lan.lan.deliver(fa_b)
    await virtual_clock.elapse(_BUDGET)
    assert lan.transport.rx_order_stats == RxOrderStats(lost=1)
    assert tracker.ack_sink_count == 0  # the FA retired the oldest entry, A
    assert not b.done()

    lan.lan.deliver(fb_a)
    await _settle(virtual_clock)
    assert lan.transport.rx_order_stats == RxOrderStats(lost=1, late=1)
    assert not b.done(), "the late datagram reached the tracker after B's FA"

    await virtual_clock.elapse(_WINDOW)
    with pytest.raises(CivTimeoutError):
        await b


async def test_lan_duplicate_fb_reaches_the_tracker_once(
    lan: _Lan, virtual_clock: VirtualClock
) -> None:
    """(2) A resent FB is a duplicate: the next write is not settled by it."""
    tracker = lan.radio._civ_request_tracker
    a = lan.blocking_write(1)
    await lan.sent_count(1)
    fb = lan.lan.packet(_fb())
    lan.lan.deliver(fb, fb)
    result_a = await a
    assert result_a is not None and result_a.command == 0xFB

    b = lan.blocking_write(2)
    await lan.sent_count(2)
    await _settle(virtual_clock)
    assert not b.done(), "the duplicate FB settled the next write"
    lan.lan.deliver(lan.lan.packet(_fa()))
    result_b = await b

    assert result_b is not None and result_b.command == 0xFA
    assert lan.transport.rx_order_stats == RxOrderStats(duplicate=1)
    assert tracker.snapshot_stats()["ack_orphans"] == 0


@pytest.mark.parametrize(
    ("answer_k", "answer_k1", "got_k", "got_k1"),
    [
        (_fb, _fb, "ack", 0xFB),
        # The residual: the answers differ, so each write gets its
        # neighbour's -- one answer each, swapped (off-by-one).
        (_fa, _fb, "ack", 0xFA),
        (_fb, _fa, "nak", 0xFB),
    ],
    ids=["both-acked", "k-refused", "k1-refused"],
)
async def test_lan_outbound_reorder_off_by_one_is_the_only_damage(
    lan: _Lan,
    virtual_clock: VirtualClock,
    answer_k: Callable[[], bytes],
    answer_k1: Callable[[], bytes],
    got_k: str,
    got_k1: int,
) -> None:
    """(3) Raw-transaction write K, then worker write K+1; the radio
    handles K+1 first (K lost and resent), so K+1's answer arrives first.

    Neither blocking caller fails early (ordered evidence never fails a
    blocking waiter), each gets exactly one answer, and nothing is left
    over; the FB/FA goes to the oldest open write, so differing answers
    are swapped.
    """
    runtime = lan.radio._civ_runtime
    tracker = lan.radio._civ_request_tracker
    k = asyncio.ensure_future(
        runtime.execute_civ_transaction(_write(1), expect="ack", timeout=_WINDOW)
    )
    await lan.sent_count(1)
    k1 = lan.blocking_write(2)
    await lan.sent_count(2)

    lan.lan.deliver(lan.lan.packet(answer_k1()))
    await _settle(virtual_clock)
    assert not k1.done(), "a blocking caller was settled or failed early"
    lan.lan.deliver(lan.lan.packet(answer_k()))
    result_k, result_k1 = await k, await k1

    assert result_k.status == got_k
    assert result_k1 is not None and result_k1.command == got_k1
    assert tracker.pending_count == 0
    assert tracker.timeout_count == 0
    assert tracker.snapshot_stats()["ack_orphans"] == 0


def _read(sub: int) -> bytes:
    return build_civ_frame(_ADDR, CONTROLLER_ADDR, 0x14, sub=sub)


async def test_lan_stated_residual_delayed_read_refusal_charged_to_next_setter(
    lan: _Lan, virtual_clock: VirtualClock
) -> None:
    """Stated residual (same exposure as main, MOR-4209), pinned as is.

    Read sink A (AF level) is delayed on the way out (resent), so the radio
    handles keyed read B (RF gain) first and answers it. B's directed
    answer on the ordered LAN link retires A by evidence. A's refusal then
    arrives late, while blocking setter C is open: it is charged to C.
    C sees the refusal and A is not marked refused. Any change that makes
    this better or worse must trip this row.
    """
    runtime = lan.radio._civ_runtime
    tracker = lan.radio._civ_request_tracker
    await runtime._execute_civ_raw(_read(0x01), wait_response=False)  # A
    await runtime._execute_civ_raw(_read(0x02), wait_response=False)  # B
    await lan.sent_count(2)
    lan.lan.deliver(
        lan.lan.packet(
            build_civ_frame(CONTROLLER_ADDR, _ADDR, 0x14, sub=0x02, data=b"\x01\x28")
        )
    )
    await _settle(virtual_clock)
    assert tracker.ack_sink_count == 0, "B's answer did not retire A"

    c = lan.blocking_write(3)
    await lan.sent_count(3)
    lan.lan.deliver(lan.lan.packet(_fa()))  # A's late refusal
    result_c = await c

    assert result_c is not None and result_c.command == 0xFA
    assert tracker.take_refused_reads() == ()
    assert tracker.pending_count == 0
