"""Tests for audio streaming (packet parsing, building, AudioStream)."""

import asyncio
import struct
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.audio import (
    AUDIO_HEADER_SIZE,
    TX_IDENT,
    RX_IDENT_0xA0,
    AudioState,
    AudioStream,
    build_audio_packet,
    parse_audio_packet,
)
from rigplane.types import PacketType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SENDER_ID = 0xAABBCCDD
RECEIVER_ID = 0x11223344


def _make_audio_bytes(
    opus_data: bytes,
    *,
    ident: int = 0x9781,
    send_seq: int = 0,
    sender_id: int = SENDER_ID,
    receiver_id: int = RECEIVER_ID,
    pkt_type: int = PacketType.DATA,
) -> bytes:
    """Build a raw audio packet for testing."""
    total = AUDIO_HEADER_SIZE + len(opus_data)
    pkt = bytearray(total)
    struct.pack_into("<I", pkt, 0x00, total)
    struct.pack_into("<H", pkt, 0x04, pkt_type)
    struct.pack_into("<H", pkt, 0x06, 0)  # seq
    struct.pack_into("<I", pkt, 0x08, sender_id)
    struct.pack_into("<I", pkt, 0x0C, receiver_id)
    struct.pack_into("<H", pkt, 0x10, ident)
    struct.pack_into(">H", pkt, 0x12, send_seq)
    struct.pack_into(">H", pkt, 0x16, len(opus_data))
    pkt[AUDIO_HEADER_SIZE:] = opus_data
    return bytes(pkt)


# ---------------------------------------------------------------------------
# parse_audio_packet
# ---------------------------------------------------------------------------


class TestParseAudioPacket:
    """Tests for parse_audio_packet()."""

    def test_parse_basic(self):
        opus = b"\x01\x02\x03\x04"
        raw = _make_audio_bytes(opus, ident=0x9781, send_seq=42)
        pkt = parse_audio_packet(raw)
        assert pkt is not None
        assert pkt.ident == 0x9781
        assert pkt.send_seq == 42
        assert pkt.data == opus

    def test_parse_tx_ident(self):
        opus = b"\xff" * 160
        raw = _make_audio_bytes(opus, ident=TX_IDENT, send_seq=1000)
        pkt = parse_audio_packet(raw)
        assert pkt is not None
        assert pkt.ident == TX_IDENT
        assert pkt.send_seq == 1000
        assert len(pkt.data) == 160

    def test_parse_0xa0_length_ident(self):
        opus = b"\xaa" * 0xA0
        raw = _make_audio_bytes(opus, ident=RX_IDENT_0xA0, send_seq=5)
        pkt = parse_audio_packet(raw)
        assert pkt is not None
        assert pkt.ident == RX_IDENT_0xA0
        assert len(pkt.data) == 0xA0

    def test_parse_too_short_returns_none(self):
        # Exactly AUDIO_HEADER_SIZE — no audio data
        raw = b"\x00" * AUDIO_HEADER_SIZE
        assert parse_audio_packet(raw) is None

    def test_parse_control_packet_returns_none(self):
        raw = _make_audio_bytes(b"\x01\x02", pkt_type=PacketType.CONTROL)
        assert parse_audio_packet(raw) is None

    def test_parse_retransmit_type_returns_none(self):
        raw = _make_audio_bytes(b"\x01\x02", pkt_type=PacketType.ARE_YOU_THERE)
        assert parse_audio_packet(raw) is None

    def test_parse_empty_below_header(self):
        assert parse_audio_packet(b"\x00" * 10) is None

    def test_parse_large_frame(self):
        opus = b"\xbb" * 1364  # max opus frame in wfview
        raw = _make_audio_bytes(opus, send_seq=0xFFFF)
        pkt = parse_audio_packet(raw)
        assert pkt is not None
        assert pkt.send_seq == 0xFFFF
        assert len(pkt.data) == 1364


