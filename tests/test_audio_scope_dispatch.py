"""Regression tests for MOR-241 — X6200 AUDIO SCOPE panel is blank.

Root cause: ``AudioFftScope.on_frame()`` is a single-slot callback setter.
``WebServer.__init__`` registered ``_broadcast_audio_scope`` (the
``/api/v1/audio-scope`` relay) and then, for non-hardware-scope radios,
*overwrote* it with ``_broadcast_scope`` (the ``/api/v1/scope`` relay). The
second registration silently clobbered the first, so the audio-scope path
received zero FFT frames and the AUDIO SCOPE panel rendered blank. IC-7610
(``CAP_SCOPE`` present) skipped the second registration and was unaffected.

The fix registers a single dispatch method that fans out to BOTH broadcasters
for non-hardware-scope radios, while a hardware-scope radio drives ONLY the
audio-scope path (so the IC-7610 main spectrum keeps coming from the real
hardware scope, byte-identical).

These tests are hardware-free: they reuse the MOR-236 mono-USB X6200 pattern
(a real ``Ic705SerialRadio`` resolved against the X6200 profile) and a
hardware-scope fake radio, feeding synthetic PCM16 through the broadcaster's
PCM tap exactly as the live audio pipeline does.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import numpy as np
import pytest

from rigplane.audio.backend import (
    AudioDeviceId,
    AudioDeviceInfo,
    FakeRxStream,
    FakeTxStream,
    TxPlayoutPolicy,
)
from rigplane.exceptions import CommandError
from rigplane.radio import IcomRadio
from rigplane.types import AudioCodec
from rigplane.web.server import WebConfig, WebServer


# ── Fakes ────────────────────────────────────────────────────────────────────


class _MonoUsbAudioBackend:
    """Fake mono USB CODEC backend (Xiegu X6200 shape), as used by MOR-236."""

    def __init__(self, *, input_channels: int = 1) -> None:
        self._device = AudioDeviceInfo(
            id=AudioDeviceId(0),
            name="USB Audio Device",
            input_channels=input_channels,
            output_channels=input_channels,
            default_samplerate=48_000,
            is_default_input=True,
            is_default_output=True,
        )
        self.rx_streams: list[FakeRxStream] = []
        self.tx_streams: list[FakeTxStream] = []

    def list_devices(self) -> list[AudioDeviceInfo]:
        return [self._device]

    def check_sample_rate(
        self, device: AudioDeviceId, sample_rate: int, *, direction: str = "rx"
    ) -> bool:
        return sample_rate in (48_000, 24_000, 16_000, 8_000)

    def open_rx(
        self,
        device: AudioDeviceId,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
        deliver_channels: int | None = None,
        rx_audio_channel: str = "mix",
    ) -> FakeRxStream:
        stream = FakeRxStream()
        self.rx_streams.append(stream)
        return stream

    def open_tx(
        self,
        device: AudioDeviceId,
        *,
        sample_rate: int = 48_000,
        channels: int = 1,
        frame_ms: int = 20,
        playout: TxPlayoutPolicy = "adaptive",
    ) -> FakeTxStream:
        stream = FakeTxStream(playout=playout)
        self.tx_streams.append(stream)
        return stream


class _FakeSerialCivLink:
    """Minimal serial CI-V link double so the radio reports ``connected``."""

    def __init__(self) -> None:
        self.connected = False
        self.ready = False
        self.healthy = False

    async def connect(self) -> None:
        self.connected = True
        self.ready = True
        self.healthy = True

    async def disconnect(self) -> None:
        self.connected = False
        self.ready = False
        self.healthy = False

    async def send(self, frame: bytes) -> None:
        _ = frame

    async def send_written(
        self, frame: bytes, *, is_current: Callable[[], bool] | None = None
    ) -> None:
        if is_current is not None and not is_current():
            raise CommandError("Serial CI-V write is no longer current.")
        await self.send(frame)

    async def receive(self, timeout: float | None = None) -> bytes | None:
        await asyncio.sleep(0.0 if timeout is None else min(timeout, 0.0))
        return None


def _make_hardware_and_pcm_radio() -> IcomRadio:
    """Build an unconnected radio with BOTH hardware scope and concrete PCM.

    Used to prove the IC-7610-class non-regression: a hardware-scope radio
    must still receive audio-scope frames but must NOT have the audio FFT
    drive ``/api/v1/scope`` (its main spectrum comes from the real scope).
    """
    return IcomRadio(
        "127.0.0.1",
        model="IC-7610",
        audio_codec=AudioCodec.PCM_1CH_16BIT,
        audio_sample_rate=48_000,
    )


def _seed_audio_facts(
    server, receiver="0", mode="USB", frequency=14_074_000, width=2400
):
    from rigplane.core.state_pipeline_contracts import (
        FieldPath,
        Observation,
        SourceMetadata,
    )

    for name, value in (
        ("freq_hz", frequency),
        ("mode", mode),
        ("filter_width", width),
    ):
        server.command_state_store.apply_current(
            Observation(
                path=FieldPath.active(receiver, "freq_mode", name),
                value=value,
                source=SourceMetadata(source="poll_response", provider="test"),
                timestamp_monotonic=0,
            )
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("other_consumer", ["none", "tap", "audio_ws"])
async def test_audio_fft_is_explicit_and_last_unsubscribe_releases_real_rx(
    other_consumer,
):
    from test_handlers_coverage import _RelayRadio
    from rigplane.audio.bus import STAGE_RX_POST_DSP
    from rigplane.scope.models import Subscription, View
    from rigplane.web.protocol import decode_scope_frame_v2

    radio = _RelayRadio()
    server = WebServer(radio, WebConfig(radio_model="IC-7610"))
    broadcaster = server._audio_broadcaster
    _seed_audio_facts(server)
    assert broadcaster._subscription is None
    assert broadcaster._legacy_tap_handle is None
    # The analyzer observes active audio, but carries no RX demand.
    assert broadcaster._tap_registry.active
    assert not broadcaster._tap_registry.demand
    extra = None
    if other_consumer == "tap":
        extra = broadcaster.taps(STAGE_RX_POST_DSP).register(
            "real-consumer", lambda pcm: None
        )
    elif other_consumer == "audio_ws":
        extra = await broadcaster.subscribe()
    subscription = await server.scope_hub.subscribe(
        Subscription("audio_fft", None, View(14_074_000, 14_076_400), 1024, 20)
    )
    second = await server.scope_hub.subscribe(
        Subscription("audio_fft", None, View(14_074_000, 14_076_400), 2800, 20)
    )
    assert broadcaster._subscription is not None
    radio._start_rx.assert_awaited_once()
    pcm = (
        np.random.default_rng(241).integers(-5000, 5000, 8192, dtype=np.int16).tobytes()
    )
    broadcaster.taps(STAGE_RX_POST_DSP).feed(pcm)
    wire = await asyncio.wait_for(subscription.receive(), 2)
    assert decode_scope_frame_v2(wire).source == "audio_fft"
    await subscription.close()
    assert broadcaster._tap_registry.active
    await second.close()
    assert broadcaster._legacy_tap_handle is None
    assert broadcaster._tap_registry.active
    assert broadcaster._tap_registry.demand is (other_consumer == "tap")
    if other_consumer == "none":
        assert broadcaster._subscription is None
        assert broadcaster._relay_task is None
        radio._stop_rx.assert_awaited_once()
    else:
        assert broadcaster._subscription is not None
        radio._stop_rx.assert_not_awaited()
        if other_consumer == "tap":
            broadcaster.taps(STAGE_RX_POST_DSP).unregister(extra)
            await broadcaster.release_relay_if_idle()
        else:
            await broadcaster.unsubscribe(extra)
        assert broadcaster._subscription is None


@pytest.mark.asyncio
@pytest.mark.parametrize("release", ["unsubscribe", "reap", "idle"])
@pytest.mark.parametrize("demanding_tap", [False, True])
async def test_passive_observer_receives_live_pcm_but_does_not_retain_rx(
    release, demanding_tap
):
    from types import SimpleNamespace

    from test_handlers_coverage import _RelayRadio
    from rigplane.audio import AudioPacket
    from rigplane.audio.bus import STAGE_RX_POST_DSP
    from rigplane.testing.timing import await_until
    from rigplane.web.handlers.audio import AudioBroadcaster

    radio = _RelayRadio()
    broadcaster = AudioBroadcaster(radio)
    received: list[bytes] = []
    broadcaster.set_passive_pcm_tap(received.append)
    registry = broadcaster.taps(STAGE_RX_POST_DSP)
    assert registry.active
    assert not registry.demand
    demanding = (
        registry.register("cw-consumer", lambda pcm: None) if demanding_tap else None
    )
    alive = [True]
    queue = await broadcaster.subscribe(ws=SimpleNamespace(is_alive=lambda: alive[0]))
    try:
        pcm = b"\x01\x00" * 160
        callback = radio._start_rx.call_args.args[0]
        callback(AudioPacket(ident=0x0080, send_seq=1, data=pcm))
        await asyncio.wait_for(queue.get(), 2)
        assert received == [pcm]
        if release == "reap":
            alive[0] = False
            assert await broadcaster.reap_dead_clients() == 1
        else:
            await broadcaster.unsubscribe(queue)
            if release == "idle":
                if demanding is not None:
                    registry.unregister(demanding)
                    demanding = None
                await broadcaster.release_relay_if_idle()
        assert registry.active
        assert (broadcaster._subscription is not None) is (demanding is not None)
        if demanding is not None:
            radio._stop_rx.assert_not_awaited()
            registry.unregister(demanding)
            await broadcaster.release_relay_if_idle()
        assert broadcaster._subscription is None
        await await_until(lambda: radio._stop_rx.await_count == 1)
        radio._stop_rx.assert_awaited_once()
    finally:
        await broadcaster._stop_relay()
