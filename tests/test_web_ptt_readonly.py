"""Tests for read-only guard on web PTT dispatch (issue #950, #987)."""

from __future__ import annotations

import asyncio
from queue import Queue
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.capabilities import CAP_CW, CAP_TUNER
from rigplane.core.exceptions import CommandError
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessClock, StateStore
from rigplane.runtime.managed_tx_state import ManagedTxOutcome
from rigplane.web import server as web_server
from rigplane.web.protocol import decode_json
from rigplane.web.radio_poller import SetTunerStatus
from rigplane.web.handlers.control import ControlHandler

_UNOBSERVED = object()


def _make_handler(
    *,
    read_only: bool = False,
    radio: Any = None,
    ptt: object = False,
    stale: bool = False,
    authority: Any = None,
) -> tuple[ControlHandler, Queue[Any]]:
    """Build a ControlHandler with a fake server and return (handler, command_queue)."""
    ws = MagicMock()

    command_queue: Queue[Any] = Queue()
    clock = FreshnessClock(start=10.0)
    state_store = StateStore(freshness_clock=clock)
    if ptt is not _UNOBSERVED:
        state_store.apply(
            Observation(
                path=FieldPath.global_("tx_state", "ptt"),
                value=ptt,
                source=SourceMetadata(
                    source="poll_response",
                    provider="test",
                    transport="fake",
                    native_id="test",
                ),
                timestamp_monotonic=clock.now(),
                max_age=1.0,
            )
        )
        if stale:
            state_store.mark_stale_due(now=12.0)

    server = SimpleNamespace(
        command_queue=command_queue,
        command_state_store=state_store,
    )

    if radio is None:
        radio = MagicMock()

    handler = ControlHandler(
        ws=ws,
        radio=radio,
        server_version="test",
        radio_model="IC-7610",
        server=server,
        read_only=read_only,
        managed_tx_authority=authority,
    )
    return handler, command_queue


class TestWebPttReadOnly:
    """read_only=True must reject PTT commands without enqueuing anything."""

    @pytest.mark.asyncio
    async def test_ptt_rejected_in_read_only_mode(self) -> None:
        """ptt command raises PermissionError when read_only=True."""
        handler, q = _make_handler(read_only=True)

        with pytest.raises(PermissionError, match="read-only"):
            await handler._enqueue_command("ptt", {"state": True})

        assert q.empty(), "command queue must not be touched in read-only mode"

    @pytest.mark.asyncio
    async def test_ptt_on_rejected_in_read_only_mode(self) -> None:
        """ptt_on command raises PermissionError when read_only=True."""
        handler, q = _make_handler(read_only=True)

        with pytest.raises(PermissionError, match="read-only"):
            await handler._enqueue_command("ptt_on", {})

        assert q.empty(), "command queue must not be touched in read-only mode"

    @pytest.mark.asyncio
    async def test_ptt_off_rejected_in_read_only_mode(self) -> None:
        """ptt_off command raises PermissionError when read_only=True."""
        handler, q = _make_handler(read_only=True)

        with pytest.raises(PermissionError, match="read-only"):
            await handler._enqueue_command("ptt_off", {})

        assert q.empty(), "command queue must not be touched in read-only mode"

    @pytest.mark.asyncio
    async def test_ptt_allowed_when_not_read_only(self) -> None:
        authority = SimpleNamespace(
            submit_ptt=AsyncMock(
                return_value=SimpleNamespace(outcome=ManagedTxOutcome.ACCEPTED)
            )
        )
        handler, q = _make_handler(read_only=False, authority=authority)

        result = await handler._enqueue_command("ptt", {"state": True})

        assert result == {"state": True}
        authority.submit_ptt.assert_awaited_once_with(True, handler._session_id)
        assert q.empty()

    @pytest.mark.asyncio
    async def test_ptt_on_allowed_when_not_read_only(self) -> None:
        authority = SimpleNamespace(
            submit_ptt=AsyncMock(
                return_value=SimpleNamespace(outcome=ManagedTxOutcome.ACCEPTED)
            )
        )
        handler, q = _make_handler(read_only=False, authority=authority)

        result = await handler._enqueue_command("ptt_on", {})

        assert result == {}
        authority.submit_ptt.assert_awaited_once_with(True, handler._session_id)
        assert q.empty()

    @pytest.mark.asyncio
    async def test_ptt_off_allowed_when_not_read_only(self) -> None:
        authority = SimpleNamespace(
            submit_ptt=AsyncMock(
                return_value=SimpleNamespace(outcome=ManagedTxOutcome.ACCEPTED)
            )
        )
        handler, q = _make_handler(read_only=False, authority=authority)

        result = await handler._enqueue_command("ptt_off", {})

        assert result == {}
        authority.submit_ptt.assert_awaited_once_with(False, handler._session_id)
        assert q.empty()


