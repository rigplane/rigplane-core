"""Adaptive egress codec controller (MOR-588, ADR §3.6 — step 19 of MOR-562).

Falsification suite: with ``audio_adaptive_egress`` ON the broadcaster
must switch a browser client PCM16↔Opus automatically from the MOR-585
link-quality signals (client-reported ``underruns`` + server-side
``ws_queue_drops``) with sustained-degradation windows and anti-flap
dwell; with the flag OFF (the default) a client's codec must NEVER
change mid-stream — exactly the static MOR-584 per-connection choice.

Deterministic: the controller clock is injected (``_FakeClock``) — no
test sleeps through the real 3 s / 10 s / 30 s windows.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from rigplane.audio_bus import AudioBus
from rigplane.radio_protocol import AudioCapable
from rigplane.types import AudioCodec
from rigplane.web.handlers import AudioBroadcaster, AudioHandler
from rigplane.web.handlers.audio import CLEAN_WINDOW_S, DEGRADE_WINDOW_S, DWELL_S
from rigplane.web.protocol import (
    AUDIO_CODEC_OPUS,
    AUDIO_CODEC_PCM16,
    AUDIO_HEADER_SIZE,
)
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.websocket import WebSocketConnection

# 20 ms @ 48 kHz mono s16le.
PCM_PAYLOAD = b"\x01\x02" * 960


class _FakeClock:
    """Injected monotonic clock — drives windows/dwell without sleeping."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FakeTranscoder:
    """Stands in for PcmOpusTranscoder — native libopus may be absent."""

    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def pcm_to_opus(self, pcm: bytes) -> bytes:
        self.frames.append(bytes(pcm))
        return b"opus-frame"


def _make_radio(
    codec: AudioCodec = AudioCodec.PCM_1CH_16BIT, sample_rate: int = 48000
) -> tuple[Any, AudioBus]:
    radio = MagicMock(spec=AudioCapable)
    radio.capabilities = {"audio"}
    radio.audio_codec = codec
    radio.audio_sample_rate = sample_rate
    radio.start_audio_rx_opus = AsyncMock()
    radio.stop_audio_rx_opus = AsyncMock()
    bus = AudioBus(radio)
    radio.audio_bus = bus
    return radio, bus


def _make_ws() -> MagicMock:
    ws = MagicMock(spec=WebSocketConnection)
    ws.send_text = AsyncMock()
    return ws


def _make_adaptive(
    radio: Any, clock: _FakeClock | None = None
) -> tuple[AudioBroadcaster, _FakeClock]:
    clock = clock or _FakeClock()
    broadcaster = AudioBroadcaster(radio, adaptive_egress=True)
    broadcaster._adaptive_monotonic = clock
    return broadcaster, clock


async def _inject(bus: AudioBus, payload: bytes = PCM_PAYLOAD, count: int = 1) -> None:
    for _ in range(count):
        pkt = MagicMock()
        pkt.data = payload
        bus._on_opus_packet(pkt)
    await asyncio.sleep(0.1)


async def _report(handler: AudioHandler, underruns: int) -> None:
    await handler._handle_control({"type": "audio_stats", "underruns": underruns})
    # Let a fire-and-forget audio_format ack task (if any) run.
    await asyncio.sleep(0)


def _acks(ws: MagicMock) -> list[dict[str, Any]]:
    sent = [json.loads(c.args[0]) for c in ws.send_text.await_args_list]
    return [m for m in sent if m.get("type") == "audio_format"]


def _drain_codecs(handler: AudioHandler) -> list[int]:
    codecs: list[int] = []
    while True:
        try:
            codecs.append(handler._frame_queue.get_nowait()[1])
        except asyncio.QueueEmpty:
            return codecs


_TRANSCODER_PATCH = "rigplane.web.handlers.audio.create_pcm_opus_transcoder"


async def _degrade_to_opus(
    handler: AudioHandler, clock: _FakeClock, *, underruns_from: int = 0
) -> int:
    """Drive sustained degradation past DEGRADE_WINDOW_S; return underruns."""
    underruns = underruns_from + 1
    await _report(handler, underruns)  # evidence — episode starts
    elapsed = 0.0
    while elapsed < DEGRADE_WINDOW_S:
        clock.advance(1.5)
        elapsed += 1.5
        underruns += 1
        await _report(handler, underruns)
    return underruns


