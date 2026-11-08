import numpy as np

from rigplane.sdr import FakeIqSource
from rigplane.sdr.scope_source import IqFftSource
from rigplane.scope.models import Coverage, View


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


async def test_failed_iq_provider_close_can_be_retried():
    import pytest

    class FlakyProvider(FakeIqSource):
        def __init__(self):
            super().__init__(center_freq_hz=10_000_000, sample_rate_hz=48000)
            self.close_attempts = 0

        def close(self):
            self.close_attempts += 1
            if self.close_attempts == 1:
                raise RuntimeError("close failed")
            super().close()

    provider = FlakyProvider()
    source = IqFftSource(provider, start_worker=False)
    await source.start()
    with pytest.raises(RuntimeError, match="close failed"):
        await source.stop()
    assert provider.is_open
    await source.stop()
    assert provider.close_attempts == 2
    assert not provider.is_open


class SampleProvider(FakeIqSource):
    def __init__(self, samples, **kwargs):
        super().__init__(block_size=4096, **kwargs)
        self.samples = samples

    def _generate_samples(self):
        return self.samples


async def test_new_iq_geometry_discards_old_fft_average():
    from rigplane.sdr.types import IqBlock

    noise = (np.random.default_rng(22).normal(size=4096) +
             1j * np.random.default_rng(23).normal(size=4096)).astype(np.complex64) * 0.001
    tone = np.exp(2j * np.pi * 6000 * np.arange(4096) / 48000).astype(np.complex64)
    for center, rate in [(11_000_000, 48000), (10_000_000, 96000)]:
        provider = SampleProvider(tone, center_freq_hz=10_000_000, sample_rate_hz=48000)
        source = IqFftSource(provider, start_worker=False)
        frames = []
        source.on_frame(frames.append)
        await source.start()
        provider.pump(1)
        source.producer.process_pending()
        provider.set_center_freq(center)
        provider.set_sample_rate(rate)
        # Exercise block metadata even if provider properties have already changed.
        block = IqBlock(noise, center, rate, 1.0)
        source._feed(block)
        source.producer.process_pending()
        reference_provider = SampleProvider(noise, center_freq_hz=center, sample_rate_hz=rate)
        reference = IqFftSource(reference_provider, start_worker=False)
        expected = []
        reference.on_frame(expected.append)
        await reference.start()
        reference_provider.pump(1)
        reference.producer.process_pending()
        assert frames[-1] == expected[-1]
        await source.stop()
        await reference.stop()


import pytest


@pytest.mark.parametrize("narrow_first", [False, True])
@pytest.mark.parametrize("close_narrow", [False, True])
async def test_shared_iq_keeps_full_capture_for_both_views(narrow_first, close_narrow, virtual_clock):
    from rigplane.scope.hub import ScopeHub
    from rigplane.scope.models import Subscription

    provider = FakeIqSource(center_freq_hz=10_000_000, sample_rate_hz=48000,
                            block_size=4096, tones=[(-12000, -6), (6000, -6)], seed=21)
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
                    index = int((10_000_000 + offset - views[i].start_hz) * 512 /
                                (views[i].end_hz - views[i].start_hz))
                    assert pixels[max(0, index - 2):index + 3].max() > 100
            if step == 0:
                await handles.pop(1 if close_narrow else 0).close()
    finally:
        for handle in handles.values():
            await handle.close()
    assert not provider.is_open
