"""End-to-end tests for the PCM audio API.

These tests exercise the full pipeline:
  start_audio_rx_pcm  → feed fake Opus → callback gets PCM → stop
  start_audio_tx_pcm  → push PCM → verify Opus sent           → stop

All radio I/O is mocked — no real hardware needed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.audio import AudioPacket, AudioState, AudioStats
from rigplane.core.types import AudioCodec
from rigplane.radio import IcomRadio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _DummyTranscoder:
    """Fake Opus transcoder that tags data instead of encoding/decoding."""

    def opus_to_pcm(self, opus: bytes) -> bytes:
        return b"pcm:" + opus

    def pcm_to_opus(self, pcm: bytes | bytearray | memoryview) -> bytes:
        return b"opus:" + bytes(pcm)[:4]


def _make_radio() -> IcomRadio:
    """Build an IcomRadio pre-wired with mocks for PCM pipeline tests."""
    radio = IcomRadio("192.168.1.100")
    radio._connected = True
    radio._civ_transport = MagicMock()
    radio._audio_stream = MagicMock()
    radio._audio_stream.start_rx = AsyncMock()
    radio._audio_stream.stop_rx = AsyncMock()
    radio._audio_stream.start_tx = AsyncMock()
    radio._audio_stream.stop_tx = AsyncMock()
    radio._audio_stream.push_tx = AsyncMock()
    radio._audio_stream.state = AudioState.IDLE
    radio._audio_stream.get_audio_stats = MagicMock(
        return_value=AudioStats.inactive().to_dict(),
    )
    # Pre-install dummy transcoder so _get_pcm_transcoder succeeds.
    radio._pcm_transcoder = _DummyTranscoder()  # type: ignore[assignment]
    radio._pcm_transcoder_fmt = (16000, 1, 20)
    # PR #1448 added a TX-codec branch in _push_audio_tx_pcm_internal: when
    # the negotiated TX codec is PCM_1CH_16BIT (the default for direct Icom
    # LAN), the frame is sent unchanged and the transcoder is bypassed.
    # These tests exercise the OPUS_1CH transcode path through the dummy
    # transcoder, so pin the TX codec accordingly.
    radio._audio_tx_codec = AudioCodec.OPUS_1CH
    return radio


# ---------------------------------------------------------------------------
# RX flow
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestPcmRxE2E:
    """RX pipeline: start → feed Opus packets → callback gets PCM → stop."""

    @pytest.mark.asyncio
    async def test_rx_happy_path(self) -> None:
        radio = _make_radio()
        received: list[bytes | None] = []

        await radio.start_audio_rx_pcm(
            lambda frame: received.append(frame),
            sample_rate=16000,
        )

        # Grab the internal Opus-level callback that was registered.
        rx_cb = radio._audio_stream.start_rx.await_args.args[0]

        # Simulate three Opus packets from the radio.
        rx_cb(AudioPacket(ident=0x0080, send_seq=1, data=b"\x01\x02"))
        rx_cb(AudioPacket(ident=0x0080, send_seq=2, data=b"\x03\x04"))
        rx_cb(AudioPacket(ident=0x0080, send_seq=3, data=b"\x05\x06"))

        assert len(received) == 3
        assert received[0] == b"pcm:\x01\x02"
        assert received[1] == b"pcm:\x03\x04"
        assert received[2] == b"pcm:\x05\x06"

        await radio.stop_audio_rx_pcm()
        radio._audio_stream.stop_rx.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rx_gap_forwarded_as_none(self) -> None:
        """Jitter-buffer gaps should be passed to callback as None."""
        radio = _make_radio()
        received: list[bytes | None] = []

        await radio.start_audio_rx_pcm(
            lambda frame: received.append(frame),
            sample_rate=16000,
        )
        rx_cb = radio._audio_stream.start_rx.await_args.args[0]

        rx_cb(AudioPacket(ident=0x0080, send_seq=1, data=b"\xaa"))
        rx_cb(None)  # gap
        rx_cb(AudioPacket(ident=0x0080, send_seq=3, data=b"\xbb"))

        assert received == [b"pcm:\xaa", None, b"pcm:\xbb"]

        await radio.stop_audio_rx_pcm()

    @pytest.mark.asyncio
    async def test_rx_many_frames(self) -> None:
        """Receive a burst of 100 frames without error."""
        radio = _make_radio()
        received: list[bytes | None] = []

        await radio.start_audio_rx_pcm(
            lambda frame: received.append(frame),
            sample_rate=16000,
        )
        rx_cb = radio._audio_stream.start_rx.await_args.args[0]

        for seq in range(100):
            rx_cb(AudioPacket(ident=0x0080, send_seq=seq, data=bytes([seq & 0xFF])))

        assert len(received) == 100
        await radio.stop_audio_rx_pcm()


# ---------------------------------------------------------------------------
# TX flow
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestPcmTxE2E:
    """TX pipeline: start → push PCM frames → Opus packets sent → stop."""

    @pytest.mark.asyncio
    async def test_tx_happy_path(self) -> None:
        radio = _make_radio()

        await radio.start_audio_tx_pcm()
        radio._audio_stream.start_tx.assert_awaited_once()
        assert radio._pcm_tx_fmt == (16000, 1, 20)

        # Push a PCM frame.
        pcm_frame = b"\x10\x00" * 320  # 640 bytes = 320 samples × 2 bytes
        await radio.push_audio_tx_pcm(pcm_frame)

        # Transcoder produces "opus:" + first 4 bytes of PCM.
        radio._audio_stream.push_tx.assert_awaited_once_with(b"opus:\x10\x00\x10\x00")

        await radio.stop_audio_tx_pcm()
        radio._audio_stream.stop_tx.assert_awaited_once()
        assert radio._pcm_tx_fmt is None

    @pytest.mark.asyncio
    async def test_tx_multiple_frames(self) -> None:
        radio = _make_radio()
        await radio.start_audio_tx_pcm()

        for _ in range(10):
            await radio.push_audio_tx_pcm(b"\x00\x01" * 320)

        assert radio._audio_stream.push_tx.await_count == 10
        await radio.stop_audio_tx_pcm()

    @pytest.mark.asyncio
    async def test_push_without_start_raises(self) -> None:
        radio = _make_radio()
        with pytest.raises(RuntimeError, match="start_audio_tx_pcm"):
            await radio.push_audio_tx_pcm(b"\x00" * 1920)


# ---------------------------------------------------------------------------
# Loopback (RX + TX simultaneously)
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestPcmLoopbackE2E:
    """Full-duplex loopback: RX callback feeds back into TX."""

    @pytest.mark.asyncio
    async def test_loopback_flow(self) -> None:
        radio = _make_radio()
        rx_frames: list[bytes | None] = []

        # RX callback is synchronous — just collect frames.
        def _on_pcm(frame: bytes | None) -> None:
            rx_frames.append(frame)

        # Start TX first, then RX.
        await radio.start_audio_tx_pcm()
        await radio.start_audio_rx_pcm(_on_pcm, sample_rate=16000)

        rx_cb = radio._audio_stream.start_rx.await_args.args[0]

        # Feed 5 Opus packets through the RX pipeline.
        for seq in range(5):
            rx_cb(AudioPacket(ident=0x0080, send_seq=seq, data=bytes([seq])))

        assert len(rx_frames) == 5

        # Now drive the TX pipeline N times. The dummy transcoder's RX output
        # is a tagged byte string (not real PCM), so we can't feed it back
        # verbatim — the TX path validates frame size against PcmAudioFormat
        # (640 bytes for 16kHz/1ch/20ms). Use a properly-sized synthetic
        # frame; the assertion only checks that TX was invoked once per RX
        # frame.
        tx_frame = b"\x00" * 640
        for frame in rx_frames:
            if frame is not None:
                await radio.push_audio_tx_pcm(tx_frame)

        assert radio._audio_stream.push_tx.await_count == 5

        await radio.stop_audio_rx_pcm()
        await radio.stop_audio_tx_pcm()


# ---------------------------------------------------------------------------
# Repeated start/stop cycles
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestPcmStartStopCycles:
    """Repeated start→stop should not leak state or raise errors."""

    @pytest.mark.asyncio
    async def test_rx_start_stop_5_cycles(self) -> None:
        for _ in range(5):
            radio = _make_radio()
            received: list[bytes | None] = []

            await radio.start_audio_rx_pcm(
                lambda frame: received.append(frame),
                sample_rate=16000,
            )
            rx_cb = radio._audio_stream.start_rx.await_args.args[0]
            rx_cb(AudioPacket(ident=0x0080, send_seq=0, data=b"\xcc"))
            assert received == [b"pcm:\xcc"]

            await radio.stop_audio_rx_pcm()
            radio._audio_stream.stop_rx.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tx_start_stop_5_cycles(self) -> None:
        for _ in range(5):
            radio = _make_radio()

            await radio.start_audio_tx_pcm()
            await radio.push_audio_tx_pcm(b"\xaa\xbb" * 320)
            assert radio._audio_stream.push_tx.await_count == 1

            await radio.stop_audio_tx_pcm()
            assert radio._pcm_tx_fmt is None


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestPcmEdgeCases:
    @pytest.mark.asyncio
    async def test_stop_rx_without_start_is_noop(self) -> None:
        """Stopping RX before starting should not raise."""
        radio = _make_radio()
        await radio.stop_audio_rx_pcm()  # should not raise

    @pytest.mark.asyncio
    async def test_stop_tx_without_start_is_noop(self) -> None:
        """Stopping TX before starting should not raise."""
        radio = _make_radio()
        await radio.stop_audio_tx_pcm()  # should not raise

    @pytest.mark.asyncio
    async def test_double_start_rx_raises(self) -> None:
        """Starting RX twice should raise RuntimeError."""
        radio = _make_radio()
        radio._audio_stream.start_rx = AsyncMock(
            side_effect=[None, RuntimeError("Already receiving")],
        )
        await radio.start_audio_rx_pcm(lambda _: None, sample_rate=16000)
        with pytest.raises(RuntimeError, match="Already receiving"):
            await radio.start_audio_rx_pcm(lambda _: None, sample_rate=16000)
        await radio.stop_audio_rx_pcm()

    @pytest.mark.asyncio
    async def test_double_start_tx_raises(self) -> None:
        """Starting TX twice should raise RuntimeError."""
        radio = _make_radio()
        radio._audio_stream.start_tx = AsyncMock(
            side_effect=[None, RuntimeError("Already transmitting")],
        )
        await radio.start_audio_tx_pcm()
        with pytest.raises(RuntimeError, match="Already transmitting"):
            await radio.start_audio_tx_pcm()
        await radio.stop_audio_tx_pcm()


# ---------------------------------------------------------------------------
# Stats after activity
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestPcmStatsE2E:
    def test_stats_idle(self) -> None:
        radio = _make_radio()
        # No audio stream => inactive stats.
        radio._audio_stream = None
        stats = radio.get_audio_stats()
        assert stats["active"] is False
        assert stats["state"] == "idle"

    def test_stats_after_rx(self) -> None:
        radio = _make_radio()
        radio._audio_stream.get_audio_stats = MagicMock(
            return_value={
                "active": True,
                "state": "receiving",
                "rx_packets_received": 42,
                "rx_packets_delivered": 40,
                "tx_packets_sent": 0,
                "packets_lost": 2,
                "packet_loss_percent": 4.76,
                "reorder_depth_ema_ms": 3.5,
                "jitter_max_ms": 15.0,
                "underrun_count": 1,
                "overrun_count": 0,
                "estimated_latency_ms": 100.0,
                "jitter_buffer_depth_packets": 5,
                "jitter_buffer_pending_packets": 2,
                "duplicates_dropped": 0,
                "stale_packets_dropped": 0,
                "out_of_order_packets": 1,
            },
        )
        stats = radio.get_audio_stats()
        assert stats["active"] is True
        assert stats["rx_packets_received"] == 42
        assert stats["packets_lost"] == 2
        assert stats["reorder_depth_ema_ms"] == 3.5
