"""IQ FFT scope — turn IQ blocks into ScopeFrame streams (MOR-3154, MOR-3200).

Implements the :class:`~rigplane.sdr.protocol.IqScopeSink` contract:
``feed`` only enqueues on the source's reader thread, while windowed FFT,
averaging, span/view cropping and level mapping run on the sink's own
worker thread at ``fps``. The result is the standard
:class:`~rigplane.scope.ScopeFrame` stream the existing frontend
spectrum/waterfall consumes.

For spans well below the sample rate the crop path would show only a
handful of real FFT bins per pixel (at 2.4 MS/s / fft 4096 one bin is
~586 Hz), so the scope switches to a zoom FFT (MOR-3200): the block
stream is mixed down, low-pass filtered and decimated by an integer
factor before the same window → FFT → dB → averaging → resample chain
runs on the decimated samples (see :class:`_ZoomChain`).

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
import math
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


# Zoom FFT (MOR-3200): engage only for spans below 1/8 of the sample
# rate. Wider spans keep the crop path — the small-D zoom chains those
# spans select (D = 2..4) measured 1.5-1.8 ms/tick at 2.4 Msps (see the
# MOR-3200 PR), over the Pi-4 CPU budget at the pessimistic 10x
# extrapolation, while the crop path still shows ~0.75 real bins per
# pixel at rate/8 (fft 4096, 689 pixels).
_ZOOM_GATE_FRAC = 0.125

# Target decimated rate as a multiple of the span ("~1.25x" per MOR-3200;
# 1.5 keeps the anti-alias transition bands wide enough that the cascade
# stays a small fraction of a Pi 4 core).
_ZOOM_OVERSAMPLE = 1.5

# Decimated-sample headroom processed ahead of each zoom window. The
# first outputs after a skip (or reset) carry the filters' startup
# transient — at most 34 outputs anywhere in the decimation table
# (established by sweeping every tabulated D across its full span
# range for the MOR-3200 PR) — so 256 outputs keep the window
# transient-free with >7x margin.
_ZOOM_SKIP_MARGIN_OUT = 256

# Decimation factors: 2 plus 5-smooth composites, so every factor above 2
# splits into two integer stages.
_ZOOM_MAX_DECIMATION = 512


def _is_five_smooth(value: int) -> bool:
    for prime in (2, 3, 5):
        while value % prime == 0:
            value //= prime
    return value == 1


_ZOOM_DECIMATIONS = tuple(
    [2]
    + [d for d in range(4, _ZOOM_MAX_DECIMATION + 1) if d != 5 and _is_five_smooth(d)]
)

# Kaiser design: stopband A = beta / 0.1102 + 8.7 dB, tap estimate
# N ≈ (A - 7.95) / (2.285 * transition_rad); combined into one constant.
_KAISER_BETA = 5.0
_KAISER_TAPS_PER_RAD = 20.2
_MIN_TAPS = 7


def _kaiser_tap_count(fs: float, fp: float, sb: float) -> int:
    """Kaiser tap estimate for a low-pass with passband ``fp``, stopband ``sb``."""
    delta_omega = 2.0 * math.pi * (sb - fp) / fs
    n = int(math.ceil(_KAISER_TAPS_PER_RAD / delta_omega)) + 1
    if n % 2 == 0:
        n += 1
    return max(n, _MIN_TAPS)


def _kaiser_lowpass(np: Any, fs: float, fp: float, sb: float) -> Any:
    """Windowed-sinc low-pass (Kaiser window), odd length, unity DC gain."""
    n = _kaiser_tap_count(fs, fp, sb)
    k = np.arange(n) - (n - 1) / 2.0
    fc = 0.5 * (fp + sb) / fs
    taps = (2.0 * fc) * np.sinc(2.0 * fc * k) * np.kaiser(n, _KAISER_BETA)
    return taps / taps.sum()


def _translate(np: Any, taps: Any, rad_per_tap: float) -> Any:
    """Multiply ``taps`` by the NCO phasor e^{+j*rad_per_tap*k} at tap k."""
    if rad_per_tap == 0.0:
        return taps.astype(np.complex64)
    phasor = np.exp(1j * rad_per_tap * np.arange(taps.size))
    return (taps * phasor).astype(np.complex64)


def _zoom_decimation(rate_hz: int, span_hz: int) -> int:
    """Largest tabulated decimation keeping the rate >= span * oversample."""
    d_max = rate_hz / (_ZOOM_OVERSAMPLE * span_hz)
    for d in reversed(_ZOOM_DECIMATIONS):
        if d <= d_max:
            return d
    return 2


def _split_stages(decimation: int, rate_hz: int, span_hz: int) -> tuple[int, int]:
    """Split ``decimation`` into two stage factors minimising modelled cost.

    Cost is output-count * taps per input sample. The single-stage
    option ``(1, D)`` is priced too — near the zoom gate (small ``D``)
    one wide-tap stage beats two cascaded ones. When both stages run
    they both pass the view span (stage 1 only has to protect what
    stage 2 keeps: anything above ends up in stage 2's
    transition/stopband or cropped outside the view). Stage 1 stops at
    its own output Nyquist; stage 2 stops at the final decimated
    Nyquist.
    """
    if decimation == 2:
        return 1, 2
    rate_d = rate_hz / decimation
    n_single = _kaiser_tap_count(rate_hz, 0.55 * span_hz, 0.49 * rate_d)
    best = (1, decimation)
    best_cost = n_single / decimation
    for d1 in range(2, decimation // 2 + 1):
        if decimation % d1:
            continue
        n1 = _kaiser_tap_count(rate_hz, 0.55 * span_hz, 0.49 * rate_hz / d1)
        n2 = _kaiser_tap_count(rate_hz / d1, 0.55 * span_hz, 0.49 * rate_d)
        cost = n1 / d1 + n2 / decimation
        if cost < best_cost:
            best, best_cost = (d1, decimation // d1), cost
    return best


class _DecimStage:
    """One polyphase FIR decimation stage with carried filter state.

    Computes ``y[m] = sum_k taps[k] * x[m * factor - k]`` (with ``x[j] = 0``
    for ``j < 0`` at stream start) for every output index whose window
    ends inside the samples seen so far. Only the kept outputs are
    evaluated: one row-dot per output against a sliding-window view, so
    the cost is ``outputs * taps`` rather than ``inputs * taps``.
    """

    def __init__(self, np: Any, taps: Any, factor: int) -> None:
        self._np = np
        self._rev = np.ascontiguousarray(taps[::-1]).astype(np.complex64)
        self._factor = factor
        self._tail = np.zeros(max(self._rev.size - 1, 0), dtype=np.complex64)
        self._in_count = 0
        self._next_out = 0

    def reset(self) -> None:
        """Restart the stream epoch: zero history, output index 0."""
        self._tail = self._np.zeros(
            max(self._rev.size - 1, 0), dtype=self._np.complex64
        )
        self._in_count = 0
        self._next_out = 0

    def skip(self, count: int) -> None:
        """Advance the epoch past ``count`` samples without filtering them.

        Filter history is dropped (the next outputs carry a startup
        transient), but sample and output indices stay true to elapsed
        time, so the decimation phase and the caller's per-output NCO
        rotation stay continuous.
        """
        if count <= 0:
            return
        self._in_count += count
        self._next_out = (self._in_count + self._factor - 1) // self._factor
        self._tail = self._np.zeros(
            max(self._rev.size - 1, 0), dtype=self._np.complex64
        )

    def process(self, samples: Any) -> tuple[Any, int]:
        """Filter and decimate one contiguous block.

        Returns ``(outputs, first_output_index)`` where ``first_output_index``
        is the absolute (per-epoch) index of ``outputs[0]``.
        """
        np = self._np
        length = self._rev.size
        factor = self._factor
        start_in = self._in_count
        count = samples.size
        ext = np.concatenate((self._tail, samples))
        if length > 1:
            self._tail = ext[-(length - 1) :]
        self._in_count = start_in + count

        first = self._next_out
        last = (start_in + count - 1) // factor
        if last < first:
            return np.empty(0, dtype=np.complex64), first
        windows = np.lib.stride_tricks.sliding_window_view(ext, length)
        outs = (
            windows[first * factor - start_in : last * factor - start_in + 1 : factor]
            @ self._rev
        )
        self._next_out = last + 1
        return outs, first


class _ZoomChain:
    """Frequency-translated polyphase decimator feeding the zoom FFT.

    Mathematically equivalent to mixing the block stream down by
    ``view_hz - center_hz`` with a phase-continuous complex NCO and then
    low-pass filtering + decimating by an integer ``D`` (one or two
    stages, whichever the modelled cost picks). The mix is folded into
    the FIR taps — each stage's taps are
    pre-multiplied by the NCO phasor at the tap's sample offset — and the
    inverse phasor is applied at the decimated output rate, so no
    full-rate NCO multiplication is ever needed. Absolute (per-epoch)
    sample indices make the NCO phase continuous across blocks by
    construction; any residual phase offset is a constant rotation that
    leaves FFT bin magnitudes unchanged.
    """

    def __init__(
        self,
        np: Any,
        center_hz: int,
        rate_hz: int,
        span_hz: int,
        view_hz: int,
    ) -> None:
        self._np = np
        self.center_hz = center_hz
        self.rate_hz = rate_hz
        self.span_hz = span_hz
        self.view_hz = view_hz
        self.decimation = _zoom_decimation(rate_hz, span_hz)
        d1, d2 = _split_stages(self.decimation, rate_hz, span_hz)
        self.decimated_rate_hz = rate_hz / self.decimation
        offset_hz = float(view_hz - center_hz)

        # Stage 1 (skipped for D == 2): passband = the view span (all
        # stage 2 keeps), stopband = this stage's own output Nyquist.
        if d1 > 1:
            taps = _kaiser_lowpass(np, rate_hz, 0.55 * span_hz, 0.49 * rate_hz / d1)
            self._stage1: _DecimStage | None = _DecimStage(
                np, _translate(np, taps, 2.0 * math.pi * offset_hz / rate_hz), d1
            )
        else:
            self._stage1 = None

        # Final stage: passband covers the view span, stopband at the
        # decimated Nyquist.
        taps2 = _kaiser_lowpass(
            np,
            rate_hz / d1,
            0.55 * span_hz,
            0.49 * self.decimated_rate_hz,
        )
        self._stage2 = _DecimStage(
            np, _translate(np, taps2, 2.0 * math.pi * offset_hz * d1 / rate_hz), d2
        )
        self._d1 = d1
        self._rot_step = -2.0 * math.pi * offset_hz * self.decimation / rate_hz

    def process(self, samples: Any) -> Any:
        """Decimate one contiguous block; return de-rotated outputs."""
        np = self._np
        if self._stage1 is not None:
            samples, _ = self._stage1.process(samples)
            if samples.size == 0:
                return samples
        outs, first = self._stage2.process(samples)
        if outs.size == 0:
            return outs
        rot = np.exp(1j * (self._rot_step * np.arange(first, first + outs.size)))
        return (outs * rot).astype(np.complex64)

    def skip(self, count: int) -> None:
        """Advance past ``count`` input samples without filtering them.

        ``count`` must be a multiple of the decimation factor so both
        stages' sample and output indices stay integer. Outputs are
        indexed by elapsed decimated time, so the NCO de-rotation phase
        stays bin-accurate across the gap; the first outputs after it
        carry the filters' startup transient, which the caller's
        processing margin must absorb.
        """
        if count <= 0:
            return
        if self._stage1 is not None:
            self._stage1.skip(count)
            self._stage2.skip(count // self._d1)
        else:
            self._stage2.skip(count)

    def reset(self) -> None:
        """Restart both stages' stream epochs (NCO phase, filter state)."""
        if self._stage1 is not None:
            self._stage1.reset()
        self._stage2.reset()


