"""Tests for control-session MOD-input bookkeeping (MOR-624/MOR-993).

The frontend auto-LAN feature (MOR-618) arms a restore at TX start and
disarms it on a clean TX stop. Teardown consumes the arm and discards it; no
command outcome or queue ordering authorizes a previous-source MOD SET
(MOR-993).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.web.handlers.control import ControlHandler
from rigplane.web.radio_poller import CommandQueue


_MOD_COMMANDS = (
    "set_data_off_mod_input",
    "set_data1_mod_input",
    "set_data2_mod_input",
    "set_data3_mod_input",
)


def _make_handler(*, read_only: bool = False) -> tuple[ControlHandler, CommandQueue]:
    """Build a ControlHandler with a real command queue."""
    command_queue = CommandQueue()
    server = SimpleNamespace(command_queue=command_queue)
    handler = ControlHandler(
        ws=MagicMock(),
        radio=MagicMock(),
        server_version="test",
        radio_model="IC-7610",
        server=server,
        read_only=read_only,
    )
    return handler, command_queue


def _make_run_handler(*, unregister: Any = None) -> tuple[ControlHandler, CommandQueue]:
    """Build a handler whose ``run()`` reaches teardown on the first ``recv``."""
    command_queue = CommandQueue()

    async def recv() -> tuple[int, bytes]:
        await asyncio.sleep(0.01)  # let the event-sender task run before EOF
        raise EOFError

    return ControlHandler(
        ws=SimpleNamespace(send_text=AsyncMock(), recv=recv),
        radio=SimpleNamespace(connected=True, radio_ready=True),
        server_version="test",
        radio_model="IC-7610",
        server=SimpleNamespace(
            command_queue=command_queue,
            register_control_event_queue=MagicMock(),
            unregister_control_event_queue=unregister or MagicMock(),
            build_state_update_envelope=MagicMock(return_value={}),
        ),
    ), command_queue


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

    def test_disarm_clears_armed_state_idempotently(self) -> None:
        handler, _ = _make_handler()
        handler._apply_mod_input_restore_cmd(
            "arm_mod_input_restore",
            {"command": "set_data1_mod_input", "source": 2},
        )

        result = handler._apply_mod_input_restore_cmd("disarm_mod_input_restore", {})
        repeated = handler._apply_mod_input_restore_cmd("disarm_mod_input_restore", {})

        assert handler._mod_input_restore is None
        assert result == repeated == {}

    @pytest.mark.parametrize("source", [True, "2", -1, 6, None])
    def test_invalid_source_is_rejected_and_clears_existing_arm(
        self, source: object
    ) -> None:
        handler, q = _make_handler()
        invalid = {"command": "set_data1_mod_input", "source": source}
        valid = {"command": "set_data1_mod_input", "source": 2}

        assert handler._apply_mod_input_restore_cmd(
            "arm_mod_input_restore", invalid
        ) == {"armed": False}
        assert handler._mod_input_restore is None
        assert handler._apply_mod_input_restore_cmd("arm_mod_input_restore", valid) == {
            "armed": True
        }
        assert handler._apply_mod_input_restore_cmd(
            "arm_mod_input_restore", invalid
        ) == {"armed": False}
        handler._clear_mod_input_restore_on_teardown()
        assert handler._mod_input_restore is None
        # Bookkeeping alone never reaches the command queue.
        assert not q.has_commands


class TestTeardownModRestoreInvariant:
    """MOR-993: teardown may never replay the remembered MOD SET."""

    def test_clearing_bookkeeping_never_enqueues_anything(self) -> None:
        handler, q = _make_handler()
        handler._apply_mod_input_restore_cmd(
            "arm_mod_input_restore",
            {"command": "set_data2_mod_input", "source": 4},
        )

        handler._clear_mod_input_restore_on_teardown()

        assert handler._mod_input_restore is None
        assert q.drain() == []


class TestRunTeardownWiring:
    """A handler without managed authority cannot invent a teardown release."""

    @pytest.mark.asyncio
    async def test_run_without_authority_enqueues_no_disconnect_ptt(self) -> None:
        handler, q = _make_run_handler()
        await handler.run()
        assert q.drain() == []

    @pytest.mark.asyncio
    async def test_dead_egress_does_not_add_a_legacy_release(self) -> None:
        handler, q = _make_run_handler()
        handler._ws.send_text = AsyncMock(
            side_effect=[None, None, ConnectionResetError("egress socket closed")]
        )
        handler._event_queue.put_nowait({"type": "state_update"})
        with pytest.raises(ConnectionResetError):
            await handler.run()
        assert q.drain() == []

    @pytest.mark.asyncio
    async def test_unregister_failure_does_not_add_a_legacy_release(self) -> None:
        handler, q = _make_run_handler(unregister=MagicMock(side_effect=RuntimeError))
        with pytest.raises(RuntimeError):
            await handler.run()
        assert q.drain() == []


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
        sent: Any = handler._ws.send_text.await_args.args[0]
        assert '"ok":true' in sent and '"armed":true' in sent

    @pytest.mark.asyncio
    async def test_read_only_routed_arm_has_no_teardown_effect(self) -> None:
        handler, q = _make_handler(read_only=True)
        handler._ws.send_text = AsyncMock()

        await handler._handle_command(
            {
                "name": "arm_mod_input_restore",
                "params": {"command": "set_data2_mod_input", "source": 3},
                "id": "x",
            }
        )
        handler._clear_mod_input_restore_on_teardown()

        assert handler._mod_input_restore is None
        assert not q.has_commands
        sent: Any = handler._ws.send_text.await_args.args[0]
        assert '"ok":true' in sent and '"armed":false' in sent
