from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable

import pytest

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.commands.commander import IcomCommander, Priority
from rigplane.core.exceptions import ConnectionError
from rigplane.core.types import CivFrame
from rigplane.runtime._civ_rx import CIV_HEADER_SIZE
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime.radio import IcomRadio


_READ_FREQUENCY = build_civ_frame(0x98, CONTROLLER_ADDR, 0x03)
_READ_MODE = build_civ_frame(0x98, CONTROLLER_ADDR, 0x04)


class _WireTransport:
    def __init__(self) -> None:
        self.my_id = 1
        self.remote_id = 2
        self.sent_packets: list[bytes] = []
        self.hold_frame: bytes | None = None
        self.send_entered = asyncio.Event()
        self.send_release = asyncio.Event()
        self.send_cancelled = asyncio.Event()
        self.disconnected = False
        self.on_disconnect: Callable[[], None] | None = None
        self._received: asyncio.Queue[bytes] = asyncio.Queue()

    @property
    def civ_frames(self) -> list[bytes]:
        return [
            packet[CIV_HEADER_SIZE:]
            for packet in self.sent_packets
            if packet[CIV_HEADER_SIZE:].startswith(b"\xfe\xfe")
        ]

    async def send_tracked(self, packet: bytes) -> None:
        if self.hold_frame is not None and packet[CIV_HEADER_SIZE:] == self.hold_frame:
            self.send_entered.set()
            try:
                await self.send_release.wait()
            except asyncio.CancelledError:
                self.send_cancelled.set()
                raise
        self.sent_packets.append(packet)

    async def receive_packet(self, timeout: float = 5.0) -> bytes:
        return await asyncio.wait_for(self._received.get(), timeout)

    async def disconnect(self) -> None:
        if self.on_disconnect is not None:
            self.on_disconnect()
        self.disconnected = True


@pytest.fixture
async def rig() -> AsyncIterator[tuple[IcomRadio, _WireTransport]]:
    radio = IcomRadio("test.invalid", radio_addr=0x98)
    transport = _WireTransport()
    radio._civ_transport = transport
    radio._ctrl_transport = _WireTransport()
    radio._connected = True
    radio._civ_min_interval = 0.0
    try:
        yield radio, transport
    finally:
        transport.send_release.set()
        await radio._civ_runtime.stop_worker()
        await radio._civ_runtime.stop_pump()
        radio._connected = False
        radio._civ_transport = None


async def _cancel_and_join(tasks: list[asyncio.Task]) -> None:
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("wait_response", [False, True], ids=["dispatch", "response"])
async def test_admitted_request_loses_transport_before_execution(
    rig: tuple[IcomRadio, _WireTransport], wait_response: bool
) -> None:
    radio, transport = rig
    runtime = radio._civ_runtime
    admitted, release = asyncio.Event(), asyncio.Event()
    first = True

    async def execute(payload: bytes, wait: bool) -> CivFrame | None:
        nonlocal first
        if first:
            first = False
            admitted.set()
            await release.wait()
        return await runtime.execute_civ_raw(payload, wait)

    commander = IcomCommander(execute)
    radio._commander = commander
    commander.start()
    queue = commander._queue
    assert queue is not None
    caller = asyncio.create_task(
        runtime.send_civ_raw(
            _READ_FREQUENCY,
            priority=Priority.BACKGROUND,
            key="admitted-read",
            wait_response=wait_response,
        )
    )
    try:
        await admitted.wait()
        future = commander._pending_by_key["admitted-read"]
        assert not future.done() and not caller.done()
        assert radio._civ_transport is transport
        assert transport.sent_packets == []

        # Deliberately omit worker stop to isolate execution-entry availability.
        await transport.disconnect()
        radio._civ_transport = None
        release.set()

        with pytest.raises(ConnectionError, match="^Not connected to radio$"):
            await caller
        await queue.join()
        assert future.done() and isinstance(future.exception(), ConnectionError)
        assert transport.sent_packets == []
        assert commander._pending_by_key == {}
        assert radio._civ_request_tracker.pending_count == 0
        assert commander._worker is not None and not commander._worker.done()

        replacement = _WireTransport()
        radio._civ_transport = replacement
        assert await runtime.send_civ_raw(_READ_MODE, wait_response=False) is None
        await queue.join()
        assert replacement.civ_frames == [_READ_MODE]
        assert transport.sent_packets == []
    finally:
        release.set()
        await _cancel_and_join([caller])


@pytest.mark.asyncio
async def test_disconnect_stops_inflight_and_queued_reads_before_transport_retirement(
    rig: tuple[IcomRadio, _WireTransport],
) -> None:
    radio, transport = rig
    runtime = radio._civ_runtime
    commander = IcomCommander(runtime.execute_civ_raw)
    radio._commander = commander
    commander.start()
    worker, queue = commander._worker, commander._queue
    assert worker is not None and queue is not None
    transport.hold_frame = _READ_FREQUENCY
    generation = radio._civ_epoch
    retired = []

    def observe_retirement() -> None:
        assert worker.done()
        assert commander._worker is None and commander._queue is None
        assert radio._civ_rx_task is None
        assert radio._civ_epoch == generation + 1
        retired.append(transport)

    transport.on_disconnect = observe_retirement
    caller = asyncio.create_task(
        runtime.send_civ_raw(_READ_FREQUENCY, wait_response=True)
    )
    try:
        await transport.send_entered.wait()
        assert radio._civ_request_tracker.pending_count > 0
        assert (
            await runtime.send_civ_raw(
                _READ_MODE,
                priority=Priority.BACKGROUND,
                key="queued-read",
                wait_response=False,
                wait_dispatch=False,
            )
            is None
        )
        queued = commander._pending_by_key["queued-read"]
        assert queue.qsize() == 1 and not queued.done()

        await radio._control_phase.disconnect()

        with pytest.raises(ConnectionError, match="Commander stopped"):
            await caller
        await queue.join()
        assert isinstance(queued.exception(), ConnectionError)
        assert transport.send_cancelled.is_set()
        assert retired == [transport] and transport.disconnected
        assert radio._civ_transport is None
        assert radio._conn_state is RadioConnectionState.DISCONNECTED
        assert radio._civ_request_tracker.pending_count == 0
        assert commander._pending_by_key == {}
        assert commander._bg_inflight == 0
        assert transport.civ_frames == []
    finally:
        await _cancel_and_join([caller])


@pytest.mark.asyncio
async def test_caller_cancellation_releases_real_execution_and_preserves_next_read(
    rig: tuple[IcomRadio, _WireTransport],
) -> None:
    radio, transport = rig
    runtime = radio._civ_runtime
    commander = IcomCommander(runtime.execute_civ_raw)
    radio._commander = commander
    commander.start()
    queue = commander._queue
    assert queue is not None
    transport.hold_frame = _READ_FREQUENCY
    caller = asyncio.create_task(
        runtime.send_civ_raw(_READ_FREQUENCY, wait_response=True)
    )
    try:
        await transport.send_entered.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        await transport.send_cancelled.wait()
        await queue.join()
        assert radio._civ_request_tracker.pending_count == 0
        assert transport.civ_frames == []
        assert commander._worker is not None and not commander._worker.done()

        transport.hold_frame = None
        assert await runtime.send_civ_raw(_READ_MODE, wait_response=False) is None
        await queue.join()
        assert transport.civ_frames == [_READ_MODE]
    finally:
        await _cancel_and_join([caller])
