"""Retransmit-gap fix (MOR-3358): unservable requests get an idle reply.

When the radio asks for a sequence the client no longer holds (evicted past
BUFSIZE, cleared at rollover, stale guard, failed guard), IcomTransport
answers with an untracked idle control packet carrying that sequence instead
of silence. Multi-request payloads are consecutive 4-byte (first, last)
inclusive ranges in the 16-bit sequence space.
"""

import struct

import pytest

from rigplane.core.transport import MAX_MISSING
from rigplane.transport import CONTROL_SIZE, IcomTransport


def _build_control(ptype: int, seq: int = 0) -> bytes:
    pkt = bytearray(CONTROL_SIZE)
    struct.pack_into("<I", pkt, 0, CONTROL_SIZE)
    struct.pack_into("<H", pkt, 4, ptype)
    struct.pack_into("<H", pkt, 6, seq)
    return bytes(pkt)


def _build_range_request(*pairs: tuple[int, int], extra: bytes = b"") -> bytes:
    """Build a multi retransmit request from (first, last) pairs plus a tail."""
    pkt = bytearray(CONTROL_SIZE)
    struct.pack_into("<I", pkt, 0, 0)  # fixed up below
    struct.pack_into("<H", pkt, 4, 0x01)
    for first, last in pairs:
        pkt.extend(struct.pack("<HH", first, last))
    pkt.extend(extra)
    struct.pack_into("<I", pkt, 0, len(pkt))
    return bytes(pkt)


def _ptype(packet: bytes) -> int:
    return struct.unpack_from("<H", packet, 4)[0]


def _seq(packet: bytes) -> int:
    return struct.unpack_from("<H", packet, 6)[0]


@pytest.fixture
def transport() -> IcomTransport:
    t = IcomTransport()
    t.my_id = 0x00010001
    t.remote_id = 0xAABBCCDD
    return t


@pytest.fixture
def sent(transport: IcomTransport) -> list:
    captured: list = []
    transport._raw_send = captured.append
    return captured


class TestIdleReply:
    def test_buffered_single_is_resent_byte_for_byte(
        self, transport: IcomTransport, sent: list
    ) -> None:
        original = _build_control(ptype=0x00, seq=3) + b"payload"
        transport.tx_buffer[3] = original
        transport._retransmit(3)
        assert len(sent) == 1
        assert sent[0] == original

    def test_unknown_single_gets_idle(
        self, transport: IcomTransport, sent: list
    ) -> None:
        before_buffer = dict(transport.tx_buffer)
        transport.send_seq = 42
        transport._retransmit(99)
        assert len(sent) == 1
        assert len(sent[0]) == CONTROL_SIZE
        assert _ptype(sent[0]) == 0x00
        assert _seq(sent[0]) == 99
        assert dict(transport.tx_buffer) == before_buffer
        assert transport.send_seq == 42

    def test_stale_guard_gets_idle_without_resend(
        self, transport: IcomTransport, sent: list
    ) -> None:
        stored = b"stale-packet"
        transport._track_sent(7, stored, is_current=lambda: False)
        transport._retransmit(7)
        assert len(sent) == 1
        assert len(sent[0]) == CONTROL_SIZE
        assert _ptype(sent[0]) == 0x00
        assert _seq(sent[0]) == 7
        assert sent[0] != stored

    def test_raising_guard_gets_idle_without_resend(
        self, transport: IcomTransport, sent: list
    ) -> None:
        stored = b"guarded-packet"
        transport._track_sent(8, stored, is_current=_boom)
        transport._retransmit(8)
        assert len(sent) == 1
        assert len(sent[0]) == CONTROL_SIZE
        assert _ptype(sent[0]) == 0x00
        assert _seq(sent[0]) == 8
        assert sent[0] != stored


def _boom() -> bool:
    raise RuntimeError("guard unavailable")


