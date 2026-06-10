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

from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest

from rigplane.audio import AudioPacket
from rigplane.audio.bus import STAGE_RX_PCM, STAGE_RX_POST_DSP, AudioBus
from rigplane.capabilities import CAP_AUDIO
from rigplane.web.handlers.audio import AudioBroadcaster


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


# ── FFT scope behavior preservation ──────────────────────────────────────────


class _AudioOnlyRadio:
    """Minimal fake radio with audio but no hardware scope."""

    def __init__(self) -> None:
        from rigplane.radio_state import RadioState

        self.capabilities = frozenset({CAP_AUDIO})
        self.radio_state = RadioState()
        self.audio_codec = None
        self.audio_sample_rate = 48_000


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

    radio = _AudioOnlyRadio()
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
