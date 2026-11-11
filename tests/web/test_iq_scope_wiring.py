"""The hub IQ source composes existing RF-follow and TX policy."""

import asyncio
from dataclasses import dataclass

import pytest

from rigplane.core.tx_observation import OBSERVED_PTT_PATH
from rigplane.core.tx_observation import ObservedPtt
from rigplane.scope.models import FollowView, Subscription, View
from rigplane.sdr import runtime as sdr_runtime
from rigplane.sdr.fake import FakeIqSource
from rigplane.sdr.runtime import register_source_kind
from rigplane.sdr.types import SdrConfig, SoapySourceSpec
from rigplane.testing.timing import await_until
from rigplane.web.server import WebConfig, WebServer
from test_audio_scope_dispatch import _seed_audio_facts
from test_web_server_coverage import _store_observation


@dataclass(frozen=True)
class _RegisteredSourceSpec:
    kind: str = "test-web"


def test_web_config_derives_soapy_source_spec() -> None:
    config = SdrConfig(device_args="driver=fake")

    assert WebConfig(sdr_config=config).iq_source == SoapySourceSpec(config)


def test_web_config_rejects_mismatched_soapy_source_config() -> None:
    with pytest.raises(ValueError, match="iq_source"):
        WebConfig(
            sdr_config=SdrConfig(device_args="driver=one"),
            iq_source=SoapySourceSpec(SdrConfig(device_args="driver=two")),
        )


async def test_registered_source_kind_builds_iq_scope_provider() -> None:
    provider = FakeIqSource(sample_rate_hz=48_000)
    spec = _RegisteredSourceSpec()
    register_source_kind(spec.kind, lambda received: provider)
    try:
        server = WebServer(
            None,
            WebConfig(
                radio_model="IC-7610",
                sdr_config=SdrConfig(device_args="driver=fake"),
                iq_source=spec,  # type: ignore[arg-type]
            ),
        )

        server.scope_hub.capabilities("iq_fft")

        assert server._iq_scope_source._provider is provider
    finally:
        sdr_runtime._SOURCE_FACTORIES.pop(spec.kind, None)


def make_server(provider, *, invert=False):
    server = WebServer(
        None,
        WebConfig(
            radio_model="IC-7610",
            sdr_config=SdrConfig(
                device_args="driver=fake",
                sample_rate_hz=48_000,
                span_hz=12_000,
                freq_offset_hz=1000,
                invert_spectrum=invert,
            ),
            sdr_source_factory=lambda config: provider,
        ),
    )
    _seed_audio_facts(server, frequency=14_100_000)
    return server


async def test_iq_seed_precedes_open_and_updates_without_control_clients(
    virtual_clock, monkeypatch
):
    provider = FakeIqSource(center_freq_hz=0, sample_rate_hz=48_000)
    server = make_server(provider)
    server.command_state_store.apply_current(
        _store_observation(
            OBSERVED_PTT_PATH, ObservedPtt.ON, at=virtual_clock(), max_age=1
        )
    )
    opened = []
    original_open = provider.open

    def open_provider():
        opened.append((provider.center_freq_hz, server._iq_scope_source.tx_frozen))
        original_open()

    monkeypatch.setattr(provider, "open", open_provider)
    subscription = await server.scope_hub.subscribe(
        Subscription("iq_fft", None, View(14_090_000, 14_110_000), 1024, 20)
    )
    try:
        assert opened and opened[0][0] > 0
        assert opened[0][1] is True
        source = server._iq_scope_source
        assert source.active and source.tx_frozen
        target = source.resolve_view(FollowView("vfo", 20_000))
        assert (target.start_hz + target.end_hz) / 2 == 14_101_000
        assert provider.center_freq_hz != 14_101_000
        assert server._sdr_status_payload()["txFrozen"] is True
        assert server._sdr_status_payload()["state"] == "starting"
        server.command_state_store.apply_current(
            _store_observation(
                OBSERVED_PTT_PATH, ObservedPtt.OFF, at=virtual_clock(), max_age=1
            )
        )
        server._broadcast_state_update()
        assert not server._control_event_queues
        assert source.tx_frozen
        await virtual_clock.elapse(0.31)
        assert not source.tx_frozen
        _seed_audio_facts(server, frequency=14_101_000)
        server._last_state_broadcast = asyncio.get_running_loop().time()
        server._broadcast_state_update()
        shifted = source.resolve_view(FollowView("vfo", 20_000))
        assert (shifted.start_hz + shifted.end_hz) / 2 == 14_102_000
    finally:
        await subscription.close()
    assert not provider._is_open and not source.active
    assert source._timer is None


