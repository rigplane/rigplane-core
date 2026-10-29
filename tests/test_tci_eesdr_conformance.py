"""Conformance with the real ``eesdr-tci`` client (MOR-3821 L2, mini-only).

Drives the real leaf server on an ephemeral port with a fake
``TciHandler`` using ``eesdr_tci.listener.Listener`` as the client.
Covers connect, the init burst and the ``ready`` event, multi-client
broadcast, typed ``TciDataPacket`` frames both ways, and the output
guard: an unknown command name raises ``ValueError`` in
``Listener._listen_main``.

Verified real API of ``eesdr-tci`` 0.1.0 (read from the installed
source on the Mac mini; every attribute below fails closed at
collection when it changes):

- ``Listener(uri)`` takes a ``ws://`` URI string (not host/port).
- ``await listener.start()`` connects (raises ``TimeoutError`` when the
  server never answers); ``await listener.ready()`` waits for the
  ``ready;`` line; ``await listener.wait()`` re-raises whatever killed
  the connection task (e.g. the ``ValueError`` for unknown commands).
- ``await listener.send(data)`` / ``listener.send_nowait(data)`` pass a
  text or binary message through as-is (no ``send_command`` helper).
- ``listener.add_param_listener("*", cb)`` with
  ``async def cb(name, rx, sub_rx, params)`` observes text commands
  (callbacks must be coroutines: each is wrapped in a task).
- ``listener.add_data_listener("*", cb)`` with
  ``async def cb(packet)`` observes ``TciDataPacket`` frames.
- ``listener.shutdown()`` cancels the connection task (it swallows the
  resulting ``CancelledError`` itself, so ``wait()`` then returns).
- ``TciDataPacket(rx, sample_rate, data_format, codec, crc, length,
  data_type, channels, data)`` with ``to_bytes()`` / ``from_buf()``;
  0.1.0 predates the 2.1 ``count`` header field (its wire image is
  ``<8I32x`` with a zeroed tail).
"""

import asyncio
import importlib
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

pytest.importorskip("eesdr_tci", reason="mini-only dev dependency")
listener_mod = importlib.import_module("eesdr_tci.listener")
tci_mod = importlib.import_module("eesdr_tci.tci")

Listener = listener_mod.Listener
TciDataPacket = tci_mod.TciDataPacket


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

#: What the real listener reports for BURST, in order: ``eesdr-tci``
#: uppercases names, converts scalars (ints, bools) and unwraps
#: single params, leaving multi-param lists as lists.
EXPECTED_OBSERVED = [
    ("PROTOCOL", None, None, ["tciserver", 1]),
    ("DEVICE", None, None, "rigplane"),
    ("TRX_COUNT", None, None, 1),
    ("CHANNELS_COUNT", None, None, 2),
    ("MODULATIONS_LIST", None, None, ["AM", "LSB", "USB", "CW", "FM", "DIGL", "DIGU"]),
    ("VFO", 0, 0, 14074000),
    ("VFO", 0, 1, 14074000),
    ("MODULATION", 0, None, "USB"),
    ("TRX", 0, None, False),
    ("DRIVE", 0, None, 50),
    ("RECEIVE_ONLY", None, None, False),
    ("READY", None, None, None),
]


class FakeHandler:
    def __init__(self) -> None:
        self.burst = list(BURST)
        self.commands: list[tuple[str, tuple[str, ...]]] = []
        self.binaries: list[bytes] = []
        self.disconnect_calls = 0

    async def on_connect(self) -> list[str]:
        return list(self.burst)

    async def on_command(self, name: str, args: tuple[str, ...]) -> CommandOutcome:
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


class Observed:
    """Collects real-listener param and data callbacks."""

    def __init__(self) -> None:
        self.params: list[tuple[object, object, object, object]] = []
        self.packets: list[object] = []

    async def on_param(
        self, name: object, rx: object, sub_rx: object, params: object
    ) -> None:
        self.params.append((name, rx, sub_rx, params))

    async def on_data(self, packet: object) -> None:
        self.packets.append(packet)


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


async def connect_listener(port: int, observed: Observed) -> object:
    """Connect a real eesdr-tci Listener and wait for the init burst."""
    listener = Listener(f"ws://127.0.0.1:{port}")
    listener.add_param_listener("*", observed.on_param)
    listener.add_data_listener("*", observed.on_data)
    await listener.start()
    await listener.ready()
    return listener


async def close_listener(listener: object) -> None:
    """Shut down a real Listener; cancel strays eesdr-tci leaves behind.

    ``shutdown()`` cancels the connection task but does not await the
    sender task it orphaned, so sweep up anything still pending (the
    server is already stopped by the time this runs).
    """
    listener.shutdown()  # type: ignore[attr-defined]
    try:
        async with asyncio.timeout(5.0):
            await listener.wait()  # type: ignore[attr-defined]
    except asyncio.CancelledError:
        pass
    except Exception:
        # The connection task may already have ended with the server's
        # close reply (ConnectionClosedOK) winning the race against the
        # shutdown cancel; teardown does not care which won.
        pass
    await asyncio.sleep(0)
    current = asyncio.current_task()
    stray = [
        task for task in asyncio.all_tasks() if task is not current and not task.done()
    ]
    for task in stray:
        task.cancel()
    if stray:
        await asyncio.gather(*stray, return_exceptions=True)


