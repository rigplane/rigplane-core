"""Real-socket leaf server tests (MOR-3821 L2, mini-only in practice).

Raw asyncio client over loopback TCP: listener lifecycle, HTTP upgrade
rejections, multi-client broadcast and coalescing, binary both ways,
close/reset/stop teardown, cancellation during accept. Skipped where
loopback bind is unavailable (sandbox); the mini runs the whole file.
"""

import asyncio
import socket
import struct
import time

import pytest

from rigplane.tci.server import TciServer
from rigplane.ws import (
    WS_OP_BINARY,
    WS_OP_CLOSE,
    WS_OP_CONTINUATION,
    WS_OP_PING,
    WS_OP_PONG,
    WS_OP_TEXT,
    _MAX_FRAGMENTS,
    _MAX_MESSAGE_BYTES,
    UpgradeDecline,
)

BURST = ["protocol:tciserver,1;", "device:rigplane;", "ready;"]


@pytest.fixture()
def tcp_available() -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
    except OSError as exc:
        pytest.skip(f"loopback bind unavailable: {exc}")
    finally:
        probe.close()


class FakeHandler:
    def __init__(self) -> None:
        self.burst = list(BURST)
        self.commands: list[tuple[str, tuple[str, ...]]] = []
        self.binaries: list[bytes] = []
        self.disconnect_calls = 0

    async def on_connect(self) -> list[str]:
        return list(self.burst)

    async def on_command(self, name: str, args: tuple[str, ...]) -> object:
        from rigplane.tci.handler import CommandOutcome

        self.commands.append((name, args))
        if name == "echo":
            return CommandOutcome(
                to_sender=[f"echo:{','.join(args)};"],
                to_others=[f"echo:{','.join(args)};"],
            )
        if name == "refuse":
            return CommandOutcome(to_sender=["refuse:0;"])
        return CommandOutcome()

    async def on_binary(self, payload: bytes) -> None:
        self.binaries.append(bytes(payload))

    async def on_disconnect(self) -> None:
        self.disconnect_calls += 1


def tci_allow(headers: dict[str, str]) -> UpgradeDecline | None:
    """Stand-in for the adapter predicate (PR 4 owns the real one).

    Native clients send neither header and connect; a browser-style
    Origin or a non-local Host is refused with 403.
    """
    from rigplane.core.host_guard import host_header_allowed

    raw_host = headers.get("host")
    if not host_header_allowed(raw_host, ()):
        return UpgradeDecline(403, "Forbidden", b"forbidden: bad host")
    if headers.get("origin") is not None:
        return UpgradeDecline(403, "Forbidden", b"forbidden: origin not allowed")
    return None


def make_server(handler: FakeHandler, port: int = 0, **kwargs: object) -> TciServer:
    import time

    return TciServer(
        lambda: handler,  # type: ignore[return-value]
        host="127.0.0.1",
        port=port,
        allow=tci_allow,  # type: ignore[arg-type]
        clock=time.monotonic,
        **kwargs,  # type: ignore[arg-type]
    )


