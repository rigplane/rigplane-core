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

from rigplane.web.handlers import (
    AudioBroadcaster,
    AudioHandler,
    ControlHandler,
    ScopeHandler,
)
from rigplane.ws import WS_OP_TEXT
from test_web_audio_link_quality import _make_radio, _make_ws


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


async def test_scope_channel_answers_ping_with_pong() -> None:
    ws = _scope_ws(json.dumps({"type": "ping"}))
    await ScopeHandler(ws).run()
    assert _sent_json(ws.send_text) == [{"type": "pong"}]


async def test_scope_run_skips_non_utf8_text_frame() -> None:
    ws = SimpleNamespace(
        recv=AsyncMock(
            side_effect=[
                (WS_OP_TEXT, b"\xff\xfe{"),
                (WS_OP_TEXT, json.dumps({"type": "ping"}).encode("utf-8")),
                EOFError(),
            ]
        ),
        send_text=AsyncMock(),
        send_binary=AsyncMock(),
    )
    await ScopeHandler(ws).run()  # type: ignore[arg-type]
    assert _sent_json(ws.send_text) == [{"type": "pong"}]


async def test_scope_channel_other_control_message_unanswered(
    caplog: pytest.LogCaptureFixture,
) -> None:
    ws = _scope_ws(json.dumps({"type": "bogus"}))
    with caplog.at_level(logging.DEBUG, logger="rigplane.web.handlers.scope"):
        await ScopeHandler(ws).run()  # type: ignore[arg-type]
    ws.send_text.assert_not_awaited()


@pytest.mark.parametrize("text", ["not json", "[1]", '"x"'])
@pytest.mark.parametrize("channel", ["control", "scope"])
async def test_non_json_and_non_object_text_gets_no_reply_and_does_not_raise(
    channel: str, text: str
) -> None:
    if channel == "control":
        ws = SimpleNamespace(send_text=AsyncMock())
        await _control_handler(ws)._handle_text(text)
    else:
        ws = _scope_ws(text)
        await ScopeHandler(ws).run()  # type: ignore[arg-type]
    if channel == "scope":
        assert _sent_json(ws.send_text)[0]["type"] == "error"
    else:
        ws.send_text.assert_not_awaited()


async def test_audio_channel_ping_gets_no_reply() -> None:
    radio, _ = _make_radio()
    ws = _make_ws()
    handler = AudioHandler(ws, radio, AudioBroadcaster(radio))
    await handler._handle_control({"type": "ping"})
    ws.send_text.assert_not_awaited()


async def test_scope_pong_does_not_require_a_subscription() -> None:
    ws = _scope_ws(json.dumps({"type": "ping"}))
    handler = ScopeHandler(ws)
    await handler.run()
    assert not handler._streams
    assert _sent_json(ws.send_text) == [{"type": "pong"}]
