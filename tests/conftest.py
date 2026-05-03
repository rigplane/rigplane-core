"""Shared test fixtures for icom-lan tests."""

from __future__ import annotations

import os as _os

_os.environ.setdefault("ICOM_LAN_DISABLE_DIAGNOSTIC_LOGGING", "1")
del _os

import struct  # noqa: E402
from collections.abc import AsyncGenerator  # noqa: E402

import pytest  # noqa: E402

from icom_lan.radio import IcomRadio  # noqa: TID251, E402
from icom_lan.types import HEADER_SIZE, PacketType  # noqa: E402

from _perf_helpers import fast_connect  # noqa: E402
from mock_server import MockIcomRadio  # noqa: E402

from _caps import FULL_ICOM_CAPS as FULL_ICOM_CAPS  # noqa: F401, E402 — re-export

_HEADER_FMT = "<IHHII"


@pytest.fixture
def control_packet() -> bytes:
    """A minimal 0x10-byte control packet (type=0x01, seq=0)."""
    return struct.pack(
        _HEADER_FMT, HEADER_SIZE, PacketType.CONTROL, 0, 0x12345678, 0x9ABCDEF0
    )


@pytest.fixture
def ping_packet() -> bytes:
    """A 0x15-byte ping packet (type=0x07)."""
    header = struct.pack(_HEADER_FMT, 0x15, PacketType.PING, 42, 0xAABBCCDD, 0x11223344)
    payload = b"\x00" + struct.pack("<I", 12345)  # reply=0, time=12345
    return header + payload


@pytest.fixture
def data_packet_with_civ() -> bytes:
    """A data packet (type=0x00) carrying a small CI-V payload."""
    civ_payload = bytes([0xFE, 0xFE, 0x94, 0xE0, 0x03, 0xFD])  # Read freq command
    inner = b"\x00" + struct.pack("<HH", len(civ_payload), 1)  # reply, datalen, sendseq
    header = struct.pack(
        _HEADER_FMT,
        HEADER_SIZE + len(inner) + len(civ_payload),
        PacketType.DATA,
        5,
        0x01,
        0x02,
    )
    return header + inner + civ_payload


class FakeRadio:
    """Mock Icom radio that responds to UDP packets.

    Simulates the radio side of the protocol for testing handshake
    and packet exchange without real hardware.
    """

    def __init__(self, radio_id: int = 0xDEADBEEF) -> None:
        self.radio_id = radio_id
        self.received: list[bytes] = []
        self.token: int = 0x12345678
        self.tok_request: int = 0

    def handle(self, data: bytes) -> bytes | None:
        """Process an incoming packet and return a response (or None).

        Args:
            data: Raw packet bytes from the client.

        Returns:
            Response bytes or None if no response needed.
        """
        self.received.append(data)
        if len(data) < HEADER_SIZE:
            return None

        ptype = struct.unpack_from("<H", data, 4)[0]
        sender_id = struct.unpack_from("<I", data, 8)[0]

        # "Are you there" (type=0x03) -> respond with "I am here" (type=0x04)
        if len(data) == 0x10 and ptype == 0x03:
            return self._control_response(sender_id, ptype=0x04)

        # "Are you ready" (type=0x06) -> respond with "I am ready" (type=0x06)
        if len(data) == 0x10 and ptype == 0x06:
            return self._control_response(sender_id, ptype=0x06)

        # Login packet (0x80 bytes)
        if len(data) == 0x80:
            return self._login_response(data, sender_id)

        # Ping -> respond with ping reply
        if len(data) == 0x15 and ptype == 0x07:
            return self._ping_response(data, sender_id)

        return None

    def _control_response(self, client_id: int, *, ptype: int) -> bytes:
        pkt = bytearray(0x10)
        struct.pack_into("<I", pkt, 0, 0x10)
        struct.pack_into("<H", pkt, 4, ptype)
        struct.pack_into("<I", pkt, 8, self.radio_id)
        struct.pack_into("<I", pkt, 0x0C, client_id)
        return bytes(pkt)

    def _ping_response(self, data: bytes, client_id: int) -> bytes:
        pkt = bytearray(0x15)
        struct.pack_into("<I", pkt, 0, 0x15)
        struct.pack_into("<H", pkt, 4, PacketType.PING)
        seq = struct.unpack_from("<H", data, 6)[0]
        struct.pack_into("<H", pkt, 6, seq)
        struct.pack_into("<I", pkt, 8, self.radio_id)
        struct.pack_into("<I", pkt, 0x0C, client_id)
        pkt[0x10] = 0x01  # reply
        pkt[0x11:0x15] = data[0x11:0x15]  # echo time
        return bytes(pkt)

    def _login_response(self, data: bytes, client_id: int) -> bytes:
        self.tok_request = struct.unpack_from("<H", data, 0x1A)[0]
        pkt = bytearray(0x60)
        struct.pack_into("<I", pkt, 0, 0x60)
        struct.pack_into("<I", pkt, 8, self.radio_id)
        struct.pack_into("<I", pkt, 0x0C, client_id)
        struct.pack_into("<H", pkt, 0x1A, self.tok_request)
        struct.pack_into("<I", pkt, 0x1C, self.token)
        # error = 0 (success)
        conn = b"FTTH"
        pkt[0x40 : 0x40 + len(conn)] = conn
        return bytes(pkt)


@pytest.fixture
def fake_radio() -> FakeRadio:
    """Create a FakeRadio instance for testing."""
    return FakeRadio()


# ---------------------------------------------------------------------------
# Mock radio server fixtures (full UDP server)
# ---------------------------------------------------------------------------


@pytest.fixture
async def mock_radio() -> AsyncGenerator[MockIcomRadio]:
    """Start a mock IC-7610 UDP server for each test, stop it after."""
    server = MockIcomRadio()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def connected_radio(mock_radio: MockIcomRadio) -> AsyncGenerator[IcomRadio]:
    """An IcomRadio that has already completed the connect() handshake."""
    radio = IcomRadio(
        host="127.0.0.1",
        port=mock_radio.control_port,
        username="testuser",
        password="testpass",
        timeout=5.0,
    )
    with fast_connect():
        await radio.connect()
    yield radio
    await radio.disconnect()