class RawClient:
    """Minimal WS client: HTTP upgrade, masked sends, frame parsing."""

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.reader = reader
        self.writer = writer
        self.received: list[tuple[int, bytes]] = []

    @classmethod
    async def connect(
        cls, port: int, headers: dict[str, str] | None = None
    ) -> "RawClient":
        reader, writer = await asyncio.open_connection("127.0.0.1", port)
        lines = [
            "GET / HTTP/1.1",
            "Host: 127.0.0.1",
            "Connection: Upgrade",
            "Upgrade: websocket",
            "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
            "Sec-WebSocket-Version: 13",
        ]
        for name, value in (headers or {}).items():
            lines.append(f"{name}: {value}")
        lines.extend(["", ""])
        writer.write("\r\n".join(lines).encode())
        await writer.drain()
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5.0)
        assert head.startswith(b"HTTP/1.1 101 "), head[:24]
        return cls(reader, writer)

    async def send_text(self, text: str) -> None:
        await self._send(WS_OP_TEXT, text.encode())

    async def send_binary(self, payload: bytes) -> None:
        await self._send(WS_OP_BINARY, payload)

    async def _send(self, opcode: int, payload: bytes) -> None:
        mask = b"\xaa\xbb\xcc\xdd"
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
        self.writer.write(
            struct.pack("!BB", 0x80 | opcode, 0x80 | len(payload)) + mask + masked
        )
        await self.writer.drain()

    async def next_frame(self, timeout: float = 5.0) -> tuple[int, bytes]:
        head = await asyncio.wait_for(self.reader.readexactly(2), timeout)
        opcode = head[0] & 0x0F
        length = head[1] & 0x7F
        if length == 126:
            (length,) = struct.unpack("!H", await self.reader.readexactly(2))
        elif length == 127:
            (length,) = struct.unpack("!Q", await self.reader.readexactly(8))
        payload = await self.reader.readexactly(length)
        self.received.append((opcode, payload))
        return opcode, payload

    async def read_until_ready(self) -> list[bytes]:
        texts: list[bytes] = []
        async with asyncio.timeout(5.0):
            while True:
                opcode, payload = await self.next_frame()
                assert opcode == WS_OP_TEXT
                texts.append(payload)
                if payload == b"ready;":
                    return texts

    async def close(self) -> None:
        self.writer.write(
            struct.pack("!BB", 0x88, 0x80 | 2) + b"\x00" * 4 + b"\x03\xe8"
        )
        await self.writer.drain()
        self.writer.close()
        await self.writer.wait_closed()


async def _read_until_closed(client: "RawClient") -> None:
    """Drain everything (e.g. in-flight pings) until EOF or reset.

    The watchdog abort manifests as EOF or a connection reset; either
    proves the transport is gone. Ping bytes still in flight are not
    the outcome — the closed transport is.
    """
    async with asyncio.timeout(5.0):
        while True:
            try:
                chunk = await client.reader.read(65536)
            except (ConnectionResetError, asyncio.IncompleteReadError):
                return
            if chunk == b"":
                return


async def test_start_stop_twice(tcp_available: None) -> None:
    handler = FakeHandler()
    server = make_server(handler)
    await server.start()
    first = server.bound_port
    await server.stop()
    await server.stop()
    await server.start()
    assert server.bound_port != 0
    await server.stop()
    assert first != 0


async def test_double_start_rejected(tcp_available: None) -> None:
    server = make_server(FakeHandler())
    await server.start()
    try:
        with pytest.raises(RuntimeError):
            await server.start()
    finally:
        await server.stop()


async def test_serve_forever_without_start() -> None:
    server = make_server(FakeHandler())
    with pytest.raises(RuntimeError):
        await server.serve_forever()


async def test_port_in_use(tcp_available: None) -> None:
    blocker = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", 0))
    blocker.listen(1)
    port = blocker.getsockname()[1]
    server = make_server(FakeHandler(), port=port)
    try:
        with pytest.raises(OSError):
            await server.start()
    finally:
        blocker.close()


async def test_native_client_gets_burst_and_ready(tcp_available: None) -> None:
    handler = FakeHandler()
    server = make_server(handler)
    await server.start()
    try:
        client = await RawClient.connect(server.bound_port)
        texts = await client.read_until_ready()
        assert [t.decode() for t in texts] == BURST
        await client.close()
    finally:
        await server.stop()
    assert handler.disconnect_calls == 1


async def test_close_then_stop_releases_transport(tcp_available: None) -> None:
    """Stopping right after a client close must return (regression pin).

    The close-frame path used to leave the server transport attached to
    the listener, so ``stop()`` hung in ``wait_closed()`` forever.
    """
    handler = FakeHandler()
    server = make_server(handler)
    await server.start()
    stopped = False
    try:
        client = await RawClient.connect(server.bound_port)
        await client.read_until_ready()
        await client.close()
        await asyncio.wait_for(server.stop(), 10.0)
        stopped = True
    finally:
        if not stopped:
            await server.stop()
    assert handler.disconnect_calls == 1


