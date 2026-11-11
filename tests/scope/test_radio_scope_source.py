from pathlib import Path

import pytest

from rigplane.profiles.rig_loader import load_rig
from rigplane.runtime.radio_scope_source import RadioScopeSource, RadioScopeWriter
from rigplane.scope import ScopeFrame
from rigplane.scope.models import Coverage, FollowView, ScopeSourcePlugin, View


class Writer(RadioScopeWriter):
    # MOR-4392 round 2: no observed mode on this fake — the source
    # drives the radio as before.
    observed_scope_mode = None

    def __init__(self):
        self.writes = []
        self.frequency = 14_100_000

    def on_frame(self, frame):
        pass

    def tuned_frequency(self, receiver):
        return self.frequency

    async def set_output(self, enabled, *, release=True):
        self.writes.append(enabled)

    async def set_center(self, center_hz, span_hz, receiver):
        self.writes.append((center_hz, span_hz, receiver))


@pytest.mark.parametrize("model", ["ic7300", "ic705"])
async def test_off_vfo_view_keeps_acquisition_at_tuned_frequency(model):
    source_profile = load_rig(Path("rigs") / f"{model}.toml").to_profile()
    writer = Writer()
    source = RadioScopeSource(source_profile, writer)
    requested = View(14_200_000, 14_210_000)
    granted = await source.set_view(requested)
    assert granted.view == requested
    # The requested edge is 110 kHz off center: the smallest preset whose
    # ±coverage contains it is 250 kHz (Icom presets are ±half-widths).
    assert granted.coverage == Coverage(13_850_000, 14_350_000)
    assert writer.writes[-1] == (writer.frequency, 250000, 0)
    assert writer.frequency == 14_100_000


@pytest.mark.parametrize("model", ["ic7300", "ic705"])
async def test_profile_center_span_and_partial(model):
    profile = load_rig(Path("rigs") / f"{model}.toml").to_profile()
    writer = Writer()
    source = RadioScopeSource(profile, writer)
    plugin: ScopeSourcePlugin = source
    assert plugin.capabilities().view_modes == ("center",)
    # MOR-4385: choices are published as real VIEW widths — twice the
    # Icom preset, whose numbers are ±half-widths on the CI-V wire.
    assert plugin.capabilities().span_choices_hz == tuple(
        2 * preset for preset in profile.scope_span_presets_hz
    )
    view = View(14_090_000, 14_105_000)
    assert plugin.coverage_for(view) == Coverage(14_090_000, 14_110_000)
    await plugin.set_view(view)
    assert writer.writes[-1] == (14_100_000, 10000, 0)
    wide = View(13_000_000, 15_000_000)
    assert (await plugin.set_view(wide)).coverage == Coverage(13_600_000, 14_600_000)
    writer.frequency = 7_100_000
    assert (await plugin.set_view(FollowView("vfo", 5000))).view == View(
        7_097_500, 7_102_500
    )
    await plugin.start()
    await plugin.stop()
    assert writer.writes[-2:] == [True, False]


@pytest.mark.parametrize(
    "view_width,preset",
    [(100_000, 50_000), (200_000, 100_000), (500_000, 250_000)],
)
@pytest.mark.parametrize("model", ["ic705", "ic7300"])
async def test_icom_span_steps_grant_their_own_preset_coverage(
    model, view_width, preset
):
    """MOR-4385: stepping a span choice grants exactly that view width,
    sends the radio the matching preset (its own ±half-width), and the
    granted coverage equals the view — so frames at that preset cover
    the whole view and no native bins are cropped away.
    """
    profile = load_rig(Path("rigs") / f"{model}.toml").to_profile()
    writer = Writer()
    source = RadioScopeSource(profile, writer)
    granted = await source.set_view(FollowView("vfo", view_width))
    half = view_width / 2
    assert granted.view == View(14_100_000 - half, 14_100_000 + half)
    assert granted.coverage == Coverage(14_100_000 - half, 14_100_000 + half)
    assert writer.writes[-1] == (14_100_000, preset, 0)


_FULL_WIDTH_TOML = """
[scope]
span_presets_hz = [2500, 5000, 10000]
span_preset_semantics = "full_width"
"""


