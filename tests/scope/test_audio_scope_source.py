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


def test_symmetric_passband_covers_tuned_plus_minus_half():
    from rigplane.audio.scope_source import AudioPassband as Passband

    facts = Passband(14_100_000, -1800, 1800, "both")
    source = AudioFftSource(Tap(), lambda: facts)
    assert source.coverage_for(View(14_000_000, 14_200_000)) == Coverage(
        14_098_200, 14_101_800
    )


async def test_symmetric_passband_emits_main_crop_slice():
    from rigplane.audio.scope_source import AudioPassband as Passband

    facts = Passband(14_100_000, -1800, 1800, "both")
    tap = Tap()
    source = AudioFftSource(tap, lambda: facts, fft_size=4096, sample_rate=48000)
    frames = []
    source.on_frame(frames.append)
    await source.start()
    try:
        tone = (np.sin(2 * np.pi * 1500 * np.arange(4096) / 48000) * 16000).astype(
            "<i2"
        )
        tap.callback(tone.tobytes())
        frame = frames[-1]
        assert frame.coverage == Coverage(14_098_200, 14_101_800)
        assert len(frame.pixels) == 307
    finally:
        await source.stop()


async def test_full_spectrum_slice_matches_main_producer_pixels(
    virtual_clock, monkeypatch
):
    import rigplane.audio.fft_scope as fft_module
    from types import SimpleNamespace

    from rigplane.audio.fft_scope import AudioFftScope

    monkeypatch.setattr(fft_module, "time", SimpleNamespace(monotonic=virtual_clock))
    await virtual_clock.elapse(1)
    tuned = 14_100_000
    main = AudioFftScope(fft_size=2048, sample_rate=48000)
    main.set_center_freq(tuned)
    main_frames = []
    main.on_frame(main_frames.append)
    facts = AudioPassband(tuned, -24000, 24000, "both")
    tap = Tap()
    new = AudioFftSource(tap, lambda: facts, fft_size=2048, sample_rate=48000)
    new_frames = []
    new.on_frame(new_frames.append)
    await new.start()
    try:
        pcm = (np.sin(2 * np.pi * 1500 * np.arange(2048 * 6) / 48000) * 16000).astype(
            "<i2"
        )
        for window in range(6):
            chunk = pcm[window * 2048 : (window + 1) * 2048].tobytes()
            main.feed_audio(chunk)
            tap.callback(chunk)
            await virtual_clock.elapse(0.06)
        assert main_frames and new_frames
        assert (main_frames[-1].start_freq_hz, main_frames[-1].end_freq_hz) == (
            tuned - 24000,
            tuned + 24000,
        )
        assert new_frames[-1].pixels == main_frames[-1].pixels
        assert new_frames[-1].coverage == Coverage(tuned - 24000, tuned + 24000)
    finally:
        await new.stop()


async def test_ruled_slice_places_tone_at_main_offset(virtual_clock, monkeypatch):
    import rigplane.audio.fft_scope as fft_module
    from types import SimpleNamespace

    from rigplane.audio.fft_scope import AudioFftScope

    monkeypatch.setattr(fft_module, "time", SimpleNamespace(monotonic=virtual_clock))
    await virtual_clock.elapse(1)
    tuned = 14_100_000
    main = AudioFftScope(fft_size=4096, sample_rate=48000)
    main.set_center_freq(tuned)
    main.set_mode_bandwidth(3600)
    main_frames = []
    main.on_frame(main_frames.append)
    facts = AudioPassband(tuned, -1800, 1800, "both")
    tap = Tap()
    new = AudioFftSource(tap, lambda: facts, fft_size=4096, sample_rate=48000)
    new_frames = []
    new.on_frame(new_frames.append)
    await new.start()
    try:
        pcm = (np.sin(2 * np.pi * 1500 * np.arange(4096 * 6) / 48000) * 16000).astype(
            "<i2"
        )
        for window in range(6):
            chunk = pcm[window * 4096 : (window + 1) * 4096].tobytes()
            main.feed_audio(chunk)
            tap.callback(chunk)
            await virtual_clock.elapse(0.06)
        assert main_frames and new_frames
        assert (main_frames[-1].start_freq_hz, main_frames[-1].end_freq_hz) == (
            tuned - 1792,
            tuned + 1792,
        )
        assert new_frames[-1].coverage == Coverage(tuned - 1800, tuned + 1800)
        assert len(new_frames[-1].pixels) == len(main_frames[-1].pixels) == 307
        # The same tone saturates adjacent bins under the two level windows
        # (the new producer estimates over the full spectrum, main over the
        # crop), so the peak indices agree to one bin.
        new_peak = int(np.argmax(np.frombuffer(new_frames[-1].pixels, dtype=np.uint8)))
        main_peak = int(
            np.argmax(np.frombuffer(main_frames[-1].pixels, dtype=np.uint8))
        )
        assert abs(new_peak - main_peak) <= 1
    finally:
        await new.stop()


