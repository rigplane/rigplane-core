from __future__ import annotations

import asyncio
from contextlib import suppress
from contextvars import copy_context

import pytest

from rigplane.core.exceptions import ConnectionError
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime.radio import CoreRadio
from rigplane.runtime.session_lifecycle import LifecycleEvent, LifecycleState


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
async def test_previous_waiter_cleanup_preserves_the_next_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio = _radio([])
    lifecycle = radio._session_lifecycle
    entered_second = asyncio.Event()
    finish_second = asyncio.Event()
    recoveries: list[asyncio.Task[object] | None] = []
    next_calls: list[asyncio.Task[None]] = []
    ownership: list[tuple[bool, bool]] = []
    first_call: asyncio.Task[None] | None = None

    async def attempt() -> None:
        recoveries.append(asyncio.current_task())
        if len(recoveries) == 2:
            assert first_call is not None
            ownership.append(
                (
                    lifecycle.is_current_recovery_awaited_by(first_call),
                    lifecycle.is_current_recovery_awaited_by(next_calls[0]),
                )
            )
            entered_second.set()
            await finish_second.wait()

    def start_next(event: LifecycleEvent) -> None:
        if event.to_state is LifecycleState.CONNECTED and not next_calls:
            next_calls.append(asyncio.create_task(lifecycle.soft_reconnect()))

    monkeypatch.setattr(lifecycle._mech, "soft_reconnect_once", attempt)
    lifecycle.add_event_listener(start_next)
    try:
        first_call = asyncio.create_task(lifecycle.soft_reconnect())
        await first_call
        await entered_second.wait()
        assert ownership == [(False, True)]
        assert lifecycle._recover_task is recoveries[1]
        assert first_call not in lifecycle._recovery_waiters
        assert not lifecycle.is_current_recovery_awaited_by(next_calls[0])
        finish_second.set()
        await next_calls[0]
        assert lifecycle._recover_task is None
        assert lifecycle._recovery_waiters == {}
    finally:
        lifecycle.remove_event_listener(start_next)
        finish_second.set()
        for task in next_calls:
            if not task.done():
                task.cancel()
        await asyncio.gather(*next_calls, return_exceptions=True)
        await radio._control_phase.release()


@pytest.mark.timeout(10)
async def test_exhaustion_cancels_watchdog_that_has_not_joined_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio = _radio([])
    lifecycle = radio._session_lifecycle
    entered = asyncio.Event()
    exhaust = asyncio.Event()
    cleanup_paused = asyncio.Event()
    resume_cleanup = asyncio.Event()
    attempts: list[asyncio.Task[object] | None] = []

    async def fail_first_attempt() -> None:
        attempts.append(asyncio.current_task())
        if len(attempts) == 1:
            entered.set()
            await exhaust.wait()
            raise ConnectionError("injected recovery exhaustion")

    real_cleanup = radio._force_cleanup_civ

    async def pause_before_join() -> None:
        await real_cleanup()
        cleanup_paused.set()
        await resume_cleanup.wait()

    monkeypatch.setattr(lifecycle._mech, "soft_reconnect_once", fail_first_attempt)
    monkeypatch.setattr(radio, "_force_cleanup_civ", pause_before_join)
    monkeypatch.setattr(radio._civ_runtime, "start_data_watchdog", lambda: None)
    direct = asyncio.create_task(radio.soft_reconnect())
    await entered.wait()
    watchdog = asyncio.create_task(radio._civ_runtime._watchdog_recover())
    radio._civ_runtime._reconnect_task = watchdog
    try:
        await cleanup_paused.wait()
        exhaust.set()
        with pytest.raises(ConnectionError, match="Soft reconnect exhausted"):
            await direct
        resume_cleanup.set()
        with suppress(asyncio.CancelledError):
            await watchdog
        assert watchdog.cancelled()
        assert len(attempts) == 1
        assert lifecycle.state is LifecycleState.DISCONNECTED
        assert radio._civ_runtime._reconnect_task is None
        assert lifecycle._recovery_waiters == {}
    finally:
        for task in (direct, watchdog):
            if not task.done():
                task.cancel()
        await asyncio.gather(direct, watchdog, return_exceptions=True)
        if radio._ctrl_transport._udp_transport is not None:
            await radio._control_phase.release()


