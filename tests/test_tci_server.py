"""Leaf server tests with fake transports (MOR-3821 L2).

No sockets: sessions run over ``asyncio.StreamReader`` fed by hand and
a fake writer, so this file runs anywhere. Real-socket and eesdr-tci
conformance live in ``test_tci_server_sockets.py`` and
``test_tci_eesdr_conformance.py`` (mini-only).
"""

import asyncio
import struct
import time
from collections.abc import Awaitable, Callable, Sequence

import pytest

from rigplane.tci.handler import CommandOutcome
from rigplane.tci.server import (
    COALESCE_WINDOW_S,
    DEADLINE_S,
    MAX_QUEUED_OUTBOUND,
    PING_INTERVAL_S,
    TciServer,
)
from rigplane.ws import (
    WS_OP_BINARY,
    WS_OP_CLOSE,
    WS_OP_PING,
    WS_OP_PONG,
    WS_OP_TEXT,
    AllowHeaders,
)

BURST = ["protocol:tciserver,1;", "device:rigplane;", "receive_only:false;", "ready;"]


class FakeClock:
    """Injectable monotonic clock."""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeTransport:
    def __init__(self) -> None:
        self.aborted = False

    def abort(self) -> None:
        self.aborted = True


class FakeWriter:
    """Memory-backed writer; drain can be scripted to block forever."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.drain_calls = 0
        self.close_calls = 0
        self._closing = False
        self.transport = FakeTransport()
        self._drain: Callable[[], Awaitable[None]] | None = None

    def block_drain(self) -> None:
        async def _never() -> None:
            await asyncio.Event().wait()

        self._drain = _never

    def write(self, data: bytes) -> None:
        self.buffer += data

    async def drain(self) -> None:
        self.drain_calls += 1
        if self._drain is not None:
            await self._drain()

    def close(self) -> None:
        self.close_calls += 1
        self._closing = True

    def is_closing(self) -> bool:
        return self._closing


def client_frame(opcode: int, payload: bytes) -> bytes:
    """One masked client→server frame (masking done by hand here)."""
    mask = b"\x11\x22\x33\x44"
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    length = len(payload)
    if length <= 125:
        header = struct.pack("!BB", 0x80 | opcode, 0x80 | length)
    elif length <= 65535:
        header = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, length)
    else:
        header = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, length)
    return header + mask + masked


def parse_server_frames(buffer: bytes) -> list[tuple[int, bytes]]:
    """Split server→client frames (unmasked) into (opcode, payload)."""
    frames: list[tuple[int, bytes]] = []
    view = bytes(buffer)
    while view:
        first, second = view[0], view[1]
        opcode = first & 0x0F
        length = second & 0x7F
        assert not (second & 0x80), "server frames are never masked"
        if length == 126:
            (length,) = struct.unpack("!H", view[2:4])
            head = 4
        elif length == 127:
            (length,) = struct.unpack("!Q", view[2:10])
            head = 10
        else:
            head = 2
        frames.append((opcode, view[head : head + length]))
        view = view[head + length :]
    return frames


async def wait_for_frames(
    writer: FakeWriter, count: int, timeout: float = 2.0
) -> list[tuple[int, bytes]]:
    """Wait for at least *count* frames (deadline, never a bare sleep)."""
    async with asyncio.timeout(timeout):
        while len(parse_server_frames(writer.buffer)) < count:
            await asyncio.sleep(0.005)
    return parse_server_frames(writer.buffer)


class FakeHandler:
    """Scriptable TciHandler; records everything."""

    def __init__(
        self,
        burst: Sequence[str] = BURST,
        replies: dict[str, CommandOutcome] | None = None,
        default_outcome: CommandOutcome | None = None,
    ) -> None:
        self._burst = list(burst)
        self._replies = replies or {}
        self._default = default_outcome or CommandOutcome()
        self.connect_calls = 0
        self.commands: list[tuple[str, tuple[str, ...]]] = []
        self.binaries: list[bytes] = []
        self.disconnect_calls = 0
        self.fail_commands: set[str] = set()
        self.fail_connect: Exception | None = None
        self.disconnect_gate: asyncio.Event | None = None

    async def on_connect(self) -> Sequence[str]:
        self.connect_calls += 1
        if self.fail_connect is not None:
            raise self.fail_connect
        return list(self._burst)

    async def on_command(self, name: str, args: tuple[str, ...]) -> CommandOutcome:
        self.commands.append((name, args))
        if name in self.fail_commands:
            raise RuntimeError(f"boom on {name}")
        return self._replies.get(name, self._default)

    async def on_binary(self, payload: bytes) -> None:
        self.binaries.append(bytes(payload))

    async def on_disconnect(self) -> None:
        self.disconnect_calls += 1
        if self.disconnect_gate is not None:
            await self.disconnect_gate.wait()


def make_server(
    handler: FakeHandler,
    clock: FakeClock,
    allow: AllowHeaders | None = None,
    **kwargs: object,
) -> TciServer:
    return TciServer(
        lambda: handler,  # type: ignore[return-value]
        host="127.0.0.1",
        port=0,
        allow=allow if allow is not None else (lambda headers: None),
        clock=clock,
        **kwargs,  # type: ignore[arg-type]
    )


async def open_session(
    server: TciServer, handler: FakeHandler, writer: FakeWriter | None = None
) -> tuple[object, FakeWriter, asyncio.StreamReader]:
    reader = asyncio.StreamReader()
    session_writer = writer if writer is not None else FakeWriter()
    session = await server._open_session(reader, session_writer, handler)
    assert session is not None
    return session, session_writer, reader


async def test_init_burst_sent_in_order() -> None:
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    session, writer, _reader = await open_session(server, handler)
    try:
        frames = await wait_for_frames(writer, len(BURST))
        assert [payload for _, payload in frames] == [line.encode() for line in BURST]
        assert all(opcode == WS_OP_TEXT for opcode, _ in frames)
    finally:
        await session.finish()
    assert handler.disconnect_calls == 1


async def test_command_routing_sender_and_others() -> None:
    clock = FakeClock()
    outcome = CommandOutcome(
        to_sender=["vfo:0,0,14074000;"], to_others=["vfo:0,0,14074000;"]
    )
    handler = FakeHandler(replies={"vfo": outcome})
    server = make_server(handler, clock)
    session_a, writer_a, reader_a = await open_session(server, handler)
    session_b, writer_b, _reader_b = await open_session(server, handler)
    try:
        await wait_for_frames(writer_a, len(BURST))
        await wait_for_frames(writer_b, len(BURST))
        reader_a.feed_data(client_frame(WS_OP_TEXT, b"vfo:0,0,14074000;"))
        frames_a = await wait_for_frames(writer_a, len(BURST) + 1)
        assert frames_a[-1] == (WS_OP_TEXT, b"vfo:0,0,14074000;")
        frames_b = await wait_for_frames(writer_b, len(BURST) + 1)
        assert frames_b[-1] == (WS_OP_TEXT, b"vfo:0,0,14074000;")
        # The sender got exactly one reply; the sync went only to others.
        assert len(frames_a) == len(BURST) + 1
        assert handler.commands == [("vfo", ("0", "0", "14074000"))]
    finally:
        await session_a.finish()
        await session_b.finish()


async def test_refusal_reaches_sender_only() -> None:
    clock = FakeClock()
    handler = FakeHandler(replies={"trx": CommandOutcome(to_sender=["trx:0,false;"])})
    server = make_server(handler, clock)
    session_a, writer_a, reader_a = await open_session(server, handler)
    session_b, writer_b, _reader_b = await open_session(server, handler)
    try:
        await wait_for_frames(writer_a, len(BURST))
        await wait_for_frames(writer_b, len(BURST))
        before_b = len(parse_server_frames(writer_b.buffer))
        reader_a.feed_data(client_frame(WS_OP_TEXT, b"trx:0,true;"))
        frames_a = await wait_for_frames(writer_a, len(BURST) + 1)
        assert frames_a[-1] == (WS_OP_TEXT, b"trx:0,false;")
        await asyncio.sleep(0.05)
        assert len(parse_server_frames(writer_b.buffer)) == before_b
    finally:
        await session_a.finish()
        await session_b.finish()


async def test_binary_both_ways() -> None:
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    session_a, _writer_a, reader_a = await open_session(server, handler)
    session_b, writer_b, _reader_b = await open_session(server, handler)
    try:
        payload = b"\x00\x01\x02\x03" * 64
        reader_a.feed_data(client_frame(WS_OP_BINARY, payload))
        async with asyncio.timeout(2.0):
            while not handler.binaries:
                await asyncio.sleep(0.005)
        assert handler.binaries == [payload]
        server.broadcast_binary(b"\x09" * 16)
        frames_b = await wait_for_frames(writer_b, len(BURST) + 1)
        assert frames_b[-1] == (WS_OP_BINARY, b"\x09" * 16)
    finally:
        await session_a.finish()
        await session_b.finish()


async def test_close_frame_disconnects_once() -> None:
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    session, writer, reader = await open_session(server, handler)
    await wait_for_frames(writer, len(BURST))
    reader.feed_data(client_frame(WS_OP_CLOSE, struct.pack("!H", 1000)))
    async with asyncio.timeout(2.0):
        while handler.disconnect_calls == 0:
            await asyncio.sleep(0.005)
    await session.finish()
    assert handler.disconnect_calls == 1


async def test_close_frame_releases_transport() -> None:
    """The close-frame path must still close the transport (regression pin).

    ``recv()`` marks the connection closed when a close frame arrives;
    ``finish()`` used to skip ``close()`` then, leaking the transport, so
    the listener's ``wait_closed()`` never returned.
    """
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    session, writer, reader = await open_session(server, handler)
    await wait_for_frames(writer, len(BURST))
    reader.feed_data(client_frame(WS_OP_CLOSE, struct.pack("!H", 1000)))
    async with asyncio.timeout(2.0):
        while handler.disconnect_calls == 0:
            await asyncio.sleep(0.005)
    await session.finish()
    assert writer.is_closing()


async def test_protocol_violation_ends_session_cleanly() -> None:
    """A compression bit without a negotiated deflate ends the session.

    The violation must run the normal teardown (disconnect once, transport
    closed), not kill the reader task with an unretrieved exception while
    the session leaks.
    """
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    session, writer, reader = await open_session(server, handler)
    await wait_for_frames(writer, len(BURST))
    frame = bytearray(client_frame(WS_OP_TEXT, b"vfo:0,0,14074000;"))
    frame[0] |= 0x40
    reader.feed_data(bytes(frame))
    async with asyncio.timeout(2.0):
        while handler.disconnect_calls == 0:
            await asyncio.sleep(0.005)
    await session.finish()
    assert handler.disconnect_calls == 1
    assert writer.is_closing()


async def test_reset_disconnects_once() -> None:
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    session, writer, reader = await open_session(server, handler)
    await wait_for_frames(writer, len(BURST))
    reader.feed_eof()
    async with asyncio.timeout(2.0):
        while handler.disconnect_calls == 0:
            await asyncio.sleep(0.005)
    await session.finish()
    assert handler.disconnect_calls == 1


async def test_invalid_utf8_ends_session_without_reply() -> None:
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    session, writer, reader = await open_session(server, handler)
    await wait_for_frames(writer, len(BURST))
    reader.feed_data(client_frame(WS_OP_TEXT, b"\xff\xfe not utf-8"))
    async with asyncio.timeout(2.0):
        while handler.disconnect_calls == 0:
            await asyncio.sleep(0.005)
    await session.finish()
    assert handler.disconnect_calls == 1
    assert handler.commands == []
    frames = parse_server_frames(writer.buffer)
    assert [opcode for opcode, _ in frames[: len(BURST)]] == [WS_OP_TEXT] * len(BURST)
    assert frames[len(BURST)][0] == WS_OP_CLOSE


async def test_failing_command_keeps_session_alive() -> None:
    clock = FakeClock()
    handler = FakeHandler(replies={"ok": CommandOutcome(to_sender=["ok:1;"])})
    handler.fail_commands.add("boom")
    server = make_server(handler, clock)
    session, writer, reader = await open_session(server, handler)
    try:
        await wait_for_frames(writer, len(BURST))
        reader.feed_data(client_frame(WS_OP_TEXT, b"boom:1;"))
        await asyncio.sleep(0.05)
        assert handler.commands == [("boom", ("1",))]
        reader.feed_data(client_frame(WS_OP_TEXT, b"ok;"))
        frames = await wait_for_frames(writer, len(BURST) + 1)
        assert frames[-1] == (WS_OP_TEXT, b"ok:1;")
    finally:
        await session.finish()
    assert handler.disconnect_calls == 1


async def test_multi_command_frame_in_order() -> None:
    clock = FakeClock()
    handler = FakeHandler(
        replies={
            "a": CommandOutcome(to_sender=["a:1;"]),
            "b": CommandOutcome(to_sender=["b:2;"]),
        }
    )
    server = make_server(handler, clock)
    session, writer, reader = await open_session(server, handler)
    try:
        await wait_for_frames(writer, len(BURST))
        reader.feed_data(client_frame(WS_OP_TEXT, b"a:1;b:2;"))
        frames = await wait_for_frames(writer, len(BURST) + 2)
        assert [payload for _, payload in frames[-2:]] == [b"a:1;", b"b:2;"]
        assert handler.commands == [("a", ("1",)), ("b", ("2",))]
    finally:
        await session.finish()


async def test_silence_for_empty_input() -> None:
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    session, writer, reader = await open_session(server, handler)
    try:
        await wait_for_frames(writer, len(BURST))
        reader.feed_data(client_frame(WS_OP_TEXT, b";;;"))
        await asyncio.sleep(0.05)
        assert handler.commands == []
        assert len(parse_server_frames(writer.buffer)) == len(BURST)
    finally:
        await session.finish()


async def test_watchdog_fresh_session_survives() -> None:
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    session, _writer, _reader = await open_session(server, handler)
    try:
        clock.advance(DEADLINE_S - 1.0)
        assert await session.check_deadline(clock()) is False
        assert handler.disconnect_calls == 0
    finally:
        await session.finish()


async def test_watchdog_stale_session_aborts_once() -> None:
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    session, writer, _reader = await open_session(server, handler)
    try:
        clock.advance(DEADLINE_S + 0.1)
        assert await session.check_deadline(clock()) is True
        assert writer.transport.aborted
        assert handler.disconnect_calls == 1
        assert await session.check_deadline(clock()) is False
        assert handler.disconnect_calls == 1
    finally:
        await session.finish()


async def test_inbound_ping_renews_liveness() -> None:
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    session, _writer, reader = await open_session(server, handler)
    try:
        clock.advance(DEADLINE_S - 1.0)
        reader.feed_data(client_frame(WS_OP_PING, b""))
        await asyncio.sleep(0.05)
        clock.advance(2.0)
        assert await session.check_deadline(clock()) is False
        assert handler.disconnect_calls == 0
    finally:
        await session.finish()


async def test_blocked_drain_still_releases() -> None:
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    writer = FakeWriter()
    writer.block_drain()
    session, _w, _reader = await open_session(server, handler, writer)
    try:
        clock.advance(DEADLINE_S + 0.1)
        assert await session.check_deadline(clock()) is True
        assert writer.transport.aborted
        assert handler.disconnect_calls == 1
    finally:
        await session.finish()


async def test_ping_task_sends_pings() -> None:
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock, ping_interval=0.02)
    session, writer, _reader = await open_session(server, handler)
    try:
        frames = await wait_for_frames(writer, len(BURST) + 1)
        pings = [f for f in frames if f[0] == WS_OP_PING]
        assert pings != []
    finally:
        await session.finish()


async def test_broadcast_coalesces_within_window() -> None:
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    session, writer, _reader = await open_session(server, handler)
    try:
        await wait_for_frames(writer, len(BURST))
        base = len(parse_server_frames(writer.buffer))
        server.broadcast(["vfo:0,0,14074000;", "modulation:0,USB;"])
        # Held, not flushed: pending grows synchronously, nothing on the wire.
        assert session._pending == ["vfo:0,0,14074000;", "modulation:0,USB;"]
        assert len(parse_server_frames(writer.buffer)) == base
        frames = await wait_for_frames(writer, base + 2)
        assert [payload for _, payload in frames[base:]] == [
            b"vfo:0,0,14074000;",
            b"modulation:0,USB;",
        ]
    finally:
        await session.finish()


async def test_disconnect_survives_handler_error() -> None:
    clock = FakeClock()
    handler = FakeHandler()

    async def _boom() -> None:
        handler.disconnect_calls += 1
        raise RuntimeError("release failed")

    handler.on_disconnect = _boom  # type: ignore[method-assign]
    server = make_server(handler, clock)
    session, _writer, _reader = await open_session(server, handler)
    await session.finish()
    assert handler.disconnect_calls == 1


async def test_disconnect_survives_one_cancellation() -> None:
    clock = FakeClock()
    handler = FakeHandler()
    gate = asyncio.Event()
    entered = asyncio.Event()
    handler.disconnect_gate = gate
    server = make_server(handler, clock)
    session, _writer, _reader = await open_session(server, handler)

    async def _guarded() -> None:
        entered.set()
        await session._teardown()

    task = asyncio.create_task(_guarded())
    await entered.wait()
    await asyncio.sleep(0)
    task.cancel()
    gate.set()
    await task
    assert handler.disconnect_calls == 1


async def test_disconnect_cancel_then_timeout_still_finalizes() -> None:
    """Cancel followed by a re-wait timeout must still finalize the callback.

    The disconnect task is cancelled after the handler entered, then the
    bounded re-wait times out: ownership stays until the callback task is
    cancelled and its finalization observed (exactly one invocation).
    """
    from rigplane.tci.server import TEARDOWN_TIMEOUT_S, _run_guarded_disconnect

    entered = asyncio.Event()
    gate = asyncio.Event()
    finalized = asyncio.Event()

    class _Handler:
        def __init__(self) -> None:
            self.calls = 0

        async def on_disconnect(self) -> None:
            self.calls += 1
            entered.set()
            try:
                await gate.wait()
            finally:
                finalized.set()

    handler = _Handler()
    task = asyncio.create_task(
        _run_guarded_disconnect(handler, "s1", set())  # type: ignore[arg-type]
    )
    try:
        async with asyncio.timeout(2.0):
            await entered.wait()
        await asyncio.sleep(0)
        task.cancel()
        async with asyncio.timeout(TEARDOWN_TIMEOUT_S * 2 + 5.0):
            await task
        assert finalized.is_set()
        assert handler.calls == 1
    finally:
        gate.set()
        if not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


async def test_connect_failure_closes_and_releases_once() -> None:
    clock = FakeClock()
    handler = FakeHandler()
    handler.fail_connect = RuntimeError("nope")
    server = make_server(handler, clock)
    reader = asyncio.StreamReader()
    writer = FakeWriter()
    assert await server._open_session(reader, writer, handler) is None
    assert writer.is_closing()
    assert handler.disconnect_calls == 1


async def test_accept_good_request_upgrades() -> None:
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    reader = asyncio.StreamReader()
    writer = FakeWriter()
    reader.feed_data(
        b"GET / HTTP/1.1\r\n"
        b"Host: 127.0.0.1:50001\r\n"
        b"Upgrade: websocket\r\n"
        b"Connection: Upgrade\r\n"
        b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        b"Sec-WebSocket-Version: 13\r\n"
        b"\r\n"
    )
    await server._accept_request(reader, writer)
    assert writer.buffer.startswith(b"HTTP/1.1 101 ")
    assert len(server.sessions) == 1
    head_end = bytes(writer.buffer).index(b"\r\n\r\n") + 4
    try:

        async def _tail_frames() -> list[tuple[int, bytes]]:
            return parse_server_frames(writer.buffer[head_end:])

        async with asyncio.timeout(2.0):
            while len(await _tail_frames()) < len(BURST):
                await asyncio.sleep(0.005)
        frames = await _tail_frames()
        assert [payload for _, payload in frames] == [line.encode() for line in BURST]
    finally:
        await server.stop()
    assert handler.disconnect_calls == 1


async def test_accept_refused_predicate() -> None:
    def _refuse(headers: object) -> object:
        from rigplane.ws import UpgradeDecline

        return UpgradeDecline(403, "Forbidden", b"nope")

    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock, allow=_refuse)  # type: ignore[arg-type]
    reader = asyncio.StreamReader()
    writer = FakeWriter()
    reader.feed_data(
        b"GET / HTTP/1.1\r\n"
        b"Host: 127.0.0.1:50001\r\n"
        b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
        b"Origin: http://evil.example\r\n"
        b"\r\n"
    )
    await server._accept_request(reader, writer)
    assert writer.buffer.startswith(b"HTTP/1.1 403 ")
    assert server.sessions == ()


@pytest.mark.parametrize(
    ("raw", "status"),
    [
        pytest.param(
            b"GET / HTTP/1.1\r\n"
            b"Host: 127.0.0.1:50001\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            b"Sec-WebSocket-Version: 13\r\n"
            b"\r\n",
            b"HTTP/1.1 400 ",
            id="missing-upgrade",
        ),
        pytest.param(
            b"GET / HTTP/1.1\r\n"
            b"Host: 127.0.0.1:50001\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: keep-alive\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            b"Sec-WebSocket-Version: 13\r\n"
            b"\r\n",
            b"HTTP/1.1 400 ",
            id="wrong-connection-token",
        ),
        pytest.param(
            b"GET / HTTP/1.1\r\n"
            b"Host: 127.0.0.1:50001\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            b"Sec-WebSocket-Version: 12\r\n"
            b"\r\n",
            b"HTTP/1.1 426 ",
            id="unsupported-version",
        ),
        pytest.param(
            b"GET / HTTP/1.1\r\n"
            b"Host: 127.0.0.1:50001\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            b"\r\n",
            b"HTTP/1.1 426 ",
            id="missing-version",
        ),
        pytest.param(
            b"GET / HTTP/1.1\r\n"
            b"Host: 127.0.0.1:50001\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Key: abc\r\n"
            b"Sec-WebSocket-Version: 13\r\n"
            b"\r\n",
            b"HTTP/1.1 400 ",
            id="invalid-key",
        ),
        pytest.param(
            b"GET / HTTP/1.0\r\n"
            b"Host: 127.0.0.1:50001\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            b"Sec-WebSocket-Version: 13\r\n"
            b"\r\n",
            b"HTTP/1.1 400 ",
            id="bad-http-version",
        ),
    ],
)
async def test_accept_malformed_handshake_refused(raw: bytes, status: bytes) -> None:
    """Malformed upgrades never reach the handler or the registry.

    Each bad handshake is refused with its status, and nothing is
    constructed: no handler factory call, no ``on_connect``, no session.
    """
    from rigplane.tci.server import TciServer

    clock = FakeClock()
    handler = FakeHandler()
    factory_calls = 0

    def _factory() -> FakeHandler:
        nonlocal factory_calls
        factory_calls += 1
        return handler

    server = TciServer(
        _factory,  # type: ignore[arg-type]
        host="127.0.0.1",
        port=0,
        allow=lambda headers: None,
        clock=clock,
    )
    reader = asyncio.StreamReader()
    writer = FakeWriter()
    reader.feed_data(raw)
    await server._accept_request(reader, writer)
    assert bytes(writer.buffer).startswith(status)
    assert factory_calls == 0
    assert handler.connect_calls == 0
    assert server.sessions == ()


async def test_accept_missing_key_is_400() -> None:
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    reader = asyncio.StreamReader()
    writer = FakeWriter()
    reader.feed_data(b"GET / HTTP/1.1\r\nHost: x\r\n\r\n")
    await server._accept_request(reader, writer)
    assert writer.buffer.startswith(b"HTTP/1.1 400 ")
    assert server.sessions == ()


async def test_accept_garbage_is_400() -> None:
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    reader = asyncio.StreamReader()
    writer = FakeWriter()
    reader.feed_data(b"HELLO\r\n\r\n")
    await server._accept_request(reader, writer)
    assert writer.buffer.startswith(b"HTTP/1.1 400 ")
    assert server.sessions == ()


async def test_stop_without_start_is_clean() -> None:
    server = make_server(FakeHandler(), FakeClock())
    await server.stop()
    await server.stop()
    assert server.sessions == ()


class _FakeListener:
    """Stand-in for ``asyncio.Server``: no sockets, records close."""

    def __init__(self) -> None:
        self.closed = False
        self.wait_closed_calls = 0

    @property
    def sockets(self) -> list[object]:
        return []

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        self.wait_closed_calls += 1

    def is_serving(self) -> bool:
        return not self.closed


async def test_stop_during_bind_closes_orphan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bind outlived by a concurrent stop is closed, never installed.

    ``start()`` parks inside the bind while ``stop()`` runs: when the
    bind completes, the orphan is closed and ``start()`` reports the
    invalidation instead of installing a listener after shutdown.
    """
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    entered = asyncio.Event()
    gate = asyncio.Event()
    created: list[_FakeListener] = []

    async def _gated_start(*args: object, **kwargs: object) -> object:
        entered.set()
        await gate.wait()
        listener = _FakeListener()
        created.append(listener)
        return listener

    monkeypatch.setattr(asyncio, "start_server", _gated_start)
    start_task = asyncio.create_task(server.start())
    try:
        async with asyncio.timeout(2.0):
            await entered.wait()
        await server.stop()
        gate.set()
        try:
            async with asyncio.timeout(10.0):
                await start_task
        except RuntimeError:
            pass
        else:
            raise AssertionError("start() outlived by stop() must not install")
        assert server._listener is None
        assert len(created) == 1
        assert created[0].closed
    finally:
        gate.set()
        if not start_task.done():
            start_task.cancel()
            try:
                await start_task
            except (asyncio.CancelledError, RuntimeError):
                pass
        for listener in created:
            listener.close()
        await server.stop()


