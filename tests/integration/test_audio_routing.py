"""Integration tests: dual-RX audio routing end-to-end (issue #754).

Exercises the full chain from a WS ``audio_config`` message through the
handler to a CI-V Phones L/R Mix emission + WS echo + broadcaster codec
cache invalidation. Builds on:

- #755 — backend audio_config WS handler + CI-V routing.
- #766 — codec/channel change detection triggered by audio_config.
- #767 / #768 — Option-B ``frame_ms`` + dead-code cleanup.

Marked ``integration`` + ``mock_integration``. The ``mock_integration``
marker is what keeps these off hardware — ``conftest.py``'s
``pytest_collection_modifyitems`` skips over them without ever adding a
skip marker — so they run in the standard suite and in all three CI
workflows, with no real hardware required.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.audio_bus import AudioBus
from rigplane.radio_protocol import AudioCapable
from rigplane.types import AudioCodec
from rigplane.web.handlers import AudioBroadcaster, AudioHandler
from rigplane.web.websocket import WebSocketConnection

pytestmark = [pytest.mark.integration, pytest.mark.mock_integration]


def _build(receiver_count: int = 2) -> tuple[AudioHandler, AudioBroadcaster, Any]:
    """Construct the full AudioHandler + AudioBroadcaster + AudioBus chain
    against a MagicMock radio. Returns (handler, broadcaster, mock_radio).

    Default shape is an IC-7610-like dual-RX profile: declares both the
    ``audio`` and ``lan_dual_rx_audio_routing`` capabilities so the
    handler actually exercises the Phones L/R Mix CI-V path.  Issue #799.
    """
    mock_ws = MagicMock(spec=WebSocketConnection)
    mock_ws.send_text = AsyncMock()
    mock_radio = MagicMock(spec=AudioCapable)
    caps = {"audio"}
    if receiver_count >= 2:
        caps.add("lan_dual_rx_audio_routing")
    mock_radio.capabilities = caps
    mock_radio.audio_codec = AudioCodec.PCM_1CH_16BIT
    mock_radio.audio_sample_rate = 48000
    mock_radio.start_audio_rx_opus = AsyncMock()
    mock_radio.stop_audio_rx_opus = AsyncMock()
    mock_radio.send_civ = AsyncMock()
    mock_radio.profile = SimpleNamespace(receiver_count=receiver_count)

    bus = AudioBus(mock_radio)
    mock_radio.audio_bus = bus

    broadcaster = AudioBroadcaster(mock_radio)
    handler = AudioHandler(mock_ws, mock_radio, broadcaster)
    return handler, broadcaster, mock_radio


class TestAudioConfigEndToEnd:
    """WS audio_config → CI-V Phones L/R Mix + WS echo + broadcaster
    codec-cache invalidation."""

    @pytest.mark.parametrize(
        "focus,split_stereo",
        [
            ("main", False),
            ("main", True),
            ("sub", False),
            ("sub", True),
            ("both", False),
            ("both", True),
        ],
    )
    async def test_focus_split_matrix_emits_mix_off(
        self, focus: str, split_stereo: bool
    ) -> None:
        """Any (focus, split_stereo) → Phones L/R Mix OFF (0x00).

        Post-#792 contract (epic #787): the radio always sends separated
        L=MAIN/R=SUB stereo whenever a 2ch codec is active, so the backend
        unconditionally keeps Mix OFF.  ``focus`` × ``split_stereo``
        resolve on the frontend via WebAudio gain + pan (rx-player.ts).

        Earlier revisions sent ``0x02`` / ``0x03`` for ``focus=sub`` /
        ``focus=both`` — values the radio silently ignored per the CI-V
        reference.  Those dead bytes are gone; this test pins the
        replacement contract.
        """
        handler, broadcaster, mock_radio = _build()
        await handler._handle_control(
            {"type": "audio_config", "focus": focus, "split_stereo": split_stereo}
        )

        # CI-V byte is always Mix OFF.
        mock_radio.send_civ.assert_awaited_once_with(
            0x1A,
            sub=0x05,
            data=bytes([0x00, 0x72, 0x00]),
            wait_response=False,
        )

        # Echo JSON round-trip.
        handler._ws.send_text.assert_awaited_once()
        echoed = json.loads(handler._ws.send_text.await_args.args[0])
        assert echoed == {
            "type": "audio_config",
            "focus": focus,
            "split_stereo": split_stereo,
            "applied": True,
        }

        # Codec cache invalidated on the broadcaster (issue #766 hook).
        assert broadcaster._codec_stale is True

    async def test_invalid_focus_returns_error_no_civ_no_invalidate(self) -> None:
        handler, broadcaster, mock_radio = _build()
        await handler._handle_control(
            {"type": "audio_config", "focus": "left", "split_stereo": False}
        )

        mock_radio.send_civ.assert_not_awaited()
        assert broadcaster._codec_stale is False

        handler._ws.send_text.assert_awaited_once()
        sent = json.loads(handler._ws.send_text.await_args.args[0])
        assert sent["type"] == "error"
        assert "invalid focus" in sent["message"]

    async def test_single_rx_profile_silent_noop(self) -> None:
        handler, broadcaster, mock_radio = _build(receiver_count=1)
        await handler._handle_control(
            {"type": "audio_config", "focus": "main", "split_stereo": False}
        )

        mock_radio.send_civ.assert_not_awaited()
        handler._ws.send_text.assert_not_awaited()
        assert broadcaster._codec_stale is False

    async def test_invalidation_picked_up_on_next_packet(self) -> None:
        """Full chain: audio_config → invalidation → next packet refreshes
        codec cache (channels flips mono→stereo)."""
        import asyncio

        handler, broadcaster, mock_radio = _build()
        await handler._start_rx()  # start relay loop

        # Inject mono packet first — broadcaster caches mono.
        mono_pkt = MagicMock()
        mono_pkt.data = b"\x00\x01" * 960  # 1920 B PCM16 mono @ 48k = 20 ms
        mock_radio.audio_bus._on_opus_packet(mono_pkt)
        await asyncio.sleep(0.05)
        handler._frame_queue.get_nowait()  # drain
        assert broadcaster._channels == 1

        # Flip radio to stereo; issue audio_config which triggers invalidate.
        mock_radio.audio_codec = AudioCodec.PCM_2CH_16BIT
        await handler._handle_control(
            {"type": "audio_config", "focus": "both", "split_stereo": True}
        )
        assert broadcaster._codec_stale is True

        # Inject stereo packet — next relay iteration refreshes codec state.
        stereo_pkt = MagicMock()
        stereo_pkt.data = b"\x00\x01" * 1920  # 3840 B PCM16 stereo @ 48k = 20 ms
        mock_radio.audio_bus._on_opus_packet(stereo_pkt)
        await asyncio.sleep(0.05)

        assert broadcaster._channels == 2
        assert broadcaster._codec_stale is False