# ---------------------------------------------------------------------------
# build_audio_packet
# ---------------------------------------------------------------------------


class TestBuildAudioPacket:
    """Tests for build_audio_packet()."""

    def test_build_basic(self):
        opus = b"\x01\x02\x03"
        pkt = build_audio_packet(
            opus, sender_id=SENDER_ID, receiver_id=RECEIVER_ID, send_seq=7
        )
        assert len(pkt) == AUDIO_HEADER_SIZE + 3

        # Verify header fields
        length = struct.unpack_from("<I", pkt, 0x00)[0]
        assert length == len(pkt)

        pkt_type = struct.unpack_from("<H", pkt, 0x04)[0]
        assert pkt_type == PacketType.DATA

        sid = struct.unpack_from("<I", pkt, 0x08)[0]
        assert sid == SENDER_ID

        rid = struct.unpack_from("<I", pkt, 0x0C)[0]
        assert rid == RECEIVER_ID

        ident = struct.unpack_from("<H", pkt, 0x10)[0]
        assert ident == TX_IDENT

        send_seq = struct.unpack_from(">H", pkt, 0x12)[0]
        assert send_seq == 7

        datalen = struct.unpack_from(">H", pkt, 0x16)[0]
        assert datalen == 3

        assert pkt[AUDIO_HEADER_SIZE:] == opus

    def test_build_custom_ident(self):
        pkt = build_audio_packet(
            b"\x00",
            sender_id=1,
            receiver_id=2,
            send_seq=0,
            ident=RX_IDENT_0xA0,
        )
        ident = struct.unpack_from("<H", pkt, 0x10)[0]
        assert ident == RX_IDENT_0xA0

    def test_build_empty_opus(self):
        pkt = build_audio_packet(b"", sender_id=1, receiver_id=2, send_seq=0)
        assert len(pkt) == AUDIO_HEADER_SIZE
        datalen = struct.unpack_from(">H", pkt, 0x16)[0]
        assert datalen == 0

    def test_build_seq_wraps(self):
        pkt = build_audio_packet(b"\x00", sender_id=1, receiver_id=2, send_seq=0xFFFF)
        send_seq = struct.unpack_from(">H", pkt, 0x12)[0]
        assert send_seq == 0xFFFF


# ---------------------------------------------------------------------------
# Roundtrip
# ---------------------------------------------------------------------------


class TestRoundtrip:
    """Parse(build(data)) should recover the original data."""

    def test_roundtrip_small(self):
        opus = b"\xde\xad\xbe\xef"
        raw = build_audio_packet(
            opus, sender_id=SENDER_ID, receiver_id=RECEIVER_ID, send_seq=99
        )
        pkt = parse_audio_packet(raw)
        assert pkt is not None
        assert pkt.data == opus
        assert pkt.send_seq == 99
        assert pkt.ident == TX_IDENT

    def test_roundtrip_large(self):
        opus = bytes(range(256)) * 5  # 1280 bytes
        raw = build_audio_packet(opus, sender_id=1, receiver_id=2, send_seq=1234)
        pkt = parse_audio_packet(raw)
        assert pkt is not None
        assert pkt.data == opus
        assert pkt.send_seq == 1234


# ---------------------------------------------------------------------------
# AudioStream
# ---------------------------------------------------------------------------


def _mock_transport(my_id: int = SENDER_ID, remote_id: int = RECEIVER_ID):
    """Create a mock IcomTransport."""
    t = MagicMock()
    t.my_id = my_id
    t.remote_id = remote_id
    t.send_tracked = AsyncMock()
    t.receive_packet = AsyncMock(side_effect=asyncio.TimeoutError)
    return t


class TestAudioStreamInit:
    """Tests for AudioStream initialization."""

    def test_initial_state(self):
        t = _mock_transport()
        stream = AudioStream(t)
        assert stream.state == AudioState.IDLE
        assert stream.transport is t


