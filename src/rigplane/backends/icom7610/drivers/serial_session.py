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
    """Extract CI-V frame payload from shared-core UDP envelope."""
    if len(packet) <= _CIV_HEADER_SIZE:
        return b""
    payload_len = int.from_bytes(packet[0x11:0x13], "little", signed=False)
    start = _CIV_HEADER_SIZE
    if payload_len <= 0:
        return packet[start:]
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
        frame = _unwrap_civ_frame(bytes(data))
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
