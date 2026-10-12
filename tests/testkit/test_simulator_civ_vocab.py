"""Wire-level tests: the simulator's CI-V 0x25/0x26 and 0x1A 05 vocabulary.

A bare asyncio datagram client speaks the CI-V wire format against a real
``MockIcomRadio`` on loopback UDP — no production code involved — pinning the
selected/unselected receiver frequency and mode reads and writes (0x25/0x26,
both subcommand bytes) and the 0x1A 05 MOD-input menu reads the product's
startup reader issues (observed on the wire: ``web/radio_poller.py:
_fetch_mod_inputs`` reads items 0x0091-0x0094 at connect).

BCD frequency decoding here is deliberately independent of the simulator's
own encoder so an encoding defect cannot cancel out.
"""

import asyncio
import struct

import pytest

from rigplane_testkit.icom import MockIcomRadio

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


def _encode_bcd_freq(freq_hz: int) -> bytes:
    """Icom 5-byte BCD, least-significant byte first."""
    digits = f"{freq_hz:010d}"
    out = bytearray(5)
    for i in range(5):
        low = int(digits[9 - 2 * i])
        high = int(digits[9 - 2 * i - 1])
        out[i] = (high << 4) | low
    return bytes(out)


def _decode_bcd_freq(data: bytes) -> int:
    freq = 0
    for i, byte in enumerate(data):
        freq += (byte & 0x0F) * (10 ** (2 * i)) + (byte >> 4) * (10 ** (2 * i + 1))
    return freq


async def _send_civ(client: _UdpClient, port: int, frame: bytes) -> bytes:
    """Send one CI-V frame, return the CI-V frame of the reply."""
    client.send(_civ_packet(frame), port)
    reply = await client.recv()
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
# 0x25 selected / unselected receiver frequency
# ---------------------------------------------------------------------------


async def test_selected_freq_read_returns_current_frequency_as_bcd(server, client):
    server.set_frequency(7_100_123)
    reply = await _send_civ(client, server.civ_port, _frame(0x25, b"\x00"))
    assert reply[4] == 0x25
    assert reply[5] == 0x00  # receiver byte echoes the selected subcommand
    assert reply[-1] == 0xFD
    assert _decode_bcd_freq(reply[6:11]) == 7_100_123


async def test_0x05_write_and_selected_freq_read_agree_both_ways(server, client):
    # 0x05 write → visible through 0x25 00.
    ack = await _send_civ(
        client, server.civ_port, _frame(0x05, _encode_bcd_freq(7_200_000))
    )
    assert ack[4] == 0xFB  # ACK
    selected = await _send_civ(client, server.civ_port, _frame(0x25, b"\x00"))
    assert _decode_bcd_freq(selected[6:11]) == 7_200_000

    # 0x25 00 write → visible through 0x03.
    ack = await _send_civ(
        client, server.civ_port, _frame(0x25, b"\x00" + _encode_bcd_freq(7_300_000))
    )
    assert ack[4] == 0xFB
    plain = await _send_civ(client, server.civ_port, _frame(0x03, b""))
    assert _decode_bcd_freq(plain[5:10]) == 7_300_000


async def test_unselected_freq_write_then_read_round_trip(server, client):
    ack = await _send_civ(
        client, server.civ_port, _frame(0x25, b"\x01" + _encode_bcd_freq(3_500_000))
    )
    assert ack[4] == 0xFB
    unselected = await _send_civ(client, server.civ_port, _frame(0x25, b"\x01"))
    assert unselected[5] == 0x01  # receiver byte echoes the unselected subcommand
    assert _decode_bcd_freq(unselected[6:11]) == 3_500_000

    # The unselected VFO has its own state: the selected frequency is untouched.
    selected = await _send_civ(client, server.civ_port, _frame(0x25, b"\x00"))
    assert _decode_bcd_freq(selected[6:11]) == 14_074_000


# ---------------------------------------------------------------------------
# 0x26 selected / unselected receiver mode
# ---------------------------------------------------------------------------


async def test_selected_mode_round_trip(server, client):
    # 0x26 00 set: receiver, mode (0x03=CW), data-mode flag, filter.
    ack = await _send_civ(client, server.civ_port, _frame(0x26, b"\x00\x03\x01\x02"))
    assert ack[4] == 0xFB
    reply = await _send_civ(client, server.civ_port, _frame(0x26, b"\x00"))
    assert reply[4] == 0x26
    assert reply[5:9] == b"\x00\x03\x01\x02"

    # The selected mode/filter state is shared with 0x04.
    plain = await _send_civ(client, server.civ_port, _frame(0x04, b""))
    assert plain[5:7] == b"\x03\x02"


async def test_unselected_mode_read_returns_own_defaults(server, client):
    reply = await _send_civ(client, server.civ_port, _frame(0x26, b"\x01"))
    assert reply[4] == 0x26
    assert reply[5] == 0x01
    mode, data_mode, filt = reply[6], reply[7], reply[8]
    assert (mode, data_mode, filt) == (0x01, 0x00, 0x01)


# ---------------------------------------------------------------------------
# 0x1A 05 menu items (startup MOD-input reads)
# ---------------------------------------------------------------------------


async def test_1a05_mod_input_read_returns_modelled_byte(server, client):
    reply = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x05\x00\x92"))
    assert reply[4] == 0x1A
    assert reply[5] == 0x05
    # Item echo (00 92 = DATA1 MOD input) then the 1-byte BCD value 5 (LAN).
    assert reply[6:9] == b"\x00\x92\x05"


async def test_unknown_1a05_item_still_naks(server, client):
    # 00 33 (Quick Split) is a real IC-7610 menu item outside the modelled
    # table — it must NAK, not answer a made-up byte.
    reply = await _send_civ(client, server.civ_port, _frame(0x1A, b"\x05\x00\x33"))
    assert reply[4] == 0xFA  # NAK
