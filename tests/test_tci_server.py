"""Leaf server tests with fake transports (MOR-3821 L2).

No sockets: sessions run over ``asyncio.StreamReader`` fed by hand and
a fake writer, so this file runs anywhere. Real-socket and eesdr-tci
conformance live in ``test_tci_server_sockets.py`` and
``test_tci_eesdr_conformance.py`` (mini-only).
"""

import asyncio
import struct
from collections.abc import Awaitable, Callable, Sequence

from rigplane.tci.handler import CommandOutcome
from rigplane.tci.server import (
    COALESCE_WINDOW_S,
    DEADLINE_S,
    MAX_QUEUED_OUTBOUND,
    PING_INTERVAL_S,
    TciServer,
)
from rigplane.tci.ws import (
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
        b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
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
        from rigplane.tci.ws import UpgradeDecline

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


async def test_server_defaults_use_leaf_constants() -> None:
    server = make_server(FakeHandler(), FakeClock())
    assert server._ping_interval == PING_INTERVAL_S
    assert server._coalesce_window == COALESCE_WINDOW_S


async def test_stalled_sender_bounded_outbox() -> None:
    """A stalled writer that keeps inbound alive cannot grow memory.

    Session A blocks in ``drain`` while broadcasts pile up: retention
    stays bounded, the session is aborted through the normal teardown
    (disconnect once, dropped from the registry), and healthy session B
    still receives every broadcast line.
    """
    clock = FakeClock()
    handler = FakeHandler()
    server = make_server(handler, clock)
    writer_a = FakeWriter()
    writer_a.block_drain()
    session_a, _writer, _reader = await open_session(server, handler, writer_a)
    session_b, writer_b, _reader_b = await open_session(server, handler)
    try:
        await wait_for_frames(writer_b, len(BURST))
        lines = [f"line:{i};" for i in range(MAX_QUEUED_OUTBOUND + 50)]
        server.broadcast(lines)
        async with asyncio.timeout(5.0):
            while handler.disconnect_calls == 0:
                await asyncio.sleep(0.005)
        assert handler.disconnect_calls == 1
        assert session_a not in server.sessions
        assert session_a._done
        assert session_a._outbox.qsize() <= MAX_QUEUED_OUTBOUND
        assert len(session_a._pending) <= MAX_QUEUED_OUTBOUND
        frames_b = await wait_for_frames(writer_b, len(BURST) + len(lines))
        assert [payload for _, payload in frames_b[-len(lines) :]] == [
            line.encode() for line in lines
        ]
    finally:
        await session_a.finish()
        await session_b.finish()
    assert handler.disconnect_calls == 2


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
