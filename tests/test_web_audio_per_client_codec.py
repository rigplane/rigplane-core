"""Per-connection web egress codecs (MOR-584, ADR §3.6 problem P11).

Falsification suite: the broadcaster must encode egress PER CLIENT — a
PCM16-preferring browser must not force PCM16 onto a concurrently
connected Opus client (the old aggregate ``_web_codec`` did exactly
that). The PCM spine (bus → DSP → taps → bridge) stays PCM and never
grows an encoder (T1a digital-path invariant): only browser WS clients
get per-client codecs.
"""

from __future__ import annotations

import asyncio
import json
import struct
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from rigplane.audio_bus import AudioBus
from rigplane.radio_protocol import AudioCapable
from rigplane.types import AudioCodec
from rigplane.web.handlers import AudioBroadcaster, AudioHandler
from rigplane.web.protocol import (
    AUDIO_CODEC_OPUS,
    AUDIO_CODEC_PCM16,
    AUDIO_HEADER_SIZE,
    MSG_TYPE_AUDIO_RX,
    encode_audio_frame,
)
from rigplane.web.websocket import WebSocketConnection

# 20 ms @ 48 kHz mono s16le.
PCM_PAYLOAD = b"\x01\x02" * 960


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


class _FakeTranscoder:
    """Stands in for PcmOpusTranscoder — native libopus may be absent."""

    def __init__(self) -> None:
        self.frames: list[bytes] = []

    def pcm_to_opus(self, pcm: bytes) -> bytes:
        self.frames.append(bytes(pcm))
        return b"opus-frame"


async def _inject(bus: AudioBus, payload: bytes = PCM_PAYLOAD) -> None:
    pkt = MagicMock()
    pkt.data = payload
    bus._on_opus_packet(pkt)
    await asyncio.sleep(0.1)


class TestPerClientEgressCodec:
    async def test_mixed_clients_each_receive_their_own_codec(self) -> None:
        """Two concurrent clients: PCM16-preferring must NOT force PCM16
        on the Opus-preferring one (the aggregate-_web_codec regression)."""
        radio, bus = _make_radio()
        broadcaster = AudioBroadcaster(radio)
        h_pcm = AudioHandler(_make_ws(), radio, broadcaster)
        h_opus = AudioHandler(_make_ws(), radio, broadcaster)
        created: list[_FakeTranscoder] = []

        def _factory(**_kwargs: Any) -> _FakeTranscoder:
            t = _FakeTranscoder()
            created.append(t)
            return t

        with patch(
            "rigplane.web.handlers.audio.create_pcm_opus_transcoder",
            side_effect=_factory,
        ):
            await h_pcm._start_rx(preferred_rx_codec=AUDIO_CODEC_PCM16)
            await h_opus._start_rx(preferred_rx_codec=AUDIO_CODEC_OPUS)
            await _inject(bus)

        f_pcm = h_pcm._frame_queue.get_nowait()
        f_opus = h_opus._frame_queue.get_nowait()
        assert f_pcm[1] == AUDIO_CODEC_PCM16
        assert f_pcm[AUDIO_HEADER_SIZE:] == PCM_PAYLOAD
        assert f_opus[1] == AUDIO_CODEC_OPUS, (
            "PCM16 client forced PCM16 onto the Opus client — egress "
            "must be per-connection (MOR-584)"
        )
        assert f_opus[AUDIO_HEADER_SIZE:] == b"opus-frame"
        # One dedicated encoder, fed the shared post-DSP PCM frame.
        assert len(created) == 1
        assert created[0].frames == [PCM_PAYLOAD]
        # Both frames stem from the same packet → same sequence number.
        seq_pcm = struct.unpack_from("<H", f_pcm, 2)[0]
        seq_opus = struct.unpack_from("<H", f_opus, 2)[0]
        assert seq_pcm == seq_opus

    async def test_single_default_client_pcm16_frame_byte_identical(self) -> None:
        """The common case — one browser, no preference — stays byte-
        identical to the pre-MOR-584 wire format, and no encoder pool
        entry is ever created. The bare (non-async) ws mock also proves
        a legacy client that cannot take the audio_format ack still
        streams."""
        radio, bus = _make_radio()
        broadcaster = AudioBroadcaster(radio)
        handler = AudioHandler(MagicMock(spec=WebSocketConnection), radio, broadcaster)
        await handler._start_rx()
        await _inject(bus)

        frame = handler._frame_queue.get_nowait()
        expected = encode_audio_frame(
            MSG_TYPE_AUDIO_RX, AUDIO_CODEC_PCM16, 0, 480, 1, 20, PCM_PAYLOAD
        )
        assert frame == expected
        assert broadcaster._client_opus_transcoders == {}

    async def test_pcm_spine_and_bridge_path_never_get_an_encoder(self) -> None:
        """T1a boundary: the post-DSP tap (FFT scope) and a bus
        subscriber (how the AudioBridge consumes RX, upstream of the
        broadcaster) both see raw PCM; the encoder pool is keyed by WS
        client id only."""
        radio, bus = _make_radio()
        broadcaster = AudioBroadcaster(radio)
        tap_frames: list[bytes] = []
        broadcaster.set_pcm_tap(tap_frames.append)
        bridge_sub = bus.subscribe(name="audio-bridge")
        await bridge_sub.start()
        handler = AudioHandler(_make_ws(), radio, broadcaster)
        with patch(
            "rigplane.web.handlers.audio.create_pcm_opus_transcoder",
            return_value=_FakeTranscoder(),
        ):
            await handler._start_rx(preferred_rx_codec=AUDIO_CODEC_OPUS)
            await _inject(bus)

        assert tap_frames == [PCM_PAYLOAD]
        bridge_pkt = bridge_sub.get_nowait()
        assert bridge_pkt is not None and bridge_pkt.data == PCM_PAYLOAD
        assert set(broadcaster._client_opus_transcoders) == {
            id(handler._frame_queue)
        }, "encoders must exist for browser WS clients only (T1a)"
        bridge_sub.stop()

    async def test_disconnect_tears_down_client_encoder(self) -> None:
        radio, bus = _make_radio()
        broadcaster = AudioBroadcaster(radio)
        handler = AudioHandler(_make_ws(), radio, broadcaster)
        with patch(
            "rigplane.web.handlers.audio.create_pcm_opus_transcoder",
            return_value=_FakeTranscoder(),
        ):
            await handler._start_rx(preferred_rx_codec=AUDIO_CODEC_OPUS)
            await _inject(bus)
        assert len(broadcaster._client_opus_transcoders) == 1

        await broadcaster.unsubscribe(handler._frame_queue)
        assert broadcaster._client_opus_transcoders == {}
        assert broadcaster._client_rx_codec == {}

    async def test_reap_dead_clients_tears_down_client_encoder(self) -> None:
        radio, bus = _make_radio()
        broadcaster = AudioBroadcaster(radio)
        ws = _make_ws()
        handler = AudioHandler(ws, radio, broadcaster)
        with patch(
            "rigplane.web.handlers.audio.create_pcm_opus_transcoder",
            return_value=_FakeTranscoder(),
        ):
            await handler._start_rx(preferred_rx_codec=AUDIO_CODEC_OPUS)
            await _inject(bus)
        assert len(broadcaster._client_opus_transcoders) == 1

        ws.is_alive.return_value = False
        assert await broadcaster.reap_dead_clients() == 1
        assert broadcaster._client_opus_transcoders == {}
        assert broadcaster._client_rx_codec == {}

    async def test_opus_native_radio_passes_through_for_all_clients(self) -> None:
        """Opus-native radios (IC-705) stay un-decoded passthrough for
        every client regardless of preference — there is no PCM to
        re-encode (issue #762), so no pool encoder is created either."""
        radio, bus = _make_radio(codec=AudioCodec.OPUS_1CH)
        broadcaster = AudioBroadcaster(radio)
        handler = AudioHandler(_make_ws(), radio, broadcaster)
        await handler._start_rx(preferred_rx_codec=AUDIO_CODEC_PCM16)
        opus_payload = b"\x42" * 120
        await _inject(bus, opus_payload)

        frame = handler._frame_queue.get_nowait()
        assert frame[1] == AUDIO_CODEC_OPUS
        assert frame[AUDIO_HEADER_SIZE:] == opus_payload
        assert broadcaster._client_opus_transcoders == {}