async def test_full_width_semantics_profile_grants_half_of_each_preset(tmp_path):
    """A profile whose presets are full display widths (no shipped radio
    declares this today) keeps the historical full-width behaviour:
    choices are the presets themselves and coverage is center ± preset/2.
    """
    from test_rig_loader import _MINIMAL_TOML, _write_toml

    path = _write_toml(tmp_path, _MINIMAL_TOML + _FULL_WIDTH_TOML)
    profile = load_rig(path).to_profile()
    writer = Writer()
    source = RadioScopeSource(profile, writer)
    assert source.capabilities().span_choices_hz == (2500, 5000, 10000)
    granted = await source.set_view(FollowView("vfo", 5000))
    assert granted.view == View(14_097_500, 14_102_500)
    assert granted.coverage == Coverage(14_097_500, 14_102_500)
    assert writer.writes[-1] == (14_100_000, 5000, 0)
    wide = await source.set_view(FollowView("vfo", 9000))
    assert wide.coverage == Coverage(14_095_000, 14_105_000)
    assert writer.writes[-1] == (14_100_000, 10000, 0)


async def test_native_frame_and_unsubscribe():
    source = RadioScopeSource(load_rig(Path("rigs/ic7300.toml")).to_profile(), Writer())
    frames = []
    unsubscribe = source.on_frame(frames.append)
    await source.start()
    source.feed_frame(ScopeFrame(0, 1, 14_010_000, 14_040_000, b"\x01\x02", False))
    assert frames[-1].coverage == Coverage(14_010_000, 14_040_000)
    assert frames[-1].pixels == b"\x01\x02"
    unsubscribe()
    source.feed_frame(ScopeFrame(0, 1, 14_010_000, 14_040_000, b"", True))
    assert len(frames) == 1
    await source.stop()


@pytest.mark.parametrize("model", ["ic705", "ic7300"])
async def test_granted_preset_frame_uses_every_native_bin(model):
    """MOR-4385: a frame at the granted preset's own coverage (the CI-V
    waveform contract: end - start == 2 x span) covers the whole granted
    view — every native bin lands inside the view, nothing is cropped or
    stretched by the projection.
    """
    from rigplane.scope.hub import ScopeHub
    from rigplane.scope.models import Subscription

    writer = Writer()
    source = RadioScopeSource(
        load_rig(Path("rigs") / f"{model}.toml").to_profile(), writer
    )
    encoded = []

    def encode(frame):
        encoded.append(frame)
        return b"frame"

    hub = ScopeHub(encoder=encode)
    hub.register("radio", source)
    sub = await hub.subscribe(
        Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
    )
    try:
        assert writer.writes[-1] == (14_100_000, 50_000, 0)
        source.feed_frame(ScopeFrame(0, 0, 14_050_000, 14_150_000, bytes(475), False))
        await sub.receive()
        frame = encoded[-1]
        assert (frame.view_start_hz, frame.view_end_hz) == (14_050_000, 14_150_000)
        assert (frame.coverage_start_hz, frame.coverage_end_hz) == (
            14_050_000,
            14_150_000,
        )
        assert frame.width == 475
        assert frame.pixels is not None and len(frame.pixels) == 475
    finally:
        await sub.close()


@pytest.mark.parametrize(
    "mode,first,last,expected",
    [
        (0, 14_100_000, 2500, Coverage(14_097_500, 14_102_500)),
        (1, 14_097_500, 14_102_500, Coverage(14_097_500, 14_102_500)),
    ],
)
async def test_real_assembler_oor_routes_through_hub(mode, first, last, expected):
    from rigplane.core.types import bcd_encode
    from rigplane.scope import ScopeAssembler
    from rigplane.scope.hub import ScopeHub
    from rigplane.scope.models import Subscription

    writer = Writer()
    source = RadioScopeSource(load_rig(Path("rigs/ic7300.toml")).to_profile(), writer)
    encoded = []

    def encode(frame):
        encoded.append(frame)
        return b"frame"

    hub = ScopeHub(encoder=encode)
    hub.register("radio", source)
    sub = await hub.subscribe(
        Subscription("radio", 0, View(14_095_000, 14_105_000), 8, 20)
    )
    try:
        payload = bytes([1, 1, mode]) + bcd_encode(first) + bcd_encode(last) + b"\x01"
        frame = ScopeAssembler().feed(payload, 0)
        assert frame is not None
        source.feed_frame(frame)
        assert await sub.receive() == b"frame"
        assert encoded[-1].receiver == 0
        assert encoded[-1].out_of_range
        assert encoded[-1].pixels is None
        assert hub.health("radio", 0).coverage == expected
    finally:
        await sub.close()
    assert writer.writes[-1] is False


