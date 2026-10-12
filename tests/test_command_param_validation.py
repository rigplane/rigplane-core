"""Malformed WS command parameters are client errors, not programmer errors.

Since #4077, ``ControlHandler._send_command_failure`` logs
``AttributeError``/``TypeError``/``NameError`` at ERROR with a traceback —
that class is reserved for genuine bugs in the command path. Malformed
client frames must instead fail as client errors: exactly one failure reply
with error ``command_failed`` whose message names the offending field (or
says ``params must be an object``, the same wording the HTTP single-command
ingress in ``web/server.py: WebServer._handle_http_single_command`` uses),
one WARNING record, and no ERROR record.

Covered here: ``params: null``/``params: []`` rejected at
``ControlHandler._handle_command`` entry (before rate limiting/coalescing),
``set_freq`` with a ``freq`` of the wrong JSON type (coerced in
``core/command_service.py: command_intent_from_request``), and ``ptt`` with
``params: null``. The control row pins that a genuine programmer error — an
executor raising ``AttributeError`` — is still logged at ERROR.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from rigplane.core.command_service import CommandExecutionResult, CommandService
from rigplane.core.state_pipeline_contracts import CommandIntent
from rigplane.core.state_store import StateStore
from rigplane.web.handlers.control import ControlHandler

_CONTROL_LOGGER = "rigplane.web.handlers.control"


class _RecordingWebSocket:
    """Fake websocket that records decoded ``send_text`` payloads."""

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(json.loads(payload))


def _handler() -> tuple[ControlHandler, _RecordingWebSocket]:
    ws = _RecordingWebSocket()
    radio = SimpleNamespace(connected=True, capabilities={"nb"})
    handler = ControlHandler(
        ws,  # type: ignore[arg-type]
        radio,
        "9.9.9",
        "IC-7610",
        server=SimpleNamespace(),
    )
    return handler, ws


@dataclass(slots=True)
class _RaisingExecutor:
    """Typed CommandService executor that fails with a fixed exception."""

    exc: Exception

    async def execute(self, intent: CommandIntent) -> CommandExecutionResult:
        raise self.exc


@pytest.mark.parametrize(
    ("frame", "expected_message_part"),
    [
        pytest.param(
            {"id": "c1", "name": "set_freq", "params": None},
            "params must be an object",
            id="params-null",
        ),
        pytest.param(
            {"id": "c2", "name": "set_freq", "params": []},
            "params must be an object",
            id="params-list",
        ),
        pytest.param(
            {"id": "c3", "name": "set_freq", "params": {"freq": None}},
            "freq",
            id="set_freq-freq-null",
        ),
        pytest.param(
            {"id": "c4", "name": "set_freq", "params": {"freq": "abc"}},
            "freq",
            id="set_freq-freq-string",
        ),
        pytest.param(
            {"id": "c5", "name": "set_freq", "params": {"freq": {}}},
            "freq",
            id="set_freq-freq-object",
        ),
        pytest.param(
            {"id": "c6", "name": "ptt", "params": None},
            "params must be an object",
            id="ptt-params-null",
        ),
    ],
)
async def test_malformed_command_params_fail_as_client_error(
    frame: dict[str, Any],
    expected_message_part: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    handler, ws = _handler()
    with caplog.at_level(logging.WARNING, logger=_CONTROL_LOGGER):
        await handler._handle_command(frame)  # noqa: SLF001

    assert len(ws.sent) == 1
    reply = ws.sent[0]
    assert reply["type"] == "response"
    assert reply["id"] == frame["id"]
    assert reply["ok"] is False
    assert reply["error"] == "command_failed"
    assert expected_message_part in reply["message"]

    records = [record for record in caplog.records if record.name == _CONTROL_LOGGER]
    errors = [record for record in records if record.levelno >= logging.ERROR]
    warnings = [record for record in records if record.levelno == logging.WARNING]
    assert errors == []
    assert len(warnings) == 1
    assert warnings[0].exc_info is None


async def test_programmer_error_from_executor_still_logs_an_error(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Control row: a genuine bug (executor raising AttributeError) is ERROR."""
    ws = _RecordingWebSocket()
    handler = ControlHandler.__new__(ControlHandler)
    handler._ws = ws  # noqa: SLF001
    handler._radio = SimpleNamespace()  # noqa: SLF001
    handler._server = None  # noqa: SLF001
    handler._session_id = "test-session"  # noqa: SLF001
    handler._managed_tx_authority = None  # noqa: SLF001
    handler._command_service = CommandService(  # noqa: SLF001
        executor=_RaisingExecutor(AttributeError("boom")),
        state_store=StateStore(),
    )

    with caplog.at_level(logging.WARNING, logger=_CONTROL_LOGGER):
        await handler._dispatch_command(  # noqa: SLF001
            "cmd-1", "set_freq", {"freq": 14_074_000}
        )

    assert len(ws.sent) == 1
    assert ws.sent[0]["ok"] is False
    assert ws.sent[0]["error"] == "command_failed"
    errors = [
        record
        for record in caplog.records
        if record.name == _CONTROL_LOGGER and record.levelno == logging.ERROR
    ]
    assert len(errors) == 1
    assert errors[0].exc_info is not None
    assert errors[0].exc_info[0] is AttributeError