class TestAudioFormatAck:
    async def test_ack_sent_with_negotiated_format(self) -> None:
        radio, bus = _make_radio()
        broadcaster = AudioBroadcaster(radio)
        ws = _make_ws()
        handler = AudioHandler(ws, radio, broadcaster)
        with patch(
            "rigplane.web.handlers.audio.create_pcm_opus_transcoder",
            return_value=_FakeTranscoder(),
        ):
            await handler._start_rx(preferred_rx_codec=AUDIO_CODEC_OPUS)

        sent = [json.loads(c.args[0]) for c in ws.send_text.await_args_list]
        acks = [m for m in sent if m.get("type") == "audio_format"]
        assert acks == [
            {
                "type": "audio_format",
                "codec": "opus",
                "sample_rate": 48000,
                "channels": 1,
                "frame_ms": 20,
            }
        ]

    async def test_default_client_ack_reports_pcm16(self) -> None:
        radio, bus = _make_radio()
        broadcaster = AudioBroadcaster(radio)
        ws = _make_ws()
        handler = AudioHandler(ws, radio, broadcaster)
        await handler._start_rx()

        sent = [json.loads(c.args[0]) for c in ws.send_text.await_args_list]
        acks = [m for m in sent if m.get("type") == "audio_format"]
        assert len(acks) == 1
        assert acks[0]["codec"] == "pcm16"

    async def test_ack_send_failure_does_not_break_rx(self) -> None:
        """An old client may close the text path or choke on the ack —
        absence of ack handling must not affect frame delivery."""
        radio, bus = _make_radio()
        broadcaster = AudioBroadcaster(radio)
        ws = _make_ws()
        ws.send_text = AsyncMock(side_effect=RuntimeError("legacy client"))
        handler = AudioHandler(ws, radio, broadcaster)
        await handler._start_rx()
        await _inject(bus)

        frame = handler._frame_queue.get_nowait()
        assert frame[1] == AUDIO_CODEC_PCM16
        assert frame[AUDIO_HEADER_SIZE:] == PCM_PAYLOAD
