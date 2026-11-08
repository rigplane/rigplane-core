import math
import time

import numpy as np
import pytest

from rigplane.sdr import FakeIqSource
from rigplane.sdr.controller import SdrScopeController, TX_HOLD_S
from rigplane.sdr.scope_source import IqFftSource
from rigplane.sdr.types import SdrConfig
from rigplane.scope.models import Coverage, FollowView, View


def controlled_source(
    provider, virtual_clock, *, tx_active=False, invert=False, clock_offset=1_000_000
):
    config = SdrConfig(
        "fake", sample_rate_hz=48000, span_hz=6000, freq_offset_hz=8_000_000
    )

    def controller_factory(sink):
        controller = SdrScopeController(
            provider, sink, config, clock=lambda: virtual_clock() + clock_offset
        )
        controller.on_radio_state(14_100_000, tx_active)
        return controller

    return IqFftSource(
        provider,
        native_width=64,
        start_worker=False,
        controller_factory=controller_factory,
        invert_spectrum=invert,
    )


@pytest.mark.parametrize("invert,peak", [(False, 40), (True, 23)])
async def test_controller_seeds_rf_before_open_without_cropping_capture(
    virtual_clock, invert, peak
):
    class Provider(FakeIqSource):
        def open(self):
            self.opened_at = self.center_freq_hz
            super().open()

    provider = Provider(
        center_freq_hz=0, sample_rate_hz=48000, block_size=4096, tones=[(6000, -6)]
    )
    source = controlled_source(provider, virtual_clock, invert=invert)
    frames = []
    source.on_frame(frames.append)
    await source.start()
    try:
        assert source.active
        assert provider.opened_at == 22_088_000
        assert source.resolve_view(FollowView("vfo", 20000)) == View(
            22_090_000, 22_110_000
        )
        provider.pump(1)
        source.producer.process_pending()
        assert frames[-1].coverage == Coverage(22_064_000, 22_112_000)
        assert (
            abs(np.argmax(np.frombuffer(frames[-1].pixels, dtype=np.uint8)) - peak) <= 1
        )
    finally:
        await source.stop()
    assert not source.active and not provider.is_open


async def test_controller_initial_tx_freeze_releases_after_hold(virtual_clock):
    provider = FakeIqSource(center_freq_hz=0, sample_rate_hz=48000)
    source = controlled_source(provider, virtual_clock, tx_active=True)
    frames = []
    source.on_frame(frames.append)
    await source.start()
    try:
        provider.pump(1)
        source.producer.process_pending()
        assert source.tx_frozen
        assert not frames  # No measured pre-TX frame exists to freeze yet.
        source.on_radio_state(None, False)
        await virtual_clock.elapse(0.299)
        assert source.tx_frozen
        await virtual_clock.elapse(0.002)
        assert not source.tx_frozen
        provider.pump(1)
        source.producer.process_pending()
        assert not frames[-1].out_of_range and frames[-1].pixels
        assert frames[-1].coverage == Coverage(22_064_000, 22_112_000)
    finally:
        await source.stop()


@pytest.mark.parametrize("clock_offset", [0, 1_000_000])
async def test_early_tx_deadline_callback_waits_without_spinning(
    virtual_clock, clock_offset
):
    provider = FakeIqSource(center_freq_hz=0, sample_rate_hz=48000)
    source = controlled_source(
        provider, virtual_clock, tx_active=True, clock_offset=clock_offset
    )
    await source.start()
    try:
        source.on_radio_state(None, False)
        resolution = max(
            time.get_clock_info("monotonic").resolution, math.ulp(virtual_clock())
        )
        # asyncio may run a timer up to one clock resolution early. Keep
        # virtual time inside that window so a premature callback must defer.
        virtual_clock.advance(TX_HOLD_S - resolution / 2)
        await virtual_clock.elapse(resolution / 4)
        assert source.tx_frozen
        await virtual_clock.elapse(resolution * 2)
        assert not source.tx_frozen
    finally:
        await source.stop()


async def test_controller_retune_debounce_and_stop_cancel_pending_actions(
    virtual_clock,
):
    provider = FakeIqSource(center_freq_hz=0, sample_rate_hz=48000)
    source = controlled_source(provider, virtual_clock)
    await source.start()
    try:
        source.on_radio_state(14_200_000, False)
        assert provider.center_freq_hz == 22_088_000
        await virtual_clock.elapse(0.1)
        source.on_radio_state(14_300_000, False)
        assert source.resolve_view(FollowView("vfo", 20000)) == View(
            22_290_000, 22_310_000
        )
        await virtual_clock.elapse(0.051)
        assert provider.center_freq_hz == 22_288_000
        source.on_radio_state(14_400_000, True)
        source.on_radio_state(None, False)
        await source.stop()
        await virtual_clock.elapse(1)
        source.on_radio_state(14_500_000, True)
        assert provider.center_freq_hz == 22_288_000
        assert not source.active and not source.tx_frozen
    finally:
        await source.stop()


