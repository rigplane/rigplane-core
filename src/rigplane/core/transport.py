"""Async UDP transport for the Icom LAN protocol.

Handles connection lifecycle, keep-alive pings, sequence tracking,
and retransmit requests using asyncio.DatagramProtocol.
"""

import asyncio
import logging
import socket
import struct
import sys
import time
from collections import OrderedDict, deque
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from rigplane.core._bounded_queue import BoundedQueue
from rigplane.core._queue_pressure import PRESSURE_THRESHOLD
from .exceptions import CommandError
from .exceptions import DiscoveryTimeout as _DiscoveryTimeout
from .types import HEADER_SIZE, PacketType

__all__ = [
    "BUFSIZE",
    "CONTROL_SIZE",
    "ConnectionState",
    "IcomCivTransport",
    "IcomTransport",
    "MAX_MISSING",
    "PACKET_QUEUE_MAXSIZE",
    "PING_SIZE",
    "PRESSURE_THRESHOLD",
    "REORDER_WINDOW",
    "RxOrderStats",
]

logger = logging.getLogger(__name__)

CONTROL_SIZE = 0x10
CONNINFO_NOTICE_SIZE = 0x90
TOKEN_PACKET_SIZE = 0x40
PING_SIZE = 0x15
PING_PERIOD = 0.5  # seconds
IDLE_PERIOD = 0.1
RETRANSMIT_PERIOD = 0.1
DISCOVERY_RETRIES = 10
DISCOVERY_TIMEOUT = 1.0  # seconds per attempt
BUFSIZE = 500
MAX_MISSING = 50
# Ordered delivery (MOR-4205): the most sequence numbers that may lie between
# the oldest undelivered number and the newest one received. Past it the
# oldest gap is given up even while its retransmit is still being asked for,
# so the hold stays bounded. At the CI-V port's ~120-225 packets/s it is
# about 1-2 s, longer than the retransmit budget that normally gives up first.
REORDER_WINDOW = 256
PACKET_QUEUE_MAXSIZE = 4096
# Non-scope overflow must not log once per packet. The first eviction logs
# immediately; while evictions continue, at most one summary per interval.
_OVERFLOW_LOG_INTERVAL = 5.0


@dataclass(frozen=True, slots=True)
class RxOrderStats:
    """Inbound sequence-order counters of one transport (MOR-4205).

    Counted only where the transport delivers in order (``ordered_ingress``:
    ``IcomCivTransport``), since the transport's creation.

    Attributes:
        reordered: Packets that arrived after a later-numbered one and were
            still delivered in their place: a delayed datagram, or the
            radio's resend after a retransmit request.
        duplicate: Packets dropped because their number was already
            delivered or is already held.
        lost: Numbers delivery moved past without their packet: the
            retransmit budget ran out or the hold reached ``REORDER_WINDOW``.
        late: Packets that arrived after their number was given up; dropped,
            because later packets were already delivered.
        reanchored: Times the delivery anchor moved onto the live stream
            after a numbering jump: a run of more than ``REORDER_WINDOW``
            consecutive packets all behind the anchor, or a forward frontier
            delta greater than ``MAX_MISSING`` (MOR-4416). One event,
            however many packets the run held — the run's packets up to the
            window still count as ``duplicate``/``late`` as before.
    """

    reordered: int = 0
    duplicate: int = 0
    lost: int = 0
    late: int = 0
    reanchored: int = 0