class TestWebCwReadOnly:
    """read_only=True must reject send_cw_text without keying the radio."""

    def _make_cw_radio(self) -> MagicMock:
        radio = MagicMock()
        radio.capabilities = frozenset({CAP_CW})
        radio.send_cw_text = AsyncMock(return_value=None)
        return radio

    @pytest.mark.asyncio
    async def test_send_cw_text_rejected_in_read_only_mode(self) -> None:
        """send_cw_text raises PermissionError when read_only=True."""
        handler, q = _make_handler(read_only=True, radio=self._make_cw_radio())

        with pytest.raises(PermissionError, match="read-only"):
            await handler._enqueue_command("send_cw_text", {"text": "CQ CQ"})

        assert q.empty(), "command queue must not be touched in read-only mode"

    @pytest.mark.asyncio
    async def test_send_cw_text_not_called_on_radio_in_read_only_mode(self) -> None:
        """Radio send_cw_text must never be invoked when read_only=True."""
        radio = self._make_cw_radio()
        handler, _ = _make_handler(read_only=True, radio=radio)

        with pytest.raises(PermissionError):
            await handler._enqueue_command("send_cw_text", {"text": "TEST"})

        radio.send_cw_text.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_cw_text_allowed_when_not_read_only(self) -> None:
        """send_cw_text dispatches normally when read_only=False."""
        radio = self._make_cw_radio()
        handler, q = _make_handler(read_only=False, radio=radio)

        result = await handler._enqueue_command("send_cw_text", {"text": "CQ"})

        assert result == {"text": "CQ"}
        radio.send_cw_text.assert_awaited_once_with("CQ")


