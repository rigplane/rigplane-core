"""Tests for auto-recovery of audio streams after reconnect (issue #7).

Verifies that when the radio disconnects and reconnects, audio streams
(RX/TX, PCM/Opus) are automatically restarted with the same callbacks
and parameters. All radio I/O is mocked.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from _audio_stream_fake import FakeAudioStream

from icom_lan.audio import AudioState
from icom_lan.radio import AudioRecoveryState, IcomRadio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_radio(**kwargs) -> IcomRadio:
    """Build a connected IcomRadio with mocked internals."""
    defaults = dict(
        auto_reconnect=True,
        reconnect_delay=0.05,
        reconnect_max_delay=0.2,
        watchdog_timeout=0.3,
        auto_recover_audio=True,
    )
    defaults.update(kwargs)
    radio = IcomRadio("192.168.1.100", **defaults)
    mt = MagicMock()
    mt.connect = AsyncMock()
    mt.disconnect = AsyncMock()
    mt.start_ping_loop = MagicMock()
    mt.start_retransmit_loop = MagicMock()
    mt._packet_queue = asyncio.Queue()
    mt.ping_seq = 0
    radio._ctrl_transport = mt
    radio._civ_transport = MagicMock()
    radio._civ_transport.disconnect = AsyncMock()
    radio._connected = True
    radio._token = 0x1234
    return radio


def _install_audio_stream(radio: IcomRadio) -> FakeAudioStream:
    """Install a FakeAudioStream on the radio."""
    stream = FakeAudioStream()
    radio._audio_stream = stream
    radio._audio_transport = MagicMock()
    radio._audio_transport.disconnect = AsyncMock()
    return stream


class _DummyTranscoder:
    """Stub transcoder for PCM mode detection."""

    def opus_to_pcm(self, opus: bytes) -> bytes:
        return b"pcm:" + opus

    def pcm_to_opus(self, pcm: bytes | bytearray | memoryview) -> bytes:
        return b"opus:" + bytes(pcm)[:4]


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestAudioRecoveryConfig:
    def test_default_auto_recover_true(self) -> None:
        radio = IcomRadio("192.168.1.100")
        assert radio._auto_recover_audio is True

    def test_disable_auto_recover(self) -> None:
        radio = IcomRadio("192.168.1.100", auto_recover_audio=False)
        assert radio._auto_recover_audio is False

    def test_on_audio_recovery_callback_stored(self) -> None:
        cb = MagicMock()
        radio = IcomRadio("192.168.1.100", on_audio_recovery=cb)
        assert radio._on_audio_recovery is cb

    def test_on_audio_recovery_default_none(self) -> None:
        radio = IcomRadio("192.168.1.100")
        assert radio._on_audio_recovery is None


# ---------------------------------------------------------------------------
# AudioRecoveryState enum
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestAudioRecoveryStateEnum:
    def test_values(self) -> None:
        assert AudioRecoveryState.RECOVERING.value == "recovering"
        assert AudioRecoveryState.RECOVERED.value == "recovered"
        assert AudioRecoveryState.FAILED.value == "failed"


# ---------------------------------------------------------------------------
# Snapshot capture
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestAudioSnapshotCapture:
    def test_snapshot_idle_returns_none(self) -> None:
        radio = _make_radio()
        assert radio._audio_runtime.capture_snapshot() is None

    def test_snapshot_no_stream_returns_none(self) -> None:
        radio = _make_radio()
        radio._audio_stream = None
        assert radio._audio_runtime.capture_snapshot() is None

    def test_snapshot_rx_pcm(self) -> None:
        radio = _make_radio()
        stream = _install_audio_stream(radio)
        stream.state = AudioState.RECEIVING

        cb = MagicMock()
        radio._pcm_rx_user_callback = cb
        radio._pcm_rx_jitter_depth = 7
        radio._pcm_transcoder = _DummyTranscoder()  # type: ignore[assignment]
        radio._pcm_transcoder_fmt = (48000, 1, 20)

        snap = radio._audio_runtime.capture_snapshot()
        assert snap is not None
        assert snap.rx_active is True
        assert snap.tx_active is False
        assert snap.pcm_mode is True
        assert snap.pcm_rx_callback is cb
        assert snap.pcm_params == (48000, 1, 20)
        assert snap.jitter_depth == 7

    def test_snapshot_rx_opus(self) -> None:
        radio = _make_radio()
        stream = _install_audio_stream(radio)
        stream.state = AudioState.RECEIVING

        cb = MagicMock()
        radio._opus_rx_user_callback = cb
        radio._opus_rx_jitter_depth = 3

        snap = radio._audio_runtime.capture_snapshot()
        assert snap is not None
        assert snap.rx_active is True
        assert snap.pcm_mode is False
        assert snap.opus_rx_callback is cb
        assert snap.jitter_depth == 3

    def test_snapshot_tx_pcm(self) -> None:
        radio = _make_radio()
        stream = _install_audio_stream(radio)
        stream.state = AudioState.TRANSMITTING

        radio._pcm_tx_fmt = (48000, 2, 20)
        radio._pcm_transcoder = _DummyTranscoder()  # type: ignore[assignment]
        radio._pcm_transcoder_fmt = (48000, 2, 20)

        snap = radio._audio_runtime.capture_snapshot()
        assert snap is not None
        assert snap.tx_active is True
        assert snap.pcm_mode is True
        assert snap.pcm_params == (48000, 2, 20)

    def test_snapshot_full_duplex_pcm(self) -> None:
        """TRANSMITTING state with a stored PCM RX callback = full duplex."""
        radio = _make_radio()
        stream = _install_audio_stream(radio)
        stream.state = AudioState.TRANSMITTING

        rx_cb = MagicMock()
        radio._pcm_rx_user_callback = rx_cb
        radio._pcm_rx_jitter_depth = 5
        radio._pcm_tx_fmt = (48000, 1, 20)
        radio._pcm_transcoder = _DummyTranscoder()  # type: ignore[assignment]
        radio._pcm_transcoder_fmt = (48000, 1, 20)

        snap = radio._audio_runtime.capture_snapshot()
        assert snap is not None
        assert snap.rx_active is True
        assert snap.tx_active is True
        assert snap.pcm_mode is True
        assert snap.pcm_rx_callback is rx_cb


# ---------------------------------------------------------------------------
# Recovery after reconnect
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestAudioRecoveryAfterReconnect:
    @pytest.mark.asyncio
    async def test_rx_pcm_recovered(self) -> None:
        """PCM RX should be restarted with same callback/params after reconnect."""
        radio = _make_radio()
        stream = _install_audio_stream(radio)
        stream.state = AudioState.RECEIVING

        rx_cb = MagicMock()
        radio._pcm_rx_user_callback = rx_cb
        radio._pcm_rx_jitter_depth = 7
        radio._pcm_transcoder = _DummyTranscoder()  # type: ignore[assignment]
        radio._pcm_transcoder_fmt = (48000, 1, 20)

        with (
            patch.object(radio, "connect", new_callable=AsyncMock) as mock_connect,
            patch.object(
                radio, "start_audio_rx_pcm", new_callable=AsyncMock
            ) as mock_start,
        ):
            mock_connect.return_value = None
            radio._intentional_disconnect = False
            await radio._reconnect_loop()

        mock_start.assert_awaited_once_with(
            rx_cb,
            sample_rate=48000,
            channels=1,
            frame_ms=20,
            jitter_depth=7,
        )

    @pytest.mark.asyncio
    async def test_tx_pcm_recovered(self) -> None:
        """PCM TX should be restarted after reconnect."""
        radio = _make_radio()
        stream = _install_audio_stream(radio)
        stream.state = AudioState.TRANSMITTING

        radio._pcm_tx_fmt = (48000, 1, 20)
        radio._pcm_transcoder = _DummyTranscoder()  # type: ignore[assignment]
        radio._pcm_transcoder_fmt = (48000, 1, 20)

        with (
            patch.object(radio, "connect", new_callable=AsyncMock) as mock_connect,
            patch.object(
                radio, "start_audio_tx_pcm", new_callable=AsyncMock
            ) as mock_start,
        ):
            mock_connect.return_value = None
            radio._intentional_disconnect = False
            await radio._reconnect_loop()

        mock_start.assert_awaited_once_with(
            sample_rate=48000,
            channels=1,
            frame_ms=20,
        )

    @pytest.mark.asyncio
    async def test_full_duplex_recovered(self) -> None:
        """Both RX and TX should be restarted after reconnect."""
        radio = _make_radio()
        stream = _install_audio_stream(radio)
        stream.state = AudioState.TRANSMITTING

        rx_cb = MagicMock()
        radio._pcm_rx_user_callback = rx_cb
        radio._pcm_rx_jitter_depth = 5
        radio._pcm_tx_fmt = (48000, 1, 20)
        radio._pcm_transcoder = _DummyTranscoder()  # type: ignore[assignment]
        radio._pcm_transcoder_fmt = (48000, 1, 20)

        with (
            patch.object(radio, "connect", new_callable=AsyncMock),
            patch.object(
                radio, "start_audio_rx_pcm", new_callable=AsyncMock
            ) as mock_rx,
            patch.object(
                radio, "start_audio_tx_pcm", new_callable=AsyncMock
            ) as mock_tx,
        ):
            await radio._reconnect_loop()

        mock_rx.assert_awaited_once()
        mock_tx.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_opus_rx_recovered(self) -> None:
        """Opus RX should be restarted with same callback after reconnect."""
        radio = _make_radio()
        stream = _install_audio_stream(radio)
        stream.state = AudioState.RECEIVING

        opus_cb = MagicMock()
        radio._opus_rx_user_callback = opus_cb
        radio._opus_rx_jitter_depth = 3

        with (
            patch.object(radio, "connect", new_callable=AsyncMock),
            patch.object(
                radio, "start_audio_rx_opus", new_callable=AsyncMock
            ) as mock_start,
        ):
            await radio._reconnect_loop()

        mock_start.assert_awaited_once_with(
            opus_cb,
            jitter_depth=3,
        )

    @pytest.mark.asyncio
    async def test_recovery_disabled(self) -> None:
        """With auto_recover_audio=False, audio should NOT restart."""
        radio = _make_radio(auto_recover_audio=False)
        stream = _install_audio_stream(radio)
        stream.state = AudioState.RECEIVING

        radio._pcm_rx_user_callback = MagicMock()
        radio._pcm_transcoder = _DummyTranscoder()  # type: ignore[assignment]
        radio._pcm_transcoder_fmt = (48000, 1, 20)

        with (
            patch.object(radio, "connect", new_callable=AsyncMock),
            patch.object(
                radio, "start_audio_rx_pcm", new_callable=AsyncMock
            ) as mock_start,
        ):
            await radio._reconnect_loop()

        mock_start.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_recovery_callback_states(self) -> None:
        """on_audio_recovery should be called with RECOVERING then RECOVERED."""
        recovery_cb = MagicMock()
        radio = _make_radio(on_audio_recovery=recovery_cb)
        stream = _install_audio_stream(radio)
        stream.state = AudioState.RECEIVING

        radio._pcm_rx_user_callback = MagicMock()
        radio._pcm_rx_jitter_depth = 5
        radio._pcm_transcoder = _DummyTranscoder()  # type: ignore[assignment]
        radio._pcm_transcoder_fmt = (48000, 1, 20)

        with (
            patch.object(radio, "connect", new_callable=AsyncMock),
            patch.object(radio, "start_audio_rx_pcm", new_callable=AsyncMock),
        ):
            await radio._reconnect_loop()

        assert recovery_cb.call_count == 2
        recovery_cb.assert_any_call(AudioRecoveryState.RECOVERING)
        recovery_cb.assert_any_call(AudioRecoveryState.RECOVERED)
        # Verify order: RECOVERING first, then RECOVERED
        calls = [c.args[0] for c in recovery_cb.call_args_list]
        assert calls == [AudioRecoveryState.RECOVERING, AudioRecoveryState.RECOVERED]

    @pytest.mark.asyncio
    async def test_recovery_failure_logged_not_crashed(self, caplog) -> None:
        """Recovery failure should log warning and emit FAILED, not crash."""
        recovery_cb = MagicMock()
        radio = _make_radio(on_audio_recovery=recovery_cb)
        stream = _install_audio_stream(radio)
        stream.state = AudioState.RECEIVING

        radio._pcm_rx_user_callback = MagicMock()
        radio._pcm_rx_jitter_depth = 5
        radio._pcm_transcoder = _DummyTranscoder()  # type: ignore[assignment]
        radio._pcm_transcoder_fmt = (48000, 1, 20)

        with (
            patch.object(radio, "connect", new_callable=AsyncMock),
            patch.object(
                radio,
                "start_audio_rx_pcm",
                new_callable=AsyncMock,
                side_effect=RuntimeError("audio port gone"),
            ),
        ):
            with caplog.at_level(logging.WARNING, logger="icom_lan.runtime.radio"):
                await radio._reconnect_loop()

        # Should not have crashed
        assert any("audio" in r.message.lower() for r in caplog.records)
        recovery_cb.assert_any_call(AudioRecoveryState.FAILED)

    @pytest.mark.asyncio
    async def test_no_audio_active_skips_recovery(self) -> None:
        """If no audio was active, recovery should be skipped entirely."""
        recovery_cb = MagicMock()
        radio = _make_radio(on_audio_recovery=recovery_cb)
        # No audio stream installed

        with patch.object(radio, "connect", new_callable=AsyncMock):
            await radio._reconnect_loop()

        recovery_cb.assert_not_called()

    @pytest.mark.asyncio
    async def test_intentional_disconnect_clears_callbacks(self) -> None:
        """disconnect() should clear stored callbacks."""
        radio = _make_radio()
        radio._pcm_rx_user_callback = MagicMock()
        radio._opus_rx_user_callback = MagicMock()

        await radio.disconnect()

        assert radio._pcm_rx_user_callback is None
        assert radio._opus_rx_user_callback is None


# ---------------------------------------------------------------------------
# Callback storage at start time
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestCallbackStorageAtStart:
    @pytest.mark.asyncio
    async def test_start_audio_rx_pcm_stores_callback(self) -> None:
        """start_audio_rx_pcm should store the user callback on the radio."""
        radio = _make_radio()
        _install_audio_stream(radio)
        radio._pcm_transcoder = _DummyTranscoder()  # type: ignore[assignment]
        radio._pcm_transcoder_fmt = (48000, 1, 20)

        cb = MagicMock()
        await radio.start_audio_rx_pcm(cb, jitter_depth=7)

        assert radio._pcm_rx_user_callback is cb
        assert radio._pcm_rx_jitter_depth == 7

    @pytest.mark.asyncio
    async def test_stop_audio_rx_pcm_clears_callback(self) -> None:
        """stop_audio_rx_pcm should clear the stored callback."""
        radio = _make_radio()
        _install_audio_stream(radio)
        radio._pcm_rx_user_callback = MagicMock()

        await radio.stop_audio_rx_pcm()

        assert radio._pcm_rx_user_callback is None

    @pytest.mark.asyncio
    async def test_start_audio_rx_opus_stores_callback(self) -> None:
        """start_audio_rx_opus should store the callback on the radio."""
        radio = _make_radio()
        _install_audio_stream(radio)

        cb = MagicMock()
        await radio.start_audio_rx_opus(cb, jitter_depth=3)

        assert radio._opus_rx_user_callback is cb
        assert radio._opus_rx_jitter_depth == 3

    @pytest.mark.asyncio
    async def test_stop_audio_rx_opus_clears_callback(self) -> None:
        """stop_audio_rx_opus should clear the stored callback."""
        radio = _make_radio()
        _install_audio_stream(radio)
        radio._opus_rx_user_callback = MagicMock()

        await radio.stop_audio_rx_opus()

        assert radio._opus_rx_user_callback is None