async def test_failed_output_off_can_be_retried():
    class FlakyWriter(Writer):
        async def set_output(self, enabled, *, release=True):
            self.writes.append(enabled)
            if not enabled and self.writes.count(False) == 1:
                raise RuntimeError("output-off failed")

    writer = FlakyWriter()
    source = RadioScopeSource(load_rig(Path("rigs/ic7300.toml")).to_profile(), writer)
    await source.start()
    with pytest.raises(RuntimeError, match="output-off failed"):
        await source.stop()
    await source.stop()
    assert writer.writes == [True, False, False]


async def test_failed_start_and_failed_rollback_preserve_output_off_retry():
    class AppliedWriter(Writer):
        def __init__(self):
            super().__init__()
            self.output = False

        async def set_output(self, enabled, *, release=True):
            self.writes.append(enabled)
            if enabled:
                self.output = True
                raise RuntimeError("enable reply lost")
            if self.writes.count(False) == 1:
                raise RuntimeError("disable reply lost")
            self.output = False

    writer = AppliedWriter()
    source = RadioScopeSource(load_rig(Path("rigs/ic7300.toml")).to_profile(), writer)
    with pytest.raises(RuntimeError, match="disable reply lost"):
        await source.start()
    await source.stop()
    assert writer.writes == [True, False, False]
    assert not writer.output


@pytest.mark.parametrize("model,width", [("ic7300", 475), ("ic7610", 689)])
async def test_native_width_is_observed_from_frames(model, width):
    source = RadioScopeSource(
        load_rig(Path("rigs") / f"{model}.toml").to_profile(), Writer()
    )
    assert source.capabilities().native_width_for(0) is None
    await source.start()
    source.feed_frame(
        ScopeFrame(0, 0, 14_090_000, 14_110_000, bytes([160]) * width, False)
    )
    assert source.capabilities().native_width_for(0) == width
    source.feed_frame(ScopeFrame(0, 1, 14_090_000, 14_110_000, b"", True))
    assert source.capabilities().native_width_for(0) == width
    source.feed_frame(
        ScopeFrame(0, 0, 14_090_000, 14_110_000, bytes([160]) * (width + 11), False)
    )
    assert source.capabilities().native_width_for(0) == width + 11
    await source.stop()


async def test_fixed_edges_are_negotiated_from_profile_data():
    class FixedWriter(Writer):
        async def set_fixed(self, fixed_edge, receiver):
            self.writes.append((fixed_edge, receiver))

    from rigplane.core.types import ScopeFixedEdge

    profile = load_rig(Path("rigs/ic7610.toml")).to_profile()
    writer = FixedWriter()
    source = RadioScopeSource(profile, writer)
    request = View(14_050_000, 14_150_000)
    grant = await source.set_view(request, 0)
    assert "fixed" in source.capabilities().view_modes
    assert grant.coverage == Coverage(request.start_hz, request.end_hz)
    assert writer.writes[-1] == (ScopeFixedEdge(6, 1, 14_050_000, 14_150_000), 0)
    # The 11 MHz range boundary crosses two profile entries.
    request = View(10_950_000, 11_050_000)
    await source.set_view(request, 0)
    assert isinstance(writer.writes[-1][0], float)


async def test_fixed_edge_quantization_is_profile_data():
    class FixedWriter(Writer):
        async def set_fixed(self, fixed_edge, receiver):
            self.writes.append((fixed_edge, receiver))

    from rigplane.core.types import ScopeFixedEdge

    writer = FixedWriter()
    source = RadioScopeSource(load_rig(Path("rigs/ic7610.toml")).to_profile(), writer)
    request = View(14_050_123, 14_150_567)
    grant = await source.set_view(request, 0)
    assert writer.writes[-1] == (ScopeFixedEdge(6, 1, 14_050_000, 14_151_000), 0)
    assert grant.coverage == Coverage(14_050_000, 14_151_000)


async def test_fixed_views_on_two_receivers_use_distinct_profile_slots():
    class FixedWriter(Writer):
        async def set_fixed(self, edges, receiver):
            self.writes.append((edges, receiver))

    writer = FixedWriter()
    source = RadioScopeSource(load_rig(Path("rigs/ic7610.toml")).to_profile(), writer)
    await source.set_view(View(14_050_000, 14_150_000), 0)
    await source.set_view(View(14_150_000, 14_250_000), 1)
    assert [(value.edge, receiver) for value, receiver in writer.writes] == [
        (1, 0),
        (2, 1),
    ]