class TestAdaptiveDegrade:
    async def test_initial_codec_is_pcm16_even_for_opus_capable_client(self) -> None:
        """ADR §3.6: Opus is NEVER the initial codec under adaptation —
        ``preferred_rx_codec=opus`` is a capability declaration, not a
        static choice. A healthy link stays PCM16 and constructs no
        encoder."""
        radio, bus = _make_radio()
        broadcaster, _clock = _make_adaptive(radio)
        ws = _make_ws()
        handler = AudioHandler(ws, radio, broadcaster)
        await handler._start_rx(preferred_rx_codec=AUDIO_CODEC_OPUS)
        await _inject(bus)

        frame = handler._frame_queue.get_nowait()
        assert frame[1] == AUDIO_CODEC_PCM16
        assert frame[AUDIO_HEADER_SIZE:] == PCM_PAYLOAD
        assert broadcaster._client_opus_transcoders == {}
        assert [a["codec"] for a in _acks(ws)] == ["pcm16"]

    async def test_sustained_degradation_flips_client_to_opus(self) -> None:
        """Client-reported underruns rising continuously past
        DEGRADE_WINDOW_S must flip the client PCM16 → Opus, with a fresh
        audio_format ack reflecting the new codec."""
        radio, bus = _make_radio()
        broadcaster, clock = _make_adaptive(radio)
        ws = _make_ws()
        handler = AudioHandler(ws, radio, broadcaster)
        with patch(_TRANSCODER_PATCH, return_value=_FakeTranscoder()):
            await handler._start_rx(preferred_rx_codec=AUDIO_CODEC_OPUS)
            await _degrade_to_opus(handler, clock)
            await _inject(bus)

        frame = handler._frame_queue.get_nowait()
        assert frame[1] == AUDIO_CODEC_OPUS, (
            "sustained degradation past DEGRADE_WINDOW_S must engage Opus "
            "(MOR-588 headline behavior)"
        )
        assert frame[AUDIO_HEADER_SIZE:] == b"opus-frame"
        state = broadcaster._client_adaptive[id(handler._frame_queue)]
        assert state.codec == AUDIO_CODEC_OPUS, (
            "the controller DECISION itself must be Opus — not just the wire"
        )
        assert [a["codec"] for a in _acks(ws)] == ["pcm16", "opus"]

    async def test_threshold_underruns_flip_with_scheduler_jitter(self) -> None:
        """Once the threshold is met during a continuous episode, normal
        scheduler jitter after DEGRADE_WINDOW_S must not miss the switch."""
        radio, bus = _make_radio()
        broadcaster, clock = _make_adaptive(radio)
        handler = AudioHandler(_make_ws(), radio, broadcaster)
        with patch(_TRANSCODER_PATCH, return_value=_FakeTranscoder()):
            await handler._start_rx(preferred_rx_codec=AUDIO_CODEC_OPUS)
            await _report(handler, 1)
            clock.advance(1.0)
            await _report(handler, 2)
            clock.advance(1.0)
            await _report(handler, 3)
            clock.advance(DEGRADE_WINDOW_S - 2.0 + 0.1)
            await _report(handler, 3)
            await _inject(bus)

        frame = handler._frame_queue.get_nowait()
        assert frame[1] == AUDIO_CODEC_OPUS
        state = broadcaster._client_adaptive[id(handler._frame_queue)]
        assert state.codec == AUDIO_CODEC_OPUS

    async def test_sustained_underruns_below_threshold_do_not_flip(self) -> None:
        """ADR §3.6 requires >= 3 underruns inside the rolling window;
        sustained evidence below that threshold must remain PCM16."""
        radio, bus = _make_radio()
        broadcaster, clock = _make_adaptive(radio)
        handler = AudioHandler(_make_ws(), radio, broadcaster)
        await handler._start_rx(preferred_rx_codec=AUDIO_CODEC_OPUS)

        await _report(handler, 1)
        clock.advance(DEGRADE_WINDOW_S / 2)
        await _report(handler, 2)
        clock.advance(DEGRADE_WINDOW_S)
        await _report(handler, 2)
        await _inject(bus)

        frame = handler._frame_queue.get_nowait()
        assert frame[1] == AUDIO_CODEC_PCM16
        state = broadcaster._client_adaptive[id(handler._frame_queue)]
        assert state.codec == AUDIO_CODEC_PCM16
        assert broadcaster._client_opus_transcoders == {}

    async def test_sustained_queue_drops_below_threshold_do_not_flip(self) -> None:
        """ADR §3.6 requires >= 5 ws_queue_drops inside the rolling window;
        sustained evidence below that threshold must remain PCM16."""
        radio, bus = _make_radio()
        broadcaster, clock = _make_adaptive(radio)
        broadcaster.HIGH_WATERMARK = 1
        handler = AudioHandler(_make_ws(), radio, broadcaster)
        await handler._start_rx(preferred_rx_codec=AUDIO_CODEC_OPUS)

        for _ in range(4):
            await _inject(bus)
            clock.advance(DEGRADE_WINDOW_S / 4)
        clock.advance(DEGRADE_WINDOW_S)
        await _inject(bus)

        state = broadcaster._client_adaptive[id(handler._frame_queue)]
        assert state.codec == AUDIO_CODEC_PCM16
        assert set(_drain_codecs(handler)) == {AUDIO_CODEC_PCM16}
        assert broadcaster._client_opus_transcoders == {}

    async def test_isolated_burst_does_not_flip(self) -> None:
        """A single evidence burst that is NOT sustained (episode gap >
        DEGRADE_WINDOW_S) must not engage Opus."""
        radio, bus = _make_radio()
        broadcaster, clock = _make_adaptive(radio)
        handler = AudioHandler(_make_ws(), radio, broadcaster)
        await handler._start_rx(preferred_rx_codec=AUDIO_CODEC_OPUS)

        await _report(handler, 1)  # one burst
        clock.advance(DEGRADE_WINDOW_S + 1.0)
        await _report(handler, 1)  # clean — episode reset
        clock.advance(0.1)
        await _report(handler, 2)  # new episode, not yet sustained
        await _inject(bus)

        frame = handler._frame_queue.get_nowait()
        assert frame[1] == AUDIO_CODEC_PCM16
        assert broadcaster._client_opus_transcoders == {}
        # The wire frame alone cannot discriminate here: without libopus
        # the MOR-584 fallback emits PCM16 + a clean encoder pool EVEN IF
        # the controller wrongly switched. Assert the controller's own
        # decision so a neutered DEGRADE_WINDOW_S gate or a removed
        # degrade-episode reset fails this test (mutation-testing gap).
        state = broadcaster._client_adaptive[id(handler._frame_queue)]
        assert state.codec == AUDIO_CODEC_PCM16, (
            "non-continuous evidence must not flip the controller decision: "
            "PCM16->Opus requires a CONTINUOUS episode >= DEGRADE_WINDOW_S, "
            "and an evidence gap > DEGRADE_WINDOW_S must reset the episode"
        )

    async def test_server_side_queue_drops_alone_flip_without_stats_uplink(
        self,
    ) -> None:
        """The server-side ws_queue_drops signal must drive adaptation on
        its own — a congested client that never sends audio_stats still
        degrades to Opus (relay-loop evaluation)."""
        radio, bus = _make_radio()
        broadcaster, clock = _make_adaptive(radio)
        broadcaster.HIGH_WATERMARK = 2  # tiny queue → forced evictions
        handler = AudioHandler(_make_ws(), radio, broadcaster)
        with patch(_TRANSCODER_PATCH, return_value=_FakeTranscoder()):
            await handler._start_rx(preferred_rx_codec=AUDIO_CODEC_OPUS)
            # Nobody drains the queue: each relay iteration past the
            # watermark evicts-oldest and increments ws_queue_drops.
            for _ in range(10):
                await _inject(bus)
                clock.advance(0.5)

        state = broadcaster._client_adaptive[id(handler._frame_queue)]
        assert state.codec == AUDIO_CODEC_OPUS, (
            "sustained ws_queue_drops must flip the client without any "
            "client-side audio_stats uplink"
        )
        assert _drain_codecs(handler)[-1] == AUDIO_CODEC_OPUS