async def test_replacement_start_survives_completed_predecessor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A completed orphan must not clear its replacement's ownership.

    A parks in the bind, ``stop()`` invalidates it, B parks in the
    bind, then A completes: A closes its orphan and reports the
    invalidation but leaves B's ``_starting`` alone, so B installs and
    serves when its own bind completes.
    """
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    entered_a = asyncio.Event()
    gate_a = asyncio.Event()
    entered_b = asyncio.Event()
    gate_b = asyncio.Event()
    created: list[_FakeListener] = []
    calls = 0

    async def _gated_start(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            entered_a.set()
            await gate_a.wait()
        else:
            entered_b.set()
            await gate_b.wait()
        listener = _FakeListener()
        created.append(listener)
        return listener

    monkeypatch.setattr(asyncio, "start_server", _gated_start)
    task_a = asyncio.create_task(server.start())
    task_b: asyncio.Task[None] | None = None
    try:
        async with asyncio.timeout(2.0):
            await entered_a.wait()
        await server.stop()
        task_b = asyncio.create_task(server.start())
        async with asyncio.timeout(2.0):
            await entered_b.wait()
        gate_a.set()
        try:
            async with asyncio.timeout(10.0):
                await task_a
        except RuntimeError:
            pass
        else:
            raise AssertionError("invalidated start() must not install")
        assert server._listener is None
        assert created[0].closed
        assert server._starting is True
        gate_b.set()
        async with asyncio.timeout(10.0):
            await task_b
        assert server._listener is created[1]
        assert not created[1].closed
        assert server._starting is False
    finally:
        gate_a.set()
        gate_b.set()
        for task in (task_a, task_b):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, RuntimeError):
                    pass
        for listener in created:
            listener.close()
        await server.stop()


async def test_replacement_start_survives_failing_predecessor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed orphan must not clear its replacement's ownership.

    Same as above, but A's bind raises instead of completing: the
    original error propagates from A, B's ``_starting`` stays set, and
    B installs when its own bind completes.
    """
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    entered_a = asyncio.Event()
    gate_a = asyncio.Event()
    entered_b = asyncio.Event()
    gate_b = asyncio.Event()
    created: list[_FakeListener] = []
    calls = 0

    async def _gated_start(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        if calls == 1:
            entered_a.set()
            await gate_a.wait()
            raise OSError("bind failed")
        entered_b.set()
        await gate_b.wait()
        listener = _FakeListener()
        created.append(listener)
        return listener

    monkeypatch.setattr(asyncio, "start_server", _gated_start)
    task_a = asyncio.create_task(server.start())
    task_b: asyncio.Task[None] | None = None
    try:
        async with asyncio.timeout(2.0):
            await entered_a.wait()
        await server.stop()
        task_b = asyncio.create_task(server.start())
        async with asyncio.timeout(2.0):
            await entered_b.wait()
        gate_a.set()
        try:
            async with asyncio.timeout(10.0):
                await task_a
        except OSError:
            pass
        else:
            raise AssertionError("failed bind must raise its own error")
        assert server._listener is None
        assert server._starting is True
        gate_b.set()
        async with asyncio.timeout(10.0):
            await task_b
        assert server._listener is created[0]
        assert not created[0].closed
        assert server._starting is False
    finally:
        gate_a.set()
        gate_b.set()
        for task in (task_a, task_b):
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, RuntimeError, OSError):
                    pass
        for listener in created:
            listener.close()
        await server.stop()


