"""MOR-4317: a completed serial read must not consume shutdown cancellation."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from rigplane.backends.icom7610.drivers.serial_session import SerialCivTransport
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.runtime.radio import IcomRadio
from rigplane.testing.timing import await_until
from rigplane.types import bcd_encode
from test_serial_civ_link import _FakeReader, _make_link


class _ReplyDuringCancellation(_FakeReader):
    def __init__(self, cancel: Callable[[], None], reply: bytes) -> None:
        super().__init__()
        self._cancel = cancel
        self._reply = reply
        self.replied = False

    async def read(self, n: int) -> bytes:
        if not self.replied:
            self.replied = True
            self._cancel()
            return self._reply
        return await super().read(n)


def _frequency_reply(radio: IcomRadio) -> bytes:
    return build_civ_frame(
        CONTROLLER_ADDR,
        radio._radio_addr,
        0x03,
        data=bcd_encode(14_074_000),
    )


async def test_serial_receive_preserves_cancel_when_read_completes() -> None:
    radio = IcomRadio("127.0.0.1", model="IC-7610")
    receiver: asyncio.Task[bytes | None] | None = None

    def cancel_receiver() -> None:
        assert receiver is not None
        receiver.cancel()

    reader = _ReplyDuringCancellation(cancel_receiver, _frequency_reply(radio))
    link, _, _ = await _make_link(reader=reader)
    try:
        receiver = asyncio.create_task(link.receive(timeout=5.0))
        await await_until(
            receiver.done,
            timeout=5.0,
            description="the cancelled serial receive to finish",
        )
        assert reader.replied
        with pytest.raises(asyncio.CancelledError):
            await receiver
        assert link._read_task is None
    finally:
        pending = [task for task in (receiver, link._read_task) if task is not None]
        for task in pending:
            if not task.done():
                task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await link.disconnect()


async def test_rx_pump_stops_when_serial_read_completes_during_cancel() -> None:
    radio = IcomRadio("127.0.0.1", model="IC-7610")
    stoppers: list[asyncio.Task[None]] = []

    def stop_pump() -> None:
        # This task runs before wait_for's completed-read wakeup on 3.11.
        stoppers.append(asyncio.create_task(radio._civ_runtime.stop_pump()))

    reader = _ReplyDuringCancellation(stop_pump, _frequency_reply(radio))
    link, _, _ = await _make_link(reader=reader)
    transport = SerialCivTransport(link)
    radio._civ_transport = transport
    radio._civ_runtime.start_pump()
    pump = radio._civ_rx_task
    assert pump is not None
    try:
        await await_until(
            lambda: bool(stoppers) and stoppers[0].done(),
            timeout=5.0,
            description="RX-pump shutdown after a completed serial read",
        )
        await stoppers[0]
        assert reader.replied
        assert pump.done()
        assert radio._civ_rx_task is None
        assert radio._civ_transport is transport
    finally:
        pending = [pump, *stoppers]
        if link._read_task is not None:
            pending.append(link._read_task)
        for task in pending:
            if not task.done():
                task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await link.disconnect()
        await radio._civ_runtime.stop_pump()
