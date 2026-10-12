"""Programmer errors in the command executor path must log a traceback.

``ControlHandler._send_command_failure`` answers every executor failure with
the same ``command_failed`` reply (the frontend consumes that code, so it
must not change), but the log record must distinguish the two cases: a bug
in the command path itself (``AttributeError``/``TypeError``/``NameError``)
is an ERROR carrying ``exc_info``, while an ordinary command failure (a
radio refusal such as ``CommandError``) stays a one-line WARNING without a
traceback.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from rigplane.core.command_service import CommandExecutionResult, CommandService
from rigplane.core.exceptions import CommandError
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


@dataclass(slots=True)
class _RaisingExecutor:
    """Typed CommandService executor that fails with a fixed exception."""

    exc: Exception

    async def execute(self, intent: CommandIntent) -> CommandExecutionResult:
        raise self.exc


def _handler(executor: _RaisingExecutor) -> tuple[ControlHandler, _RecordingWebSocket]:
    ws = _RecordingWebSocket()
    handler = ControlHandler.__new__(ControlHandler)
    handler._ws = ws
    handler._radio = SimpleNamespace()
    handler._server = None
    handler._session_id = "test-session"
    handler._managed_tx_authority = None
    handler._command_service = CommandService(
        executor=executor, state_store=StateStore()
    )
    return handler, ws


@pytest.mark.parametrize("exc_type", [AttributeError, TypeError, NameError])
async def test_programmer_error_logs_an_error_with_traceback(
    exc_type: type[Exception], caplog: pytest.LogCaptureFixture
) -> None:
    handler, ws = _handler(_RaisingExecutor(exc_type("boom")))
    with caplog.at_level(logging.WARNING, logger=_CONTROL_LOGGER):
        await handler._dispatch_command(  # noqa: SLF001
            "cmd-1", "set_freq", {"freq": 14_074_000}
        )

    assert ws.sent == [
        {
            "type": "response",
            "id": "cmd-1",
            "ok": False,
            "error": "command_failed",
            "message": "boom",
        }
    ]
    errors = [
        record
        for record in caplog.records
        if record.name == _CONTROL_LOGGER and record.levelno == logging.ERROR
    ]
    assert len(errors) == 1
    assert errors[0].exc_info is not None
    assert errors[0].exc_info[0] is exc_type


async def test_ordinary_command_error_stays_a_plain_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    handler, ws = _handler(_RaisingExecutor(CommandError("refused")))
    with caplog.at_level(logging.WARNING, logger=_CONTROL_LOGGER):
        await handler._dispatch_command(  # noqa: SLF001
            "cmd-1", "set_freq", {"freq": 14_074_000}
        )

    assert len(ws.sent) == 1
    assert ws.sent[0]["ok"] is False
    assert ws.sent[0]["error"] == "command_failed"
    records = [record for record in caplog.records if record.name == _CONTROL_LOGGER]
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING
    assert records[0].exc_info is None
