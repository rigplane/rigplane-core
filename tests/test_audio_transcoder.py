"""Tests for internal PCM<->Opus transcoder helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from icom_lan.audio._transcoder import PcmAudioFormat, PcmOpusTranscoder
from icom_lan.audio import AudioPacket
from icom_lan.exceptions import (
    AudioCodecBackendError,
    ConnectionError,
    AudioFormatError,
    AudioTranscodeError,
)
from icom_lan.radio import IcomRadio
from _audio_stream_fake import FakeAudioStream


class _FakeBackend:
    def __init__(
        self,
        *,
        fail_encode: bool = False,
        fail_decode: bool = False,
        decode_size_mismatch: bool = False,
    ) -> None:
        self.fail_encode = fail_encode
        self.fail_decode = fail_decode
        self.decode_size_mismatch = decode_size_mismatch
        self.last_encode_frame_samples: int | None = None
        self.last_decode_frame_samples: int | None = None
        self.last_decode_channels: int | None = None

    def create_encoder(self, sample_rate: int, channels: int) -> tuple[int, int]:
        return (sample_rate, channels)

    def create_decoder(self, sample_rate: int, channels: int) -> tuple[int, int]:
        return (sample_rate, channels)

    def encode(
        self,
        encoder: tuple[int, int],
        pcm_data: bytes,
        frame_samples: int,
    ) -> bytes:
        _ = encoder
        self.last_encode_frame_samples = frame_samples
        if self.fail_encode:
            raise RuntimeError("encode boom")
        return b"OPUS" + pcm_data[:4]

    def decode(
        self,
        decoder: tuple[int, int],
        opus_data: bytes,
        frame_samples: int,
    ) -> bytes:
        _ = opus_data
        self.last_decode_frame_samples = frame_samples
        self.last_decode_channels = decoder[1]
        if self.fail_decode:
            raise RuntimeError("decode boom")
        if self.decode_size_mismatch:
            return b"\x00"
        return b"\x01" * (frame_samples * decoder[1] * 2)


class TestPcmOpusTranscoder:
    def test_missing_backend_raises_actionable_error(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "icom_lan._audio_transcoder._load_default_backend", lambda: None
        )
        with pytest.raises(AudioCodecBackendError, match="install icom-lan\\[audio\\]"):
            PcmOpusTranscoder()

    def test_format_validation_error(self) -> None:
        with pytest.raises(AudioFormatError, match="Unsupported sample_rate"):
            PcmOpusTranscoder(PcmAudioFormat(sample_rate=44100), backend=_FakeBackend())

    def test_pcm_opus_roundtrip_happy_path(self) -> None:
        backend = _FakeBackend()
        fmt = PcmAudioFormat(sample_rate=48000, channels=1, frame_ms=20)
        transcoder = PcmOpusTranscoder(fmt, backend=backend)

        pcm = b"\x10\x00" * fmt.frame_samples
        opus = transcoder.pcm_to_opus(pcm)
        decoded = transcoder.opus_to_pcm(opus)

        assert opus.startswith(b"OPUS")
        assert len(decoded) == fmt.frame_bytes
        assert backend.last_encode_frame_samples == fmt.frame_samples
        assert backend.last_decode_frame_samples == fmt.frame_samples

    def test_pcm_frame_size_error(self) -> None:
        transcoder = PcmOpusTranscoder(backend=_FakeBackend())
        with pytest.raises(AudioFormatError, match="PCM frame size mismatch"):
            transcoder.pcm_to_opus(b"\x00" * 16)

    def test_empty_opus_frame_error(self) -> None:
        transcoder = PcmOpusTranscoder(backend=_FakeBackend())
        with pytest.raises(AudioFormatError, match="must not be empty"):
            transcoder.opus_to_pcm(b"")

    def test_encode_failure_wrapped(self) -> None:
        transcoder = PcmOpusTranscoder(backend=_FakeBackend(fail_encode=True))
        pcm = b"\x00\x00" * transcoder.fmt.frame_samples
        with pytest.raises(AudioTranscodeError, match="encode PCM frame"):
            transcoder.pcm_to_opus(pcm)

    def test_decode_failure_wrapped(self) -> None:
        transcoder = PcmOpusTranscoder(backend=_FakeBackend(fail_decode=True))
        with pytest.raises(AudioTranscodeError, match="decode Opus frame"):
            transcoder.opus_to_pcm(b"\xaa")

    def test_decode_size_mismatch_wrapped(self) -> None:
        transcoder = PcmOpusTranscoder(backend=_FakeBackend(decode_size_mismatch=True))
        with pytest.raises(AudioTranscodeError, match="size mismatch"):
            transcoder.opus_to_pcm(b"\xaa")


class TestRadioPcmHooks:
    @pytest.mark.asyncio
    async def test_push_audio_tx_pcm_internal_uses_transcoder(self) -> None:
        radio = IcomRadio("192.168.1.100")
        radio.push_audio_tx_opus = AsyncMock()  # type: ignore[method-assign]

        class _DummyTranscoder:
            def pcm_to_opus(self, pcm: bytes) -> bytes:
                return b"opus:" + pcm[:2]

        radio._pcm_transcoder = _DummyTranscoder()  # type: ignore[assignment]
        radio._pcm_transcoder_fmt = (48000, 1, 20)

        await radio._push_audio_tx_pcm_internal(b"\x01\x02" * 960)
        radio.push_audio_tx_opus.assert_awaited_once_with(b"opus:\x01\x02")

    def test_decode_audio_packet_to_pcm_and_callback_adapter(self) -> None:
        radio = IcomRadio("192.168.1.100")

        class _DummyTranscoder:
            def opus_to_pcm(self, opus: bytes) -> bytes:
                return b"pcm:" + opus

        radio._pcm_transcoder = _DummyTranscoder()  # type: ignore[assignment]
        radio._pcm_transcoder_fmt = (48000, 1, 20)

        packet = AudioPacket(ident=0x0080, send_seq=1, data=b"\xaa\xbb")
        decoded = radio._decode_audio_packet_to_pcm(packet)
        assert decoded == b"pcm:\xaa\xbb"

        received: list[bytes | None] = []
        adapter = radio._build_pcm_rx_callback(lambda frame: received.append(frame))
        adapter(None)
        adapter(packet)

        assert received == [None, b"pcm:\xaa\xbb"]


class TestRadioPcmRxApi:
    @pytest.mark.asyncio
    async def test_start_audio_rx_pcm_decodes_and_forwards_gaps(self) -> None:
        radio = IcomRadio("192.168.1.100")
        radio._connected = True
        radio._civ_transport = MagicMock()
        fake_stream = FakeAudioStream()
        radio._audio_stream = fake_stream  # type: ignore[assignment]

        class _DummyTranscoder:
            def opus_to_pcm(self, opus: bytes) -> bytes:
                return b"pcm:" + opus

        radio._pcm_transcoder = _DummyTranscoder()  # type: ignore[assignment]
        radio._pcm_transcoder_fmt = (48000, 1, 20)

        received: list[bytes | None] = []
        await radio.start_audio_rx_pcm(
            lambda frame: received.append(frame),
            jitter_depth=7,
        )

        assert fake_stream.start_rx_count == 1
        rx_callback = fake_stream.last_start_rx_callback
        assert fake_stream.last_start_rx_jitter_depth == 7

        rx_callback(AudioPacket(ident=0x0080, send_seq=10, data=b"\x01\x02"))
        rx_callback(None)

        assert received == [b"pcm:\x01\x02", None]

    @pytest.mark.asyncio
    async def test_stop_audio_rx_pcm_delegates(self) -> None:
        radio = IcomRadio("192.168.1.100")
        fake_stream = FakeAudioStream()
        radio._audio_stream = fake_stream  # type: ignore[assignment]

        await radio.stop_audio_rx_pcm()
        assert fake_stream.stop_rx_count == 1

    @pytest.mark.asyncio
    async def test_start_audio_rx_pcm_invalid_callback(self) -> None:
        radio = IcomRadio("192.168.1.100")
        with pytest.raises(TypeError, match="callback must be callable"):
            await radio.start_audio_rx_pcm(None)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_start_audio_rx_pcm_invalid_jitter_depth_type(self) -> None:
        radio = IcomRadio("192.168.1.100")
        with pytest.raises(TypeError, match="jitter_depth must be an int"):
            await radio.start_audio_rx_pcm(lambda _: None, jitter_depth=1.5)  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_start_audio_rx_pcm_invalid_jitter_depth_value(self) -> None:
        radio = IcomRadio("192.168.1.100")
        with pytest.raises(ValueError, match="jitter_depth must be >= 0"):
            await radio.start_audio_rx_pcm(lambda _: None, jitter_depth=-1)

    @pytest.mark.asyncio
    async def test_start_audio_rx_pcm_invalid_format(self) -> None:
        radio = IcomRadio("192.168.1.100")
        radio._connected = True
        radio._civ_transport = MagicMock()
        with pytest.raises(AudioFormatError, match="Unsupported sample_rate"):
            await radio.start_audio_rx_pcm(lambda _: None, sample_rate=44100)

    @pytest.mark.asyncio
    async def test_start_audio_rx_pcm_disconnected(self) -> None:
        radio = IcomRadio("192.168.1.100")
        with pytest.raises(ConnectionError, match="Not connected to radio"):
            await radio.start_audio_rx_pcm(lambda _: None)


class TestRadioPcmTxApi:
    @pytest.mark.asyncio
    async def test_start_audio_tx_pcm_starts_opus_and_tracks_format(self) -> None:
        radio = IcomRadio("192.168.1.100")
        radio._connected = True
        radio._civ_transport = MagicMock()
        fake_stream = FakeAudioStream()
        radio._audio_stream = fake_stream  # type: ignore[assignment]
        radio._pcm_transcoder = object()  # type: ignore[assignment]
        radio._pcm_transcoder_fmt = (48000, 1, 20)

        await radio.start_audio_tx_pcm(sample_rate=48000, channels=1, frame_ms=20)

        assert fake_stream.start_tx_count == 1
        assert radio._pcm_tx_fmt == (48000, 1, 20)

    @pytest.mark.asyncio
    async def test_push_audio_tx_pcm_encodes_and_sends(self) -> None:
        radio = IcomRadio("192.168.1.100")
        radio._connected = True
        radio._civ_transport = MagicMock()
        radio._pcm_tx_fmt = (48000, 1, 20)
        radio.push_audio_tx_opus = AsyncMock()  # type: ignore[method-assign]

        class _DummyTranscoder:
            def pcm_to_opus(self, pcm: bytes | bytearray | memoryview) -> bytes:
                return b"opus:" + bytes(pcm)[:2]

        radio._pcm_transcoder = _DummyTranscoder()  # type: ignore[assignment]
        radio._pcm_transcoder_fmt = (48000, 1, 20)

        await radio.push_audio_tx_pcm(b"\x01\x02" * 960)
        radio.push_audio_tx_opus.assert_awaited_once_with(b"opus:\x01\x02")

    @pytest.mark.asyncio
    async def test_push_audio_tx_pcm_not_started(self) -> None:
        radio = IcomRadio("192.168.1.100")
        radio._connected = True
        radio._civ_transport = MagicMock()

        with pytest.raises(RuntimeError, match="start_audio_tx_pcm"):
            await radio.push_audio_tx_pcm(b"\x00" * 1920)

    @pytest.mark.asyncio
    async def test_start_audio_tx_pcm_invalid_types(self) -> None:
        radio = IcomRadio("192.168.1.100")
        with pytest.raises(TypeError, match="sample_rate must be an int"):
            await radio.start_audio_tx_pcm(sample_rate=True)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="channels must be an int"):
            await radio.start_audio_tx_pcm(channels=1.5)  # type: ignore[arg-type]
        with pytest.raises(TypeError, match="frame_ms must be an int"):
            await radio.start_audio_tx_pcm(frame_ms="20")  # type: ignore[arg-type]

    @pytest.mark.asyncio
    async def test_start_audio_tx_pcm_invalid_format(self) -> None:
        radio = IcomRadio("192.168.1.100")
        radio._connected = True
        radio._civ_transport = MagicMock()

        with pytest.raises(AudioFormatError, match="Unsupported sample_rate"):
            await radio.start_audio_tx_pcm(sample_rate=44100)

    @pytest.mark.asyncio
    async def test_start_audio_tx_pcm_backend_error_is_actionable(self) -> None:
        radio = IcomRadio("192.168.1.100")
        radio._connected = True
        radio._civ_transport = MagicMock()
        fake_stream = FakeAudioStream()
        radio._audio_stream = fake_stream  # type: ignore[assignment]
        radio._get_pcm_transcoder = MagicMock(  # type: ignore[method-assign]
            side_effect=AudioCodecBackendError(
                "Audio codec backend unavailable; install icom-lan[audio]."
            )
        )

        with pytest.raises(AudioCodecBackendError, match="install icom-lan\\[audio\\]"):
            await radio.start_audio_tx_pcm()
        assert fake_stream.start_tx_count == 0

    @pytest.mark.asyncio
    async def test_push_audio_tx_pcm_frame_size_error(self) -> None:
        radio = IcomRadio("192.168.1.100")
        radio._connected = True
        radio._civ_transport = MagicMock()
        radio._pcm_tx_fmt = (48000, 1, 20)

        class _DummyTranscoder:
            def pcm_to_opus(self, pcm: bytes | bytearray | memoryview) -> bytes:
                _ = pcm
                raise AudioFormatError(
                    "PCM frame size mismatch: expected 1920 bytes, got 1919."
                )

        radio._pcm_transcoder = _DummyTranscoder()  # type: ignore[assignment]
        radio._pcm_transcoder_fmt = (48000, 1, 20)

        with pytest.raises(AudioFormatError, match="expected 1920 bytes"):
            await radio.push_audio_tx_pcm(b"\x00" * 1919)

    @pytest.mark.asyncio
    async def test_stop_audio_tx_pcm_delegates_and_clears_state(self) -> None:
        radio = IcomRadio("192.168.1.100")
        fake_stream = FakeAudioStream()
        radio._audio_stream = fake_stream  # type: ignore[assignment]
        radio._pcm_tx_fmt = (48000, 1, 20)

        await radio.stop_audio_tx_pcm()
        assert fake_stream.stop_tx_count == 1
        assert radio._pcm_tx_fmt is None

    @pytest.mark.asyncio
    async def test_start_audio_tx_pcm_disconnected(self) -> None:
        radio = IcomRadio("192.168.1.100")
        with pytest.raises(ConnectionError, match="Not connected to radio"):
            await radio.start_audio_tx_pcm()
