"""Serial session driver and transport adapters for IC-7610 shared core."""

from __future__ import annotations

import asyncio
import struct
from typing import Protocol

from ....types import PacketType
from .contracts import CivLink, SessionDriver

_CIV_HEADER_SIZE = 0x15
_CLIENT_ID = 0x00010001
_RADIO_ID = 0x00000098

# Type marker at offset 0x10-0x11 (little-endian u16) for LAN-style OpenClose
# session-control packets emitted by ``_send_open_close_on_transport`` in
# ``runtime/_control_phase.py``. These packets have no CI-V payload and must
# never be forwarded onto a serial CI-V wire — see MOR-172.
_LAN_OPENCLOSE_TYPE_MARKER = 0x01C0


class _LifecycleCivLink(CivLink, Protocol):
    """CivLink with connect/disconnect lifecycle required by serial session."""

    async def connect(self) -> None:
        """Open the CI-V link."""

    async def disconnect(self) -> None:
        """Close the CI-V link."""

    @property
    def connected(self) -> bool:
        """Whether the CI-V link is connected."""


def _unwrap_civ_frame(packet: bytes) -> bytes:
    """Extract CI-V frame payload from shared-core UDP envelope.

    Session-control packets such as OpenClose share the envelope layout
    but stamp the LAN type marker ``0x01C0`` at offset 0x10 (LE u16) and
    carry no CI-V payload. Note that the marker's high byte spills into
    offsets 0x11-0x12, so a payload-length-only check misreads it as
    ``payload_len = 1`` and extracts the trailing close-flag byte — which
    the serial codec then wraps as a malformed CI-V frame ``FE FE 00 FD``
    that wedged the Xiegu X6200's CI-V parser on every session close
    (MOR-172). Reject the marker explicitly.

    DATA packets can come from either :func:`_wrap_civ_frame` (byte 0x10
    = 0x00) or from runtime ``_civ_rx._wrap_civ`` (byte 0x10 = 0xC1);
    both layouts must pass through. Hence we gate on the OpenClose
    marker exactly, not on "byte 0x10 not zero".
    """
    if len(packet) <= _CIV_HEADER_SIZE:
        return b""
    if (
        len(packet) >= 0x12
        and int.from_bytes(packet[0x10:0x12], "little") == _LAN_OPENCLOSE_TYPE_MARKER
    ):
        return b""
    payload_len = int.from_bytes(packet[0x11:0x13], "little", signed=False)
    if payload_len <= 0:
        return b""
    start = _CIV_HEADER_SIZE
    end = start + payload_len
    if end > len(packet):
        return packet[start:]
    return packet[start:end]


def _wrap_civ_frame(frame: bytes, *, seq: int) -> bytes:
    """Wrap raw CI-V frame into shared-core UDP envelope."""
    total_len = _CIV_HEADER_SIZE + len(frame)
    pkt = bytearray(total_len)
    struct.pack_into("<I", pkt, 0, total_len)
    struct.pack_into("<H", pkt, 4, PacketType.DATA)
    struct.pack_into("<H", pkt, 6, seq)
    struct.pack_into("<I", pkt, 8, _RADIO_ID)
    struct.pack_into("<I", pkt, 0x0C, _CLIENT_ID)
    pkt[0x10] = 0x00
    struct.pack_into("<H", pkt, 0x11, len(frame))
    struct.pack_into("<H", pkt, 0x13, 0)
    pkt[_CIV_HEADER_SIZE:] = frame
    return bytes(pkt)


class SerialControlTransport:
    """Minimal control transport shim expected by shared core."""

    def __init__(self) -> None:
        self.my_id = _CLIENT_ID
        self.remote_id = _RADIO_ID
        self.send_seq = 0
        self.ping_seq = 0
        self.rx_packet_count = 0
        self._udp_transport: object | None = None

    @property
    def connected(self) -> bool:
        return self._udp_transport is not None

    def mark_connected(self) -> None:
        self._udp_transport = object()

    async def disconnect(self) -> None:
        self._udp_transport = None

    def start_ping_loop(self) -> None:
        return None

    def start_retransmit_loop(self) -> None:
        return None

    def start_idle_loop(self) -> None:
        return None

    async def send_tracked(self, _data: bytes) -> None:
        return None

    async def receive_packet(self, timeout: float = 5.0) -> bytes:
        await asyncio.sleep(timeout)
        raise asyncio.TimeoutError()


