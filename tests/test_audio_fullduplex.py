"""Tests for full-duplex audio and jitter-buffered RX in AudioStream."""

import asyncio

import pytest

from rigplane.audio import AudioState, AudioStream
from rigplane.radio import IcomRadio
from rigplane.exceptions import ConnectionError

from test_radio import MockTransport


@pytest.fixture
def mock_transport() -> MockTransport:
    return MockTransport()


@pytest.fixture
def radio(mock_transport: MockTransport) -> IcomRadio:
    r = IcomRadio("192.168.1.100")
    r._civ_transport = mock_transport
    r._ctrl_transport = mock_transport
    r._connected = True
    return r


class TestFullDuplex:
    @pytest.mark.asyncio
    async def test_start_audio(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        """start_audio should enable both RX and TX."""
        radio._audio_port = 50003
        radio._audio_transport = mock_transport
        radio._audio_stream = AudioStream(mock_transport)
        received = []
        await radio.start_audio_opus(lambda pkt: received.append(pkt), tx_enabled=True)
        assert radio._audio_stream.state == AudioState.TRANSMITTING
        await radio.stop_audio_opus()

    @pytest.mark.asyncio
    async def test_start_audio_rx_only(
        self, radio: IcomRadio, mock_transport: MockTransport
    ) -> None:
        radio._audio_port = 50003
        radio._audio_transport = mock_transport
        radio._audio_stream = AudioStream(mock_transport)
        received = []
        await radio.start_audio_opus(lambda pkt: received.append(pkt), tx_enabled=False)
        assert radio._audio_stream.state == AudioState.RECEIVING
        await radio.stop_audio_opus()

    @pytest.mark.asyncio
    async def test_start_audio_disconnected(self) -> None:
        r = IcomRadio("192.168.1.100")
        with pytest.raises(ConnectionError):
            await r.start_audio_opus(lambda pkt: None)

    @pytest.mark.asyncio
    async def test_stop_audio_noop(self, radio: IcomRadio) -> None:
        await radio.stop_audio_opus()  # should not raise


class TestAudioStreamJitter:
    @pytest.mark.asyncio
    async def test_rx_with_jitter_buffer(self, mock_transport: MockTransport) -> None:
        """Packets should be delivered in order through jitter buffer."""
        import struct
        from rigplane.types import PacketType
        from rigplane.audio import AUDIO_HEADER_SIZE

        stream = AudioStream(mock_transport, jitter_depth=2)
        received = []

        def _build_audio_pkt(seq: int, data: bytes = b"\xaa\xbb") -> bytes:
            total = AUDIO_HEADER_SIZE + len(data)
            pkt = bytearray(total)
            struct.pack_into("<I", pkt, 0, total)
            struct.pack_into("<H", pkt, 4, PacketType.DATA)
            struct.pack_into("<H", pkt, 0x10, 0x0080)
            struct.pack_into(">H", pkt, 0x12, seq)
            struct.pack_into(">H", pkt, 0x16, len(data))
            pkt[AUDIO_HEADER_SIZE:] = data
            return bytes(pkt)

        await stream.start_rx(lambda pkt: received.append(pkt), jitter_depth=2)

        # Queue packets out of order
        mock_transport.queue_response(_build_audio_pkt(0))
        mock_transport.queue_response(_build_audio_pkt(2))
        mock_transport.queue_response(_build_audio_pkt(1))
        mock_transport.queue_response(_build_audio_pkt(3))
        mock_transport.queue_response(_build_audio_pkt(4))

        await asyncio.sleep(0.3)
        await stream.stop_rx()

        # Should have received packets in order
        seqs = [p.send_seq for p in received if p is not None]
        assert seqs == sorted(seqs)
        assert 0 in seqs

    @pytest.mark.asyncio
    async def test_rx_no_jitter(self, mock_transport: MockTransport) -> None:
        """With jitter_depth=0, packets are delivered immediately."""
        import struct
        from rigplane.types import PacketType
        from rigplane.audio import AUDIO_HEADER_SIZE

        stream = AudioStream(mock_transport, jitter_depth=0)
        received = []

        def _build_audio_pkt(seq: int) -> bytes:
            data = b"\xaa"
            total = AUDIO_HEADER_SIZE + len(data)
            pkt = bytearray(total)
            struct.pack_into("<I", pkt, 0, total)
            struct.pack_into("<H", pkt, 4, PacketType.DATA)
            struct.pack_into("<H", pkt, 0x10, 0x0080)
            struct.pack_into(">H", pkt, 0x12, seq)
            struct.pack_into(">H", pkt, 0x16, len(data))
            pkt[AUDIO_HEADER_SIZE:] = data
            return bytes(pkt)

        await stream.start_rx(lambda pkt: received.append(pkt), jitter_depth=0)

        mock_transport.queue_response(_build_audio_pkt(5))
        mock_transport.queue_response(_build_audio_pkt(3))

        await asyncio.sleep(0.2)
        await stream.stop_rx()

        # No jitter buffering — packets arrive in receive order
        assert len(received) == 2
        assert received[0].send_seq == 5
        assert received[1].send_seq == 3


class TestAudioStreamState:
    @pytest.mark.asyncio
    async def test_cannot_start_rx_twice(self, mock_transport: MockTransport) -> None:
        stream = AudioStream(mock_transport)
        await stream.start_rx(lambda p: None)
        with pytest.raises(RuntimeError):
            await stream.start_rx(lambda p: None)
        await stream.stop_rx()

    @pytest.mark.asyncio
    async def test_full_duplex_allowed(self, mock_transport: MockTransport) -> None:
        """start_tx should work while RX is active (full-duplex)."""
        stream = AudioStream(mock_transport)
        await stream.start_rx(lambda p: None)
        await stream.start_tx()  # should not raise
        assert stream.state == AudioState.TRANSMITTING
        await stream.stop_tx()
        assert stream.state == AudioState.RECEIVING
        await stream.stop_rx()
        assert stream.state == AudioState.IDLE

    @pytest.mark.asyncio
    async def test_cannot_start_tx_twice(self, mock_transport: MockTransport) -> None:
        stream = AudioStream(mock_transport)
        await stream.start_tx()
        with pytest.raises(RuntimeError, match="Already transmitting"):
            await stream.start_tx()
        await stream.stop_tx()

    @pytest.mark.asyncio
    async def test_double_start_tx_raises_typed_error(
        self, mock_transport: MockTransport
    ) -> None:
        """Double TX start raises AudioAlreadyStartedError — a typed
        lifecycle error, not a bare RuntimeError (MOR-563)."""
        from rigplane.audio.usb_driver import AudioAlreadyStartedError

        stream = AudioStream(mock_transport)
        await stream.start_tx()
        with pytest.raises(AudioAlreadyStartedError):
            await stream.start_tx()
        await stream.stop_tx()
