"""Optional PCM resampling using numpy linear interpolation.

Used when a device doesn't support the radio's native 48kHz sample rate.
No new dependencies — uses numpy which is already required by ``[bridge]``.

Usage::

    resampler = PcmResampler(from_rate=48000, to_rate=44100, channels=1)
    out_bytes = resampler.process(in_bytes)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)

__all__ = ["PcmResampler", "negotiate_sample_rate"]

# Preferred sample rates in descending priority for ham radio audio.
_PREFERRED_RATES = (48_000, 44_100, 96_000, 32_000, 24_000, 16_000, 8_000)


@dataclass(frozen=True, slots=True)
class SampleRateNegotiation:
    """Result of sample-rate negotiation."""

    device_rate: int
    radio_rate: int
    needs_resample: bool


def negotiate_sample_rate(
    backend: object,
    device_id: object,
    *,
    radio_rate: int = 48_000,
    direction: str = "rx",
) -> SampleRateNegotiation:
    """Negotiate a sample rate between the radio and a device.

    Tries *radio_rate* first; if unsupported, falls back through
    :data:`_PREFERRED_RATES`.

    Args:
        backend: An :class:`AudioBackend` instance.
        device_id: Target device id.
        radio_rate: Radio's native rate (default 48000).
        direction: ``"rx"`` or ``"tx"``.

    Returns:
        A :class:`SampleRateNegotiation` result.
    """
    check = getattr(backend, "check_sample_rate", None)
    if check is None:
        return SampleRateNegotiation(
            device_rate=radio_rate, radio_rate=radio_rate, needs_resample=False
        )

    if check(device_id, radio_rate, direction=direction):
        return SampleRateNegotiation(
            device_rate=radio_rate, radio_rate=radio_rate, needs_resample=False
        )

    for rate in _PREFERRED_RATES:
        if rate != radio_rate and check(device_id, rate, direction=direction):
            logger.warning(
                "Device does not support %dHz — will resample %dHz ↔ %dHz",
                radio_rate,
                radio_rate,
                rate,
            )
            return SampleRateNegotiation(
                device_rate=rate, radio_rate=radio_rate, needs_resample=True
            )

    logger.warning(
        "No common sample rate found; falling back to %dHz without resampling",
        radio_rate,
    )
    return SampleRateNegotiation(
        device_rate=radio_rate, radio_rate=radio_rate, needs_resample=False
    )


class PcmResampler:
    """Resamples PCM s16le frames between two sample rates using numpy.

    This is a simple linear interpolation resampler suitable for voice-grade
    ham radio audio. Not suitable for music or wideband signals.

    Args:
        from_rate: Source sample rate in Hz.
        to_rate: Target sample rate in Hz.
        channels: Number of audio channels (default 1).
    """

    def __init__(
        self,
        from_rate: int,
        to_rate: int,
        channels: int = 1,
    ) -> None:
        if from_rate <= 0 or to_rate <= 0:
            raise ValueError("Sample rates must be positive.")
        self._from_rate = from_rate
        self._to_rate = to_rate
        self._channels = channels
        self._ratio = to_rate / from_rate
        self._aa_kernel: object | None = None  # lazy-built anti-aliasing kernel

    @property
    def from_rate(self) -> int:
        return self._from_rate

    @property
    def to_rate(self) -> int:
        return self._to_rate

    @property
    def ratio(self) -> float:
        return self._ratio

    @property
    def identity(self) -> bool:
        """True when from_rate == to_rate (no-op)."""
        return self._from_rate == self._to_rate

    def _get_aa_kernel(self, np: object) -> object:
        """Build a windowed-sinc anti-aliasing FIR kernel for downsampling."""
        if self._aa_kernel is not None:
            return self._aa_kernel
        # Kernel length proportional to decimation ratio, minimum 5 taps
        cutoff = self._ratio  # normalized cutoff (0..1 of Nyquist)
        n_taps = max(5, int(4.0 / cutoff)) | 1  # ensure odd
        half = n_taps // 2
        n = np.arange(n_taps) - half  # type: ignore[attr-defined]
        # Sinc * Hann window
        with np.errstate(divide="ignore", invalid="ignore"):  # type: ignore[attr-defined]
            h = np.sinc(n * cutoff) * cutoff  # type: ignore[attr-defined]
        window = 0.5 * (1 - np.cos(2 * np.pi * np.arange(n_taps) / (n_taps - 1)))  # type: ignore[attr-defined]
        h = h * window
        h = h / h.sum()  # normalize
        self._aa_kernel = h
        return h

    def _apply_aa_filter(self, samples: object, np: object) -> object:
        """Apply anti-aliasing low-pass filter before downsampling."""
        kernel = self._get_aa_kernel(np)
        return np.convolve(samples, kernel, mode="same")  # type: ignore[attr-defined]

    def process(self, pcm: bytes) -> bytes:
        """Resample a PCM s16le frame.

        When downsampling, an anti-aliasing low-pass filter is applied
        before interpolation to prevent aliasing distortion.

        Args:
            pcm: Input PCM bytes (s16le, interleaved if multi-channel).

        Returns:
            Resampled PCM bytes.
        """
        if self.identity:
            return pcm

        import numpy as np

        samples = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
        downsample = self._ratio < 1.0

        if self._channels > 1:
            samples = samples.reshape(-1, self._channels)
            n_in = samples.shape[0]
            n_out = max(1, round(n_in * self._ratio))
            x_in = np.linspace(0, 1, n_in, endpoint=False)
            x_out = np.linspace(0, 1, n_out, endpoint=False)
            cols = []
            for ch in range(self._channels):
                col = samples[:, ch]
                if downsample:
                    col = self._apply_aa_filter(col, np)
                cols.append(np.interp(x_out, x_in, col))
            resampled = np.column_stack(cols)
        else:
            n_in = len(samples)
            n_out = max(1, round(n_in * self._ratio))
            x_in = np.linspace(0, 1, n_in, endpoint=False)
            x_out = np.linspace(0, 1, n_out, endpoint=False)
            if downsample:
                samples = self._apply_aa_filter(samples, np)
            resampled = np.interp(x_out, x_in, samples)

        return np.clip(resampled, -32768, 32767).astype(np.int16).tobytes()  # type: ignore[no-any-return]
