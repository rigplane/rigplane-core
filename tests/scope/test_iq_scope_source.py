import numpy as np

from rigplane.sdr import FakeIqSource
from rigplane.sdr.scope_source import IqFftSource
from rigplane.scope.models import Coverage, View


async def test_real_iq_provider_full_coverage_peak_and_restart():
    provider = FakeIqSource(center_freq_hz=10_000_000, sample_rate_hz=48000,
                            block_size=4096, tones=[(6000, -6)], seed=21)
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