class TestAdaptiveUpgrade:
    async def test_clean_window_restores_pcm16_and_tears_down_encoder(self) -> None:
        radio, bus = _make_radio()
        broadcaster, clock = _make_adaptive(radio)
        ws = _make_ws()
        handler = AudioHandler(ws, radio, broadcaster)
        with patch(_TRANSCODER_PATCH, return_value=_FakeTranscoder()):
            await handler._start_rx(preferred_rx_codec=AUDIO_CODEC_OPUS)
            underruns = await _degrade_to_opus(handler, clock)
            await _inject(bus)  # encodes one Opus frame → encoder exists
            assert len(broadcaster._client_opus_transcoders) == 1

            clock.advance(CLEAN_WINDOW_S)
            await _report(handler, underruns)  # unchanged counters = clean
            await _inject(bus)

        assert _drain_codecs(handler)[-1] == AUDIO_CODEC_PCM16, (
            "a fully clean CLEAN_WINDOW_S must restore PCM16"
        )
        assert broadcaster._client_opus_transcoders == {}, (
            "upgrade must tear down the per-client encoder (MOR-584 pool)"
        )
        state = broadcaster._client_adaptive[id(handler._frame_queue)]
        assert state.codec == AUDIO_CODEC_PCM16
        assert [a["codec"] for a in _acks(ws)] == ["pcm16", "opus", "pcm16"]

    async def test_clean_window_not_met_stays_opus(self) -> None:
        radio, bus = _make_radio()
        broadcaster, clock = _make_adaptive(radio)
        handler = AudioHandler(_make_ws(), radio, broadcaster)
        with patch(_TRANSCODER_PATCH, return_value=_FakeTranscoder()):
            await handler._start_rx(preferred_rx_codec=AUDIO_CODEC_OPUS)
            underruns = await _degrade_to_opus(handler, clock)

            clock.advance(CLEAN_WINDOW_S - 1.0)
            await _report(handler, underruns)
            await _inject(bus)

        assert _drain_codecs(handler)[-1] == AUDIO_CODEC_OPUS