async def test_browser_origin_refused(tcp_available: None) -> None:
    handler = FakeHandler()
    server = make_server(handler)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
        writer.write(
            b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            b"Origin: http://evil.example\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
        )
        await writer.drain()
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5.0)
        assert head.startswith(b"HTTP/1.1 403 ")
        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()
    assert handler.disconnect_calls == 0


async def test_rebinding_host_refused(tcp_available: None) -> None:
    handler = FakeHandler()
    server = make_server(handler)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
        writer.write(
            b"GET / HTTP/1.1\r\nHost: attacker.example\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
        )
        await writer.drain()
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5.0)
        assert head.startswith(b"HTTP/1.1 403 ")
        writer.close()
        await writer.wait_closed()
    finally:
        await server.stop()
    assert handler.disconnect_calls == 0


async def test_broadcast_reaches_both_sessions(tcp_available: None) -> None:
    handler = FakeHandler()
    server = make_server(handler)
    await server.start()
    try:
        first = await RawClient.connect(server.bound_port)
        second = await RawClient.connect(server.bound_port)
        await first.read_until_ready()
        await second.read_until_ready()
        server.broadcast(["vfo:0,0,14074000;"])
        for client in (first, second):
            opcode, payload = await client.next_frame()
            assert (opcode, payload) == (WS_OP_TEXT, b"vfo:0,0,14074000;")
        await first.send_text("echo:0,7;")
        opcode, payload = await first.next_frame()
        assert (opcode, payload) == (WS_OP_TEXT, b"echo:0,7;")
        opcode, payload = await second.next_frame()
        assert (opcode, payload) == (WS_OP_TEXT, b"echo:0,7;")
        await first.close()
        await second.close()
    finally:
        await server.stop()
    assert handler.disconnect_calls == 2


async def test_binary_both_ways_raw(tcp_available: None) -> None:
    from rigplane.tci.frames import (
        SampleType,
        StreamHeader,
        StreamType,
        pack_header,
    )

    handler = FakeHandler()
    server = make_server(handler)
    await server.start()
    try:
        first = await RawClient.connect(server.bound_port)
        second = await RawClient.connect(server.bound_port)
        await first.read_until_ready()
        await second.read_until_ready()
        payload = struct.pack("<4h", 0, 1000, -1000, 32767)
        header = pack_header(
            StreamHeader(
                receiver=0,
                sample_rate=48000,
                sample_type=SampleType.INT16,
                codec=0,
                crc=0,
                length=4,
                stream_type=StreamType.TX_AUDIO,
                channels=1,
                count=9,
            )
        )
        await first.send_binary(header + payload)
        async with asyncio.timeout(5.0):
            while not handler.binaries:
                await asyncio.sleep(0.005)
        assert handler.binaries == [header + payload]
        server.broadcast_binary(header + payload)
        opcode, received = await second.next_frame()
        assert opcode == WS_OP_BINARY
        assert received == header + payload
        await first.close()
        await second.close()
    finally:
        await server.stop()


async def test_broadcast_then_reply_keep_fifo_order(tcp_available: None) -> None:
    """A reply sent inside the coalescing window never jumps the held broadcast.

    The broadcast is held first, then the client command arrives: B must
    receive the older broadcast line before its own newer reply.
    """
    handler = FakeHandler()
    server = make_server(handler, coalesce_window=0.2)
    await server.start()
    try:
        client = await RawClient.connect(server.bound_port)
        await client.read_until_ready()
        server.broadcast(["vfo:0,0,111;"])
        await client.send_text("echo:0,7;")
        _opcode, first = await client.next_frame()
        _opcode, second = await client.next_frame()
        assert first == b"vfo:0,0,111;"
        assert second == b"echo:0,7;"
        await client.close()
    finally:
        await server.stop()
    assert handler.disconnect_calls == 1


