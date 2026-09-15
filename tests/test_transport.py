"""Tests for IcomTransport — packet handling, ping, retransmit, sequence tracking."""

import asyncio
import logging
import struct
from unittest.mock import AsyncMock, patch

import pytest

from rigplane.transport import (
    CONTROL_SIZE,
    PING_SIZE,
    ConnectionState,
    IcomTransport,
    PACKET_QUEUE_MAXSIZE,
    PRESSURE_THRESHOLD,
)
from rigplane.core.transport import _UdpProtocol
from rigplane.core.exceptions import CommandError
from rigplane.types import HEADER_SIZE, PacketType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_control(
    ptype: int, seq: int = 0, sender_id: int = 0xAABBCCDD, receiver_id: int = 0
) -> bytes:
    """Build a 0x10-byte control packet."""
    pkt = bytearray(CONTROL_SIZE)
    struct.pack_into("<I", pkt, 0, CONTROL_SIZE)
    struct.pack_into("<H", pkt, 4, ptype)
    struct.pack_into("<H", pkt, 6, seq)
    struct.pack_into("<I", pkt, 8, sender_id)
    struct.pack_into("<I", pkt, 0x0C, receiver_id)
    return bytes(pkt)


def _build_ping(
    seq: int = 0, sender_id: int = 0xAABBCCDD, receiver_id: int = 0, reply: int = 0
) -> bytes:
    """Build a 0x15-byte ping packet."""
    pkt = bytearray(PING_SIZE)
    struct.pack_into("<I", pkt, 0, PING_SIZE)
    struct.pack_into("<H", pkt, 4, PacketType.PING)
    struct.pack_into("<H", pkt, 6, seq)
    struct.pack_into("<I", pkt, 8, sender_id)
    struct.pack_into("<I", pkt, 0x0C, receiver_id)
    pkt[0x10] = reply
    struct.pack_into("<I", pkt, 0x11, 12345)  # timestamp
    return bytes(pkt)


def _build_data_packet(
    seq: int = 1, sender_id: int = 0xAABBCCDD, payload: bytes = b"\x00" * 8
) -> bytes:
    """Build a data packet (type=0x00) with payload."""
    total = CONTROL_SIZE + len(payload)
    pkt = bytearray(total)
    struct.pack_into("<I", pkt, 0, total)
    struct.pack_into("<H", pkt, 4, 0x00)  # DATA
    struct.pack_into("<H", pkt, 6, seq)
    struct.pack_into("<I", pkt, 8, sender_id)
    struct.pack_into("<I", pkt, 0x0C, 0)
    pkt[CONTROL_SIZE:] = payload
    return bytes(pkt)


@pytest.fixture
def transport() -> IcomTransport:
    t = IcomTransport()
    t.my_id = 0x00010001
    t.remote_id = 0xAABBCCDD
    return t


class _FakeDatagramTransport:
    def __init__(
        self,
        sockname: tuple[str, int],
        peername: tuple[str, int] | None = None,
    ) -> None:
        self._sockname = sockname
        self._peername = peername
        self.sent: list[tuple[bytes, tuple[str, int] | None]] = []

    def get_extra_info(self, name: str):
        if name == "sockname":
            return self._sockname
        if name == "peername":
            return self._peername
        return None

    def sendto(self, data: bytes, addr: tuple[str, int] | None = None) -> None:
        self.sent.append((data, addr))


class _FakeLoop:
    def __init__(self, sockname: tuple[str, int]) -> None:
        self.sockname = sockname
        self.calls: list[dict[str, object]] = []

    async def create_datagram_endpoint(
        self,
        protocol_factory,
        *,
        remote_addr=None,
        local_addr=None,
        sock=None,
    ):
        transport = _FakeDatagramTransport(self.sockname, remote_addr)
        protocol = protocol_factory()
        protocol.connection_made(transport)
        call: dict[str, object] = {
            "remote_addr": remote_addr,
            "local_addr": local_addr,
        }
        if sock is not None:
            call["sock"] = sock
        self.calls.append(call)
        return transport, protocol


