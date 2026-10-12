"""Malformed WS command parameters are client errors, not programmer errors.

Since #4077, ``ControlHandler._send_command_failure`` logs
``AttributeError``/``TypeError``/``NameError`` at ERROR with a traceback —
that class is reserved for genuine bugs in the command path. Malformed
client frames must instead fail as client errors: exactly one failure reply
with error ``command_failed`` whose message names the offending field (or
says ``params must be an object``, the same wording the HTTP single-command
ingress in ``web/server.py: WebServer._handle_http_single_command`` uses),
one WARNING record, and no ERROR record.

Covered here: ``params: null`` behaves exactly like an omitted ``params``
(``ControlHandler._handle_command`` treats it as ``{}``); any other
non-object ``params`` (a list, a string) is refused at that entry, before
rate limiting and coalescing; ``set_freq`` with a ``freq`` of the wrong
JSON type is refused by ``core/command_service.py:
command_intent_from_request``; ``set_att``/``set_af_level`` with
``receiver: null`` are refused by ``core/command_dispatch.py:
_bind_attenuator``/``_bind_level``; a non-string command ``name`` gets the
``unknown_command`` reply and the session continues — also when the same
frame carries a non-object ``params`` (the name check runs before the
params refusal, so an unhashable name never reaches ``command_descriptor``
inside the failure sender); ``ptt_off`` with ``params: null`` is admitted
past ingress validation. Legacy executor-side commands whose params are
coerced inside the command-service executor (``set_band``, ``set_nr_level``,
``set_data_mode``, ``set_rit_frequency``, ``set_key_speed``,
``set_scope_span``) fail the same way: one ``command_failed`` reply naming
the field, one WARNING record, no ERROR record, and nothing queued. The
control row pins that a genuine programmer error — an executor raising
``AttributeError`` — is still logged at ERROR.
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


def _handler_with_queue() -> tuple[ControlHandler, _RecordingWebSocket]:
    """Handler whose server exposes a command queue, so descriptor-bound
    commands (``set_att``, ``set_af_level``, ...) reach their bind."""
    ws = _RecordingWebSocket()
    radio = SimpleNamespace(connected=True, capabilities={"nb"})
    handler = ControlHandler(
        ws,  # type: ignore[arg-type]
        radio,
        "9.9.9",
        "IC-7610",
        server=SimpleNamespace(command_queue=SimpleNamespace()),
    )
    return handler, ws


class _RecordingQueue:
    """Fake command queue that records every enqueued command."""

    def __init__(self) -> None:
        self.commands: list[Any] = []

    def put(self, command: Any, **_: Any) -> None:
        self.commands.append(command)

    def put_ordered(self, command: Any, **_: Any) -> None:
        self.commands.append(command)


def _handler_with_legacy_queue() -> (
    tuple[ControlHandler, _RecordingWebSocket, _RecordingQueue]
):
    """Handler whose server exposes a command queue, so legacy commands
    (``set_band``, ``set_nr_level``, ...) reach their executor-side
    coercion in the ``_enqueue_rc_*`` helpers."""
    ws = _RecordingWebSocket()
    queue = _RecordingQueue()
    radio = SimpleNamespace(connected=True, capabilities={"nb"})
    handler = ControlHandler(
        ws,  # type: ignore[arg-type]
        radio,
        "9.9.9",
        "IC-7610",
        server=SimpleNamespace(command_queue=queue),
    )
    return handler, ws, queue


@dataclass(slots=True)
class _RaisingExecutor:
    """Typed CommandService executor that fails with a fixed exception."""

    exc: Exception

    async def execute(self, intent: CommandIntent) -> CommandExecutionResult:
        raise self.exc


def _assert_single_client_failure(
    ws: _RecordingWebSocket,
    cmd_id: Any,
    expected_message_part: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert len(ws.sent) == 1
    reply = ws.sent[0]
    assert reply["type"] == "response"
    assert reply["id"] == cmd_id
    assert reply["ok"] is False
    assert reply["error"] == "command_failed"
    assert expected_message_part in reply["message"]

    records = [record for record in caplog.records if record.name == _CONTROL_LOGGER]
    errors = [record for record in records if record.levelno >= logging.ERROR]
    warnings = [record for record in records if record.levelno == logging.WARNING]
    assert errors == []
    assert len(warnings) == 1
    assert warnings[0].exc_info is None


@pytest.mark.parametrize(
    ("frame", "expected_message_part"),
    [
        pytest.param(
            {"id": "c1", "name": "set_freq", "params": None},
            # ``params: null`` means ``{}``; the refusal is the missing freq.
            "freq",
            id="params-null",
        ),
        pytest.param(
            {"id": "c2", "name": "set_freq", "params": []},
            "params must be an object",
            id="params-list",
        ),
        pytest.param(
            {"id": "c2b", "name": "set_freq", "params": ""},
            "params must be an object",
            id="params-string",
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

    _assert_single_client_failure(ws, frame["id"], expected_message_part, caplog)


@pytest.mark.parametrize(
    "frame",
    [
        pytest.param(
            {"id": "c6", "name": "set_att", "params": {"db": 20, "receiver": None}},
            id="set_att-receiver-null",
        ),
        pytest.param(
            {
                "id": "c7",
                "name": "set_af_level",
                "params": {"level": 10, "receiver": None},
            },
            id="set_af_level-receiver-null",
        ),
    ],
)
async def test_null_receiver_in_descriptor_bound_commands_fails_as_client_error(
    frame: dict[str, Any],
    caplog: pytest.LogCaptureFixture,
) -> None:
    handler, ws = _handler_with_queue()
    with caplog.at_level(logging.WARNING, logger=_CONTROL_LOGGER):
        await handler._handle_command(frame)  # noqa: SLF001

    _assert_single_client_failure(ws, frame["id"], "receiver", caplog)


@pytest.mark.parametrize("name", [42, None], ids=["int-name", "null-name"])
async def test_non_string_command_name_gets_unknown_command(
    name: Any,
    caplog: pytest.LogCaptureFixture,
) -> None:
    handler, ws = _handler()
    with caplog.at_level(logging.WARNING, logger=_CONTROL_LOGGER):
        # Returns normally: the malformed frame must not end the session.
        await handler._handle_command({"id": "c8", "name": name, "params": {}})  # noqa: SLF001

    assert ws.sent == [
        {
            "type": "response",
            "id": "c8",
            "ok": False,
            "error": "unknown_command",
            "message": f"unknown command: {name!r}",
        }
    ]
    records = [record for record in caplog.records if record.name == _CONTROL_LOGGER]
    errors = [record for record in records if record.levelno >= logging.ERROR]
    warnings = [record for record in records if record.levelno == logging.WARNING]
    assert errors == []
    assert len(warnings) == 1


@pytest.mark.parametrize(
    ("frame", "expected_message_part"),
    [
        pytest.param(
            {"id": "e1", "name": "set_band", "params": {"band": None}},
            "band",
            id="set_band-band-null",
        ),
        pytest.param(
            {"id": "e2", "name": "set_nr_level", "params": {"level": None}},
            "level",
            id="set_nr_level-level-null",
        ),
        pytest.param(
            {"id": "e3", "name": "set_data_mode", "params": {"mode": {}}},
            "mode",
            id="set_data_mode-mode-object",
        ),
        pytest.param(
            # ``set_vfo_freq``'s ``freq`` is never coerced by a bare int()
            # (it rides the ordered-queue identity machinery), and
            # ``set_freq``'s executor site is pre-validated at ingress, so
            # the reachable executor-side ``freq`` coercion is
            # ``set_rit_frequency``.
            {"id": "e4", "name": "set_rit_frequency", "params": {"freq": None}},
            "freq",
            id="set_rit_frequency-freq-null",
        ),
        pytest.param(
            {"id": "e5", "name": "set_key_speed", "params": {"speed": "abc"}},
            "speed",
            id="set_key_speed-speed-non-numeric",
        ),
        pytest.param(
            {"id": "e6", "name": "set_scope_span", "params": {"span": None}},
            "span",
            id="set_scope_span-span-null",
        ),
    ],
)
async def test_executor_side_param_coercion_fails_as_client_error(
    frame: dict[str, Any],
    expected_message_part: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    handler, ws, queue = _handler_with_legacy_queue()
    with caplog.at_level(logging.WARNING, logger=_CONTROL_LOGGER):
        await handler._handle_command(frame)  # noqa: SLF001

    _assert_single_client_failure(ws, frame["id"], expected_message_part, caplog)
    assert queue.commands == []


async def test_list_name_with_non_object_params_gets_unknown_command(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The name check runs before the params refusal.

    A list ``name`` alongside a non-object ``params`` used to reach the
    params refusal first, where ``command_descriptor`` raised TypeError
    (unhashable key) and ended the control session. It must answer
    ``unknown_command`` and keep the session alive instead.
    """
    handler, ws = _handler()
    with caplog.at_level(logging.WARNING, logger=_CONTROL_LOGGER):
        # Returns normally: the malformed frame must not end the session.
        await handler._handle_command(  # noqa: SLF001
            {"id": "e7", "name": ["set_freq"], "params": []}
        )

    assert ws.sent == [
        {
            "type": "response",
            "id": "e7",
            "ok": False,
            "error": "unknown_command",
            "message": "unknown command: ['set_freq']",
        }
    ]
    records = [record for record in caplog.records if record.name == _CONTROL_LOGGER]
    errors = [record for record in records if record.levelno >= logging.ERROR]
    warnings = [record for record in records if record.levelno == logging.WARNING]
    assert errors == []
    assert len(warnings) == 1


async def test_ptt_off_with_null_params_is_not_refused_as_malformed() -> None:
    """``params: null`` spells "no params"; the frame reaches the managed-PTT path."""
    handler, ws = _handler()
    await handler._handle_command({"id": "c9", "name": "ptt_off", "params": None})  # noqa: SLF001

    assert len(ws.sent) == 1
    reply = ws.sent[0]
    assert reply["ok"] is False
    assert "params must be an object" not in reply["message"]
    # This fixture has no managed transmit authority, so the refusal proves
    # the frame was admitted past ingress validation into the PTT path.
    assert reply["error"] == "radio_nak"


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
