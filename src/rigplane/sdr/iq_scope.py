"""IQ FFT scope — turn IQ blocks into ScopeFrame streams (MOR-3154).

Implements the :class:`~rigplane.sdr.protocol.IqScopeSink` contract:
``feed`` only enqueues on the source's reader thread, while windowed FFT,
averaging, span/view cropping and level mapping run on the sink's own
worker thread at ``fps``. The result is the standard
:class:`~rigplane.scope.ScopeFrame` stream the existing frontend
spectrum/waterfall consumes.

Usage::

    scope = IqFftScope(invert_spectrum=config.invert_spectrum)
    scope.on_frame(send_to_websocket)
    source.on_block(scope.feed)  # reader thread; enqueue only

    scope.set_span(config.span_hz)
    scope.set_view_center(rf_hz)
    scope.set_tx_active(True)  # freeze display, mark out_of_range

    ...
    scope.close()
"""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Any, Callable

from rigplane.scope import ScopeFrame
from rigplane.scope.levels import AdaptiveLevelMapper

from .types import IqBlock

__all__ = ["IqFftScope"]

_log = logging.getLogger(__name__)

_SCOPE_MODE_CENTER = 0

# Blocks waiting for the worker: at 2.4 Msps / 4096-sample blocks the
# source produces ~586 blocks/s while a 20 fps worker consumes one per
# tick, so 64 slots hold far more than one fps interval of backlog.
_QUEUE_LIMIT = 64

# FFT bins blanked around DC (±2) to hide the RTL-SDR LO spike before
# the max-hold resample can spread it into neighbouring pixels.
_DC_EXCLUDE_BINS = 2

_WORKER_NAME = "IqFftScope-worker"

_CLOSE_JOIN_TIMEOUT_S = 5.0


def _import_numpy() -> Any:
    """Lazy-import numpy to avoid a hard dependency at module level."""
    from rigplane.core._optional_deps import _require_numpy

    _require_numpy()
    import numpy as np

    return np


def _blackman_harris(size: int) -> Any:
    """Return the 4-term Blackman-Harris window of ``size`` points."""
    np = _import_numpy()
    t = 2.0 * np.pi * np.arange(size) / (size - 1)
    return (
        0.35875
        - 0.48829 * np.cos(t)
        + 0.14128 * np.cos(2.0 * t)
        - 0.01168 * np.cos(3.0 * t)
    )