async def test_controller_outside_provider_range_marks_native_frame(virtual_clock):
    provider = FakeIqSource(center_freq_hz=0, sample_rate_hz=48000)
    source = controlled_source(provider, virtual_clock)
    frames = []
    source.on_frame(frames.append)
    await source.start()
    try:
        source.on_radio_state(7_000_000_000, False)
        provider.pump(1)
        source.producer.process_pending()
        assert not source.frequency_in_range
        assert frames[-1].out_of_range
    finally:
        await source.stop()


@pytest.mark.parametrize("failure", ["factory", "open"])
async def test_composition_start_failure_closes_owned_worker(failure):
    class Provider(FakeIqSource):
        def open(self):
            if failure == "open":
                raise RuntimeError("open failed")
            super().open()

    provider = Provider(center_freq_hz=0, sample_rate_hz=48000)

    def factory(sink):
        if failure == "factory":
            raise RuntimeError("factory failed")
        controller = SdrScopeController(provider, sink, SdrConfig("fake"))
        controller.on_radio_state(14_100_000, False)
        return controller

    source = IqFftSource(provider, controller_factory=factory)
    standby = source.producer
    assert standby._thread is None
    try:
        with pytest.raises(RuntimeError, match=f"{failure} failed"):
            await source.start()
        assert not source.active and not provider.is_open
        thread = source.producer._thread
        if failure == "open":
            assert thread is not None
        assert thread is None or not thread.is_alive()
    finally:
        await source.stop()
        source.producer.close()
        standby.close()


async def test_real_iq_provider_full_coverage_peak_and_restart():
    provider = FakeIqSource(
        center_freq_hz=10_000_000,
        sample_rate_hz=48000,
        block_size=4096,
        tones=[(6000, -6)],
        seed=21,
    )
    source = IqFftSource(provider, fft_size=4096, native_width=512, start_worker=False)
    view = View(9_950_000, 10_050_000)
    assert source.coverage_for(view) == Coverage(9_976_000, 10_024_000)
    assert (await source.set_view(view)).coverage == Coverage(9_976_000, 10_024_000)
    frames = []
    source.on_frame(frames.append)
    for _ in range(2):
        await source.start()
        provider.pump(1)
        source.producer.process_pending()
        assert provider.is_open
        frame = frames[-1]
        assert frame.receiver is None
        assert frame.coverage == Coverage(9_976_000, 10_024_000)
        assert abs(np.argmax(np.frombuffer(frame.pixels, dtype=np.uint8)) - 320) < 5
        await source.stop()
        assert not provider.is_open


async def test_partial_iq_view_zoom_never_retunes_provider():
    provider = FakeIqSource(center_freq_hz=10_000_000, sample_rate_hz=48000)
    source = IqFftSource(provider, start_worker=False)
    view = View(10_012_000, 10_032_000)
    assert (await source.set_view(view)).coverage == Coverage(10_012_000, 10_024_000)
    assert provider.center_freq_hz == 10_000_000


async def test_follow_tracks_provider_retune_through_real_hub():
    from rigplane.scope.hub import ScopeHub
    from rigplane.scope.models import FollowView, Subscription

    provider = FakeIqSource(
        center_freq_hz=10_000_000,
        sample_rate_hz=48000,
        block_size=4096,
        tones=[(6000, -6)],
        seed=21,
    )
    source = IqFftSource(provider, native_width=512, start_worker=False)
    encoded = []

    def encode(frame):
        encoded.append(frame)
        return b"frame"

    hub = ScopeHub(encoder=encode)
    hub.register("iq", source)
    sub = await hub.subscribe(
        Subscription("iq", None, FollowView("vfo", 20000), 512, 20)
    )
    try:
        provider.set_center_freq(11_000_000)
        provider.pump(1)
        source.producer.process_pending()
        assert await sub.receive() == b"frame"
        frame = encoded[-1]
        assert frame.receiver is None
        assert not frame.out_of_range
        assert abs(frame.coverage_start_hz - 10_990_000) < 12
        assert abs(frame.coverage_end_hz - 11_010_000) < 12
        assert frame.pixels is not None
    finally:
        await sub.close()
    assert not provider.is_open


