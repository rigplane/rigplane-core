"""Reconnect subscriber coalescing on the RX broadcaster (MOR-924).

Falsification suite for the silent-web-audio regression: after a
``soft_reconnect`` / audio re-arm the browser's audio WS drops and the
client reconnects, re-sending ``audio_start`` with the SAME stable
``client_id``. Before the fix the broadcaster registered the new
subscription while the prior one's half-open socket was still
``is_alive()`` (pong timeout up to 60 s), so RX fanned out to TWO
subscribers on TWO sockets — the live tab's player and an abandoned
zombie — and stayed at ``total=2`` for the rest of the session until a
full app restart (the user-observed symptom).

The broadcaster must instead drop the prior subscription carrying the
same identity SYNCHRONOUSLY on the new subscribe, converging to exactly
one subscriber per browser, and must close the superseded socket so its
handler unwinds.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

from rigplane.audio_bus import AudioBus
from rigplane.radio_protocol import AudioCapable
from rigplane.types import AudioCodec
from rigplane.web.handlers import AudioBroadcaster
from rigplane.web.websocket import WebSocketConnection


def _make_radio() -> tuple[Any, AudioBus]:
    radio = MagicMock(spec=AudioCapable)
    radio.capabilities = {"audio"}
    radio.audio_codec = AudioCodec.PCM_1CH_16BIT
    radio.audio_sample_rate = 48000
    bus = AudioBus(radio)
    radio.audio_bus = bus
    return radio, bus


def _make_ws(alive: bool = True) -> MagicMock:
    ws = MagicMock(spec=WebSocketConnection)
    ws.send_text = AsyncMock()
    ws.send_binary = AsyncMock()
    ws.close = AsyncMock()
    # Half-open zombie: the socket still reports alive (pong not yet timed
    # out) — the exact condition under which reap_dead_clients is a no-op.
    ws.is_alive.return_value = alive
    return ws


class TestReconnectCoalescing:
    async def test_same_identity_reconnect_drops_prior_subscriber(self) -> None:
        """A reconnect with the same client_id converges to exactly 1 client."""
        radio, _bus = _make_radio()
        broadcaster = AudioBroadcaster(radio)

        old_ws = _make_ws(alive=True)  # zombie: still "alive" at TCP level
        old_q = await broadcaster.subscribe(ws=old_ws, identity="tab-A")
        assert len(broadcaster._clients) == 1

        # Same browser reconnects (new socket, SAME identity) before the
        # old half-open socket is reaped.
        new_ws = _make_ws(alive=True)
        new_q = await broadcaster.subscribe(ws=new_ws, identity="tab-A")

        # Converged to exactly one subscriber — the new one.
        assert len(broadcaster._clients) == 1
        assert id(new_q) in broadcaster._clients
        assert id(old_q) not in broadcaster._clients
        # The superseded socket was closed so its handler unwinds.
        old_ws.close.assert_awaited_once()
        new_ws.close.assert_not_called()
        # No per-client state leaked for the dropped client.
        assert id(old_q) not in broadcaster._client_ws
        assert id(old_q) not in broadcaster._client_identity

    async def test_distinct_identities_are_not_coalesced(self) -> None:
        """Two genuinely different browsers keep two subscribers."""
        radio, _bus = _make_radio()
        broadcaster = AudioBroadcaster(radio)

        ws_a = _make_ws()
        ws_b = _make_ws()
        await broadcaster.subscribe(ws=ws_a, identity="tab-A")
        await broadcaster.subscribe(ws=ws_b, identity="tab-B")

        assert len(broadcaster._clients) == 2
        ws_a.close.assert_not_called()
        ws_b.close.assert_not_called()

    async def test_no_identity_preserves_legacy_behavior(self) -> None:
        """Clients that send no client_id are never coalesced (old clients)."""
        radio, _bus = _make_radio()
        broadcaster = AudioBroadcaster(radio)

        await broadcaster.subscribe(ws=_make_ws(), identity=None)
        await broadcaster.subscribe(ws=_make_ws(), identity=None)

        assert len(broadcaster._clients) == 2
