"""Internal PCM <-> Opus transcoder utilities.

This module is intentionally private and provides building blocks for
future PCM-first public audio APIs while preserving existing low-level
Opus APIs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from icom_lan.core.exceptions import AudioCodecBackendError, AudioFormatError, AudioTranscodeError

__all__ = [
    "PcmAudioFormat",
    "PcmOpusTranscoder",
    "create_pcm_opus_transcoder",
]

_INSTALL_HINT = "Audio codec backend unavailable; install icom-lan[audio]."
_VALID_SAMPLE_RATES = {8000, 12000, 16000, 24000, 48000}
_VALID_CHANNELS = {1, 2}
_VALID_FRAME_MS = {10, 20, 40, 60}
_PCM_SAMPLE_WIDTH = 2  # s16le


class _OpusBackend(Protocol):
    """Minimal backend interface used by :class:`PcmOpusTranscoder`."""

    def create_encoder(self, sample_rate: int, channels: int) -> Any: ...

    def create_decoder(self, sample_rate: int, channels: int) -> Any: ...

    def encode(self, encoder: Any, pcm_data: bytes, frame_samples: int) -> bytes: ...

    def decode(self, decoder: Any, opus_data: bytes, frame_samples: int) -> bytes: ...


class _OpuslibBackend:
    """Adapter around optional ``opuslib`` dependency."""

    def __init__(self, opuslib_module: Any) -> None:
        self._opuslib = opuslib_module

    def create_encoder(self, sample_rate: int, channels: int) -> Any:
        application = getattr(self._opuslib, "APPLICATION_AUDIO", 2049)
        return self._opuslib.Encoder(sample_rate, channels, application)

    def create_decoder(self, sample_rate: int, channels: int) -> Any:
        return self._opuslib.Decoder(sample_rate, channels)

    def encode(self, encoder: Any, pcm_data: bytes, frame_samples: int) -> bytes:
        out: bytes = encoder.encode(pcm_data, frame_samples)
        return out

    def decode(self, decoder: Any, opus_data: bytes, frame_samples: int) -> bytes:
        out: bytes = decoder.decode(opus_data, frame_samples)
        return out


def _load_default_backend() -> _OpusBackend | None:
    try:
        import opuslib  # noqa: F401
    except Exception:
        # opuslib raises bare ``Exception`` (not ImportError) when the native
        # libopus cannot be located — catch both cases so the backend falls
        # back gracefully to ``None`` instead of aborting import.
        return None
    return _OpuslibBackend(opuslib)


@dataclass(frozen=True, slots=True)
class PcmAudioFormat:
    """PCM stream format used for Opus transcoding."""

    sample_rate: int = 48000
    channels: int = 1
    frame_ms: int = 20
    sample_width: int = _PCM_SAMPLE_WIDTH

    @property
    def frame_samples(self) -> int:
        """Number of samples per channel in one frame."""
        return self.sample_rate * self.frame_ms // 1000

    @property
    def frame_bytes(self) -> int:
        """PCM frame size in bytes (interleaved s16le)."""
        return self.frame_samples * self.channels * self.sample_width


class PcmOpusTranscoder:
    """Internal transcoder between fixed-size PCM frames and Opus frames."""

    def __init__(
        self,
        fmt: PcmAudioFormat | None = None,
        *,
        backend: _OpusBackend | None = None,
    ) -> None:
        self._fmt = fmt if fmt is not None else PcmAudioFormat()
        self._validate_format(self._fmt)

        self._backend = backend if backend is not None else _load_default_backend()
        if self._backend is None:
            raise AudioCodecBackendError(_INSTALL_HINT)

        try:
            self._encoder = self._backend.create_encoder(
                self._fmt.sample_rate, self._fmt.channels
            )
            self._decoder = self._backend.create_decoder(
                self._fmt.sample_rate, self._fmt.channels
            )
        except Exception as exc:
            raise AudioCodecBackendError(
                "Failed to initialize Opus codec backend. "
                "Ensure icom-lan[audio] is installed and functional."
            ) from exc

    @property
    def fmt(self) -> PcmAudioFormat:
        """Active PCM format."""
        return self._fmt

    def pcm_to_opus(self, pcm_data: bytes | bytearray | memoryview) -> bytes:
        """Encode one PCM frame to Opus."""
        if self._backend is None:
            raise AudioCodecBackendError(_INSTALL_HINT)
        frame = self._coerce_pcm_frame(pcm_data)
        try:
            encoded = self._backend.encode(
                self._encoder, frame, self._fmt.frame_samples
            )
        except Exception as exc:
            raise AudioTranscodeError("Failed to encode PCM frame to Opus.") from exc
        if not isinstance(encoded, (bytes, bytearray, memoryview)):
            raise AudioTranscodeError("Opus encoder returned non-bytes output.")
        return bytes(encoded)

    def opus_to_pcm(self, opus_data: bytes | bytearray | memoryview) -> bytes:
        """Decode one Opus frame to PCM."""
        if self._backend is None:
            raise AudioCodecBackendError(_INSTALL_HINT)
        if not isinstance(opus_data, (bytes, bytearray, memoryview)):
            raise AudioFormatError("Opus input must be bytes-like.")
        opus_frame = bytes(opus_data)
        if not opus_frame:
            raise AudioFormatError("Opus frame must not be empty.")
        try:
            decoded = self._backend.decode(
                self._decoder,
                opus_frame,
                self._fmt.frame_samples,
            )
        except Exception as exc:
            raise AudioTranscodeError("Failed to decode Opus frame to PCM.") from exc
        if not isinstance(decoded, (bytes, bytearray, memoryview)):
            raise AudioTranscodeError("Opus decoder returned non-bytes output.")
        pcm_frame = bytes(decoded)
        if len(pcm_frame) != self._fmt.frame_bytes:
            raise AudioTranscodeError(
                f"Decoded PCM frame size mismatch: expected {self._fmt.frame_bytes} "
                f"bytes, got {len(pcm_frame)}."
            )
        return pcm_frame

    def _coerce_pcm_frame(self, pcm_data: bytes | bytearray | memoryview) -> bytes:
        if not isinstance(pcm_data, (bytes, bytearray, memoryview)):
            raise AudioFormatError("PCM input must be bytes-like.")
        frame = bytes(pcm_data)
        if len(frame) != self._fmt.frame_bytes:
            raise AudioFormatError(
                f"PCM frame size mismatch: expected {self._fmt.frame_bytes} bytes "
                f"({self._fmt.frame_ms}ms at {self._fmt.sample_rate}Hz, "
                f"{self._fmt.channels}ch s16le), got {len(frame)}."
            )
        return frame

    @staticmethod
    def _validate_format(fmt: PcmAudioFormat) -> None:
        if fmt.sample_rate not in _VALID_SAMPLE_RATES:
            raise AudioFormatError(
                f"Unsupported sample_rate={fmt.sample_rate}; supported values: "
                f"{sorted(_VALID_SAMPLE_RATES)}."
            )
        if fmt.channels not in _VALID_CHANNELS:
            raise AudioFormatError(
                f"Unsupported channels={fmt.channels}; supported values: "
                f"{sorted(_VALID_CHANNELS)}."
            )
        if fmt.frame_ms not in _VALID_FRAME_MS:
            raise AudioFormatError(
                f"Unsupported frame_ms={fmt.frame_ms}; supported values: "
                f"{sorted(_VALID_FRAME_MS)}."
            )
        if fmt.sample_width != _PCM_SAMPLE_WIDTH:
            raise AudioFormatError(
                f"Unsupported PCM sample_width={fmt.sample_width}; only "
                f"{_PCM_SAMPLE_WIDTH}-byte s16le is supported."
            )
        if (fmt.sample_rate * fmt.frame_ms) % 1000 != 0:
            raise AudioFormatError(
                "sample_rate * frame_ms must produce an integer frame size."
            )


def create_pcm_opus_transcoder(
    *,
    sample_rate: int = 48000,
    channels: int = 1,
    frame_ms: int = 20,
) -> PcmOpusTranscoder:
    """Factory used by audio internals to create a PCM/Opus transcoder."""
    return PcmOpusTranscoder(PcmAudioFormat(sample_rate, channels, frame_ms))
