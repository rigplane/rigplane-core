"""The LAN transport delivers CI-V datagrams in sequence order (MOR-4205).

A fake LAN radio (``tests/lan_reorder_fake.py``) hands numbered datagrams to
a real ``IcomCivTransport`` through its datagram entry point, reordered,
duplicated or with a number missing. The upper layer (the packet queue the
CI-V pump reads) must see them strictly by number; duplicates are dropped; a
missing number is asked for again and given up when the retransmit budget
runs out. Timeouts run on the shared ``VirtualClock``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.core.transport import (
    REORDER_WINDOW,
    RETRANSMIT_PERIOD,
    ConnectionState,
    IcomCivTransport,
    IcomTransport,
    RxOrderStats,
)
from rigplane.testing.timing import VirtualClock

from lan_reorder_fake import LanRadioFake, drain_seqs
from test_transport import _FakeLoop

pytestmark = pytest.mark.timeout(10)

_RADIO = 0xA4
# The retransmit loop asks for a missing number on each tick and gives it up
# after its fifth request: well past this many periods it is gone.
_BUDGET = 6 * RETRANSMIT_PERIOD


def _frame(n: int) -> bytes:
    level = bytes([n >> 8, n & 0xFF])
    return build_civ_frame(CONTROLLER_ADDR, _RADIO, 0x15, sub=0x02, data=level)


@pytest.fixture
async def transport(virtual_clock: VirtualClock) -> AsyncIterator[IcomTransport]:
    transport = IcomCivTransport()
    transport.state = ConnectionState.CONNECTED
    transport.start_retransmit_loop()
    try:
        yield transport
    finally:
        transport.state = ConnectionState.DISCONNECTED
        task = transport._retransmit_task
        assert task is not None
        task.cancel()


def _radio(transport: IcomTransport, count: int, *, first_seq: int = 1) -> LanRadioFake:
    radio = LanRadioFake(transport, first_seq=first_seq)
    for n in range(count):
        radio.packet(_frame(n))
    return radio


def test_ordered_delivery_is_the_lan_ingress_contract() -> None:
    assert IcomCivTransport.ordered_ingress is True
    # The control and audio ports keep arrival order.
    assert IcomTransport.ordered_ingress is False


async def test_reordered_datagrams_reach_the_upper_layer_in_order(
    transport: IcomTransport,
) -> None:
    radio = _radio(transport, 5)

    radio.deliver(1, 3, 2, 5, 4)

    assert drain_seqs(transport) == [1, 2, 3, 4, 5]
    assert transport.rx_order_stats == RxOrderStats(reordered=2)


async def test_duplicates_are_dropped(transport: IcomTransport) -> None:
    radio = _radio(transport, 4)

    radio.deliver(1, 2, 2, 1, 4, 4, 3)

    assert drain_seqs(transport) == [1, 2, 3, 4]
    assert transport.rx_order_stats == RxOrderStats(reordered=1, duplicate=3)


async def test_a_gap_waits_for_the_resend_the_transport_asks_for(
    transport: IcomTransport, virtual_clock: VirtualClock
) -> None:
    radio = _radio(transport, 4)

    radio.deliver(1, 3, 4)
    assert drain_seqs(transport) == [1]  # 3 and 4 wait for 2

    radio.serve_retransmits = True
    await virtual_clock.elapse(RETRANSMIT_PERIOD)

    assert radio.retransmit_requests == [2]
    assert drain_seqs(transport) == [2, 3, 4]
    assert transport.rx_order_stats == RxOrderStats(reordered=1)
    assert transport.rx_missing == {}


async def test_a_gap_never_resent_times_out_and_delivery_moves_on(
    transport: IcomTransport, virtual_clock: VirtualClock
) -> None:
    radio = _radio(transport, 5)

    radio.deliver(1, 2, 4, 5)
    await virtual_clock.elapse(3 * RETRANSMIT_PERIOD)
    assert drain_seqs(transport) == [1, 2]  # still asking for 3

    await virtual_clock.elapse(_BUDGET)
    assert drain_seqs(transport) == [4, 5]
    assert radio.retransmit_requests.count(3) == 5
    assert transport.rx_order_stats == RxOrderStats(lost=1)

    radio.deliver(3)  # its resend comes after all: dropped, never out of order
    assert drain_seqs(transport) == []
    assert transport.rx_order_stats == RxOrderStats(lost=1, late=1)


async def test_the_hold_is_bounded_by_the_reorder_window(
    transport: IcomTransport,
) -> None:
    last = REORDER_WINDOW + 3  # 2 is missing and 3..last span past the window
    radio = _radio(transport, last)

    radio.deliver(1, *range(3, last + 1))  # 2 never comes

    assert drain_seqs(transport) == [1, *range(3, last + 1)]
    assert transport.rx_order_stats == RxOrderStats(lost=1)


async def test_order_holds_across_the_sequence_wrap(transport: IcomTransport) -> None:
    radio = _radio(transport, 4, first_seq=0xFFFE)

    radio.deliver(0xFFFE, 0x0001, 0xFFFF, 0x0000)

    assert drain_seqs(transport) == [0xFFFE, 0xFFFF, 0x0000, 0x0001]
    assert transport.rx_order_stats == RxOrderStats(reordered=2)


async def test_zero_is_ordered_like_any_number_at_the_wrap(
    transport: IcomTransport,
) -> None:
    radio = _radio(transport, 3, first_seq=0xFFFE)

    radio.deliver(0xFFFE, 0x0000, 0xFFFF)

    assert drain_seqs(transport) == [0xFFFE, 0xFFFF, 0x0000]
    assert transport.rx_order_stats == RxOrderStats(reordered=1)


async def test_a_duplicate_zero_is_dropped(transport: IcomTransport) -> None:
    radio = _radio(transport, 3, first_seq=0xFFFF)

    radio.deliver(0xFFFF, 0x0000, 0x0000, 0x0001, 0x0000)

    assert drain_seqs(transport) == [0xFFFF, 0x0000, 0x0001]
    assert transport.rx_order_stats == RxOrderStats(duplicate=2)


async def test_a_zero_resent_after_it_was_given_up_is_late(
    transport: IcomTransport, virtual_clock: VirtualClock
) -> None:
    radio = _radio(transport, 4, first_seq=0xFFFE)

    radio.deliver(0xFFFE, 0xFFFF, 0x0001)
    await virtual_clock.elapse(_BUDGET)
    assert drain_seqs(transport) == [0xFFFE, 0xFFFF, 0x0001]

    radio.deliver(0x0000)

    assert drain_seqs(transport) == []
    assert transport.rx_order_stats == RxOrderStats(lost=1, late=1)


async def test_an_old_duplicate_far_behind_is_never_redelivered(
    transport: IcomTransport,
) -> None:
    radio = _radio(transport, 1100)
    radio.deliver(*range(1, 1101))
    assert drain_seqs(transport) == list(range(1, 1101))

    radio.deliver(1)

    assert drain_seqs(transport) == []
    assert transport.rx_order_stats == RxOrderStats(duplicate=1)


async def test_a_restarted_numbering_starts_with_the_new_session(
    transport: IcomTransport,
) -> None:
    """The radio numbers afresh only for a new session, and a new session
    goes through ``connect``; within the old one a low number is old."""
    radio = _radio(transport, 500)
    radio.deliver(*range(1, 501))
    assert drain_seqs(transport) == list(range(1, 501))
    radio.deliver(1)
    assert drain_seqs(transport) == []

    with (
        patch(
            "rigplane.transport.asyncio.get_running_loop",
            return_value=_FakeLoop(("192.168.2.194", 50002)),
        ),
        patch.object(transport, "_discover", new=AsyncMock()),
        patch.object(transport, "_ready_handshake", new=AsyncMock()),
    ):
        await transport.connect("192.168.2.1", 50002)
    fresh = _radio(transport, 500)

    fresh.deliver(*range(1, 501))

    assert drain_seqs(transport) == list(range(1, 501))
    assert transport.rx_order_stats == RxOrderStats(duplicate=1)


async def test_the_control_and_audio_ports_keep_arrival_order() -> None:
    transport = IcomTransport()
    radio = _radio(transport, 3)

    radio.deliver(1, 3, 2)

    assert drain_seqs(transport) == [1, 3, 2]
    assert transport.rx_order_stats == RxOrderStats()
