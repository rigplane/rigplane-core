"""Stress tests for scope pipeline: high frame rate, slow consumer, clean teardown.

- Feed many scope frames into the pipeline with a slow consumer (artificial delay).
- Run for a bounded duration; assert queue size does not grow without bound.
- After stop, assert no lingering tasks and clean teardown.
Uses mock transport and scope helpers; no real hardware.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import pytest

from rigplane import IC_7610_ADDR
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.radio import IcomRadio
from rigplane.scope import ScopeFrame
from rigplane.types import bcd_encode

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


@pytest.mark.slow
@pytest.mark.asyncio
async def test_scope_stress_slow_consumer_queue_bounded(
    radio_with_mock: tuple[IcomRadio, MockTransport],
) -> None:
    """Feed many scope frames with a slow consumer; queue must not grow without bound."""
    radio, transport = radio_with_mock
    num_frames = 400
    slow_delay_s = 0.02
    run_duration_s = 3.0
    max_queue_size = 64  # radio's _scope_frame_queue maxsize

    for pkt in _make_scope_udp_packets(num_frames):
        transport.queue_response(pkt)

    radio._civ_runtime.start_pump()
    received: list[ScopeFrame] = []
    max_qsize_seen = 0

    async def consume(stream: AsyncGenerator[ScopeFrame, None]) -> None:
        nonlocal max_qsize_seen
        async for frame in stream:
            qsize = radio._scope_frame_queue.qsize()
            if qsize > max_qsize_seen:
                max_qsize_seen = qsize
            received.append(frame)
            await asyncio.sleep(slow_delay_s)

    consumer = asyncio.create_task(consume(radio.scope_stream()))
    try:
        await asyncio.sleep(run_duration_s)
    finally:
        radio._connected = False
        await asyncio.wait_for(consumer, timeout=2.0)

    assert max_qsize_seen <= max_queue_size, (
        f"Queue grew beyond bound: max_qsize_seen={max_qsize_seen}, max={max_queue_size}"
    )
    assert len(received) >= 1, "Consumer should have received at least one frame"


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
