"""Tests for control-session teardown (MOR-624/MOR-993/MOR-1013).

Two concerns share the teardown path and are deliberately kept apart:

* **PTT release** — a writable session that disconnects always requests PTT
  OFF. Unkeying is the safe direction, so no session state may gate it
  (MOR-1013). Read-only sessions are excluded: ``ptt_off`` is a
  ``_TX_COMMANDS`` member they may never issue, so they cannot have keyed.
* **MOD-input bookkeeping** — the frontend auto-LAN feature (MOR-618) arms a
  restore at TX start and disarms it on a clean TX stop. Teardown consumes the
  arm and discards it; no command outcome or queue ordering authorizes a
  previous-source MOD SET (MOR-993).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.profiles import resolve_radio_profile
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.radio_poller import CommandQueue, PttOff, PttOn, RadioPoller

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


def _teardown(handler: ControlHandler) -> None:
    """Run the teardown steps in the same order as ``run()``'s finally block."""
    handler._release_ptt_on_teardown()
    handler._clear_mod_input_restore_on_teardown()


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


def _provider_poller(error: Exception | None) -> tuple[RadioPoller, MagicMock]:
    radio = MagicMock()
    radio.profile = resolve_radio_profile(model="IC-7610")
    radio.capabilities = set(radio.profile.capabilities) - {"audio"}
    # No managed TX runtime: the teardown unkey under test is the legacy
    # ``set_ptt`` write. ``None`` reads unmanaged on every interpreter; a bare
    # Mock does not, because runtime-checkable protocols use hasattr on 3.11
    # and getattr_static on 3.12+ (gh-102433).
    # Full note: the ``mock_radio`` fixture in tests/test_web_server.py.
    radio.managed_tx = None
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
        handler._clear_mod_input_restore_on_teardown()
        assert handler._mod_input_restore is None
        # Bookkeeping alone never reaches the command queue.
        assert not q.has_commands


class TestTeardownModRestoreInvariant:
    """MOR-993: teardown may never replay the remembered MOD SET."""

    @pytest.mark.parametrize("command", _MOD_COMMANDS)
    def test_teardown_when_armed_enqueues_ptt_off_and_no_mod_set(
        self, command: str
    ) -> None:
        handler, q = _make_handler()
        handler._apply_mod_input_restore_cmd(
            "arm_mod_input_restore",
            {"command": command, "source": 0},
        )

        _teardown(handler)

        # Exact list equality: a MOD SET of any kind would show up here.
        assert q.drain() == [PttOff()]
        assert handler._mod_input_restore is None

    def test_clearing_bookkeeping_never_enqueues_anything(self) -> None:
        handler, q = _make_handler()
        handler._apply_mod_input_restore_cmd(
            "arm_mod_input_restore",
            {"command": "set_data2_mod_input", "source": 4},
        )

        handler._clear_mod_input_restore_on_teardown()

        assert handler._mod_input_restore is None
        assert q.drain() == []

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
        _teardown(handler)
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


