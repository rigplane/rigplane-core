"""Tests for rigplane.sdr.iq_scope — IqFftScope (MOR-3154)."""

from __future__ import annotations

import sys
import threading
import time

import numpy as np
import pytest

from rigplane.scope import ScopeFrame
from rigplane.sdr import FakeIqSource, IqBlock, IqScopeSink
from rigplane.sdr.iq_scope import IqFftScope

_WORKER_NAME = "IqFftScope-worker"


def _make_source(**kwargs: object) -> FakeIqSource:
    kwargs.setdefault("center_freq_hz", 10_000_000)
    kwargs.setdefault("sample_rate_hz", 2_400_000)
    kwargs.setdefault("block_size", 4096)
    kwargs.setdefault("seed", 21)
    return FakeIqSource(**kwargs)  # type: ignore[arg-type]


def _make_scope(**kwargs: object) -> IqFftScope:
    kwargs.setdefault("fft_size", 4096)
    kwargs.setdefault("start_worker", False)
    return IqFftScope(**kwargs)  # type: ignore[arg-type]


def _drive(
    scope: IqFftScope,
    source: FakeIqSource,
    frames: list[ScopeFrame],
    n_frames: int,
) -> None:
    """Synchronously pump one block and step the sink ``n_frames`` times."""
    source.on_block(scope.feed)
    source.open()
    try:
        for _ in range(n_frames):
            source.pump(1)
            scope.process_pending()
    finally:
        source.close()


def _peak_pixel(frame: ScopeFrame, expected: int, window: int = 4) -> int:
    pixels = np.frombuffer(frame.pixels, dtype=np.uint8)
    lo = max(0, expected - window)
    hi = min(len(pixels), expected + window + 1)
    assert hi > lo, f"empty search window around pixel {expected}"
    return lo + int(np.argmax(pixels[lo:hi]))


def test_satisfies_iq_scope_sink_protocol() -> None:
    assert isinstance(_make_scope(), IqScopeSink)


def test_module_does_not_import_soapysdr() -> None:
    import rigplane.sdr.iq_scope  # noqa: F401

    assert "SoapySDR" not in sys.modules


def test_full_span_frame_metadata() -> None:
    frames: list[ScopeFrame] = []
    scope = _make_scope()
    scope.on_frame(frames.append)
    source = _make_source(tones=[(100_000.0, -6.0)])

    _drive(scope, source, frames, 1)
    scope.close()

    assert len(frames) == 1
    frame = frames[0]
    assert frame.receiver == 0
    assert frame.mode == 0
    assert frame.out_of_range is False
    assert frame.start_freq_hz == 10_000_000 - 1_200_000
    assert frame.end_freq_hz == 10_000_000 + 1_200_000
    assert len(frame.pixels) == 689


def test_process_pending_without_data_emits_nothing() -> None:
    frames: list[ScopeFrame] = []
    scope = _make_scope()
    scope.on_frame(frames.append)

    scope.process_pending()

    assert frames == []
    scope.close()


def test_tone_lands_in_expected_pixel() -> None:
    frames: list[ScopeFrame] = []
    scope = _make_scope()
    scope.on_frame(frames.append)
    source = _make_source(tones=[(100_000.0, -6.0)], noise_floor_dbfs=-95.0)

    _drive(scope, source, frames, 1)
    scope.close()

    pixels = np.frombuffer(frames[0].pixels, dtype=np.uint8)
    # Full 2.4 MHz span: +100 kHz sits at (100k + 1.2M) / 2.4M of the window.
    expected = int((100_000 + 1_200_000) / 2_400_000 * 689)
    peak = _peak_pixel(frames[0], expected)
    assert abs(peak - expected) <= 1
    # Max-hold resampling keeps the narrow (sub-pixel) CW-like signal strong.
    assert int(pixels[peak]) - int(np.median(pixels)) >= 40


