"""Deterministic fake IQ source for tests (MOR-3151).

The test-side counterpart of the future SoapySDR adapter: generates
phase-continuous complex64 blocks (configured tones over a seeded noise
floor) either synchronously via :meth:`FakeIqSource.pump` or from an
optional background reader thread for integration tests.
"""

from __future__ import annotations

import math
import threading
import time
from typing import Any, Callable, Sequence

from .types import IqBlock

__all__ = ["FakeIqSource"]


def _import_numpy() -> Any:
    """Lazy-import numpy to avoid a hard dependency at module level."""
    from rigplane.core._optional_deps import _require_numpy

    _require_numpy()
    import numpy as np

    return np


class FakeIqSource:
    """Deterministic in-process :class:`~rigplane.sdr.protocol.IqSource`.

    Emits blocks of ``block_size`` complex64 samples: the sum of the
    configured ``tones`` (each ``(offset_hz, dbfs)`` relative to the
    center frequency) over a white-noise floor at ``noise_floor_dbfs``.
    Sample generation is seeded and phase-continuous, so equal
    configurations with equal seeds produce identical sample streams.

    Args:
        center_freq_hz: Initial center frequency in Hz.
        sample_rate_hz: Complex sample rate in Hz.
        block_size: Samples per emitted :class:`IqBlock`.
        tones: ``(offset_hz, dbfs)`` pairs to inject; full scale = 0 dBFS.
        noise_floor_dbfs: White-noise floor level in dBFS.
        frequency_range: ``(low, high)`` reported by
            :meth:`frequency_range_hz`.
        seed: Seed for the noise RNG; blocks are identical across
            instances built with equal settings and equal seeds.
        background_interval_s: When not ``None``, ``open()`` starts a
            daemon reader thread that emits one block per interval.
    """

    def __init__(
        self,
        *,
        center_freq_hz: int = 0,
        sample_rate_hz: int = 2_400_000,
        block_size: int = 4096,
        tones: Sequence[tuple[float, float]] = (),
        noise_floor_dbfs: float = -90.0,
        frequency_range: tuple[int, int] = (0, 6_000_000_000),
        seed: int = 0,
        background_interval_s: float | None = None,
    ) -> None:
        np = _import_numpy()
        self._np = np
        self._rng = np.random.default_rng(seed)

        self._center_freq = int(center_freq_hz)
        self._sample_rate = int(sample_rate_hz)
        self._block_size = int(block_size)
        self._tones = tuple(
            (float(offset_hz), float(dbfs)) for offset_hz, dbfs in tones
        )
        self._noise_floor_dbfs = float(noise_floor_dbfs)
        self._frequency_range = (
            int(frequency_range[0]),
            int(frequency_range[1]),
        )
        self._background_interval_s = background_interval_s

        self._gain_db: float | None = None
        self._callback: Callable[[IqBlock], None] | None = None
        self._is_open = False
        self._overflow = False
        self._sample_index = 0
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    # -- IqSource lifecycle ---------------------------------------------------

    def open(self) -> None:
        """Open the fake source; starts the background thread when
        ``background_interval_s`` was set."""
        if self._is_open:
            return
        self._is_open = True
        if self._background_interval_s is not None:
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._background_loop,
                name="FakeIqSource-reader",
                daemon=True,
            )
            self._thread.start()

    def close(self) -> None:
        """Stop the background thread (if any) and mark the source closed."""
        self._is_open = False
        self._stop_event.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5.0)
            self._thread = None

    @property
    def is_open(self) -> bool:
        """Whether the source is open."""
        return self._is_open

    # -- IqSource configuration ----------------------------------------------

    def set_center_freq(self, hz: int) -> None:
        """Set the center frequency reported by emitted blocks."""
        self._center_freq = int(hz)

    @property
    def center_freq_hz(self) -> int:
        """Current center frequency in Hz."""
        return self._center_freq

    def set_sample_rate(self, hz: int) -> None:
        """Set the sample rate reported by emitted blocks."""
        self._sample_rate = int(hz)

    @property
    def sample_rate_hz(self) -> int:
        """Current sample rate in Hz."""
        return self._sample_rate

    def set_gain(self, db: float | None) -> None:
        """Record a gain request; ``None`` means AGC."""
        self._gain_db = None if db is None else float(db)

    def frequency_range_hz(self) -> tuple[int, int]:
        """Return the configured ``(low, high)`` tunable range in Hz."""
        return self._frequency_range

    # -- IqSource streaming ---------------------------------------------------

    def on_block(self, callback: Callable[[IqBlock], None] | None) -> None:
        """Set (or clear with ``None``) the single block callback."""
        self._callback = callback

    def pump(self, n_blocks: int) -> None:
        """Generate and deliver ``n_blocks`` blocks synchronously.

        Intended for tests; mirrors what the reader thread does, on the
        caller's thread.

        Args:
            n_blocks: Number of blocks to emit.

        Raises:
            RuntimeError: When the source is not open.
        """
        if not self._is_open:
            raise RuntimeError("FakeIqSource.pump() requires an open source")
        for _ in range(n_blocks):
            self._emit_block()

    def set_overflow(self, active: bool) -> None:
        """Mark all subsequently emitted blocks as overflowed.

        Only sets the :attr:`IqBlock.overflow` flag — the fake never
        drops samples itself.

        Args:
            active: ``True`` to flag subsequent blocks as overflowed.
        """
        self._overflow = bool(active)

    # -- internals ------------------------------------------------------------

    def _background_loop(self) -> None:
        interval = self._background_interval_s
        while self._is_open and not self._stop_event.is_set():
            self._emit_block()
            self._stop_event.wait(interval)

    def _emit_block(self) -> None:
        callback = self._callback
        block = IqBlock(
            samples=self._generate_samples(),
            center_freq_hz=self._center_freq,
            sample_rate_hz=self._sample_rate,
            timestamp_s=time.monotonic(),
            overflow=self._overflow,
        )
        if callback is not None:
            callback(block)

    def _generate_samples(self) -> Any:
        """Generate one block_size complex64 sample array."""
        np = self._np
        count = self._block_size
        indices = np.arange(self._sample_index, self._sample_index + count)
        self._sample_index += count

        acc = np.zeros(count, dtype=np.complex128)
        for offset_hz, dbfs in self._tones:
            amplitude = 10.0 ** (dbfs / 20.0)
            acc += amplitude * np.exp(
                2j * np.pi * offset_hz * indices / self._sample_rate
            )

        noise_amplitude = 10.0 ** (self._noise_floor_dbfs / 20.0)
        if noise_amplitude > 0.0:
            # Complex noise: split power evenly between I and Q so the
            # total (I²+Q²) RMS sits at noise_floor_dbfs.
            per_component = noise_amplitude / math.sqrt(2.0)
            acc += self._rng.standard_normal(count) * per_component
            acc += 1j * self._rng.standard_normal(count) * per_component

        return acc.astype(np.complex64)
