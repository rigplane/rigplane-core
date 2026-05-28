"""Tests for serial session transports — regression coverage for MOR-172.

MOR-172: the shared-core ``_send_open_close_on_transport`` in
``runtime/_control_phase.py`` emits a LAN-style binary OpenClose packet
(type marker 0x01C0) on every session open/close. On the serial backend the
serial transport adapter must drop those packets — forwarding them produced
a malformed CI-V frame ``FE FE 00 FD`` on the wire that wedged the Xiegu
X6200's CI-V parser for ~5-10s.
"""

from __future__ import annotations

import struct

import pytest

from rigplane.backends.icom7610.drivers.serial_session import (
    _CIV_HEADER_SIZE,
    _LAN_OPENCLOSE_TYPE_MARKER,
    SerialCivTransport,
    _unwrap_civ_frame,
    _wrap_civ_frame,
)


def _build_openclose_packet(*, open_stream: bool) -> bytes:
    """Build the exact 22-byte OpenClose packet that
    ``_send_open_close_on_transport`` emits.
    """
    pkt = bytearray(0x16)
    struct.pack_into("<I", pkt, 0x00, 0x16)
    struct.pack_into("<H", pkt, 0x10, _LAN_OPENCLOSE_TYPE_MARKER)
    pkt[0x15] = 0x04 if open_stream else 0x00
    return bytes(pkt)


class _RecordingCivLink:
    """Minimal CivLink that records what got sent through it."""

    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.connected = True
        self.ready = True
        self.healthy = True

    async def send(self, frame: bytes) -> None:
        self.sent.append(bytes(frame))

    async def receive(self, *, timeout: float = 5.0) -> bytes:  # pragma: no cover
        raise NotImplementedError

    async def connect(self) -> None:  # pragma: no cover
        pass

    async def disconnect(self) -> None:  # pragma: no cover
        pass


@pytest.mark.asyncio
async def test_send_tracked_drops_openclose_close_packet() -> None:
    """MOR-172: an OpenClose(close) LAN packet must NOT be forwarded to the
    serial CI-V wire. Forwarding it produced ``FE FE 00 FD`` which wedged X6200.
    """
    link = _RecordingCivLink()
    transport = SerialCivTransport(link)
    pkt = _build_openclose_packet(open_stream=False)
    assert len(pkt) == 0x16
    # Sanity: the type marker really is at 0x10 in this packet shape.
    assert int.from_bytes(pkt[0x10:0x12], "little") == _LAN_OPENCLOSE_TYPE_MARKER

    await transport.send_tracked(pkt)

    assert link.sent == [], f"OpenClose(close) leaked to serial CI-V wire: {link.sent}"


@pytest.mark.asyncio
async def test_send_tracked_drops_openclose_open_packet() -> None:
    """Same as close, but for the open variant (pkt[0x15]=0x04). Symmetry
    matters — both directions of the session-control lifecycle must be
    blocked on serial.
    """
    link = _RecordingCivLink()
    transport = SerialCivTransport(link)
    pkt = _build_openclose_packet(open_stream=True)

    await transport.send_tracked(pkt)

    assert link.sent == [], f"OpenClose(open) leaked to serial CI-V wire: {link.sent}"


@pytest.mark.asyncio
async def test_send_tracked_forwards_real_civ_frame() -> None:
    """A real CI-V data frame (wrapped via ``_wrap_civ_frame``) must still be
    forwarded unchanged — the OpenClose guard must not over-shoot.
    """
    link = _RecordingCivLink()
    transport = SerialCivTransport(link)
    civ_frame = bytes([0xFE, 0xFE, 0xA4, 0xE0, 0x03, 0xFD])  # get_freq to 0xA4
    packet = _wrap_civ_frame(civ_frame, seq=0)
    # Sanity: the wrapper packet does NOT have the OpenClose type marker.
    assert int.from_bytes(packet[0x10:0x12], "little") != _LAN_OPENCLOSE_TYPE_MARKER

    await transport.send_tracked(packet)

    assert link.sent == [civ_frame], (
        f"Real CI-V frame should be forwarded as-is, got {link.sent}"
    )