def test_two_tones_1khz_apart_resolved_at_48khz_span() -> None:
    frames: list[ScopeFrame] = []
    scope = _make_scope()
    scope.on_frame(frames.append)
    source = _make_source(
        sample_rate_hz=48_000,
        tones=[(10_000.0, -10.0), (11_000.0, -10.0)],
        noise_floor_dbfs=-95.0,
    )

    _drive(scope, source, frames, 2)
    scope.close()

    pixels = np.frombuffer(frames[-1].pixels, dtype=np.uint8)
    expected_a = int((10_000 + 24_000) / 48_000 * 689)
    expected_b = int((11_000 + 24_000) / 48_000 * 689)
    peak_a = _peak_pixel(frames[-1], expected_a)
    peak_b = _peak_pixel(frames[-1], expected_b)
    assert abs(peak_a - expected_a) <= 2
    assert abs(peak_b - expected_b) <= 2
    valley = int(np.min(pixels[peak_a + 2 : peak_b - 1]))
    assert valley <= min(pixels[peak_a], pixels[peak_b]) - 20


def test_invert_spectrum_mirrors_peak() -> None:
    normal_frames: list[ScopeFrame] = []
    inverted_frames: list[ScopeFrame] = []
    normal = _make_scope()
    normal.on_frame(normal_frames.append)
    inverted = _make_scope(invert_spectrum=True)
    inverted.on_frame(inverted_frames.append)

    _drive(normal, _make_source(tones=[(100_000.0, -6.0)]), normal_frames, 1)
    _drive(inverted, _make_source(tones=[(100_000.0, -6.0)]), inverted_frames, 1)
    normal.close()
    inverted.close()

    expected = int((100_000 + 1_200_000) / 2_400_000 * 689)
    peak_normal = _peak_pixel(normal_frames[0], expected)
    peak_inverted = _peak_pixel(inverted_frames[0], 689 - 1 - expected, window=5)
    assert abs(peak_inverted - (689 - 1 - peak_normal)) <= 1


def test_view_shift_moves_peak() -> None:
    frames: list[ScopeFrame] = []
    scope = _make_scope()
    scope.on_frame(frames.append)
    source = _make_source(tones=[(100_000.0, -6.0)])
    scope.set_span(600_000)

    _drive(scope, source, frames, 1)
    scope.set_view_center(10_200_000)
    _drive(scope, source, frames, 1)
    scope.close()

    unshifted = int((100_000 + 300_000) / 600_000 * 689)
    shifted = int((100_000 - 200_000 + 300_000) / 600_000 * 689)
    peak_unshifted = _peak_pixel(frames[0], unshifted)
    peak_shifted = _peak_pixel(frames[1], shifted)
    assert abs(peak_unshifted - unshifted) <= 2
    assert abs(peak_shifted - shifted) <= 2
    assert abs((peak_shifted - peak_unshifted) - (shifted - unshifted)) <= 2


def test_view_outside_iq_window_sets_out_of_range() -> None:
    frames: list[ScopeFrame] = []
    scope = _make_scope()
    scope.on_frame(frames.append)
    source = _make_source(tones=[(100_000.0, -6.0)])

    scope.set_view_center(10_000_000 + 3_000_000)
    _drive(scope, source, frames, 1)
    scope.close()

    assert len(frames) == 1
    assert frames[0].out_of_range is True
    assert frames[0].pixels == b""
    assert frames[0].start_freq_hz == 10_000_000 + 3_000_000 - 1_200_000
    assert frames[0].end_freq_hz == 10_000_000 + 3_000_000 + 1_200_000


def test_set_span_crops_window_and_none_restores_full() -> None:
    frames: list[ScopeFrame] = []
    scope = _make_scope()
    scope.on_frame(frames.append)
    source = _make_source()
    scope.set_span(600_000)

    _drive(scope, source, frames, 1)
    scope.set_span(None)
    _drive(scope, source, frames, 1)
    scope.close()

    assert frames[0].start_freq_hz == 10_000_000 - 300_000
    assert frames[0].end_freq_hz == 10_000_000 + 300_000
    assert len(frames[0].pixels) == 689
    assert frames[1].start_freq_hz == 10_000_000 - 1_200_000
    assert frames[1].end_freq_hz == 10_000_000 + 1_200_000


def test_tx_freeze_repeats_pixels_and_marks_out_of_range() -> None:
    frames: list[ScopeFrame] = []
    scope = _make_scope()
    scope.on_frame(frames.append)
    source = _make_source(tones=[(100_000.0, -6.0)])

    _drive(scope, source, frames, 2)
    frozen_pixels = frames[-1].pixels
    scope.set_tx_active(True)
    _drive(scope, source, frames, 2)

    after_source = _make_source(tones=[(-100_000.0, -6.0)])
    scope.set_tx_active(False)
    _drive(scope, after_source, frames, 1)
    scope.close()

    tx_frames = frames[2:4]
    for frame in tx_frames:
        assert frame.out_of_range is True
        assert frame.pixels == frozen_pixels
    assert frames[-1].out_of_range is False
    assert frames[-1].pixels != frozen_pixels