class SerialCivTransport:
    """Shared-core transport adapter over a raw serial CI-V link."""

    def __init__(self, civ_link: _LifecycleCivLink) -> None:
        self._civ_link = civ_link
        self.my_id = _CLIENT_ID
        self.remote_id = _RADIO_ID
        self.send_seq = 0
        self.ping_seq = 0
        self.rx_packet_count = 0
        self._udp_error_count = 0
        self._packet_queue: asyncio.Queue[bytes] = asyncio.Queue()

    @property
    def connected(self) -> bool:
        return bool(getattr(self._civ_link, "connected", False))

    @property
    def ready(self) -> bool:
        ready = getattr(self._civ_link, "ready", None)
        if isinstance(ready, bool):
            return ready
        healthy = getattr(self._civ_link, "healthy", None)
        if isinstance(healthy, bool):
            return self.connected and healthy
        return self.connected

    def start_ping_loop(self) -> None:
        return None

    def start_retransmit_loop(self) -> None:
        return None

    def start_idle_loop(self) -> None:
        return None

    async def send_tracked(self, data: bytes) -> None:
        # MOR-172: ``_send_open_close_on_transport`` in ``_control_phase.py``
        # emits a LAN-style binary OpenClose packet (type marker 0x01C0 at
        # offset 0x10) that the shared-core lifecycle calls on session
        # open/close. The serial CI-V wire has no LAN session-control layer;
        # these packets carry no CI-V payload and must be dropped here, not
        # forwarded. ``_unwrap_civ_frame`` already short-circuits non-DATA
        # packets as a backstop (it gates on byte 0x10), but matching the
        # type marker explicitly here keeps the intent self-documenting
        # and resistant to future changes in the LAN packet layout.
        data_bytes = bytes(data)
        if (
            len(data_bytes) >= 0x12
            and int.from_bytes(data_bytes[0x10:0x12], "little")
            == _LAN_OPENCLOSE_TYPE_MARKER
        ):
            return
        frame = _unwrap_civ_frame(data_bytes)
        if not frame:
            return
        try:
            await self._civ_link.send(frame)
            self.send_seq = (self.send_seq + 1) & 0xFFFF
            self._udp_error_count = 0
        except Exception:
            self._udp_error_count += 1
            raise

    async def receive_packet(self, timeout: float = 5.0) -> bytes:
        if not self._packet_queue.empty():
            return self._packet_queue.get_nowait()

        try:
            frame = await self._civ_link.receive(timeout=timeout)
        except Exception as exc:
            self._udp_error_count += 1
            raise asyncio.TimeoutError() from exc
        if frame is None:
            raise asyncio.TimeoutError()

        self._udp_error_count = 0
        self.rx_packet_count += 1
        self.ping_seq = (self.ping_seq + 1) & 0xFFFF
        return _wrap_civ_frame(bytes(frame), seq=self.ping_seq)

    async def disconnect(self) -> None:
        await self._civ_link.disconnect()
        self._packet_queue = asyncio.Queue()


class SerialSessionDriver(SessionDriver):
    """Serial session lifecycle driver for shared IC-7610 core."""

    def __init__(self, civ_link: _LifecycleCivLink) -> None:
        self._civ_link = civ_link
        self._connected = False
        self._control_transport = SerialControlTransport()
        self._civ_transport = SerialCivTransport(civ_link)

    @property
    def control_transport(self) -> SerialControlTransport:
        return self._control_transport

    @property
    def civ_transport(self) -> SerialCivTransport:
        return self._civ_transport

    @property
    def connected(self) -> bool:
        return (
            self._connected
            and self._control_transport.connected
            and self._civ_transport.connected
        )

    @property
    def ready(self) -> bool:
        return self.connected and self._civ_transport.ready

    async def connect(self) -> None:
        if self.connected:
            return
        self._control_transport = SerialControlTransport()
        self._civ_transport = SerialCivTransport(self._civ_link)
        await self._civ_link.connect()
        self._control_transport.mark_connected()
        self._connected = True

    async def disconnect(self) -> None:
        await self._civ_transport.disconnect()
        await self._control_transport.disconnect()
        self._connected = False


__all__ = [
    "SerialCivTransport",
    "SerialControlTransport",
    "SerialSessionDriver",
]