class TestWebTunerReadOnly:
    """read_only=True must reject set_tuner_status TUNING (value=2) only."""

    def _make_tuner_radio(self) -> MagicMock:
        radio = MagicMock()
        radio.capabilities = frozenset({CAP_TUNER})
        radio.set_tuner_status = AsyncMock(return_value=None)
        return radio

    @pytest.mark.asyncio
    async def test_tuner_tune_rejected_in_read_only_mode(self) -> None:
        """set_tuner_status value=2 (TUNING) raises PermissionError when read_only=True."""
        handler, q = _make_handler(read_only=True, radio=self._make_tuner_radio())

        with pytest.raises(PermissionError, match="read-only"):
            await handler._enqueue_command("set_tuner_status", {"value": 2})

        assert q.empty(), "command queue must not be touched in read-only mode"

    @pytest.mark.asyncio
    async def test_tuner_on_allowed_in_read_only_mode(self) -> None:
        """set_tuner_status value=1 (ON) is allowed even when read_only=True."""
        radio = self._make_tuner_radio()
        handler, q = _make_handler(read_only=True, radio=radio)

        result = await handler._enqueue_command("set_tuner_status", {"value": 1})

        assert result == {"value": 1, "label": "ON"}
        radio.set_tuner_status.assert_awaited_once_with(1)

    @pytest.mark.asyncio
    async def test_tuner_off_allowed_in_read_only_mode(self) -> None:
        """set_tuner_status value=0 (OFF) is allowed even when read_only=True."""
        radio = self._make_tuner_radio()
        handler, q = _make_handler(read_only=True, radio=radio)

        result = await handler._enqueue_command("set_tuner_status", {"value": 0})

        assert result == {"value": 0, "label": "OFF"}
        radio.set_tuner_status.assert_awaited_once_with(0)

    @pytest.mark.asyncio
    async def test_tuner_tune_allowed_when_not_read_only(self) -> None:
        """set_tuner_status value=2 (TUNING) dispatches normally when read_only=False."""
        radio = self._make_tuner_radio()
        handler, q = _make_handler(read_only=False, radio=radio)

        result = await handler._enqueue_command("set_tuner_status", {"value": 2})

        assert result == {"value": 2, "label": "TUNING"}
        radio.set_tuner_status.assert_awaited_once_with(2)

    @pytest.mark.parametrize("value", [1, 2])
    @pytest.mark.parametrize(
        ("ptt", "stale", "reason"),
        [
            (True, False, "RF state is TX"),
            (_UNOBSERVED, False, "RF state is unknown"),
            (False, True, "RF state is unknown"),
            (1, False, "RF state is unknown"),
        ],
        ids=["tx", "missing", "stale", "invalid"],
    )
    async def test_tuner_engage_fails_closed_before_direct_call(
        self, value: int, ptt: object, stale: bool, reason: str
    ) -> None:
        radio = self._make_tuner_radio()
        handler, q = _make_handler(radio=radio, ptt=ptt, stale=stale)

        with pytest.raises(CommandError, match=reason):
            await handler._enqueue_command("set_tuner_status", {"value": value})

        radio.set_tuner_status.assert_not_awaited()
        assert q.empty()

    @pytest.mark.parametrize("value", [1, 2])
    @pytest.mark.parametrize("ptt", [True, _UNOBSERVED], ids=["tx", "unknown"])
    async def test_tuner_engage_fails_closed_before_queue(
        self, value: int, ptt: object
    ) -> None:
        radio = SimpleNamespace(capabilities=frozenset())
        handler, q = _make_handler(radio=radio, ptt=ptt)

        with pytest.raises(CommandError):
            await handler._enqueue_command("set_tuner_status", {"value": value})

        assert q.empty()

    @pytest.mark.parametrize(
        ("ptt", "stale"),
        [(True, False), (_UNOBSERVED, False), (False, True)],
        ids=["tx", "missing", "stale"],
    )
    async def test_tuner_off_is_always_attempted(
        self, ptt: object, stale: bool
    ) -> None:
        radio = self._make_tuner_radio()
        handler, _ = _make_handler(radio=radio, ptt=ptt, stale=stale)

        await handler._enqueue_command("set_tuner_status", {"value": 0})

        radio.set_tuner_status.assert_awaited_once_with(0)

    async def test_fresh_rx_queue_preserves_tuner_dispatch(self) -> None:
        handler, _ = _make_handler(
            radio=SimpleNamespace(capabilities=frozenset()), ptt=False
        )

        class SuccessfulQueue:
            def __init__(self) -> None:
                self.commands: list[SetTunerStatus] = []

            def put_ordered(
                self, command: SetTunerStatus, *, future: asyncio.Future[object]
            ) -> None:
                self.commands.append(command)
                future.set_result(None)

        q = SuccessfulQueue()
        handler._server.command_queue = q

        await handler._enqueue_command("set_tuner_status", {"value": 2})

        assert q.commands == [SetTunerStatus(2)]

    @pytest.mark.parametrize("value", [True, 1.5, 3, "bogus"])
    async def test_invalid_tuner_value_fails_without_call(self, value: object) -> None:
        radio = self._make_tuner_radio()
        handler, q = _make_handler(radio=radio)

        with pytest.raises(ValueError, match="tuner value"):
            await handler._enqueue_command("set_tuner_status", {"value": value})

        radio.set_tuner_status.assert_not_awaited()
        assert q.empty()

    @pytest.mark.parametrize("outcome", [False, RuntimeError("backend rejected")])
    async def test_direct_backend_failure_is_failed_not_acknowledged(
        self, outcome: object
    ) -> None:
        radio = self._make_tuner_radio()
        if isinstance(outcome, BaseException):
            radio.set_tuner_status.side_effect = outcome
        else:
            radio.set_tuner_status.return_value = outcome
        handler, _ = _make_handler(radio=radio, ptt=False)

        with pytest.raises((CommandError, RuntimeError), match="rejected"):
            await handler._enqueue_command("set_tuner_status", {"value": 1})

        radio.set_tuner_status.assert_awaited_once_with(1)
        states = [event.state for event in handler._command_service.lifecycle_events()]
        assert states[-1] == "failed"
        assert "acknowledged" not in states

    @pytest.mark.parametrize("outcome", [False, RuntimeError("backend rejected")])
    async def test_queued_backend_failure_is_failed_not_acknowledged(
        self, outcome: object
    ) -> None:
        class OutcomeQueue:
            def __init__(self) -> None:
                self.commands: list[SetTunerStatus] = []

            def put_ordered(
                self, command: SetTunerStatus, *, future: asyncio.Future[object]
            ) -> None:
                self.commands.append(command)
                if isinstance(outcome, BaseException):
                    future.set_exception(outcome)
                else:
                    future.set_result(outcome)

        handler, _ = _make_handler(
            radio=SimpleNamespace(capabilities=frozenset()), ptt=False
        )
        queue = OutcomeQueue()
        handler._server.command_queue = queue

        with pytest.raises((CommandError, RuntimeError)):
            await handler._enqueue_command("set_tuner_status", {"value": 2})

        assert queue.commands == [SetTunerStatus(2)]
        states = [event.state for event in handler._command_service.lifecycle_events()]
        assert states[-1] == "failed"
        assert "acknowledged" not in states

    async def test_queued_backend_timeout_is_terminal_not_acknowledged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        class StalledQueue:
            calls = 0
            future: asyncio.Future[object] | None = None

            def put_ordered(
                self, command: SetTunerStatus, *, future: asyncio.Future[object]
            ) -> None:
                self.calls += 1
                self.future = future

        monkeypatch.setattr(web_server, "_COMMAND_BATCH_STEP_TIMEOUT", 0.001)
        handler, _ = _make_handler(
            radio=SimpleNamespace(capabilities=frozenset()), ptt=False
        )
        handler._ws.send_text = AsyncMock()
        queue = StalledQueue()
        handler._server.command_queue = queue

        await asyncio.wait_for(
            handler._dispatch_command(1, "set_tuner_status", {"value": 2}),
            timeout=0.1,
        )

        response = decode_json(handler._ws.send_text.await_args.args[0])
        states = [event.state for event in handler._command_service.lifecycle_events()]
        assert response["ok"] is False
        assert queue.calls == 1
        assert queue.future is not None and queue.future.cancelled()
        assert states[-1] == "timed_out"
        assert not {"acknowledged", "confirmed", "applied"} & set(states)
