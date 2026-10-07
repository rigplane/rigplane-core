"""Tests for rigplane.sdr.fake — FakeIqSource (MOR-3151)."""

from __future__ import annotations

import threading
import time
from typing import Callable

import numpy as np
import pytest

from rigplane.scope import ScopeFrame
from rigplane.sdr import FakeIqSource, IqBlock, IqScopeSink, IqSource


def _pump_blocks(**kwargs: object) -> list[IqBlock]:
    """Open a FakeIqSource with ``kwargs``, pump 2 blocks, close, return them."""
    blocks: list[IqBlock] = []
    source = FakeIqSource(**kwargs)  # type: ignore[arg-type]
    source.on_block(blocks.append)
    source.open()
    source.pump(2)
    source.close()
    return blocks


class _RecordingSink:
    """Minimal IqScopeSink stand-in: records calls, does no work."""

    def __init__(self) -> None:
        self.fed: list[IqBlock] = []
        self.view_center: int | None = None
        self.span: int | None = -1
        self.tx_active: bool | None = None
        self.frame_callback: Callable[[ScopeFrame], None] | None = None

    def feed(self, block: IqBlock) -> None:
        self.fed.append(block)

    def set_view_center(self, hz: int) -> None:
        self.view_center = hz

    def set_span(self, hz: int | None) -> None:
        self.span = hz

    def set_tx_active(self, active: bool) -> None:
        self.tx_active = active

    def on_frame(self, callback: Callable[[ScopeFrame], None] | None) -> None:
        self.frame_callback = callback


def test_fake_iq_source_satisfies_protocol() -> None:
    assert isinstance(FakeIqSource(), IqSource)


def test_iq_scope_sink_protocol_shape() -> None:
    assert isinstance(_RecordingSink(), IqScopeSink)


def test_iq_scope_sink_rejects_incomplete_implementation() -> None:
    class _MissingFeed:
        def set_view_center(self, hz: int) -> None: ...

        def set_span(self, hz: int | None) -> None: ...

        def set_tx_active(self, active: bool) -> None: ...

        def on_frame(self, callback: Callable[[ScopeFrame], None] | None) -> None: ...

    assert not isinstance(_MissingFeed(), IqScopeSink)


def test_blocks_have_requested_size_and_dtype() -> None:
    blocks = _pump_blocks(sample_rate_hz=48_000, block_size=1024, seed=5)

    assert len(blocks) == 2
    for block in blocks:
        assert block.samples.ndim == 1
        assert block.samples.shape == (1024,)
        assert block.samples.dtype == np.complex64
        assert block.overflow is False


def test_block_metadata_and_setters() -> None:
    blocks: list[IqBlock] = []
    source = FakeIqSource(
        center_freq_hz=7_100_000,
        sample_rate_hz=48_000,
        block_size=128,
        frequency_range=(100_000, 2_000_000_000),
        seed=2,
    )
    source.on_block(blocks.append)

    assert source.is_open is False
    source.open()
    assert source.is_open is True

    assert source.center_freq_hz == 7_100_000
    assert source.sample_rate_hz == 48_000
    assert source.frequency_range_hz() == (100_000, 2_000_000_000)

    source.set_center_freq(14_074_000)
    source.set_sample_rate(96_000)
    source.set_gain(19.0)
    source.set_gain(None)
    assert source.center_freq_hz == 14_074_000
    assert source.sample_rate_hz == 96_000

    source.pump(2)
    source.close()
    assert source.is_open is False

    assert len(blocks) == 2
    for block in blocks:
        assert block.center_freq_hz == 14_074_000
        assert block.sample_rate_hz == 96_000
    assert blocks[1].timestamp_s >= blocks[0].timestamp_s


def test_tone_at_configured_offset_is_fft_peak() -> None:
    blocks = _pump_blocks(
        sample_rate_hz=48_000,
        block_size=1024,
        tones=[(5_000.0, -6.0)],
        noise_floor_dbfs=-100.0,
        seed=6,
    )

    samples = np.concatenate([block.samples for block in blocks])
    spectrum = np.abs(np.fft.fft(samples))
    peak_bin = int(np.argmax(spectrum))
    bin_hz = 48_000 / samples.size
    expected_bin = 5_000.0 / bin_hz

    assert abs(peak_bin - expected_bin) <= 1.0


def test_overflow_flag_injectable() -> None:
    blocks: list[IqBlock] = []
    source = FakeIqSource(block_size=32, seed=9)
    source.on_block(blocks.append)
    source.open()

    source.pump(1)
    assert blocks[-1].overflow is False

    source.set_overflow(True)
    source.pump(1)
    assert blocks[-1].overflow is True

    source.set_overflow(False)
    source.pump(1)
    assert blocks[-1].overflow is False

    source.close()


def test_pump_requires_open() -> None:
    source = FakeIqSource(block_size=32)

    with pytest.raises(RuntimeError):
        source.pump(1)

    source.open()
    source.open()  # idempotent
    source.pump(1)
    source.close()
    source.close()  # idempotent

    with pytest.raises(RuntimeError):
        source.pump(1)


def test_on_block_is_single_slot() -> None:
    first: list[IqBlock] = []
    second: list[IqBlock] = []
    source = FakeIqSource(block_size=32, seed=4)
    source.open()

    source.on_block(first.append)
    source.pump(1)
    assert len(first) == 1

    source.on_block(second.append)
    source.pump(1)
    assert len(first) == 1
    assert len(second) == 1

    source.on_block(None)
    source.pump(1)
    assert len(second) == 1

    source.close()


def test_generation_is_seeded() -> None:
    same_a = _pump_blocks(block_size=64, seed=7)
    same_b = _pump_blocks(block_size=64, seed=7)
    other = _pump_blocks(block_size=64, seed=8)

    a = np.concatenate([block.samples for block in same_a])
    b = np.concatenate([block.samples for block in same_b])
    c = np.concatenate([block.samples for block in other])

    assert np.array_equal(a, b)
    assert not np.array_equal(a, c)


def test_fake_source_can_drive_a_sink() -> None:
    sink = _RecordingSink()
    source = FakeIqSource(block_size=64, seed=3)
    source.on_block(sink.feed)

    source.open()
    source.pump(2)
    source.close()

    assert len(sink.fed) == 2
    assert all(block.sample_rate_hz == 2_400_000 for block in sink.fed)


def test_background_thread_delivers_on_reader_thread() -> None:
    blocks: list[IqBlock] = []
    delivery_threads: list[threading.Thread] = []
    got_three = threading.Event()
    lock = threading.Lock()

    def collector(block: IqBlock) -> None:
        with lock:
            blocks.append(block)
            delivery_threads.append(threading.current_thread())
            if len(blocks) >= 3:
                got_three.set()

    source = FakeIqSource(
        center_freq_hz=14_074_000,
        sample_rate_hz=48_000,
        block_size=256,
        background_interval_s=0.001,
        seed=1,
    )
    source.on_block(collector)
    source.open()

    try:
        assert got_three.wait(timeout=5.0), "background reader delivered no blocks"
    finally:
        source.close()

    assert source.is_open is False
    assert all(thread is not threading.main_thread() for thread in delivery_threads)

    with lock:
        count_after_close = len(blocks)
    time.sleep(0.02)
    with lock:
        assert len(blocks) == count_after_close
