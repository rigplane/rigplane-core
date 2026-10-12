"""IcomTransport.disconnect must not return before the UDP socket is closed.

After a radio power-cycle the CI-V rebuild path disconnects one transport and
immediately binds the same advertised local port on a fresh one.
asyncio's ``DatagramTransport.close()`` defers the real socket close to a
``call_soon`` callback, so a ``disconnect()`` that returns without yielding
leaves the port bound and the follow-up bind fails with EADDRINUSE.
"""

from __future__ import annotations

import asyncio
import socket
import struct
from collections.abc import AsyncGenerator

import pytest

from rigplane.core.types import PacketType
from rigplane.transport import CONTROL_SIZE, IcomTransport

_RADIO_ID = 0xDEADBEEF


class _DiscoveryEcho(asyncio.DatagramProtocol):
    """Loopback stand-in answering the transport's discovery handshake."""

    def __init__(self) -> None:
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if len(data) < CONTROL_SIZE or self.transport is None:
            return
        ptype = struct.unpack_from("<H", data, 4)[0]
        sender_id = struct.unpack_from("<I", data, 8)[0]
        if ptype not in (PacketType.ARE_YOU_THERE, PacketType.ARE_YOU_READY):
            return
        reply = bytearray(CONTROL_SIZE)
        struct.pack_into("<I", reply, 0, CONTROL_SIZE)
        struct.pack_into(
            "<H",
            reply,
            4,
            (
                PacketType.I_AM_HERE
                if ptype == PacketType.ARE_YOU_THERE
                else PacketType.ARE_YOU_READY
            ),
        )
        struct.pack_into("<I", reply, 8, _RADIO_ID)
        struct.pack_into("<I", reply, 0x0C, sender_id)
        self.transport.sendto(bytes(reply), addr)


@pytest.fixture
async def echo_port() -> AsyncGenerator[int]:
    loop = asyncio.get_running_loop()
    transport, _protocol = await loop.create_datagram_endpoint(
        _DiscoveryEcho, local_addr=("127.0.0.1", 0)
    )
    yield transport.get_extra_info("sockname")[1]
    transport.close()


def _free_local_port() -> int:
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    probe.bind(("127.0.0.1", 0))
    port = probe.getsockname()[1]
    probe.close()
    return port


async def test_disconnect_releases_the_local_udp_port_before_returning(
    echo_port: int,
) -> None:
    """A reconnecting bind of the same local port must succeed immediately
    after ``disconnect()`` returns, with no intervening await."""
    local_port = _free_local_port()

    t1 = IcomTransport()
    t2 = IcomTransport()
    try:
        await t1.connect("127.0.0.1", echo_port, local_port=local_port)
        assert t1._udp_transport is not None
        assert t1.remote_id == _RADIO_ID
        await t1.disconnect()
        await t2.connect("127.0.0.1", echo_port, local_port=local_port)
        assert t2._udp_transport is not None
    finally:
        await t1.disconnect()
        await t2.disconnect()
