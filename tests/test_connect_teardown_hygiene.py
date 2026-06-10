"""Teardown hygiene for failed connect()/disconnect (MOR-595).

A failed ``connect()`` attempt (e.g. the IC-7610 stale-LAN-slot CI-V
data-port discovery timeout) must fully unwind everything it started:

* keepalive tasks (``_ping_loop``/``_idle_loop``/``_retransmit_loop``) are
  cancelled AND awaited before ``IcomTransport.disconnect()`` returns, and
* orphaned fire-and-forget commander futures never surface
  ``Future exception was never retrieved: ConnectionError('Commander
  stopped')`` warnings at GC time.
"""

import asyncio
import gc
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from rigplane.commander import IcomCommander, Priority
from rigplane.exceptions import ConnectionError, TimeoutError
from rigplane.radio import IcomRadio
from rigplane.transport import ConnectionState, IcomTransport
from rigplane.types import CivFrame

from test_radio_connect import (
    ConnectMockTransport,
    _build_login_response,
    _FakeSocket,
)

_LOOP_QUALNAMES = (
    "_ping_loop",
    "_idle_loop",
    "_retransmit_loop",
    "IcomCommander._loop",
    "_civ_rx_loop",
    "_civ_data_watchdog_loop",
)


def _pending_runtime_tasks() -> list["asyncio.Task[Any]"]:
    """Commander/keepalive/pump tasks still pending in the running loop."""
    pending = []
    for task in asyncio.all_tasks():
        if task is asyncio.current_task() or task.done():
            continue
        qualname = getattr(task.get_coro(), "__qualname__", "")
        if any(name in qualname for name in _LOOP_QUALNAMES):
            pending.append(task)
    return pending


class _ExceptionCapture:
    """Loop exception handler capturing 'exception was never retrieved'."""

    def __init__(self) -> None:
        self.contexts: list[dict[str, Any]] = []

    def __call__(
        self, loop: asyncio.AbstractEventLoop, context: dict[str, Any]
    ) -> None:
        if "never retrieved" in context.get("message", ""):
            self.contexts.append(context)


async def _force_gc_turns() -> None:
    """Run GC + event-loop turns so unretrieved future exceptions surface."""
    for _ in range(3):
        gc.collect()
        await asyncio.sleep(0)


class TestTransportDisconnectHygiene:
    async def test_disconnect_cancels_and_awaits_keepalive_tasks(self) -> None:
        transport = IcomTransport()
        transport.state = ConnectionState.CONNECTED
        transport.start_ping_loop()
        transport.start_idle_loop()
        transport.start_retransmit_loop()
        await asyncio.sleep(0)  # let the loops start running

        tasks = [
            transport._ping_task,
            transport._idle_task,
            transport._retransmit_task,
        ]
        assert all(task is not None for task in tasks)

        await transport.disconnect()

        # Teardown must be complete when disconnect() returns: no keepalive
        # task may still be pending in the event loop.
        assert all(task is not None and task.done() for task in tasks)
        assert _pending_runtime_tasks() == []

        # A follow-up connect must be able to restart the loops immediately
        # (a cancelled-but-unawaited task would block the restart check).
        transport.state = ConnectionState.CONNECTED
        transport.start_ping_loop()
        assert transport._ping_task is not tasks[0]
        assert transport._ping_task is not None
        assert not transport._ping_task.done()
        await transport.disconnect()
        assert _pending_runtime_tasks() == []


class TestCommanderStopHygiene:
    async def test_stop_retrieves_fire_and_forget_future_exceptions(self) -> None:
        release = asyncio.Event()

        async def execute(cmd: bytes, wait_response: bool = True) -> CivFrame | None:
            await release.wait()
            return None

        capture = _ExceptionCapture()
        loop = asyncio.get_running_loop()
        old_handler = loop.get_exception_handler()
        loop.set_exception_handler(capture)
        try:
            commander = IcomCommander(execute, min_interval=0.0)
            commander.start()
            # Fire-and-forget background polls: nobody ever awaits these
            # futures (the live IC-7610 stale-slot flood, MOR-595).
            for _ in range(5):
                await commander.send(
                    b"\xfe\xfe\x98\xe0\x15\x02\xfd",
                    priority=Priority.BACKGROUND,
                    wait_response=False,
                    wait_dispatch=False,
                )
            await asyncio.sleep(0.01)  # worker picks up the first item
            await commander.stop()
            release.set()
            await _force_gc_turns()
        finally:
            loop.set_exception_handler(old_handler)

        assert capture.contexts == []