async def test_partial_headers_closed_on_stop(tcp_available: None) -> None:
    """A connection parked mid-upgrade is owned by shutdown.

    The client sends a partial HTTP head and never completes it:
    ``stop()`` must close the pending accept (the client sees EOF),
    never promote a session, and never touch the handler.
    """
    handler = FakeHandler()
    server = make_server(handler)
    await server.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
    try:
        writer.write(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n")
        await writer.drain()
        await asyncio.sleep(0.1)
        await server.stop()
        async with asyncio.timeout(5.0):
            data = await reader.read()
        assert data == b""
        assert server.sessions == ()
        assert handler.disconnect_calls == 0
    finally:
        writer.close()
        await server.stop()


async def test_blocked_on_connect_no_promotion_after_stop(
    tcp_available: None,
) -> None:
    """A gated ``on_connect`` cannot create a session after shutdown.

    ``stop()`` cancels the pending setup; releasing the gate later must
    close the transport (EOF) without running any command, and the
    handler is cleaned up exactly once.
    """
    handler = FakeHandler()
    entered = asyncio.Event()
    gate = asyncio.Event()
    real_on_connect = handler.on_connect

    async def _gated_on_connect() -> list[str]:
        entered.set()
        await gate.wait()
        return await real_on_connect()

    handler.on_connect = _gated_on_connect  # type: ignore[method-assign]
    server = make_server(handler)
    await server.start()
    client: RawClient | None = None
    try:
        client = await RawClient.connect(server.bound_port)
        async with asyncio.timeout(5.0):
            await entered.wait()
        await server.stop()
        gate.set()
        async with asyncio.timeout(5.0):
            data = await client.reader.read()
        assert data == b""
        assert server.sessions == ()
        assert handler.commands == []
        assert handler.disconnect_calls == 1
    finally:
        gate.set()
        if client is not None:
            client.writer.close()
        await server.stop()


async def test_watchdog_expires_silent_session_automatically(
    tcp_available: None,
) -> None:
    """Inbound silence past the deadline aborts the session on its own.

    The test never calls ``check_deadline()``, ``finish()`` or
    ``stop()`` before asserting: the client only observes the dropped
    transport, and the handler records exactly one disconnect. The
    short deadline (a constructor constant, real clock) keeps the run
    fast; the elapsed time proves the firing came from the watchdog.
    """
    handler = FakeHandler()
    server = make_server(handler, deadline=0.3)
    await server.start()
    started = time.monotonic()
    stopped = False
    try:
        client = await RawClient.connect(server.bound_port)
        await client.read_until_ready()
        async with asyncio.timeout(5.0):
            while handler.disconnect_calls == 0:
                await asyncio.sleep(0.005)
        assert time.monotonic() - started < 5.0
        await _read_until_closed(client)
        assert handler.disconnect_calls == 1
        await server.stop()
        stopped = True
    finally:
        if not stopped:
            await server.stop()


async def test_watchdog_fires_despite_concurrent_pings(
    tcp_available: None,
) -> None:
    """Ping writes in flight do not renew the watchdog deadline.

    The server keeps pinging an unread socket while the client stays
    silent: the watchdog still aborts at the deadline on its own (no
    test-side ``check_deadline``/``finish``/``stop`` first). Genuine
    write-block independence is pinned at unit level with a blocked
    drain; this covers the automatic path with heartbeat output busy.
    """
    handler = FakeHandler()
    server = make_server(handler, deadline=0.4, ping_interval=0.05)
    await server.start()
    stopped = False
    try:
        client = await RawClient.connect(server.bound_port)
        await client.read_until_ready()
        async with asyncio.timeout(5.0):
            while handler.disconnect_calls == 0:
                await asyncio.sleep(0.005)
        await _read_until_closed(client)
        assert handler.disconnect_calls == 1
        await server.stop()
        stopped = True
    finally:
        if not stopped:
            await server.stop()


async def test_reset_releases_session(tcp_available: None) -> None:
    handler = FakeHandler()
    server = make_server(handler)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
        writer.write(
            b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            b"Upgrade: websocket\r\n"
            b"Connection: Upgrade\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n"
            b"Sec-WebSocket-Version: 13\r\n\r\n"
        )
        await writer.drain()
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5.0)
        assert head.startswith(b"HTTP/1.1 101 ")
        writer.transport.abort()
        await writer.wait_closed()
        async with asyncio.timeout(5.0):
            while handler.disconnect_calls == 0:
                await asyncio.sleep(0.005)
    finally:
        await server.stop()
    assert handler.disconnect_calls == 1