# One held inbound data packet, as ``_route_data_packet`` takes it:
# (data, ptype, seq, sender_id).
_HeldPacket = tuple[bytes, int, int, int]


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
            self._owner._udp_peer_addr = (str(peer[0]), int(peer[1]))
            self._peer = f"{peer[0]}:{peer[1]}"

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self._owner._handle_packet(data)

    def error_received(self, exc: Exception) -> None:
        owner = self._owner
        owner._udp_error_count += 1
        owner._udp_error_total += 1
        n = owner._udp_error_total
        if n <= 3:
            logger.warning("UDP error [peer=%s] (#%d): %s", self._peer, n, exc)
        elif n % 100 == 0:
            # MOR-1789: exact count of occurrences not logged since the
            # previous emitted line (last full line #3, or the previous
            # hundredth) — 96 at n=100, 99 at every later hundredth.
            prev_logged = 3 if n == 100 else n - 100
            logger.warning(
                "UDP error [peer=%s] (#%d, suppressed %d): %s",
                self._peer,
                n,
                n - prev_logged - 1,
                exc,
            )

    def connection_lost(self, exc: Exception | None) -> None:
        logger.info("UDP connection lost [peer=%s]: %s", self._peer, exc)
        close_event = self._owner._udp_close_event
        if close_event is not None:
            close_event.set()


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
        ordered_ingress: Whether data packets reach the upper layer in the
            order the radio sent them (MOR-4209). False here: UDP may deliver
            a delayed, duplicated or retransmitted datagram out of order, and
            this transport passes packets on as they arrive. True on
            ``IcomCivTransport``: it holds every numbered data packet (type 0)
            that arrives ahead of a missing one, the retransmit loop asks the
            radio to resend the missing one, and delivery follows the sequence
            number; a number whose retransmit budget runs out is given up and
            delivery moves on; duplicates are dropped (MOR-4205,
            ``rx_order_stats``).
    """

    ordered_ingress: ClassVar[bool] = False

    def __init__(self) -> None:
        self.state: ConnectionState = ConnectionState.DISCONNECTED
        self.my_id: int = 0
        self.remote_id: int = 0
        self.send_seq: int = 0
        self.ping_seq: int = 0
        self.tx_buffer: OrderedDict[int, bytes] = OrderedDict()
        self._tx_guards: dict[int, Callable[[], bool]] = {}
        self.rx_last_seq: int | None = None
        self.rx_missing: dict[int, int] = {}  # seq -> retry count
        # Ordered delivery (``ordered_ingress``): the next number to deliver,
        # the packets held ahead of it, and the numbers recently given up.
        self._rx_next: int | None = None
        self._rx_held: dict[int, _HeldPacket] = {}
        self._rx_given_up: deque[int] = deque(maxlen=REORDER_WINDOW)
        self._rx_reordered = 0
        self._rx_duplicate = 0
        self._rx_lost = 0
        self._rx_late = 0
        self._rx_reanchored = 0
        # Consecutive numbered packets seen behind the delivery anchor; a run
        # past ``REORDER_WINDOW`` re-anchors (MOR-4416, ``_accept_in_order``).
        self._rx_behind_run = 0
        self._udp_transport: asyncio.DatagramTransport | None = None
        self._udp_peer_addr: tuple[str, int] | None = None
        self._udp_close_event: asyncio.Event | None = None
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
        # Consecutive UDP errors (Broken pipe etc.); reset to 0 on any
        # inbound datagram by _handle_packet.
        self._udp_error_count: int = 0
        # Lifetime UDP error count, used only to throttle the warning log.
        self._udp_error_total: int = 0
        self._scope_dropped: int = 0  # scope packets dropped under queue pressure
        # Non-scope evictions since the last overflow warning, and when that
        # warning was logged. Zero means no warning has been logged yet.
        self._overflow_evicted: int = 0
        self._overflow_logged_at: float = 0.0
        # When True, data packets (ptype=0x00) are silently discarded instead
        # of being queued.  Used for the control transport after connection
        # setup completes — the radio keeps sending periodic status packets on
        # the control port, but nobody ever reads the queue after handshake.
        # Without this flag the queue fills up in ~27 minutes (4096 / ~2.5 pkt/s)
        # causing a cascade of eviction warnings and watchdog reconnects.
        self._discard_data_packets: bool = False
        # Optional callback for a 0x90 conninfo notice (ptype 0x00). Runs
        # before the discard branch so a "radio free" notice can start
        # recovery.
        self._conninfo_notice_callback: Callable[[bytes], None] | None = None
        # Optional callback for a 0x40 token packet (ptype 0x00), also routed
        # before the discard branch.
        self._token_reply_callback: Callable[[bytes], None] | None = None

    @property
    def rx_order_stats(self) -> RxOrderStats:
        """Inbound sequence-order counters since this transport was created."""
        return RxOrderStats(
            reordered=self._rx_reordered,
            duplicate=self._rx_duplicate,
            lost=self._rx_lost,
            late=self._rx_late,
            reanchored=self._rx_reanchored,
        )

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
            if sys.platform == "win32" and self._udp_peer_addr is not None:
                self._udp_transport.sendto(data, self._udp_peer_addr)
                return
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
            DiscoveryTimeout: If the radio does not respond to discovery
                (a TimeoutError subclass).
        """
        # Start the sequence numbers at 0 and drop the buffered packets and
        # gap tracking of any earlier use of this instance. After a teardown
        # that kept the radio session, ControlPhaseRuntime.soft_reconnect
        # (runtime/_control_phase.py) sets the CI-V send_seq back to the number
        # recorded then; tests/test_civ_seq_continuity.py pins that.
        self.send_seq = 0
        self.ping_seq = 0
        self.tx_buffer.clear()
        self._tx_guards.clear()
        self.rx_last_seq = None
        self.rx_missing.clear()
        self._rx_next = None
        self._rx_held.clear()
        self._rx_given_up.clear()
        self._rx_behind_run = 0
        self.remote_id = 0
        while True:
            try:
                self._packet_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
        self.state = ConnectionState.CONNECTING
        loop = asyncio.get_running_loop()
        self._udp_close_event = asyncio.Event()
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
        self._udp_close_event = asyncio.Event()
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
            DiscoveryTimeout: If radio does not respond after retries (a
                TimeoutError subclass; the message is unchanged).
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

        raise _DiscoveryTimeout(
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
        """Close the UDP connection and stop background tasks.

        Keepalive tasks are cancelled AND awaited so that when this
        coroutine returns nothing from this transport is left pending in
        the event loop and a follow-up ``connect()`` on the same instance
        restarts the loops from a clean slate (MOR-595).  The UDP socket
        close is likewise awaited (bounded at 1 s): asyncio closes the socket
        in a deferred callback, right after ``connection_lost``, and returning
        earlier let a reconnect rebind the same local port and fail with
        EADDRINUSE.
        Idempotent — safe to call again after a partial start.
        A sent DISCONNECT also forgets the learned ids (MOR-3807).
        """
        tasks = [
            task
            for task in (self._ping_task, self._idle_task, self._retransmit_task)
            if task is not None and task is not asyncio.current_task()
        ]
        for task in tasks:
            if not task.done():
                task.cancel()

        send_error: BaseException | None = None
        try:
            if self.state != ConnectionState.DISCONNECTED and self.remote_id:
                pkt = self._build_control(ptype=PacketType.DISCONNECT, seq=0)
                self._raw_send(pkt)
                # MOR-3807: DISCONNECT makes the radio forget this session,
                # so forget the learned ids with it. The next connect then
                # falls back from reconnect() to a full handshake (a zero
                # remote_id selects connect()). Paths that deliberately keep
                # the radio session (soft_reconnect's CI-V rebuild) never
                # disconnect the control transport, so their reuse is kept.
                self.remote_id = 0
                self.my_id = 0
        except BaseException as exc:
            # Defer, rather than suppress, the initiating error until retirement.
            send_error = exc

        # Initiate socket retirement in this task, before the first yield.
        udp, self._udp_transport = self._udp_transport, None
        close_event = self._udp_close_event if udp is not None else None
        close_error: BaseException | None = None
        if udp is not None:
            try:
                udp.close()
            except BaseException as exc:
                close_error = exc

        async def retire() -> None:
            try:
                if close_event is not None and close_error is None:
                    try:
                        await asyncio.wait_for(close_event.wait(), 1.0)
                    except asyncio.TimeoutError:
                        logger.debug(
                            "UDP socket close not confirmed within 1s; continuing"
                        )
            finally:
                self.state = ConnectionState.DISCONNECTED
                for task in tasks:
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
                    except Exception:
                        logger.debug(
                            "keepalive task raised during disconnect", exc_info=True
                        )
            if close_error is not None:
                raise close_error
            logger.info("Disconnected")

        # Caller cancellation must not cancel a keepalive's own finalizer.
        retirement = asyncio.create_task(retire(), name="icom-transport-disconnect")
        cancelled: asyncio.CancelledError | None = None
        try:
            while not retirement.done():
                try:
                    await asyncio.shield(retirement)
                except asyncio.CancelledError as exc:
                    if retirement.cancelled():
                        raise
                    if cancelled is None:
                        cancelled = exc
            retirement.result()
        except BaseException as cleanup_error:
            if send_error is not None:
                raise send_error from cleanup_error
            raise
        if send_error is not None:
            if cancelled is not None:
                raise send_error from cancelled
            raise send_error
        if cancelled is not None:
            raise cancelled

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

    async def send_tracked(
        self, data: bytes, *, is_current: Callable[[], bool] | None = None
    ) -> None:
        """Send a packet with sequence tracking.

        The sequence number is written into the packet header at offset 6-7,
        and the packet is buffered for potential retransmission.

        Args:
            data: Packet bytes (header already filled except seq).
            is_current: Optional caller-owned synchronous, nonblocking,
                read-only predicate, checked before sending and retransmitting.
                False raises CommandError before an initial send; an initial
                predicate exception propagates. A false or raising guard
                answers the retransmit request with an idle packet carrying
                that sequence instead of the stored packet.

        This guards local submission only, not delivery or radio execution.
        """
        if is_current is not None and not is_current():
            raise CommandError("Tracked write is no longer current")
        seq = self._next_send_seq()
        pkt = bytearray(data)
        struct.pack_into("<H", pkt, 6, seq)
        pkt_bytes = bytes(pkt)
        self._track_sent(seq, pkt_bytes, is_current=is_current)
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
        pkt: bytes
        if timeout is not None and timeout <= 0:
            # Same TimeoutError outcome as the old wait_for(timeout <= 0),
            # which cancelled the inner get() before it could run and left
            # any queued packet for the next consumer (asyncio.timeout(0)
            # would instead let the synchronous get() consume and return
            # it — MOR-3575 re-review). That wait_for suspended while
            # cancelling its helper, so a cancel requested before this
            # call was delivered; sleep(0) is the checkpoint that keeps
            # delivering it (MOR-3575 review round 2).
            await asyncio.sleep(0)
            raise asyncio.TimeoutError
        # asyncio.timeout runs in this task (unlike 3.11 wait_for's helper
        # task, which could swallow a delivered cancellation when the inner
        # get() completed in the same pass — the stop_pump deadlock of
        # MOR-3575), and it never reinterprets a cancellation requested
        # before this receive, so a consumer doing cleanup after catching
        # CancelledError still receives its packet.
        async with asyncio.timeout(timeout):
            pkt = await self._packet_queue.get()
        return pkt

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

    def _track_sent(
        self, seq: int, data: bytes, *, is_current: Callable[[], bool] | None = None
    ) -> None:
        """Store a sent packet for potential retransmission."""
        # Mirror wfview behavior: clear tracked TX buffer on sequence rollover.
        if seq == 0:
            self.tx_buffer.clear()
            self._tx_guards.clear()

        if seq in self.tx_buffer:
            del self.tx_buffer[seq]
        self._tx_guards.pop(seq, None)

        if len(self.tx_buffer) >= BUFSIZE:
            evicted, _ = self.tx_buffer.popitem(last=False)
            self._tx_guards.pop(evicted, None)
        self.tx_buffer[seq] = data
        if is_current is not None:
            self._tx_guards[seq] = is_current

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
                self._reanchor_rx(seq, forward_jump=delta)
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
        # Any inbound datagram proves the path works, so the error run is over.
        self._udp_error_count = 0
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

    def _retransmit(self, seq: int) -> None:
        seq &= 0xFFFF
        data = self.tx_buffer.get(seq)
        if data is None:
            logger.debug("retransmit #%d not buffered; answering with idle", seq)
            self._raw_send(self._build_control(ptype=0x00, seq=seq))
            return
        is_current = self._tx_guards.get(seq)
        if is_current is not None:
            try:
                if not is_current():
                    logger.debug("retransmit #%d stale; answering with idle", seq)
                    self._raw_send(self._build_control(ptype=0x00, seq=seq))
                    return
            except Exception:
                logger.debug(
                    "retransmit #%d guard failed; answering with idle",
                    seq,
                    exc_info=True,
                )
                self._raw_send(self._build_control(ptype=0x00, seq=seq))
                return
        self._raw_send(data)

    def _handle_retransmit_packet(
        self, data: bytes, length: int, seq: int, sender_id: int
    ) -> bool:
        """Handle ptype=0x01 retransmit requests (single or multi)."""
        if length == CONTROL_SIZE and len(data) == CONTROL_SIZE:
            # Single retransmit request from radio
            self._retransmit(seq)
            return True

        # Multi retransmit request: the payload past the header holds
        # consecutive 4-byte (first, last) inclusive ranges in the 16-bit
        # sequence space. A trailing partial pair carries no range.
        end = CONTROL_SIZE + (len(data) - CONTROL_SIZE) // 4 * 4
        for i in range(CONTROL_SIZE, end, 4):
            first, last = struct.unpack_from("<HH", data, i)
            span = (last - first) & 0xFFFF
            if span >= 0x8000:
                logger.debug(
                    "retransmit range %d..%d backward; ignoring",
                    first,
                    last,
                )
                continue
            count = min(span + 1, MAX_MISSING)
            if count < span + 1:
                logger.debug(
                    "retransmit range %d..%d wider than %d; serving first %d, "
                    "rest left for the radio's next request",
                    first,
                    last,
                    MAX_MISSING,
                    count,
                )
            for offset in range(count):
                self._retransmit((first + offset) & 0xFFFF)
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
        """Track the sequence of a data packet, then route it (in order)."""
        # Number 0 is untracked where packets pass on as they arrive; where
        # they are ordered it is the number after 0xFFFF like any other.
        if ptype == 0x00 and (seq != 0 or self.ordered_ingress):
            last = self.rx_last_seq
            arrived_after_later = (
                last is not None and 0 < ((last - seq) & 0xFFFF) < 0x8000
            )
            self._record_rx_seq(seq)
            if self.ordered_ingress:
                self._accept_in_order(
                    (data, ptype, seq, sender_id), arrived_after_later
                )
                return
        self._route_data_packet(data, ptype, seq, sender_id)

    def _accept_in_order(self, packet: _HeldPacket, arrived_after_later: bool) -> None:
        """Hold one numbered data packet and deliver what is now in order."""
        seq = packet[2]
        if self._rx_next is None:
            nxt = seq
            logger.info(
                "CI-V ingress anchored at the first numbered packet seq=%d", seq
            )
        else:
            nxt = self._rx_next
        behind = (nxt - seq) & 0xFFFF
        if 0 < behind < 0x8000:
            # Older than the next number, however far: a duplicate, or late
            # after its number was given up. One such packet — even far
            # behind — is never a reason to move the anchor: a stray old
            # datagram must never be redelivered
            # (test_an_old_duplicate_far_behind_is_never_redelivered). But a
            # RUN of them past the window is no reorder: the anchor itself is
            # stale. A first packet from the previous session's numbering
            # (the radio may track numbering per client across processes,
            # MOR-3418) or a radio that restarted its numbering on the live
            # link (MOR-4416) otherwise drops every fresh packet until the
            # counter climbs past the anchor — past the CI-V data watchdog.
            self._rx_behind_run += 1
            if self._rx_behind_run <= REORDER_WINDOW:
                if seq in self._rx_given_up:
                    self._rx_late += 1
                else:
                    self._rx_duplicate += 1
                return
            self._reanchor_rx(seq)
            nxt = seq
            arrived_after_later = False  # the frontier just reset
        else:
            self._rx_behind_run = 0
        if seq in self._rx_held:
            self._rx_duplicate += 1
            return
        if arrived_after_later:
            self._rx_reordered += 1
        self._rx_held[seq] = packet
        self._rx_next = nxt
        self._release_in_order()

    def _reanchor_rx(self, seq: int, *, forward_jump: int = 0) -> None:
        """Move the delivery anchor onto the live stream after a numbering jump.

        The held packets and the missing set belong to the dead numbering
        epoch: the radio never resumes them, and giving them up one by one
        would count thousands of ``lost``. They drop uncounted — the event
        itself is counted once (``reanchored``) and logged once, with the old
        anchor, the new sequence and the run length or forward delta. The
        frontier (``rx_last_seq``) restarts at the re-anchor packet, so a gap
        right after the jump is tracked and asked for like any other.
        """
        old_next = self._rx_next
        run = self._rx_behind_run
        dropped_held = len(self._rx_held)
        self._rx_held.clear()
        self.rx_missing.clear()
        self._rx_given_up.clear()
        self._rx_behind_run = 0
        self.rx_last_seq = seq
        self._rx_next = seq
        self._rx_reanchored += 1
        logger.warning(
            "CI-V ingress re-anchored after a numbering jump "
            "(old_next=%s new_seq=%d behind_run=%d forward_jump=%d dropped_held=%d)",
            old_next,
            seq,
            run,
            forward_jump,
            dropped_held,
        )

    def _release_in_order(self) -> None:
        """Deliver held packets from the next number on, giving up spent gaps.

        The gap at the next number waits while the retransmit loop still asks
        the radio for it (it is in ``rx_missing``) and the hold is within
        ``REORDER_WINDOW``; otherwise it is given up together with the other
        missing numbers before the next held packet.
        """
        nxt = self._rx_next
        held = self._rx_held
        if nxt is None:
            return
        while held:
            packet = held.pop(nxt, None)
            if packet is not None:
                self._route_data_packet(*packet)
                nxt = (nxt + 1) & 0xFFFF
                continue
            newest = nxt if self.rx_last_seq is None else self.rx_last_seq
            if nxt in self.rx_missing and (newest - nxt) & 0xFFFF <= REORDER_WINDOW:
                break
            first_held = min(held, key=lambda s: (s - nxt) & 0xFFFF)
            skipped = (first_held - nxt) & 0xFFFF
            for offset in range(skipped):
                self.rx_missing.pop((nxt + offset) & 0xFFFF, None)
            for offset in range(max(0, skipped - REORDER_WINDOW), skipped):
                self._rx_given_up.append((nxt + offset) & 0xFFFF)
            self._rx_lost += skipped
            nxt = first_held
        self._rx_next = nxt

    def _route_data_packet(
        self, data: bytes, ptype: int, seq: int, sender_id: int
    ) -> None:
        """Route one data packet: callbacks, scope fast-path, queue, overflow."""
        # A conninfo notice is routed before the discard branch: after setup
        # the control queue has no consumer, but a "radio free" notice is the
        # signal to recover.
        if (
            ptype == 0x00
            and len(data) == CONNINFO_NOTICE_SIZE
            and self._conninfo_notice_callback is not None
        ):
            self._conninfo_notice_callback(data)
            return
        if (
            ptype == 0x00
            and len(data) == TOKEN_PACKET_SIZE
            and self._token_reply_callback is not None
        ):
            self._token_reply_callback(data)
            return

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

            self._note_overflow(
                dropped_seq=dropped_seq,
                seq=seq,
                ptype=ptype,
                sender_id=sender_id,
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
            self._note_overflow(
                dropped_seq=None,
                seq=seq,
                ptype=ptype,
                sender_id=sender_id,
                second_chance=True,
            )

    def _note_overflow(
        self,
        *,
        dropped_seq: int | None,
        seq: int,
        ptype: int,
        sender_id: int,
        second_chance: bool = False,
    ) -> None:
        """Count a non-scope eviction and log at most once per interval.

        The first eviction logs immediately. While evictions continue, later
        lines are summaries: how many were evicted since the previous line,
        and the running total. Eviction itself is unchanged.
        """
        self._overflow_evicted += 1
        now = time.monotonic()
        first = self._overflow_logged_at == 0.0
        if not first and now - self._overflow_logged_at < _OVERFLOW_LOG_INTERVAL:
            return
        since_last = 1 if first else self._overflow_evicted
        kind = "second-chance" if second_chance else "evicting for CI-V control"
        seq_text = f"0x{dropped_seq:04X}" if isinstance(dropped_seq, int) else "n/a"
        logger.warning(
            (
                "Packet-queue overflow: %s "
                "(dropped_seq=%s, new_seq=0x%04X, ptype=0x%04X, "
                "sender_id=0x%08X, queue_size=%d, maxsize=%d, rx_count=%d, "
                "evicted %d since last, total=%d)"
            ),
            kind,
            seq_text,
            seq,
            ptype,
            sender_id,
            self._packet_queue.qsize(),
            self._packet_queue.maxsize,
            self.rx_packet_count,
            since_last,
            self._overflow_evicted,
        )
        self._overflow_logged_at = now
        self._overflow_evicted = 0

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
                if to_delete and self._rx_held:
                    # A spent number no longer holds back what came after it.
                    self._release_in_order()
        except asyncio.CancelledError:
            pass


class IcomCivTransport(IcomTransport):
    """The LAN CI-V port's transport: delivers in the radio's order (MOR-4205).

    Only the CI-V stream needs the order: a CI-V answer retires the requests
    sent before it (``CivRequestTracker``, MOR-4209). The control port (login,
    token, conninfo notices, whose header numbers the radio need not keep in
    one sequence) and the audio port (``LanAudioStream`` orders its packets in
    its jitter buffer, and holding audio for the retransmit budget would add
    that wait to the playout) keep arrival order.
    """

    ordered_ingress: ClassVar[bool] = True
