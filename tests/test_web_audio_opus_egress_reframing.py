"""Opus egress PCM→20 ms reframing (MOR-596).

Falsification suite: real radio RX packets are not Opus-frame-aligned
(IC-7610 LAN @48k stereo ≈1280 B ≈6.67 ms), but ``PcmOpusTranscoder``
accepts EXACTLY one fixed frame per encode.  Per-packet transcode
therefore raised ``AudioFormatError`` on every frame and silently fell
back to PCM16 — an Opus-egress client on a PCM radio never received
Opus.  The broadcaster must buffer s16le per client and emit exact
20 ms Opus frames (N packets → M frames); PCM16 and Opus-native
pass-through stay strictly 1:1.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rigplane.audio_bus import AudioBus
from rigplane.core.exceptions import AudioFormatError
from rigplane.radio_protocol import AudioCapable
from rigplane.types import AudioCodec
from rigplane.web.handlers import AudioBroadcaster
from rigplane.web.protocol import (
    AUDIO_CODEC_OPUS,
    AUDIO_CODEC_PCM16,
    AUDIO_HEADER_SIZE,
)
from rigplane.web.websocket import WebSocketConnection

# IC-7610 LAN-like RX packet: 1280 B @ 48 kHz stereo s16le ≈ 6.67 ms.
PACKET_BYTES = 1280
# One 20 ms egress frame @ 48 kHz stereo s16le.
FRAME_BYTES = 48000 * 20 // 1000 * 2 * 2  # 3840

_FACTORY = "rigplane.web.handlers.audio.create_pcm_opus_transcoder"


class _StrictFakeTranscoder:
    """Mimics PcmOpusTranscoder's fixed-frame contract without libopus."""

    def __init__(self, frame_bytes: int) -> None:
        self.frame_bytes = frame_bytes
        self.chunks: list[bytes] = []

    def pcm_to_opus(self, pcm: bytes) -> bytes:
        self.chunks.append(bytes(pcm))
        if len(pcm) != self.frame_bytes:
            raise AudioFormatError(
                f"PCM frame size mismatch: expected {self.frame_bytes}, got {len(pcm)}."
            )
        return b"opus-frame"