async def test_server_stop_releases_open_session(tcp_available: None) -> None:
    handler = FakeHandler()
    server = make_server(handler)
    await server.start()
    client = await RawClient.connect(server.bound_port)
    await client.read_until_ready()
    await server.stop()
    assert handler.disconnect_calls == 1
    client.writer.close()
    await client.writer.wait_closed()


async def test_cancellation_during_accept(tcp_available: None) -> None:
    handler = FakeHandler()
    server = make_server(handler)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())
    await asyncio.sleep(0.1)
    client = await RawClient.connect(server.bound_port)
    await client.read_until_ready()
    serve_task.cancel()
    # Stop before awaiting the cancelled serve task: the cancel path
    # inside ``serve_forever`` waits for open sessions to detach, so the
    # session has to be finished first (otherwise only the 15 s watchdog
    # releases it).
    await server.stop()
    try:
        await serve_task
    except asyncio.CancelledError:
        pass
    assert handler.disconnect_calls == 1
    client.writer.close()
    await client.writer.wait_closed()


async def _send_fragment(
    writer: asyncio.StreamWriter, opcode: int, payload: bytes, *, fin: bool
) -> None:
    """Write one masked client fragment with an explicit FIN bit."""
    mask = b"\x55\x66\x77\x88"
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    first = (0x80 if fin else 0x00) | (opcode & 0x0F)
    length = len(payload)
    if length <= 125:
        header = struct.pack("!BB", first, 0x80 | length)
    elif length <= 65535:
        header = struct.pack("!BBH", first, 0x80 | 126, length)
    else:
        header = struct.pack("!BBQ", first, 0x80 | 127, length)
    writer.write(header + mask + masked)
    await writer.drain()


async def test_fragmented_bytes_overflow_bounded(
    tcp_available: None,
) -> None:
    """A message whose fragments sum past the budget is refused with 1009.

    Every frame is individually valid; only the running total exceeds
    the budget. The session answers close 1009, disconnects exactly
    once, leaves the registry, and another client is served meanwhile.
    """
    handler = FakeHandler()
    server = make_server(handler)
    await server.start()
    try:
        client = await RawClient.connect(server.bound_port)
        await client.read_until_ready()
        assert len(server.sessions) == 1
        assert 5 * 60000 > _MAX_MESSAGE_BYTES
        chunk = b"y" * 60000
        await _send_fragment(client.writer, WS_OP_BINARY, chunk, fin=False)
        for _ in range(4):
            await _send_fragment(client.writer, WS_OP_CONTINUATION, chunk, fin=False)
        opcode, payload = await client.next_frame()
        assert opcode == WS_OP_CLOSE
        assert struct.unpack("!H", payload[:2])[0] == 1009
        await _read_until_closed(client)
        async with asyncio.timeout(5.0):
            while handler.disconnect_calls == 0:
                await asyncio.sleep(0.005)
        assert handler.disconnect_calls == 1
        assert len(server.sessions) == 0
        other = await RawClient.connect(server.bound_port)
        texts = await other.read_until_ready()
        assert [t.decode() for t in texts] == BURST
        await other.send_text("echo:1;")
        opcode, payload = await other.next_frame()
        assert (opcode, payload) == (WS_OP_TEXT, b"echo:1;")
        await other.close()
    finally:
        await server.stop()
    assert handler.disconnect_calls == 2


