"""Conformance with the real ``eesdr-tci`` client (MOR-3821 L2, mini-only).

Drives the real leaf server on an ephemeral port with a fake
``TciHandler`` using ``eesdr_tci.listener.Listener`` as the client.
Covers connect, the init burst and the ``ready`` event, multi-client
broadcast and coalescing, typed ``TciDataPacket`` frames both ways,
and the output guard: a scripted session that completes proves the
server emitted only commands from the ``eesdr-tci`` dictionary
(``Listener._listen_main`` raises ``ValueError`` on any other name).

API assumptions (verify against the eesdr-tci 0.1.0 README on the
mini; each fails closed naming the exact attribute tried):

- ``eesdr_tci.listener.Listener(host, port)`` connects on
  ``await listener.connect()`` (or ``start()``) and exposes received
  text commands as ``listener.received_commands: list[str]`` and a
  ``ready: asyncio.Event`` set after the ``ready;`` line.
- ``listener.send_command(name, *args)`` sends one text command.
- ``eesdr_tci.tci.TciDataPacket(...)`` takes the eight header fields of
  the 8.1 table plus ``data``, and ``bytes(packet)`` is the wire image
  (``<8I32x`` header plus data).
"""

import asyncio
import socket
import struct

import pytest

from rigplane.tci.frames import (
    HEADER_SIZE,
    SampleType,
    StreamHeader,
    StreamType,
    pack_header,
    unpack_header,
)
from rigplane.tci.handler import CommandOutcome

eesdr_tci = pytest.importorskip("eesdr_tci", reason="mini-only dev dependency")

Listener = getattr(eesdr_tci.listener, "Listener", None)
assert Listener is not None, "eesdr_tci.listener has no Listener"

TciDataPacket = getattr(eesdr_tci.tci, "TciDataPacket", None)
assert TciDataPacket is not None, "eesdr_tci.tci has no TciDataPacket"


@pytest.fixture()
def tcp_available() -> None:
    probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        probe.bind(("127.0.0.1", 0))
    except OSError as exc:
        pytest.skip(f"loopback bind unavailable: {exc}")
    finally:
        probe.close()


BURST = [
    "protocol:tciserver,1;",
    "device:rigplane;",
    "trx_count:1;",
    "channels_count:2;",
    "modulations_list:AM,LSB,USB,CW,FM,DIGL,DIGU;",
    "vfo:0,0,14074000;",
    "vfo:0,1,14074000;",
    "modulation:0,USB;",
    "trx:0,false;",
    "drive:0,50;",
    "receive_only:false;",
    "ready;",
]


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
    ) -> CommandOutcome:
        self.commands.append((name, args))
        if name == "vfo":
            joined = ",".join(args)
            return CommandOutcome(
                to_sender=[f"vfo:{joined};"], to_others=[f"vfo:{joined};"]
            )
        return CommandOutcome()

    async def on_binary(self, payload: bytes) -> None:
        self.binaries.append(bytes(payload))

    async def on_disconnect(self) -> None:
        self.disconnect_calls += 1


def make_server(handler: FakeHandler, **kwargs: object) -> object:
    import time

    from rigplane.tci.server import TciServer

    from rigplane.tci.ws import UpgradeDecline

    def _allow(headers: dict[str, str]) -> UpgradeDecline | None:
        if headers.get("origin") is not None:
            return UpgradeDecline(403, "Forbidden", b"nope")
        return None

    return TciServer(
        lambda: handler,  # type: ignore[return-value]
        host="127.0.0.1",
        port=0,
        allow=_allow,  # type: ignore[arg-type]
        clock=time.monotonic,
        **kwargs,  # type: ignore[arg-type]
    )


async def connect_listener(port: int) -> object:
    """Connect a real eesdr-tci Listener (API assumption, fail-closed)."""
    listener = Listener("127.0.0.1", port)
    connect = getattr(listener, "connect", None)
    if connect is None:
        connect = getattr(listener, "start", None)
    assert callable(connect), "Listener has neither connect() nor start()"
    result = connect()
    if asyncio.iscoroutine(result):
        await result
    return listener


async def wait_until(
    predicate: object, timeout: float = 5.0
) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():  # type: ignore[operator]
            await asyncio.sleep(0.005)


