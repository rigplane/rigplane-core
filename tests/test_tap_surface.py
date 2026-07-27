"""Tests for the named RX tap-stage surface (MOR-565, ADR §3.7 skeleton).

Stage scheme — only stages with a live frame source are instantiated:

- ``rx.pcm``      — radio-native RX frames at the AudioBus fan-out
  (hosted on :class:`~rigplane.audio.bus.AudioBus`).
- ``rx.post_dsp`` — decoded PCM16 after the broadcaster's DSP pipeline
  (hosted on :class:`~rigplane.web.handlers.audio.AudioBroadcaster`; this
  is the pre-existing ``_tap_registry``, renamed into the scheme).

Reserved stage names (``rx.raw``, ``rx.egress``, ``tx.*``) have NO registry —
asking for them must fail loudly rather than silently swallow frames.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import numpy as np
import pytest

from rigplane.audio import AudioPacket
from rigplane.audio.bus import STAGE_RX_PCM, STAGE_RX_POST_DSP, AudioBus
from rigplane.capabilities import CAP_AUDIO, CAP_SCOPE
from rigplane.radio_protocol import AudioCapable, ScopeCapable
from rigplane.types import AudioCodec
from rigplane.web.handlers.audio import AudioBroadcaster, RxPcmTapSource


@pytest.fixture
def bus():
    radio = SimpleNamespace(
        start_audio_rx_opus=AsyncMock(),
        stop_audio_rx_opus=AsyncMock(),
    )
    return AudioBus(radio)


class TestRxPcmStage:
    """``rx.pcm`` — passive tap at the AudioBus fan-out."""

    async def test_tap_observes_frames_without_altering_delivery(self, bus):
        sub = bus.subscribe(name="s1")
        await sub.start()
        seen: list[bytes] = []
        handle = bus.taps(STAGE_RX_PCM).register("debug", seen.append)

        pkt = AudioPacket(ident=0x80, send_seq=1, data=b"abc")
        bus._on_opus_packet(pkt)
        assert seen == [b"abc"]
        # Passive observer: subscriber delivery and heartbeat are untouched.
        assert await sub.get(timeout=1.0) is pkt
        assert bus.last_rx_frame_monotonic is not None

        bus.taps(STAGE_RX_PCM).unregister(handle)
        bus._on_opus_packet(AudioPacket(ident=0x80, send_seq=2, data=b"def"))
        assert seen == [b"abc"], "detach must restore no-op"
        assert bus.taps(STAGE_RX_PCM).active is False
        await sub.aclose()

    def test_empty_stage_is_noop(self, bus):
        bus._on_opus_packet(AudioPacket(ident=0x80, send_seq=1, data=b"x"))
        assert bus.taps(STAGE_RX_PCM).active is False
        assert bus.last_rx_frame_monotonic is not None

    def test_none_packet_is_not_fed_to_taps(self, bus):
        seen: list[bytes] = []
        bus.taps(STAGE_RX_PCM).register("debug", seen.append)
        bus._on_opus_packet(None)  # EOF/idle marker
        assert seen == []
        assert bus.last_rx_frame_monotonic is not None, "heartbeat preserved"

    def test_reserved_stage_has_no_registry(self, bus):
        with pytest.raises(KeyError):
            bus.taps("rx.raw")


class TestRxPostDspStage:
    """``rx.post_dsp`` — the broadcaster's pre-existing registry, renamed."""

    def test_post_dsp_stage_is_the_existing_tap_registry(self):
        broadcaster = AudioBroadcaster(radio=None)
        assert broadcaster.taps(STAGE_RX_POST_DSP) is broadcaster._tap_registry

    def test_set_pcm_tap_compat_registers_on_post_dsp_stage(self):
        broadcaster = AudioBroadcaster(radio=None)
        received: list[bytes] = []
        broadcaster.set_pcm_tap(received.append)
        broadcaster.taps(STAGE_RX_POST_DSP).feed(b"\xaa")
        assert received == [b"\xaa"]
        broadcaster.set_pcm_tap(None)
        assert broadcaster.taps(STAGE_RX_POST_DSP).active is False

    def test_reserved_stage_has_no_registry(self):
        broadcaster = AudioBroadcaster(radio=None)
        with pytest.raises(KeyError):
            broadcaster.taps("rx.egress")


class _Route:
    def __init__(self) -> None:
        sub = self.subscription = AsyncMock()
        sub.started = False
        sub.start.side_effect = lambda: setattr(sub, "started", True)
        sub.stop = Mock()
        sub.__aiter__.return_value = []
        self.subscribe_calls = 0

    def subscribe(self, name: str = ""):  # noqa: ARG002
        self.subscribe_calls += 1
        return self.subscription

    async def subscribe_rx(self, name: str):  # noqa: ARG002
        return self.subscription