async def test_off_band_fixed_request_uses_center_uncovered_fallback():
    class FixedWriter(Writer):
        async def set_fixed(self, edges, receiver):
            self.writes.append((edges, receiver))

    writer = FixedWriter()
    source = RadioScopeSource(load_rig(Path("rigs/ic7610.toml")).to_profile(), writer)
    grant = await source.set_view(View(7_050_000, 7_150_000), 0)
    assert grant.coverage == Coverage(13_600_000, 14_600_000)
    assert writer.writes[-1] == (14_100_000, 500000, 0)


class _SimRadio:
    """In-test stand-in for the radio behind a ScopeSession.

    MOR-4385 B2: tests mutate ``span_index``/``frequency``/``fixed``
    directly — never through Core — to model a front-panel change,
    another controller, or another Core on the same radio.
    """

    def __init__(self, profile, *, start_span_hz=250_000):
        self.presets = profile.scope_span_presets_hz
        self.span_index = self.presets.index(start_span_hz)
        self.frequency = 14_100_000
        self.mode = 0
        self.fixed_edges = (14_200_000, 14_300_000)
        self.writes: list = []

    async def read(self):
        from rigplane.runtime.scope_session import ScopeState

        return ScopeState(False, True, self.mode, self.span_index, 1)

    async def write(self, name, value, receiver):
        self.writes.append((name, value, receiver))
        if name == "span":
            self.span_index = value
        elif name == "mode":
            self.mode = value
        elif name == "fixed_edge":
            self.fixed_edges = (value.start_hz, value.end_hz)

    async def read_fixed(self, range_index, edge):
        from rigplane.core.types import ScopeFixedEdge

        start_hz, end_hz = self.fixed_edges
        return ScopeFixedEdge(range_index, edge, start_hz, end_hz)

    def tuned(self, receiver):
        return float(self.frequency)


def _sim_session(sim, profile):
    from rigplane.runtime.scope_session import ScopeSession

    return ScopeSession(
        sim.read,
        sim.write,
        sim.read_fixed,
        None,
        sim.tuned,
        spans=profile.scope_span_presets_hz,
        ready=True,
    )


def _sim_hub(source):
    from rigplane.scope.hub import ScopeHub
    from rigplane.scope.models import Subscription as _Subscription

    encoded = []

    def encode(frame):
        encoded.append(frame)
        return b"frame"

    hub = ScopeHub(encoder=encode)
    hub.register("radio", source)
    return hub, encoded, _Subscription


@pytest.mark.parametrize("model", ["ic705", "ic7300"])
async def test_external_span_change_is_adopted_without_writes(model):
    """MOR-4385 B2: another controller moves the radio ±50k → ±100k.

    The SPAN label shows the radio's ±100k value; MOR-4392 keeps the
    granted view on our own ±50k requested window while the coverage
    stays the truthful native ±100k — and Core writes nothing back
    (no loop).
    """
    profile = load_rig(Path("rigs") / f"{model}.toml").to_profile()
    sim = _SimRadio(profile)
    session = _sim_session(sim, profile)
    source = RadioScopeSource(profile, session)
    hub, encoded, Subscription = _sim_hub(source)
    sub = await hub.subscribe(
        Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
    )
    try:
        assert sim.writes
        source.feed_frame(ScopeFrame(0, 0, 14_050_000, 14_150_000, bytes(475), False))
        await sub.receive()
        first = encoded[-1]
        assert (first.view_start_hz, first.view_end_hz) == (14_050_000, 14_150_000)
        assert (first.coverage_start_hz, first.coverage_end_hz) == (
            14_050_000,
            14_150_000,
        )
        sim.writes.clear()
        new_index = profile.scope_span_presets_hz.index(100_000)
        sim.span_index = new_index
        session.observe("span", new_index, 0)
        source.feed_frame(ScopeFrame(0, 0, 14_000_000, 14_200_000, bytes(475), False))
        await sub.receive()
        adopted = encoded[-1]
        assert (adopted.view_start_hz, adopted.view_end_hz) == (14_050_000, 14_150_000)
        assert (adopted.coverage_start_hz, adopted.coverage_end_hz) == (
            14_050_000,
            14_150_000,
        )
        assert sub.granted.view == View(14_050_000, 14_150_000)
        assert sub.granted.coverage == Coverage(14_000_000, 14_200_000)
        await session.reconcile()
        assert sim.writes == []
    finally:
        await sub.close()


