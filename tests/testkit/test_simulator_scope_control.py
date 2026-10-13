"""Wire-level tests: the simulator's CI-V 0x27 scope control vocabulary.

A bare asyncio datagram client speaks the CI-V wire format against a real
``MockIcomRadio`` on loopback UDP — no production code involved — pinning the
recorded scope control forms from the ``scope_control`` and ``scope`` windows
of ``tests/fixtures/ic7610/civ_pairs.jsonl``.

The recorded forms are authoritative: the simulator must answer exactly those
requests with the recorded reply shapes (ACK, data, or wave frames), and must
NAK or not answer unrecorded forms (e.g. bare 0x10/0x11 reads, 0x1A 01 read).
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
# Recorded scope control forms (scope_control window)
# ---------------------------------------------------------------------------


async def test_scope_1c_read_returns_recorded_data(server, client):
    """0x27 1C read → recorded data reply (27 1C 02)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x1c"), seq=1)
    assert reply[4] == 0x27
    assert reply[5] == 0x1C
    assert reply[6] == 0x02  # recorded payload byte
    assert reply[-1] == 0xFD


async def test_scope_13_read_returns_recorded_data(server, client):
    """0x27 13 read → recorded data reply (27 13 01)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x13"), seq=2)
    assert reply[4] == 0x27
    assert reply[5] == 0x13
    assert reply[6] == 0x01  # recorded payload byte
    assert reply[-1] == 0xFD


async def test_scope_1b_read_returns_recorded_data(server, client):
    """0x27 1B read → recorded data reply (27 1B 01)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x1b"), seq=3)
    assert reply[4] == 0x27
    assert reply[5] == 0x1B
    assert reply[6] == 0x01  # recorded payload byte
    assert reply[-1] == 0xFD


async def test_scope_16_00_set_returns_recorded_ack(server, client):
    """0x27 16 00 set → recorded ACK reply (27 16 00 01).

    The recording shows a data frame with payload 01, not a bare FB ACK.
    """
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x16\x00"), seq=4)
    assert reply[4] == 0x27
    assert reply[5] == 0x16
    assert reply[6:8] == b"\x00\x01"  # recorded ACK with 01 payload
    assert reply[-1] == 0xFD


async def test_scope_1e_01_01_set_returns_recorded_data(server, client):
    """0x27 1E 01 01 set → recorded long data reply."""
    reply = await _send_civ(
        client,
        server.civ_port,
        _frame(0x27, b"\x1e\x01\x01"),
        seq=5,
    )
    assert reply[4] == 0x27
    assert reply[5] == 0x1E
    # Recorded reply: 01 01 00 00 50 00 00 00 00 50 01 00
    assert reply[6:18] == b"\x01\x01\x00\x00\x50\x00\x00\x00\x00\x50\x01\x00"
    assert reply[-1] == 0xFD


async def test_scope_17_00_set_returns_ack(server, client):
    """0x27 17 00 set → ACK (00 payload)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x17\x00"), seq=6)
    assert reply[4] == 0x27
    assert reply[5] == 0x17
    assert reply[6] == 0x00  # recorded ACK with 00
    assert reply[-1] == 0xFD


async def test_scope_14_00_set_returns_ack(server, client):
    """0x27 14 00 set → ACK (00 payload)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x14\x00"), seq=7)
    assert reply[4] == 0x27
    assert reply[5] == 0x14
    assert reply[6] == 0x00  # recorded ACK with 00
    assert reply[-1] == 0xFD


async def test_scope_1f_00_set_returns_ack(server, client):
    """0x27 1F 00 set → ACK (01 payload)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x1f\x00"), seq=8)
    assert reply[4] == 0x27
    assert reply[5] == 0x1F
    assert reply[6] == 0x01  # recorded ACK with 01
    assert reply[-1] == 0xFD


async def test_scope_12_read_returns_recorded_data(server, client):
    """0x27 12 read → recorded data reply (27 12 00)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x12"), seq=9)
    assert reply[4] == 0x27
    assert reply[5] == 0x12
    assert reply[6] == 0x00  # recorded payload byte
    assert reply[-1] == 0xFD