class TestRangeRequests:
    def test_range_resends_buffered_and_idles_gaps_in_order(
        self, transport: IcomTransport, sent: list
    ) -> None:
        resent = b"pkt155"
        transport.tx_buffer[155] = resent
        transport._handle_retransmit_packet(
            _build_range_request((154, 156)), CONTROL_SIZE + 4, 0, 0
        )
        assert len(sent) == 3
        assert _seq(sent[0]) == 154
        assert _ptype(sent[0]) == 0x00
        assert sent[1] == resent
        assert _seq(sent[2]) == 156
        assert _ptype(sent[2]) == 0x00

    def test_singleton_pair_resends_exactly_that_sequence(
        self, transport: IcomTransport, sent: list
    ) -> None:
        resent = b"pkt9"
        transport.tx_buffer[9] = resent
        transport._handle_retransmit_packet(
            _build_range_request((9, 9)), CONTROL_SIZE + 4, 0, 0
        )
        assert len(sent) == 1
        assert sent[0] == resent

    def test_wrapped_range(self, transport: IcomTransport, sent: list) -> None:
        transport._handle_retransmit_packet(
            _build_range_request((0xFFFF, 0x0001)), CONTROL_SIZE + 4, 0, 0
        )
        assert len(sent) == 3
        assert [_seq(p) for p in sent] == [0xFFFF, 0x0000, 0x0001]
        assert all(_ptype(p) == 0x00 for p in sent)

    def test_wide_forward_range_serves_first_max_missing_in_order(
        self, transport: IcomTransport, sent: list
    ) -> None:
        first = 1000
        expected: list = []
        for s in range(first, first + 60):
            pkt = b"pkt%04d" % s
            transport.tx_buffer[s] = pkt
            expected.append(pkt)
        transport._handle_retransmit_packet(
            _build_range_request((first, first + 59)), CONTROL_SIZE + 4, 0, 0
        )
        assert len(sent) == MAX_MISSING
        assert sent == expected[:MAX_MISSING]

    def test_backward_range_sends_nothing(
        self, transport: IcomTransport, sent: list
    ) -> None:
        transport.tx_buffer[155] = b"pkt155"
        transport._handle_retransmit_packet(
            _build_range_request((156, 154)), CONTROL_SIZE + 4, 0, 0
        )
        assert sent == []

    def test_max_missing_boundary(
        self, transport: IcomTransport, sent: list
    ) -> None:
        first = 2000
        for s in range(first, first + MAX_MISSING + 1):
            transport.tx_buffer[s] = b"b%04d" % s
        transport._handle_retransmit_packet(
            _build_range_request((first, first + MAX_MISSING - 1)),
            CONTROL_SIZE + 4,
            0,
            0,
        )
        assert len(sent) == MAX_MISSING
        sent.clear()
        transport._handle_retransmit_packet(
            _build_range_request((first, first + MAX_MISSING)),
            CONTROL_SIZE + 4,
            0,
            0,
        )
        assert len(sent) == MAX_MISSING

    def test_trailing_partial_pair_ignored(
        self, transport: IcomTransport, sent: list
    ) -> None:
        resent = b"pkt20"
        transport.tx_buffer[20] = resent
        transport._handle_retransmit_packet(
            _build_range_request((20, 20), extra=b"\x2a\x2b"),
            CONTROL_SIZE + 4 + 2,
            0,
            0,
        )
        assert len(sent) == 1
        assert sent[0] == resent

    def test_round_trip_through_handle_packet(
        self, transport: IcomTransport, sent: list
    ) -> None:
        resent = b"pkt155"
        transport.tx_buffer[155] = resent
        req = _build_range_request((154, 156))
        length = struct.unpack_from("<I", req, 0)[0]
        assert length == len(req)
        assert _ptype(req) == 0x01
        transport._handle_packet(req)
        assert len(sent) == 3
        assert _seq(sent[0]) == 154
        assert sent[1] == resent
        assert _seq(sent[2]) == 156