class TestAdaptiveDwell:
    async def test_no_flap_within_dwell(self) -> None:
        """Rapid degradation right after a switch must NOT flip again
        until DWELL_S has passed — then, with evidence still sustained,
        the switch happens."""
        radio, bus = _make_radio()
        broadcaster, clock = _make_adaptive(radio)
        handler = AudioHandler(_make_ws(), radio, broadcaster)
        with patch(_TRANSCODER_PATCH, side_effect=lambda **_kw: _FakeTranscoder()):
            await handler._start_rx(preferred_rx_codec=AUDIO_CODEC_OPUS)
            underruns = await _degrade_to_opus(handler, clock)
            clock.advance(CLEAN_WINDOW_S)
            await _report(handler, underruns)  # upgrade switch → PCM16
            switch_at = clock.now

            # Fresh sustained degradation immediately after the upgrade.
            elapsed = 0.0
            while elapsed < DEGRADE_WINDOW_S + 1.5:
                clock.advance(1.5)
                elapsed += 1.5
                underruns += 1
                await _report(handler, underruns)
            assert clock.now - switch_at < DWELL_S
            await _inject(bus)
            assert _drain_codecs(handler)[-1] == AUDIO_CODEC_PCM16, (
                "a switch within DWELL_S of the previous one is flapping"
            )

            # Keep the episode alive until the dwell expires → now it flips.
            while clock.now - switch_at < DWELL_S:
                clock.advance(1.5)
                underruns += 1
                await _report(handler, underruns)
            await _inject(bus)
            assert _drain_codecs(handler)[-1] == AUDIO_CODEC_OPUS


class TestAdaptiveExemptions:
    async def test_pcm16_pinned_client_never_adapts(self) -> None:
        """A client without Opus decode (declared pcm16) is pinned PCM16
        and fully exempt — no controller state, no encoder, ever."""
        radio, bus = _make_radio()
        broadcaster, clock = _make_adaptive(radio)
        handler = AudioHandler(_make_ws(), radio, broadcaster)
        await handler._start_rx(preferred_rx_codec=AUDIO_CODEC_PCM16)
        await _degrade_to_opus(handler, clock)
        await _inject(bus)

        assert broadcaster._client_adaptive == {}
        assert broadcaster._client_opus_transcoders == {}
        assert _drain_codecs(handler) == [AUDIO_CODEC_PCM16]

    async def test_no_preference_client_keeps_static_behavior(self) -> None:
        """Unknown decode capability (legacy client, no preferred_rx_codec)
        → exempt: static MOR-584 resolution, no adaptation."""
        radio, bus = _make_radio()
        broadcaster, clock = _make_adaptive(radio)
        handler = AudioHandler(_make_ws(), radio, broadcaster)
        await handler._start_rx()
        await _degrade_to_opus(handler, clock)
        await _inject(bus)

        assert broadcaster._client_adaptive == {}
        assert _drain_codecs(handler) == [AUDIO_CODEC_PCM16]

    async def test_opus_native_radio_stays_passthrough(self) -> None:
        """Opus-native radios pass through un-decoded for every client
        (issue #762) — the controller never re-encodes or interferes."""
        radio, bus = _make_radio(codec=AudioCodec.OPUS_1CH)
        broadcaster, clock = _make_adaptive(radio)
        handler = AudioHandler(_make_ws(), radio, broadcaster)
        await handler._start_rx(preferred_rx_codec=AUDIO_CODEC_OPUS)
        await _degrade_to_opus(handler, clock)
        opus_payload = b"\x42" * 120
        await _inject(bus, opus_payload)

        frame = handler._frame_queue.get_nowait()
        assert frame[1] == AUDIO_CODEC_OPUS
        assert frame[AUDIO_HEADER_SIZE:] == opus_payload
        assert broadcaster._client_opus_transcoders == {}


