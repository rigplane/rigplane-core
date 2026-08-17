"""Browser TX codec negotiation on the audio WS (MOR-1791).

The defect: when the server cannot decode Opus (no native opus codec), it
already knows at ``audio_start direction=tx`` that browser PCM16 TX would
work — and says so in the log — but nothing tells the client.  The browser
keeps emitting Opus, every frame is dropped fail-closed, the radio keys, and
no modulation reaches the air.

The fix is an additive server→client ack on the EXISTING audio control
channel, mirroring the RX-side ``audio_format`` ack (MOR-584): one
``audio_tx_format`` message per TX start naming the codec this server can
actually accept.  Nothing is versioned and nothing is removed — clients that
ignore audio-WS text frames behave exactly as before.

Host independence: these tests never ask whether a native Opus library
exists on the machine running them.  Decoder availability is injected by
stubbing ``AudioHandler._ensure_tx_transcoder``, so both branches are
exercised on every host.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from _order_sensitive_radios import LanLikeRadio

from rigplane.audio.session import AudioSession
from rigplane.web.handlers import AudioHandler
from rigplane.web.protocol import (
    AUDIO_CODEC_OPUS,
    AUDIO_CODEC_PCM16,
    AUDIO_HEADER_SIZE,
    MSG_TYPE_AUDIO_TX,
)


class _SessionLanRadio(LanLikeRadio):
    """LAN-like stub + radio-owned session singleton (MOR-579 shape)."""

    capabilities = {"audio", "tx"}
    backend_id = "rigplane"
    has_usb_audio = True
    audio_sample_rate = 48000

    def __init__(self) -> None:
        super().__init__()
        self.pushed: list[bytes] = []
        self._audio_session: AudioSession | None = None

    @property
    def audio_session(self) -> AudioSession:
        if self._audio_session is None:
            self._audio_session = AudioSession(self)
        return self._audio_session

    async def push_tx(self, audio_data: bytes) -> None:
        await super().push_tx(audio_data)  # raises unless TX is armed
        self.pushed.append(audio_data)


class _CapturingWs:
    """WS double capturing the JSON text frames the handler sends."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []
        self.recv = AsyncMock()
        self.send_binary = AsyncMock()

    async def send_text(self, text: str) -> None:
        self.sent.append(json.loads(text))

    def messages(self, msg_type: str) -> list[dict[str, Any]]:
        return [m for m in self.sent if m.get("type") == msg_type]


def _make_handler(
    radio: _SessionLanRadio, *, opus_decode: bool
) -> tuple[AudioHandler, _CapturingWs]:
    """Handler whose Opus decoder availability is injected, not discovered."""
    ws = _CapturingWs()
    handler = AudioHandler(ws, radio, None)

    def _ensure(*, _ok: bool = opus_decode) -> bool:
        if _ok:
            handler._transcoder = SimpleNamespace(
                opus_to_pcm=lambda data: b"pcm:" + data
            )
            handler._transcoder_rate = radio.audio_sample_rate
            return True
        handler._transcoder = None
        handler._transcoder_rate = 0
        return False

    handler._ensure_tx_transcoder = _ensure  # type: ignore[method-assign]
    return handler, ws


def _tx_frame(codec: int, payload: bytes) -> bytes:
    return (
        bytes([MSG_TYPE_AUDIO_TX, codec]) + b"\x00" * (AUDIO_HEADER_SIZE - 2) + payload
    )


async def _start_tx(handler: AudioHandler) -> None:
    await handler._handle_control({"type": "audio_start", "direction": "tx"})


async def test_tx_start_advertises_pcm16_when_opus_decoder_unavailable() -> None:
    """No decoder → the ack names PCM16, the codec that actually reaches air."""
    radio = _SessionLanRadio()
    handler, ws = _make_handler(radio, opus_decode=False)

    await _start_tx(handler)

    acks = ws.messages("audio_tx_format")
    assert len(acks) == 1, "exactly one TX codec ack per audio_start"
    assert acks[0]["codec"] == "pcm16"
    assert acks[0]["opus_decode"] is False
    assert acks[0]["sample_rate"] == 48000
    # The session is still armed: a missing decoder never denies TX (MOR-1173).
    assert handler._tx_active is True
    assert radio.audio_session.tx_demand == 1


async def test_tx_start_advertises_opus_when_decoder_available() -> None:
    """Decoder present → the ack names Opus; the client keeps today's choice."""
    radio = _SessionLanRadio()
    handler, ws = _make_handler(radio, opus_decode=True)

    await _start_tx(handler)

    acks = ws.messages("audio_tx_format")
    assert len(acks) == 1
    assert acks[0]["codec"] == "opus"
    assert acks[0]["opus_decode"] is True


async def test_advertised_pcm16_frames_reach_the_radio_with_zero_drops() -> None:
    """The advertised fallback is the one that works: PCM16 in, PCM16 pushed."""
    radio = _SessionLanRadio()
    handler, _ws = _make_handler(radio, opus_decode=False)
    await _start_tx(handler)

    for i in range(5):
        await handler._handle_tx_audio(_tx_frame(AUDIO_CODEC_PCM16, b"frame-%d" % i))

    assert radio.pushed == [b"frame-%d" % i for i in range(5)]
    assert handler._tx_warn_counts == {}, "no drop warnings for the advertised codec"


async def test_opus_frames_remain_dropped_fail_closed_without_a_decoder() -> None:
    """Fail-closed is unchanged: an undecodable frame is never reported applied."""
    radio = _SessionLanRadio()
    handler, _ws = _make_handler(radio, opus_decode=False)
    await _start_tx(handler)

    await handler._handle_tx_audio(_tx_frame(AUDIO_CODEC_OPUS, b"opus-payload"))

    assert radio.pushed == []
    assert handler._tx_warn_counts.get("dropped_no_transcoder") == 1


async def test_no_tx_codec_ack_when_tx_audio_is_unavailable() -> None:
    """A denied TX start advertises nothing — there is no codec to negotiate."""
    ws = _CapturingWs()
    handler = AudioHandler(ws, None, None)

    await _start_tx(handler)

    assert ws.messages("audio_tx_format") == []
    assert handler._tx_active is False