class TestAudioStreamRx:
    """Tests for AudioStream RX."""

    @pytest.mark.asyncio
    async def test_start_stop_rx(self):
        t = _mock_transport()
        stream = AudioStream(t)

        cb = MagicMock()
        await stream.start_rx(cb)
        assert stream.state == AudioState.RECEIVING

        await stream.stop_rx()
        assert stream.state == AudioState.IDLE

    @pytest.mark.asyncio
    async def test_start_rx_twice_raises(self):
        t = _mock_transport()
        stream = AudioStream(t)
        await stream.start_rx(MagicMock())
        with pytest.raises(RuntimeError):
            await stream.start_rx(MagicMock())
        await stream.stop_rx()

    @pytest.mark.asyncio
    async def test_rx_callback_invoked(self):
        opus = b"\x01\x02\x03"
        raw = _make_audio_bytes(opus, send_seq=1)

        t = _mock_transport()
        # Return one audio packet, then timeout forever
        t.receive_packet = AsyncMock(
            side_effect=[raw, asyncio.TimeoutError, asyncio.CancelledError]
        )

        received = []
        stream = AudioStream(t)
        # No jitter buffer: stop_rx drops held packets (MOR-4214), and one
        # packet stays held below the default depth.
        await stream.start_rx(lambda pkt: received.append(pkt), jitter_depth=0)

        # Give the rx loop a moment to process
        await asyncio.sleep(0.05)
        await stream.stop_rx()

        assert len(received) == 1
        assert received[0].data == opus
        assert received[0].send_seq == 1

    @pytest.mark.asyncio
    async def test_rx_skips_short_packets(self):
        t = _mock_transport()
        # Short packet (control), then cancel
        t.receive_packet = AsyncMock(
            side_effect=[b"\x00" * 16, asyncio.TimeoutError, asyncio.CancelledError]
        )

        received = []
        stream = AudioStream(t)
        await stream.start_rx(lambda pkt: received.append(pkt))
        await asyncio.sleep(0.05)
        await stream.stop_rx()

        assert len(received) == 0

    @pytest.mark.asyncio
    async def test_stop_rx_when_idle_is_noop(self):
        t = _mock_transport()
        stream = AudioStream(t)
        await stream.stop_rx()  # should not raise
        assert stream.state == AudioState.IDLE


class TestAudioStreamTx:
    """Tests for AudioStream TX."""

    @pytest.mark.asyncio
    async def test_start_stop_tx(self):
        t = _mock_transport()
        stream = AudioStream(t)
        await stream.start_tx()
        assert stream.state == AudioState.TRANSMITTING
        await stream.stop_tx()
        assert stream.state == AudioState.IDLE

    @pytest.mark.asyncio
    async def test_push_tx_sends_packet(self):
        t = _mock_transport()
        stream = AudioStream(t)
        await stream.start_tx()

        await stream.push_tx(b"\xaa\xbb")
        t.send_tracked.assert_called_once()

        sent = t.send_tracked.call_args[0][0]
        pkt = parse_audio_packet(sent)
        assert pkt is not None
        assert pkt.data == b"\xaa\xbb"
        assert pkt.send_seq == 0
        assert pkt.ident == TX_IDENT

        await stream.stop_tx()

    @pytest.mark.asyncio
    async def test_push_tx_increments_seq(self):
        t = _mock_transport()
        stream = AudioStream(t)
        await stream.start_tx()

        await stream.push_tx(b"\x01")
        await stream.push_tx(b"\x02")
        await stream.push_tx(b"\x03")

        assert t.send_tracked.call_count == 3
        seqs = []
        for call in t.send_tracked.call_args_list:
            pkt = parse_audio_packet(call[0][0])
            seqs.append(pkt.send_seq)
        assert seqs == [0, 1, 2]

        await stream.stop_tx()

    @pytest.mark.asyncio
    async def test_push_tx_not_started_raises(self):
        t = _mock_transport()
        stream = AudioStream(t)
        with pytest.raises(RuntimeError):
            await stream.push_tx(b"\x00")

    @pytest.mark.asyncio
    async def test_start_tx_twice_raises(self):
        t = _mock_transport()
        stream = AudioStream(t)
        await stream.start_tx()
        with pytest.raises(RuntimeError):
            await stream.start_tx()
        await stream.stop_tx()

    @pytest.mark.asyncio
    async def test_stop_tx_when_idle_is_noop(self):
        t = _mock_transport()
        stream = AudioStream(t)
        await stream.stop_tx()
        assert stream.state == AudioState.IDLE

    @pytest.mark.asyncio
    async def test_full_duplex_tx_while_rx(self):
        """TX can start while RX is active (full-duplex)."""
        t = _mock_transport()
        stream = AudioStream(t)
        await stream.start_rx(MagicMock())
        await stream.start_tx()  # should succeed (full-duplex)
        assert stream.state == AudioState.TRANSMITTING
        await stream.stop_tx()
        assert stream.state == AudioState.RECEIVING
        await stream.stop_rx()

    @pytest.mark.asyncio
    async def test_cannot_start_rx_while_tx(self):
        t = _mock_transport()
        stream = AudioStream(t)
        await stream.start_tx()
        with pytest.raises(RuntimeError):
            await stream.start_rx(MagicMock())
        await stream.stop_tx()