async def test_scope_19_00_set_returns_recorded_data(server, client):
    """0x27 19 00 set → recorded data reply (27 19 00 10 00 01)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x19\x00"), seq=10)
    assert reply[4] == 0x27
    assert reply[5] == 0x19
    assert reply[6:10] == b"\x00\x10\x00\x01"  # recorded data
    assert reply[-1] == 0xFD


async def test_scope_15_00_set_returns_recorded_data(server, client):
    """0x27 15 00 set → recorded data reply (27 15 00 00 10 00 00)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x15\x00"), seq=11)
    assert reply[4] == 0x27
    assert reply[5] == 0x15
    assert reply[6:11] == b"\x00\x00\x10\x00\x00"  # recorded data
    assert reply[-1] == 0xFD


async def test_scope_1a_00_set_returns_ack(server, client):
    """0x27 1A 00 set → ACK (01 payload)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x1a\x00"), seq=12)
    assert reply[4] == 0x27
    assert reply[5] == 0x1A
    assert reply[6] == 0x01  # recorded ACK with 01
    assert reply[-1] == 0xFD


async def test_scope_1d_00_set_returns_ack(server, client):
    """0x27 1D 00 set → ACK (00 payload)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x1d\x00"), seq=13)
    assert reply[4] == 0x27
    assert reply[5] == 0x1D
    assert reply[6] == 0x00  # recorded ACK with 00
    assert reply[-1] == 0xFD


# ---------------------------------------------------------------------------
# Unrecorded bare reads must NAK (M1 from frozen review)
# ---------------------------------------------------------------------------


async def test_scope_10_bare_read_naks(server, client):
    """0x27 10 bare read (no subcommand) is unrecorded → must NAK."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x10"), seq=14)
    assert reply == b"\xfe\xfe\xe0\x98\xfa\xfd"  # bare NAK


async def test_scope_11_bare_read_naks(server, client):
    """0x27 11 bare read (no subcommand) is unrecorded → must NAK."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x11"), seq=15)
    assert reply == b"\xfe\xfe\xe0\x98\xfa\xfd"  # bare NAK


async def test_scope_1a_bare_read_naks(server, client):
    """0x27 1A bare read (no subcommand data) is unrecorded → must NAK."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x1a"), seq=16)
    assert reply == b"\xfe\xfe\xe0\x98\xfa\xfd"  # bare NAK


# ---------------------------------------------------------------------------
# Scope SET forms that must ACK (scope_control window later entries)
# ---------------------------------------------------------------------------


async def test_scope_13_01_set_returns_ack(server, client):
    """0x27 13 01 set → ACK."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x13\x01"), seq=17)
    assert reply == b"\xfe\xfe\xe0\x98\xfb\xfd"  # bare ACK


async def test_scope_13_00_set_returns_ack(server, client):
    """0x27 13 00 set → ACK."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x13\x00"), seq=18)
    assert reply == b"\xfe\xfe\xe0\x98\xfb\xfd"  # bare ACK


async def test_scope_12_01_set_returns_ack(server, client):
    """0x27 12 01 set → ACK."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x12\x01"), seq=19)
    assert reply == b"\xfe\xfe\xe0\x98\xfb\xfd"  # bare ACK


async def test_scope_12_00_set_returns_ack(server, client):
    """0x27 12 00 set → ACK."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x12\x00"), seq=20)
    assert reply == b"\xfe\xfe\xe0\x98\xfb\xfd"  # bare ACK


async def test_scope_11_00_set_returns_ack(server, client):
    """0x27 11 00 set → ACK."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x11\x00"), seq=21)
    assert reply == b"\xfe\xfe\xe0\x98\xfb\xfd"  # bare ACK


# ---------------------------------------------------------------------------
# Scope window forms: 10 01 and 11 01 set → wave frames, NOT ACK (B2)
# ---------------------------------------------------------------------------