def test_unwrap_civ_frame_returns_empty_on_openclose_packet() -> None:
    """MOR-172 root-cause fix: ``_unwrap_civ_frame`` must NOT extract any
    bytes from an OpenClose packet. The type marker ``0x01C0`` at offset
    0x10 (LE u16) lays its low byte ``0xC0`` over byte 0x10 and its high
    byte ``0x01`` over byte 0x11 — which used to be misread as
    ``payload_len = 1`` by the previous code, causing extraction of the
    single close-flag byte at offset 0x15 and downstream emission of the
    wedge frame ``FE FE 00 FD``.
    """
    pkt = _build_openclose_packet(open_stream=False)
    # Sanity: byte 0x10 is the low byte of type marker 0x01C0 = 0xC0,
    # NOT 0x00 (the DATA type indicator). This is the discriminator.
    assert pkt[0x10] == 0xC0
    # And the OLD bug: reading payload_len from offsets 0x11-0x12 gives
    # 0x01 (the high byte of the type marker), which the previous code
    # would have treated as a valid 1-byte payload.
    assert int.from_bytes(pkt[0x11:0x13], "little") == 0x01

    assert _unwrap_civ_frame(pkt) == b"", (
        "OpenClose packet (byte 0x10 != 0x00) must unwrap to b'', not "
        "spilled bytes from the close-flag region"
    )


def test_unwrap_civ_frame_returns_empty_on_zero_payload_data_packet() -> None:
    """A DATA packet (byte 0x10 == 0x00) with payload_len = 0 still
    unwraps to ``b""``. Belt-and-braces for the original ``payload_len <= 0``
    branch that the previous regression test (incorrectly) tried to exercise.
    """
    pkt = bytearray(_CIV_HEADER_SIZE + 1)  # 22 bytes, all zero
    # byte 0x10 = 0 (DATA); bytes 0x11-0x12 = 0 (payload_len = 0)
    assert pkt[0x10] == 0x00
    assert int.from_bytes(pkt[0x11:0x13], "little") == 0

    assert _unwrap_civ_frame(bytes(pkt)) == b""


def test_unwrap_civ_frame_extracts_real_civ_payload() -> None:
    """Non-zero payload_len still extracts the CI-V frame correctly — the
    fix must not regress the normal path. ``_wrap_civ_frame`` here is the
    serial_session-side wrap (``pkt[0x10] = 0x00``).
    """
    civ_frame = bytes([0xFE, 0xFE, 0xA4, 0xE0, 0x03, 0xFD])
    packet = _wrap_civ_frame(civ_frame, seq=0)
    assert _unwrap_civ_frame(packet) == civ_frame


def test_unwrap_civ_frame_extracts_runtime_wrap_civ_payload() -> None:
    """Regression for the bug introduced in the first iteration of this
    fix: ``runtime/_civ_rx.py:_wrap_civ`` sets ``pkt[0x10] = 0xC1`` (not
    0x00) but the packet still carries a valid CI-V payload. The OpenClose
    discriminator must match the type marker exactly (``0x01C0``), not
    "byte 0x10 != 0x00", otherwise every real CI-V command sent by the
    runtime gets silently dropped on serial. MOR-172.
    """
    civ_frame = bytes([0xFE, 0xFE, 0xA4, 0xE0, 0x03, 0xFD])
    # Mimic runtime/_civ_rx.py::_wrap_civ layout exactly.
    total_len = _CIV_HEADER_SIZE + len(civ_frame)
    pkt = bytearray(total_len)
    struct.pack_into("<I", pkt, 0, total_len)
    struct.pack_into("<H", pkt, 4, 0x00)
    struct.pack_into("<I", pkt, 8, 0)
    struct.pack_into("<I", pkt, 0x0C, 0)
    pkt[0x10] = (
        0xC1  # <-- the byte that the previous Fix-A iteration falsely treated as "non-DATA"
    )
    struct.pack_into("<H", pkt, 0x11, len(civ_frame))
    pkt[_CIV_HEADER_SIZE:] = civ_frame
    # Sanity: this is NOT an OpenClose packet.
    assert int.from_bytes(pkt[0x10:0x12], "little") != _LAN_OPENCLOSE_TYPE_MARKER
    assert pkt[0x10] == 0xC1

    assert _unwrap_civ_frame(bytes(pkt)) == civ_frame