@pytest.mark.parametrize("model", ["ic705", "ic7300"])
async def test_external_centre_change_is_adopted_without_writes(model):
    """MOR-4385 B2: the radio retunes behind our back while Core's VFO
    reading is still stale — the granted view still follows the frames
    the radio actually streams, with no writes back.
    """
    profile = load_rig(Path("rigs") / f"{model}.toml").to_profile()
    sim = _SimRadio(profile)
    session = _sim_session(sim, profile)
    source = RadioScopeSource(profile, session)
    hub, encoded, Subscription = _sim_hub(source)
    sub = await hub.subscribe(
        Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
    )
    try:
        source.feed_frame(ScopeFrame(0, 0, 14_050_000, 14_150_000, bytes(475), False))
        await sub.receive()
        sim.writes.clear()
        source.feed_frame(ScopeFrame(0, 0, 14_150_000, 14_250_000, bytes(475), False))
        await sub.receive()
        adopted = encoded[-1]
        assert (adopted.view_start_hz, adopted.view_end_hz) == (14_150_000, 14_250_000)
        assert (adopted.coverage_start_hz, adopted.coverage_end_hz) == (
            14_150_000,
            14_250_000,
        )
        await session.reconcile()
        assert sim.writes == []
    finally:
        await sub.close()


async def test_external_vfo_change_is_adopted_without_writes():
    """MOR-4385 B2: the radio VFO moves and Core's reading caught up —
    the granted view follows the new centre (here the ±50k requested
    window coincides with the native coverage) and Core writes
    nothing (MOR-4392 keeps the coverage truthful).
    """
    profile = load_rig(Path("rigs/ic705.toml")).to_profile()
    sim = _SimRadio(profile)
    session = _sim_session(sim, profile)
    source = RadioScopeSource(profile, session)
    hub, encoded, Subscription = _sim_hub(source)
    sub = await hub.subscribe(
        Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
    )
    try:
        source.feed_frame(ScopeFrame(0, 0, 14_050_000, 14_150_000, bytes(475), False))
        await sub.receive()
        sim.writes.clear()
        sim.frequency = 14_200_000
        source.feed_frame(ScopeFrame(0, 0, 14_150_000, 14_250_000, bytes(475), False))
        await sub.receive()
        adopted = encoded[-1]
        assert (adopted.view_start_hz, adopted.view_end_hz) == (14_150_000, 14_250_000)
        assert (adopted.coverage_start_hz, adopted.coverage_end_hz) == (
            14_150_000,
            14_250_000,
        )
        assert sub.granted.view == View(14_150_000, 14_250_000)
        assert sub.granted.coverage == Coverage(14_150_000, 14_250_000)
        await session.reconcile()
        assert sim.writes == []
    finally:
        await sub.close()


async def test_external_fixed_change_is_adopted_without_writes():
    """MOR-4385 B2: the radio's fixed edges change behind our back — the
    grant keeps the user's crop, covers it with no hatch, and Core
    writes nothing back.
    """
    from rigplane.core.types import ScopeFixedEdge

    profile = load_rig(Path("rigs/ic7610.toml")).to_profile()
    sim = _SimRadio(profile)
    session = _sim_session(sim, profile)
    source = RadioScopeSource(profile, session)
    hub, encoded, Subscription = _sim_hub(source)
    sub = await hub.subscribe(
        Subscription("radio", 0, View(14_180_000, 14_200_000), 475, 20)
    )
    try:
        assert ("mode", 1, 0) in sim.writes
        source.feed_frame(ScopeFrame(0, 1, 14_180_000, 14_200_000, bytes(475), False))
        await sub.receive()
        sim.writes.clear()
        new_edges = ScopeFixedEdge(6, 1, 14_180_000, 14_320_000)
        sim.fixed_edges = (new_edges.start_hz, new_edges.end_hz)
        session.observe("fixed_edge", new_edges, 0)
        source.feed_frame(ScopeFrame(0, 1, 14_180_000, 14_320_000, bytes(475), False))
        await sub.receive()
        adopted = encoded[-1]
        assert (adopted.view_start_hz, adopted.view_end_hz) == (14_180_000, 14_200_000)
        assert (adopted.coverage_start_hz, adopted.coverage_end_hz) == (
            14_180_000,
            14_200_000,
        )
        await session.reconcile()
        assert sim.writes == []
    finally:
        await sub.close()