async def test_scope_10_01_set_returns_wave_frames_not_ack(server, client):
    """0x27 10 01 set → wave frames (scope data stream), NOT ACK.

    In the ``scope`` window of civ_pairs.jsonl, 27 10 01 is followed only
    by the LAN echo, then wave frames (27 00 01...), with NO FB.
    """
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x10\x01"), seq=22)
    # Must NOT be a bare ACK (FB)
    assert reply[4] != 0xFB, "27 10 01 must not answer with ACK"
    # Must be a wave frame starting with 27 00 01...
    assert reply[4] == 0x27
    assert reply[5] == 0x00
    assert reply[6] == 0x01


async def test_scope_11_01_set_returns_wave_frames_not_ack(server, client):
    """0x27 11 01 set → wave frames (scope data stream), NOT ACK.

    In the ``scope`` window of civ_pairs.jsonl, 27 11 01 is followed only
    by the LAN echo, then wave frames (27 00 00...), with NO FB.
    """
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x11\x01"), seq=23)
    # Must NOT be a bare ACK (FB)
    assert reply[4] != 0xFB, "27 11 01 must not answer with ACK"
    # Must be a wave frame starting with 27 00 00...
    assert reply[4] == 0x27
    assert reply[5] == 0x00
    assert reply[6] == 0x00


# ---------------------------------------------------------------------------
# Scope window later SET forms that return data (not ACK)
# ---------------------------------------------------------------------------


async def test_scope_1a_01_set_returns_recorded_data(server, client):
    """0x27 1A 01 set → recorded data reply (27 1A 01 00)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x1a\x01"), seq=24)
    assert reply[4] == 0x27
    assert reply[5] == 0x1A
    assert reply[6] == 0x01
    assert reply[7] == 0x00  # recorded data
    assert reply[-1] == 0xFD


async def test_scope_15_01_set_returns_recorded_data(server, client):
    """0x27 15 01 set → recorded data reply (27 15 01 00 00 10 00 00)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x15\x01"), seq=25)
    assert reply[4] == 0x27
    assert reply[5] == 0x15
    assert reply[6] == 0x01
    assert reply[7:12] == b"\x00\x00\x10\x00\x00"  # recorded data
    assert reply[-1] == 0xFD


async def test_scope_14_01_set_returns_recorded_data(server, client):
    """0x27 14 01 set → recorded data reply (27 14 01 00)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x14\x01"), seq=26)
    assert reply[4] == 0x27
    assert reply[5] == 0x14
    assert reply[6] == 0x01
    assert reply[7] == 0x00  # recorded data
    assert reply[-1] == 0xFD


async def test_scope_19_01_set_returns_recorded_data(server, client):
    """0x27 19 01 set → recorded data reply (27 19 01 10 00 00)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x19\x01"), seq=27)
    assert reply[4] == 0x27
    assert reply[5] == 0x19
    assert reply[6] == 0x01
    assert reply[7:10] == b"\x10\x00\x00"  # recorded data
    assert reply[-1] == 0xFD


async def test_scope_16_01_set_returns_ack(server, client):
    """0x27 16 01 set → ACK (01 payload)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x16\x01"), seq=28)
    assert reply[4] == 0x27
    assert reply[5] == 0x16
    assert reply[6] == 0x01  # recorded ACK with 01
    assert reply[-1] == 0xFD


async def test_scope_1d_01_set_returns_recorded_data(server, client):
    """0x27 1D 01 set → recorded data reply (27 1D 01 00)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x1d\x01"), seq=29)
    assert reply[4] == 0x27
    assert reply[5] == 0x1D
    assert reply[6] == 0x01
    assert reply[7] == 0x00  # recorded data
    assert reply[-1] == 0xFD


async def test_scope_1f_01_set_returns_ack(server, client):
    """0x27 1F 01 set → ACK (01 payload)."""
    reply = await _send_civ(client, server.civ_port, _frame(0x27, b"\x1f\x01"), seq=30)
    assert reply[4] == 0x27
    assert reply[5] == 0x1F
    assert reply[6] == 0x01  # recorded ACK with 01
    assert reply[-1] == 0xFD
