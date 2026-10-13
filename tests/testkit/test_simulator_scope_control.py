"""Wire-level tests: the simulator's CI-V 0x27 scope control vocabulary (scope_control window).

A bare asyncio datagram client speaks the CI-V wire format against a real
``MockIcomRadio`` on loopback UDP — no production code involved — pinning the
recorded scope control forms from the ``scope_control`` window of
``tests/fixtures/ic7610/civ_pairs.jsonl``.

Only subcommands 0x10, 0x11, 0x12, 0x13 are in this slice per scope_control
recordings. Other forms fall through to NAK via existing simulator fallback.

Recorded authoritative forms (scope_control window):
- 27 10 bare read → 27 10 01
- 27 11 bare read → 27 11 00
- 27 12 bare read → 27 12 00
- 27 13 bare read → 27 13 01
- 27 10 01 SET → FB (ACK)
- 27 11 01 SET → FB (ACK)
- 27 11 00 SET → FB (ACK)
- 27 12 00 SET → FB (ACK)
- 27 12 01 SET → FB (ACK)
- 27 13 00 SET → FB (ACK)
- 27 13 01 SET → FB (ACK)

SET state must be reflected by subsequent bare read.
Unrecorded payloads (e.g., 27 10 00) → NAK.
"""

import asyncio
import struct

import pytest

from rigplane_testkit.icom import MockIcomRadio

from testkit.test_simulator_faults import _recv_reply

_HOST = "127.0.0.1"
_CLIENT_ID = 0x11223344
_PT_DATA = 0x00

_RADIO_ADDR = 0x98
_CONTROLLER_ADDR = 0xE0


# ---------------------------------------------------------------------------
# Plain UDP client (same shape as test_simulator_faults.py)
# ---------------------------------------------------------------------------


class _UdpClient(asyncio.DatagramProtocol):
    """Bare datagram client: everything received lands in ``queue``."""

    def __init__(self) -> None:
        self.queue: asyncio.Queue[bytes] = asyncio.Queue()
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport) -> None:  # type: ignore[override]
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        self.queue.put_nowait(data)

    def send(self, data: bytes, port: int) -> None:
        assert self.transport is not None
        self.transport.sendto(data, (_HOST, port))

    async def recv(self, timeout: float = 2.0) -> bytes:
        return await asyncio.wait_for(self.queue.get(), timeout)

    def close(self) -> None:
        if self.transport is not None:
            self.transport.close()
            self.transport = None


# ---------------------------------------------------------------------------
# Wire builders / parsers (client side)
# ---------------------------------------------------------------------------


def _header(size: int, ptype: int, seq: int = 0) -> bytearray:
    pkt = bytearray(size)
    struct.pack_into("<I", pkt, 0x00, size)
    struct.pack_into("<H", pkt, 0x04, ptype)
    struct.pack_into("<H", pkt, 0x06, seq)
    struct.pack_into("<I", pkt, 0x08, _CLIENT_ID)
    return pkt


def _frame(cmd: int, data: bytes) -> bytes:
    """CI-V frame addressed to the radio."""
    return b"\xfe\xfe" + bytes([_RADIO_ADDR, _CONTROLLER_ADDR, cmd]) + data + b"\xfd"


def _civ_packet(frame: bytes, seq: int = 1) -> bytes:
    """Wrap a CI-V frame in a DATA header for the civ port."""
    pkt = _header(0x15 + len(frame), _PT_DATA, seq=seq)
    pkt[0x10] = 0xC1
    struct.pack_into("<H", pkt, 0x11, len(frame))
    pkt[0x15:] = frame
    return bytes(pkt)


async def _send_civ(client: _UdpClient, port: int, frame: bytes, seq: int) -> bytes:
    """Send one CI-V frame, return the CI-V frame of the reply.

    The shared ``_recv_reply`` skips the simulator's LAN CI-V echo (the
    request packet back byte-identical), so this resolves to the reply.
    """
    request = _civ_packet(frame, seq=seq)
    client.send(request, port)
    reply = await _recv_reply(client, request)
    assert reply[0x15:0x17] == b"\xfe\xfe"
    return reply[0x15:]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def server():
    s = MockIcomRadio()
    await s.start()
    yield s
    await s.stop()


@pytest.fixture
async def client():
    loop = asyncio.get_running_loop()
    proto = _UdpClient()
    await loop.create_datagram_endpoint(lambda: proto, local_addr=(_HOST, 0))
    yield proto
    proto.close()


# ---------------------------------------------------------------------------
# Scope control state defaults (from scope_control recording)
# ---------------------------------------------------------------------------


async def test_scope_10_default_01(server, client):
    """27 10 bare read → 27 10 01 (recorded default)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x10"), seq=1)
    assert reply[4] == 0x27
    assert reply[5] == 0x10
    assert reply[6] == 0x01  # recorded default
    assert reply[-1] == 0xFD


async def test_scope_11_default_00(server, client):
    """27 11 bare read → 27 11 00 (recorded default)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x11"), seq=2)
    assert reply[4] == 0x27
    assert reply[5] == 0x11
    assert reply[6] == 0x00  # recorded default
    assert reply[-1] == 0xFD