# ---------------------------------------------------------------------------
# Connection state
# ---------------------------------------------------------------------------


class TestConnectionState:
    def test_initial_state(self) -> None:
        t = IcomTransport()
        assert t.state == ConnectionState.DISCONNECTED
        assert t.my_id == 0
        assert t.remote_id == 0

    def test_state_enum_values(self) -> None:
        assert ConnectionState.DISCONNECTED == "disconnected"
        assert ConnectionState.CONNECTING == "connecting"
        assert ConnectionState.CONNECTED == "connected"

    def test_raw_send_uses_peer_address_on_windows_datagram_transport(self) -> None:
        t = IcomTransport()
        peer = ("192.168.55.40", 50002)
        udp = _FakeDatagramTransport(("10.211.55.4", 60538), peer)
        t._udp_transport = udp  # type: ignore[assignment]
        t._udp_peer_addr = peer

        with patch("rigplane.transport.sys.platform", "win32"):
            t._default_raw_send(b"are-you-there")

        assert udp.sent == [(b"are-you-there", peer)]

    @pytest.mark.asyncio
    async def test_connect_binds_to_specific_local_host_and_port(self) -> None:
        t = IcomTransport()
        loop = _FakeLoop(("192.168.2.194", 50002))

        with (
            patch("rigplane.transport.asyncio.get_running_loop", return_value=loop),
            patch.object(t, "_discover", new=AsyncMock()),
            patch.object(t, "_ready_handshake", new=AsyncMock()),
        ):
            await t.connect(
                "192.168.2.1",
                50001,
                local_host="192.168.2.194",
                local_port=50002,
            )

        assert loop.calls == [
            {
                "remote_addr": ("192.168.2.1", 50001),
                "local_addr": ("192.168.2.194", 50002),
            }
        ]

    @pytest.mark.asyncio
    async def test_connect_with_prebound_socket(self) -> None:
        """When sock= is provided, the socket is connected and passed through."""
        import socket as _socket

        t = IcomTransport()
        loop = _FakeLoop(("192.168.2.194", 50002))

        sock = _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM)
        sock.bind(("0.0.0.0", 0))

        with (
            patch("rigplane.transport.asyncio.get_running_loop", return_value=loop),
            patch.object(t, "_discover", new=AsyncMock()),
            patch.object(t, "_ready_handshake", new=AsyncMock()),
        ):
            await t.connect("192.168.2.1", 50001, sock=sock)

        # Should use sock= path, not remote_addr/local_addr
        assert len(loop.calls) == 1
        call = loop.calls[0]
        assert call["remote_addr"] is None
        assert call["local_addr"] is None
        assert call["sock"] is sock

        # Verify socket was connected and set non-blocking before handoff
        assert sock.getblocking() is False
        assert sock.getpeername() == ("192.168.2.1", 50001)

    @pytest.mark.asyncio
    async def test_reconnect_binds_to_specific_local_host(self) -> None:
        t = IcomTransport()
        t.remote_id = 0xAABBCCDD
        t.my_id = 0x0001C352
        loop = _FakeLoop(("192.168.2.194", 50001))

        with (
            patch("rigplane.transport.asyncio.get_running_loop", return_value=loop),
            patch.object(t, "_ready_handshake", new=AsyncMock()),
        ):
            await t.reconnect(
                "192.168.2.1",
                50001,
                local_host="192.168.2.194",
            )

        assert loop.calls == [
            {
                "remote_addr": ("192.168.2.1", 50001),
                "local_addr": ("192.168.2.194", 0),
            }
        ]


# ---------------------------------------------------------------------------
# Sequence numbers
# ---------------------------------------------------------------------------


class TestSequenceNumbers:
    def test_next_send_seq(self, transport: IcomTransport) -> None:
        assert transport._next_send_seq() == 0
        assert transport._next_send_seq() == 1
        assert transport._next_send_seq() == 2

    def test_seq_wraps(self, transport: IcomTransport) -> None:
        transport.send_seq = 0xFFFF
        assert transport._next_send_seq() == 0xFFFF
        assert transport.send_seq == 0  # wrapped


