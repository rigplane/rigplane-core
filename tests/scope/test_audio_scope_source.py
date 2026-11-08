import numpy as np
import pytest

from rigplane.audio.scope_source import AudioFftSource, AudioPassband, PcmTap
from rigplane.scope.models import Coverage, FollowView, View


class Tap(PcmTap):
    def __init__(self):
        self.callback = None
        self.releases = 0

    async def acquire(self, callback):
        self.callback = callback

    async def release(self):
        self.callback = None
        self.releases += 1


@pytest.mark.parametrize('sideband,lo,hi,carrier,expected', [
    ('upper', 300, 2700, 0, 14_101_500),
    ('lower', -2700, -300, 0, 14_098_500),
    ('upper', -300, 300, -700, 14_100_000),
])
async def test_real_pcm_passband_mapping(sideband, lo, hi, carrier, expected):
    facts = AudioPassband(14_100_000, lo, hi, sideband, carrier)
    tap = Tap()
    source = AudioFftSource(tap, lambda: facts, fft_size=4096, sample_rate=48000)
    frames = []
    source.on_frame(frames.append)
    await source.start()
    tone = 700 if carrier else 1500
    samples = np.sin(2 * np.pi * tone * np.arange(4096) / 48000)
    tap.callback((samples * 16000).astype('<i2').tobytes())
    frame = frames[-1]
    assert frame.receiver is None
    assert lo <= frame.coverage.start_hz - facts.tuned_hz <= lo + 12
    assert hi - 12 <= frame.coverage.end_hz - facts.tuned_hz <= hi
    peak = int(np.argmax(np.frombuffer(frame.pixels, dtype=np.uint8)))
    peak_hz = frame.coverage.start_hz + peak * (
        frame.coverage.end_hz - frame.coverage.start_hz) / (len(frame.pixels) - 1)
    assert abs(peak_hz - expected) < 25
    assert source.coverage_for(View(1, 2)) == Coverage(14_100_000 + lo, 14_100_000 + hi)
    await source.stop()
    assert tap.releases == 1


async def test_follow_live_and_release_on_cleanup_failure(monkeypatch):
    facts = AudioPassband(7_100_000, 0, 2400, 'upper')
    tap = Tap()
    source = AudioFftSource(tap, lambda: facts)
    assert (await source.set_view(FollowView('vfo', 10000))).view == View(7_095_000, 7_105_000)
    await source.start()
    def fail(callback):
        raise RuntimeError('cleanup')
    monkeypatch.setattr(source.producer, 'on_frame', fail)
    with pytest.raises(RuntimeError, match='cleanup'):
        await source.stop()
    assert tap.releases == 1
