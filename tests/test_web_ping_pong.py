"""Application-level ping/pong on the WS channels the web client pings.

The browser client (``frontend/src/lib/transport/ws-client.ts``) sends a JSON
``{"type": "ping"}`` on every ``WsChannel`` and re-arms its heartbeat deadline
only on received frames, so each channel it pings must answer with a frame.
"""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.web.handlers import ControlHandler, ScopeHandler
from rigplane.web.websocket import WS_OP_TEXT


def _sent_json(send_text: AsyncMock) -> list[object]:
    return [json.loads(call.args[0]) for call in send_text.await_args_list]


def _control_handler(ws: object) -> ControlHandler:
    return ControlHandler(ws, None, "9.9.9", "IC-7610")  # type: ignore[arg-type]


async def test_control_channel_answers_ping_with_pong() -> None:
    ws = SimpleNamespace(send_text=AsyncMock())
    await _control_handler(ws)._handle_text(json.dumps({"type": "ping"}))
    assert _sent_json(ws.send_text) == [{"type": "pong"}]


async def test_control_channel_unknown_type_still_logged_and_unanswered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = SimpleNamespace(send_text=AsyncMock())
    with caplog.at_level(logging.DEBUG, logger="rigplane.web.handlers.control"):
        await _control_handler(ws)._handle_text(json.dumps({"type": "bogus"}))
    assert "control: unknown message type: 'bogus'" in caplog.text
    ws.send_text.assert_not_awaited()


def _scope_ws(text: str) -> SimpleNamespace:
    recv = AsyncMock(side_effect=[(WS_OP_TEXT, text.encode("utf-8")), EOFError()])
    return SimpleNamespace(recv=recv, send_text=AsyncMock(), send_binary=AsyncMock())


@pytest.mark.parametrize("audio_mode", [False, True], ids=["scope", "audio-scope"])
async def test_scope_channel_answers_ping_with_pong(audio_mode: bool) -> None:
    ws = _scope_ws(json.dumps({"type": "ping"}))
    await ScopeHandler(ws, None, audio_mode=audio_mode).run()  # type: ignore[arg-type]
    assert _sent_json(ws.send_text) == [{"type": "pong"}]


async def test_scope_channel_other_control_message_unanswered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = _scope_ws(json.dumps({"type": "bogus"}))
    with caplog.at_level(logging.DEBUG, logger="rigplane.web.handlers.scope"):
        await ScopeHandler(ws, None).run()  # type: ignore[arg-type]
    assert "scope: control message: 'bogus'" in caplog.text
    ws.send_text.assert_not_awaited()