class TestTeardownPttRelease:
    """MOR-1013: the teardown unkey is not gated on any session state."""

    def test_teardown_when_not_armed_enqueues_ptt_off(self) -> None:
        """Behaviour change: this used to assert that nothing was enqueued.

        A session that keyed but never armed a MOD restore previously
        disconnected with no PTT OFF at all, leaving the rig transmitting.
        """
        handler, q = _make_handler()

        _teardown(handler)

        assert q.drain() == [PttOff()]

    def test_teardown_after_disarm_enqueues_ptt_off(self) -> None:
        """A clean TX stop disarms; the teardown unkey must survive it."""
        handler, q = _make_handler()
        handler._apply_mod_input_restore_cmd(
            "arm_mod_input_restore",
            {"command": "set_data1_mod_input", "source": 2},
        )
        handler._apply_mod_input_restore_cmd("disarm_mod_input_restore", {})

        _teardown(handler)

        assert q.drain() == [PttOff()]

    def test_keyed_session_without_arm_is_released_on_teardown(self) -> None:
        """The reported defect: key via ptt_on, never arm, then disconnect."""
        handler, q = _make_handler()

        handler._enqueue_rc_power("ptt_on", {}, q, handler._radio)
        assert handler._mod_input_restore is None
        _teardown(handler)

        assert q.drain() == [PttOn(), PttOff()]

    def test_release_is_not_suppressed_by_prior_release(self) -> None:
        """No consume-once flag: re-entering teardown re-requests the unkey.

        Idempotency state would be another gate able to swallow the release;
        a duplicate OFF is harmless, a missing one is not.
        """
        handler, q = _make_handler()

        _teardown(handler)
        _teardown(handler)

        assert q.drain() == [PttOff(), PttOff()]

    def test_read_only_teardown_enqueues_nothing(self) -> None:
        """A read-only session may never issue ptt_off, so it cannot have keyed.

        Releasing here would de-key whoever is actually transmitting.
        """
        handler, q = _make_handler(read_only=True)

        _teardown(handler)

        assert q.drain() == []

    def test_teardown_without_server_does_not_raise(self) -> None:
        handler = ControlHandler(
            ws=MagicMock(),
            radio=MagicMock(),
            server_version="test",
            radio_model="IC-7610",
            server=None,
        )

        _teardown(handler)

        assert handler._mod_input_restore is None

    def test_teardown_without_command_queue_does_not_raise(self) -> None:
        handler = ControlHandler(
            ws=MagicMock(),
            radio=MagicMock(),
            server_version="test",
            radio_model="IC-7610",
            server=SimpleNamespace(),
        )

        _teardown(handler)

        assert handler._mod_input_restore is None

    def test_teardown_survives_queue_put_failure(self) -> None:
        """Teardown stays bounded: an enqueue failure must not escape."""
        queue = MagicMock()
        queue.put.side_effect = RuntimeError("queue is gone")
        handler = ControlHandler(
            ws=MagicMock(),
            radio=MagicMock(),
            server_version="test",
            radio_model="IC-7610",
            server=SimpleNamespace(command_queue=queue),
        )
        handler._apply_mod_input_restore_cmd(
            "arm_mod_input_restore",
            {"command": "set_data1_mod_input", "source": 1},
        )

        _teardown(handler)

        queue.put.assert_called_once_with(PttOff())
        assert handler._mod_input_restore is None


class TestRunTeardownWiring:
    """run()'s finally must reach the release before any step that can raise."""

    @pytest.mark.asyncio
    async def test_run_releases_ptt_on_disconnect_without_arm(self) -> None:
        handler, q = _make_run_handler()
        await handler.run()
        assert q.drain() == [PttOff()]

    @pytest.mark.asyncio
    async def test_release_survives_dead_egress_socket(self) -> None:
        """A dead egress socket kills the sender; ``await event_task`` re-raises."""
        handler, q = _make_run_handler()
        handler._ws.send_text = AsyncMock(
            side_effect=[None, None, ConnectionResetError("egress socket closed")]
        )
        handler._enqueue_rc_power("ptt_on", {}, q, handler._radio)
        handler._event_queue.put_nowait({"type": "state_update"})
        with pytest.raises(ConnectionResetError):
            await handler.run()
        assert q.drain() == [PttOn(), PttOff()]

    @pytest.mark.asyncio
    async def test_release_survives_unregister_failure(self) -> None:
        """``unregister_control_event_queue`` broadcasts; it is not raise-free."""
        handler, q = _make_run_handler(unregister=MagicMock(side_effect=RuntimeError))
        handler._enqueue_rc_power("ptt_on", {}, q, handler._radio)
        with pytest.raises(RuntimeError):
            await handler.run()
        assert q.drain() == [PttOn(), PttOff()]


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
        _teardown(handler)

        assert handler._mod_input_restore is None
        assert not q.has_commands
        sent: Any = handler._ws.send_text.await_args.args[0]
        assert '"ok":true' in sent and '"armed":false' in sent