def _strict_factory(created: list[_StrictFakeTranscoder]) -> Any:
    def factory(*, sample_rate: int, channels: int, frame_ms: int) -> Any:
        if frame_ms not in (10, 20, 40, 60):
            raise AudioFormatError(f"Unsupported frame_ms={frame_ms}.")
        t = _StrictFakeTranscoder(sample_rate * frame_ms // 1000 * channels * 2)
        created.append(t)
        return t

    return factory


def _make_radio(
    codec: AudioCodec = AudioCodec.PCM_2CH_16BIT, sample_rate: int = 48000
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


async def _inject(bus: AudioBus, payloads: list[bytes]) -> None:
    for payload in payloads:
        pkt = MagicMock()
        pkt.data = payload
        bus._on_opus_packet(pkt)
    await asyncio.sleep(0.1)


def _drain(queue: asyncio.Queue[bytes]) -> list[tuple[int, int, bytes]]:
    """Drain (codec, header_frame_ms, payload) from a client queue."""
    frames: list[tuple[int, int, bytes]] = []
    while True:
        try:
            f = queue.get_nowait()
        except asyncio.QueueEmpty:
            return frames
        frames.append((f[1], f[7], f[AUDIO_HEADER_SIZE:]))


class TestOpusEgressReframing:
    async def test_non_aligned_packets_reframe_to_exact_20ms_opus(self) -> None:
        """8×1280 B → exactly floor(10240/3840)=2 Opus frames, every
        encode fed exactly FRAME_BYTES, the remainder carried over to the
        next packet, and NO PCM16 fallback (codec stays 0x01)."""
        radio, bus = _make_radio()
        broadcaster = AudioBroadcaster(radio)
        created: list[_StrictFakeTranscoder] = []
        packets = [bytes([0x10 + i]) * PACKET_BYTES for i in range(8)]
        with patch(_FACTORY, side_effect=_strict_factory(created)):
            queue = await broadcaster.subscribe(
                ws=_make_ws(), preferred_rx_codec=AUDIO_CODEC_OPUS
            )
            await _inject(bus, packets)
            frames = _drain(queue)
            assert [c for c, _, _ in frames] == [AUDIO_CODEC_OPUS] * 2, (
                "Opus egress fell back to PCM16 — non-aligned radio packets "
                "must be reframed, not encoded per packet (MOR-596)"
            )
            assert all(ms == 20 for _, ms, _ in frames)
            stream = b"".join(packets)
            assert len(created) == 1
            assert all(len(c) == FRAME_BYTES for c in created[0].chunks)
            assert created[0].chunks == [stream[:3840], stream[3840:7680]]
            # Remainder (2560 B) is retained: one more packet completes
            # the third frame.
            await _inject(bus, [bytes([0x99]) * PACKET_BYTES])
            stream += bytes([0x99]) * PACKET_BYTES
            assert _drain(queue) == [(AUDIO_CODEC_OPUS, 20, b"opus-frame")]
            assert created[0].chunks[2] == stream[7680:11520]

    async def test_pcm16_client_stays_one_to_one(self) -> None:
        radio, bus = _make_radio()
        broadcaster = AudioBroadcaster(radio)
        queue = await broadcaster.subscribe(
            ws=_make_ws(), preferred_rx_codec=AUDIO_CODEC_PCM16
        )
        packet = b"\x01\x02" * (PACKET_BYTES // 2)
        await _inject(bus, [packet] * 3)
        frames = _drain(queue)
        assert [(c, p) for c, _, p in frames] == [(AUDIO_CODEC_PCM16, packet)] * 3
        assert broadcaster._client_opus_transcoders == {}
        assert broadcaster._client_opus_pcm_buffers == {}

    async def test_codec_switch_to_pcm16_clears_accumulator(self) -> None:
        """Adaptive Opus→PCM16 switch must flush the partial frame; a
        later switch back to Opus must not prepend stale PCM."""
        radio, bus = _make_radio()
        broadcaster = AudioBroadcaster(radio, adaptive_egress=True)
        broadcaster._adaptive_monotonic = lambda: 1000.0
        created: list[_StrictFakeTranscoder] = []
        with patch(_FACTORY, side_effect=_strict_factory(created)):
            queue = await broadcaster.subscribe(
                ws=_make_ws(), preferred_rx_codec=AUDIO_CODEC_OPUS
            )
            client_id = id(queue)
            state = broadcaster._client_adaptive[client_id]
            state.codec = AUDIO_CODEC_OPUS
            state.last_switch = 1000.0  # inside dwell — controller holds
            await _inject(bus, [b"\xaa" * PACKET_BYTES])  # partial: buffered
            assert _drain(queue) == []
            broadcaster._adaptive_switch(client_id, state, AUDIO_CODEC_PCM16, 1000.0)
            assert broadcaster._client_opus_pcm_buffers == {}
            broadcaster._adaptive_switch(client_id, state, AUDIO_CODEC_OPUS, 1000.0)
            await _inject(bus, [b"\xbb" * PACKET_BYTES] * 3)
        frames = _drain(queue)
        assert [c for c, _, _ in frames] == [AUDIO_CODEC_OPUS]
        assert created[-1].chunks == [b"\xbb" * FRAME_BYTES]

    async def test_unsubscribe_clears_accumulator(self) -> None:
        radio, bus = _make_radio()
        broadcaster = AudioBroadcaster(radio)
        with patch(_FACTORY, side_effect=_strict_factory([])):
            queue = await broadcaster.subscribe(
                ws=_make_ws(), preferred_rx_codec=AUDIO_CODEC_OPUS
            )
            await _inject(bus, [b"\xaa" * PACKET_BYTES])
            assert broadcaster._client_opus_pcm_buffers != {}
            await broadcaster.unsubscribe(queue)
        assert broadcaster._client_opus_pcm_buffers == {}

    async def test_format_change_does_not_concatenate_mismatched_pcm(self) -> None:
        """A stereo partial frame must not prefix mono PCM after a codec
        state refresh (invalidate_codec_state path)."""
        radio, bus = _make_radio()
        broadcaster = AudioBroadcaster(radio)
        created: list[_StrictFakeTranscoder] = []
        with patch(_FACTORY, side_effect=_strict_factory(created)):
            queue = await broadcaster.subscribe(
                ws=_make_ws(), preferred_rx_codec=AUDIO_CODEC_OPUS
            )
            await _inject(bus, [b"\xaa" * PACKET_BYTES])  # stereo partial
            radio.audio_codec = AudioCodec.PCM_1CH_16BIT
            broadcaster.invalidate_codec_state()
            mono_20ms = b"\xbb" * (48000 * 20 // 1000 * 2)  # 1920 B
            await _inject(bus, [mono_20ms])
        frames = _drain(queue)
        assert [c for c, _, _ in frames] == [AUDIO_CODEC_OPUS]
        assert created[-1].chunks == [mono_20ms]

    async def test_opus_native_radio_passthrough_unchanged(self) -> None:
        radio, bus = _make_radio(codec=AudioCodec.OPUS_1CH)
        broadcaster = AudioBroadcaster(radio)
        queue = await broadcaster.subscribe(
            ws=_make_ws(), preferred_rx_codec=AUDIO_CODEC_OPUS
        )
        opus_packet = b"\x42" * 120
        await _inject(bus, [opus_packet] * 2)
        frames = _drain(queue)
        assert [(c, p) for c, _, p in frames] == [(AUDIO_CODEC_OPUS, opus_packet)] * 2
        assert broadcaster._client_opus_transcoders == {}
        assert broadcaster._client_opus_pcm_buffers == {}


def _libopus_available() -> bool:
    try:
        import opuslib  # noqa: F401
    except Exception:
        return False
    return True


@pytest.mark.skipif(not _libopus_available(), reason="native libopus unavailable")
async def test_real_opus_frames_decode_back_to_20ms() -> None:
    """End-to-end with real libopus: emitted frames are valid Opus and
    decode back to exactly one 20 ms PCM frame each."""
    from rigplane.audio._transcoder import create_pcm_opus_transcoder

    radio, bus = _make_radio()
    broadcaster = AudioBroadcaster(radio)
    queue = await broadcaster.subscribe(
        ws=_make_ws(), preferred_rx_codec=AUDIO_CODEC_OPUS
    )
    await _inject(bus, [b"\x00" * PACKET_BYTES] * 6)
    frames = _drain(queue)
    assert [c for c, _, _ in frames] == [AUDIO_CODEC_OPUS] * 2
    decoder = create_pcm_opus_transcoder(sample_rate=48000, channels=2, frame_ms=20)
    for _, _, payload in frames:
        assert len(decoder.opus_to_pcm(payload)) == FRAME_BYTES