@pytest.mark.timeout(10)
@pytest.mark.parametrize("instrument_cancel", [True, False], ids=["checked", "real"])
async def test_watchdog_joining_existing_recovery_finishes_release(
    monkeypatch: pytest.MonkeyPatch, instrument_cancel: bool
) -> None:
    events: list[str] = []
    violations: list[str] = []
    radio = _radio(events)
    lifecycle = radio._session_lifecycle
    entered = asyncio.Event()
    joined = asyncio.Event()
    exhaust = asyncio.Event()
    attempts: list[asyncio.Task[object] | None] = []

    async def fail_after_join() -> None:
        attempts.append(asyncio.current_task())
        entered.set()
        await exhaust.wait()
        raise ConnectionError("injected coalesced recovery failure")

    monkeypatch.setattr(lifecycle._mech, "soft_reconnect_once", fail_after_join)
    monkeypatch.setattr(radio._civ_runtime, "start_data_watchdog", lambda: None)
    direct = asyncio.create_task(radio.soft_reconnect())
    await entered.wait()
    shared_recovery = lifecycle._recover_task
    assert shared_recovery is not None and attempts == [shared_recovery]
    real_soft_reconnect = radio.soft_reconnect

    async def observe_join() -> None:
        joined.set()
        assert lifecycle._recover_task is shared_recovery
        await real_soft_reconnect()

    monkeypatch.setattr(radio, "soft_reconnect", observe_join)

    class WatchedRecoveryTask(asyncio.Task[None]):
        def cancel(self, msg: object = None) -> bool:
            if asyncio.current_task() is shared_recovery:
                violations.append("pre-existing recovery cancelled its waiter")
                raise _AncestorCancellationAttempt()
            return super().cancel(msg)

    recovery = radio._civ_runtime._watchdog_recover()
    watchdog = (
        WatchedRecoveryTask(recovery)
        if instrument_cancel
        else asyncio.create_task(recovery)
    )
    radio._civ_runtime._reconnect_task = watchdog
    try:
        await joined.wait()
        assert lifecycle._recover_task is shared_recovery
        exhaust.set()
        outcomes = await asyncio.gather(direct, watchdog, return_exceptions=True)
        assert violations == []
        assert isinstance(outcomes[0], ConnectionError)
        assert outcomes[1] is None
        assert attempts == [shared_recovery]
        assert events == ["civ-close", "token-remove", "control-close"]
        assert lifecycle.state is LifecycleState.DISCONNECTED
        assert watchdog.done() and not watchdog.cancelled()
        assert radio._civ_runtime._reconnect_task is None
        assert lifecycle._recover_task is None
        assert lifecycle._recovery_waiters == {}
    finally:
        for task in (direct, watchdog):
            if not task.done():
                task.cancel()
        await asyncio.gather(direct, watchdog, return_exceptions=True)
        if radio._ctrl_transport._udp_transport is not None:
            await radio._control_phase.release()


@pytest.mark.timeout(10)
@pytest.mark.parametrize("fails", [False, True], ids=["success", "exhausted"])
async def test_inline_recovery_restores_caller_context(
    monkeypatch: pytest.MonkeyPatch, fails: bool
) -> None:
    radio = _radio([])

    async def attempt() -> None:
        if fails:
            raise ConnectionError("injected CI-V connection failure")

    monkeypatch.setattr(radio._session_lifecycle._mech, "soft_reconnect_once", attempt)
    monkeypatch.setattr(radio._civ_runtime, "start_data_watchdog", lambda: None)
    before = dict(copy_context().items())
    try:
        await radio._civ_runtime._watchdog_recover()
        assert dict(copy_context().items()) == before
        assert radio._session_lifecycle._recovery_waiters == {}
    finally:
        if radio._ctrl_transport._udp_transport is not None:
            await radio._control_phase.release()


