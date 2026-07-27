"""Tests for backend session-teardown MOD handling (MOR-624/MOR-993).

The frontend auto-LAN feature (MOR-618) arms a restore on the backend
session at TX start and disarms it on a clean TX stop. If the session
tears down while still armed, the handler enqueues PttOff only; no command
outcome or queue ordering authorizes a previous-source MOD SET.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.profiles import resolve_radio_profile
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.radio_poller import CommandQueue, PttOff, RadioPoller

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


def _provider_poller(error: Exception | None) -> tuple[RadioPoller, MagicMock]:
    radio = MagicMock()
    radio.profile = resolve_radio_profile(model="IC-7610")
    radio.capabilities = set(radio.profile.capabilities) - {"audio"}
    radio.set_ptt = AsyncMock(side_effect=error)
    for name in _MOD_COMMANDS:
        setattr(radio, name, AsyncMock())
    return RadioPoller(radio, CommandQueue()), radio


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
        handler._restore_mod_input_on_teardown()
        assert handler._mod_input_restore is None
        assert not q.has_commands


class TestTeardownRestore:
    """An armed teardown consumes its state and enqueues PTT OFF only."""

    @pytest.mark.parametrize("command", _MOD_COMMANDS)
    def test_teardown_when_armed_enqueues_one_ptt_off(self, command: str) -> None:
        handler, q = _make_handler()
        handler._apply_mod_input_restore_cmd(
            "arm_mod_input_restore",
            {"command": command, "source": 0},
        )

        handler._restore_mod_input_on_teardown()
        handler._restore_mod_input_on_teardown()

        assert q.drain() == [PttOff()]
        assert handler._mod_input_restore is None

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "error",
        [
            None,
            TimeoutError("OFF timed out"),
            ConnectionError("radio unreachable"),
            RuntimeError("provider failure"),
        ],
        ids=["success", "timeout", "unreachable", "provider-failure"],
    )
    async def test_off_outcome_never_invokes_mod_provider(
        self, error: Exception | None
    ) -> None:
        handler, q = _make_handler()
        handler._apply_mod_input_restore_cmd(
            "arm_mod_input_restore",
            {"command": "set_data3_mod_input", "source": 3},
        )
        handler._restore_mod_input_on_teardown()
        commands = q.drain()
        assert commands == [PttOff()]

        poller, radio = _provider_poller(error)
        try:
            await poller._execute(commands[0])
        except Exception as exc:
            assert exc is error
        radio.set_ptt.assert_awaited_once_with(False)
        for name in _MOD_COMMANDS:
            getattr(radio, name).assert_not_awaited()

    def test_teardown_when_not_armed_enqueues_nothing(self) -> None:
        handler, q = _make_handler()

        handler._restore_mod_input_on_teardown()

        assert not q.has_commands

    def test_teardown_after_disarm_enqueues_nothing(self) -> None:
        handler, q = _make_handler()
        handler._apply_mod_input_restore_cmd(
            "arm_mod_input_restore",
            {"command": "set_data1_mod_input", "source": 2},
        )
        handler._apply_mod_input_restore_cmd("disarm_mod_input_restore", {})

        handler._restore_mod_input_on_teardown()

        assert not q.has_commands


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
        handler._restore_mod_input_on_teardown()

        assert handler._mod_input_restore is None
        assert not q.has_commands
        sent = handler._ws.send_text.await_args.args[0]
        assert '"ok":true' in sent and '"armed":false' in sent