@pytest.mark.parametrize("invert", [False, True])
async def test_iq_full_capture_follow_rf_and_configured_inversion(invert):
    provider = FakeIqSource(
        center_freq_hz=0,
        sample_rate_hz=48_000,
        tones=((6000, -6),),
        noise_floor_dbfs=-100,
    )
    server = make_server(provider, invert=invert)
    server.scope_hub
    source = server._iq_scope_source
    assert source is not None
    frames = []
    detach = source.on_frame(frames.append)
    subscription = await server.scope_hub.subscribe(
        Subscription("iq_fft", None, FollowView("vfo", 20_000), 1024, 20)
    )
    try:
        assert provider.center_freq_hz > 0
        assert (
            subscription.granted.view.start_hz + subscription.granted.view.end_hz
        ) / 2 == 14_101_000
        provider.pump(8)
        await await_until(lambda: any(frame.pixels for frame in frames), timeout=2)
        frame = next(frame for frame in frames if frame.pixels)
        assert frame.coverage.end_hz - frame.coverage.start_hz == 48_000
        peak = max(range(len(frame.pixels)), key=frame.pixels.__getitem__)
        peak_hz = frame.coverage.start_hz + (peak + 0.5) * 48_000 / len(frame.pixels)
        expected = provider.center_freq_hz + (-6000 if invert else 6000)
        assert abs(peak_hz - expected) < 300
    finally:
        detach()
        await subscription.close()
    assert not provider._is_open


async def test_iq_status_tracks_current_session_frames_and_staleness(virtual_clock):
    from rigplane.scope import ScopeFrame

    provider = FakeIqSource(center_freq_hz=0, sample_rate_hz=48_000)
    server = make_server(provider)
    request = Subscription("iq_fft", None, View(14_090_000, 14_110_000), 1024, 20)
    subscription = await server.scope_hub.subscribe(request)
    source = server._iq_scope_source
    try:
        assert server._sdr_status_payload()["state"] == "starting"
        frame = ScopeFrame(0, 0, 14_080_000, 14_128_000, bytes(689), False)
        source._emit(frame)
        assert server._sdr_status_payload()["state"] == "streaming"
        await virtual_clock.elapse(2.01)
        assert server._sdr_status_payload()["state"] == "reconnecting"
        source._emit(frame)
        assert server._sdr_status_payload()["state"] == "streaming"
    finally:
        await subscription.close()
    assert server._sdr_status_payload()["state"] == "disabled"
    await virtual_clock.elapse(0.1)
    subscription = await server.scope_hub.subscribe(request)
    try:
        assert server._sdr_status_payload()["state"] == "starting"
    finally:
        await subscription.close()


async def test_iq_status_does_not_classify_cancelled_start_as_error(monkeypatch):
    provider = FakeIqSource(center_freq_hz=0, sample_rate_hz=48_000)
    server = make_server(provider)

    def cancel_open():
        raise asyncio.CancelledError

    monkeypatch.setattr(provider, "open", cancel_open)
    with pytest.raises(asyncio.CancelledError):
        await server.scope_hub.subscribe(
            Subscription("iq_fft", None, View(14_090_000, 14_110_000), 1024, 20)
        )
    assert server._sdr_status_payload()["state"] == "disabled"
    assert server._sdr_status_payload()["lastError"] is None


async def test_iq_status_counts_actual_overflow_blocks_and_resets():
    provider = FakeIqSource(center_freq_hz=0, sample_rate_hz=48_000)
    server = make_server(provider)
    request = Subscription("iq_fft", None, View(14_090_000, 14_110_000), 1024, 20)
    subscription = await server.scope_hub.subscribe(request)
    try:
        provider.set_overflow(True)
        provider.pump(2)
        provider.set_overflow(False)
        provider.pump(1)
        assert server._sdr_status_payload()["overflowCount"] == 2
    finally:
        await subscription.close()
    subscription = await server.scope_hub.subscribe(request)
    try:
        assert server._sdr_status_payload()["overflowCount"] == 0
    finally:
        await subscription.close()


async def test_iq_status_retains_open_error_until_successful_retry(monkeypatch):
    provider = FakeIqSource(center_freq_hz=0, sample_rate_hz=48_000)
    server = make_server(provider)

    def fail_open():
        raise RuntimeError("device unavailable")

    monkeypatch.setattr(provider, "open", fail_open)
    with pytest.raises(RuntimeError, match="device unavailable"):
        await server.scope_hub.subscribe(
            Subscription("iq_fft", None, View(14_090_000, 14_110_000), 1024, 20)
        )
    status = server._sdr_status_payload()
    assert status["state"] == "error"
    assert status["lastError"] == "device unavailable"
    assert not server._iq_scope_source.active
    await server._iq_scope_source.stop()
    assert server._sdr_status_payload()["lastError"] == "device unavailable"
    monkeypatch.undo()
    subscription = await server.scope_hub.subscribe(
        Subscription("iq_fft", None, View(14_090_000, 14_110_000), 1024, 20)
    )
    assert server._sdr_status_payload()["lastError"] is None
    await subscription.close()
    assert server._sdr_status_payload()["lastError"] is None
    assert server._sdr_status_payload()["state"] == "disabled"
