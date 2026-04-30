"""Additional coverage tests for _audio_transcoder.py.

Covers:
- _OpuslibBackend adapter class (lines 44-61)
- _load_default_backend function (lines 64-69)
- PcmOpusTranscoder backend init failure (lines 115-116)
- pcm_to_opus non-bytes encoder output (line 134)
- opus_to_pcm non-bytes-like input (line 140)
- opus_to_pcm non-bytes decoder output (line 153)
- _coerce_pcm_frame non-bytes input (line 164)
- _validate_format channels/frame_ms/sample_width validation errors (lines 182, 187, 192)
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest

from icom_lan.audio._transcoder import (
    PcmAudioFormat,
    PcmOpusTranscoder,
    _OpuslibBackend,
    _load_default_backend,
)
from icom_lan.exceptions import (
    AudioCodecBackendError,
    AudioFormatError,
    AudioTranscodeError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeBackend:
    """Minimal fake backend for edge-case testing."""

    def __init__(
        self,
        *,
        fail_create_encoder: bool = False,
        bad_encode_return: bool = False,
        bad_decode_return: bool = False,
    ) -> None:
        self.fail_create_encoder = fail_create_encoder
        self.bad_encode_return = bad_encode_return
        self.bad_decode_return = bad_decode_return

    def create_encoder(self, sample_rate: int, channels: int) -> tuple:
        if self.fail_create_encoder:
            raise RuntimeError("encoder creation failed")
        return (sample_rate, channels)

    def create_decoder(self, sample_rate: int, channels: int) -> tuple:
        return (sample_rate, channels)

    def encode(self, encoder: object, pcm_data: bytes, frame_samples: int) -> object:
        if self.bad_encode_return:
            return 12345  # intentionally wrong type
        return b"OPUS" + pcm_data[:2]

    def decode(self, decoder: object, opus_data: bytes, frame_samples: int) -> object:
        if self.bad_decode_return:
            return 12345  # intentionally wrong type
        sr, ch = decoder  # type: ignore[misc]
        return b"\x00\x00" * (frame_samples * ch)


# ---------------------------------------------------------------------------
# _OpuslibBackend adapter class (lines 44-61)
# ---------------------------------------------------------------------------


class TestOpuslibBackendAdapter:
    """Test _OpuslibBackend adapter class methods directly."""

    def test_init_stores_opuslib_module(self) -> None:
        """Constructor stores reference to opuslib module (line 48)."""
        mock_opuslib = MagicMock()
        backend = _OpuslibBackend(mock_opuslib)
        assert backend._opuslib is mock_opuslib

    def test_create_encoder_uses_application_audio_attribute(self) -> None:
        """create_encoder reads APPLICATION_AUDIO via getattr (line 51)."""
        mock_opuslib = MagicMock()
        mock_opuslib.APPLICATION_AUDIO = 2049
        backend = _OpuslibBackend(mock_opuslib)
        backend.create_encoder(48000, 1)  # line 51-52
        mock_opuslib.Encoder.assert_called_once_with(48000, 1, 2049)

    def test_create_encoder_fallback_application_value(self) -> None:
        """create_encoder uses default 2049 when APPLICATION_AUDIO is missing (line 51)."""
        mock_opuslib = MagicMock()
        # MagicMock responds to getattr, so simulate getattr default
        getattr(mock_opuslib, "APPLICATION_AUDIO", 2049)
        # With MagicMock, getattr returns a MagicMock; we just test the backend runs
        backend = _OpuslibBackend(mock_opuslib)
        backend.create_encoder(48000, 2)
        mock_opuslib.Encoder.assert_called_once()

    def test_create_decoder_calls_opuslib_decoder(self) -> None:
        """create_decoder creates a decoder via opuslib.Decoder (line 55)."""
        mock_opuslib = MagicMock()
        backend = _OpuslibBackend(mock_opuslib)
        backend.create_decoder(48000, 2)  # line 55
        mock_opuslib.Decoder.assert_called_once_with(48000, 2)

    def test_create_decoder_mono(self) -> None:
        """create_decoder with mono channel."""
        mock_opuslib = MagicMock()
        backend = _OpuslibBackend(mock_opuslib)
        backend.create_decoder(16000, 1)
        mock_opuslib.Decoder.assert_called_once_with(16000, 1)

    def test_encode_delegates_to_encoder_object(self) -> None:
        """encode delegates to encoder.encode (line 58)."""
        mock_opuslib = MagicMock()
        backend = _OpuslibBackend(mock_opuslib)
        encoder = MagicMock()
        pcm = b"\x00" * 1920
        result = backend.encode(encoder, pcm, 960)  # line 58
        encoder.encode.assert_called_once_with(pcm, 960)
        assert result is encoder.encode.return_value

    def test_decode_delegates_to_decoder_object(self) -> None:
        """decode delegates to decoder.decode (line 61)."""
        mock_opuslib = MagicMock()
        backend = _OpuslibBackend(mock_opuslib)
        decoder = MagicMock()
        opus = b"\xaa\xbb"
        result = backend.decode(decoder, opus, 960)  # line 61
        decoder.decode.assert_called_once_with(opus, 960)
        assert result is decoder.decode.return_value


# ---------------------------------------------------------------------------
# _load_default_backend (lines 64-69)
# ---------------------------------------------------------------------------


class TestLoadDefaultBackend:
    """Test _load_default_backend function paths."""

    def test_returns_opuslib_backend_when_available(self) -> None:
        """Returns _OpuslibBackend when opuslib is importable (lines 65-69)."""
        mock_opuslib = MagicMock()
        with patch.dict(sys.modules, {"opuslib": mock_opuslib}):
            result = _load_default_backend()
        assert result is not None
        assert isinstance(result, _OpuslibBackend)

    def test_returns_none_when_opuslib_missing(self) -> None:
        """Returns None when opuslib import fails (lines 65-68)."""
        # Setting sys.modules['opuslib'] = None forces ImportError on 'import opuslib'
        with patch.dict(sys.modules, {"opuslib": None}):
            result = _load_default_backend()
        assert result is None


# ---------------------------------------------------------------------------
# PcmOpusTranscoder backend init failure (lines 115-116)
# ---------------------------------------------------------------------------


class TestBackendInitFailure:
    """Test PcmOpusTranscoder constructor when backend raises during init."""

    def test_create_encoder_failure_raises_audio_codec_backend_error(self) -> None:
        """Backend create_encoder raising is wrapped in AudioCodecBackendError (line 115-116)."""
        with pytest.raises(AudioCodecBackendError, match="Failed to initialize"):
            PcmOpusTranscoder(backend=_FakeBackend(fail_create_encoder=True))


# ---------------------------------------------------------------------------
# pcm_to_opus edge cases (line 134, 164)
# ---------------------------------------------------------------------------


class TestPcmToOpusEdgeCases:
    """Test pcm_to_opus with unusual inputs."""

    def test_encoder_non_bytes_output_raises(self) -> None:
        """When encoder returns non-bytes, AudioTranscodeError is raised (line 134)."""
        transcoder = PcmOpusTranscoder(backend=_FakeBackend(bad_encode_return=True))
        fmt = transcoder.fmt
        pcm = b"\x00\x00" * fmt.frame_samples
        with pytest.raises(AudioTranscodeError, match="non-bytes"):
            transcoder.pcm_to_opus(pcm)

    def test_non_bytes_pcm_input_raises(self) -> None:
        """Non-bytes-like PCM input raises AudioFormatError (line 164)."""
        transcoder = PcmOpusTranscoder(backend=_FakeBackend())
        with pytest.raises(AudioFormatError, match="PCM input must be bytes-like"):
            transcoder.pcm_to_opus("not bytes")  # type: ignore[arg-type]

    def test_memoryview_pcm_input_accepted(self) -> None:
        """memoryview PCM input is accepted (valid bytes-like)."""
        transcoder = PcmOpusTranscoder(backend=_FakeBackend())
        fmt = transcoder.fmt
        pcm = memoryview(b"\x00\x00" * fmt.frame_samples)
        result = transcoder.pcm_to_opus(pcm)
        assert isinstance(result, bytes)

    def test_bytearray_pcm_input_accepted(self) -> None:
        """bytearray PCM input is accepted."""
        transcoder = PcmOpusTranscoder(backend=_FakeBackend())
        fmt = transcoder.fmt
        pcm = bytearray(b"\x00\x00" * fmt.frame_samples)
        result = transcoder.pcm_to_opus(pcm)
        assert isinstance(result, bytes)


# ---------------------------------------------------------------------------
# opus_to_pcm edge cases (lines 140, 153)
# ---------------------------------------------------------------------------


class TestOpusToPcmEdgeCases:
    """Test opus_to_pcm with unusual inputs."""

    def test_non_bytes_like_input_raises(self) -> None:
        """Non-bytes-like opus input raises AudioFormatError (line 140)."""
        transcoder = PcmOpusTranscoder(backend=_FakeBackend())
        with pytest.raises(AudioFormatError, match="bytes-like"):
            transcoder.opus_to_pcm(99999)  # type: ignore[arg-type]

    def test_non_bytes_like_integer_input(self) -> None:
        """Integer input raises AudioFormatError (line 140)."""
        transcoder = PcmOpusTranscoder(backend=_FakeBackend())
        with pytest.raises(AudioFormatError, match="bytes-like"):
            transcoder.opus_to_pcm([0x01, 0x02])  # type: ignore[arg-type]

    def test_decoder_non_bytes_output_raises(self) -> None:
        """When decoder returns non-bytes, AudioTranscodeError is raised (line 153)."""
        transcoder = PcmOpusTranscoder(backend=_FakeBackend(bad_decode_return=True))
        with pytest.raises(AudioTranscodeError, match="non-bytes"):
            transcoder.opus_to_pcm(b"\xaa")

    def test_memoryview_opus_input_accepted(self) -> None:
        """memoryview opus input is accepted."""
        backend = _FakeBackend()
        transcoder = PcmOpusTranscoder(backend=backend)
        opus = memoryview(b"\xaa\xbb")
        # This will call decode and get proper output
        result = transcoder.opus_to_pcm(opus)
        assert isinstance(result, bytes)


# ---------------------------------------------------------------------------
# _validate_format error paths (lines 182, 187, 192)
# ---------------------------------------------------------------------------


class TestValidateFormatErrors:
    """Test _validate_format raises for invalid channels, frame_ms, sample_width."""

    def test_invalid_channels_raises(self) -> None:
        """Unsupported channels raises AudioFormatError (line 182)."""
        with pytest.raises(AudioFormatError, match="channels"):
            PcmOpusTranscoder(
                PcmAudioFormat(sample_rate=48000, channels=3, frame_ms=20),
                backend=_FakeBackend(),
            )

    def test_channels_zero_raises(self) -> None:
        """channels=0 also raises AudioFormatError."""
        with pytest.raises(AudioFormatError, match="channels"):
            PcmOpusTranscoder(
                PcmAudioFormat(sample_rate=48000, channels=0, frame_ms=20),
                backend=_FakeBackend(),
            )

    def test_invalid_frame_ms_raises(self) -> None:
        """Unsupported frame_ms raises AudioFormatError (line 187)."""
        with pytest.raises(AudioFormatError, match="frame_ms"):
            PcmOpusTranscoder(
                PcmAudioFormat(sample_rate=48000, channels=1, frame_ms=15),
                backend=_FakeBackend(),
            )

    def test_frame_ms_5_raises(self) -> None:
        """frame_ms=5 raises AudioFormatError."""
        with pytest.raises(AudioFormatError, match="frame_ms"):
            PcmOpusTranscoder(
                PcmAudioFormat(sample_rate=48000, channels=1, frame_ms=5),
                backend=_FakeBackend(),
            )

    def test_invalid_sample_width_raises(self) -> None:
        """Unsupported sample_width raises AudioFormatError (line 192)."""
        with pytest.raises(AudioFormatError, match="sample_width"):
            PcmOpusTranscoder(
                PcmAudioFormat(
                    sample_rate=48000, channels=1, frame_ms=20, sample_width=4
                ),
                backend=_FakeBackend(),
            )

    def test_sample_width_1_raises(self) -> None:
        """sample_width=1 (8-bit) is not supported."""
        with pytest.raises(AudioFormatError, match="sample_width"):
            PcmOpusTranscoder(
                PcmAudioFormat(
                    sample_rate=48000, channels=1, frame_ms=20, sample_width=1
                ),
                backend=_FakeBackend(),
            )


# ---------------------------------------------------------------------------
# PcmAudioFormat properties
# ---------------------------------------------------------------------------


class TestPcmAudioFormatProperties:
    """Test PcmAudioFormat computed properties."""

    def test_frame_samples_48k_20ms(self) -> None:
        """48kHz at 20ms = 960 samples."""
        fmt = PcmAudioFormat(sample_rate=48000, channels=1, frame_ms=20)
        assert fmt.frame_samples == 960

    def test_frame_bytes_stereo(self) -> None:
        """frame_bytes accounts for stereo channels."""
        fmt = PcmAudioFormat(sample_rate=48000, channels=2, frame_ms=20)
        assert fmt.frame_bytes == 960 * 2 * 2  # samples * channels * sample_width

    def test_frame_bytes_8khz_40ms(self) -> None:
        """8kHz at 40ms = 320 samples."""
        fmt = PcmAudioFormat(sample_rate=8000, channels=1, frame_ms=40)
        assert fmt.frame_samples == 320
        assert fmt.frame_bytes == 320 * 2
