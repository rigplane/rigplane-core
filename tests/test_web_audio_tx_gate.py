"""Web TX audio gate: browser mic frames reach the rig only while keyed (MOR-2870).

The audio bridge got the fail-closed rule in MOR-2863 (``_tx_gate_is_closed``
fed by ``WebServer._bridge_tx_gate_open``); this file pins the same rule on
the web path. ``_handle_tx_audio`` must consult that same predicate before a
browser frame may reach ``push_tx``: the client's ``audio_start
direction=tx`` (``_tx_active``) arms the stream, but it must never be what
authorizes RF-bound audio. The gate is open only while the managed TX intent
is keyed or a fresh ``ObservedPtt.ON`` is observed.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock

from test_managed_tx_web_projection import _GatePort, _gate_authority
from test_web_audio_tx_session import _SessionLanRadio, _pcm_tx_frame, _start_tx

from rigplane.core.state_pipeline_contracts import Observation, SourceMetadata
from rigplane.core.tx_observation import OBSERVED_PTT_PATH, ObservedPtt
from rigplane.web.handlers import AudioHandler
from rigplane.web.server import WebConfig, WebServer

_SOURCE = SourceMetadata(source="poll_response", provider="test")


def _gated_server(managed: object) -> WebServer:
    server = WebServer(None, WebConfig(host="127.0.0.1", port=0, radio_model="IC-7610"))
    server._production_managed_tx_port = _GatePort(managed)  # type: ignore[assignment]
    return server


def _make_handler(radio: _SessionLanRadio, server: WebServer) -> AudioHandler:
    ws = SimpleNamespace(recv=AsyncMock(), send_binary=AsyncMock())
    return AudioHandler(ws, radio, None, tx_gate=server._bridge_tx_gate_open)


async def test_web_tx_frames_follow_the_managed_key() -> None:
    """``_tx_active`` arms the stream; the managed key is what opens the rig."""
    managed = _gate_authority()
    try:
        server = _gated_server(managed)
        radio = _SessionLanRadio()
        handler = _make_handler(radio, server)
        await _start_tx(handler)
        assert handler._tx_active is True

        # Browser sequence reality (managed-controller.#sendPttOn): mic
        # capture and ``audio_start direction=tx`` complete BEFORE the
        # ``ptt_on`` key-down, so early frames can arrive while the rig is
        # not yet transmitting — the gate drops them.
        await handler._handle_tx_audio(_pcm_tx_frame(b"before-key"))
        assert radio.pushed == []

        # After the web key-down the very next frame passes at once: no
        # added delay, no buffering, no clipped start.
        await managed.ptt_down("web")
        await handler._handle_tx_audio(_pcm_tx_frame(b"while-keyed"))
        assert radio.pushed == [b"while-keyed"]

        # Server-side release (operator release / server TOT) while the
        # browser's ``audio_stop`` is late or lost: ``_tx_active`` stays
        # true, but RigPlane no longer holds the key — the next frame must
        # not reach the radio's push_tx.
        await managed.ptt_up("web")
        assert handler._tx_active is True
        await handler._handle_tx_audio(_pcm_tx_frame(b"after-release"))
        assert radio.pushed == [b"while-keyed"]

        # Dropped frames are counted per reason key (MOR-1788 plumbing).
        assert handler._tx_warn_counts["tx_gate_closed"] == 2
    finally:
        await managed.close()


async def test_web_tx_frame_dropped_when_observation_goes_stale() -> None:
    """A stale ``ObservedPtt.ON`` never keeps the web gate open (fail closed)."""
    managed = _gate_authority()
    try:
        server = _gated_server(managed)
        radio = _SessionLanRadio()
        handler = _make_handler(radio, server)
        await _start_tx(handler)

        # Intent stays RX; a FRESH observed ON opens the gate without a key.
        server.command_state_store.apply_current(
            Observation(
                path=OBSERVED_PTT_PATH,
                value=ObservedPtt.ON,
                source=_SOURCE,
                timestamp_monotonic=time.monotonic(),
                max_age=0.05,
            )
        )
        await handler._handle_tx_audio(_pcm_tx_frame(b"fresh-on"))
        assert radio.pushed == [b"fresh-on"]

        # The observation goes stale (→ UNKNOWN) while the intent is RX —
        # the gate closes and the frame is dropped.
        await asyncio.sleep(0.15)
        await handler._handle_tx_audio(_pcm_tx_frame(b"stale-on"))
        assert radio.pushed == [b"fresh-on"]
        assert handler._tx_warn_counts["tx_gate_closed"] == 1
    finally:
        await managed.close()