class SampleProvider(FakeIqSource):
    def __init__(self, samples, **kwargs):
        super().__init__(block_size=4096, **kwargs)
        self.samples = samples

    def _generate_samples(self):
        return self.samples


@pytest.mark.parametrize("center,rate", [(11_000_000, 48000), (10_000_000, 96000)])
async def test_new_iq_geometry_discards_old_fft_average(center, rate):
    from rigplane.sdr.types import IqBlock

    noise = (
        np.random.default_rng(22).normal(size=4096)
        + 1j * np.random.default_rng(23).normal(size=4096)
    ).astype(np.complex64) * 0.001
    tone = np.exp(2j * np.pi * 6000 * np.arange(4096) / 48000).astype(np.complex64)
    provider = SampleProvider(tone, center_freq_hz=10_000_000, sample_rate_hz=48000)
    source = IqFftSource(provider, start_worker=False)
    frames = []
    source.on_frame(frames.append)
    await source.start()
    provider.pump(1)
    source.producer.process_pending()
    block = IqBlock(noise, center, rate, 1.0)
    source._feed(block)
    source.producer.process_pending()
    reference_provider = SampleProvider(
        noise, center_freq_hz=center, sample_rate_hz=rate
    )
    reference = IqFftSource(reference_provider, start_worker=False)
    expected = []
    reference.on_frame(expected.append)
    await reference.start()
    reference_provider.pump(1)
    reference.producer.process_pending()
    assert frames[-1] == expected[-1]
    await source.stop()
    await reference.stop()


@pytest.mark.parametrize("narrow_first", [False, True])
@pytest.mark.parametrize("close_narrow", [False, True])
async def test_shared_iq_keeps_full_capture_for_both_views(
    narrow_first, close_narrow, virtual_clock
):
    from rigplane.scope.hub import ScopeHub
    from rigplane.scope.models import Subscription

    provider = FakeIqSource(
        center_freq_hz=10_000_000,
        sample_rate_hz=48000,
        block_size=4096,
        tones=[(-12000, -6), (6000, -6)],
        seed=21,
    )
    source = IqFftSource(provider, native_width=1024, start_worker=False)
    encoded = []

    def encode(frame):
        encoded.append(frame)
        return b"frame"

    hub = ScopeHub(encoder=encode)
    hub.register("iq", source)
    views = [View(9_976_000, 10_024_000), View(10_000_000, 10_012_000)]
    order = [1, 0] if narrow_first else [0, 1]
    handles = {}
    for i in order:
        handles[i] = await hub.subscribe(Subscription("iq", None, views[i], 512, 20))
    try:
        for step in range(2):
            await virtual_clock.elapse(0.1)
            encoded.clear()
            provider.pump(1)
            source.producer.process_pending()
            for i, handle in handles.items():
                assert await handle.receive() == b"frame"
                frame = next(f for f in encoded if f.view_start_hz == views[i].start_hz)
                assert frame.coverage_start_hz == views[i].start_hz
                assert frame.coverage_end_hz == views[i].end_hz
                pixels = np.frombuffer(frame.pixels, dtype=np.uint8)
                peaks = [6000] if i == 1 else [-12000, 6000]
                for offset in peaks:
                    index = int(
                        (10_000_000 + offset - views[i].start_hz)
                        * 512
                        / (views[i].end_hz - views[i].start_hz)
                    )
                    assert pixels[max(0, index - 2) : index + 3].max() > 100
            if step == 0:
                await handles.pop(1 if close_narrow else 0).close()
    finally:
        for handle in handles.values():
            await handle.close()
    assert not provider.is_open


async def test_geometry_change_enqueues_on_existing_worker(monkeypatch):
    from unittest.mock import MagicMock

    provider = FakeIqSource(center_freq_hz=10_000_000, sample_rate_hz=48000)
    source = IqFftSource(provider)
    assert source.producer._thread is None
    await source.start()
    provider.pump(1)
    old_producer = source.producer
    old_worker = old_producer._thread
    assert old_worker is not None and old_worker.is_alive()
    close = MagicMock(wraps=old_producer.close)
    monkeypatch.setattr(old_producer, "close", close)
    try:
        provider.set_center_freq(11_000_000)
        provider.pump(1)
        close.assert_not_called()
        assert source.producer is old_producer
        assert old_worker.is_alive()
    finally:
        await source.stop()
        old_producer.close()
    assert not old_worker.is_alive()
    assert not provider.is_open
