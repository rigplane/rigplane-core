from pathlib import Path

import pytest

from rigplane.profiles.rig_loader import load_rig
from rigplane.runtime.radio_scope_source import RadioScopeSource, RadioScopeWriter
from rigplane.scope import ScopeFrame
from rigplane.scope.models import Coverage, FollowView, ScopeSourcePlugin, View


class Writer(RadioScopeWriter):
    def __init__(self):
        self.writes = []
        self.frequency = 14_100_000

    def tuned_frequency(self, receiver):
        return self.frequency

    async def set_output(self, enabled):
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
    assert granted.coverage == Coverage(13_975_000, 14_225_000)
    assert writer.writes[-1] == (writer.frequency, 250000, 0)
    assert writer.frequency == 14_100_000


@pytest.mark.parametrize("model", ["ic7300", "ic705"])
async def test_profile_center_span_and_partial(model):
    profile = load_rig(Path("rigs") / f"{model}.toml").to_profile()
    writer = Writer()
    source = RadioScopeSource(profile, writer)
    plugin: ScopeSourcePlugin = source
    assert plugin.capabilities().view_modes == ("center",)
    assert plugin.capabilities().span_choices_hz == profile.scope_span_presets_hz
    view = View(14_090_000, 14_105_000)
    assert plugin.coverage_for(view) == Coverage(14_087_500, 14_112_500)
    await plugin.set_view(view)
    assert writer.writes[-1] == (14_100_000, 25000, 0)
    wide = View(13_000_000, 15_000_000)
    assert (await plugin.set_view(wide)).coverage == Coverage(13_850_000, 14_350_000)
    writer.frequency = 7_100_000
    assert (await plugin.set_view(FollowView("vfo", 5000))).view == View(
        7_097_500, 7_102_500
    )
    await plugin.start()
    await plugin.stop()
    assert writer.writes[-2:] == [True, False]


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
        async def set_output(self, enabled):
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

        async def set_output(self, enabled):
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
