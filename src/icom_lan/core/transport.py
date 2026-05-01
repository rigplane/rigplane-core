"""Async UDP transport for the Icom LAN protocol.

Handles connection lifecycle, keep-alive pings, sequence tracking,
and retransmit requests using asyncio.DatagramProtocol.
"""

import asyncio
import logging
import socket
import struct
import time
from collections import OrderedDict
from collections.abc import Callable
from enum import StrEnum
from typing import ClassVar

from icom_lan.core._bounded_queue import BoundedQueue
from icom_lan.core._queue_pressure import PRESSURE_THRESHOLD
from .exceptions import TimeoutError as _TimeoutError
from .types import HEADER_SIZE, PacketType

__all__ = [
    "BUFSIZE",
    "CONTROL_SIZE",
    "ConnectionState",
    "IcomTransport",
    "MAX_MISSING",
    "PACKET_QUEUE_MAXSIZE",
    "PING_SIZE",
    "PRESSURE_THRESHOLD",
]

logger = logging.getLogger(__name__)

CONTROL_SIZE = 0x10
PING_SIZE = 0x15
PING_PERIOD = 0.5  # seconds
IDLE_PERIOD = 0.1
RETRANSMIT_PERIOD = 0.1
DISCOVERY_RETRIES = 10
DISCOVERY_TIMEOUT = 1.0  # seconds per attempt
BUFSIZE = 500
MAX_MISSING = 50
PACKET_QUEUE_MAXSIZE = 4096


