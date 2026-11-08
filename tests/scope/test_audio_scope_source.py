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


@pytest.mark.parametrize(
    "sideband,lo,hi,carrier,expected",
    [
        ("upper", 300, 2700, 0, 14_101_500),
        ("lower", -2700, -300, 0, 14_098_500),
        ("upper", -300, 300, -700, 14_100_000),
    ],
)
async def test_real_pcm_passband_mapping(sideband, lo, hi, carrier, expected):
    facts = AudioPassband(14_100_000, lo, hi, sideband, carrier)
    tap = Tap()
    source = AudioFftSource(tap, lambda: facts, fft_size=4096, sample_rate=48000)
    frames = []
    source.on_frame(frames.append)
    await source.start()
    tone = 700 if carrier else 1500
    samples = np.sin(2 * np.pi * tone * np.arange(4096) / 48000)
    tap.callback((samples * 16000).astype("<i2").tobytes())
    frame = frames[-1]
    assert frame.receiver is None
    assert lo <= frame.coverage.start_hz - facts.tuned_hz <= lo + 12
    assert hi - 12 <= frame.coverage.end_hz - facts.tuned_hz <= hi
    peak = int(np.argmax(np.frombuffer(frame.pixels, dtype=np.uint8)))
    peak_hz = frame.coverage.start_hz + peak * (
        frame.coverage.end_hz - frame.coverage.start_hz
    ) / (len(frame.pixels) - 1)
    assert abs(peak_hz - expected) < 25
    assert source.coverage_for(View(1, 2)) == Coverage(14_100_000 + lo, 14_100_000 + hi)
    await source.stop()
    assert tap.releases == 1


async def test_follow_live_and_release_on_cleanup_failure(monkeypatch):
    facts = AudioPassband(7_100_000, 0, 2400, "upper")
    tap = Tap()
    source = AudioFftSource(tap, lambda: facts)
    assert (await source.set_view(FollowView("vfo", 10000))).view == View(
        7_095_000, 7_105_000
    )
    await source.start()

    def fail(callback):
        raise RuntimeError("cleanup")

    monkeypatch.setattr(source.producer, "on_frame", fail)
    with pytest.raises(RuntimeError, match="cleanup"):
        await source.stop()
    assert tap.releases == 1


async def test_pcm_frame_routes_through_real_hub_and_releases_on_last_close():
    from rigplane.scope.hub import ScopeHub
    from rigplane.scope.models import ScopeSourcePlugin, Subscription

    tap = Tap()
    source = AudioFftSource(tap, lambda: AudioPassband(14_100_000, 300, 2700, "upper"))
    plugin: ScopeSourcePlugin = source
    encoded = []

    def encode(frame):
        encoded.append(frame)
        return b"frame"

    hub = ScopeHub(encoder=encode)
    hub.register("audio", plugin)
    request = Subscription("audio", None, View(14_099_000, 14_104_000), 64, 20)
    first = await hub.subscribe(request)
    second = await hub.subscribe(request)
    samples = np.sin(2 * np.pi * 1500 * np.arange(4096) / 48000)
    tap.callback((samples * 16000).astype("<i2").tobytes())
    assert await first.receive() == await second.receive() == b"frame"
    assert len(encoded) == 1
    assert encoded[0].receiver is None
    assert encoded[0].coverage_start_hz > request.view.start_hz
    assert encoded[0].coverage_end_hz < request.view.end_hz
    await first.close()
    assert tap.releases == 0
    await second.close()
    assert tap.releases == 1


async def test_constructor_does_not_require_observed_passband():
    def facts():
        raise ValueError("passband not observed")

    source = AudioFftSource(Tap(), facts)
    with pytest.raises(ValueError, match="passband not observed"):
        source.coverage_for(View(7_100_000, 7_103_000))


async def test_failed_pcm_tap_release_can_be_retried():
    class FlakyTap(Tap):
        def __init__(self):
            super().__init__()
            self.attempts = 0

        async def release(self):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("release failed")
            await super().release()

    tap = FlakyTap()
    source = AudioFftSource(tap, lambda: AudioPassband(7_100_000, 0, 2400, "upper"))
    await source.start()
    with pytest.raises(RuntimeError, match="release failed"):
        await source.stop()
    await source.stop()
    assert tap.attempts == 2
    assert tap.releases == 1
    assert tap.callback is None


@pytest.mark.parametrize("old_count", [3072, 4096])
async def test_geometry_change_discards_pcm_and_fft_history(
    old_count, virtual_clock, monkeypatch
):
    import rigplane.audio.fft_scope as fft_module

    from types import SimpleNamespace

    monkeypatch.setattr(fft_module, "time", SimpleNamespace(monotonic=virtual_clock))
    await virtual_clock.elapse(1)
    facts = AudioPassband(14_100_000, 300, 2700, "upper")
    tap = Tap()
    source = AudioFftSource(tap, lambda: facts)
    frames = []
    source.on_frame(frames.append)
    await source.start()
    tone = (np.sin(2 * np.pi * 1500 * np.arange(old_count) / 48000) * 16000).astype(
        "<i2"
    )
    tap.callback(tone.tobytes())
    frames.clear()
    facts = AudioPassband(7_100_000, -2700, -300, "lower")
    noise = np.random.default_rng(12).integers(-100, 101, 4096, dtype=np.int16)
    await virtual_clock.elapse(0.1)
    tap.callback(noise[:1024].tobytes())
    assert not frames
    tap.callback(noise[1024:].tobytes())
    assert len(frames) == 1
    reference_tap = Tap()
    reference = AudioFftSource(reference_tap, lambda: facts)
    expected = []
    reference.on_frame(expected.append)
    await reference.start()
    reference_tap.callback(noise.tobytes())
    assert frames[0] == expected[0]
    await source.stop()
    await reference.stop()


async def test_failed_release_does_not_restart_fft_on_changed_facts():
    class RetainedTap(Tap):
        async def release(self):
            if not self.releases:
                self.releases += 1
                raise RuntimeError("release failed")
            await super().release()

    facts = AudioPassband(14_100_000, 300, 2700, "upper")
    tap = RetainedTap()
    source = AudioFftSource(tap, lambda: facts)
    frames = []
    source.on_frame(frames.append)
    await source.start()
    with pytest.raises(RuntimeError, match="release failed"):
        await source.stop()
    try:
        facts = AudioPassband(7_100_000, -2700, -300, "lower")
        tap.callback(np.ones(4096, dtype="<i2").tobytes())
        assert not frames
    finally:
        await source.stop()
