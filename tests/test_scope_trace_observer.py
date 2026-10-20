"""Opt-in queue/send evidence must not change scope delivery."""

import asyncio

import pytest

from rigplane.scope import ScopeFrame
from rigplane.web.handlers.scope import ScopeHandler
from rigplane.web.protocol import encode_scope_frame


def frame(value: int = 0) -> ScopeFrame:
    return ScopeFrame(0, 0, 14_000_000, 14_100_000, bytes([value]), False)


class Connection:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def send_binary(self, data: bytes) -> None:
        self.started.set()
        await self.release.wait()
        self.sent.append(data)


@pytest.mark.asyncio
async def test_blocked_send_distinguished_from_queue_loss_and_wrap() -> None:
    ws = Connection()
    events = []
    handler = ScopeHandler(ws, trace_observer=events.append)
    handler._running = True
    handler._seq = 65535
    handler.enqueue_frame(frame())
    sender = asyncio.create_task(handler._sender())
    try:
        await asyncio.wait_for(ws.started.wait(), 1)
        for value in range(1, 13):
            handler.enqueue_frame(frame(value))
        assert [e["wire_sequence"] for e in events if e["stage"] == "drop_oldest"] == [
            0,
            1,
        ]
        assert [e["wire_sequence"] for e in events if e["stage"] == "send_start"] == [
            65535
        ]
        assert not any(e["stage"] == "send_complete" for e in events)
        assert handler._frame_queue.qsize() == 10
        ws.release.set()
        async with asyncio.timeout(1):
            while len(ws.sent) < 11:
                await asyncio.sleep(0)
        assert ws.sent == [encode_scope_frame(frame(), 65535)] + [
            encode_scope_frame(frame(value), value - 1) for value in range(3, 13)
        ]
        assert [
            e["wire_sequence"] for e in events if e["stage"] == "send_complete"
        ] == [65535, *range(2, 12)]
        assert all(
            e["channel"] == "scope" and 0 <= e["queue_depth"] <= 10 for e in events
        )
        assert all(
            a["monotonic_ns"] <= b["monotonic_ns"] for a, b in zip(events, events[1:])
        )
        assert all(
            set(e)
            == {"stage", "channel", "wire_sequence", "queue_depth", "monotonic_ns"}
            for e in events
        )
    finally:
        sender.cancel()
        with pytest.raises(asyncio.CancelledError):
            await sender


@pytest.mark.asyncio
async def test_observer_failure_disables_only_diagnostics() -> None:
    calls = []

    def broken(event):
        calls.append(event)
        raise RuntimeError("observer failed")

    ws = Connection()
    ws.release.set()
    handler = ScopeHandler(ws, trace_observer=broken)
    handler._running = True
    handler.enqueue_frame(frame(1))
    handler.enqueue_frame(frame(2))
    sender = asyncio.create_task(handler._sender())
    try:
        async with asyncio.timeout(1):
            while len(ws.sent) < 2:
                await asyncio.sleep(0)
        assert len(calls) == 1
        assert ws.sent == [
            encode_scope_frame(frame(1), 0),
            encode_scope_frame(frame(2), 1),
        ]
    finally:
        sender.cancel()
        with pytest.raises(asyncio.CancelledError):
            await sender


@pytest.mark.asyncio
async def test_cancelled_send_is_not_success_or_error() -> None:
    ws = Connection()
    events = []
    handler = ScopeHandler(ws, audio_mode=True, trace_observer=events.append)
    handler._running = True
    handler.enqueue_frame(frame())
    sender = asyncio.create_task(handler._sender())
    await asyncio.wait_for(ws.started.wait(), 1)
    sender.cancel()
    with pytest.raises(asyncio.CancelledError):
        await sender
    assert [e["stage"] for e in events] == ["enqueue", "send_start", "send_cancelled"]
    assert all(e["channel"] == "audio-scope" for e in events)
    assert ws.sent == []


@pytest.mark.asyncio
async def test_send_error_remains_terminal_and_has_no_success() -> None:
    class BrokenConnection:
        async def send_binary(self, data):
            raise OSError("closed")

    events = []
    handler = ScopeHandler(BrokenConnection(), trace_observer=events.append)
    handler._running = True
    handler.enqueue_frame(frame())
    await asyncio.wait_for(handler._sender(), 1)
    assert [e["stage"] for e in events] == ["enqueue", "send_start", "send_error"]


def test_default_off_does_not_read_diagnostic_clock(monkeypatch) -> None:
    def forbidden():
        raise AssertionError("diagnostic clock read while disabled")

    monkeypatch.setattr("rigplane.web.handlers.scope.time.monotonic_ns", forbidden)
    handler = ScopeHandler(None)
    handler._running = True
    for value in range(12):
        handler.enqueue_frame(frame(value))
    assert [handler._frame_queue.get_nowait() for _ in range(10)] == [
        encode_scope_frame(frame(value), value) for value in range(2, 12)
    ]