def test_tx_before_any_frame_emits_empty_pixels() -> None:
    frames: list[ScopeFrame] = []
    scope = _make_scope()
    scope.on_frame(frames.append)
    source = _make_source()

    scope.set_tx_active(True)
    _drive(scope, source, frames, 1)
    scope.close()

    assert len(frames) == 1
    assert frames[0].out_of_range is True
    assert frames[0].pixels == b""


def test_overflow_resets_averaging() -> None:
    frames_a: list[ScopeFrame] = []
    frames_b: list[ScopeFrame] = []
    scope_a = _make_scope()
    scope_a.on_frame(frames_a.append)
    scope_b = _make_scope()
    scope_b.on_frame(frames_b.append)

    noise_a = _make_source(tones=(), noise_floor_dbfs=-95.0, seed=42)
    noise_b = _make_source(tones=(), noise_floor_dbfs=-95.0, seed=42)
    tone_a = _make_source(tones=[(100_000.0, -6.0)], noise_floor_dbfs=-95.0, seed=43)
    tone_b = _make_source(tones=[(100_000.0, -6.0)], noise_floor_dbfs=-95.0, seed=43)
    tone_b.set_overflow(True)

    _drive(scope_a, noise_a, frames_a, 3)
    _drive(scope_a, tone_a, frames_a, 1)
    _drive(scope_b, noise_b, frames_b, 3)
    _drive(scope_b, tone_b, frames_b, 1)
    scope_a.close()
    scope_b.close()

    expected = int((100_000 + 1_200_000) / 2_400_000 * 689)
    peak_a = _peak_pixel(frames_a[-1], expected)
    peak_b = _peak_pixel(frames_b[-1], expected)
    assert frames_b[-1].pixels[peak_b] > frames_a[-1].pixels[peak_a] + 10, (
        "overflow frame must not average with pre-overflow noise"
    )


def test_feed_is_bounded_drop_oldest_and_counts_drops() -> None:
    frames: list[ScopeFrame] = []
    scope = _make_scope()
    scope.on_frame(frames.append)
    noise = _make_source(tones=(), noise_floor_dbfs=-95.0, seed=5)
    tone = _make_source(tones=[(100_000.0, -6.0)], noise_floor_dbfs=-95.0, seed=6)
    noise.on_block(scope.feed)
    noise.open()
    noise.pump(70)
    assert scope.dropped_blocks == 6
    noise.close()

    tone.on_block(scope.feed)
    tone.open()
    tone.pump(1)
    tone.close()

    scope.process_pending()
    scope.close()

    assert len(frames) == 1
    expected = int((100_000 + 1_200_000) / 2_400_000 * 689)
    peak = _peak_pixel(frames[0], expected)
    assert abs(peak - expected) <= 1


def test_short_block_is_padded_and_still_frames() -> None:
    frames: list[ScopeFrame] = []
    scope = _make_scope()
    scope.on_frame(frames.append)

    scope.feed(
        IqBlock(
            samples=np.zeros(1024, dtype=np.complex64),
            center_freq_hz=10_000_000,
            sample_rate_hz=2_400_000,
            timestamp_s=0.0,
        )
    )
    scope.process_pending()
    scope.close()

    assert len(frames) == 1
    assert len(frames[0].pixels) == 689


def test_frames_are_deterministic_for_equal_input() -> None:
    frames_a: list[ScopeFrame] = []
    frames_b: list[ScopeFrame] = []
    scope_a = _make_scope()
    scope_a.on_frame(frames_a.append)
    scope_b = _make_scope()
    scope_b.on_frame(frames_b.append)

    _drive(scope_a, _make_source(tones=[(100_000.0, -6.0)], seed=7), frames_a, 3)
    _drive(scope_b, _make_source(tones=[(100_000.0, -6.0)], seed=7), frames_b, 3)
    scope_a.close()
    scope_b.close()

    assert [f.pixels for f in frames_a] == [f.pixels for f in frames_b]