class TestFlagOff:
    async def test_flag_off_opus_client_is_static_and_never_switches(self) -> None:
        """[BC] pin: default (flag off) is EXACTLY MOR-584 — an Opus-
        preferring client gets Opus from the first frame, a degradation
        storm changes nothing, and no controller state exists."""
        radio, bus = _make_radio()
        broadcaster = AudioBroadcaster(radio)
        clock = _FakeClock()
        broadcaster._adaptive_monotonic = clock
        ws = _make_ws()
        handler = AudioHandler(ws, radio, broadcaster)
        with patch(_TRANSCODER_PATCH, return_value=_FakeTranscoder()):
            await handler._start_rx(preferred_rx_codec=AUDIO_CODEC_OPUS)
            await _inject(bus)
            await _degrade_to_opus(handler, clock)
            await _inject(bus)

        assert _drain_codecs(handler) == [AUDIO_CODEC_OPUS, AUDIO_CODEC_OPUS]
        assert broadcaster._client_adaptive == {}
        assert [a["codec"] for a in _acks(ws)] == ["opus"], (
            "flag off must not emit adaptive audio_format acks"
        )

    async def test_flag_off_pcm16_client_never_switches(self) -> None:
        radio, bus = _make_radio()
        broadcaster = AudioBroadcaster(radio)
        clock = _FakeClock()
        broadcaster._adaptive_monotonic = clock
        broadcaster.HIGH_WATERMARK = 2
        handler = AudioHandler(_make_ws(), radio, broadcaster)
        await handler._start_rx(preferred_rx_codec=AUDIO_CODEC_PCM16)
        await _degrade_to_opus(handler, clock)
        for _ in range(8):  # server-side congestion signal too
            await _inject(bus)
            clock.advance(1.0)

        assert set(_drain_codecs(handler)) == {AUDIO_CODEC_PCM16}
        assert broadcaster._client_adaptive == {}
        assert broadcaster._client_opus_transcoders == {}

    async def test_webconfig_flag_defaults_off_and_plumbs_to_broadcaster(
        self,
    ) -> None:
        assert WebConfig().audio_adaptive_egress is False
        assert WebServer()._audio_broadcaster._adaptive_egress is False
        server = WebServer(config=WebConfig(audio_adaptive_egress=True))
        assert server._audio_broadcaster._adaptive_egress is True


class TestAdaptiveCleanup:
    async def test_unsubscribe_clears_adaptive_state(self) -> None:
        radio, _bus = _make_radio()
        broadcaster, _clock = _make_adaptive(radio)
        handler = AudioHandler(_make_ws(), radio, broadcaster)
        await handler._start_rx(preferred_rx_codec=AUDIO_CODEC_OPUS)
        assert len(broadcaster._client_adaptive) == 1

        await broadcaster.unsubscribe(handler._frame_queue)
        assert broadcaster._client_adaptive == {}

    async def test_reap_dead_clients_clears_adaptive_state(self) -> None:
        radio, _bus = _make_radio()
        broadcaster, _clock = _make_adaptive(radio)
        ws = _make_ws()
        handler = AudioHandler(ws, radio, broadcaster)
        await handler._start_rx(preferred_rx_codec=AUDIO_CODEC_OPUS)
        assert len(broadcaster._client_adaptive) == 1

        ws.is_alive.return_value = False
        assert await broadcaster.reap_dead_clients() == 1
        assert broadcaster._client_adaptive == {}