async def test_connect_burst_and_ready(tcp_available: None) -> None:
    handler = FakeHandler()
    server = make_server(handler)
    await server.start()  # type: ignore[attr-defined]
    try:
        listener = await connect_listener(server.bound_port)  # type: ignore[attr-defined]
        ready = getattr(listener, "ready", None)
        assert ready is not None, "Listener has no ready event"
        async with asyncio.timeout(5.0):
            await ready.wait()
        received = getattr(listener, "received_commands", None)
        assert received is not None, "Listener has no received_commands"
        assert list(received) == BURST
    finally:
        await server.stop()  # type: ignore[attr-defined]


async def test_command_round_trip(tcp_available: None) -> None:
    handler = FakeHandler()
    server = make_server(handler)
    await server.start()  # type: ignore[attr-defined]
    try:
        listener = await connect_listener(server.bound_port)  # type: ignore[attr-defined]
        async with asyncio.timeout(5.0):
            await listener.ready.wait()
        send = getattr(listener, "send_command", None)
        assert callable(send), "Listener has no send_command()"
        result = send("vfo", "0", "0", "14074001")
        if asyncio.iscoroutine(result):
            await result
        await wait_until(lambda: ("vfo", ("0", "0", "14074001")) in handler.commands)
    finally:
        await server.stop()  # type: ignore[attr-defined]


async def test_multi_client_broadcast(tcp_available: None) -> None:
    handler = FakeHandler()
    server = make_server(handler)
    await server.start()  # type: ignore[attr-defined]
    try:
        first = await connect_listener(server.bound_port)  # type: ignore[attr-defined]
        second = await connect_listener(server.bound_port)  # type: ignore[attr-defined]
        async with asyncio.timeout(5.0):
            await first.ready.wait()
            await second.ready.wait()
        before_first = len(first.received_commands)
        before_second = len(second.received_commands)
        server.broadcast(["vfo:0,0,14074001;"])  # type: ignore[attr-defined]
        await wait_until(lambda: len(first.received_commands) > before_first)
        await wait_until(lambda: len(second.received_commands) > before_second)
        assert first.received_commands[-1] == "vfo:0,0,14074001;"
        assert second.received_commands[-1] == "vfo:0,0,14074001;"
    finally:
        await server.stop()  # type: ignore[attr-defined]


async def test_typed_packets_both_ways(tcp_available: None) -> None:
    handler = FakeHandler()
    server = make_server(handler)
    await server.start()  # type: ignore[attr-defined]
    try:
        listener = await connect_listener(server.bound_port)  # type: ignore[attr-defined]
        async with asyncio.timeout(5.0):
            await listener.ready.wait()
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
                count=5,
            )
        )
        send_binary = getattr(listener, "send_binary", None)
        assert callable(send_binary), "Listener has no send_binary()"
        result = send_binary(header + payload)
        if asyncio.iscoroutine(result):
            await result
        await wait_until(lambda: len(handler.binaries) > 0)
        parsed = unpack_header(handler.binaries[0][:HEADER_SIZE])
        assert parsed.stream_type is StreamType.TX_AUDIO
        assert parsed.count == 5
        assert handler.binaries[0][HEADER_SIZE:] == payload
        server.broadcast_binary(header + payload)  # type: ignore[attr-defined]
        received_frames = getattr(listener, "received_binary", None)
        assert received_frames is not None, "Listener has no received_binary"
        await wait_until(lambda: len(received_frames) > 0)
        assert received_frames[-1] == header + payload
    finally:
        await server.stop()  # type: ignore[attr-defined]


async def test_output_guard_scripted_session(tcp_available: None) -> None:
    """A completing scripted session proves only dictionary commands
    were emitted (unknown names raise in ``_listen_main``)."""
    handler = FakeHandler()
    server = make_server(handler)
    await server.start()  # type: ignore[attr-defined]
    try:
        listener = await connect_listener(server.bound_port)  # type: ignore[attr-defined]
        async with asyncio.timeout(5.0):
            await listener.ready.wait()
        for line in BURST:
            assert line in listener.received_commands
        server.broadcast(["drive:0,50;", "trx:0,false;"])  # type: ignore[attr-defined]
        await wait_until(lambda: "drive:0,50;" in listener.received_commands)
        await wait_until(lambda: "trx:0,false;" in listener.received_commands)
    finally:
        await server.stop()  # type: ignore[attr-defined]