@pytest.mark.parametrize("old_count", [3072, 4096])
async def test_unavailable_facts_drop_pcm_and_recovery_cannot_mix_history(
    old_count, virtual_clock, monkeypatch, caplog
):
    from types import SimpleNamespace
    from unittest.mock import Mock
    import rigplane.audio.fft_scope as fft_module

    monkeypatch.setattr(fft_module, "time", SimpleNamespace(monotonic=virtual_clock))
    valid = AudioPassband(14_100_000, 300, 2700, "upper")
    available = True

    def facts():
        if not available:
            raise ValueError("RF mapping unavailable")
        return valid

    tap = Tap()
    source = AudioFftSource(tap, facts)
    frames = []
    source.on_frame(frames.append)
    await source.start()
    try:
        old = (np.sin(2 * np.pi * 1500 * np.arange(old_count) / 48000) * 16000).astype(
            "<i2"
        )
        tap.callback(old.tobytes())
        frames.clear()
        stop = Mock(wraps=source.producer.stop)
        monkeypatch.setattr(source.producer, "stop", stop)
        available = False
        for _ in range(3):
            tap.callback(bytes(1920))
        assert stop.call_count == 1
        assert not frames and source._frame_facts is None
        assert not caplog.records
        available = True
        noise = np.random.default_rng(31).integers(-100, 101, 4096, dtype=np.int16)
        await virtual_clock.elapse(0.1)
        tap.callback(noise[:1024].tobytes())
        assert not frames
        tap.callback(noise[1024:].tobytes())
        reference_tap = Tap()
        reference = AudioFftSource(reference_tap, lambda: valid)
        expected = []
        reference.on_frame(expected.append)
        await reference.start()
        try:
            reference_tap.callback(noise.tobytes())
            assert frames == expected and len(frames) == 1
        finally:
            await reference.stop()
    finally:
        await source.stop()


@pytest.mark.parametrize("sideband,lo,hi,width", [
    ("upper", 0, 2400, 103), ("lower", -2400, 0, 103),
    ("both", -1200, 1200, 103), ("both", -1800, 1800, 153),
])
async def test_real_pcm_hub_grants_only_emitted_crop_bins(sideband, lo, hi, width):
    from rigplane.scope.hub import ScopeHub
    from rigplane.scope.models import Subscription

    facts = AudioPassband(14_100_000, lo, hi, sideband)
    tap = Tap()
    source = AudioFftSource(tap, lambda: facts, fft_size=2048, sample_rate=48000)
    native, encoded = [], []
    source.on_frame(native.append)
    hub = ScopeHub(encoder=lambda frame: encoded.append(frame) or b"frame")
    hub.register("audio", source)
    view = View(facts.tuned_hz + lo, facts.tuned_hz + hi)
    sub = await hub.subscribe(Subscription("audio", None, view, 1024, 20))
    try:
        pcm = (16000 * np.sin(2 * np.pi * 700 * np.arange(2048) / 48000)).astype("<i2").tobytes()
        tap.callback(pcm)
        assert len(native[-1].pixels) == width
        assert encoded[-1].width == sub.granted.width == width
        assert len(encoded[-1].pixels) == width
        assert native[-1].native_width == source.capabilities().native_width == width
    finally:
        await sub.close()


async def test_real_pcm_crop_width_changes_update_existing_hub_client(virtual_clock, monkeypatch):
    import rigplane.audio.fft_scope as fft_module
    from rigplane.scope.hub import ScopeHub
    from rigplane.scope.models import Subscription

    virtual_clock.scope_to(monkeypatch, fft_module)
    await virtual_clock.elapse(1)
    facts = AudioPassband(14_100_000, 0, 2400, "upper")
    tap = Tap()
    source = AudioFftSource(tap, lambda: facts, fft_size=2048, sample_rate=48000)
    encoded = []
    hub = ScopeHub(encoder=lambda frame: encoded.append(frame) or b"frame")
    hub.register("audio", source)
    sub = await hub.subscribe(Subscription("audio", None, View(14_097_600, 14_102_400), 2800, 20))
    try:
        pcm = (16000 * np.sin(2 * np.pi * 700 * np.arange(2048) / 48000)).astype("<i2").tobytes()
        for lo, hi, sideband, width in [(0, 2400, "upper", 103), (-1200, 0, "lower", 52), (-1800, 1800, "both", 153)]:
            facts = AudioPassband(14_100_000, lo, hi, sideband)
            await virtual_clock.elapse(0.1)
            tap.callback(pcm)
            assert encoded[-1].width == sub.granted.width == width
            assert source.capabilities().native_width == width
            assert encoded[-1].pixels is not None and max(encoded[-1].pixels) >= 100
    finally:
        await sub.close()


@pytest.mark.parametrize("model", ["ic7300", "ftx1"])
@pytest.mark.parametrize("mode", ["AM", "FM", "WFM", "AM-N", "FM-N", "DATA-FM", "DATA-FM-N"])
async def test_symmetric_web_mode_real_pcm_reaches_both_rf_halves(model, mode):
    from rigplane.web.server import WebServer, WebConfig
    from rigplane.web.scope_sources import WebScopePcmTap
    from rigplane.scope.hub import ScopeHub
    from rigplane.scope.models import Subscription
    from test_audio_scope_dispatch import _seed_audio_facts

    server = WebServer(None, WebConfig(radio_model=model))
    _seed_audio_facts(server, mode=mode, frequency=14_100_000, width=2400)
    facts = WebScopePcmTap(server).facts
    tap = Tap()
    source = AudioFftSource(tap, facts, fft_size=2048, sample_rate=48000)
    encoded = []
    hub = ScopeHub(encoder=lambda frame: encoded.append(frame) or b"frame")
    hub.register("audio", source)
    sub = await hub.subscribe(Subscription("audio", None, View(14_098_800, 14_101_200), 1024, 20))
    try:
        pcm = (16000 * np.sin(2 * np.pi * 700 * np.arange(2048) / 48000)).astype("<i2").tobytes()
        tap.callback(pcm)
        frame = encoded[-1]
        assert facts().sideband == "both"
        assert (frame.coverage_start_hz, frame.coverage_end_hz) == (14_098_800, 14_101_200)
        assert max(frame.pixels[:len(frame.pixels)//2]) >= 100
        assert max(frame.pixels[len(frame.pixels)//2:]) >= 100
    finally:
        await sub.close()
