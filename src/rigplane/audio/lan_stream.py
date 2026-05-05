"""Audio streaming for Icom transceivers over LAN (UDP).

Handles RX (receive from radio) and TX (transmit to radio) audio
via the audio UDP port. Audio data is Opus-encoded; codec handling
is pluggable via callbacks.
"""

import asyncio
import logging
import struct
from dataclasses import dataclass
from enum import StrEnum
from typing import Callable

from ..transport import IcomTransport
from ..types import PacketType

__all__ = [
    "AudioStream",
    "AudioPacket",
    "AudioStats",
    "AudioState",
    "JitterBuffer",
    "AUDIO_HEADER_SIZE",
    "TX_IDENT",
    "RX_IDENT_0xA0",
]

logger = logging.getLogger(__name__)

# Audio packet header is 0x18 bytes (standard 0x10 header + 8 audio-specific)
AUDIO_HEADER_SIZE = 0x18

# TX audio ident value
TX_IDENT = 0x0080

#: Maximum audio payload per UDP packet (matches wfview chunking).
#: IC-7610 silently drops packets with larger payloads.
MAX_AUDIO_PAYLOAD = 1364

# RX ident for 0xa0-length frames
RX_IDENT_0xA0 = 0x9781


@dataclass(frozen=True, slots=True)
class AudioPacket:
    """Parsed audio packet.

    Attributes:
        ident: Audio stream identifier (0x0080 for TX, varies for RX).
        send_seq: Audio-level sequence number.
        data: Raw audio payload (format depends on negotiated codec —
              PCM16, uLaw, or Opus).  Bytes after Icom LAN header.
    """

    ident: int
    send_seq: int
    data: bytes


class AudioState(StrEnum):
    """Audio stream state."""

    IDLE = "idle"
    RECEIVING = "receiving"
    TRANSMITTING = "transmitting"


AudioStatsValue = bool | int | float | str


@dataclass(frozen=True, slots=True)
class AudioStats:
    """Runtime stream statistics snapshot.

    All metrics are JSON-safe primitives and can be serialized directly via
    :meth:`to_dict`.
    """

    active: bool
    state: str
    rx_packets_received: int
    rx_packets_delivered: int
    tx_packets_sent: int
    packets_lost: int
    packet_loss_percent: float
    # EMA of reorder depth, not RFC 3550 jitter.
    reorder_depth_ema_ms: float
    jitter_max_ms: float
    underrun_count: int
    overrun_count: int
    estimated_latency_ms: float
    jitter_buffer_depth_packets: int
    jitter_buffer_pending_packets: int
    duplicates_dropped: int
    stale_packets_dropped: int
    out_of_order_packets: int

    @classmethod
    def inactive(cls) -> "AudioStats":
        """Build a zeroed, idle stats snapshot."""
        return cls(
            active=False,
            state=AudioState.IDLE,
            rx_packets_received=0,
            rx_packets_delivered=0,
            tx_packets_sent=0,
            packets_lost=0,
            packet_loss_percent=0.0,
            reorder_depth_ema_ms=0.0,
            jitter_max_ms=0.0,
            underrun_count=0,
            overrun_count=0,
            estimated_latency_ms=0.0,
            jitter_buffer_depth_packets=0,
            jitter_buffer_pending_packets=0,
            duplicates_dropped=0,
            stale_packets_dropped=0,
            out_of_order_packets=0,
        )

    def to_dict(self) -> dict[str, AudioStatsValue]:
        """Return a JSON-friendly dictionary representation."""
        return {
            "active": self.active,
            "state": self.state,
            "rx_packets_received": self.rx_packets_received,
            "rx_packets_delivered": self.rx_packets_delivered,
            "tx_packets_sent": self.tx_packets_sent,
            "packets_lost": self.packets_lost,
            "packet_loss_percent": self.packet_loss_percent,
            "reorder_depth_ema_ms": self.reorder_depth_ema_ms,
            "jitter_max_ms": self.jitter_max_ms,
            "underrun_count": self.underrun_count,
            "overrun_count": self.overrun_count,
            "estimated_latency_ms": self.estimated_latency_ms,
            "jitter_buffer_depth_packets": self.jitter_buffer_depth_packets,
            "jitter_buffer_pending_packets": self.jitter_buffer_pending_packets,
            "duplicates_dropped": self.duplicates_dropped,
            "stale_packets_dropped": self.stale_packets_dropped,
            "out_of_order_packets": self.out_of_order_packets,
        }


