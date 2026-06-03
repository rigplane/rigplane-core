"""Tests for read-only guard on web PTT dispatch (issue #950, #987)."""

from __future__ import annotations

from queue import Queue
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.capabilities import CAP_CW, CAP_TUNER
from rigplane.web.handlers.control import ControlHandler


def _make_handler(
    *, read_only: bool = False, radio: Any = None
) -> tuple[ControlHandler, Queue[Any]]:
    """Build a ControlHandler with a fake server and return (handler, command_queue)."""
    ws = MagicMock()

    command_queue: Queue[Any] = Queue()

    server = SimpleNamespace(
        command_queue=command_queue,
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
        """ptt command dispatches normally when read_only=False."""
        handler, q = _make_handler(read_only=False)

        result = await handler._enqueue_command("ptt", {"state": True})

        assert result == {"state": True}
        assert not q.empty(), "PttOn must be enqueued"

    @pytest.mark.asyncio
    async def test_ptt_on_allowed_when_not_read_only(self) -> None:
        """ptt_on command dispatches normally when read_only=False."""
        handler, q = _make_handler(read_only=False)

        result = await handler._enqueue_command("ptt_on", {})

        assert result == {}
        assert not q.empty(), "PttOn must be enqueued"

    @pytest.mark.asyncio
    async def test_ptt_off_allowed_when_not_read_only(self) -> None:
        """ptt_off command dispatches normally when read_only=False."""
        handler, q = _make_handler(read_only=False)

        result = await handler._enqueue_command("ptt_off", {})

        assert result == {}
        assert not q.empty(), "PttOff must be enqueued"


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
    """read_only=True must reject set_tuner_status writes."""

    def _make_tuner_radio(self) -> MagicMock:
        radio = MagicMock()
        radio.capabilities = frozenset({CAP_TUNER})
        radio.get_tuner_status = AsyncMock(return_value=1)
        radio.set_tuner_status = AsyncMock(return_value=None)
        return radio

    @pytest.mark.asyncio
    async def test_tuner_status_read_allowed_in_read_only_mode(self) -> None:
        """get_tuner_status remains usable when read_only=True."""
        radio = self._make_tuner_radio()
        handler, q = _make_handler(read_only=True, radio=radio)

        result = await handler._enqueue_command("get_tuner_status", {})

        assert result == {"status": 1, "label": "ON"}
        assert q.empty(), "read command must not touch the command queue"
        radio.get_tuner_status.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tuner_tune_rejected_in_read_only_mode(self) -> None:
        """set_tuner_status value=2 (TUNING) raises PermissionError when read_only=True."""
        handler, q = _make_handler(read_only=True, radio=self._make_tuner_radio())

        with pytest.raises(PermissionError, match="read-only"):
            await handler._enqueue_command("set_tuner_status", {"value": 2})

        assert q.empty(), "command queue must not be touched in read-only mode"

    @pytest.mark.asyncio
    async def test_tuner_on_rejected_in_read_only_mode(self) -> None:
        """set_tuner_status value=1 (ON) raises PermissionError when read_only=True."""
        radio = self._make_tuner_radio()
        handler, q = _make_handler(read_only=True, radio=radio)

        with pytest.raises(PermissionError, match="read-only"):
            await handler._enqueue_command("set_tuner_status", {"value": 1})

        assert q.empty(), "command queue must not be touched in read-only mode"
        radio.set_tuner_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_tuner_off_rejected_in_read_only_mode(self) -> None:
        """set_tuner_status value=0 (OFF) raises PermissionError when read_only=True."""
        radio = self._make_tuner_radio()
        handler, q = _make_handler(read_only=True, radio=radio)

        with pytest.raises(PermissionError, match="read-only"):
            await handler._enqueue_command("set_tuner_status", {"value": 0})

        assert q.empty(), "command queue must not be touched in read-only mode"
        radio.set_tuner_status.assert_not_called()

    @pytest.mark.asyncio
    async def test_tuner_tune_allowed_when_not_read_only(self) -> None:
        """set_tuner_status value=2 (TUNING) dispatches normally when read_only=False."""
        radio = self._make_tuner_radio()
        handler, q = _make_handler(read_only=False, radio=radio)

        result = await handler._enqueue_command("set_tuner_status", {"value": 2})

        assert result == {"value": 2, "label": "TUNING"}
        radio.set_tuner_status.assert_awaited_once_with(2)