class IqFftScope:
    """FFT scope turning IQ blocks into :class:`ScopeFrame` at ``fps``.

    Processing per frame: 4-term Blackman-Harris window → complex FFT →
    ``fftshift`` → power dB → exponential averaging (``avg_count``
    frames) → optional spectrum inversion → crop to ``span_hz`` around
    the view center → max-hold resample to ``pixel_width`` bins →
    :class:`~rigplane.scope.levels.AdaptiveLevelMapper` over the full
    displayed window.

    When ``span_hz`` is below an eighth of the sample rate the same
    chain runs on a zoomed signal instead: the blocks are mixed down by
    ``view_center - block_center`` (phase-continuous across blocks),
    low-pass filtered and decimated by an integer factor so the
    decimated rate is a small multiple of the span, and ``fft_size``
    decimated samples are accumulated per FFT window (:class:`_ZoomChain`).
    A change of block centre, sample rate, span or view centre resets
    the zoom state; the crop path is used unchanged for wider spans.

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

        # Zoom-path state (MOR-3200): the active chain keyed by
        # (center, rate, span, view), its decimated-sample accumulation
        # buffer, and continuity bookkeeping.
        self._zoom: _ZoomChain | None = None
        self._zoom_key: tuple[int, int, int, int] | None = None
        self._zoom_buf: list[Any] = []
        self._zoom_len = 0
        self._zoom_last_drops = 0
        self._zoom_resume_reset = False

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
            self._zoom = None
            self._zoom_buf = []
            self._zoom_len = 0
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
        """Process queued blocks and emit at most one frame (one worker tick).

        The zoom path dequeues every queued block per tick and filters
        the newest samples the next window needs (older ones are
        skipped phase-continuously — see :meth:`_ZoomChain.skip`); the
        crop path uses only the newest block, as before. Caller holds
        the lock.
        """
        callback = self._callback
        if callback is None:
            self._queue.clear()
            return

        blocks: list[IqBlock] = []
        overflow = False
        while self._queue:
            block = self._queue.popleft()
            blocks.append(block)
            overflow = overflow or block.overflow

        if self._tx_active:
            # Blocks are discarded; the zoom stream resumes from a
            # clean continuity state after TX.
            self._zoom_resume_reset = True
            self._emit(callback, self._tx_frame())
            return

        if not blocks:
            return

        newest = blocks[-1]
        chain = self._zoom_chain_for(newest)
        if chain is None:
            if overflow:
                # A sample discontinuity invalidates the average; the
                # frame that contains it re-seeds from its own block.
                self._avg_db = None
            frame = self._frame_from_block(newest)
        else:
            frame = self._zoom_tick(blocks, chain)
            if frame is None:
                return
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
            return self._out_of_range_frame(
                int(view_hz - span_hz // 2), int(view_hz + span_hz // 2)
            )
        lo = int(round(lo_bin))
        hi = max(int(round(hi_bin)), lo + 1)

        pixels = self._pixels_from_bins(avg_db[lo:hi])
        return ScopeFrame(
            receiver=0,
            mode=_SCOPE_MODE_CENTER,
            start_freq_hz=int(round(center_hz + (lo - center_bin) * bin_hz)),
            end_freq_hz=int(round(center_hz + (hi - center_bin) * bin_hz)),
            pixels=pixels,
            out_of_range=False,
        )

    @staticmethod
    def _out_of_range_frame(start_hz: int, end_hz: int) -> ScopeFrame:
        """Frame carrying the requested window, marked out of range."""
        return ScopeFrame(
            receiver=0,
            mode=_SCOPE_MODE_CENTER,
            start_freq_hz=start_hz,
            end_freq_hz=end_hz,
            pixels=b"",
            out_of_range=True,
        )

    def _pixels_from_bins(self, crop: Any) -> bytes:
        """Max-hold resample bins to ``pixel_width`` and adaptive-map."""
        np = self._np
        # Each output pixel takes the maximum of the bins it covers, so
        # a narrow CW signal survives decimation. With fewer bins than
        # pixels the same indexing repeats bins.
        starts = (np.arange(self._pixel_width) * len(crop)) // self._pixel_width
        reduced = np.maximum.reduceat(crop, starts)
        pixels: bytes = self._mapper.map(reduced)
        return pixels

    # -- zoom path (MOR-3200) -------------------------------------------

    def _zoom_chain_for(self, block: IqBlock) -> _ZoomChain | None:
        """Return the zoom chain for this block's geometry, or ``None`` for crop.

        Falls back to the crop path when ``span >= sample_rate *
        _ZOOM_GATE_FRAC`` (or the span is unset). Any change of block
        centre, sample rate, span or view centre rebuilds the chain and
        resets the zoom state (NCO
        phase reference, filter state, accumulation buffer) plus the
        averaging seed. Caller holds the lock.
        """
        span = self._span_hz
        rate = block.sample_rate_hz
        if span is None or rate <= 0 or span >= rate * _ZOOM_GATE_FRAC:
            if self._zoom is not None:
                self._zoom = None
                self._zoom_key = None
                self._zoom_buf = []
                self._zoom_len = 0
            return None
        view = (
            self._view_center_hz
            if self._view_center_hz is not None
            else block.center_freq_hz
        )
        key = (block.center_freq_hz, rate, span, view)
        if self._zoom is None or key != self._zoom_key:
            self._zoom = _ZoomChain(self._np, block.center_freq_hz, rate, span, view)
            self._zoom_key = key
            self._avg_db = None
            self._zoom_buf = []
            self._zoom_len = 0
            self._zoom_last_drops = self._dropped_blocks
            self._zoom_resume_reset = False
        return self._zoom

    def _zoom_reset_stream(self) -> None:
        """Drop zoom continuity: chain epochs and the accumulation buffer."""
        if self._zoom is not None:
            self._zoom.reset()
        self._zoom_buf = []
        self._zoom_len = 0
        self._zoom_resume_reset = True

    def _zoom_tick(self, blocks: list[IqBlock], chain: _ZoomChain) -> ScopeFrame | None:
        """Consume every queued block through the zoom chain.

        Returns the frame for the freshest full ``fft_size`` window, or
        ``None`` when not enough decimated samples have accumulated yet.
        """
        if abs(chain.view_hz - chain.center_hz) * 2 + chain.span_hz > chain.rate_hz:
            # The view window does not fit inside the IQ bandwidth; drop
            # the stale stream so resumed blocks start contiguous.
            self._zoom_reset_stream()
            return self._out_of_range_frame(
                int(chain.view_hz - chain.span_hz // 2),
                int(chain.view_hz + chain.span_hz // 2),
            )
        if self._zoom_resume_reset or self._dropped_blocks != self._zoom_last_drops:
            # Queue drops (or a TX resume) broke sample continuity; the
            # filter tails and buffer no longer join the next blocks.
            self._zoom_reset_stream()
            self._avg_db = None
        self._zoom_last_drops = self._dropped_blocks
        self._zoom_resume_reset = False

        n = self._fft_size
        if any(block.overflow for block in blocks):
            # A sample discontinuity invalidates the stream epoch; the
            # whole tick restarts from its first block.
            self._zoom_reset_stream()
            self._avg_db = None
        samples = self._np.concatenate([block.samples for block in blocks])
        # One chain call per tick on the newest samples the next window
        # needs (plus transient margin); older queued samples are stale
        # and are skipped with phase-continuous bookkeeping, which keeps
        # the small-D chains near the zoom gate inside the CPU budget.
        need = (n + _ZOOM_SKIP_MARGIN_OUT) * chain.decimation
        skip = samples.size - need
        if skip > 0:
            skip = (skip // chain.decimation) * chain.decimation
            chain.skip(skip)
            samples = samples[skip:]
        chunk = chain.process(samples)
        if chunk.size:
            self._zoom_buf.append(chunk)
            self._zoom_len += chunk.size
        if self._zoom_len < n:
            return None

        buffer = self._np.concatenate(self._zoom_buf)
        frame = self._frame_from_zoom(buffer[-n:], chain)
        # Keep the freshest n-1 decimated samples so the next tick's
        # window overlaps instead of waiting for a full buffer.
        self._zoom_buf = [buffer[-(n - 1) :]]
        self._zoom_len = self._zoom_buf[0].size
        return frame

    def _frame_from_zoom(self, samples: Any, chain: _ZoomChain) -> ScopeFrame:
        """Run the FFT chain for one zoomed window into a ScopeFrame."""
        np = self._np
        n = self._fft_size

        spectrum = np.fft.fftshift(np.fft.fft(samples * self._window))
        db = 20.0 * np.log10(np.maximum(np.abs(spectrum), 1e-10) / n)

        # The LO spike now sits at the block centre's offset from the
        # view centre; blank it there before the max-hold resample can
        # spread it into neighbouring pixels.
        center_bin = n // 2
        bin_hz = chain.decimated_rate_hz / n
        spike_bin = center_bin + int(round((chain.center_hz - chain.view_hz) / bin_hz))
        lo = max(spike_bin - _DC_EXCLUDE_BINS, 0)
        hi = min(spike_bin + _DC_EXCLUDE_BINS, n - 1)
        if hi >= lo:
            db[lo : hi + 1] = np.median(db)

        if self._avg_db is None:
            self._avg_db = db
        else:
            alpha = self._avg_alpha
            self._avg_db = db * alpha + self._avg_db * (1.0 - alpha)
        avg_db = self._avg_db

        if self._invert_spectrum:
            avg_db = avg_db[::-1]

        half_bins = (chain.span_hz / 2.0) / bin_hz
        lo_bin = int(round(center_bin - half_bins))
        hi_bin = max(int(round(center_bin + half_bins)), lo_bin + 1)
        pixels = self._pixels_from_bins(avg_db[lo_bin:hi_bin])
        return ScopeFrame(
            receiver=0,
            mode=_SCOPE_MODE_CENTER,
            start_freq_hz=int(round(chain.view_hz - chain.span_hz / 2.0)),
            end_freq_hz=int(round(chain.view_hz + chain.span_hz / 2.0)),
            pixels=pixels,
            out_of_range=False,
        )

    def _emit(self, callback: Callable[[ScopeFrame], None], frame: ScopeFrame) -> None:
        try:
            callback(frame)
        except Exception:
            _log.exception("IqFftScope: frame callback error")
