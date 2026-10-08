"""SoapySDR-backed :class:`IqSource` (MOR-3155).

The only module that imports ``SoapySDR``, lazily through
:mod:`rigplane.core._optional_deps`.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any, Callable

from rigplane.core._optional_deps import _require_numpy, _require_soapysdr

from .types import IqBlock, SdrConfig

__all__ = ["SoapyIqSource"]

logger = logging.getLogger(__name__)

_SLEEP_CHUNK_S = 0.25
_CLOSE_JOIN_TIMEOUT_S = 5.0
# frequency_range_hz() result before the first successful connect:
# permissive, so pre-connect callers never see an out-of-range answer.
_PERMISSIVE_RANGE_HZ = (0, 6_000_000_000)


class _SoapySession:
    """One live device + RX stream, used only on the reader thread."""

    def __init__(
        self,
        device: Any,
        *,
        rx: int,
        ppm: float,
        hw_ppm: bool,
    ) -> None:
        self._device = device
        self._rx = rx
        self._ppm = ppm
        self._hw_ppm = hw_ppm
        self._stream: Any = None
        self._applied_generation: int | None = None

    def apply_config(
        self,
        generation: int,
        center_freq_hz: int | None,
        sample_rate_hz: int,
        gain_db: float | None,
    ) -> None:
        """Push rate/gain/frequency once per ``generation``.

        ``center_freq_hz is None`` (no centre requested yet) skips
        ``setFrequency`` — 0 Hz is out of RTL-SDR range — while rate
        and gain still apply.
        """
        if generation == self._applied_generation:
            return
        self._device.setSampleRate(self._rx, 0, sample_rate_hz)
        if gain_db is None:
            self._device.setGainMode(self._rx, 0, True)
        else:
            self._device.setGainMode(self._rx, 0, False)
            self._device.setGain(self._rx, 0, gain_db)
        if center_freq_hz is not None:
            if not self._hw_ppm and self._ppm != 0.0:
                center_freq_hz = int(center_freq_hz / (1.0 + self._ppm * 1e-6))
            self._device.setFrequency(self._rx, 0, center_freq_hz)
        self._applied_generation = generation

    def open_stream(self, cf32: int) -> None:
        """Set up and activate the CF32 RX stream."""
        self._stream = self._device.setupStream(self._rx, cf32, [0], {})
        self._device.activateStream(self._stream)

    def read(self, buffer: Any, num_elems: int, timeout_us: int) -> Any:
        """One ``readStream`` call; returns the stream-result object."""
        return self._device.readStream(
            self._stream, [buffer], num_elems, timeoutUs=timeout_us
        )

    def close(self) -> None:
        """Deactivate and close the stream; best-effort, idempotent."""
        stream = self._stream
        self._stream = None
        if stream is None:
            return
        try:
            self._device.deactivateStream(stream)
        except Exception:  # noqa: BLE001 - teardown must still closeStream
            logger.debug("deactivateStream failed", exc_info=True)
        try:
            self._device.closeStream(stream)
        except Exception:  # noqa: BLE001 - best-effort teardown
            logger.debug("closeStream failed", exc_info=True)


class SoapyIqSource:
    """SoapySDR device as an :class:`~rigplane.sdr.protocol.IqSource`.

    All device I/O — the initial open included — happens on a daemon
    reader thread; ``open()`` never blocks the caller's event loop. On
    device loss the session is torn down, one warning is logged per
    incident, and reopening is retried with exponential backoff.
    ``is_open`` tracks the requested lifecycle (``open()`` …
    ``close()``) and stays ``True`` across transparent reconnects.

    Args:
        config: Device/stream parameters. ``device_args``,
            ``extra_settings``, ``sample_rate_hz``, ``gain_db``, and
            ``ppm`` are applied on every (re)connect.
        block_size: Reusable read-buffer size in samples; each delivered
            block carries the number of samples the device returned.
        read_timeout_us: Per-read ``readStream`` timeout.
        min_retry_s: Initial reconnect delay; doubled per failed attempt.
        max_retry_s: Reconnect-delay cap.
        clock: Timestamp source for emitted blocks.
        sleep: Sleep primitive used for backoff waits.

    Raises:
        ImportError: The SoapySDR bindings are not installed.
        ValueError: ``block_size`` or ``min_retry_s`` is not positive,
            or ``max_retry_s`` is below ``min_retry_s``.
    """

    def __init__(
        self,
        config: SdrConfig,
        *,
        block_size: int = 65_536,
        read_timeout_us: int = 100_000,
        min_retry_s: float = 1.0,
        max_retry_s: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        _require_soapysdr()
        import SoapySDR  # type: ignore[import-not-found]

        _require_numpy()
        import numpy as np

        if block_size <= 0:
            raise ValueError(f"block_size must be positive, got {block_size}")
        if min_retry_s <= 0:
            raise ValueError(f"min_retry_s must be positive, got {min_retry_s}")
        if max_retry_s < min_retry_s:
            raise ValueError(
                f"max_retry_s ({max_retry_s}) must be >= min_retry_s ({min_retry_s})"
            )

        self._config = config
        self._sdr = SoapySDR
        self._block_size = int(block_size)
        self._read_timeout_us = int(read_timeout_us)
        self._min_retry_s = float(min_retry_s)
        self._max_retry_s = float(max_retry_s)
        self._clock = clock
        self._sleep = sleep

        self._buffer: Any = np.empty(self._block_size, dtype=np.complex64)

        self._lock = threading.Lock()
        self._center_freq = 0
        self._center_requested = False
        self._sample_rate: int = int(config.sample_rate_hz)
        self._gain_db = config.gain_db
        self._config_generation = 1
        self._frequency_range: tuple[int, int] | None = None
        self._callback: Callable[[IqBlock], None] | None = None

        self._is_open = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._callback_warned = False

    # -- IqSource lifecycle ---------------------------------------------------

    def open(self) -> None:
        """Start the reader thread; idempotent — the device connects there."""
        if self._is_open:
            return
        self._is_open = True
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._reader_loop,
            name="SoapyIqSource-reader",
            daemon=True,
        )
        self._thread.start()

    def close(self) -> None:
        """Stop the reader thread and tear down any live stream; idempotent."""
        self._is_open = False
        self._stop.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=_CLOSE_JOIN_TIMEOUT_S)
            if thread.is_alive():
                logger.error(
                    "SoapyIqSource reader thread did not stop within %.1f s",
                    _CLOSE_JOIN_TIMEOUT_S,
                )
        self._thread = None

    @property
    def is_open(self) -> bool:
        """Whether the source is in the open lifecycle state."""
        return self._is_open

    # -- IqSource configuration ----------------------------------------------

    def set_center_freq(self, hz: int) -> None:
        """Request a new center frequency; applied on the reader thread."""
        with self._lock:
            self._center_freq = int(hz)
            self._center_requested = True
            self._config_generation += 1

    @property
    def center_freq_hz(self) -> int:
        """Requested (PPM-corrected) center frequency in Hz."""
        with self._lock:
            return self._center_freq

    def set_sample_rate(self, hz: int) -> None:
        """Request a new sample rate; applied on the reader thread."""
        with self._lock:
            self._sample_rate = int(hz)
            self._config_generation += 1

    @property
    def sample_rate_hz(self) -> int:
        """Current sample rate in Hz."""
        with self._lock:
            return self._sample_rate

    def set_gain(self, db: float | None) -> None:
        """Request a gain change; ``None`` enables device AGC."""
        with self._lock:
            self._gain_db = None if db is None else float(db)
            self._config_generation += 1

    def frequency_range_hz(self) -> tuple[int, int]:
        """Tunable ``(low, high)`` range in Hz.

        Returns the permissive default ``(0, 6_000_000_000)`` until the
        first successful connect; never raises, so callers may use it
        before the reader thread has connected. Afterwards, the range
        reported by the device (cached from its last connect).
        """
        with self._lock:
            if self._frequency_range is None:
                return _PERMISSIVE_RANGE_HZ
            return self._frequency_range

    # -- IqSource streaming ---------------------------------------------------

    def on_block(self, callback: Callable[[IqBlock], None] | None) -> None:
        """Set (or clear with ``None``) the single block callback."""
        with self._lock:
            self._callback = callback

    # -- internals ------------------------------------------------------------

    def _reader_loop(self) -> None:
        """Connect, stream, and reconnect with backoff until stopped."""
        backoff_s = self._min_retry_s
        warned = False
        while not self._stop.is_set():
            session = None
            saw_live_read = False
            try:
                session = self._open_session()
                saw_live_read = self._stream_loop(session)
            except Exception as exc:  # noqa: BLE001 - any device error reconnects
                if not self._stop.is_set():
                    if warned:
                        logger.debug("SoapySDR still failing (%s); retrying", exc)
                    else:
                        warned = True
                        logger.warning(
                            "SoapySDR device error (%s); reconnecting with backoff",
                            exc,
                        )
            finally:
                if session is not None:
                    session.close()
            if saw_live_read:
                if warned:
                    logger.info("SoapySDR stream recovered")
                    warned = False
                backoff_s = self._min_retry_s
            if self._stop.is_set():
                break
            self._sleep_interruptible(backoff_s)
            backoff_s = min(backoff_s * 2.0, self._max_retry_s)

    def _open_session(self) -> _SoapySession:
        """Open and fully configure the device; runs on the reader thread."""
        sdr = self._sdr
        device = sdr.Device(sdr.KwargsFromString(self._config.device_args))
        for key, value in self._config.extra_settings.items():
            device.writeSetting(key, value)

        hw_ppm = True
        if self._config.ppm != 0.0:
            try:
                device.setFrequencyCorrection(sdr.SOAPY_SDR_RX, 0, self._config.ppm)
            except Exception:  # noqa: BLE001 - capability probe
                hw_ppm = False
        session = _SoapySession(
            device,
            rx=sdr.SOAPY_SDR_RX,
            ppm=self._config.ppm,
            hw_ppm=hw_ppm,
        )
        session.apply_config(*self._config_snapshot())
        session.open_stream(sdr.SOAPY_SDR_CF32)

        ranges = device.getFrequencyRange(sdr.SOAPY_SDR_RX, 0)
        with self._lock:
            self._frequency_range = (
                int(min(r.minimum() for r in ranges)),
                int(max(r.maximum() for r in ranges)),
            )
        return session

    def _config_snapshot(self) -> tuple[int, int | None, int, float | None]:
        """Generation, centre (``None`` until first request), rate, gain."""
        with self._lock:
            center = self._center_freq if self._center_requested else None
            return (
                self._config_generation,
                center,
                self._sample_rate,
                self._gain_db,
            )

    def _stream_loop(self, session: _SoapySession) -> bool:
        """Read blocks until stopped or a device error; returns liveness."""
        sdr = self._sdr
        saw_live_read = False
        pending_overflow = False
        while not self._stop.is_set():
            generation, center, rate, gain = self._config_snapshot()
            session.apply_config(generation, center, rate, gain)

            result = session.read(self._buffer, self._block_size, self._read_timeout_us)
            ret = result.ret
            if ret == sdr.SOAPY_SDR_TIMEOUT or ret == 0:
                saw_live_read = True
                continue
            if ret == sdr.SOAPY_SDR_OVERFLOW:
                saw_live_read = True
                pending_overflow = True
                continue
            if ret < 0:
                raise RuntimeError(f"readStream returned {ret}")
            saw_live_read = True

            block = IqBlock(
                samples=self._buffer[:ret].copy(),
                center_freq_hz=center or 0,
                sample_rate_hz=rate,
                timestamp_s=self._clock(),
                overflow=pending_overflow,
            )
            pending_overflow = False
            self._deliver(block)
        return saw_live_read

    def _deliver(self, block: IqBlock) -> None:
        """Invoke the block callback, surviving a raising consumer."""
        with self._lock:
            callback = self._callback
        if callback is None:
            return
        try:
            callback(block)
        except Exception:  # noqa: BLE001 - the reader thread must survive
            if not self._callback_warned:
                self._callback_warned = True
                logger.exception("IqBlock on_block callback raised; continuing")
        else:
            self._callback_warned = False

    def _sleep_interruptible(self, seconds: float) -> None:
        """Sleep in chunks so ``close()`` wakes the reader quickly."""
        remaining = seconds
        while remaining > 0.0 and not self._stop.is_set():
            chunk = min(remaining, _SLEEP_CHUNK_S)
            self._sleep(chunk)
            remaining -= chunk
