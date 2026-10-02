from __future__ import annotations

import asyncio
from contextlib import suppress

import pytest

from rigplane.core.exceptions import ConnectionError
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime.radio import CoreRadio
from rigplane.runtime.session_lifecycle import LifecycleState


class _Transport:
    def __init__(self, events: list[str], label: str) -> None:
        self.events = events
        self.label = label
        self._udp_transport: object | None = object()
        self.my_id = 1
        self.remote_id = 2

    async def send_tracked(self, packet: bytes) -> None:
        if len(packet) == 0x40 and packet[0x15] == 0x01:
            self.events.append("token-remove")

    async def disconnect(self) -> None:
        self.events.append(f"{self.label}-close")
        self._udp_transport = None


def _radio(events: list[str]) -> CoreRadio:
    radio = CoreRadio("192.0.2.1", model="IC-7610")
    radio._ctrl_transport = _Transport(events, "control")  # type: ignore[assignment]
    radio._civ_transport = _Transport(events, "civ")  # type: ignore[assignment]
    radio._conn_state = RadioConnectionState.CONNECTED
    radio._token = 1
    radio._session_lifecycle._max_recovery_attempts = 1
    radio._session_lifecycle._recovery_backoff_s = (0.0,)
    return radio


class _AncestorCancellationAttempt(BaseException):
    pass


@pytest.mark.timeout(10)
async def test_registered_watchdog_exhaustion_finishes_release(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    violations: list[str] = []
    radio = _radio(events)
    lifecycle = radio._session_lifecycle

    async def fail_attempt() -> None:
        raise ConnectionError("injected CI-V connection failure")

    monkeypatch.setattr(lifecycle._mech, "soft_reconnect_once", fail_attempt)
    monkeypatch.setattr(radio._civ_runtime, "start_data_watchdog", lambda: None)

    class WatchedRecoveryTask(asyncio.Task[None]):
        def cancel(self, msg: object = None) -> bool:
            if asyncio.current_task() is lifecycle._recover_task:
                violations.append("recovery child cancelled its waiting watchdog")
                # Abort before the broken code can await its own waiting parent.
                raise _AncestorCancellationAttempt()
            return super().cancel(msg)

    task = WatchedRecoveryTask(radio._civ_runtime._watchdog_recover())
    radio._civ_runtime._reconnect_task = task
    try:
        with suppress(_AncestorCancellationAttempt):
            await task
        assert violations == []
        assert events == ["civ-close", "token-remove", "control-close"]
        assert lifecycle.state is LifecycleState.DISCONNECTED
        assert radio._conn_state is RadioConnectionState.DISCONNECTED
        assert task.done() and not task.cancelled()
        assert radio._civ_runtime._reconnect_task is None
        assert lifecycle._recover_task is None
    finally:
        if radio._ctrl_transport._udp_transport is not None:
            await radio._control_phase.release()


@pytest.mark.timeout(10)
@pytest.mark.parametrize("shutdown", [False, True], ids=["stop-watchdog", "shutdown"])
async def test_external_stop_cancels_registered_recovery(
    monkeypatch: pytest.MonkeyPatch, shutdown: bool
) -> None:
    events: list[str] = []
    radio = _radio(events)
    lifecycle = radio._session_lifecycle
    entered = asyncio.Event()
    finish = asyncio.Event()
    attempts: list[asyncio.Task[object] | None] = []

    async def blocked_attempt() -> None:
        attempts.append(asyncio.current_task())
        entered.set()
        await finish.wait()
        events.append("late-reconnect")

    monkeypatch.setattr(lifecycle._mech, "soft_reconnect_once", blocked_attempt)
    monkeypatch.setattr(radio._civ_runtime, "start_data_watchdog", lambda: None)
    task = asyncio.create_task(radio._civ_runtime._watchdog_recover())
    radio._civ_runtime._reconnect_task = task
    try:
        await entered.wait()
        if shutdown:
            await lifecycle.request_shutdown()
        else:
            await radio._civ_runtime.stop_data_watchdog()
        with suppress(asyncio.CancelledError):
            await task
        finish.set()
        await asyncio.sleep(0)
        assert task.done() and task.cancelled()
        assert len(attempts) == 1
        assert attempts[0] is not None and attempts[0].cancelled()
        assert "late-reconnect" not in events
        assert radio._civ_runtime._reconnect_task is None
        if shutdown:
            assert events == ["civ-close", "token-remove", "control-close"]
            assert lifecycle.state is LifecycleState.DISCONNECTED
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if radio._ctrl_transport._udp_transport is not None:
            await radio._control_phase.release()