# ---------------------------------------------------------------------------
# Packet building
# ---------------------------------------------------------------------------


class TestPacketBuilding:
    def test_build_control(self, transport: IcomTransport) -> None:
        pkt = transport._build_control(ptype=PacketType.ARE_YOU_THERE, seq=0)
        assert len(pkt) == CONTROL_SIZE
        length = struct.unpack_from("<I", pkt, 0)[0]
        assert length == CONTROL_SIZE
        ptype = struct.unpack_from("<H", pkt, 4)[0]
        assert ptype == PacketType.ARE_YOU_THERE
        my_id = struct.unpack_from("<I", pkt, 8)[0]
        assert my_id == transport.my_id

    def test_build_ping(self, transport: IcomTransport) -> None:
        pkt = transport._build_ping()
        assert len(pkt) == PING_SIZE
        ptype = struct.unpack_from("<H", pkt, 4)[0]
        assert ptype == PacketType.PING
        assert pkt[0x10] == 0x00  # request, not reply

    def test_send_ping_increments_seq(self, transport: IcomTransport) -> None:
        sent = []
        transport._raw_send = lambda data: sent.append(data)
        assert transport.ping_seq == 0
        transport._send_ping()
        assert transport.ping_seq == 1
        transport._send_ping()
        assert transport.ping_seq == 2
        assert len(sent) == 2


# ---------------------------------------------------------------------------
# Tracking sent packets
# ---------------------------------------------------------------------------


class TestTrackSent:
    def test_track_sent(self, transport: IcomTransport) -> None:
        transport._track_sent(0, b"packet0")
        transport._track_sent(1, b"packet1")
        assert transport.tx_buffer[0] == b"packet0"
        assert transport.tx_buffer[1] == b"packet1"

    def test_track_evicts_oldest(self, transport: IcomTransport) -> None:
        from rigplane.transport import BUFSIZE

        for i in range(BUFSIZE + 10):
            transport._track_sent(i, f"pkt{i}".encode())
        assert len(transport.tx_buffer) <= BUFSIZE
        # Oldest should be evicted
        assert 0 not in transport.tx_buffer

    def test_track_evicts_by_send_order_across_rollover(
        self, transport: IcomTransport
    ) -> None:
        from rigplane.transport import BUFSIZE

        start = 0xFFF0
        for i in range(BUFSIZE):
            seq = (start + i) & 0xFFFF
            transport._track_sent(seq, f"pkt{i}".encode())

        # Ensure wrapped low keys exist in the buffer.
        assert 0 in transport.tx_buffer

        first_inserted = start
        transport._track_sent((start + BUFSIZE) & 0xFFFF, b"new")

        # FIFO eviction should remove the first inserted sequence, not min(seq).
        assert first_inserted not in transport.tx_buffer
        assert 0 in transport.tx_buffer

    @pytest.mark.asyncio
    async def test_send_tracked(self, transport: IcomTransport) -> None:
        sent = []
        transport._raw_send = lambda data: sent.append(data)
        pkt = bytearray(CONTROL_SIZE)
        struct.pack_into("<I", pkt, 0, CONTROL_SIZE)
        await transport.send_tracked(bytes(pkt))
        assert len(sent) == 1
        # Check seq was written
        seq = struct.unpack_from("<H", sent[0], 6)[0]
        assert seq == 0
        assert 0 in transport.tx_buffer


# ---------------------------------------------------------------------------
# RX sequence tracking and gap detection
# ---------------------------------------------------------------------------