class TestAudioStreamSeqWrap:
    """Test TX sequence number wrapping at 0xFFFF."""

    @pytest.mark.asyncio
    async def test_seq_wraps_at_65535(self):
        t = _mock_transport()
        stream = AudioStream(t)
        await stream.start_tx()

        # Manually set seq near wrap
        stream._tx_seq = 0xFFFF
        await stream.push_tx(b"\x01")

        pkt = parse_audio_packet(t.send_tracked.call_args[0][0])
        assert pkt.send_seq == 0xFFFF

        # Next should wrap to 0
        await stream.push_tx(b"\x02")
        pkt2 = parse_audio_packet(t.send_tracked.call_args[0][0])
        assert pkt2.send_seq == 0

        await stream.stop_tx()


class TestAudioStreamStats:
    @pytest.mark.asyncio
    async def test_get_audio_stats_idle(self):
        t = _mock_transport()
        stream = AudioStream(t)
        stats = stream.get_audio_stats()

        assert stats["active"] is False
        assert stats["state"] == AudioState.IDLE
        assert stats["packet_loss_percent"] == 0.0
        assert stats["estimated_latency_ms"] == 0.0
        assert stats["underrun_count"] == 0
        assert stats["overrun_count"] == 0

    @pytest.mark.asyncio
    async def test_get_audio_stats_with_gaps_and_jitter(self):
        raw0 = _make_audio_bytes(b"\x00", send_seq=0)
        raw2 = _make_audio_bytes(b"\x02", send_seq=2)
        raw3 = _make_audio_bytes(b"\x03", send_seq=3)

        t = _mock_transport()
        t.receive_packet = AsyncMock(
            side_effect=[raw0, raw2, raw3, asyncio.TimeoutError, asyncio.CancelledError]
        )

        stream = AudioStream(t)
        received = []
        await stream.start_rx(lambda pkt: received.append(pkt), jitter_depth=2)
        await asyncio.sleep(0.05)

        stats = stream.get_audio_stats()
        assert stats["active"] is True
        assert stats["state"] == AudioState.RECEIVING
        assert stats["packets_lost"] == 1
        assert stats["packet_loss_percent"] == 25.0
        assert stats["reorder_depth_ema_ms"] >= 0.0
        assert stats["jitter_max_ms"] >= 0.0
        assert stats["underrun_count"] >= 1
        assert stats["estimated_latency_ms"] >= 40.0

        await stream.stop_rx()

        assert any(pkt is None for pkt in received)
