"""Tests for backend session-teardown MOD-input restore (MOR-624).

The frontend auto-LAN feature (MOR-618) arms a restore on the backend
session at TX start and disarms it on a clean TX stop. If the session
tears down while still armed (abnormal mid-TX disconnect), the handler
enqueues PttOff followed by the previous-source SET on the shared
command queue.
"""

from __future__ import annotations

from queue import Queue
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.web.handlers.control import ControlHandler
from rigplane.web.radio_poller import (
    PttOff,
    SetDataOffModInput,
    SetData1ModInput,
    SetData2ModInput,
)


def _make_handler() -> tuple[ControlHandler, Queue[Any]]:
    """Build a ControlHandler with a fake server and return (handler, command_queue)."""
    ws = MagicMock()

    command_queue: Queue[Any] = Queue()

    server = SimpleNamespace(
        command_queue=command_queue,
    )

    handler = ControlHandler(
        ws=ws,
        radio=MagicMock(),
        server_version="test",
        radio_model="IC-7610",
        server=server,
    )
    return handler, command_queue


def _drain(q: Queue[Any]) -> list[Any]:
    items: list[Any] = []
    while not q.empty():
        items.append(q.get_nowait())
    return items


class TestArmDisarm:
    """_apply_mod_input_restore_cmd is pure session-local bookkeeping."""

    def test_arm_with_valid_command_sets_state(self) -> None:
        handler, _ = _make_handler()

        result = handler._apply_mod_input_restore_cmd(
            "arm_mod_input_restore",
            {"command": "set_data1_mod_input", "source": 2},
        )

        assert handler._mod_input_restore == ("set_data1_mod_input", 2)
        assert result == {"armed": True}

    def test_arm_with_unknown_command_does_not_arm(self) -> None:
        handler, _ = _make_handler()

        result = handler._apply_mod_input_restore_cmd(
            "arm_mod_input_restore",
            {"command": "set_freq", "source": 2},
        )

        assert handler._mod_input_restore is None
        assert result == {"armed": False}

    def test_disarm_clears_armed_state(self) -> None:
        handler, _ = _make_handler()
        handler._apply_mod_input_restore_cmd(
            "arm_mod_input_restore",
            {"command": "set_data1_mod_input", "source": 2},
        )

        result = handler._apply_mod_input_restore_cmd("disarm_mod_input_restore", {})

        assert handler._mod_input_restore is None
        assert result == {}


class TestTeardownRestore:
    """_restore_mod_input_on_teardown enqueues PttOff then the restore SET."""

    def test_teardown_when_armed_enqueues_ptt_off_then_restore(self) -> None:
        handler, q = _make_handler()
        handler._apply_mod_input_restore_cmd(
            "arm_mod_input_restore",
            {"command": "set_data_off_mod_input", "source": 0},
        )

        handler._restore_mod_input_on_teardown()

        items = _drain(q)
        assert len(items) == 2
        assert isinstance(items[0], PttOff)
        assert isinstance(items[1], SetDataOffModInput)
        assert items[1].source == 0
        assert handler._mod_input_restore is None

    def test_teardown_carries_armed_source(self) -> None:
        handler, q = _make_handler()
        handler._apply_mod_input_restore_cmd(
            "arm_mod_input_restore",
            {"command": "set_data1_mod_input", "source": 3},
        )

        handler._restore_mod_input_on_teardown()

        items = _drain(q)
        assert isinstance(items[0], PttOff)
        assert isinstance(items[1], SetData1ModInput)
        assert items[1].source == 3

    def test_teardown_when_not_armed_enqueues_nothing(self) -> None:
        handler, q = _make_handler()

        handler._restore_mod_input_on_teardown()

        assert q.empty(), "nothing armed — teardown must not touch the queue"

    def test_teardown_after_disarm_enqueues_nothing(self) -> None:
        handler, q = _make_handler()
        handler._apply_mod_input_restore_cmd(
            "arm_mod_input_restore",
            {"command": "set_data1_mod_input", "source": 2},
        )
        handler._apply_mod_input_restore_cmd("disarm_mod_input_restore", {})

        handler._restore_mod_input_on_teardown()

        assert q.empty(), "clean disarm owns the restore — teardown must be a no-op"


class TestCommandRouting:
    """arm/disarm are intercepted in _handle_command before the _COMMANDS gate."""

    @pytest.mark.asyncio
    async def test_arm_routed_and_acked(self) -> None:
        handler, _ = _make_handler()
        handler._ws.send_text = AsyncMock()

        await handler._handle_command(
            {
                "name": "arm_mod_input_restore",
                "params": {"command": "set_data2_mod_input", "source": 3},
                "id": "x",
            }
        )

        assert handler._mod_input_restore == ("set_data2_mod_input", 3)
        handler._ws.send_text.assert_awaited_once()
        sent = handler._ws.send_text.await_args.args[0]
        assert '"ok": true' in sent or '"ok":true' in sent

    @pytest.mark.asyncio
    async def test_teardown_after_routed_arm_restores(self) -> None:
        handler, q = _make_handler()
        handler._ws.send_text = AsyncMock()

        await handler._handle_command(
            {
                "name": "arm_mod_input_restore",
                "params": {"command": "set_data2_mod_input", "source": 3},
                "id": "x",
            }
        )
        handler._restore_mod_input_on_teardown()

        items = _drain(q)
        assert len(items) == 2
        assert isinstance(items[0], PttOff)
        assert isinstance(items[1], SetData2ModInput)
        assert items[1].source == 3
