"""Staged Web ingress contract for composition-owned local TX work."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.capabilities import CAP_CW, CAP_TUNER
from rigplane.core.exceptions import CommandError
from rigplane.web.handlers.control import ControlHandler


class _Runner:
    def __init__(self) -> None:
        self.operations: list[Any] = []

    async def run(self, operation: Any) -> None:
        self.operations.append(operation)
        await operation(lambda: True)


class _Authority:
    def __init__(self, admitted: bool = True) -> None:
        self.admitted = admitted
        self.intents: list[Any] = []

    async def admit_managed_write(self, intent: Any) -> bool:
        self.intents.append(intent)
        return self.admitted


def _handler(
    radio: Any,
    *,
    authority: _Authority | None = None,
    runner: _Runner | None = None,
) -> ControlHandler:
    port = (
        None
        if authority is None and runner is None
        else SimpleNamespace(
            authority=authority,
            local_tx_work_runner=runner,
        )
    )
    server = SimpleNamespace(
        command_queue=MagicMock(),
    )
    return ControlHandler(
        ws=MagicMock(),
        radio=radio,
        server_version="test",
        radio_model="test",
        server=server,
        managed_tx_port=port,
    )


def _radio(*capabilities: str) -> MagicMock:
    radio = MagicMock()
    radio.capabilities = frozenset(capabilities)
    radio.send_cw_text = AsyncMock()
    radio.stop_cw_text = AsyncMock()
    radio.set_tuner_status = AsyncMock()
    return radio


@pytest.mark.asyncio
async def test_cw_send_uses_composition_runner_and_current_predicate() -> None:
    radio = _radio(CAP_CW)
    runner = _Runner()
    handler = _handler(radio, runner=runner)

    assert await handler._enqueue_command("send_cw_text", {"text": "CQ"}) == {
        "text": "CQ"
    }

    assert len(runner.operations) == 1
    radio.send_cw_text.assert_awaited_once()
    assert radio.send_cw_text.await_args.args == ("CQ",)
    assert radio.send_cw_text.await_args.kwargs["is_current"]()


@pytest.mark.asyncio
async def test_cw_stop_remains_an_unfenced_release() -> None:
    radio = _radio(CAP_CW)
    runner = _Runner()
    handler = _handler(radio, runner=runner)

    assert await handler._enqueue_command("stop_cw_text", {}) == {}

    radio.stop_cw_text.assert_awaited_once_with()
    assert runner.operations == []


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [1, 2])
async def test_positive_tuner_admits_once_then_uses_runner(value: int) -> None:
    radio = _radio(CAP_TUNER)
    authority = _Authority()
    runner = _Runner()
    handler = _handler(radio, authority=authority, runner=runner)

    assert await handler._enqueue_command("set_tuner_status", {"value": value}) == {
        "value": value,
        "label": {1: "ON", 2: "TUNING"}[value],
    }

    assert len(authority.intents) == 1
    assert authority.intents[0].name == "set_tuner_status"
    assert authority.intents[0].params["value"] == value
    assert len(runner.operations) == 1
    radio.set_tuner_status.assert_awaited_once()
    assert radio.set_tuner_status.await_args.args == (value,)
    assert radio.set_tuner_status.await_args.kwargs["is_current"]()


@pytest.mark.asyncio
async def test_positive_tuner_rejection_never_calls_runner_radio_or_queue() -> None:
    radio = _radio(CAP_TUNER)
    authority = _Authority(admitted=False)
    runner = _Runner()
    handler = _handler(radio, authority=authority, runner=runner)

    with pytest.raises(CommandError, match="managed tuner write was rejected"):
        await handler._enqueue_command("set_tuner_status", {"value": 1})

    assert len(authority.intents) == 1
    assert runner.operations == []
    radio.set_tuner_status.assert_not_awaited()
    handler._server.command_queue.put_ordered.assert_not_called()


@pytest.mark.asyncio
async def test_positive_tuner_without_runner_fails_closed_without_queue_fallback() -> (
    None
):
    radio = _radio(CAP_TUNER)
    authority = _Authority()
    handler = _handler(radio, authority=authority)

    with pytest.raises(RuntimeError, match="managed local TX runner is unavailable"):
        await handler._enqueue_command("set_tuner_status", {"value": 2})

    assert len(authority.intents) == 1
    radio.set_tuner_status.assert_not_awaited()
    handler._server.command_queue.put_ordered.assert_not_called()


@pytest.mark.asyncio
async def test_composed_tuner_off_uses_runner_without_legacy_interlock_or_admission(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio = _radio(CAP_TUNER)
    authority = _Authority()
    runner = _Runner()
    handler = _handler(radio, authority=authority, runner=runner)
    monkeypatch.setattr(
        handler,
        "_observed_rf_state",
        lambda: pytest.fail("composed tuner release must not use legacy interlock"),
    )

    assert await handler._enqueue_command("set_tuner_status", {"value": 0}) == {
        "value": 0,
        "label": "OFF",
    }

    assert authority.intents == []
    assert len(runner.operations) == 1
    radio.set_tuner_status.assert_awaited_once()
    assert radio.set_tuner_status.await_args.args == (0,)
    assert radio.set_tuner_status.await_args.kwargs["is_current"]()
