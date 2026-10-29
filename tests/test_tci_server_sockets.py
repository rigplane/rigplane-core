"""Real-socket leaf server tests (MOR-3821 L2, mini-only in practice).

Raw asyncio client over loopback TCP: listener lifecycle, HTTP upgrade
rejections, multi-client broadcast and coalescing, binary both ways,
close/reset/stop teardown, cancellation during accept. Skipped where
loopback bind is unavailable (sandbox); the mini runs the whole file.
"""

import asyncio
import socket
import struct

import pytest

from rigplane.tci.server import TciServer
from rigplane.tci.ws import WS_OP_BINARY, WS_OP_CLOSE, WS_OP_TEXT, UpgradeDecline

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

    async def on_command(
        self, name: str, args: tuple[str, ...]
    ) -> object:
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


def make_server(handler: FakeHandler, **kwargs: object) -> TciServer:
    import time

    return TciServer(
        lambda: handler,  # type: ignore[return-value]
        host="127.0.0.1",
        port=0,
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
        lines = ["GET / HTTP/1.1", "Host: 127.0.0.1", "Connection: Upgrade",
                 "Upgrade: websocket",
                 "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==",
                 "Sec-WebSocket-Version: 13"]
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
        self.writer.write(struct.pack("!BB", 0x88, 0x80 | 2) + b"\x00" * 4 + b"\x03\xe8")
        await self.writer.drain()
        self.writer.close()


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


async def test_reset_releases_session(tcp_available: None) -> None:
    handler = FakeHandler()
    server = make_server(handler)
    await server.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", server.bound_port)
        writer.write(
            b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n"
            b"Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n"
        )
        await writer.drain()
        head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 5.0)
        assert head.startswith(b"HTTP/1.1 101 ")
        writer.transport.abort()
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


async def test_cancellation_during_accept(tcp_available: None) -> None:
    handler = FakeHandler()
    server = make_server(handler)
    await server.start()
    serve_task = asyncio.create_task(server.serve_forever())
    await asyncio.sleep(0.1)
    client = await RawClient.connect(server.bound_port)
    await client.read_until_ready()
    serve_task.cancel()
    try:
        await serve_task
    except asyncio.CancelledError:
        pass
    await server.stop()
    assert handler.disconnect_calls == 1
    client.writer.close()
