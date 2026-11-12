"""The LAN transport delivers CI-V datagrams in sequence order (MOR-4205).

A fake LAN radio (``tests/lan_reorder_fake.py``) hands numbered datagrams to
a real ``IcomCivTransport`` through its datagram entry point, reordered,
duplicated or with a number missing. The upper layer (the packet queue the
CI-V pump reads) must see them strictly by number; duplicates are dropped; a
missing number is asked for again and given up when the retransmit budget
runs out. Timeouts run on the shared ``VirtualClock``.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, patch

import pytest

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.core.transport import (
    MAX_MISSING,
    RENUMBER_JUMP,
    REORDER_WINDOW,
    RETRANSMIT_PERIOD,
    ConnectionState,
    IcomCivTransport,
    IcomTransport,
    RxOrderStats,
)
from rigplane.testing.timing import VirtualClock

from _helpers import wrap_civ_in_udp
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


async def test_a_numbering_jump_reanchors_onto_the_live_stream(
    transport: IcomTransport, caplog: pytest.LogCaptureFixture
) -> None:
    """A stale-high first packet must not starve the stream behind it.

    The field failure (owner's IC-705 over LAN, MOR-4416): the first
    numbered packet after a connect carried a number from the session that
    had just ended, far ahead of the radio's live stream; every fresh
    packet then counted as a duplicate behind that anchor until the CI-V
    data watchdog tore the link down. A run of consecutive behind-packets
    longer than ``REORDER_WINDOW`` is no reorder — the anchor is stale and
    moves onto the live stream (counted once as ``reanchored``).
    """
    stale = 20001
    radio = _radio(transport, 2 * REORDER_WINDOW)
    radio.sent[stale] = wrap_civ_in_udp(_frame(0), seq=stale)

    with caplog.at_level(logging.INFO, logger="rigplane.core.transport"):
        radio.deliver(stale)
        assert drain_seqs(transport) == [stale]  # the anchor packet itself
        radio.deliver(*range(1, REORDER_WINDOW + 2))

    # The run's first REORDER_WINDOW packets drop as duplicates like before;
    # the next one re-anchors and is delivered, and the live stream flows on.
    assert drain_seqs(transport) == [REORDER_WINDOW + 1]
    assert transport.rx_order_stats == RxOrderStats(
        duplicate=REORDER_WINDOW, reanchored=1
    )

    radio.deliver(*range(REORDER_WINDOW + 2, 2 * REORDER_WINDOW + 1))
    assert drain_seqs(transport) == list(
        range(REORDER_WINDOW + 2, 2 * REORDER_WINDOW + 1)
    )

    lines = [r.getMessage() for r in caplog.records]
    assert sum("re-anchored" in line for line in lines) == 1
    reanchor = next(line for line in lines if "re-anchored" in line)
    # The stale packet 20001 anchored delivery; the frontier stood at 20002.
    assert f"old_next={stale + 1}" in reanchor
    assert f"new_seq={REORDER_WINDOW + 1}" in reanchor
    assert f"behind_run={REORDER_WINDOW + 1}" in reanchor


async def test_a_gap_right_after_a_reanchor_is_tracked(
    transport: IcomTransport,
) -> None:
    """Re-anchoring restarts gap tracking at the new epoch's first packet:
    a number skipped right after the jump is held for, not skipped over."""
    stale = 20001
    radio = _radio(transport, REORDER_WINDOW + 4)
    radio.sent[stale] = wrap_civ_in_udp(_frame(0), seq=stale)

    radio.deliver(stale)
    assert drain_seqs(transport) == [stale]
    radio.deliver(*range(1, REORDER_WINDOW + 2))  # the run re-anchors at 257
    assert drain_seqs(transport) == [REORDER_WINDOW + 1]

    skip = REORDER_WINDOW + 2  # 258
    radio.deliver(skip + 1)  # 259 ahead of the missing 258: held, not lost
    assert drain_seqs(transport) == []
    assert skip in transport.rx_missing

    radio.deliver(skip)
    assert drain_seqs(transport) == [skip, skip + 1]
    assert transport.rx_order_stats == RxOrderStats(
        duplicate=REORDER_WINDOW, reordered=1, reanchored=1
    )


async def test_a_run_at_the_window_edge_still_drops_as_duplicates(
    transport: IcomTransport,
) -> None:
    """A behind-run that never exceeds the window is no jump: drop as today."""
    stale = 20001
    radio = _radio(transport, REORDER_WINDOW)
    radio.sent[stale] = wrap_civ_in_udp(_frame(9), seq=stale)

    radio.deliver(stale)
    assert drain_seqs(transport) == [stale]  # the anchor packet itself
    radio.deliver(*range(1, REORDER_WINDOW + 1))

    assert drain_seqs(transport) == []
    assert transport.rx_order_stats == RxOrderStats(duplicate=REORDER_WINDOW)


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


async def test_forward_wrapping_numbering_jump_reanchors_without_phantom_loss(
    transport: IcomTransport, caplog: pytest.LogCaptureFixture
) -> None:
    radio = _radio(transport, 20, first_seq=0)
    stale = 45633
    radio.sent[stale] = wrap_civ_in_udp(_frame(0), seq=stale)
    radio.deliver(stale)
    assert drain_seqs(transport) == [stale]

    with caplog.at_level(logging.WARNING, logger="rigplane.core.transport"):
        radio.deliver(*range(20))

    assert drain_seqs(transport) == list(range(20))
    assert transport.rx_order_stats == RxOrderStats(reanchored=1)
    assert transport.rx_missing == {}
    assert not transport._rx_held
    assert not transport._rx_given_up
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert "re-anchored" in warnings[0]
    assert "old_next=45634" in warnings[0]
    assert "new_seq=0" in warnings[0]
    assert "forward_jump=19903" in warnings[0]


async def test_forward_gap_exactly_max_missing_is_tracked(
    transport: IcomTransport, virtual_clock: VirtualClock
) -> None:
    radio = _radio(transport, MAX_MISSING + 1)
    radio.deliver(1, 1 + MAX_MISSING)
    assert drain_seqs(transport) == [1]
    assert set(transport.rx_missing) == set(range(2, 1 + MAX_MISSING))
    assert transport.rx_order_stats == RxOrderStats()

    radio.serve_retransmits = True
    await virtual_clock.elapse(RETRANSMIT_PERIOD)

    assert set(radio.retransmit_requests) == set(range(2, 1 + MAX_MISSING))
    assert drain_seqs(transport) == list(range(2, MAX_MISSING + 2))
    assert transport.rx_order_stats == RxOrderStats(reordered=MAX_MISSING - 1)


@pytest.mark.parametrize(
    "delta", [MAX_MISSING + 1, 120, RENUMBER_JUMP, RENUMBER_JUMP + 1]
)
async def test_forward_gap_bands_preserve_loss_visibility(
    transport: IcomTransport,
    virtual_clock: VirtualClock,
    caplog: pytest.LogCaptureFixture,
    delta: int,
) -> None:
    anchor = 100
    new_seq = anchor + delta
    radio = _radio(transport, delta + REORDER_WINDOW + 3, first_seq=anchor)
    radio.deliver(anchor)
    assert drain_seqs(transport) == [anchor]

    with caplog.at_level(logging.WARNING, logger="rigplane.core.transport"):
        radio.deliver(*range(new_seq, new_seq + REORDER_WINDOW + 3))
        await virtual_clock.elapse(_BUDGET)

    assert drain_seqs(transport) == list(range(new_seq, new_seq + REORDER_WINDOW + 3))
    burst = delta <= RENUMBER_JUMP
    assert transport.rx_order_stats == RxOrderStats(
        lost=delta - 1 if burst else 0, reanchored=0 if burst else 1
    )
    assert transport.rx_last_seq == new_seq + REORDER_WINDOW + 2
    assert transport.rx_missing == {}
    assert not transport._rx_held
    assert radio.retransmit_requests == []
    warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    if burst:
        assert warnings[0] == (
            f"CI-V burst loss {delta - 1} packets "
            f"(last_seq={anchor}, new_seq={new_seq})"
        )
    else:
        assert "re-anchored" in warnings[0]


@pytest.mark.parametrize("give_up_first", [False, True], ids=["held", "given-up"])
async def test_burst_loss_does_not_recount_an_older_gap(
    transport: IcomTransport,
    virtual_clock: VirtualClock,
    give_up_first: bool,
) -> None:
    radio = _radio(transport, 125)
    radio.deliver(1, 3)
    assert drain_seqs(transport) == [1]
    if give_up_first:
        await virtual_clock.elapse(_BUDGET)
        assert drain_seqs(transport) == [3]
        assert transport.rx_order_stats == RxOrderStats(lost=1)

    radio.deliver(123, 124, 125)
    assert drain_seqs(transport) == (
        [123, 124, 125] if give_up_first else [3, 123, 124, 125]
    )
    await virtual_clock.elapse(_BUDGET)
    assert transport.rx_order_stats == RxOrderStats(lost=120)
    assert transport.rx_missing == {}
    assert not transport._rx_held

    radio.deliver(122)
    assert drain_seqs(transport) == []
    assert transport.rx_order_stats == RxOrderStats(lost=120, late=1)