async def test_fragment_count_overflow_bounded(
    tcp_available: None,
) -> None:
    """More fragments than budgeted are refused with 1009, even when empty.

    Empty continuations carry no bytes but each occupies a slot; the
    count limit stops the list from growing without bound. Same
    teardown and uninterrupted-service assertions as the byte limit.
    """
    handler = FakeHandler()
    server = make_server(handler)
    await server.start()
    try:
        client = await RawClient.connect(server.bound_port)
        await client.read_until_ready()
        assert len(server.sessions) == 1
        await _send_fragment(client.writer, WS_OP_BINARY, b"", fin=False)
        for _ in range(_MAX_FRAGMENTS):
            await _send_fragment(client.writer, WS_OP_CONTINUATION, b"", fin=False)
        opcode, payload = await client.next_frame()
        assert opcode == WS_OP_CLOSE
        assert struct.unpack("!H", payload[:2])[0] == 1009
        await _read_until_closed(client)
        async with asyncio.timeout(5.0):
            while handler.disconnect_calls == 0:
                await asyncio.sleep(0.005)
        assert handler.disconnect_calls == 1
        assert len(server.sessions) == 0
        other = await RawClient.connect(server.bound_port)
        texts = await other.read_until_ready()
        assert [t.decode() for t in texts] == BURST
        await other.close()
    finally:
        await server.stop()
    assert handler.disconnect_calls == 2


async def test_cancelled_join_still_closes_transport(
    tcp_available: None,
) -> None:
    """Cancelling ``finish()`` mid-join still closes the transport.

    One session task gates its cancellation cleanup so the task join
    (``gather``) stays open long enough for the cancel to land inside
    it — before the close/abort. The transport must still die (the
    client observes EOF), the disconnect runs exactly once, the
    session leaves the registry, and a repeated ``finish()`` is
    harmless.
    """
    handler = FakeHandler()
    server = make_server(handler)
    await server.start()
    try:
        client = await RawClient.connect(server.bound_port)
        await client.read_until_ready()
        assert len(server.sessions) == 1
        session = server.sessions[0]

        async def _slow_cleanup() -> None:
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                await asyncio.sleep(0.3)
                raise

        slow = asyncio.create_task(_slow_cleanup())
        session._tasks.add(slow)
        finishing = asyncio.create_task(session.finish())
        try:
            async with asyncio.timeout(5.0):
                while not session._done:
                    await asyncio.sleep(0.005)
            await asyncio.sleep(0.05)
            finishing.cancel()
            try:
                async with asyncio.timeout(10.0):
                    await finishing
            except asyncio.CancelledError:
                pass
            await _read_until_closed(client)
            assert handler.disconnect_calls == 1
            assert session not in server.sessions
            await session.finish()
            assert handler.disconnect_calls == 1
        finally:
            slow.cancel()
            try:
                await slow
            except (asyncio.CancelledError, Exception):
                pass
    finally:
        await server.stop()