class _PcmRadio(AudioCapable):
    audio_bus: object = None

    def __init__(
        self,
        *,
        codec: object = AudioCodec.PCM_1CH_16BIT,
        sample_rate: object = 48_000,
        bus: object = ...,
        session: object = ...,
        raising: str | None = None,
    ) -> None:
        self.capabilities = {CAP_AUDIO}
        self._codec = codec
        self._sample_rate = sample_rate
        self.audio_bus = _Route() if bus is ... else bus
        self._raising = raising
        if session is not ...:
            self.audio_session = session

    @property
    def audio_codec(self):
        return self._codec

    @property
    def audio_sample_rate(self):
        return self._sample_rate

    def __getattribute__(self, name: str):
        if name == object.__getattribute__(self, "_raising"):
            raise RuntimeError(f"{name.removeprefix('audio_')} route unavailable")
        return object.__getattribute__(self, name)


def test_pcm_tap_source_preserves_descriptor_matrix() -> None:
    for codec in (
        AudioCodec.PCM_1CH_16BIT,
        AudioCodec.PCM_2CH_16BIT,
        AudioCodec.ULAW_1CH,
        AudioCodec.ULAW_2CH,
    ):
        assert AudioBroadcaster(_PcmRadio(codec=codec)).rx_pcm_tap_source == (
            RxPcmTapSource(codec=codec, sample_rate=48_000)
        )
    rejected = (
        _PcmRadio(codec=AudioCodec.PCM_1CH_8BIT),
        _PcmRadio(codec=AudioCodec.PCM_2CH_8BIT),
        _PcmRadio(codec=AudioCodec.OPUS_1CH),
        _PcmRadio(codec="pcm16"),
        _PcmRadio(sample_rate=0),
        SimpleNamespace(
            capabilities={CAP_AUDIO},
            audio_codec=AudioCodec.PCM_1CH_16BIT,
            audio_sample_rate=48_000,
            audio_bus=_Route(),
        ),
        _PcmRadio(sample_rate=True),
        _PcmRadio(sample_rate=48_000.0),
        _PcmRadio(bus=SimpleNamespace(subscribe=None)),
        _PcmRadio(session=SimpleNamespace(subscribe_rx=None)),
        _PcmRadio(raising="audio_session"),
        _PcmRadio(raising="audio_bus"),
    )
    assert all(AudioBroadcaster(radio).rx_pcm_tap_source is None for radio in rejected)


@pytest.mark.asyncio
async def test_relay_route_prefers_session_then_falls_back_to_bus() -> None:
    session = _Route()
    bus = _Route()
    for radio, route, bus_started in (
        (_PcmRadio(session=session), session, False),
        (_PcmRadio(bus=bus), bus, True),
    ):
        broadcaster = AudioBroadcaster(radio)
        await broadcaster._start_relay()
        assert broadcaster._subscription is route.subscription
        assert radio.audio_bus.subscription.started is bus_started
        await broadcaster._relay_task


@pytest.mark.asyncio
async def test_malformed_selected_relay_routes_notify_without_fallback() -> None:
    for radio, expected in (
        (
            _PcmRadio(session=SimpleNamespace(subscribe_rx=None)),
            "audio_session.subscribe_rx must be callable",
        ),
        (_PcmRadio(raising="audio_session"), "session route unavailable"),
        (
            _PcmRadio(bus=SimpleNamespace(subscribe=None)),
            "audio_bus.subscribe must be callable",
        ),
        (_PcmRadio(raising="audio_bus"), "bus route unavailable"),
        (_PcmRadio(bus=None), "audio_bus is required for runtime audio"),
        (
            SimpleNamespace(capabilities={CAP_AUDIO}, has_usb_audio=True),
            "audio_bus is required for runtime audio",
        ),
    ):
        ws = SimpleNamespace(send_text=AsyncMock(), is_alive=lambda: True)
        broadcaster = AudioBroadcaster(radio)
        assert broadcaster.rx_pcm_tap_source is None
        await broadcaster.subscribe(ws=ws)
        assert broadcaster._subscription is broadcaster._relay_task is None
        if "session" in expected:
            assert radio.__dict__["audio_bus"].subscribe_calls == 0
        assert json.loads(ws.send_text.await_args.args[0]) == {
            "type": "error",
            "message": f"audio_start: RX audio failed to start: {expected}",
        }


@pytest.mark.asyncio
async def test_native_opus_route_is_relayable_without_pcm_tap() -> None:
    broadcaster = AudioBroadcaster(_PcmRadio(codec=AudioCodec.OPUS_1CH))
    assert broadcaster.rx_pcm_tap_source is None
    await broadcaster._start_relay()
    assert broadcaster._subscription is not None
    await broadcaster._relay_task


# ── FFT scope behavior preservation ──────────────────────────────────────────


class _MatrixRadio(_PcmRadio, ScopeCapable):
    def __init__(
        self, *, hardware: bool, audio: bool, sample_rate: int = 32_000
    ) -> None:
        super().__init__(sample_rate=sample_rate)
        from rigplane.radio_state import RadioState

        self.capabilities = {
            capability
            for capability, enabled in ((CAP_SCOPE, hardware), (CAP_AUDIO, audio))
            if enabled
        }
        self.radio_state = RadioState()
        self.scope_callback = None

    def on_scope_data(self, callback) -> None:
        self.scope_callback = callback