class JitterBuffer:
    """Reorder-and-delay buffer for incoming audio packets.

    Collects packets and delivers them in sequence-number order after
    a configurable depth of buffering. Handles out-of-order packets,
    duplicates, and gaps (delivering ``None`` for missing packets).

    Args:
        depth: Number of packets to buffer before delivery (default 5,
               which is ~100 ms at 20 ms/packet).

    Example::

        jb = JitterBuffer(depth=5)
        for pkt in jb.push(audio_packet):
            if pkt is None:
                # gap — insert silence
                ...
            else:
                play(pkt.data)
    """

    def __init__(self, depth: int = 5) -> None:
        if depth < 1:
            raise ValueError(f"Jitter buffer depth must be >= 1, got {depth}")
        self._depth = depth
        self._buffer: dict[int, AudioPacket] = {}
        self._next_seq: int | None = None
        self._max_held = depth * 4  # hard cap to prevent memory leak
        self._duplicates_dropped = 0
        self._stale_packets_dropped = 0
        self._gap_count = 0
        self._underrun_count = 0
        self._overrun_count = 0

    @property
    def depth(self) -> int:
        """Configured buffer depth (number of packets)."""
        return self._depth

    @property
    def pending(self) -> int:
        """Number of packets currently held in the buffer."""
        return len(self._buffer)

    @property
    def duplicate_count(self) -> int:
        """Count of duplicate packets dropped."""
        return self._duplicates_dropped

    @property
    def stale_count(self) -> int:
        """Count of stale/old packets dropped."""
        return self._stale_packets_dropped

    @property
    def gap_count(self) -> int:
        """Count of inferred missing packets (gap placeholders)."""
        return self._gap_count

    @property
    def underrun_count(self) -> int:
        """Count of jitter-buffer underrun events."""
        return self._underrun_count

    @property
    def overrun_count(self) -> int:
        """Count of jitter-buffer overrun events."""
        return self._overrun_count

    def push(self, packet: AudioPacket) -> list[AudioPacket | None]:
        """Insert a packet and return any packets ready for delivery.

        Packets are delivered in order. If a gap is detected (missing
        sequence number), ``None`` is yielded in its place.

        Args:
            packet: Incoming audio packet.

        Returns:
            List of packets (or None for gaps) ready for playback.
            May be empty if more buffering is needed.
        """
        seq = packet.send_seq

        # Initialize on first packet
        if self._next_seq is None:
            self._next_seq = seq

        # Ignore duplicates and very old packets
        if seq in self._buffer:
            self._duplicates_dropped += 1
            return []
        # Detect wrap-around: if seq is far behind _next_seq, it's old
        diff = (seq - self._next_seq) & 0xFFFF
        if diff > 0x8000:
            # seq is behind _next_seq (wrapped), ignore
            self._stale_packets_dropped += 1
            return []

        self._buffer[seq] = packet

        # Overflow protection
        if len(self._buffer) > self._max_held:
            self._overrun_count += 1
            return self._flush_all()

        # Only deliver when we have enough buffered
        if len(self._buffer) < self._depth:
            return []

        return self._deliver()

    def flush(self) -> list[AudioPacket | None]:
        """Flush all buffered packets in order (for stream end).

        Returns:
            Remaining packets in order (None for gaps).
        """
        return self._flush_all()

    def _deliver(self) -> list[AudioPacket | None]:
        """Deliver packets starting from _next_seq.

        Delivers as long as the buffer contains enough packets ahead
        of _next_seq to maintain the jitter depth, or the next expected
        packet is available.
        """
        result: list[AudioPacket | None] = []
        assert self._next_seq is not None

        while self._buffer:
            seq = self._next_seq

            if seq in self._buffer:
                result.append(self._buffer.pop(seq))
                self._next_seq = (seq + 1) & 0xFFFF
            else:
                # seq is missing — is it a gap or should we wait?
                # Count how many packets we hold that are ahead of seq
                ahead_count = sum(
                    1 for s in self._buffer if 0 < ((s - seq) & 0xFFFF) < 0x8000
                )
                if ahead_count >= self._depth:
                    # Enough evidence that seq is lost → gap
                    result.append(None)
                    self._gap_count += 1
                    self._next_seq = (seq + 1) & 0xFFFF
                else:
                    # Not enough buffered ahead, wait for more packets
                    self._underrun_count += 1
                    break

        return result

    def _flush_all(self) -> list[AudioPacket | None]:
        """Flush everything, filling gaps with None."""
        result: list[AudioPacket | None] = []
        if not self._buffer:
            return result

        assert self._next_seq is not None
        max_seq = max(self._buffer)
        # Handle wrap: deliver up to max_seq
        while self._next_seq != ((max_seq + 1) & 0xFFFF):
            seq = self._next_seq
            if seq in self._buffer:
                result.append(self._buffer.pop(seq))
            else:
                result.append(None)
                self._gap_count += 1
            self._next_seq = (self._next_seq + 1) & 0xFFFF
            # Safety: prevent infinite loop
            if len(result) > self._max_held + self._depth:
                break

        self._buffer.clear()
        return result


