"""MOR-4776: bounded admission through the real CAT carrier and scheduler."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, cast
from unittest.mock import Mock

import pytest

from rigplane.rigctld import protocol
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.handler import RigctldHandler
from rigplane.rigctld.server import RigctldServer, _MAX_PENDING_CLIENT_RESPONSES
from rigplane.testing.timing import VirtualClock
from rigplane.web.channel_attach import ChannelAttachedAck, RIGCTL_MAX_FRAME_BYTES
from rigplane.web.handlers.rigctl import (
    RigctlChannelHandler,
    _LineReader,
    _MAX_QUEUED_LINES,
)
from rigplane.web.transport.webrtc import (
    MAX_FRAMES_BEFORE_SERVE,
    WebRtcDataChannelConnection,
)
from test_webrtc_split_channel import _FakeChannel, _FakePc

pytestmark = pytest.mark.timeout(30)


@pytest.mark.parametrize("prefill", [0, _MAX_QUEUED_LINES])
async def test_full_datachannel_line_queue_waits_and_preserves_order(
    virtual_clock: VirtualClock, prefill: int
) -> None:
    reader = _LineReader(datachannel=True)
    lines = [f"line-{i}\n" for i in range(2 * _MAX_QUEUED_LINES)]
    await reader.feed("".join(lines[:prefill]))
    feed = asyncio.create_task(reader.feed("".join(lines[prefill:])))
    try:
        await virtual_clock.elapse(0)
        assert not feed.done(), "a full queue must wait instead of rejecting the frame"
        assert len(reader._lines) == _MAX_QUEUED_LINES
        received = []
        for _ in lines:
            received.append(await reader.readline())
            await virtual_clock.elapse(0)
            assert len(reader._lines) <= _MAX_QUEUED_LINES
        await feed
        assert received == [line.encode() for line in lines]
        reader.feed_eof()
        assert await reader.readline() == b""
    finally:
        reader.feed_eof()
        feed.cancel()
        await asyncio.gather(feed, return_exceptions=True)


@pytest.mark.parametrize("end", ["eof", "cancel"])
async def test_full_datachannel_line_queue_exits_on_eof_or_cancel(
    virtual_clock: VirtualClock, end: str
) -> None:
    reader = _LineReader(datachannel=True)
    await reader.feed("f\n" * _MAX_QUEUED_LINES)
    feed = asyncio.create_task(reader.feed("m\n"))
    try:
        await virtual_clock.elapse(0)
        assert not feed.done()
        if end == "eof":
            reader.feed_eof()
            await virtual_clock.elapse(0)
            assert feed.done()
            await feed
            assert not reader._lines
            assert await reader.readline() == b""
        else:
            feed.cancel()
            with pytest.raises(asyncio.CancelledError):
                await feed
            assert list(reader._lines) == [b"f\n"] * _MAX_QUEUED_LINES
            reader.feed_eof()
            assert await reader.readline() == b""
    finally:
        reader.feed_eof()
        feed.cancel()
        await asyncio.gather(feed, return_exceptions=True)


class _Radio:
    connected = True
    radio_ready = True
    model = "CarrierFixture"

    async def get_freq(self) -> int:
        return 14_074_000


@dataclass
class _Carrier:
    server: RigctldServer
    radio: _Radio
    channel: _FakeChannel
    connection: WebRtcDataChannelConnection
    run: asyncio.Task[None]
    reader: _LineReader
    read_allowed: asyncio.Event
    pending: set[asyncio.Task[Any]]
    retired: int = 0
    peak_pending: int = 0
    answers: list[Any] = field(default_factory=list)


@pytest.fixture
async def carrier(
    monkeypatch: pytest.MonkeyPatch, virtual_clock: VirtualClock
) -> AsyncIterator[_Carrier]:
    radio = _Radio()
    config = RigctldConfig(command_rate_limit=23)
    server = RigctldServer(
        cast(Any, radio),
        config,
        _protocol=protocol,
        _handler=RigctldHandler(cast(Any, radio), config),
        clock=virtual_clock,
    )
    monkeypatch.setattr(server, "_server", Mock(spec=asyncio.Server))
    read_allowed = asyncio.Event()
    read_allowed.set()
    read = server._readline

    async def gated_read(reader: Any) -> bytes | None:
        await read_allowed.wait()
        return await read(reader)

    monkeypatch.setattr(server, "_readline", gated_read)
    opened: list[_LineReader] = []

    def open_channel(reader: Any, writer: Any, owner: str) -> asyncio.Task[None] | None:
        opened.append(reader)
        return server.open_channel(reader, writer, owner)

    channel = _FakeChannel("rigctl")
    channel.bufferedAmount = 0  # type: ignore[attr-defined]
    connection = WebRtcDataChannelConnection(cast(Any, channel), cast(Any, _FakePc()))
    connection.serving = True
    run = asyncio.create_task(
        RigctlChannelHandler(
            connection,
            open_channel,
            "carrier-owner",
            ("203.0.113.5", 40000),
            datachannel=True,
        ).run()
    )
    pending: set[asyncio.Task[Any]] = set()
    execute = server._execute_and_retire_client_command

    async def observed(**kwargs: Any) -> None:
        task = asyncio.current_task()
        assert task is not None
        pending.add(task)
        path.peak_pending = max(path.peak_pending, len(pending))
        path.answers.append((kwargs["cmd"], kwargs["immediate_error"]))
        try:
            await execute(**kwargs)
        finally:
            pending.remove(task)
            path.retired += 1

    monkeypatch.setattr(server, "_execute_and_retire_client_command", observed)
    try:
        await virtual_clock.elapse(0)
        assert channel.sent == [ChannelAttachedAck("rigctl").encode()]
        path = _Carrier(
            server, radio, channel, connection, run, opened[0], read_allowed, pending
        )
        yield path
    finally:
        channel.close()
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)
        tasks = tuple(server._client_tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def test_thousands_of_mixed_lines_use_real_parser_scheduler_and_carrier(
    carrier: _Carrier, virtual_clock: VirtualClock
) -> None:
    path = carrier
    dump_lines = (
        (Path(__file__).parent / "golden/dump_state_ic7610.txt")
        .read_text()
        .splitlines()
    )
    dump_lines[0] = "1"
    dump = "\n".join([*dump_lines, "targetable_vfo=0x3", "done"]) + "\n"
    group = "f\n\n \nunknown-command\nf m\n\\dump_state\nf\n\n"
    ordinary = "14074000\nRPRT -4\nRPRT -4\nRPRT -1\n" + dump + "14074000\n"
    expected = ""
    for _ in range(48):
        path.channel.deliver(group * 8)  # 64 physical lines, 24 valid commands.
        await virtual_clock.elapse(0)
        expected += ordinary * 7 + ordinary.removesuffix("14074000\n") + "RPRT -6\n"
        assert "".join(cast(list[str], path.channel.sent[1:])) == expected
        assert path.retired == len(path.answers)
        assert not path.pending
        assert not path.reader._lines
        assert path.connection._queue.empty()
        assert path.connection.is_alive()
        assert not path.run.done()
        assert path.peak_pending <= _MAX_PENDING_CLIENT_RESPONSES
        await virtual_clock.elapse(1)
    assert len(path.answers) == 48 * 8 * 6
    assert sum(error == -6 for _, error in path.answers) == 48

    # get_info's real producer interpolates Radio.model (a string).
    path.radio.model = "Fixture" * (RIGCTL_MAX_FRAME_BYTES // 7 + 1)
    before = len(path.channel.sent)
    path.channel.deliver("\\get_info\nf\n")
    await virtual_clock.elapse(0)
    frames = cast(list[str], path.channel.sent[before:])
    assert "".join(frames) == f"Icom {path.radio.model} (rigplane)\n14074000\n"
    assert len(frames) == 3  # Two logical replies; the first spans two frames.
    assert all(len(frame.encode()) <= RIGCTL_MAX_FRAME_BYTES for frame in frames)
    assert not path.pending
    assert path.retired == len(path.answers)

    path.channel.deliver("q\n")
    await virtual_clock.elapse(0)
    assert path.run.done()
    await path.run
    assert path.channel.sent[before:] == frames  # Quit has no reply.
    assert not path.server._client_tasks
    assert not path.server._channel_owners

    replacement = _FakeChannel("rigctl")
    replacement.bufferedAmount = 0  # type: ignore[attr-defined]
    conn = WebRtcDataChannelConnection(cast(Any, replacement), cast(Any, _FakePc()))
    run = asyncio.create_task(
        RigctlChannelHandler(
            conn,
            path.server.open_channel,
            "carrier-owner",
            ("203.0.113.5", 40001),
            datachannel=True,
        ).run()
    )
    try:
        await virtual_clock.elapse(0)
        replacement.deliver("f\nq\n")
        await virtual_clock.elapse(0)
        assert run.done()
        await run
        assert replacement.sent == [ChannelAttachedAck("rigctl").encode(), "14074000\n"]
        assert not path.pending
    finally:
        run.cancel()
        await asyncio.gather(run, return_exceptions=True)


async def test_real_carrier_waits_on_two_legal_frames_then_resumes(
    carrier: _Carrier, virtual_clock: VirtualClock
) -> None:
    path = carrier
    path.read_allowed.clear()
    # Let the read already waiting inside readline finish, then gate the next.
    path.channel.deliver("f\n")
    await virtual_clock.elapse(0)
    path.channel.deliver("\n" * _MAX_QUEUED_LINES)
    path.channel.deliver("\n" * (_MAX_QUEUED_LINES - 1) + "f\n")
    await virtual_clock.elapse(0)
    assert len(path.reader._lines) == _MAX_QUEUED_LINES
    assert path.connection.is_alive()
    assert path.channel.sent[1:] == ["14074000\n"]
    path.read_allowed.set()
    await virtual_clock.elapse(0)
    assert path.channel.sent[1:] == ["14074000\n", "14074000\n"]
    assert not path.reader._lines
    assert not path.pending
    assert path.connection.is_alive()


async def test_real_scheduler_capacity_still_closes_carrier(
    carrier: _Carrier, virtual_clock: VirtualClock, caplog: pytest.LogCaptureFixture
) -> None:
    path = carrier
    path.channel.bufferedAmount = RIGCTL_MAX_FRAME_BYTES + 1  # type: ignore[attr-defined]
    path.channel.deliver("f\n" * _MAX_PENDING_CLIENT_RESPONSES)
    await virtual_clock.elapse(0)
    assert len(path.pending) == _MAX_PENDING_CLIENT_RESPONSES
    assert path.connection.is_alive()
    path.channel.deliver("f\n")
    await virtual_clock.elapse(0.25)
    assert "response capacity reached" in caplog.text
    assert path.run.done()
    await path.run
    assert not path.pending
    assert not path.reader._lines
    assert not path.connection.is_alive()


async def test_serving_transport_still_aborts_at_64_unread_frames(
    carrier: _Carrier, virtual_clock: VirtualClock
) -> None:
    path = carrier
    path.read_allowed.clear()
    path.channel.deliver("f\n")
    await virtual_clock.elapse(0)
    path.channel.deliver("\n" * (2 * _MAX_QUEUED_LINES))
    await virtual_clock.elapse(0)
    assert len(path.reader._lines) == _MAX_QUEUED_LINES
    for _ in range(MAX_FRAMES_BEFORE_SERVE):
        path.channel.deliver("f\n")
    assert path.connection._queue.qsize() == MAX_FRAMES_BEFORE_SERVE
    assert path.connection.is_alive()
    path.channel.deliver("f\n")
    assert not path.connection.is_alive()
    with pytest.raises(EOFError, match="data channel closed"):
        await path.connection.recv()
    # Cancel the carrier: its feed is waiting rather than receiving again.
    path.run.cancel()
    await virtual_clock.elapse(0)
    await asyncio.gather(path.run, return_exceptions=True)
    assert not path.pending
    assert not path.reader._lines
    assert not path.server._client_tasks