@pytest.mark.timeout(10)
@pytest.mark.parametrize("instrument_cancel", [True, False], ids=["checked", "real"])
async def test_registered_watchdog_exhaustion_finishes_release(
    monkeypatch: pytest.MonkeyPatch, instrument_cancel: bool
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

    recovery = radio._civ_runtime._watchdog_recover()
    task = (
        WatchedRecoveryTask(recovery)
        if instrument_cancel
        else asyncio.create_task(recovery)
    )
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
        assert lifecycle._recovery_waiters == {}
    finally:
        if radio._ctrl_transport._udp_transport is not None:
            await radio._control_phase.release()


@pytest.mark.timeout(10)
async def test_one_radios_recovery_can_stop_another_radios_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = _radio([])
    second_events: list[str] = []
    second = _radio(second_events)
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def blocked_attempt() -> None:
        entered.set()
        await finish.wait()
        second_events.append("late-reconnect")

    async def stop_other_recovery() -> None:
        await second._civ_runtime.stop_data_watchdog()

    monkeypatch.setattr(
        first._session_lifecycle._mech, "soft_reconnect_once", stop_other_recovery
    )
    monkeypatch.setattr(
        second._session_lifecycle._mech, "soft_reconnect_once", blocked_attempt
    )
    for radio in (first, second):
        monkeypatch.setattr(radio._civ_runtime, "start_data_watchdog", lambda: None)

    second_task = asyncio.create_task(second._civ_runtime._watchdog_recover())
    second._civ_runtime._reconnect_task = second_task
    try:
        await entered.wait()
        first_task = asyncio.create_task(first._civ_runtime._watchdog_recover())
        first._civ_runtime._reconnect_task = first_task
        await first_task
        assert second_task.done() and second_task.cancelled()
        finish.set()
        await asyncio.sleep(0)
        assert "late-reconnect" not in second_events
    finally:
        if not second_task.done():
            second_task.cancel()
        with suppress(asyncio.CancelledError):
            await second_task
        for radio in (first, second):
            await radio._control_phase.release()


@pytest.mark.timeout(10)
@pytest.mark.parametrize("shutdown", [False, True], ids=["stop-watchdog", "shutdown"])
@pytest.mark.parametrize("coalesced", [False, True], ids=["new", "coalesced"])
async def test_external_stop_cancels_registered_recovery(
    monkeypatch: pytest.MonkeyPatch, shutdown: bool, coalesced: bool
) -> None:
    events: list[str] = []
    radio = _radio(events)
    lifecycle = radio._session_lifecycle
    entered = asyncio.Event()
    joined = asyncio.Event()
    finish = asyncio.Event()
    attempts: list[asyncio.Task[object] | None] = []

    async def blocked_attempt() -> None:
        attempts.append(asyncio.current_task())
        entered.set()
        await finish.wait()
        events.append("late-reconnect")

    monkeypatch.setattr(lifecycle._mech, "soft_reconnect_once", blocked_attempt)
    monkeypatch.setattr(radio._civ_runtime, "start_data_watchdog", lambda: None)
    direct = asyncio.create_task(radio.soft_reconnect()) if coalesced else None
    if direct is not None:
        await entered.wait()
    real_soft_reconnect = radio.soft_reconnect

    async def observe_join() -> None:
        joined.set()
        await real_soft_reconnect()

    monkeypatch.setattr(radio, "soft_reconnect", observe_join)
    task = asyncio.create_task(radio._civ_runtime._watchdog_recover())
    radio._civ_runtime._reconnect_task = task
    try:
        await joined.wait()
        await entered.wait()
        if shutdown:
            await lifecycle.request_shutdown()
        else:
            await radio._civ_runtime.stop_data_watchdog()
        with suppress(asyncio.CancelledError):
            await task
        if direct is not None:
            with suppress(asyncio.CancelledError):
                await direct
        finish.set()
        await asyncio.sleep(0)
        assert task.done() and task.cancelled()
        assert len(attempts) == 1
        assert attempts[0] is not None and attempts[0].cancelled()
        assert "late-reconnect" not in events
        assert radio._civ_runtime._reconnect_task is None
        assert lifecycle._recovery_waiters == {}
        if shutdown:
            assert events == ["civ-close", "token-remove", "control-close"]
            assert lifecycle.state is LifecycleState.DISCONNECTED
    finally:
        if not task.done():
            task.cancel()
            with suppress(asyncio.CancelledError):
                await task
        if direct is not None:
            if not direct.done():
                direct.cancel()
            with suppress(asyncio.CancelledError):
                await direct
        if radio._ctrl_transport._udp_transport is not None:
            await radio._control_phase.release()