class IqFftScope:
    """FFT scope turning IQ blocks into :class:`ScopeFrame` at ``fps``.

    Processing per frame: 4-term Blackman-Harris window → complex FFT →
    ``fftshift`` → power dB → exponential averaging (``avg_count``
    frames) → optional spectrum inversion → crop to ``span_hz`` around
    the view center → max-hold resample to ``pixel_width`` bins →
    :class:`~rigplane.scope.levels.AdaptiveLevelMapper` over the full
    displayed window.

    Frequencies in emitted frames are RF frequencies: block
    ``center_freq_hz`` must already carry any ppm/offset correction
    (the controller/source applies it); this class applies none.

    Args:
        fft_size: FFT window size in samples (power of 2 recommended).
        fps: Worker ticks per second; one frame at most per tick.
        avg_count: Exponential-averaging length in frames; the blend
            weight of a new frame is ``1 / avg_count``.
        pixel_width: Output pixel count per frame (ScopeFrame
            convention: 689).
        invert_spectrum: Mirror the frequency axis (equivalent to
            conjugating the samples; wired from
            :class:`~rigplane.sdr.types.SdrConfig.invert_spectrum`).
        start_worker: Start the ``fps`` worker thread lazily on first
            use. ``False`` keeps the sink fully synchronous — ``feed``
            enqueues and :meth:`process_pending` steps — for
            deterministic tests.
    """

    def __init__(
        self,
        fft_size: int = 4096,
        fps: int = 20,
        avg_count: int = 3,
        pixel_width: int = 689,
        *,
        invert_spectrum: bool = False,
        start_worker: bool = True,
    ) -> None:
        self._np = _import_numpy()

        self._fft_size = int(fft_size)
        self._fps = max(1, int(fps))
        self._avg_alpha = 1.0 / max(1, int(avg_count))
        self._pixel_width = int(pixel_width)
        self._invert_spectrum = bool(invert_spectrum)
        self._start_worker = bool(start_worker)

        self._window = _blackman_harris(self._fft_size)

        # dB→pixel mapping over the full displayed window (in-band = all
        # of it; the DC spike is blanked before the mapper sees data).
        self._mapper = AdaptiveLevelMapper()

        # Display state (set through the IqScopeSink protocol).
        self._span_hz: int | None = None
        self._view_center_hz: int | None = None
        self._tx_active = False

        self._callback: Callable[[ScopeFrame], None] | None = None
        self._queue: deque[IqBlock] = deque()
        self._dropped_blocks = 0
        self._avg_db: Any = None
        # (start_freq_hz, end_freq_hz, pixels) of the last live frame,
        # repeated while TX is active.
        self._last_live: tuple[int, int, bytes] | None = None

        # Reentrant: frame callbacks run on the worker thread under the
        # lock and may call the display setters back.
        self._lock = threading.RLock()
        self._closed = False
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

        _log.info(
            "IqFftScope: fft_size=%d fps=%d avg_count=%s pixel_width=%d",
            self._fft_size,
            self._fps,
            avg_count,
            self._pixel_width,
        )

    # -- IqScopeSink: source reader thread ------------------------------------

    def feed(self, block: IqBlock) -> None:
        """Enqueue one IQ block; never processes on the caller's thread.

        The queue is bounded at 64 blocks; the oldest block is dropped
        and counted in :attr:`dropped_blocks` when it overflows.
        Feeding a closed sink is ignored.
        """
        with self._lock:
            if self._closed:
                return
            if len(self._queue) >= _QUEUE_LIMIT:
                self._queue.popleft()
                self._dropped_blocks += 1
            self._queue.append(block)
        self._ensure_worker()

    @property
    def dropped_blocks(self) -> int:
        """Blocks dropped from the bounded feed queue so far."""
        return self._dropped_blocks

    # -- IqScopeSink: controller thread ---------------------------------------

    def set_view_center(self, hz: int) -> None:
        """Set the RF center of the displayed window (display-only).

        No clamping happens: when the requested window does not fit
        inside the current IQ bandwidth, the following frames carry
        ``out_of_range=True`` and empty pixels until the view fits
        again (the controller retunes).
        """
        with self._lock:
            self._view_center_hz = int(hz)

    def set_span(self, hz: int | None) -> None:
        """Set the displayed span in Hz; ``None`` = full sample rate."""
        with self._lock:
            self._span_hz = None if hz is None else int(hz)

    def set_tx_active(self, active: bool) -> None:
        """Freeze (``True``) or resume (``False``) live display.

        While active, frames keep emitting at ``fps`` repeating the last
        pre-TX pixels (empty before the first live frame) with
        ``out_of_range=True``, so the waterfall shows no TX hole.
        Incoming blocks are discarded.
        """
        with self._lock:
            if active != self._tx_active:
                _log.info("IqFftScope: tx_active=%s", active)
            self._tx_active = bool(active)

    def on_frame(self, callback: Callable[[ScopeFrame], None] | None) -> None:
        """Set (or clear with ``None``) the single frame callback."""
        with self._lock:
            self._callback = callback
        if callback is not None:
            self._ensure_worker()

    def close(self) -> None:
        """Stop the worker thread and drop queued state; idempotent."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._queue.clear()
            thread = self._thread
        self._stop_event.set()
        if thread is not None:
            thread.join(timeout=_CLOSE_JOIN_TIMEOUT_S)

    # -- stepping --------------------------------------------------------------

    def process_pending(self) -> None:
        """Drain the queue and emit at most one frame (one worker tick).

        The worker thread calls this at ``fps``; with
        ``start_worker=False`` it is the synchronous stepping entry
        point for deterministic tests.
        """
        with self._lock:
            if self._closed:
                return
            self._drain_and_process()

    # -- internals ---------------------------------------------------------------

    def _ensure_worker(self) -> None:
        """Start the fps worker thread once, lazily."""
        if not self._start_worker:
            return
        with self._lock:
            if self._closed or self._thread is not None:
                return
            self._stop_event.clear()
            thread = threading.Thread(
                target=self._worker_loop, name=_WORKER_NAME, daemon=True
            )
            self._thread = thread
        thread.start()

    def _worker_loop(self) -> None:
        interval = 1.0 / self._fps
        while not self._stop_event.wait(interval):
            with self._lock:
                if self._closed:
                    break
                try:
                    self._drain_and_process()
                except Exception:
                    _log.exception("IqFftScope: frame processing error")

    def _drain_and_process(self) -> None:
        """Process the newest queued block (or the TX freeze). Caller holds the lock."""
        callback = self._callback
        if callback is None:
            self._queue.clear()
            return

        block: IqBlock | None = None
        overflow = False
        while self._queue:
            block = self._queue.popleft()
            overflow = overflow or block.overflow

        if self._tx_active:
            self._emit(callback, self._tx_frame())
            return

        if block is None:
            return

        if overflow:
            # A sample discontinuity invalidates the average; the frame
            # that contains it re-seeds from its own block.
            self._avg_db = None
        frame = self._frame_from_block(block)
        self._last_live = (
            frame.start_freq_hz,
            frame.end_freq_hz,
            frame.pixels,
        )
        self._emit(callback, frame)

    def _tx_frame(self) -> ScopeFrame:
        """Frame repeating the last live pixels, marked out_of_range."""
        if self._last_live is not None:
            start_hz, end_hz, pixels = self._last_live
        else:
            center = self._view_center_hz or 0
            start_hz = end_hz = center
            pixels = b""
        return ScopeFrame(
            receiver=0,
            mode=_SCOPE_MODE_CENTER,
            start_freq_hz=start_hz,
            end_freq_hz=end_hz,
            pixels=pixels,
            out_of_range=True,
        )

    def _frame_from_block(self, block: IqBlock) -> ScopeFrame:
        """Run the FFT chain for one block into a ScopeFrame."""
        np = self._np
        n = self._fft_size

        samples = block.samples
        if samples.size < n:
            padded = np.zeros(n, dtype=np.complex64)
            padded[n - samples.size :] = samples
            samples = padded
        else:
            samples = samples[-n:]

        spectrum = np.fft.fftshift(np.fft.fft(samples * self._window))
        db = 20.0 * np.log10(np.maximum(np.abs(spectrum), 1e-10) / n)

        # Blank ±2 bins around DC (the RTL-SDR LO spike) before the
        # max-hold resample can spread it into neighbouring pixels.
        center_bin = n // 2
        db[center_bin - _DC_EXCLUDE_BINS : center_bin + _DC_EXCLUDE_BINS + 1] = (
            np.median(db)
        )

        if self._avg_db is None:
            self._avg_db = db
        else:
            alpha = self._avg_alpha
            self._avg_db = db * alpha + self._avg_db * (1.0 - alpha)
        avg_db = self._avg_db

        if self._invert_spectrum:
            avg_db = avg_db[::-1]

        rate_hz = block.sample_rate_hz
        center_hz = block.center_freq_hz
        bin_hz = rate_hz / n
        span_hz = self._span_hz if self._span_hz is not None else rate_hz
        view_hz = (
            self._view_center_hz if self._view_center_hz is not None else center_hz
        )

        lo_bin = (view_hz - span_hz / 2.0 - center_hz) / bin_hz + center_bin
        hi_bin = (view_hz + span_hz / 2.0 - center_hz) / bin_hz + center_bin
        if lo_bin < 0.0 or hi_bin > n:
            return ScopeFrame(
                receiver=0,
                mode=_SCOPE_MODE_CENTER,
                start_freq_hz=int(view_hz - span_hz // 2),
                end_freq_hz=int(view_hz + span_hz // 2),
                pixels=b"",
                out_of_range=True,
            )
        lo = int(round(lo_bin))
        hi = max(int(round(hi_bin)), lo + 1)

        crop = avg_db[lo:hi]
        # Max-hold resample: each output pixel takes the maximum of the
        # bins it covers, so a narrow CW signal survives decimation.
        # With fewer bins than pixels the same indexing repeats bins.
        starts = (np.arange(self._pixel_width) * (hi - lo)) // self._pixel_width
        reduced = np.maximum.reduceat(crop, starts)

        pixels = self._mapper.map(reduced)
        return ScopeFrame(
            receiver=0,
            mode=_SCOPE_MODE_CENTER,
            start_freq_hz=int(round(center_hz + (lo - center_bin) * bin_hz)),
            end_freq_hz=int(round(center_hz + (hi - center_bin) * bin_hz)),
            pixels=pixels,
            out_of_range=False,
        )

    def _emit(self, callback: Callable[[ScopeFrame], None], frame: ScopeFrame) -> None:
        try:
            callback(frame)
        except Exception:
            _log.exception("IqFftScope: frame callback error")