class _TimeoutCivTransport(ConnectMockTransport):
    """CI-V transport whose data-port discovery always times out."""

    async def connect(
        self,
        host: str,
        port: int,
        *,
        local_host: str | None = None,
        local_port: int = 0,
        sock: object | None = None,
    ) -> None:
        await super().connect(
            host, port, local_host=local_host, local_port=local_port, sock=sock
        )
        raise TimeoutError("Radio did not respond to discovery after 10 attempts")


class TestFailedConnectRetryHygiene:
    async def test_failed_discovery_then_retry_leaves_no_orphans(self) -> None:
        """Stale-LAN-slot: discovery timeout, then a clean successful retry."""
        radio = IcomRadio("192.168.1.100", username="u", password="p")
        ctrl = radio._ctrl_transport  # real IcomTransport — real keepalive tasks
        assert isinstance(ctrl, IcomTransport)

        async def _fake_ctrl_connect(*args: object, **kwargs: object) -> None:
            ctrl.state = ConnectionState.CONNECTING

        timeout_transports = [_TimeoutCivTransport() for _ in range(4)]
        success_transport = ConnectMockTransport()
        fake_sockets = [_FakeSocket(("192.168.2.194", 50010 + i)) for i in range(10)]

        capture = _ExceptionCapture()
        loop = asyncio.get_running_loop()
        old_handler = loop.get_exception_handler()
        loop.set_exception_handler(capture)
        try:
            with (
                patch.object(
                    ctrl, "connect", new=AsyncMock(side_effect=_fake_ctrl_connect)
                ),
                patch.object(
                    radio._control_phase,
                    "_resolve_local_bind_host",
                    return_value="192.168.2.194",
                ),
                patch(
                    "rigplane._control_phase._socket.socket",
                    side_effect=fake_sockets,
                ),
                patch.object(
                    radio._control_phase, "_status_retry_pause", return_value=0.0
                ),
                patch.object(
                    radio._control_phase,
                    "_wait_for_packet",
                    new=AsyncMock(return_value=_build_login_response()),
                ),
                patch.object(radio._control_phase, "_send_token_ack", new=AsyncMock()),
                patch.object(
                    radio._control_phase,
                    "_receive_guid",
                    new=AsyncMock(return_value=b"\x00" * 16),
                ),
                patch.object(radio._control_phase, "_send_conninfo", new=AsyncMock()),
                patch.object(
                    radio._control_phase,
                    "_receive_civ_port",
                    new=AsyncMock(return_value=50002),
                ),
                patch.object(
                    radio._control_phase, "_flush_queue", new=AsyncMock(return_value=0)
                ),
                patch.object(radio._control_phase, "_start_token_renewal"),
                patch.object(radio._control_phase, "_start_watchdog"),
                patch.object(radio, "_fetch_initial_state", new=AsyncMock()),
                patch(
                    "rigplane.transport.IcomTransport",
                    side_effect=[*timeout_transports, success_transport],
                ),
                patch(
                    "rigplane.runtime._control_phase.wait_for_radio_startup_ready",
                    new=AsyncMock(),
                ),
                # NOTE: deliberately NOT patching asyncio.sleep — this test
                # runs the REAL ctrl keepalive loops, and patching the module
                # attribute would turn them into never-yielding hot spins.
                # Cooldown pauses are already 0.0 via _status_retry_pause.
            ):
                # Attempt 1: CI-V data-port discovery times out on every
                # cooldown retry → connect() fails.
                with pytest.raises(ConnectionError):
                    await radio.connect()

                # The failed attempt must not leave commander/keepalive tasks
                # pending in the event loop.
                assert _pending_runtime_tasks() == []

                # Attempt 2 on the SAME radio: the stale slot was released.
                await radio.connect()
                assert radio.connected

                # Simulate background poll bursts that the live scheduler
                # enqueues as fire-and-forget sends, then tear down while
                # most of them are still queued (paced at ~35 ms/item).
                for _ in range(8):
                    await radio.send_civ(
                        0x15,
                        sub=0x02,
                        wait_response=False,
                        priority=Priority.BACKGROUND,
                        wait_dispatch=False,
                    )
                await radio.disconnect()

                # Nothing pending immediately after teardown returns…
                assert _pending_runtime_tasks() == []

            # …and no orphaned commander futures surface at GC time.
            await _force_gc_turns()
        finally:
            loop.set_exception_handler(old_handler)

        assert capture.contexts == []
        assert not radio.connected