class TestTrackedWriteGuard:
    @staticmethod
    def _request(*seqs: int) -> bytes:
        if len(seqs) == 1:
            return _build_control(ptype=0x01, seq=seqs[0])
        packet = bytearray(_build_control(ptype=0x01))
        packet.extend(struct.pack(f"<{len(seqs)}H", *seqs))
        struct.pack_into("<I", packet, 0, len(packet))
        return bytes(packet)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("raises", [False, True])
    async def test_initial_suppression_has_no_send_side_effects(
        self, transport: IcomTransport, raises: bool
    ) -> None:
        sent = []
        transport._raw_send = sent.append
        transport.send_seq = 7
        transport._track_sent(6, b"previous")
        previous_time = transport._last_tracked_send
        error = RuntimeError("guard unavailable")

        def is_current() -> bool:
            if raises:
                raise error
            return False

        expected = RuntimeError if raises else CommandError
        with pytest.raises(expected) as caught:
            await transport.send_tracked(_build_data_packet(), is_current=is_current)
        if raises:
            assert caught.value is error
        assert sent == []
        assert transport.send_seq == 7
        assert transport.tx_buffer == {6: b"previous"}
        assert transport._last_tracked_send == previous_time

    @pytest.mark.asyncio
    async def test_current_send_and_replay_do_not_yield_before_raw_send(
        self, transport: IcomTransport
    ) -> None:
        trace = []

        def is_current() -> bool:
            trace.append("guard")
            return True

        transport._raw_send = lambda data: trace.append("write")
        loop = asyncio.get_running_loop()
        callback = loop.call_soon(trace.append, "yielded")
        try:
            assert (
                await transport.send_tracked(
                    _build_data_packet(), is_current=is_current
                )
                is None
            )
            transport._handle_packet(self._request(0))
            assert trace == ["guard", "write", "guard", "write"]
        finally:
            callback.cancel()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("multi", [False, True])
    async def test_stale_on_replay_after_current_off_is_suppressed(
        self, transport: IcomTransport, multi: bool
    ) -> None:
        sent = []
        current = True
        transport._raw_send = sent.append
        await transport.send_tracked(
            _build_data_packet(payload=b"ON"), is_current=lambda: current
        )
        current = False
        await transport.send_tracked(
            _build_data_packet(payload=b"OFF"), is_current=lambda: True
        )
        off = sent[-1]
        transport._handle_packet(self._request(0, 1) if multi else self._request(0))
        assert [packet[CONTROL_SIZE:] for packet in sent] == (
            [b"ON", b"OFF", b"OFF"] if multi else [b"ON", b"OFF"]
        )
        assert sent[-1] == off

    @pytest.mark.asyncio
    async def test_multi_replay_rechecks_each_entry(
        self, transport: IcomTransport
    ) -> None:
        current = True
        transport._raw_send = lambda data: None
        for payload in (b"first", b"second"):
            await transport.send_tracked(
                _build_data_packet(payload=payload), is_current=lambda: current
            )
        sent = []

        def write(data: bytes) -> None:
            nonlocal current
            sent.append(data)
            current = False

        transport._raw_send = write
        transport._handle_packet(self._request(0, 1))
        assert [packet[CONTROL_SIZE:] for packet in sent] == [b"first"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("multi", [False, True])
    async def test_replay_guard_exception_is_suppressed(
        self, transport: IcomTransport, multi: bool
    ) -> None:
        sent = []
        broken = False

        def is_current() -> bool:
            if broken:
                raise RuntimeError("guard unavailable")
            return True

        transport._raw_send = sent.append
        await transport.send_tracked(_build_data_packet(), is_current=is_current)
        await transport.send_tracked(_build_data_packet(payload=b"unguarded"))
        broken = True
        sent.clear()
        transport._handle_packet(self._request(0, 1) if multi else self._request(0))
        assert [packet[CONTROL_SIZE:] for packet in sent] == (
            [b"unguarded"] if multi else []
        )

    def test_guard_metadata_follows_fifo_eviction(
        self, transport: IcomTransport
    ) -> None:
        from rigplane.transport import BUFSIZE

        for seq in range(1, BUFSIZE + 2):
            transport._track_sent(seq, b"packet", is_current=lambda: True)
        assert set(transport._tx_guards) == set(transport.tx_buffer)
        assert len(transport._tx_guards) == BUFSIZE
        assert 1 not in transport._tx_guards

    def test_guard_metadata_clears_on_rollover(self, transport: IcomTransport) -> None:
        transport._track_sent(0xFFFF, b"old", is_current=lambda: False)
        transport._track_sent(0, b"new")
        assert transport.tx_buffer == {0: b"new"}
        assert transport._tx_guards == {}

    def test_duplicate_sequence_replaces_guard_and_keeps_bytes_compatible(
        self, transport: IcomTransport
    ) -> None:
        sent = []
        transport._raw_send = sent.append
        transport._track_sent(7, b"old", is_current=lambda: False)
        transport._track_sent(7, b"current", is_current=lambda: True)
        transport._handle_packet(self._request(7))
        assert sent == [b"current"]
        transport._track_sent(7, b"unguarded")
        transport._handle_packet(self._request(7))
        assert sent == [b"current", b"unguarded"]
        assert transport.tx_buffer == {7: b"unguarded"}
        assert transport._tx_guards == {}


class TestRxSequence:
    def test_first_packet(self, transport: IcomTransport) -> None:
        transport._record_rx_seq(1)
        assert transport.rx_last_seq == 1
        assert len(transport.rx_missing) == 0

    def test_sequential(self, transport: IcomTransport) -> None:
        for i in range(1, 5):
            transport._record_rx_seq(i)
        assert transport.rx_last_seq == 4
        assert len(transport.rx_missing) == 0

    def test_gap_detected(self, transport: IcomTransport) -> None:
        transport._record_rx_seq(1)
        transport._record_rx_seq(5)  # gap: 2, 3, 4
        assert transport.rx_last_seq == 5
        assert set(transport.rx_missing.keys()) == {2, 3, 4}

    def test_gap_filled(self, transport: IcomTransport) -> None:
        transport._record_rx_seq(1)
        transport._record_rx_seq(4)
        assert 2 in transport.rx_missing
        transport._record_rx_seq(2)
        assert 2 not in transport.rx_missing
        assert 3 in transport.rx_missing

    def test_large_gap_resets(self, transport: IcomTransport) -> None:
        from rigplane.transport import MAX_MISSING

        transport._record_rx_seq(1)
        transport._record_rx_seq(1 + MAX_MISSING + 10)
        assert len(transport.rx_missing) == 0

    def test_wraparound_progression(self, transport: IcomTransport) -> None:
        for seq in (0xFFFE, 0xFFFF, 0x0000, 0x0001):
            transport._record_rx_seq(seq)
        assert transport.rx_last_seq == 1
        assert len(transport.rx_missing) == 0

    def test_wraparound_gap_detected(self, transport: IcomTransport) -> None:
        transport._record_rx_seq(0xFFFE)
        transport._record_rx_seq(0x0001)
        assert transport.rx_last_seq == 1
        assert set(transport.rx_missing.keys()) == {0xFFFF, 0x0000}

    def test_old_packet_does_not_rewind_last_seq(
        self, transport: IcomTransport
    ) -> None:
        transport._record_rx_seq(2)
        transport._record_rx_seq(1)  # old/out-of-order relative to last_seq=2
        assert transport.rx_last_seq == 2


# ---------------------------------------------------------------------------
# Retransmit request building
# ---------------------------------------------------------------------------


class TestRetransmitRequests:
    def test_no_missing(self, transport: IcomTransport) -> None:
        assert transport._build_retransmit_requests() == []

    def test_single_missing(self, transport: IcomTransport) -> None:
        transport.rx_missing[5] = 0
        pkts = transport._build_retransmit_requests()
        assert len(pkts) == 1
        assert len(pkts[0]) == CONTROL_SIZE
        ptype = struct.unpack_from("<H", pkts[0], 4)[0]
        assert ptype == 0x01
        seq = struct.unpack_from("<H", pkts[0], 6)[0]
        assert seq == 5

    def test_multiple_missing(self, transport: IcomTransport) -> None:
        transport.rx_missing[5] = 0
        transport.rx_missing[7] = 0
        pkts = transport._build_retransmit_requests()
        assert len(pkts) == 1
        assert len(pkts[0]) == CONTROL_SIZE + 8  # 2 * 4 bytes


# ---------------------------------------------------------------------------
# _handle_packet
# ---------------------------------------------------------------------------


class TestHandlePacket:
    def test_too_short_ignored(self, transport: IcomTransport) -> None:
        transport._handle_packet(b"\x00" * (HEADER_SIZE - 1))
        assert transport._packet_queue.empty()

    def test_data_packet_queued(self, transport: IcomTransport) -> None:
        pkt = _build_data_packet(seq=1)
        transport._handle_packet(pkt)
        assert not transport._packet_queue.empty()

    def test_data_packet_records_seq(self, transport: IcomTransport) -> None:
        pkt = _build_data_packet(seq=5)
        transport._handle_packet(pkt)
        assert transport.rx_last_seq == 5

    def test_ping_request_replied(self, transport: IcomTransport) -> None:
        sent = []
        transport._raw_send = lambda data: sent.append(data)
        pkt = _build_ping(seq=42, reply=0)
        transport._handle_packet(pkt)
        assert len(sent) == 1
        reply = sent[0]
        assert len(reply) == PING_SIZE
        assert reply[0x10] == 0x01  # reply flag
        reply_seq = struct.unpack_from("<H", reply, 6)[0]
        assert reply_seq == 42

    def test_ping_reply_not_replied(self, transport: IcomTransport) -> None:
        sent = []
        transport._raw_send = lambda data: sent.append(data)
        transport.ping_seq = 5
        pkt = _build_ping(seq=4, reply=1)  # reply to our ping #4
        transport._handle_packet(pkt)
        assert len(sent) == 0  # should not send anything
        assert transport._packet_queue.empty()  # not queued

    def test_retransmit_request_single(self, transport: IcomTransport) -> None:
        sent = []
        transport._raw_send = lambda data: sent.append(data)
        transport.tx_buffer[3] = b"original_packet"
        req = _build_control(ptype=0x01, seq=3)
        transport._handle_packet(req)
        assert len(sent) == 1
        assert sent[0] == b"original_packet"

    def test_retransmit_request_missing_seq(self, transport: IcomTransport) -> None:
        sent = []
        transport._raw_send = lambda data: sent.append(data)
        # seq not in tx_buffer
        req = _build_control(ptype=0x01, seq=99)
        transport._handle_packet(req)
        assert len(sent) == 0

    def test_retransmit_multi(self, transport: IcomTransport) -> None:
        sent = []
        transport._raw_send = lambda data: sent.append(data)
        transport.tx_buffer[5] = b"pkt5"
        transport.tx_buffer[7] = b"pkt7"
        # Multi retransmit: longer than CONTROL_SIZE, type=0x01
        pkt = bytearray(CONTROL_SIZE + 4)
        struct.pack_into("<I", pkt, 0, CONTROL_SIZE + 4)
        struct.pack_into("<H", pkt, 4, 0x01)
        struct.pack_into("<H", pkt, 6, 0)
        struct.pack_into("<I", pkt, 8, 0xAABBCCDD)
        struct.pack_into("<I", pkt, 0x0C, 0)
        # Two seq entries
        struct.pack_into("<H", pkt, CONTROL_SIZE, 5)
        struct.pack_into("<H", pkt, CONTROL_SIZE + 2, 7)
        transport._handle_packet(bytes(pkt))
        assert len(sent) == 2

    def test_remote_id_learned(self) -> None:
        t = IcomTransport()
        t.my_id = 0x10001
        assert t.remote_id == 0
        pkt = _build_data_packet(seq=1, sender_id=0xDEAD)
        t._handle_packet(pkt)
        assert t.remote_id == 0xDEAD

    def test_control_type_queued(self, transport: IcomTransport) -> None:
        """Non-retransmit control packets (like I_AM_HERE) get queued."""
        pkt = _build_control(ptype=PacketType.I_AM_HERE, seq=0)
        transport._handle_packet(pkt)
        assert not transport._packet_queue.empty()


# ---------------------------------------------------------------------------
# Packet queue capacity / overflow
# ---------------------------------------------------------------------------


class TestPacketQueueOverflow:
    def test_queue_is_bounded(self, transport: IcomTransport) -> None:
        # Fill queue to maxsize
        for seq in range(1, PACKET_QUEUE_MAXSIZE + 1):
            pkt = _build_data_packet(seq=seq)
            transport._packet_queue.put_nowait(pkt)

        assert transport._packet_queue.qsize() == PACKET_QUEUE_MAXSIZE

        # Incoming packet when full should not grow queue beyond maxsize
        extra_pkt = _build_data_packet(seq=PACKET_QUEUE_MAXSIZE + 1)
        transport._handle_packet(extra_pkt)

        assert transport._packet_queue.qsize() == PACKET_QUEUE_MAXSIZE

        # Oldest packet should have been dropped; newest should be present.
        seen_seqs: set[int] = set()
        while not transport._packet_queue.empty():
            pkt = transport._packet_queue.get_nowait()
            seq = struct.unpack_from("<H", pkt, 6)[0]
            seen_seqs.add(seq)

        assert 1 not in seen_seqs
        assert PACKET_QUEUE_MAXSIZE + 1 in seen_seqs

    def test_overflow_logged_as_warning(
        self, transport: IcomTransport, caplog: pytest.LogCaptureFixture
    ) -> None:
        for seq in range(1, PACKET_QUEUE_MAXSIZE + 1):
            pkt = _build_data_packet(seq=seq)
            transport._packet_queue.put_nowait(pkt)

        extra_pkt = _build_data_packet(seq=PACKET_QUEUE_MAXSIZE + 1)

        with caplog.at_level(logging.WARNING):
            transport._handle_packet(extra_pkt)

        warnings = [rec for rec in caplog.records if rec.levelno == logging.WARNING]
        assert warnings, "Expected at least one WARNING log for queue overflow"
        # Ensure log mentions queue and overflow in some form
        joined = " ".join(rec.getMessage() for rec in warnings)
        assert "queue" in joined.lower()
        assert "overflow" in joined.lower()


# ---------------------------------------------------------------------------
# receive_packet
# ---------------------------------------------------------------------------


class TestReceivePacket:
    @pytest.mark.asyncio
    async def test_receive_returns_queued(self, transport: IcomTransport) -> None:
        transport._packet_queue.put_nowait(b"hello")
        data = await transport.receive_packet(timeout=1.0)
        assert data == b"hello"

    @pytest.mark.asyncio
    async def test_receive_timeout(self, transport: IcomTransport) -> None:
        with pytest.raises(asyncio.TimeoutError):
            await transport.receive_packet(timeout=0.05)


# ---------------------------------------------------------------------------
# Disconnect
# ---------------------------------------------------------------------------


class TestDisconnect:
    @pytest.mark.asyncio
    async def test_disconnect_sends_disconnect_pkt(
        self, transport: IcomTransport
    ) -> None:
        sent = []
        transport._raw_send = lambda data: sent.append(data)
        transport.state = ConnectionState.CONNECTED
        await transport.disconnect()
        assert transport.state == ConnectionState.DISCONNECTED
        # Should have sent a disconnect packet
        assert len(sent) == 1
        ptype = struct.unpack_from("<H", sent[0], 4)[0]
        assert ptype == PacketType.DISCONNECT

    @pytest.mark.asyncio
    async def test_disconnect_already_disconnected(self) -> None:
        t = IcomTransport()
        await t.disconnect()  # should not raise
        assert t.state == ConnectionState.DISCONNECTED

    @pytest.mark.asyncio
    async def test_disconnect_cancels_tasks(self, transport: IcomTransport) -> None:
        sent = []
        transport._raw_send = lambda data: sent.append(data)
        transport.state = ConnectionState.CONNECTED

        # Create fake tasks
        async def never_end():
            await asyncio.sleep(999)

        transport._ping_task = asyncio.create_task(never_end())
        transport._retransmit_task = asyncio.create_task(never_end())

        await transport.disconnect()
        # Allow cancellation to propagate
        await asyncio.sleep(0)
        assert transport._ping_task.cancelled()
        assert transport._retransmit_task.cancelled()


# ---------------------------------------------------------------------------
# Ping loop
# ---------------------------------------------------------------------------


class TestPingLoop:
    @pytest.mark.asyncio
    async def test_ping_loop_sends_pings(self, transport: IcomTransport) -> None:
        sent = []
        transport._raw_send = lambda data: sent.append(data)
        transport.state = ConnectionState.CONNECTED
        transport.start_ping_loop()
        await asyncio.sleep(0.6)  # enough for 1 ping
        transport.state = ConnectionState.DISCONNECTED
        transport._ping_task.cancel()
        try:
            await transport._ping_task
        except asyncio.CancelledError:
            pass
        assert len(sent) >= 1


# ---------------------------------------------------------------------------
# Retransmit loop
# ---------------------------------------------------------------------------


class TestRetransmitLoop:
    @pytest.mark.asyncio
    async def test_retransmit_loop_cleans_old(self, transport: IcomTransport) -> None:
        sent = []
        transport._raw_send = lambda data: sent.append(data)
        transport.state = ConnectionState.CONNECTED
        transport.rx_missing[10] = (
            3  # already at retry 3, next loop will bump to 4 and delete
        )
        transport.start_retransmit_loop()
        await asyncio.sleep(0.5)  # allow retransmit loop time to process
        transport.state = ConnectionState.DISCONNECTED
        transport._retransmit_task.cancel()
        try:
            await transport._retransmit_task
        except asyncio.CancelledError:
            pass
        # seq 10 should have been removed after 4 retries
        assert 10 not in transport.rx_missing


# ---------------------------------------------------------------------------
# Default raw send
# ---------------------------------------------------------------------------


class TestDefaultRawSend:
    def test_no_transport_noop(self) -> None:
        t = IcomTransport()
        t._default_raw_send(b"test")  # should not raise


# ---------------------------------------------------------------------------
# Queue pressure
# ---------------------------------------------------------------------------


class TestQueuePressure:
    def test_empty_queue_returns_zero(self) -> None:
        t = IcomTransport()
        assert t.queue_pressure == 0.0

    def test_correct_ratio_after_adding_items(self) -> None:
        t = IcomTransport()
        count = 100
        for _ in range(count):
            t._packet_queue.put_nowait(b"\x00")
        expected = count / PACKET_QUEUE_MAXSIZE
        assert t.queue_pressure == pytest.approx(expected)

    def test_full_queue_returns_one(self) -> None:
        t = IcomTransport()
        for _ in range(PACKET_QUEUE_MAXSIZE):
            t._packet_queue.put_nowait(b"\x00")
        assert t.queue_pressure == pytest.approx(1.0)

    def test_pressure_threshold_value(self) -> None:
        assert PRESSURE_THRESHOLD == 0.7


# ---------------------------------------------------------------------------
# UDP error throttling (MOR-1789)
# ---------------------------------------------------------------------------


class TestUdpErrorThrottling:
    """Throttled UDP error warnings must report the exact number of
    suppressed occurrences — the count of errors not logged since the
    previous emitted line — not a hardcoded literal."""

    def test_suppressed_count_is_computed_exactly(
        self, transport: IcomTransport, caplog: pytest.LogCaptureFixture
    ) -> None:
        protocol = _UdpProtocol(transport)

        with caplog.at_level(logging.WARNING, logger="rigplane.core.transport"):
            for _ in range(250):
                protocol.error_received(OSError("Broken pipe"))

        records = [
            rec
            for rec in caplog.records
            if rec.levelno == logging.WARNING and "UDP error" in rec.message
        ]
        # Cadence unchanged: first three in full, then every hundredth.
        assert len(records) == 5
        assert "(#1)" in records[0].message
        assert "(#2)" in records[1].message
        assert "(#3)" in records[2].message
        # The n=100 line accounts for 4..99 not logged: 96 suppressed.
        assert "(#100, suppressed 96)" in records[3].message
        # The n=200 line accounts for 101..199 not logged: 99 suppressed.
        assert "(#200, suppressed 99)" in records[4].message