class ConnectionState(StrEnum):
    """Transport connection state."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"


class _UdpProtocol(asyncio.DatagramProtocol):
    """Internal asyncio datagram protocol for IcomTransport."""

    def __init__(self, transport_owner: "IcomTransport") -> None:
        self._owner = transport_owner
        # Peer "host:port" captured on connection_made so diagnostic logs
        # can disambiguate which radio port (control/CI-V/audio) is failing.
        self._peer: str = "?"

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        # Note: on macOS/selector loop, _SelectorDatagramTransport does NOT
        # inherit from asyncio.DatagramTransport (CPython quirk), but it
        # has sendto() — so we skip the isinstance check.
        self._owner._udp_transport = transport  # type: ignore[assignment]
        peer = transport.get_extra_info("peername")
        if isinstance(peer, tuple) and len(peer) >= 2:
            self._peer = f"{peer[0]}:{peer[1]}"

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._owner._handle_packet(data)

    def error_received(self, exc: Exception) -> None:
        owner = self._owner
        owner._udp_error_count += 1
        n = owner._udp_error_count
        if n <= 3:
            logger.warning("UDP error [peer=%s] (#%d): %s", self._peer, n, exc)
        elif n % 100 == 0:
            logger.warning(
                "UDP error [peer=%s] (#%d, suppressed 97): %s", self._peer, n, exc
            )

    def connection_lost(self, exc: Exception | None) -> None:
        logger.info("UDP connection lost [peer=%s]: %s", self._peer, exc)


class IcomTransport:
    """Async UDP transport for Icom radio communication.

    Manages the UDP socket, keep-alive pings, sequence numbers,
    and retransmit tracking.

    Attributes:
        state: Current connection state.
        my_id: Local connection identifier.
        remote_id: Remote (radio) connection identifier.
        send_seq: Next outgoing tracked sequence number.
        ping_seq: Next outgoing ping sequence number.
    """

    def __init__(self) -> None:
        self.state: ConnectionState = ConnectionState.DISCONNECTED
        self.my_id: int = 0
        self.remote_id: int = 0
        self.send_seq: int = 0
        self.ping_seq: int = 0
        self.tx_buffer: OrderedDict[int, bytes] = OrderedDict()
        self.rx_last_seq: int | None = None
        self.rx_missing: dict[int, int] = {}  # seq -> retry count
        self._udp_transport: asyncio.DatagramTransport | None = None
        self._ping_task: asyncio.Task[None] | None = None
        self._idle_task: asyncio.Task[None] | None = None
        self._retransmit_task: asyncio.Task[None] | None = None
        self._packet_queue: BoundedQueue[bytes] = BoundedQueue(
            maxsize=PACKET_QUEUE_MAXSIZE
        )
        # Optional callback invoked when queue pressure exceeds 75%.
        # Expected signature: () -> int (returns number of items shed).
        self._scope_shed_callback: Callable[[], int] | None = None
        self._raw_send = self._default_raw_send
        self.rx_packet_count: int = 0  # total packets received (incl. pings)
        self._last_tracked_send: float = 0.0  # monotonic time of last tracked send
        self._udp_error_count: int = 0  # consecutive UDP errors (Broken pipe etc.)
        # Optional fast-path callback for scope data — bypasses packet queue
        self._scope_fast_path: Callable[[bytes], None] | None = None
        self._scope_dropped: int = 0  # scope packets dropped under queue pressure
        # When True, data packets (ptype=0x00) are silently discarded instead
        # of being queued.  Used for the control transport after connection
        # setup completes — the radio keeps sending periodic status packets on
        # the control port, but nobody ever reads the queue after handshake.
        # Without this flag the queue fills up in ~27 minutes (4096 / ~2.5 pkt/s)
        # causing a cascade of eviction warnings and watchdog reconnects.
        self._discard_data_packets: bool = False

    @property
    def queue_pressure(self) -> float:
        """Return packet queue fill ratio (0.0 = empty, 1.0 = full)."""
        q = self._packet_queue
        maxsize = q.maxsize
        if maxsize <= 0:
            return 0.0
        return q.qsize() / maxsize

    def _default_raw_send(self, data: bytes) -> None:
        """Send raw bytes via UDP transport."""
        if self._udp_transport is not None:
            self._udp_transport.sendto(data)

    async def connect(
        self,
        host: str,
        port: int,
        *,
        local_host: str | None = None,
        local_port: int = 0,
        sock: "socket.socket | None" = None,
    ) -> None:
        """Open UDP connection and perform discovery handshake.

        Sends "Are You There" until the radio replies with "I Am Here",
        then sends "Are You Ready" and waits for acknowledgement.

        Args:
            host: Radio IP address or hostname.
            port: Radio control port.
            local_host: Local interface IP to bind to when reserving a port.
                When omitted, the transport keeps the previous wildcard/default
                bind behavior.
            local_port: Local UDP port to bind to (0 = random).
                wfview binds CI-V/audio sockets to the same port sent in
                conninfo so the radio knows where to send data.
            sock: Pre-bound UDP socket to reuse.  When provided the socket
                is connected to *(host, port)*, set non-blocking, and handed
                directly to ``create_datagram_endpoint(sock=…)`` — this
                eliminates the TOCTOU race between port reservation and
                transport bind.  *local_host* / *local_port* are ignored
                when *sock* is given.

        Raises:
            TimeoutError: If the radio does not respond to discovery.
        """
        self.state = ConnectionState.CONNECTING
        loop = asyncio.get_running_loop()
        if sock is not None:
            # Caller reserved this socket earlier; connect + hand off.
            sock.connect((host, port))
            sock.setblocking(False)
            await loop.create_datagram_endpoint(
                lambda: _UdpProtocol(self),
                sock=sock,
            )
        else:
            local_addr = None
            if local_port or local_host:
                local_addr = (local_host or "0.0.0.0", local_port)
            await loop.create_datagram_endpoint(
                lambda: _UdpProtocol(self),
                remote_addr=(host, port),
                local_addr=local_addr,
            )
        # Generate local ID from local address info
        if self._udp_transport is not None:
            info = self._udp_transport.get_extra_info("sockname")
            if info:
                lport = info[1] if isinstance(info, tuple) else 0
                self.my_id = (lport & 0xFFFF) | 0x10000
        logger.info("UDP open to %s:%d, my_id=0x%08X", host, port, self.my_id)

        # Phase 1: Are You There → I Am Here
        await self._discover()

        # Phase 2: Are You Ready
        await self._ready_handshake()

        logger.info("Discovery complete, remote_id=0x%08X", self.remote_id)

    async def reconnect(
        self,
        host: str,
        port: int,
        *,
        local_host: str | None = None,
    ) -> None:
        """Reconnect to a known radio, skipping discovery.

        Reuses the previously learned ``remote_id`` and skips the
        Are-You-There/I-Am-Here exchange.  Falls back to full discovery
        if no ``remote_id`` is cached.
        """
        if self.remote_id == 0:
            # No cached remote_id — do full connect
            await self.connect(host, port, local_host=local_host)
            return

        saved_remote_id = self.remote_id
        self.state = ConnectionState.CONNECTING
        loop = asyncio.get_running_loop()
        local_addr = (local_host, 0) if local_host else None
        await loop.create_datagram_endpoint(
            lambda: _UdpProtocol(self),
            remote_addr=(host, port),
            local_addr=local_addr,
        )
        saved_my_id = self.my_id
        if self._udp_transport is not None:
            info = self._udp_transport.get_extra_info("sockname")
            if info:
                lport = info[1] if isinstance(info, tuple) else 0
                self.my_id = (lport & 0xFFFF) | 0x10000
        # Prefer reusing old my_id — radio may reject login from a new sender_id
        # while previous session is still cached.
        if saved_my_id != 0:
            self.my_id = saved_my_id
        logger.info(
            "UDP reconnect to %s:%d, my_id=0x%08X (reusing remote_id=0x%08X)",
            host,
            port,
            self.my_id,
            saved_remote_id,
        )
        self.remote_id = saved_remote_id

        # Skip discovery, go straight to ready handshake
        await self._ready_handshake()

        logger.info("Reconnect complete, remote_id=0x%08X", self.remote_id)

    async def _discover(self) -> None:
        """Send 'Are You There' and wait for 'I Am Here' to learn remote_id.

        Raises:
            TimeoutError: If radio does not respond after retries.
        """
        for attempt in range(DISCOVERY_RETRIES):
            pkt = self._build_control(ptype=PacketType.ARE_YOU_THERE, seq=0)
            self._raw_send(pkt)
            try:
                resp = await asyncio.wait_for(
                    self._packet_queue.get(), timeout=DISCOVERY_TIMEOUT
                )
            except asyncio.TimeoutError:
                logger.debug(
                    "Are You There attempt %d/%d — no response",
                    attempt + 1,
                    DISCOVERY_RETRIES,
                )
                continue

            if len(resp) >= CONTROL_SIZE:
                ptype = struct.unpack_from("<H", resp, 4)[0]
                if ptype == PacketType.I_AM_HERE:
                    self.remote_id = struct.unpack_from("<I", resp, 8)[0]
                    logger.info(
                        "I Am Here received, remote_id=0x%08X",
                        self.remote_id,
                    )
                    return

        raise _TimeoutError(
            f"Radio did not respond to discovery after {DISCOVERY_RETRIES} attempts"
        )

    async def _ready_handshake(self) -> None:
        """Send 'Are You Ready' and wait for acknowledgement.

        Raises:
            TimeoutError: If radio does not respond.
        """
        pkt = self._build_control(ptype=PacketType.ARE_YOU_READY, seq=0)
        self._raw_send(pkt)

        # Radio may send multiple packets; look for ARE_YOU_READY echo
        for _ in range(5):
            try:
                resp = await asyncio.wait_for(
                    self._packet_queue.get(), timeout=DISCOVERY_TIMEOUT
                )
            except asyncio.TimeoutError:
                break

            if len(resp) >= CONTROL_SIZE:
                ptype = struct.unpack_from("<H", resp, 4)[0]
                if ptype == PacketType.ARE_YOU_READY:
                    logger.info("I Am Ready received")
                    return

        # Some radios don't send an explicit reply; proceed anyway
        logger.warning("No explicit 'I Am Ready' reply, proceeding")

    async def disconnect(self) -> None:
        """Close the UDP connection and stop background tasks."""
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
        if self._idle_task and not self._idle_task.done():
            self._idle_task.cancel()
        if self._retransmit_task and not self._retransmit_task.done():
            self._retransmit_task.cancel()

        if self.state != ConnectionState.DISCONNECTED and self.remote_id:
            pkt = self._build_control(ptype=PacketType.DISCONNECT, seq=0)
            self._raw_send(pkt)

        if self._udp_transport is not None:
            self._udp_transport.close()
            self._udp_transport = None

        self.state = ConnectionState.DISCONNECTED
        logger.info("Disconnected")

    def start_ping_loop(self) -> None:
        """Start the periodic ping task."""
        if self._ping_task is None or self._ping_task.done():
            self._ping_task = asyncio.create_task(self._ping_loop())

    def start_idle_loop(self) -> None:
        """Start periodic idle keepalive task (wfview-style).

        Sends a tracked control packet every IDLE_PERIOD when no other
        tracked packet has been sent recently.  This keeps the radio's
        CI-V/audio session alive.  wfview: idleTimer -> sendControl(true, 0, 0).
        """
        if self._idle_task is None or self._idle_task.done():
            self._idle_task = asyncio.create_task(self._idle_loop())

    def start_retransmit_loop(self) -> None:
        """Start the periodic retransmit request task."""
        if self._retransmit_task is None or self._retransmit_task.done():
            self._retransmit_task = asyncio.create_task(self._retransmit_loop())

    async def send_tracked(self, data: bytes) -> None:
        """Send a packet with sequence tracking.

        The sequence number is written into the packet header at offset 6-7,
        and the packet is buffered for potential retransmission.

        Args:
            data: Packet bytes (header already filled except seq).
        """
        seq = self._next_send_seq()
        pkt = bytearray(data)
        struct.pack_into("<H", pkt, 6, seq)
        pkt_bytes = bytes(pkt)
        self._track_sent(seq, pkt_bytes)
        self._raw_send(pkt_bytes)
        self._last_tracked_send = time.monotonic()

    async def receive_packet(self, timeout: float = 5.0) -> bytes:
        """Wait for the next incoming packet.

        Args:
            timeout: Maximum seconds to wait.

        Returns:
            Raw packet bytes.

        Raises:
            asyncio.TimeoutError: If no packet arrives within timeout.
        """
        return await asyncio.wait_for(self._packet_queue.get(), timeout=timeout)

    # --- Internal helpers ---

    def _next_send_seq(self) -> int:
        """Get and increment the send sequence number (wraps at 0x10000)."""
        seq = self.send_seq
        self.send_seq = (self.send_seq + 1) & 0xFFFF
        return seq

    def _build_control(self, *, ptype: int, seq: int) -> bytes:
        """Build a 0x10-byte control packet."""
        pkt = bytearray(CONTROL_SIZE)
        struct.pack_into("<I", pkt, 0, CONTROL_SIZE)
        struct.pack_into("<H", pkt, 4, ptype)
        struct.pack_into("<H", pkt, 6, seq)
        struct.pack_into("<I", pkt, 8, self.my_id)
        struct.pack_into("<I", pkt, 0x0C, self.remote_id)
        return bytes(pkt)

    def _build_ping(self) -> bytes:
        """Build a 0x15-byte ping request packet."""
        pkt = bytearray(PING_SIZE)
        struct.pack_into("<I", pkt, 0, PING_SIZE)
        struct.pack_into("<H", pkt, 4, PacketType.PING)
        struct.pack_into("<H", pkt, 6, self.ping_seq)
        struct.pack_into("<I", pkt, 8, self.my_id)
        struct.pack_into("<I", pkt, 0x0C, self.remote_id)
        pkt[0x10] = 0x00  # reply = request
        ms = int(time.monotonic() * 1000) & 0xFFFFFFFF
        struct.pack_into("<I", pkt, 0x11, ms)
        return bytes(pkt)

    def _send_ping(self) -> None:
        """Send a single ping packet and increment ping_seq."""
        pkt = self._build_ping()
        self._raw_send(pkt)
        self.ping_seq = (self.ping_seq + 1) & 0xFFFF

    def _track_sent(self, seq: int, data: bytes) -> None:
        """Store a sent packet for potential retransmission."""
        # Mirror wfview behavior: clear tracked TX buffer on sequence rollover.
        if seq == 0:
            self.tx_buffer.clear()

        if seq in self.tx_buffer:
            del self.tx_buffer[seq]

        if len(self.tx_buffer) >= BUFSIZE:
            self.tx_buffer.popitem(last=False)
        self.tx_buffer[seq] = data

    def _record_rx_seq(self, seq: int) -> None:
        """Record a received sequence number and detect gaps."""
        seq &= 0xFFFF
        if seq in self.rx_missing:
            del self.rx_missing[seq]

        if self.rx_last_seq is None:
            self.rx_last_seq = seq
            return

        last_seq = self.rx_last_seq
        delta = (seq - last_seq) & 0xFFFF

        if delta == 0:
            # Duplicate packet.
            return

        if 1 <= delta <= 0x7FFF:
            # Forward progress in uint16 sequence space.
            if delta > MAX_MISSING:
                logger.warning("Large seq gap: %d -> %d, resetting", last_seq, seq)
                self.rx_missing.clear()
                self.rx_last_seq = seq
                return

            for offset in range(1, delta):
                missing = (last_seq + offset) & 0xFFFF
                if missing not in self.rx_missing:
                    self.rx_missing[missing] = 0
            self.rx_last_seq = seq

    def _build_retransmit_requests(self) -> list[bytes]:
        """Build retransmit request packets for missing sequences."""
        if not self.rx_missing:
            return []

        seqs = list(self.rx_missing.keys())

        if len(seqs) == 1:
            # Single: use control packet with seq field
            return [self._build_control(ptype=0x01, seq=seqs[0])]

        # Multiple: control header + pairs of (seq, seq) for each missing
        pkt = bytearray(CONTROL_SIZE + 4 * len(seqs))
        struct.pack_into("<I", pkt, 0, len(pkt))
        struct.pack_into("<H", pkt, 4, 0x01)  # type = CONTROL
        struct.pack_into("<H", pkt, 6, 0x00)
        struct.pack_into("<I", pkt, 8, self.my_id)
        struct.pack_into("<I", pkt, 0x0C, self.remote_id)
        offset = CONTROL_SIZE
        for s in seqs:
            struct.pack_into("<H", pkt, offset, s)
            struct.pack_into("<H", pkt, offset + 2, s)
            offset += 4
        return [bytes(pkt)]

    def _handle_packet(self, data: bytes) -> None:
        """Process an incoming UDP packet via dispatch table."""
        if len(data) < HEADER_SIZE:
            return
        self.rx_packet_count += 1

        length = struct.unpack_from("<I", data, 0)[0]
        ptype = struct.unpack_from("<H", data, 4)[0]
        seq = struct.unpack_from("<H", data, 6)[0]
        sender_id = struct.unpack_from("<I", data, 8)[0]

        handler = self._PACKET_HANDLERS.get(ptype)
        if handler is not None and handler(self, data, length, seq, sender_id):
            return
        self._handle_data_packet(data, ptype, seq, sender_id)

    def _handle_retransmit_packet(
        self, data: bytes, length: int, seq: int, sender_id: int
    ) -> bool:
        """Handle ptype=0x01 retransmit requests (single or multi)."""
        if length == CONTROL_SIZE and len(data) == CONTROL_SIZE:
            # Single retransmit request from radio
            if seq in self.tx_buffer:
                logger.debug("Retransmitting seq 0x%04X", seq)
                self._raw_send(self.tx_buffer[seq])
            return True

        # Multi retransmit request
        for i in range(CONTROL_SIZE, len(data), 2):
            if i + 2 <= len(data):
                rseq = struct.unpack_from("<H", data, i)[0]
                if rseq in self.tx_buffer:
                    self._raw_send(self.tx_buffer[rseq])
        return True

    def _handle_ping_packet(
        self, data: bytes, length: int, seq: int, sender_id: int
    ) -> bool:
        """Handle ptype=PING packets (request or reply).

        Returns True if handled.  A PING-typed packet with the wrong size
        falls through to the data-packet path (preserves prior semantics).
        """
        if len(data) != PING_SIZE:
            return False

        reply_flag = data[0x10]
        if reply_flag == 0x00:
            # Ping request from radio — send reply
            reply = bytearray(PING_SIZE)
            struct.pack_into("<I", reply, 0, PING_SIZE)
            struct.pack_into("<H", reply, 4, PacketType.PING)
            struct.pack_into("<H", reply, 6, seq)
            struct.pack_into("<I", reply, 8, self.my_id)
            struct.pack_into("<I", reply, 0x0C, self.remote_id)
            reply[0x10] = 0x01  # reply flag
            reply[0x11:0x15] = data[0x11:0x15]  # echo time
            self._raw_send(bytes(reply))
        elif reply_flag == 0x01:
            # Response to our ping
            if seq == self.ping_seq - 1 or seq == self.ping_seq:
                pass  # Latency measurement could go here
        return True

    def _handle_data_packet(
        self, data: bytes, ptype: int, seq: int, sender_id: int
    ) -> None:
        """Handle generic data packets: scope fast-path, queueing, overflow."""
        # Track sequence for data packets
        if ptype == 0x00 and seq != 0:
            self._record_rx_seq(seq)

        # Discard data packets when queue consumer is absent (control transport
        # after setup).  Pings and retransmits are already handled above.
        if self._discard_data_packets and ptype == 0x00:
            return

        # Update remote_id if needed
        if self.remote_id == 0 and sender_id != 0:
            self.remote_id = sender_id

        # Detect scope-data packets: CI-V frame cmd=0x27, sub=0x00.
        # Layout after UDP header (0x10): FE FE DST SRC CMD SUB ...
        is_scope = (
            len(data) > HEADER_SIZE + 5
            and data[HEADER_SIZE] == 0xFE
            and data[HEADER_SIZE + 1] == 0xFE
            and data[HEADER_SIZE + 4] == 0x27
            and data[HEADER_SIZE + 5] == 0x00
        )

        # Fast-path: route scope data directly to callback, bypassing the queue.
        # Scope frames are high-volume (~225 pkt/sec) and would otherwise fill
        # the queue, starving CI-V control commands.
        if is_scope and self._scope_fast_path is not None:
            self._scope_fast_path(data)
            return

        # Queue for consumer with bounded capacity.
        # On overflow: drop scope packets first (visual only), never CI-V control.
        if self._packet_queue.full():
            if is_scope:
                # Drop this scope packet — spectrum glitch is acceptable
                self._scope_dropped += 1
                if self._scope_dropped % 100 == 1:
                    logger.warning(
                        "Packet-queue full: dropping scope packet "
                        "(total_dropped=%d, queue_size=%d)",
                        self._scope_dropped,
                        self._packet_queue.qsize(),
                    )
                return

            # Non-scope packet (CI-V control) and queue is full:
            # evict oldest packet to make room — control must get through.
            dropped: bytes | None = None
            try:
                dropped = self._packet_queue.get_nowait()
            except asyncio.QueueEmpty:
                dropped = None

            dropped_seq: int | None = None
            if dropped is not None and len(dropped) >= HEADER_SIZE:
                dropped_seq = struct.unpack_from("<H", dropped, 6)[0]

            logger.warning(
                (
                    "Packet-queue overflow: evicting for CI-V control "
                    "(dropped_seq=%s, new_seq=0x%04X, ptype=0x%04X, "
                    "sender_id=0x%08X, queue_size=%d, maxsize=%d, rx_count=%d)"
                ),
                (f"0x{dropped_seq:04X}" if isinstance(dropped_seq, int) else "n/a"),
                seq,
                ptype,
                sender_id,
                self._packet_queue.qsize(),
                self._packet_queue.maxsize,
                self.rx_packet_count,
            )

        try:
            self._packet_queue.put_nowait(data)
            # Proactive shedding: when queue fills past threshold, ask the scope
            # assembler to drop incomplete frames before we hit hard overflow.
            cb = self._scope_shed_callback
            if cb is not None and self.queue_pressure > PRESSURE_THRESHOLD:
                shed = cb()
                if shed:
                    logger.warning(
                        "Queue pressure %.0f%%: shed %d incomplete scope frame(s)",
                        self.queue_pressure * 100,
                        shed,
                    )
        except asyncio.QueueFull:
            # Race: queue became full between check and put.
            # Drop the incoming packet only if it's scope.
            if is_scope:
                self._scope_dropped += 1
                return
            logger.warning(
                (
                    "Packet-queue overflow (second-chance): dropping newest packet "
                    "(seq=0x%04X, ptype=0x%04X, sender_id=0x%08X, maxsize=%d)"
                ),
                seq,
                ptype,
                sender_id,
                self._packet_queue.maxsize,
            )

    # Dispatch table: ptype → handler returning True if consumed.
    # Falls through to ``_handle_data_packet`` when no entry matches or a
    # handler returns False (e.g. PING with non-canonical size).
    _PACKET_HANDLERS: ClassVar[
        dict[int, Callable[["IcomTransport", bytes, int, int, int], bool]]
    ] = {
        0x01: _handle_retransmit_packet,
        PacketType.PING: _handle_ping_packet,
    }

    async def _ping_loop(self) -> None:
        """Background task: send pings every PING_PERIOD."""
        try:
            while self.state != ConnectionState.DISCONNECTED:
                self._send_ping()
                await asyncio.sleep(PING_PERIOD)
        except asyncio.CancelledError:
            pass

    async def _idle_loop(self) -> None:
        """Background task: send idle keepalive when no tracked packet sent recently.

        Reference: wfview icomudpbase.cpp — idleTimer fires every IDLE_PERIOD (100ms).
        If triggered, sends sendControl(tracked=true, type=0, seq=0) to keep the
        radio session alive.  The timer is reset each time sendTrackedPacket() runs.
        """
        idle_count = 0
        try:
            while self.state != ConnectionState.DISCONNECTED:
                await asyncio.sleep(IDLE_PERIOD)
                elapsed = time.monotonic() - self._last_tracked_send
                if elapsed >= IDLE_PERIOD:
                    # Send tracked idle control packet (type=0x00)
                    pkt = self._build_control(ptype=0x00, seq=0)
                    await self.send_tracked(pkt)
                    idle_count += 1
                    if idle_count % 100 == 1:
                        logger.debug(
                            "idle-keepalive: sent #%d (send_seq=%d, rx_count=%d)",
                            idle_count,
                            self.send_seq,
                            self.rx_packet_count,
                        )
        except asyncio.CancelledError:
            pass

    async def _retransmit_loop(self) -> None:
        """Background task: send retransmit requests periodically."""
        try:
            while self.state != ConnectionState.DISCONNECTED:
                await asyncio.sleep(RETRANSMIT_PERIOD)
                pkts = self._build_retransmit_requests()
                for pkt in pkts:
                    self._raw_send(pkt)
                # Increment retry counters, drop after 4 tries
                to_delete = []
                for s, count in self.rx_missing.items():
                    if count >= 4:
                        to_delete.append(s)
                    else:
                        self.rx_missing[s] = count + 1
                for s in to_delete:
                    del self.rx_missing[s]
        except asyncio.CancelledError:
            pass