async def test_gated_handlers_survive_while_client_pongs(
    tcp_available: None,
) -> None:
    """Gated text and binary handlers do not starve liveness on the wire.

    The client answers every server ping while both handlers are
    parked past several watchdog deadlines: the session must survive
    (no disconnect, still registered) and both operations must
    complete in order once the gates open.
    """
    from rigplane.tci.handler import CommandOutcome

    gate_text = asyncio.Event()
    entered_text = asyncio.Event()
    gate_binary = asyncio.Event()
    entered_binary = asyncio.Event()

    class _GatedHandler(FakeHandler):
        async def on_command(self, name: str, args: tuple[str, ...]) -> object:
            if name == "gated":
                self.commands.append((name, args))
                entered_text.set()
                await gate_text.wait()
                return CommandOutcome(to_sender=["done:1;"])
            return await super().on_command(name, args)

        async def on_binary(self, payload: bytes) -> None:
            entered_binary.set()
            await gate_binary.wait()
            await super().on_binary(payload)

    handler = _GatedHandler()
    server = make_server(handler, deadline=0.4, ping_interval=0.05)
    await server.start()
    pump: asyncio.Task[None] | None = None
    pump_stop = asyncio.Event()
    try:
        client = await RawClient.connect(server.bound_port)

        async def _answer_pings() -> None:
            try:
                while not pump_stop.is_set():
                    try:
                        opcode, payload = await client.next_frame(timeout=1.0)
                    except TimeoutError:
                        continue
                    if opcode == WS_OP_PING:
                        await client._send(WS_OP_PONG, payload)
            except (
                asyncio.CancelledError,
                asyncio.IncompleteReadError,
                ConnectionResetError,
            ):
                pass

        pump = asyncio.create_task(_answer_pings())

        def _texts() -> list[bytes]:
            return [p for op, p in client.received if op == WS_OP_TEXT]

        async with asyncio.timeout(5.0):
            while b"ready;" not in _texts():
                await asyncio.sleep(0.005)
        await client.send_text("gated:1;")
        await client.send_binary(b"\x01\x02")
        async with asyncio.timeout(5.0):
            await entered_text.wait()
        # Several deadlines pass with the command parked; the answered
        # pongs must keep the session alive.
        await asyncio.sleep(1.2)
        assert handler.disconnect_calls == 0
        assert len(server.sessions) == 1
        gate_text.set()
        # The binary handler runs only after the text one completes
        # (ordered dispatch); the session must still be alive then.
        async with asyncio.timeout(5.0):
            await entered_binary.wait()
        assert handler.disconnect_calls == 0
        gate_binary.set()
        async with asyncio.timeout(5.0):
            while b"done:1;" not in _texts() or not handler.binaries:
                await asyncio.sleep(0.005)
        assert handler.binaries == [b"\x01\x02"]
        assert handler.disconnect_calls == 0
        pump_stop.set()
        await client.close()
    finally:
        gate_text.set()
        gate_binary.set()
        pump_stop.set()
        if pump is not None:
            pump.cancel()
            try:
                await pump
            except (asyncio.CancelledError, Exception):
                pass
        await server.stop()
    assert handler.disconnect_calls == 1


async def test_gated_handler_silent_client_still_expires(
    tcp_available: None,
) -> None:
    """A gated handler does not keep a silent session alive.

    The client sends one command, then stops answering pings while the
    handler is parked: the watchdog must still expire the session at
    the deadline (disconnect exactly once, transport closed). Pin: the
    liveness split must not pin sessions open forever.
    """
    from rigplane.tci.handler import CommandOutcome

    gate = asyncio.Event()
    entered = asyncio.Event()

    class _GatedHandler(FakeHandler):
        async def on_command(self, name: str, args: tuple[str, ...]) -> object:
            self.commands.append((name, args))
            entered.set()
            await gate.wait()
            return CommandOutcome()

    handler = _GatedHandler()
    server = make_server(handler, deadline=0.3)
    await server.start()
    stopped = False
    try:
        client = await RawClient.connect(server.bound_port)
        await client.read_until_ready()
        await client.send_text("gated:1;")
        async with asyncio.timeout(5.0):
            await entered.wait()
        async with asyncio.timeout(5.0):
            while handler.disconnect_calls == 0:
                await asyncio.sleep(0.005)
        await _read_until_closed(client)
        assert handler.disconnect_calls == 1
        gate.set()
        await server.stop()
        stopped = True
    finally:
        gate.set()
        if not stopped:
            await server.stop()
    assert handler.disconnect_calls == 1
