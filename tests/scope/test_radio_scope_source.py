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


@pytest.mark.parametrize('model', ['ic7300', 'ic705'])
async def test_profile_center_span_and_partial(model):
    profile = load_rig(Path('rigs') / f'{model}.toml').to_profile()
    writer = Writer()
    source = RadioScopeSource(profile, writer)
    plugin: ScopeSourcePlugin = source
    assert plugin.capabilities().view_modes == ('center',)
    assert plugin.capabilities().span_choices_hz == profile.scope_span_presets_hz
    view = View(14_090_000, 14_105_000)
    assert plugin.coverage_for(view) == Coverage(14_085_000, 14_110_000)
    await plugin.set_view(view)
    assert writer.writes[-1] == (14_097_500, 25000, None)
    wide = View(13_000_000, 15_000_000)
    assert (await plugin.set_view(wide)).coverage == Coverage(13_750_000, 14_250_000)
    writer.frequency = 7_100_000
    assert (await plugin.set_view(FollowView('vfo', 5000))).view == View(7_097_500, 7_102_500)
    await plugin.start()
    await plugin.stop()
    assert writer.writes[-2:] == [True, False]


async def test_native_frame_and_unsubscribe():
    source = RadioScopeSource(load_rig(Path('rigs/ic7300.toml')).to_profile(), Writer())
    frames = []
    unsubscribe = source.on_frame(frames.append)
    await source.start()
    source.feed_frame(ScopeFrame(0, 1, 14_010_000, 14_040_000, b'\x01\x02', False))
    assert frames[-1].coverage == Coverage(14_010_000, 14_040_000)
    assert frames[-1].pixels == b'\x01\x02'
    unsubscribe()
    source.feed_frame(ScopeFrame(0, 1, 14_010_000, 14_040_000, b'', True))
    assert len(frames) == 1
    await source.stop()
