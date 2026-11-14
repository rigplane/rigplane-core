"""MOR-4455: 300 ms-link interleaving through the real tracker.

A blocking write registers after ``_drain_ack_sinks_before_blocking`` spent
its 0.5 s budget and tombstoned the still-open fire-and-forget sink. The
late bare answer (an FA refusing the earlier read, or the earlier write's
own FB) must land on that tombstone by send order — not reject the write,
not succeed it early. The write settles only on its own FB.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from rigplane import IC_7610_ADDR
from rigplane.civ import read_identity_from_frame
from rigplane.commands import (
    CONTROLLER_ADDR,
    build_civ_frame,
    parse_civ_frame,
)
from rigplane.radio import IcomRadio

from _helpers import (
    ack_response as _ack_response,
)
from _helpers import (
    observe_selected_receiver,
)
from _helpers import (
    wrap_civ_in_udp,
)

_REFUSED_READ = build_civ_frame(IC_7610_ADDR, CONTROLLER_ADDR, 0x1C, sub=0x01)

_PTT_ON = build_civ_frame(IC_7610_ADDR, CONTROLLER_ADDR, 0x1C, sub=0x00, data=b"\x01")
_PTT_OFF = build_civ_frame(IC_7610_ADDR, CONTROLLER_ADDR, 0x1C, sub=0x00, data=b"\x00")


def _fa_response() -> bytes:
    return wrap_civ_in_udp(build_civ_frame(CONTROLLER_ADDR, IC_7610_ADDR, 0xFA))


class _MockTransport:
    """Minimal UDP transport: queued responses released per send count."""

    ordered_ingress = True

    def __init__(self) -> None:
        self.connected = False
        self.sent_packets: list[bytes] = []
        self._responses: asyncio.Queue[bytes] = asyncio.Queue()
        self._responses_by_send: dict[int, list[bytes]] = {}
        self.my_id = 0x00010001
        self.remote_id = 0xDEADBEEF
        self.send_seq = 0
        self.rx_packet_count = 0

    async def send_tracked(self, data: bytes, **kwargs) -> None:
        self.sent_packets.append(data)
        self.send_seq += 1
        for pkt in self._responses_by_send.pop(self.send_seq, []):
            self._responses.put_nowait(pkt)

    async def receive_packet(self, timeout: float = 5.0) -> bytes:
        return await asyncio.wait_for(self._responses.get(), timeout=timeout)

    def queue_response_on_send(self, send_number: int, data: bytes) -> None:
        self._responses_by_send.setdefault(send_number, []).append(data)

    def start_ping_loop(self) -> None:
        pass

    def start_retransmit_loop(self) -> None:
        pass


def _make_radio(transport: _MockTransport) -> IcomRadio:
    radio = IcomRadio("192.168.1.100", timeout=2.0, model="IC-7610")
    radio._civ_transport = transport
    radio._ctrl_transport = transport
    radio._connected = True
    observe_selected_receiver(radio, 0)
    radio._civ_min_interval = 0.0
    return radio


async def _shutdown(radio: IcomRadio) -> None:
    radio._civ_transport = None
    pump = radio._civ_rx_task
    if pump is not None:
        await pump
    radio._connected = False


class TestSlowLinkInterleaving:
    """The drain budget expires; the late answer must not hit the write."""

    @pytest.mark.realtime(
        reason="the late FA must arrive after the 0.5 s drain budget spent"
    )
    @pytest.mark.asyncio
    async def test_late_refusal_does_not_reject_set_frequency(self) -> None:
        """A refused read's late FA retires its tombstone, not the write.

        On the pre-fix head the drain dropped the read's sink at its 0.5 s
        budget, so the FA routed after the set_frequency frame went out
        settled the write's waiter and ``set_freq`` raised
        ``CommandRejectedError`` although the radio tuned.
        """
        transport = _MockTransport()
        radio = _make_radio(transport)
        try:
            runtime = radio._civ_runtime
            assert (
                await runtime._execute_civ_raw(_REFUSED_READ, wait_response=False)
                is None
            )
            # Released when the set_frequency frame leaves (send 2), i.e.
            # after the drain budget tombstoned the read: first the read's
            # own late FA, then the write's own FB.
            transport.queue_response_on_send(2, _fa_response())
            transport.queue_response_on_send(2, _ack_response())
            await radio.set_freq(7_074_000)
            assert radio._civ_request_tracker.tombstone_count == 0
            assert radio._civ_request_tracker.take_refused_reads() == (
                read_identity_from_frame(parse_civ_frame(_REFUSED_READ)),
            )
        finally:
            await _shutdown(radio)

    @pytest.mark.realtime(
        reason="the late FB must arrive after the 0.5 s drain budget spent"
    )
    @pytest.mark.asyncio
    async def test_late_write_fb_does_not_complete_next_write_early(self) -> None:
        """A fire-and-forget write's late FB retires its tombstone.

        The following blocking write must not take that older FB as its
        own success: it settles only on its own FB.
        """
        transport = _MockTransport()
        radio = _make_radio(transport)
        try:
            runtime = radio._civ_runtime
            assert await runtime._execute_civ_raw(_PTT_ON, wait_response=False) is None
            # Both routed after the PTT-OFF frame leaves (send 2): the
            # ON write's late FB first, the OFF write's own FB second.
            transport.queue_response_on_send(2, _ack_response())
            transport.queue_response_on_send(2, _ack_response())
            started = time.monotonic()
            assert (
                await runtime._execute_civ_raw(_PTT_OFF, wait_response=True) is not None
            )
            assert radio._civ_request_tracker.tombstone_count == 0
            assert time.monotonic() - started >= 0.5, (
                "the blocking write sent before the drain budget spent"
            )
        finally:
            await _shutdown(radio)