class AudioStream:
    """Manages audio RX/TX on the Icom audio UDP port.

    Uses an :class:`IcomTransport` for the underlying UDP communication
    (discovery, pings, retransmit). Audio-specific packet framing is
    handled here.

    Args:
        transport: Connected IcomTransport for the audio port.

    Example::

        stream = AudioStream(audio_transport)
        await stream.start_rx(my_callback)
        # ... later
        await stream.stop_rx()
    """

    def __init__(self, transport: IcomTransport, jitter_depth: int = 5) -> None:
        self._transport = transport
        self._state: AudioState = AudioState.IDLE
        self._rx_callback: Callable[[AudioPacket | None], None] | None = None
        self._rx_taps: list[Callable[[AudioPacket | None], None]] = []
        self._rx_task: asyncio.Task[None] | None = None
        self._tx_seq: int = 0
        self._jitter_depth = jitter_depth
        self._jitter_buffer: JitterBuffer | None = None
        self._packet_duration_ms = 20.0
        self._rx_packets_received = 0
        self._rx_packets_delivered = 0
        self._rx_packets_lost = 0
        self._tx_packets_sent = 0
        self._rx_jitter_ema_packets = 0.0
        self._rx_jitter_max_packets = 0
        self._rx_underruns = 0
        self._rx_overruns = 0
        self._rx_duplicates_dropped = 0
        self._rx_stale_packets_dropped = 0
        self._rx_out_of_order_packets = 0
        self._rx_last_seq: int | None = None

    def _reset_rx_stats(self) -> None:
        self._rx_packets_received = 0
        self._rx_packets_delivered = 0
        self._rx_packets_lost = 0
        self._rx_jitter_ema_packets = 0.0
        self._rx_jitter_max_packets = 0
        self._rx_underruns = 0
        self._rx_overruns = 0
        self._rx_duplicates_dropped = 0
        self._rx_stale_packets_dropped = 0
        self._rx_out_of_order_packets = 0
        self._rx_last_seq = None

    @property
    def state(self) -> AudioState:
        """Current audio stream state."""
        return self._state

    @property
    def transport(self) -> IcomTransport:
        """Underlying transport."""
        return self._transport

    def _update_rx_order_stats(self, send_seq: int) -> None:
        if self._rx_last_seq is None:
            self._rx_last_seq = send_seq
            return

        step = (send_seq - self._rx_last_seq) & 0xFFFF
        if step == 0:
            return

        if step < 0x8000:
            deviation = abs(step - 1)
            self._rx_jitter_ema_packets = (
                self._rx_jitter_ema_packets * 0.875 + deviation * 0.125
            )
            if deviation > self._rx_jitter_max_packets:
                self._rx_jitter_max_packets = deviation
            if step != 1:
                self._rx_out_of_order_packets += 1
            if self._jitter_buffer is None and step > 1:
                self._rx_packets_lost += step - 1
                self._rx_underruns += step - 1
            self._rx_last_seq = send_seq
            return

        # Packet appears behind the last seen sequence (likely stale/reordered).
        self._rx_out_of_order_packets += 1

    def _sync_jitter_stats(self) -> None:
        if self._jitter_buffer is None:
            return
        self._rx_packets_lost = self._jitter_buffer.gap_count
        self._rx_underruns = self._jitter_buffer.underrun_count
        self._rx_overruns = self._jitter_buffer.overrun_count
        self._rx_duplicates_dropped = self._jitter_buffer.duplicate_count
        self._rx_stale_packets_dropped = self._jitter_buffer.stale_count

    def _build_audio_stats(self) -> AudioStats:
        self._sync_jitter_stats()
        depth = self._jitter_buffer.depth if self._jitter_buffer is not None else 0
        pending = self._jitter_buffer.pending if self._jitter_buffer is not None else 0
        expected_rx = self._rx_packets_delivered + self._rx_packets_lost
        packet_loss_percent = (
            (self._rx_packets_lost / expected_rx) * 100.0 if expected_rx > 0 else 0.0
        )
        estimated_latency_ms = (
            float(max(depth, pending) * self._packet_duration_ms) if depth > 0 else 0.0
        )

        return AudioStats(
            active=self._state != AudioState.IDLE,
            state=self._state,
            rx_packets_received=self._rx_packets_received,
            rx_packets_delivered=self._rx_packets_delivered,
            tx_packets_sent=self._tx_packets_sent,
            packets_lost=self._rx_packets_lost,
            packet_loss_percent=packet_loss_percent,
            reorder_depth_ema_ms=self._rx_jitter_ema_packets * self._packet_duration_ms,
            jitter_max_ms=self._rx_jitter_max_packets * self._packet_duration_ms,
            underrun_count=self._rx_underruns,
            overrun_count=self._rx_overruns,
            estimated_latency_ms=estimated_latency_ms,
            jitter_buffer_depth_packets=depth,
            jitter_buffer_pending_packets=pending,
            duplicates_dropped=self._rx_duplicates_dropped,
            stale_packets_dropped=self._rx_stale_packets_dropped,
            out_of_order_packets=self._rx_out_of_order_packets,
        )

    def get_audio_stats(self) -> dict[str, AudioStatsValue]:
        """Return runtime audio stats for the current stream.

        Metrics and units:

        - ``rx_packets_received`` / ``rx_packets_delivered`` / ``tx_packets_sent``:
          packet counters (>= 0).
        - ``packets_lost``: inferred missing RX packets (>= 0).
        - ``packet_loss_percent``: percentage in [0.0, 100.0].
        - ``reorder_depth_ema_ms`` / ``jitter_max_ms``: reorder-depth EMA and peak
          deviation estimates in ms (>= 0.0). Despite the legacy ``jitter_max_ms``
          name, both are reorder-depth metrics, not RFC 3550 jitter.
        - ``underrun_count`` / ``overrun_count``: jitter-buffer event counters (>= 0).
        - ``estimated_latency_ms``: current buffering latency estimate in ms (>= 0.0).
        - ``jitter_buffer_depth_packets`` / ``jitter_buffer_pending_packets``:
          packet counts (>= 0).
        """
        return self._build_audio_stats().to_dict()

    # ------------------------------------------------------------------
    # RX
    # ------------------------------------------------------------------

    def add_rx_tap(self, callback: Callable[["AudioPacket | None"], None]) -> None:
        """Add an additional RX listener (tap) that receives all audio packets."""
        if callback not in self._rx_taps:
            self._rx_taps.append(callback)

    def remove_rx_tap(self, callback: Callable[["AudioPacket | None"], None]) -> None:
        """Remove an RX tap."""
        try:
            self._rx_taps.remove(callback)
        except ValueError:
            pass

    async def start_rx(
        self,
        callback: Callable[[AudioPacket | None], None],
        *,
        jitter_depth: int | None = None,
    ) -> None:
        """Start receiving audio from the radio.

        Args:
            callback: Called with each decoded :class:`AudioPacket`.
                When jitter buffering is enabled, ``None`` may be passed
                for gap placeholders (missing packets).
            jitter_depth: Override jitter buffer depth (0 to disable).
                Defaults to the value set at construction time.

        Raises:
            RuntimeError: If already receiving or transmitting.
        """
        if self._state != AudioState.IDLE:
            raise RuntimeError(f"Cannot start RX in state {self._state}")

        depth = jitter_depth if jitter_depth is not None else self._jitter_depth
        self._reset_rx_stats()
        self._jitter_buffer = JitterBuffer(depth) if depth > 0 else None
        self._rx_callback = callback
        self._state = AudioState.RECEIVING
        self._rx_task = asyncio.create_task(self._rx_loop())
        logger.info("Audio RX started (jitter_depth=%d)", depth)

    async def stop_rx(self) -> None:
        """Stop receiving audio and flush remaining buffered packets."""
        if self._state != AudioState.RECEIVING:
            return
        self._state = AudioState.IDLE
        if self._rx_task is not None:
            self._rx_task.cancel()
            try:
                await self._rx_task
            except asyncio.CancelledError:
                pass
            self._rx_task = None
        # Flush remaining jitter buffer
        if self._jitter_buffer is not None and self._rx_callback is not None:
            for pkt in self._jitter_buffer.flush():
                if pkt is not None:
                    self._rx_packets_delivered += 1
                self._rx_callback(pkt)
        self._sync_jitter_stats()
        self._jitter_buffer = None
        self._rx_callback = None
        logger.info("Audio RX stopped")

    async def _rx_loop(self) -> None:
        """Background loop that reads audio packets from transport."""
        while self._state in (AudioState.RECEIVING, AudioState.TRANSMITTING):
            try:
                data = await self._transport.receive_packet(timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break

            if len(data) <= AUDIO_HEADER_SIZE:
                continue  # Control/ping packet, not audio

            pkt = parse_audio_packet(data)
            if pkt is not None and (self._rx_callback is not None or self._rx_taps):
                self._rx_packets_received += 1
                self._update_rx_order_stats(pkt.send_seq)
                if self._jitter_buffer is not None:
                    for ready in self._jitter_buffer.push(pkt):
                        if ready is not None:
                            self._rx_packets_delivered += 1
                        if self._rx_callback is not None:
                            self._rx_callback(ready)
                        for tap in self._rx_taps:
                            tap(ready)
                    self._sync_jitter_stats()
                else:
                    self._rx_packets_delivered += 1
                    if self._rx_callback is not None:
                        self._rx_callback(pkt)
                    for tap in self._rx_taps:
                        tap(pkt)

    # ------------------------------------------------------------------
    # TX
    # ------------------------------------------------------------------

    async def start_tx(self) -> None:
        """Start transmitting audio to the radio.

        Can be called while already receiving (full-duplex).

        Raises:
            RuntimeError: If already transmitting.
        """
        if self._state == AudioState.TRANSMITTING:
            raise RuntimeError("Already transmitting")
        self._state = AudioState.TRANSMITTING
        self._tx_seq = 0
        self._tx_packets_sent = 0
        logger.info("Audio TX started")

    async def push_tx(self, opus_data: bytes) -> None:
        """Send an audio frame to the radio.

        Large payloads (e.g. raw PCM) are automatically chunked to fit the
        IC-7610 maximum audio payload size (1364 bytes per UDP packet),
        matching the wfview chunking behaviour.

        Args:
            opus_data: Audio data (Opus-encoded or raw PCM).

        Raises:
            RuntimeError: If not in transmitting state.
        """
        if self._state != AudioState.TRANSMITTING:
            raise RuntimeError(f"Cannot push TX in state {self._state}")

        data = opus_data
        offset = 0
        while offset < len(data):
            chunk = data[offset : offset + MAX_AUDIO_PAYLOAD]
            pkt = build_audio_packet(
                chunk,
                sender_id=self._transport.my_id,
                receiver_id=self._transport.remote_id,
                send_seq=self._tx_seq,
            )
            await self._transport.send_tracked(pkt)
            self._tx_seq = (self._tx_seq + 1) & 0xFFFF
            self._tx_packets_sent += 1
            offset += MAX_AUDIO_PAYLOAD

    async def stop_tx(self) -> None:
        """Stop transmitting audio.

        If RX is still active, state reverts to RECEIVING.
        """
        if self._state != AudioState.TRANSMITTING:
            return
        # If RX loop is still running, go back to RECEIVING
        if self._rx_task is not None and not self._rx_task.done():
            self._state = AudioState.RECEIVING
        else:
            self._state = AudioState.IDLE
        logger.info("Audio TX stopped")


# ------------------------------------------------------------------
# Packet parsing / building (module-level for easy testing)
# ------------------------------------------------------------------


def parse_audio_packet(data: bytes) -> AudioPacket | None:
    """Parse a raw UDP audio packet into an :class:`AudioPacket`.

    Args:
        data: Raw UDP packet bytes (must be > 0x18 bytes).

    Returns:
        Parsed AudioPacket, or None if the packet is too short or
        is a control/retransmit packet (type != DATA).
    """
    if len(data) <= AUDIO_HEADER_SIZE:
        return None

    pkt_type = struct.unpack_from("<H", data, 0x04)[0]
    if pkt_type != PacketType.DATA:
        return None

    ident = struct.unpack_from("<H", data, 0x10)[0]
    send_seq = struct.unpack_from(">H", data, 0x12)[0]
    # datalen at 0x16 is BE, but we use actual remaining bytes
    audio_data = data[AUDIO_HEADER_SIZE:]

    return AudioPacket(ident=ident, send_seq=send_seq, data=audio_data)


def build_audio_packet(
    opus_data: bytes,
    *,
    sender_id: int,
    receiver_id: int,
    send_seq: int,
    ident: int = TX_IDENT,
) -> bytes:
    """Build a raw UDP audio packet from Opus data.

    Args:
        opus_data: Opus-encoded audio frame.
        sender_id: Our connection ID.
        receiver_id: Radio's connection ID.
        send_seq: Audio-level sequence number.
        ident: Audio ident field (default TX_IDENT=0x0080).

    Returns:
        Complete UDP packet bytes ready to send.
    """
    total_len = AUDIO_HEADER_SIZE + len(opus_data)
    pkt = bytearray(total_len)

    struct.pack_into("<I", pkt, 0x00, total_len)  # len (LE)
    struct.pack_into("<H", pkt, 0x04, PacketType.DATA)  # type (LE)
    # seq at 0x06 left as 0 — transport will fill it
    struct.pack_into("<I", pkt, 0x08, sender_id)  # sentid (LE)
    struct.pack_into("<I", pkt, 0x0C, receiver_id)  # rcvdid (LE)
    struct.pack_into("<H", pkt, 0x10, ident)  # ident (LE)
    struct.pack_into(">H", pkt, 0x12, send_seq)  # sendseq (BE)
    # 0x14: unused (stays 0)
    struct.pack_into(">H", pkt, 0x16, len(opus_data))  # datalen (BE)

    pkt[AUDIO_HEADER_SIZE:] = opus_data
    return bytes(pkt)