async def wait_until(predicate: object, timeout: float = 5.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():  # type: ignore[operator]
            await asyncio.sleep(0.005)


async def test_connect_burst_and_ready(tcp_available: None) -> None:
    handler = FakeHandler()
    observed = Observed()
    server = make_server(handler)
    await server.start()  # type: ignore[attr-defined]
    try:
        listener = await connect_listener(server.bound_port, observed)  # type: ignore[attr-defined]
        try:
            await wait_until(lambda: len(observed.params) >= len(EXPECTED_OBSERVED))
            assert observed.params == EXPECTED_OBSERVED
        finally:
            await close_listener(listener)
    finally:
        await server.stop()  # type: ignore[attr-defined]


async def test_command_round_trip(tcp_available: None) -> None:
    handler = FakeHandler()
    observed = Observed()
    server = make_server(handler)
    await server.start()  # type: ignore[attr-defined]
    try:
        listener = await connect_listener(server.bound_port, observed)  # type: ignore[attr-defined]
        try:
            await listener.send("vfo:0,0,14074001;")  # type: ignore[attr-defined]
            await wait_until(
                lambda: ("vfo", ("0", "0", "14074001")) in handler.commands
            )
        finally:
            await close_listener(listener)
    finally:
        await server.stop()  # type: ignore[attr-defined]


async def test_multi_client_broadcast(tcp_available: None) -> None:
    handler = FakeHandler()
    first_seen = Observed()
    second_seen = Observed()
    server = make_server(handler)
    await server.start()  # type: ignore[attr-defined]
    try:
        first = await connect_listener(server.bound_port, first_seen)  # type: ignore[attr-defined]
        second = await connect_listener(server.bound_port, second_seen)  # type: ignore[attr-defined]
        try:
            server.broadcast(["vfo:0,0,14074001;"])  # type: ignore[attr-defined]
            await wait_until(
                lambda: (
                    ("VFO", 0, 0, 14074001) in first_seen.params
                    and ("VFO", 0, 0, 14074001) in second_seen.params
                )
            )
        finally:
            await close_listener(first)
            await close_listener(second)
    finally:
        await server.stop()  # type: ignore[attr-defined]


async def test_typed_packets_both_ways(tcp_available: None) -> None:
    handler = FakeHandler()
    observed = Observed()
    server = make_server(handler)
    await server.start()  # type: ignore[attr-defined]
    try:
        listener = await connect_listener(server.bound_port, observed)  # type: ignore[attr-defined]
        try:
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
            await listener.send(header + payload)  # type: ignore[attr-defined]
            await wait_until(lambda: len(handler.binaries) > 0)
            parsed = unpack_header(handler.binaries[0][:HEADER_SIZE])
            assert parsed.stream_type is StreamType.TX_AUDIO
            assert parsed.count == 5
            assert handler.binaries[0][HEADER_SIZE:] == payload
            server.broadcast_binary(header + payload)  # type: ignore[attr-defined]
            await wait_until(lambda: len(observed.packets) > 0)
            packet = observed.packets[-1]
            assert packet.rx == 0  # type: ignore[attr-defined]
            assert packet.sample_rate == 48000  # type: ignore[attr-defined]
            assert int(packet.data_format) == int(SampleType.INT16)  # type: ignore[attr-defined]
            assert packet.length == 4  # type: ignore[attr-defined]
            assert int(packet.data_type) == int(StreamType.TX_AUDIO)  # type: ignore[attr-defined]
            assert packet.channels == 1  # type: ignore[attr-defined]
            assert packet.data == payload  # type: ignore[attr-defined]
        finally:
            await close_listener(listener)
    finally:
        await server.stop()  # type: ignore[attr-defined]


async def test_output_guard_unknown_command_kills_listener(tcp_available: None) -> None:
    """An unknown command name raises ``ValueError`` in ``_listen_main``.

    A scripted session that only ever emits dictionary commands
    completes (see above); anything else kills the real client, which
    is the guard the leaf relies on.
    """
    handler = FakeHandler()
    observed = Observed()
    server = make_server(handler)
    await server.start()  # type: ignore[attr-defined]
    try:
        listener = await connect_listener(server.bound_port, observed)  # type: ignore[attr-defined]
        try:
            server.broadcast(["frobnicate:1;"])  # type: ignore[attr-defined]
            with pytest.raises(ValueError, match="unrecognized"):
                async with asyncio.timeout(5.0):
                    await listener.wait()  # type: ignore[attr-defined]
        finally:
            await close_listener(listener)
    finally:
        await server.stop()  # type: ignore[attr-defined]