async def test_concurrent_starts_reject_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two overlapping ``start()`` calls never bind twice."""
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    entered = asyncio.Event()
    gate = asyncio.Event()
    created: list[_FakeListener] = []

    async def _gated_start(*args: object, **kwargs: object) -> object:
        entered.set()
        await gate.wait()
        listener = _FakeListener()
        created.append(listener)
        return listener

    monkeypatch.setattr(asyncio, "start_server", _gated_start)
    first = asyncio.create_task(server.start())
    try:
        async with asyncio.timeout(2.0):
            await entered.wait()
        try:
            async with asyncio.timeout(2.0):
                await server.start()
        except RuntimeError:
            pass
        except TimeoutError:
            raise AssertionError("overlapping start() was not rejected") from None
        else:
            raise AssertionError("overlapping start() was not rejected")
        gate.set()
        async with asyncio.timeout(10.0):
            await first
        assert len(created) == 1
    finally:
        gate.set()
        if not first.done():
            first.cancel()
            try:
                await first
            except (asyncio.CancelledError, RuntimeError):
                pass
        for listener in created:
            listener.close()
        await server.stop()


async def test_server_defaults_use_leaf_constants() -> None:
    server = make_server(FakeHandler(), FakeClock())
    assert server._ping_interval == PING_INTERVAL_S
    assert server._coalesce_window == COALESCE_WINDOW_S
    assert server._deadline == DEADLINE_S


async def test_stalled_sender_bounded_outbox() -> None:
    """A stalled writer that keeps inbound alive cannot grow memory.

    Session A blocks in ``drain`` while inbound commands keep arriving
    (so the watchdog cannot fire): retention stays bounded, the session
    is aborted through the normal teardown (disconnect once, dropped
    from the registry), and healthy session B still receives a later
    broadcast.
    """
    clock = FakeClock()
    handler = FakeHandler(replies={"echo": CommandOutcome(to_sender=["echo:1;"])})
    server = make_server(handler, clock)
    writer_a = FakeWriter()
    writer_a.block_drain()
    session_a, _writer, reader_a = await open_session(server, handler, writer_a)
    session_b, writer_b, _reader_b = await open_session(server, handler)
    try:
        await wait_for_frames(writer_b, len(BURST))
        for _ in range(MAX_QUEUED_OUTBOUND + 50):
            reader_a.feed_data(client_frame(WS_OP_TEXT, b"echo:1;"))
        async with asyncio.timeout(5.0):
            while handler.disconnect_calls == 0:
                await asyncio.sleep(0.005)
        assert handler.disconnect_calls == 1
        assert session_a not in server.sessions
        assert session_a._done
        assert session_a._outbox.qsize() <= MAX_QUEUED_OUTBOUND
        assert len(session_a._pending) <= MAX_QUEUED_OUTBOUND
        server.broadcast(["ok:1;"])
        frames_b = await wait_for_frames(writer_b, len(BURST) + 1)
        assert frames_b[-1] == (WS_OP_TEXT, b"ok:1;")
    finally:
        await session_a.finish()
        await session_b.finish()
    assert handler.disconnect_calls == 2


async def test_finish_completes_with_blocked_drain() -> None:
    """``finish()`` bounds the graceful close and falls back to abort.

    With ``drain`` blocked forever the close frame can never flush:
    finish must still complete (bounded), run the disconnect exactly
    once, release the transport, drop the session, and stay harmless
    when called again.
    """
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    writer = FakeWriter()
    writer.block_drain()
    session, _writer, _reader = await open_session(server, handler, writer)
    started = time.monotonic()
    async with asyncio.timeout(10.0):
        await session.finish()
    assert time.monotonic() - started < 10.0
    assert handler.disconnect_calls == 1
    assert session not in server.sessions
    assert writer.transport.aborted
    await session.finish()
    assert handler.disconnect_calls == 1


async def test_cancelled_finish_still_releases() -> None:
    """Cancelling ``finish()`` mid-close still releases everything.

    The abort fires, the disconnect runs exactly once, the session
    leaves the registry, and latecomers observe completion.
    """
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    writer = FakeWriter()
    writer.block_drain()
    session, _writer, _reader = await open_session(server, handler, writer)
    task = asyncio.create_task(session.finish())
    async with asyncio.timeout(2.0):
        while not session._done:
            await asyncio.sleep(0.005)
    await asyncio.sleep(0.05)
    task.cancel()
    async with asyncio.timeout(10.0):
        while not task.done():
            await asyncio.sleep(0.005)
    assert handler.disconnect_calls == 1
    assert session not in server.sessions
    await session.finish()
    assert handler.disconnect_calls == 1


async def test_disconnect_reawaited_after_first_cancel() -> None:
    """A cancelled disconnect wait is retained and re-awaited.

    Cancelling ``finish()`` while ``on_disconnect`` is blocked must not
    complete the finish or drop the session: the callback task is kept
    and waited on again, and only its completion releases the session.
    """
    clock = FakeClock()
    handler = FakeHandler()
    entered = asyncio.Event()
    completed = asyncio.Event()
    gate = asyncio.Event()

    async def _gated_disconnect() -> None:
        handler.disconnect_calls += 1
        entered.set()
        await gate.wait()
        completed.set()

    handler.on_disconnect = _gated_disconnect  # type: ignore[method-assign]
    server = make_server(handler, clock)
    session, _writer, _reader = await open_session(server, handler)
    task = asyncio.create_task(session.finish())
    async with asyncio.timeout(2.0):
        await entered.wait()
    task.cancel()
    await asyncio.sleep(0.05)
    assert not task.done()
    assert not completed.is_set()
    assert session in server.sessions
    gate.set()
    async with asyncio.timeout(5.0):
        await task
    assert completed.is_set()
    assert handler.disconnect_calls == 1
    assert session not in server.sessions


async def test_bounded_join_runs_disconnect_before_gated_cleanup() -> None:
    """A gated cancellation cleanup must not wedge the disconnect.

    The handler's ``on_command`` catches its cancellation and parks in a
    gated cleanup: ``finish()`` still completes bounded (disconnect runs
    exactly once, session dropped) before the gate is released.
    """
    from rigplane.tci.server import TEARDOWN_TIMEOUT_S

    clock = FakeClock()
    entered = asyncio.Event()
    cleanup_entered = asyncio.Event()
    gate = asyncio.Event()

    class _GatedHandler(FakeHandler):
        async def on_command(self, name: str, args: tuple[str, ...]) -> CommandOutcome:
            entered.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cleanup_entered.set()
                await gate.wait()
                raise
            raise AssertionError("unreachable")

    handler = _GatedHandler()
    server = make_server(handler, clock)
    session, _writer, reader = await open_session(server, handler)
    task: asyncio.Task[None] | None = None
    try:
        reader.feed_data(client_frame(WS_OP_TEXT, b"gated:1;"))
        async with asyncio.timeout(2.0):
            await entered.wait()
        # The finish starts only once the handler is parked inside the
        # command: the cancel must land mid-callback, not mid-read.
        task = asyncio.create_task(session.finish())
        async with asyncio.timeout(2.0):
            await cleanup_entered.wait()
        try:
            async with asyncio.timeout(TEARDOWN_TIMEOUT_S + 5.0):
                await task
        except TimeoutError:
            raise AssertionError(
                "finish() did not complete before the gate was released"
            ) from None
        assert handler.disconnect_calls == 1
        assert session not in server.sessions
    finally:
        gate.set()
        if task is not None:
            if not task.done():
                task.cancel()
            try:
                async with asyncio.timeout(10.0):
                    await task
            except (asyncio.CancelledError, TimeoutError):
                pass
        await session.finish()


async def test_hung_disconnect_is_abandoned_bounded() -> None:
    """A disconnect that never settles is abandoned, not hung on.

    Each wait on the callback is bounded (two waits, like the managed
    handback): finish still completes, drops the session, and marks
    completion even when the handler never returns.
    """
    clock = FakeClock()
    handler = FakeHandler()
    handler.disconnect_gate = asyncio.Event()
    server = make_server(handler, clock)
    session, _writer, _reader = await open_session(server, handler)
    started = time.monotonic()
    async with asyncio.timeout(15.0):
        await session.finish()
    assert time.monotonic() - started < 15.0
    assert session not in server.sessions


async def test_reply_ordered_behind_pending_broadcast() -> None:
    """An immediate reply never jumps ahead of a held broadcast."""
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    session, writer, _reader = await open_session(server, handler)
    try:
        await wait_for_frames(writer, len(BURST))
        server.broadcast(["vfo:0,0,111;"])
        assert session._pending == ["vfo:0,0,111;"]
        session._send_now("vfo:0,0,222;")
        frames = await wait_for_frames(writer, len(BURST) + 2)
        assert [payload for _, payload in frames[-2:]] == [
            b"vfo:0,0,111;",
            b"vfo:0,0,222;",
        ]
    finally:
        await session.finish()


async def test_close_with_blocked_echo_watchdog_aborts() -> None:
    """A received CLOSE with a saturated echo drain still dies on watchdog.

    The close path marks the connection closed and parks in the echo
    drain (blocked peer). When the watchdog fires it must still abort
    the actual transport — not return early on protocol-close state —
    run the disconnect exactly once, drop the session, and finish
    bounded.
    """
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    writer = FakeWriter()
    writer.block_drain()
    session, _writer, reader = await open_session(server, handler, writer)
    try:
        await wait_for_frames(writer, 1)
        reader.feed_data(client_frame(WS_OP_CLOSE, b"\x03\xe8"))
        async with asyncio.timeout(5.0):
            while not session._conn.closed:
                await asyncio.sleep(0.005)
        clock.advance(DEADLINE_S + 1.0)
        async with asyncio.timeout(10.0):
            assert await session.check_deadline(clock()) is True
        assert writer.transport.aborted
        assert handler.disconnect_calls == 1
        assert session not in server.sessions
    finally:
        await session.finish()
    assert handler.disconnect_calls == 1


async def test_ping_loop_recovers_after_stalled_write() -> None:
    """A stalled ping write must not kill the heartbeat forever.

    The drain blocks past one bounded write (timeout), then recovers:
    subsequent pings must still go out while the session is live.
    """
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock, ping_interval=0.05)
    session, writer, _reader = await open_session(server, handler)
    try:

        async def _ping_count() -> int:
            return sum(
                1
                for opcode, _ in parse_server_frames(writer.buffer)
                if opcode == WS_OP_PING
            )

        async def _wait_pings(count: int, timeout: float = 5.0) -> None:
            async with asyncio.timeout(timeout):
                while await _ping_count() < count:
                    await asyncio.sleep(0.005)

        await _wait_pings(1)
        writer.block_drain()
        await asyncio.sleep(0.2)
        stalled = await _ping_count()
        writer._drain = None
        try:
            await _wait_pings(stalled + 2)
        except TimeoutError:
            raise AssertionError("ping loop died after one stalled write") from None
        assert await _ping_count() >= stalled + 2
        assert session in server.sessions
    finally:
        await session.finish()


async def test_pong_answers_ping() -> None:
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    session, writer, reader = await open_session(server, handler)
    try:
        await wait_for_frames(writer, len(BURST))
        reader.feed_data(client_frame(WS_OP_PING, b"hi"))
        frames = await wait_for_frames(writer, len(BURST) + 1)
        assert frames[-1] == (WS_OP_PONG, b"hi")
    finally:
        await session.finish()


async def test_gated_finalizer_does_not_wedge_finish() -> None:
    """A disconnect parked in an async finalizer must not wedge ``finish()``.

    The callback is cancelled after entry and parks in a gated
    ``finally``: ``finish()`` must still return bounded (registry
    dropped, transport released) before the gate opens; opening the
    gate then completes the cleanup exactly once.
    """
    from rigplane.tci.server import TEARDOWN_TIMEOUT_S

    clock = FakeClock()
    entered = asyncio.Event()
    finalizing = asyncio.Event()
    finalized = asyncio.Event()
    cleanup_gate = asyncio.Event()

    class _FinalizingHandler(FakeHandler):
        async def on_disconnect(self) -> None:
            self.disconnect_calls += 1
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                finalizing.set()
                await cleanup_gate.wait()
                finalized.set()

    handler = _FinalizingHandler()
    server = make_server(handler, clock)
    session, writer, _reader = await open_session(server, handler)
    task = asyncio.create_task(session.finish())
    try:
        async with asyncio.timeout(2.0):
            await entered.wait()
        try:
            async with asyncio.timeout(TEARDOWN_TIMEOUT_S * 3 + 5.0):
                await task
        except TimeoutError:
            raise AssertionError(
                "finish() wedged on a gated disconnect finalizer"
            ) from None
        assert finalizing.is_set()
        assert not finalized.is_set()
        assert handler.disconnect_calls == 1
        assert session not in server.sessions
        assert writer.is_closing()
        cleanup_gate.set()
        async with asyncio.timeout(5.0):
            while not finalized.is_set():
                await asyncio.sleep(0.005)
        assert handler.disconnect_calls == 1
    finally:
        cleanup_gate.set()
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, TimeoutError):
                pass
        await session.finish()


async def test_stop_returns_while_disconnect_finalizer_gated() -> None:
    """``stop()`` must return bounded with a gated disconnect finalizer.

    The session teardown parks in an async ``finally`` behind the two
    bounded waits: the aggregate ``stop()`` wait still returns within
    its own budget, and opening the gate completes the disconnect
    exactly once.
    """
    clock = FakeClock()
    entered = asyncio.Event()
    finalized = asyncio.Event()
    cleanup_gate = asyncio.Event()

    class _FinalizingHandler(FakeHandler):
        async def on_disconnect(self) -> None:
            self.disconnect_calls += 1
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                await cleanup_gate.wait()
                finalized.set()

    handler = _FinalizingHandler()
    server = make_server(handler, clock, stop_timeout=10.0)
    session, _writer, _reader = await open_session(server, handler)
    task = asyncio.create_task(server.stop())
    try:
        async with asyncio.timeout(2.0):
            await entered.wait()
        try:
            async with asyncio.timeout(15.0):
                await task
        except TimeoutError:
            raise AssertionError(
                "stop() wedged on a gated disconnect finalizer"
            ) from None
        assert handler.disconnect_calls == 1
        assert session not in server.sessions
        cleanup_gate.set()
        async with asyncio.timeout(5.0):
            while not finalized.is_set():
                await asyncio.sleep(0.005)
        assert handler.disconnect_calls == 1
    finally:
        cleanup_gate.set()
        if not task.done():
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, TimeoutError):
                pass
        await server.stop()


async def test_gated_command_pong_keeps_session_alive() -> None:
    """A pong received while a command runs must still renew liveness.

    The reader keeps consuming inbound frames while the command
    handler is parked: with the deadline already passed since the
    command arrived, a pong still releases the session instead of the
    watchdog aborting a responsive peer.
    """
    clock = FakeClock()
    gate = asyncio.Event()
    entered = asyncio.Event()

    class _GatedHandler(FakeHandler):
        async def on_command(self, name: str, args: tuple[str, ...]) -> CommandOutcome:
            entered.set()
            await gate.wait()
            return CommandOutcome(to_sender=["done:1;"])

    handler = _GatedHandler()
    server = make_server(handler, clock)
    session, writer, reader = await open_session(server, handler)
    try:
        await wait_for_frames(writer, len(BURST))
        reader.feed_data(client_frame(WS_OP_TEXT, b"gated:1;"))
        async with asyncio.timeout(2.0):
            await entered.wait()
        clock.advance(DEADLINE_S + 1.0)
        reader.feed_data(client_frame(WS_OP_PONG, b""))
        try:
            async with asyncio.timeout(2.0):
                while session._conn.last_activity < clock():
                    await asyncio.sleep(0.005)
        except TimeoutError:
            raise AssertionError(
                "reader stalled behind a gated command; pong unread"
            ) from None
        assert await session.check_deadline(clock()) is False
        assert handler.disconnect_calls == 0
        gate.set()
        frames = await wait_for_frames(writer, len(BURST) + 1)
        assert frames[-1] == (WS_OP_TEXT, b"done:1;")
        assert handler.disconnect_calls == 0
    finally:
        gate.set()
        await session.finish()


async def test_gated_binary_pong_keeps_session_alive() -> None:
    """A pong received while binary handling runs must still renew liveness.

    Binary callbacks run on the same dispatch path as commands: a
    parked ``on_binary`` must not stall the reader either.
    """
    clock = FakeClock()
    gate = asyncio.Event()
    entered = asyncio.Event()

    class _GatedHandler(FakeHandler):
        async def on_binary(self, payload: bytes) -> None:
            entered.set()
            await gate.wait()
            await super().on_binary(payload)

    handler = _GatedHandler()
    server = make_server(handler, clock)
    session, writer, reader = await open_session(server, handler)
    try:
        await wait_for_frames(writer, len(BURST))
        reader.feed_data(client_frame(WS_OP_BINARY, b"\x01\x02\x03"))
        async with asyncio.timeout(2.0):
            await entered.wait()
        clock.advance(DEADLINE_S + 1.0)
        reader.feed_data(client_frame(WS_OP_PONG, b""))
        try:
            async with asyncio.timeout(2.0):
                while session._conn.last_activity < clock():
                    await asyncio.sleep(0.005)
        except TimeoutError:
            raise AssertionError(
                "reader stalled behind gated binary; pong unread"
            ) from None
        assert await session.check_deadline(clock()) is False
        assert handler.disconnect_calls == 0
        gate.set()
        async with asyncio.timeout(2.0):
            while not handler.binaries:
                await asyncio.sleep(0.005)
        assert handler.binaries == [b"\x01\x02\x03"]
        assert handler.disconnect_calls == 0
    finally:
        gate.set()
        await session.finish()


async def test_join_observed_before_gate_with_repeated_cancel_command() -> None:
    """A command cleanup surviving repeated cancel must not wedge ``finish()``.

    The dispatch callback parks in a gated ``finally`` that ignores
    further cancellation (re-awaits the gate): ``finish()`` must still
    complete bounded (disconnect once, session dropped, transport
    released) before the gate opens; the retained cleanup completes
    after the gate opens.
    """
    from rigplane.tci.server import TEARDOWN_TIMEOUT_S

    clock = FakeClock()
    entered = asyncio.Event()
    cleaning = asyncio.Event()
    cleaned = asyncio.Event()
    gate = asyncio.Event()

    seen: list[str] = []

    class _RepeatCancelHandler(FakeHandler):
        async def on_command(self, name: str, args: tuple[str, ...]) -> CommandOutcome:
            seen.append(name)
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                while not gate.is_set():
                    try:
                        await gate.wait()
                    except asyncio.CancelledError:
                        continue
                cleaned.set()
                return CommandOutcome()

    handler = _RepeatCancelHandler()
    server = make_server(handler, clock)
    session, writer, reader = await open_session(server, handler)
    task: asyncio.Task[None] | None = None
    try:
        await wait_for_frames(writer, len(BURST))
        reader.feed_data(client_frame(WS_OP_TEXT, b"gated;second;"))
        async with asyncio.timeout(2.0):
            await entered.wait()
        task = asyncio.create_task(session.finish())
        async with asyncio.timeout(2.0):
            await cleaning.wait()
        # Observe with ``wait`` (never ``await`` the task under a timeout:
        # cancelling the waiter would propagate into the join under test
        # and mask the wedge). ``wait`` returns bounded either way.
        _, pending_wait = await asyncio.wait([task], timeout=TEARDOWN_TIMEOUT_S + 5.0)
        if pending_wait:
            raise AssertionError("finish() wedged on a repeat-cancel command cleanup")
        assert handler.disconnect_calls == 1
        assert session not in server.sessions
        assert writer.is_closing()
        assert not cleaned.is_set()
        assert seen == ["gated"], f"second command dispatched after teardown: {seen}"
        # The retained cleanup survives a second cancellation too.
        pending = [t for t in list(session._tasks) if not t.done()]
        for t in pending:
            t.cancel()
        await asyncio.sleep(0.05)
        assert not cleaned.is_set()
        assert seen == ["gated"], f"second command dispatched after teardown: {seen}"
        gate.set()
        async with asyncio.timeout(5.0):
            while not cleaned.is_set():
                await asyncio.sleep(0.005)
        assert handler.disconnect_calls == 1
        assert seen == ["gated"], f"second command dispatched after teardown: {seen}"
    finally:
        gate.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait([task], timeout=10.0)
        for pending_task in list(session._tasks):
            if not pending_task.done():
                pending_task.cancel()
        try:
            async with asyncio.timeout(5.0):
                await session.finish()
        except (asyncio.CancelledError, TimeoutError):
            pass


async def test_second_command_never_dispatched_when_first_raises_after_teardown() -> None:
    """A raising first command must not leak the second one past teardown.

    Same shape as the repeat-cancel join test, but the gated cleanup
    raises instead of returning an empty outcome: the ``except``
    arm in ``_handle_text`` must still observe ``_done`` before the
    next dispatch, so ``second`` never runs.
    """
    from rigplane.tci.server import TEARDOWN_TIMEOUT_S

    clock = FakeClock()
    entered = asyncio.Event()
    cleaning = asyncio.Event()
    cleaned = asyncio.Event()
    gate = asyncio.Event()
    seen: list[str] = []

    class _RaisingRepeatCancelHandler(FakeHandler):
        async def on_command(self, name: str, args: tuple[str, ...]) -> CommandOutcome:
            seen.append(name)
            if name != "gated":
                return CommandOutcome()
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                while not gate.is_set():
                    try:
                        await gate.wait()
                    except asyncio.CancelledError:
                        continue
                cleaned.set()
                raise RuntimeError("boom-after-teardown")

    handler = _RaisingRepeatCancelHandler()
    server = make_server(handler, clock)
    session, writer, reader = await open_session(server, handler)
    task: asyncio.Task[None] | None = None
    try:
        await wait_for_frames(writer, len(BURST))
        reader.feed_data(client_frame(WS_OP_TEXT, b"gated;second;"))
        async with asyncio.timeout(2.0):
            await entered.wait()
        task = asyncio.create_task(session.finish())
        async with asyncio.timeout(2.0):
            await cleaning.wait()
        _, pending_wait = await asyncio.wait([task], timeout=TEARDOWN_TIMEOUT_S + 5.0)
        if pending_wait:
            raise AssertionError("finish() wedged on a raising repeat-cancel cleanup")
        assert handler.disconnect_calls == 1
        assert session not in server.sessions
        assert writer.is_closing()
        assert not cleaned.is_set()
        assert seen == ["gated"], f"second command dispatched after teardown: {seen}"
        gate.set()
        async with asyncio.timeout(5.0):
            while not cleaned.is_set():
                await asyncio.sleep(0.005)
        assert seen == ["gated"], f"second command dispatched after teardown: {seen}"
        assert handler.disconnect_calls == 1
    finally:
        gate.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait([task], timeout=10.0)
        for pending_task in list(session._tasks):
            if not pending_task.done():
                pending_task.cancel()
        try:
            async with asyncio.timeout(5.0):
                await session.finish()
        except (asyncio.CancelledError, TimeoutError):
            pass


async def test_stop_closes_pending_setup_transport_before_gate_release() -> None:
    """``stop()`` releases the accepted socket even when setup never returns.

    A cancellation-resistant ``on_connect`` (repeated-cancel shape):
    ``stop()`` must close the pending writer before the gate opens
    (the client observes closed), then the retained setup completes
    after the gate opens with exactly-once disconnect and no session.
    """
    from rigplane.tci.server import TEARDOWN_TIMEOUT_S

    clock = FakeClock()
    entered = asyncio.Event()
    cleaning = asyncio.Event()
    cleaned = asyncio.Event()
    gate = asyncio.Event()

    class _RepeatCancelConnectHandler(FakeHandler):
        async def on_connect(self) -> Sequence[str]:
            self.connect_calls += 1
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                while not gate.is_set():
                    try:
                        await gate.wait()
                    except asyncio.CancelledError:
                        continue
                cleaned.set()
            return list(BURST)

    handler = _RepeatCancelConnectHandler()
    server = make_server(handler, clock)
    reader = asyncio.StreamReader()
    writer = FakeWriter()
    setup_task = asyncio.create_task(server._open_session(reader, writer, handler))
    server._setup_tasks.add(setup_task)
    stop_task: asyncio.Task[None] | None = None
    try:
        async with asyncio.timeout(2.0):
            await entered.wait()
        stop_task = asyncio.create_task(server.stop())
        async with asyncio.timeout(2.0):
            await cleaning.wait()
        _, pending_wait = await asyncio.wait([stop_task], timeout=10.0)
        if pending_wait:
            raise AssertionError("stop() wedged on a repeat-cancel on_connect cleanup")
        assert writer.is_closing(), "pending setup transport still open after stop()"
        assert not cleaned.is_set()
        assert server.sessions == ()
        gate.set()
        _, pending_setup = await asyncio.wait([setup_task], timeout=5.0)
        assert not pending_setup, "setup did not complete after the gate opened"
        async with asyncio.timeout(5.0):
            while not cleaned.is_set():
                await asyncio.sleep(0.005)
        assert handler.disconnect_calls == 1
        assert server.sessions == ()
    finally:
        gate.set()
        server._setup_tasks.discard(setup_task)
        if not setup_task.done():
            setup_task.cancel()
            await asyncio.wait([setup_task], timeout=5.0)
        if stop_task is not None and not stop_task.done():
            stop_task.cancel()
            await asyncio.wait([stop_task], timeout=5.0)
        try:
            async with asyncio.timeout(10.0):
                await server.stop()
        except (asyncio.CancelledError, TimeoutError):
            pass
        assert TEARDOWN_TIMEOUT_S > 0


async def test_join_observed_before_gate_with_repeated_cancel_binary() -> None:
    """A binary cleanup surviving repeated cancel must not wedge ``finish()``.

    Same join as the command path (the dispatch task runs both
    callbacks): ``finish()`` completes bounded before the gate opens,
    the retained binary cleanup completes after.
    """
    from rigplane.tci.server import TEARDOWN_TIMEOUT_S

    clock = FakeClock()
    entered = asyncio.Event()
    cleaning = asyncio.Event()
    cleaned = asyncio.Event()
    gate = asyncio.Event()

    class _RepeatCancelBinaryHandler(FakeHandler):
        async def on_binary(self, payload: bytes) -> None:
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                while not gate.is_set():
                    try:
                        await gate.wait()
                    except asyncio.CancelledError:
                        continue
                self.binaries.append(bytes(payload))
                cleaned.set()

    handler = _RepeatCancelBinaryHandler()
    server = make_server(handler, clock)
    session, writer, reader = await open_session(server, handler)
    task: asyncio.Task[None] | None = None
    try:
        await wait_for_frames(writer, len(BURST))
        reader.feed_data(client_frame(WS_OP_BINARY, b"\x01\x02"))
        async with asyncio.timeout(2.0):
            await entered.wait()
        task = asyncio.create_task(session.finish())
        async with asyncio.timeout(2.0):
            await cleaning.wait()
        # Observe with ``wait``: awaiting under a timeout would cancel
        # the join under test and mask the wedge.
        _, pending_wait = await asyncio.wait([task], timeout=TEARDOWN_TIMEOUT_S + 5.0)
        if pending_wait:
            raise AssertionError("finish() wedged on a repeat-cancel binary cleanup")
        assert handler.disconnect_calls == 1
        assert session not in server.sessions
        assert not cleaned.is_set()
        pending = [t for t in list(session._tasks) if not t.done()]
        for t in pending:
            t.cancel()
        await asyncio.sleep(0.05)
        assert not cleaned.is_set()
        gate.set()
        async with asyncio.timeout(5.0):
            while not cleaned.is_set():
                await asyncio.sleep(0.005)
        assert handler.binaries == [b"\x01\x02"]
    finally:
        gate.set()
        if task is not None and not task.done():
            task.cancel()
            await asyncio.wait([task], timeout=10.0)
        for pending_task in list(session._tasks):
            if not pending_task.done():
                pending_task.cancel()
        try:
            async with asyncio.timeout(5.0):
                await session.finish()
        except (asyncio.CancelledError, TimeoutError):
            pass


async def test_stop_observed_before_gate_with_repeated_cancel_connect() -> None:
    """An ``on_connect`` cleanup surviving repeated cancel must not wedge ``stop()``.

    The pending setup parks in a gated ``finally`` that ignores further
    cancellation: ``stop()`` still returns bounded before the gate
    opens; the retained setup completes after.
    """
    clock = FakeClock()
    entered = asyncio.Event()
    cleaning = asyncio.Event()
    cleaned = asyncio.Event()
    gate = asyncio.Event()

    class _RepeatCancelConnectHandler(FakeHandler):
        async def on_connect(self) -> Sequence[str]:
            self.connect_calls += 1
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                while not gate.is_set():
                    try:
                        await gate.wait()
                    except asyncio.CancelledError:
                        continue
                cleaned.set()
            return list(BURST)

    handler = _RepeatCancelConnectHandler()
    server = make_server(handler, clock)
    reader = asyncio.StreamReader()
    writer = FakeWriter()
    setup_task = asyncio.create_task(server._open_session(reader, writer, handler))
    server._setup_tasks.add(setup_task)
    stop_task: asyncio.Task[None] | None = None
    try:
        async with asyncio.timeout(2.0):
            await entered.wait()
        stop_task = asyncio.create_task(server.stop())
        async with asyncio.timeout(2.0):
            await cleaning.wait()
        # Observe with ``wait``: awaiting under a timeout would cancel
        # the join under test and mask the wedge.
        _, pending_wait = await asyncio.wait([stop_task], timeout=10.0)
        if pending_wait:
            raise AssertionError("stop() wedged on a repeat-cancel on_connect cleanup")
        assert not cleaned.is_set()
        assert not setup_task.done()
        setup_task.cancel()
        await asyncio.sleep(0.05)
        assert not cleaned.is_set()
        gate.set()
        _, pending_setup = await asyncio.wait([setup_task], timeout=5.0)
        assert not pending_setup, "setup did not complete after the gate opened"
        async with asyncio.timeout(5.0):
            while not cleaned.is_set():
                await asyncio.sleep(0.005)
        assert handler.disconnect_calls == 1
    finally:
        gate.set()
        server._setup_tasks.discard(setup_task)
        if not setup_task.done():
            setup_task.cancel()
            await asyncio.wait([setup_task], timeout=5.0)
        if stop_task is not None and not stop_task.done():
            stop_task.cancel()
            await asyncio.wait([stop_task], timeout=5.0)
        try:
            async with asyncio.timeout(10.0):
                await server.stop()
        except (asyncio.CancelledError, TimeoutError):
            pass


async def test_overflowed_sender_observers_still_updated() -> None:
    """An accepted change reaches observers even when the sender overflows.

    Session A stalls (blocked drain) while inbound stays alive; the
    command that overflows A's own reply queue must still fan its
    accepted ``to_others`` change out to healthy session B, and A must
    then disconnect exactly once.
    """
    clock = FakeClock()
    handler = FakeHandler(
        replies={
            "fill": CommandOutcome(to_sender=["x;"]),
            "f": CommandOutcome(to_sender=["s:1;"], to_others=["o:F2;"]),
        }
    )
    server = make_server(handler, clock)
    writer_a = FakeWriter()
    writer_a.block_drain()
    session_a, writer_a2, reader_a = await open_session(server, handler, writer_a)
    assert writer_a2 is writer_a
    session_b, writer_b, _reader_b = await open_session(server, handler)
    try:
        await wait_for_frames(writer_b, len(BURST))
        # Pin A's outbox occupancy: once the first burst frame reaches
        # the wire, A's sender is parked in the blocked drain with
        # exactly one frame in flight, so 3 burst frames stay queued.
        await wait_for_frames(writer_a, 1)
        assert session_a._outbox.qsize() == len(BURST) - 1
        # Fill A's queue with reply-only commands (B observes nothing),
        # stopping exactly at capacity: no overflow yet.
        for _ in range(MAX_QUEUED_OUTBOUND - (len(BURST) - 1)):
            reader_a.feed_data(client_frame(WS_OP_TEXT, b"fill;"))
        async with asyncio.timeout(5.0):
            while len(handler.commands) < MAX_QUEUED_OUTBOUND - (len(BURST) - 1):
                await asyncio.sleep(0.005)
        assert session_a._outbox.qsize() == MAX_QUEUED_OUTBOUND
        assert handler.disconnect_calls == 0
        # The accepted write overflows A's own reply queue: its
        # observer update must still reach healthy B, then A goes.
        reader_a.feed_data(client_frame(WS_OP_TEXT, b"f:1;"))
        async with asyncio.timeout(5.0):
            while handler.disconnect_calls == 0:
                await asyncio.sleep(0.005)
        assert handler.disconnect_calls == 1
        assert session_a not in server.sessions
        frames_b = await wait_for_frames(writer_b, len(BURST) + 1)
        assert frames_b[-1] == (WS_OP_TEXT, b"o:F2;")
    finally:
        await session_a.finish()
        await session_b.finish()
    assert handler.disconnect_calls == 2