async def test_scope_12_default_00(server, client):
    """27 12 bare read → 27 12 00 (recorded default)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x12"), seq=3)
    assert reply[4] == 0x27
    assert reply[5] == 0x12
    assert reply[6] == 0x00
    assert reply[-1] == 0xFD


async def test_scope_13_default_01(server, client):
    """27 13 bare read → 27 13 01 (recorded default)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x13"), seq=4)
    assert reply[4] == 0x27
    assert reply[5] == 0x13
    assert reply[6] == 0x01
    assert reply[-1] == 0xFD


# ---------------------------------------------------------------------------
# SET state reflected by subsequent read (scope_control authoritative)
# ---------------------------------------------------------------------------


async def test_scope_10_set_01_then_read_reflects(server, client):
    """27 10 01 SET → FB, then bare read → 27 10 01."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x10\x01"), seq=5)
    assert reply == b"\xfe\xfe\xe0\x98\xfb\xfd"  # bare FB ACK

    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x10"), seq=6)
    assert reply[6] == 0x01  # state persisted


async def test_scope_10_set_00_naks_state_unchanged(server, client):
    """27 10 00 SET (unrecorded) → NAK, state unchanged (remains 01)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x10\x00"), seq=7)
    assert reply == b"\xfe\xfe\xe0\x98\xfa\xfd"  # bare NAK

    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x10"), seq=8)
    assert reply[6] == 0x01  # state unchanged


async def test_scope_11_set_01_then_read_reflects(server, client):
    """27 11 01 SET → FB, then bare read → 27 11 01."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x11\x01"), seq=9)
    assert reply == b"\xfe\xfe\xe0\x98\xfb\xfd"  # bare FB ACK

    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x11"), seq=10)
    assert reply[6] == 0x01


async def test_scope_11_set_00_then_read_reflects(server, client):
    """27 11 00 SET → FB, then bare read → 27 11 00."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x11\x00"), seq=11)
    assert reply == b"\xfe\xfe\xe0\x98\xfb\xfd"  # bare FB ACK

    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x11"), seq=12)
    assert reply[6] == 0x00


async def test_scope_12_set_00_then_read_reflects(server, client):
    """27 12 00 SET → FB, then bare read → 27 12 00."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x12\x00"), seq=13)
    assert reply == b"\xfe\xfe\xe0\x98\xfb\xfd"

    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x12"), seq=14)
    assert reply[6] == 0x00


async def test_scope_12_set_01_then_read_reflects(server, client):
    """27 12 01 SET → FB, then bare read → 27 12 01."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x12\x01"), seq=15)
    assert reply == b"\xfe\xfe\xe0\x98\xfb\xfd"

    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x12"), seq=16)
    assert reply[6] == 0x01


async def test_scope_13_set_00_then_read_reflects(server, client):
    """27 13 00 SET → FB, then bare read → 27 13 00."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x13\x00"), seq=17)
    assert reply == b"\xfe\xfe\xe0\x98\xfb\xfd"

    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x13"), seq=18)
    assert reply[6] == 0x00


async def test_scope_13_set_01_then_read_reflects(server, client):
    """27 13 01 SET → FB, then bare read → 27 13 01."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x13\x01"), seq=19)
    assert reply == b"\xfe\xfe\xe0\x98\xfb\xfd"

    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x13"), seq=20)
    assert reply[6] == 0x01


# ---------------------------------------------------------------------------
# Invalid payloads NAK (not in scope_control)
# ---------------------------------------------------------------------------


async def test_scope_10_invalid_payload_naks(server, client):
    """27 10 02 (unrecorded) → NAK."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x10\x02"), seq=21)
    assert reply == b"\xfe\xfe\xe0\x98\xfa\xfd"  # bare NAK


async def test_scope_11_invalid_payload_naks(server, client):
    """27 11 02 (unrecorded) → NAK."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x11\x02"), seq=22)
    assert reply == b"\xfe\xfe\xe0\x98\xfa\xfd"


async def test_scope_12_invalid_payload_naks(server, client):
    """27 12 02 (unrecorded) → NAK."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x12\x02"), seq=23)
    assert reply == b"\xfe\xfe\xe0\x98\xfa\xfd"


async def test_scope_13_invalid_payload_naks(server, client):
    """27 13 02 (unrecorded) → NAK."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x13\x02"), seq=24)
    assert reply == b"\xfe\xfe\xe0\x98\xfa\xfd"


# ---------------------------------------------------------------------------
# Other subcommands (14-1F) NAK via fallback (not in this slice)
# ---------------------------------------------------------------------------


async def test_scope_14_bare_read_naks_via_fallback(server, client):
    """27 14 bare read → NAK (not in 10-13 slice)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x14"), seq=25)
    assert reply == b"\xfe\xfe\xe0\x98\xfa\xfd"


async def test_scope_15_bare_read_naks_via_fallback(server, client):
    """27 15 bare read → NAK (not in 10-13 slice)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x15"), seq=26)
    assert reply == b"\xfe\xfe\xe0\x98\xfa\xfd"


async def test_scope_1a_bare_read_naks_via_fallback(server, client):
    """27 1A bare read → NAK (not in 10-13 slice)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x1a"), seq=27)
    assert reply == b"\xfe\xfe\xe0\x98\xfa\xfd"