async def test_observed_external_span_is_adopted_never_reasserted():
    """MOR-4385 B2: a radio-reported span differing from our last write
    becomes the desired state — reconcile never writes our stale span.
    """
    profile = load_rig(Path("rigs/ic705.toml")).to_profile()
    sim = _SimRadio(profile, start_span_hz=100_000)
    session = _sim_session(sim, profile)
    await session.set_output(True)
    await session.set_center(14_100_000, 50_000, 0)
    assert ("span", profile.scope_span_presets_hz.index(50_000), 0) in sim.writes
    sim.writes.clear()
    new_index = profile.scope_span_presets_hz.index(100_000)
    sim.span_index = new_index
    session.observe("span", new_index, 0)
    await session.reconcile()
    assert sim.writes == []
    assert session.observed is not None and session.observed.span == new_index


@pytest.mark.parametrize("model", ["ic705", "ic7300"])
async def test_follow_grant_is_requested_window_cropped_to_native(model):
    """MOR-4392: a ±25k follow request inside ±100k native coverage
    grants the ±25k window centred on the tuned frequency — never the
    native coverage itself. The coverage stays the truthful native
    ±100k and the encoded frame covers the requested window.
    """
    profile = load_rig(Path("rigs") / f"{model}.toml").to_profile()
    sim = _SimRadio(profile)
    session = _sim_session(sim, profile)
    source = RadioScopeSource(profile, session)
    hub, encoded, Subscription = _sim_hub(source)
    sub = await hub.subscribe(
        Subscription("radio", 0, FollowView("vfo", 50_000), 475, 20)
    )
    try:
        source.feed_frame(ScopeFrame(0, 0, 14_000_000, 14_200_000, bytes(475), False))
        await sub.receive()
        frame = encoded[-1]
        assert (frame.view_start_hz, frame.view_end_hz) == (14_075_000, 14_125_000)
        assert (frame.coverage_start_hz, frame.coverage_end_hz) == (
            14_075_000,
            14_125_000,
        )
        assert frame.width == 475
        assert frame.pixels is not None and len(frame.pixels) == 475
        assert sub.granted.view == View(14_075_000, 14_125_000)
        assert sub.granted.coverage == Coverage(14_000_000, 14_200_000)
    finally:
        await sub.close()


@pytest.mark.parametrize("model", ["ic705", "ic7300"])
async def test_external_narrowing_clips_follow_view_without_writes(model):
    """MOR-4392: the radio narrows behind our back (±100k → ±10k) —
    the follow grant becomes the clipped intersection and Core writes
    nothing back (MOR-4385 adoption, no loop).
    """
    profile = load_rig(Path("rigs") / f"{model}.toml").to_profile()
    sim = _SimRadio(profile)
    session = _sim_session(sim, profile)
    source = RadioScopeSource(profile, session)
    hub, encoded, Subscription = _sim_hub(source)
    sub = await hub.subscribe(
        Subscription("radio", 0, FollowView("vfo", 50_000), 475, 20)
    )
    try:
        source.feed_frame(ScopeFrame(0, 0, 14_000_000, 14_200_000, bytes(475), False))
        await sub.receive()
        assert sub.granted.view == View(14_075_000, 14_125_000)
        sim.writes.clear()
        narrow_index = profile.scope_span_presets_hz.index(10_000)
        sim.span_index = narrow_index
        session.observe("span", narrow_index, 0)
        source.feed_frame(ScopeFrame(0, 0, 14_090_000, 14_110_000, bytes(475), False))
        await sub.receive()
        narrowed = encoded[-1]
        assert (narrowed.view_start_hz, narrowed.view_end_hz) == (
            14_090_000,
            14_110_000,
        )
        assert (narrowed.coverage_start_hz, narrowed.coverage_end_hz) == (
            14_090_000,
            14_110_000,
        )
        assert sub.granted.view == View(14_090_000, 14_110_000)
        assert sub.granted.coverage == Coverage(14_090_000, 14_110_000)
        await session.reconcile()
        assert sim.writes == []
    finally:
        await sub.close()


