"""The hub IQ source composes existing RF-follow and TX policy."""

import asyncio

import pytest

from rigplane.core.tx_observation import OBSERVED_PTT_PATH
from rigplane.core.tx_observation import ObservedPtt
from rigplane.scope.models import FollowView, Subscription, View
from rigplane.sdr.fake import FakeIqSource
from rigplane.sdr.types import SdrConfig
from rigplane.testing.timing import await_until
from rigplane.web.server import WebConfig, WebServer
from test_audio_scope_dispatch import _seed_audio_facts
from test_web_server_coverage import _store_observation


def make_server(provider, *, invert=False):
    server = WebServer(None, WebConfig(
        radio_model='IC-7610',
        sdr_config=SdrConfig(device_args='driver=fake', sample_rate_hz=48_000,
                             span_hz=12_000, freq_offset_hz=1000, invert_spectrum=invert),
        sdr_source_factory=lambda config: provider,
    ))
    _seed_audio_facts(server, frequency=14_100_000)
    return server


async def test_iq_seed_precedes_open_and_updates_without_control_clients(virtual_clock, monkeypatch):
    provider = FakeIqSource(center_freq_hz=0, sample_rate_hz=48_000)
    server = make_server(provider)
    server.command_state_store.apply_current(_store_observation(OBSERVED_PTT_PATH, ObservedPtt.ON, at=virtual_clock()))
    opened = []
    original_open = provider.open

    def open_provider():
        opened.append((provider.center_freq_hz, server._iq_scope_source.tx_frozen))
        original_open()

    monkeypatch.setattr(provider, 'open', open_provider)
    subscription = await server.scope_hub.subscribe(Subscription('iq_fft', None, View(14_090_000, 14_110_000), 1024, 20))
    try:
        assert opened and opened[0][0] > 0
        assert opened[0][1] is True
        source = server._iq_scope_source
        assert source.active and source.tx_frozen
        target = source.resolve_view(FollowView('vfo', 20_000))
        assert (target.start_hz + target.end_hz) / 2 == 14_101_000
        assert provider.center_freq_hz != 14_101_000
        assert server._sdr_status_payload()['txFrozen'] is True
        server.command_state_store.apply_current(_store_observation(OBSERVED_PTT_PATH, ObservedPtt.OFF, at=virtual_clock()))
        server._broadcast_state_update()
        assert not server._control_event_queues
        assert source.tx_frozen
        await virtual_clock.elapse(.31)
        assert not source.tx_frozen
        _seed_audio_facts(server, frequency=14_101_000)
        server._last_state_broadcast = asyncio.get_running_loop().time()
        server._broadcast_state_update()
        shifted = source.resolve_view(FollowView('vfo', 20_000))
        assert (shifted.start_hz + shifted.end_hz) / 2 == 14_102_000
    finally:
        await subscription.close()
    assert not provider._is_open and not source.active
    assert source._timer is None


@pytest.mark.parametrize('invert', [False, True])
async def test_iq_full_capture_follow_rf_and_configured_inversion(invert):
    provider = FakeIqSource(center_freq_hz=0, sample_rate_hz=48_000,
                            tones=((6000, -6),), noise_floor_dbfs=-100)
    server = make_server(provider, invert=invert)
    server.scope_hub
    source = server._iq_scope_source
    assert source is not None
    frames = []
    detach = source.on_frame(frames.append)
    subscription = await server.scope_hub.subscribe(Subscription('iq_fft', None, FollowView('vfo', 20_000), 1024, 20))
    try:
        assert provider.center_freq_hz > 0
        assert (subscription.granted.view.start_hz + subscription.granted.view.end_hz) / 2 == 14_101_000
        provider.pump(8)
        await await_until(lambda: any(frame.pixels for frame in frames), timeout=2)
        frame = next(frame for frame in frames if frame.pixels)
        assert frame.coverage.end_hz - frame.coverage.start_hz == 48_000
        peak = max(range(len(frame.pixels)), key=frame.pixels.__getitem__)
        peak_hz = frame.coverage.start_hz + (peak + .5) * 48_000 / len(frame.pixels)
        expected = provider.center_freq_hz + (-6000 if invert else 6000)
        assert abs(peak_hz - expected) < 300
    finally:
        detach()
        await subscription.close()
    assert not provider._is_open
