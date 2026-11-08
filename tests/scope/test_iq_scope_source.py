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