@pytest.mark.parametrize("model", ["ic7610", "ic7300"])
async def test_follow_span_gesture_in_observed_fix_writes_nothing(model):
    """MOR-4392 round 2: the radio is in FIX (observed mode 1) — a
    follow zoom/span gesture is a client crop (MOR-4387), never a mode
    change. Zero 0x14/0x15 writes; the grant keeps the requested window
    and the hub re-resolves it per frame against the live fixed window.
    """
    profile = load_rig(Path("rigs") / f"{model}.toml").to_profile()
    sim = _SimRadio(profile)
    session = _sim_session(sim, profile)
    source = RadioScopeSource(profile, session)
    hub, encoded, Subscription = _sim_hub(source)
    sub = await hub.subscribe(
        Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
    )
    try:
        assert sim.writes
        source.feed_frame(ScopeFrame(0, 0, 14_050_000, 14_150_000, bytes(475), False))
        await sub.receive()
        # External FIX entry (front panel): adopted, nothing written back.
        sim.writes.clear()
        sim.mode = 1
        session.observe("mode", 1, 0)
        await session.reconcile()
        assert sim.writes == []
        assert session.observed is not None and session.observed.mode == 1
        # A follow span gesture inside FIX writes nothing at all.
        grant = await source.set_view(FollowView("vfo", 50_000), 0)
        assert grant.view == View(14_075_000, 14_125_000)
        assert grant.coverage is None
        assert sim.writes == []
        # ...and a hub resubscribe (the real gesture path) is equally quiet.
        gesture = await hub.subscribe(
            Subscription("radio", 0, FollowView("vfo", 50_000), 475, 20)
        )
        try:
            assert sim.writes == []
            assert gesture.granted.view == View(14_075_000, 14_125_000)
            assert gesture.granted.coverage is None
        finally:
            await gesture.close()
    finally:
        await sub.close()


async def test_observed_external_fix_mode_is_adopted_never_reasserted():
    """MOR-4392 round 2: a radio-reported FIX mode differing from our
    last CENTER write becomes the desired state — reconcile never writes
    our stale mode back (mirrors the MOR-4385 span adoption test).
    """
    profile = load_rig(Path("rigs/ic705.toml")).to_profile()
    sim = _SimRadio(profile)
    session = _sim_session(sim, profile)
    await session.set_output(True)
    await session.set_center(14_100_000, 50_000, 0)
    assert ("span", profile.scope_span_presets_hz.index(50_000), 0) in sim.writes
    sim.writes.clear()
    sim.mode = 1
    session.observe("mode", 1, 0)
    await session.reconcile()
    assert sim.writes == []
    assert session.observed is not None and session.observed.mode == 1


@pytest.mark.parametrize("model", ["ic7610", "ic7300"])
async def test_fix_transitions_reresolve_follow_grant_per_frame(model):
    """MOR-4392 round 2: CTR→FIX→CTR re-resolves the follow grant per
    frame — the request clipped to the fixed window in FIX, to the
    native coverage in CENTER — with zero radio writes after the initial
    CENTER subscribe and no resubscribe for the grant to follow.
    """
    profile = load_rig(Path("rigs") / f"{model}.toml").to_profile()
    sim = _SimRadio(profile)
    session = _sim_session(sim, profile)
    source = RadioScopeSource(profile, session)
    hub, encoded, Subscription = _sim_hub(source)
    sub = await hub.subscribe(
        Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
    )
    try:
        source.feed_frame(ScopeFrame(0, 0, 14_000_000, 14_200_000, bytes(475), False))
        await sub.receive()
        assert sub.granted.view == View(14_050_000, 14_150_000)
        assert sub.granted.coverage == Coverage(14_000_000, 14_200_000)
        sim.writes.clear()
        # CTR → FIX behind our back: adopt, then stream the fixed window.
        sim.mode = 1
        session.observe("mode", 1, 0)
        source.feed_frame(ScopeFrame(0, 1, 14_000_000, 14_100_000, bytes(475), False))
        await sub.receive()
        fixed = encoded[-1]
        assert (fixed.view_start_hz, fixed.view_end_hz) == (14_050_000, 14_100_000)
        assert (fixed.coverage_start_hz, fixed.coverage_end_hz) == (
            14_050_000,
            14_100_000,
        )
        assert sub.granted.view == View(14_050_000, 14_100_000)
        assert sub.granted.coverage == Coverage(14_000_000, 14_100_000)
        # A follow span gesture inside FIX writes nothing (RED pre-fix:
        # set_view drives set_center and writes 0x14 mode 0).
        gesture = await hub.subscribe(
            Subscription("radio", 0, FollowView("vfo", 50_000), 475, 20)
        )
        try:
            assert sim.writes == []
        finally:
            await gesture.close()
        # FIX → CTR behind our back: the same subscription follows.
        sim.mode = 0
        session.observe("mode", 0, 0)
        source.feed_frame(ScopeFrame(0, 0, 14_000_000, 14_200_000, bytes(475), False))
        await sub.receive()
        back = encoded[-1]
        assert (back.view_start_hz, back.view_end_hz) == (14_050_000, 14_150_000)
        assert (back.coverage_start_hz, back.coverage_end_hz) == (
            14_050_000,
            14_150_000,
        )
        assert sub.granted.view == View(14_050_000, 14_150_000)
        assert sub.granted.coverage == Coverage(14_000_000, 14_200_000)
        await session.reconcile()
        assert sim.writes == []
    finally:
        await sub.close()


