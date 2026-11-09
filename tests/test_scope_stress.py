"""Stress tests for scope pipeline: high frame rate, slow consumer, clean teardown.

- Feed many scope frames into the pipeline with a slow consumer (artificial delay).
- Run for a bounded duration; assert queue size does not grow without bound.
- After stop, assert no lingering tasks and clean teardown.
Uses mock transport and scope helpers; no real hardware.
"""

from __future__ import annotations

import asyncio
from contextlib import suppress
from typing import ClassVar, TYPE_CHECKING
from unittest.mock import call, create_autospec

import pytest

from rigplane.core.transport import IcomCivTransport
from rigplane import IC_7610_ADDR
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.radio import IcomRadio
from rigplane.profiles import resolve_radio_profile
from rigplane.runtime.radio_scope_source import RadioScopeSource
from rigplane.runtime.scope_session import ScopeSession
from rigplane.scope import ScopeFrame
from rigplane.scope.models import (
    Coverage,
    GrantedSubscription,
    ScopeTarget,
    Subscription,
    View,
)
from rigplane.testing.timing import VirtualClock
from rigplane.types import bcd_encode
from rigplane.web.handlers.scope import ScopeHandler
from rigplane.web.protocol import (
    decode_scope_frame_v2,
    decode_scope_granted,
    encode_scope_subscription,
)
from rigplane.web.server import WebServer
from rigplane.ws import WS_OP_TEXT

from _helpers import wrap_civ_in_udp as _wrap_civ_in_udp

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

# Reuse scope payload building (single-packet LAN mode: seq=1, seq_max=1).