class _Writer:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        pass


def _json_body(writer: _Writer) -> dict:
    payload = writer.buffer.decode("ascii", errors="replace")
    return json.loads(payload[payload.index("\r\n\r\n") + 4 :])


class _FakeScopeHandler:
    def __init__(self) -> None:
        self.frames: list = []

    def enqueue_frame(self, frame) -> None:
        self.frames.append(frame)


def test_fft_scope_receives_frames_via_named_post_dsp_stage() -> None:
    """The FFT scope (wired via ``set_pcm_tap`` at server init) is a tap on
    the named ``rx.post_dsp`` stage — feeding that stage produces scope
    frames exactly as it did through ``_tap_registry`` before the rename.
    """
    from rigplane.web.server import WebConfig, WebServer

    radio = _MatrixRadio(hardware=False, audio=True, sample_rate=48_000)
    server = WebServer(radio=radio, config=WebConfig())
    scope = server._audio_fft_scope
    assert scope is not None, "audio FFT scope not wired"
    radio.radio_state.main.freq = 14_074_000
    scope.set_center_freq(14_074_000)
    scope._last_frame_time = 0.0  # bypass the fps rate-limit for the first frame

    handler = _FakeScopeHandler()
    server._audio_scope_handlers.add(handler)

    rng = np.random.default_rng(565)
    registry = server._audio_broadcaster.taps(STAGE_RX_POST_DSP)
    for _ in range(9):  # 9 × 960 samples (20 ms @ 48 kHz) ≥ 4 FFT windows
        pcm = (rng.uniform(-1, 1, 960) * 5000).astype(np.int16).tobytes()
        registry.feed(pcm)

    assert len(handler.frames) >= 1, "FFT scope stopped receiving via rx.post_dsp"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("hardware", "audio", "scope_source"),
    [
        (True, False, "hardware"),
        (False, True, "audio_fft"),
        (True, True, "hardware"),
        (False, False, None),
    ],
)
async def test_scope_service_lifecycle_and_serializer_matrix(
    hardware: bool, audio: bool, scope_source: str | None
) -> None:
    from rigplane.web.server import WebServer

    server = WebServer(_MatrixRadio(hardware=hardware, audio=audio))
    scope = server._audio_fft_scope
    assert (scope is not None) is audio
    if scope is not None:
        assert scope._sample_rate == 32_000

    broadcaster = server._audio_broadcaster
    assert (broadcaster._legacy_tap_handle is not None) is (audio and not hardware)
    main_handler = _FakeScopeHandler()
    audio_handler = _FakeScopeHandler()
    server._scope_handlers.add(main_handler)
    await server.ensure_audio_scope_enabled(audio_handler)
    assert (broadcaster._legacy_tap_handle is not None) is audio
    if scope is not None:
        frame = object()
        server._dispatch_audio_fft_frame(frame)
        assert audio_handler.frames == [frame]
        assert main_handler.frames == ([] if hardware else [frame])
    server.unregister_audio_scope_handler(audio_handler)
    assert (broadcaster._legacy_tap_handle is not None) is (audio and not hardware)

    writer = _Writer()
    await server._serve_capabilities(writer)
    capabilities = _json_body(writer)
    assert capabilities["scope"] is hardware
    assert capabilities["audio"] is audio
    assert capabilities["audioFftAvailable"] is audio
    assert capabilities["scopeSource"] == scope_source


@pytest.mark.asyncio
async def test_runtime_hardware_scope_is_cached_for_dispatch_and_teardown() -> None:
    from rigplane.web.server import WebServer

    radio = _MatrixRadio(hardware=True, audio=True)
    server = WebServer(radio)
    audio_handler = _FakeScopeHandler()
    main_handler = _FakeScopeHandler()
    server._scope_handlers.add(main_handler)
    await server.ensure_audio_scope_enabled(audio_handler)
    radio.capabilities.clear()

    frame = object()
    server._dispatch_audio_fft_frame(frame)
    assert audio_handler.frames == [frame]
    assert main_handler.frames == []
    server.unregister_audio_scope_handler(audio_handler)
    assert server._audio_broadcaster._legacy_tap_handle is None


@pytest.mark.asyncio
@pytest.mark.parametrize("hardware", [False, True])
async def test_audio_tag_without_proven_route_does_not_advertise_fft(
    hardware: bool,
) -> None:
    from rigplane.web.server import WebServer

    radio = _MatrixRadio(hardware=hardware, audio=True)
    radio.audio_bus = None
    server = WebServer(radio)
    writer = _Writer()
    await server._serve_capabilities(writer)
    capabilities = _json_body(writer)
    assert server._audio_fft_scope is None
    assert capabilities["audioFftAvailable"] is False
    assert capabilities["scopeSource"] == ("hardware" if hardware else None)