def test_close_joins_worker_and_is_idempotent() -> None:
    frames: list[ScopeFrame] = []
    scope = IqFftScope(fft_size=4096)  # worker enabled
    scope.on_frame(frames.append)
    scope.feed(
        IqBlock(
            samples=np.zeros(4096, dtype=np.complex64),
            center_freq_hz=10_000_000,
            sample_rate_hz=2_400_000,
            timestamp_s=0.0,
        )
    )
    scope.close()
    scope.close()

    workers = [t for t in threading.enumerate() if t.name == _WORKER_NAME]
    assert workers == []
    scope.feed(
        IqBlock(
            samples=np.zeros(4096, dtype=np.complex64),
            center_freq_hz=10_000_000,
            sample_rate_hz=2_400_000,
            timestamp_s=2.0,
        )
    )  # feeding a closed sink must not raise


def test_worker_thread_emits_frames_from_background_source() -> None:
    frames: list[ScopeFrame] = []
    got_frame = threading.Event()
    lock = threading.Lock()

    def collector(frame: ScopeFrame) -> None:
        with lock:
            frames.append(frame)
        got_frame.set()

    scope = IqFftScope(fft_size=4096)  # worker enabled
    scope.on_frame(collector)
    source = _make_source(
        tones=[(100_000.0, -6.0)],
        background_interval_s=0.005,
    )
    source.on_block(scope.feed)
    source.open()

    try:
        assert got_frame.wait(timeout=5.0), "worker emitted no frame"
    finally:
        scope.close()
        source.close()

    with lock:
        assert len(frames) >= 1
    assert frames[0].receiver == 0
    assert frames[0].mode == 0
    workers = [t for t in threading.enumerate() if t.name == _WORKER_NAME]
    assert workers == []


def test_frame_callback_exception_is_contained() -> None:
    frames: list[ScopeFrame] = []

    def broken(frame: ScopeFrame) -> None:
        raise RuntimeError("boom")

    scope = _make_scope()
    scope.on_frame(broken)
    source = _make_source(tones=[(100_000.0, -6.0)])
    source.on_block(scope.feed)

    source.open()
    source.pump(1)
    source.close()
    scope.process_pending()  # must contain the callback error

    scope.on_frame(frames.append)
    source.open()
    source.pump(1)
    source.close()
    scope.process_pending()
    scope.close()

    assert len(frames) == 1


@pytest.mark.benchmark
def test_processing_performance_budget() -> None:
    """Per-frame cost at the RTL-SDR operating point (MOR-3154 budget).

    Measures the two thread budgets separately with 2.4 Msps blocks,
    fft_size 4096, pixel_width 689, full span: the enqueue-only ``feed``
    (source reader thread) and the FFT chain per emitted frame (worker
    thread at 20 fps). The MOR-3154 budget is ≤ 25 % of one Pi 4 core
    — 12.5 ms per frame at 20 fps on the Pi 4; this host's measured
    numbers (printed by a deliberate ``-m benchmark`` run) are recorded
    in the PR with the extrapolation.
    """
    n_frames = 200
    blocks: list[IqBlock] = []
    collector_source = _make_source(tones=[(100_000.0, -6.0)], noise_floor_dbfs=-90.0)
    collector_source.on_block(blocks.append)
    collector_source.open()
    collector_source.pump(n_frames)
    collector_source.close()

    # Reader-thread budget: enqueue-only cost of feed().
    feed_scope = _make_scope()
    feed_start = time.perf_counter()
    for block in blocks:
        feed_scope.feed(block)
    feed_s = time.perf_counter() - feed_start
    feed_scope.close()

    # Worker-thread budget: the FFT chain per emitted frame.
    frames: list[ScopeFrame] = []
    scope = _make_scope()
    scope.on_frame(frames.append)
    process_s = 0.0
    for block in blocks:
        scope.feed(block)
        process_start = time.perf_counter()
        scope.process_pending()
        process_s += time.perf_counter() - process_start
    scope.close()

    per_feed_ms = feed_s / n_frames * 1000.0
    per_frame_ms = process_s / n_frames * 1000.0
    print(
        f"\nIqFftScope @2.4 Msps fft=4096: feed={per_feed_ms:.3f} ms/block, "
        f"frame={per_frame_ms:.3f} ms/frame ({len(frames)} frames)"
    )

    # Enqueue must stay far under one fps tick; the FFT chain must stay
    # under the 12.5 ms/frame Pi 4 budget with multi-fold host margin.
    assert len(frames) == n_frames
    assert per_feed_ms < 1.0
    assert per_frame_ms < 8.0