def _bcd_byte(value: int) -> int:
    return ((value // 10) << 4) | (value % 10)


def _seq1_payload_single(
    receiver: int,
    mode: int,
    start_hz: int,
    end_hz: int,
    pixels: bytes,
) -> bytes:
    """Build single-packet scope payload (seq=1, seq_max=1) with pixel data."""
    return bytes(
        [
            _bcd_byte(1),
            _bcd_byte(1),
            mode,
            *bcd_encode(start_hz),
            *bcd_encode(end_hz),
            0x00,  # oor
            *pixels,
        ]
    )


def _scope_civ_frame(receiver: int, payload_after_receiver: bytes) -> bytes:
    return build_civ_frame(
        CONTROLLER_ADDR,
        IC_7610_ADDR,
        0x27,
        sub=0x00,
        data=bytes([receiver]) + payload_after_receiver,
    )


class MockTransport:
    """Minimal mock transport: queue responses for receive_packet."""

    #: The LAN transport's ingress-order contract (MOR-4209).
    ordered_ingress: ClassVar[bool] = IcomCivTransport.ordered_ingress

    def __init__(self) -> None:
        self._responses: asyncio.Queue[bytes] = asyncio.Queue()
        self.sent_packets: list[bytes] = []
        self.my_id: int = 0x00010001
        self.remote_id: int = 0xDEADBEEF

    async def connect(self, host: str, port: int) -> None:
        pass

    async def disconnect(self) -> None:
        pass

    def start_ping_loop(self) -> None:
        pass

    def start_retransmit_loop(self) -> None:
        pass

    async def send_tracked(self, data: bytes) -> None:
        self.sent_packets.append(data)

    async def receive_packet(self, timeout: float = 5.0) -> bytes:
        return await asyncio.wait_for(self._responses.get(), timeout=timeout)

    def queue_response(self, data: bytes) -> None:
        self._responses.put_nowait(data)


def _make_scope_udp_packets(count: int) -> list[bytes]:
    """Build `count` UDP packets, each carrying one complete scope frame (single-packet mode)."""
    out: list[bytes] = []
    for i in range(count):
        pixels = bytes([i % 256] * 32)
        payload = _seq1_payload_single(
            receiver=0,
            mode=1,
            start_hz=14_000_000,
            end_hz=14_350_000,
            pixels=pixels,
        )
        civ = _scope_civ_frame(0, payload)
        out.append(_wrap_civ_in_udp(civ, seq=i + 1))
    return out


@pytest.fixture
def radio_with_mock() -> tuple[IcomRadio, MockTransport]:
    """Radio with mock CIV/ctrl transport, connected state, radio_addr set for scope."""
    t = MockTransport()
    r = IcomRadio("192.168.1.1", model="IC-7610")
    r._civ_transport = t  # type: ignore[assignment]
    r._ctrl_transport = t  # type: ignore[assignment]
    r._connected = True
    r._radio_addr = IC_7610_ADDR
    return r, t


# ---------------------------------------------------------------------------
# Scope stress: high frame rate, slow consumer, queue bounded
# ---------------------------------------------------------------------------


class _SlowScopeSocket:
    """Connection fake whose binary sends wait for explicit drain permits."""

    def __init__(self) -> None:
        self.incoming: asyncio.Queue[str | None] = asyncio.Queue()
        self.text: list[str] = []
        self.binary: list[bytes] = []
        self.permits = asyncio.Semaphore(0)
        self.in_flight: bytes | None = None
        self.send_attempts = 0
        self.closed = False

    async def recv(self) -> tuple[int, bytes]:
        message = await self.incoming.get()
        if message is None:
            raise EOFError
        return WS_OP_TEXT, message.encode()

    async def send_text(self, text: str) -> None:
        self.text.append(text)

    async def send_binary(self, data: bytes) -> None:
        assert self.in_flight is None
        self.in_flight = data
        self.send_attempts += 1
        try:
            await self.permits.acquire()
            self.binary.append(data)
        finally:
            self.in_flight = None

    async def close(self, code: int = 1000, reason: str = "") -> None:
        self.abort()

    def abort(self) -> None:
        self.closed = True
        self.incoming.put_nowait(None)

    def is_alive(self) -> bool:
        return not self.closed


@pytest.mark.slow
@pytest.mark.asyncio
@pytest.mark.timeout(20)
@pytest.mark.parametrize("model", ["ic7300", "ic7610"])
async def test_scope_stress_slow_consumer_queue_bounded(
    model: str,
    virtual_clock: VirtualClock,
) -> None:
    """Bound v2 delivery to two hub frames plus one blocked send; newest wins."""
    server = WebServer(None)
    writer = create_autospec(ScopeSession, instance=True, spec_set=True)
    writer.tuned_frequency.return_value = 14_100_000
    source = RadioScopeSource(resolve_radio_profile(model=model), writer)
    server.scope_hub.register("radio", source)
    ws = _SlowScopeSocket()
    handler = ScopeHandler(ws, server)
    consumer = asyncio.create_task(handler.run())
    num_frames = 400
    width = 1024
    view = View(14_090_000, 14_110_000)
    try:
        ws.incoming.put_nowait(
            encode_scope_subscription(Subscription("radio", 0, view, width, 10))
        )
        await virtual_clock.elapse(0)
        assert len(ws.text) == 1
        assert decode_scope_granted(ws.text[0]) == GrantedSubscription(
            "radio", 0, view, Coverage(view.start_hz, view.end_hz), width, 10
        )
        stream = handler._streams[ScopeTarget("radio", 0)]
        subscription = stream.subscription
        sender = stream.sender
        assert sender is not None and not sender.done()
        assert writer.set_output.await_args_list == [call(True)]

        def publish(index: int) -> None:
            source.feed_frame(
                ScopeFrame(
                    0,
                    1,
                    int(view.start_hz),
                    int(view.end_hz),
                    bytes([index % 161]) * width,
                    False,
                )
            )

        publish(0)
        await virtual_clock.elapse(0)
        assert ws.in_flight is not None
        wire_size = len(ws.in_flight)
        assert decode_scope_frame_v2(ws.in_flight).seq == 1
        max_retained_frames = 0
        for index in range(1, num_frames):
            publish(index)
            retained = [
                delivery[1]
                for delivery in (subscription._latest, subscription._pending)
                if delivery is not None
            ]
            max_retained_frames = max(max_retained_frames, len(retained))
            assert len(retained) <= 2
            assert sum(map(len, retained)) + len(ws.in_flight) <= 3 * wire_size
            assert stream.sender is sender and not sender.done()
            assert ws.send_attempts == 1 and ws.binary == []
            await virtual_clock.elapse(0.001)
        assert max_retained_frames == 2

        await virtual_clock.elapse(0.1)
        assert subscription._pending is None and subscription._timer is None
        assert subscription._latest is not None
        assert decode_scope_frame_v2(subscription._latest[1]).seq == num_frames

        ws.permits.release()
        await virtual_clock.elapse(0)
        assert ws.in_flight is not None
        newest = decode_scope_frame_v2(ws.in_flight)
        assert newest.seq == num_frames
        assert newest.pixels == bytes([(num_frames - 1) % 161]) * width
        assert newest.source_id == "radio" and newest.receiver == 0
        assert subscription._latest is None and subscription._pending is None
        ws.permits.release()
        await virtual_clock.elapse(0)
        assert [decode_scope_frame_v2(data).seq for data in ws.binary] == [
            1,
            num_frames,
        ]
        assert ws.send_attempts == 2 and ws.in_flight is None

        ws.incoming.put_nowait(None)
        await virtual_clock.elapse(0)
        assert consumer.done()
        await consumer
        assert sender.done() and handler._streams == {}
        assert subscription._latest is None and subscription._pending is None
        assert subscription._timer is None
        assert writer.set_output.await_args_list == [
            call(True),
            call(False, release=True),
        ]
        assert handler not in server._scope_handlers
    finally:
        consumer.cancel()
        with suppress(asyncio.CancelledError):
            await consumer


@pytest.mark.asyncio
async def test_scope_stress_clean_teardown_no_lingering_tasks(
    radio_with_mock: tuple[IcomRadio, MockTransport],
) -> None:
    """After stop (disconnect / pump stopped), background tasks are cancelled and no lingering work."""
    radio, transport = radio_with_mock
    for pkt in _make_scope_udp_packets(150):
        transport.queue_response(pkt)

    radio._civ_runtime.start_pump()
    received: list[ScopeFrame] = []
    consumer_done: asyncio.Event = asyncio.Event()

    async def consume(stream: AsyncGenerator[ScopeFrame, None]) -> None:
        try:
            async for frame in stream:
                received.append(frame)
                await asyncio.sleep(0.01)
        finally:
            consumer_done.set()

    consumer_task = asyncio.create_task(consume(radio.scope_stream()))
    await asyncio.sleep(0.5)
    radio._connected = False
    await asyncio.wait_for(consumer_task, timeout=2.0)
    assert consumer_done.is_set()

    await radio._civ_runtime.stop_pump()
    await asyncio.sleep(0.15)

    civ_rx_task = getattr(radio, "_civ_rx_task", None)
    assert civ_rx_task is None or civ_rx_task.done(), (
        "CIV RX pump task should be stopped"
    )
    assert consumer_task.done()
