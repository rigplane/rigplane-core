"""T1a conformance pin for the adaptive egress codec controller (MOR-588).

ADR §3.6 / tenet T1a: lossless-class consumers (the AudioBridge /
digital path → WSJT-X, the FFT-scope PCM taps) are categorically exempt
from adaptive codec switching — by construction, NO egress encoder is
ever constructible on a bridge/digital path, even while the controller
actively degrades a browser WS client on the same broadcaster.

The bridge consumes RX as an AudioBus subscriber UPSTREAM of the
broadcaster's per-client egress encode; the FFT scope consumes the
post-DSP PCM tap. Both must observe bit-identical spine PCM throughout
an adaptive PCM16→Opus switch.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from rigplane.audio_bus import AudioBus
from rigplane.radio_protocol import AudioCapable
from rigplane.types import AudioCodec
from rigplane.web.handlers import AudioBroadcaster, AudioHandler
from rigplane.web.handlers.audio import DEGRADE_WINDOW_S
from rigplane.web.protocol import AUDIO_CODEC_OPUS
from rigplane.web.websocket import WebSocketConnection

# 20 ms @ 48 kHz mono s16le.
PCM_PAYLOAD = b"\x01\x02" * 960


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _FakeTranscoder:
    def pcm_to_opus(self, pcm: bytes) -> bytes:
        return b"opus-frame"


def _make_radio() -> tuple[Any, AudioBus]:
    radio = MagicMock(spec=AudioCapable)
    radio.capabilities = {"audio"}
    radio.audio_codec = AudioCodec.PCM_1CH_16BIT
    radio.audio_sample_rate = 48000
    radio.start_audio_rx_opus = AsyncMock()
    radio.stop_audio_rx_opus = AsyncMock()
    bus = AudioBus(radio)
    radio.audio_bus = bus
    return radio, bus


def _make_ws() -> MagicMock:
    ws = MagicMock(spec=WebSocketConnection)
    ws.send_text = AsyncMock()
    return ws


async def _inject(bus: AudioBus) -> None:
    pkt = MagicMock()
    pkt.data = PCM_PAYLOAD
    bus._on_opus_packet(pkt)
    await asyncio.sleep(0.1)


async def _storm(handler: AudioHandler, clock: _Clock) -> None:
    """Sustained degradation evidence past DEGRADE_WINDOW_S."""
    underruns = 0
    elapsed = 0.0
    while elapsed <= DEGRADE_WINDOW_S:
        underruns += 1
        await handler._handle_control({"type": "audio_stats", "underruns": underruns})
        clock.now += 1.5
        elapsed += 1.5
    await asyncio.sleep(0)


class TestT1aLosslessPathUnderAdaptation:
    async def test_bridge_and_taps_get_no_encoder_during_adaptive_switch(
        self,
    ) -> None:
        """While the controller degrades a WS client to Opus, the bridge
        bus subscriber and the post-DSP PCM tap keep receiving the spine
        PCM bit-identically; every encoder construction is keyed to the
        WS client only."""
        radio, bus = _make_radio()
        broadcaster = AudioBroadcaster(radio, adaptive_egress=True)
        clock = _Clock()
        broadcaster._adaptive_monotonic = clock

        tap_frames: list[bytes] = []
        broadcaster.set_pcm_tap(tap_frames.append)
        bridge_sub = bus.subscribe(name="audio-bridge")
        await bridge_sub.start()

        handler = AudioHandler(_make_ws(), radio, broadcaster)
        constructed: list[dict[str, Any]] = []

        def _factory(**kwargs: Any) -> _FakeTranscoder:
            constructed.append(kwargs)
            return _FakeTranscoder()

        with patch(
            "rigplane.web.handlers.audio.create_pcm_opus_transcoder",
            side_effect=_factory,
        ):
            await handler._start_rx(preferred_rx_codec=AUDIO_CODEC_OPUS)
            await _inject(bus)  # healthy: PCM16, no encoder anywhere
            assert constructed == []
            await _storm(handler, clock)  # controller flips WS client → Opus
            await _inject(bus)

        ws_client_id = id(handler._frame_queue)
        assert broadcaster._client_adaptive[ws_client_id].codec == AUDIO_CODEC_OPUS
        # T1a: the encoder pool is keyed by the WS client id ONLY — no
        # encoder is constructible on the bridge/digital path.
        assert set(broadcaster._client_opus_transcoders) == {ws_client_id}
        assert len(constructed) == 1
        # Lossless-class consumers observed bit-identical spine PCM
        # through the entire adaptive switch.
        assert tap_frames == [PCM_PAYLOAD, PCM_PAYLOAD]
        bridge_packets: list[bytes] = []
        while True:
            try:
                pkt = bridge_sub.get_nowait()
            except asyncio.QueueEmpty:
                break
            if pkt is not None:
                bridge_packets.append(pkt.data)
        assert bridge_packets == [PCM_PAYLOAD, PCM_PAYLOAD]
        bridge_sub.stop()

    async def test_no_encoder_constructed_while_links_healthy(self) -> None:
        """Flag ON + healthy link: the controller stays PCM16 and never
        constructs an encoder for anyone (ADR §3.6: zero cost in the
        common case)."""
        radio, bus = _make_radio()
        broadcaster = AudioBroadcaster(radio, adaptive_egress=True)
        handler = AudioHandler(_make_ws(), radio, broadcaster)
        with patch(
            "rigplane.web.handlers.audio.create_pcm_opus_transcoder",
        ) as factory:
            await handler._start_rx(preferred_rx_codec=AUDIO_CODEC_OPUS)
            await _inject(bus)

        factory.assert_not_called()
        assert broadcaster._client_opus_transcoders == {}
