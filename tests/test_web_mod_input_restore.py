"""Control-session teardown never enqueues a MOD-input restore (MOR-993)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.web.handlers.control import ControlHandler
from rigplane.web.radio_poller import CommandQueue


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