async def test_scope_region_recaptures_within_frames_after_mode_changes():
    """MOR-4392 round 2 (MOR-4345 pattern): after CTR→FIX→CTR the
    published scope region is current again within 3 frames each way —
    view clipped to the fixed window in FIX, to native in CENTER.
    """
    profile = load_rig(Path("rigs/ic7610.toml")).to_profile()
    sim = _SimRadio(profile)
    sim.frequency = 14_250_000
    session = _sim_session(sim, profile)
    source = RadioScopeSource(profile, session)
    hub, _, Subscription = _sim_hub(source)
    sub = await hub.subscribe(
        Subscription("radio", 0, FollowView("vfo", 100_000), 475, 20)
    )
    try:
        fixed_native = Coverage(14_200_000, 14_300_000)
        center_native = Coverage(14_150_000, 14_350_000)

        async def recaptured(
            view: View, coverage: Coverage, natives: list[Coverage], mode: int
        ) -> bool:
            for native in natives[:3]:
                source.feed_frame(
                    ScopeFrame(
                        0, mode, native.start_hz, native.end_hz, bytes(475), False
                    )
                )
                await sub.receive()
                if sub.granted.view == view and sub.granted.coverage == coverage:
                    return True
            return False

        source.feed_frame(ScopeFrame(0, 0, 14_150_000, 14_350_000, bytes(475), False))
        await sub.receive()
        assert sub.granted.view == View(14_200_000, 14_300_000)
        sim.writes.clear()
        sim.mode = 1
        session.observe("mode", 1, 0)
        assert await recaptured(
            View(14_200_000, 14_300_000),
            fixed_native,
            [fixed_native] * 3,
            1,
        )
        sim.mode = 0
        session.observe("mode", 0, 0)
        assert await recaptured(
            View(14_200_000, 14_300_000),
            center_native,
            [center_native] * 3,
            0,
        )
        await session.reconcile()
        assert sim.writes == []
    finally:
        await sub.close()


@pytest.mark.parametrize("model", ["ic705", "ic7300"])
async def test_follow_resubscribe_writes_only_on_preset_change(model):
    """MOR-4392: re-subscribing with a new follow spanHz writes the
    radio only when ``_center_span`` picks a different preset — the
    existing ScopeSession suppression, no new cache.

    IC-705/7300 presets are ±half-widths, so 500k and 250k both need
    preset 250000 (no write on that step) while 200k/100k/50k each
    step down to 100000/50000/25000 (one span write each).
    """
    profile = load_rig(Path("rigs") / f"{model}.toml").to_profile()
    sim = _SimRadio(profile, start_span_hz=500_000)
    session = _sim_session(sim, profile)
    source = RadioScopeSource(profile, session)
    hub, _, Subscription = _sim_hub(source)
    subs = []
    try:
        deltas = []
        for span_hz in (500_000, 250_000, 200_000, 100_000, 50_000):
            before = [w for w in sim.writes if w[0] == "span"]
            subs.append(
                await hub.subscribe(
                    Subscription("radio", 0, FollowView("vfo", span_hz), 475, 20)
                )
            )
            after = [w for w in sim.writes if w[0] == "span"]
            deltas.append(len(after) - len(before))
        assert deltas == [1, 0, 1, 1, 1]
    finally:
        for sub in subs:
            await sub.close()
