from __future__ import annotations

import asyncio
import struct
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from contextvars import copy_context
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_radio import _wrap_civ_in_udp
from test_radio_connect import (
    ConnectMockTransport,
    _build_conninfo,
    _build_login_response,
    _build_status,
    _FakeSocket,
)

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.core.exceptions import ConnectionError
from rigplane.core.transport import ConnectionState
from rigplane.core.tx_safety import RadioTx, TxOutcome, TxOwner, TxPhase, TxSource
from rigplane.runtime import (
    _civ_rx,
    _control_phase,
    managed_radio_runtime,
    radio as radio_module,
)
from rigplane.runtime import session_lifecycle
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime.radio import CoreRadio
from rigplane.runtime.session_lifecycle import LifecycleEvent, LifecycleState
from rigplane.testing.timing import VirtualClock


class _Transport:
    def __init__(self, events: list[str], label: str) -> None:
        self.events = events
        self.label = label
        self._udp_transport: object | None = object()
        self.my_id = 1
        self.remote_id = 2
        self.send_seq = 0

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


class _RecoveryControl(ConnectMockTransport):
    def __init__(self) -> None:
        super().__init__()
        self._udp_transport: object | None = None
        self._conninfo_notice_callback: Callable[[bytes], None] | None = None
        self.remote_id = 0
        self.discovery: list[int] = []
        self.logins: list[int] = []

    async def connect(self, host: str, port: int, **kwargs: object) -> None:
        self.discovery.append(self.remote_id)
        if self.remote_id:
            raise TimeoutError("retired control session")
        await super().connect(host, port)
        self.my_id += 1
        self.remote_id = 0xDEADBEEF
        self._udp_transport = object()

    async def reconnect(self, host: str, port: int, **kwargs: object) -> None:
        await self.connect(host, port, **kwargs)

    async def disconnect(self) -> None:
        await super().disconnect()
        self.state = ConnectionState.DISCONNECTED
        self._udp_transport = None

    async def send_tracked(
        self, data: bytes, *, is_current: Callable[[], bool] | None = None
    ) -> None:
        await super().send_tracked(data, is_current=is_current)
        if len(data) == 0x80:
            self.logins.append(self.my_id)
            self.queue_response(_build_login_response(token=self.my_id))
            self.queue_response(_build_conninfo())
        elif len(data) == 0x90:
            self.queue_response(_build_status())

    def drop_notice(self) -> bytes:
        data = bytearray(0x90)
        struct.pack_into("<III", data, 0, len(data), 0, self.remote_id)
        struct.pack_into("<I", data, 0x0C, self.my_id)
        return bytes(data)

    def deliver_notice(self, data: bytes) -> None:
        assert self._conninfo_notice_callback is not None
        self._conninfo_notice_callback(data)


class _RecoveryCiv(ConnectMockTransport):
    def __init__(self, ptt: list[bool]) -> None:
        super().__init__()
        self._udp_transport: object | None = None
        self._ptt = ptt
        self.writes: list[bool] = []

    async def connect(
        self,
        host: str,
        port: int,
        *,
        local_host: str | None = None,
        local_port: int = 0,
        sock: object | None = None,
    ) -> None:
        await super().connect(host, port, local_host=local_host, local_port=local_port)
        if isinstance(sock, _FakeSocket):
            sock.close()
        self._udp_transport = object()

    async def disconnect(self) -> None:
        await super().disconnect()
        self._udp_transport = None

    async def send_tracked(
        self, data: bytes, *, is_current: Callable[[], bool] | None = None
    ) -> None:
        await super().send_tracked(data, is_current=is_current)
        payload = data[_civ_rx.CIV_HEADER_SIZE :]
        if not payload.startswith(b"\xfe\xfe"):
            return
        frame = parse_civ_frame(payload)
        if (frame.command, frame.sub) != (0x1C, 0x00):
            return
        if frame.data:
            on = bool(frame.data[0])
            self._ptt[0] = on
            self.writes.append(on)
            reply = build_civ_frame(CONTROLLER_ADDR, frame.to_addr, 0xFB)
        else:
            reply = build_civ_frame(
                CONTROLLER_ADDR, frame.to_addr, 0x1C, 0x00, bytes(self._ptt)
            )
        self.queue_response(_wrap_civ_in_udp(reply))


@asynccontextmanager
async def _recovery_link(
    monkeypatch: pytest.MonkeyPatch, clock: VirtualClock
) -> AsyncIterator[tuple[CoreRadio, _RecoveryControl, list[_RecoveryCiv]]]:
    for module in (
        _civ_rx,
        _control_phase,
        session_lifecycle,
        radio_module,
        managed_radio_runtime,
    ):
        clock.scope_to(monkeypatch, module)
        # Timers run on the virtual loop. Only elapse() should advance it.
        monkeypatch.setattr(module, "asyncio", asyncio)
    monkeypatch.setattr(
        _control_phase,
        "_socket",
        SimpleNamespace(
            AF_INET=2, SOCK_DGRAM=2, socket=lambda *_: _FakeSocket(("192.0.2.2", 50004))
        ),
    )
    radio = CoreRadio("192.0.2.1", model="IC-7610", auto_reconnect=False)
    control = _RecoveryControl()
    civ_ports: list[_RecoveryCiv] = []
    ptt = [False]

    def new_civ() -> _RecoveryCiv:
        civ = _RecoveryCiv(ptt)
        civ_ports.append(civ)
        return civ

    monkeypatch.setattr("rigplane.transport.IcomCivTransport", new_civ)
    monkeypatch.setattr(
        radio, "probe_connection_identity", AsyncMock(return_value=False)
    )
    monkeypatch.setattr(radio, "_start_identity_read", lambda: None)
    monkeypatch.setattr(radio, "_scope_session_ready", AsyncMock())
    monkeypatch.setattr(radio, "_fetch_initial_state", AsyncMock())
    monkeypatch.setattr(radio, "_ensure_audio_transport", AsyncMock())
    radio._ctrl_transport = control  # type: ignore[assignment]
    connect = asyncio.create_task(radio._control_phase._connect_once())
    try:
        await clock.elapse(1)
        assert connect.done()
        connect.result()
        control.discovery.clear()
        control.logins.clear()
        yield radio, control, civ_ports
    finally:
        if not connect.done():
            connect.cancel()
        await asyncio.gather(connect, return_exceptions=True)
        await radio.disconnect()
        await radio._control_phase.release()


@pytest.mark.timeout(10)
async def test_session_free_interrupts_fast_watchdog_join_and_confirms_ptt_off(
    monkeypatch: pytest.MonkeyPatch, virtual_clock: VirtualClock
) -> None:
    monkeypatch.setenv("RIGPLANE_MANAGED_TX", "1")
    async with _recovery_link(monkeypatch, virtual_clock) as (
        radio,
        control,
        civ_ports,
    ):
        arming = asyncio.create_task(radio.rearm_managed_tx())
        await virtual_clock.elapse(0.2)
        assert arming.done()
        arming.result()
        tx = radio._managed_tx_runtime
        assert tx is not None
        keying = asyncio.create_task(tx.request_on(TxOwner(TxSource.SDK, "test")))
        await virtual_clock.elapse(0.2)
        assert keying.done()
        assert keying.result().outcome is TxOutcome.ACCEPTED
        assert civ_ports[0].writes == [True]
        assert tx.tx_snapshot.radio_tx is RadioTx.ON
        radio._last_civ_data_received = None

        runtime = radio._civ_runtime
        watchdog = radio._civ_data_watchdog_task
        assert watchdog is not None and not watchdog.done()
        notice = control.drop_notice()
        control.state = ConnectionState.DISCONNECTED
        stop = runtime.stop_data_watchdog
        full: asyncio.Task[None] | None = None

        def notify_at_join() -> None:
            nonlocal full
            assert watchdog.cancelling()
            control.deliver_notice(notice)
            full = runtime._reconnect_task

        async def stop_at_join() -> None:
            if radio._civ_data_watchdog_task is watchdog and not watchdog.cancelling():
                asyncio.get_running_loop().call_soon(notify_at_join)
            await stop()

        monkeypatch.setattr(runtime, "stop_data_watchdog", stop_at_join)
        soft = asyncio.create_task(radio.soft_reconnect())
        try:
            await virtual_clock.elapse(2)
            assert control.discovery == [0], (
                "the superseded attempt used the old session"
            )
            assert soft.done()
            with pytest.raises(ConnectionError, match="superseded"):
                soft.result()
            assert full is not None and full.done()
            full.result()
            assert len(control.logins) == 1
            assert civ_ports[-1].writes == [False]
            assert tx.tx_snapshot.radio_tx is RadioTx.OFF
            assert tx.tx_snapshot.phase is TxPhase.IDLE
        finally:
            # A stalled baseline can still have both attempts in flight.
            for task in (soft, full):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (soft, full) if task), return_exceptions=True
            )


@pytest.mark.timeout(10)
async def test_detached_soft_predecessor_never_joins_full_replacement(
    monkeypatch: pytest.MonkeyPatch, virtual_clock: VirtualClock
) -> None:
    async with _recovery_link(monkeypatch, virtual_clock) as (
        radio,
        control,
        civ_ports,
    ):
        runtime = radio._civ_runtime
        watchdog = radio._civ_data_watchdog_task
        assert watchdog is not None and not watchdog.done()
        notice = control.drop_notice()
        full: asyncio.Task[None] | None = None

        disconnect = civ_ports[0].disconnect

        async def close_while_superseded() -> None:
            nonlocal full
            assert watchdog.done()
            await disconnect()
            if full is None:
                control.deliver_notice(notice)
                full = runtime._reconnect_task
                # A predecessor whose close absorbs cancellation still
                # must never attach to the full handoff waiting for it.
                with suppress(asyncio.CancelledError):
                    await asyncio.Future[None]()

        monkeypatch.setattr(civ_ports[0], "disconnect", close_while_superseded)
        soft = runtime._spawn_recovery_task()
        try:
            await virtual_clock.elapse(2)
            assert full is not None and full is not soft and full.done(), (
                "full recovery is still waiting for its detached predecessor"
            )
            full.result()
            assert soft.done()
            assert control.discovery == [0]
            assert len(control.logins) == 1
        finally:
            for task in (soft, full):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (soft, full) if task), return_exceptions=True
            )


@pytest.mark.timeout(10)
async def test_duplicate_drop_during_connected_tail_keeps_single_full_login(
    monkeypatch: pytest.MonkeyPatch, virtual_clock: VirtualClock
) -> None:
    async with _recovery_link(monkeypatch, virtual_clock) as (radio, control, _):
        runtime = radio._civ_runtime
        entered = asyncio.Event()
        finish = asyncio.Event()

        async def hold_after_connected() -> bool:
            entered.set()
            await finish.wait()
            return False

        monkeypatch.setattr(radio, "probe_connection_identity", hold_after_connected)
        retired_notice = control.drop_notice()
        control.deliver_notice(retired_notice)
        full = runtime._reconnect_task
        assert full is not None
        await virtual_clock.elapse(1)
        assert entered.is_set() and radio.connected and not full.done()

        control.deliver_notice(retired_notice)
        control.deliver_notice(bytes(0x90))
        observed = runtime._reconnect_task
        finish.set()
        await virtual_clock.elapse(2)
        assert observed is full
        assert full.done()
        full.result()
        assert control.discovery == [0]
        assert len(control.logins) == 1

        control.deliver_notice(retired_notice)
        assert runtime._reconnect_task is None
        control.deliver_notice(control.drop_notice())
        fresh = runtime._reconnect_task
        assert fresh is not None and fresh is not full
        await virtual_clock.elapse(2)
        assert fresh.done()
        fresh.result()
        assert control.discovery == [0, 0]
        assert len(control.logins) == 2


@pytest.mark.timeout(10)
async def test_full_request_preempts_running_soft_recovery(
    monkeypatch: pytest.MonkeyPatch, virtual_clock: VirtualClock
) -> None:
    events: list[str] = []
    radio = _radio(events)
    runtime = radio._civ_runtime
    entered = asyncio.Event()
    unwinding = asyncio.Event()
    finish = asyncio.Event()

    async def attempt() -> None:
        if entered.is_set():
            events.append("reconnect")
            return
        radio._conn_state = RadioConnectionState.CONNECTING
        entered.set()
        try:
            await asyncio.Future[None]()
        finally:
            unwinding.set()
            await finish.wait()

    monkeypatch.setattr(radio._session_lifecycle._mech, "soft_reconnect_once", attempt)
    monkeypatch.setattr(runtime, "start_data_watchdog", lambda: None)
    soft = asyncio.create_task(radio.soft_reconnect())
    full: asyncio.Task[None] | None = None
    try:
        await entered.wait()
        assert runtime.request_recovery_now("session free")
        full = runtime._reconnect_task
        assert full is not None
        await unwinding.wait()
        # Cancelling a soft joiner must leave the full owner alive.
        joiner = asyncio.create_task(radio.soft_reconnect())
        await virtual_clock.elapse(0)
        joiner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await joiner
        assert not full.done()
        assert events == []
        finish.set()
        await full
        with pytest.raises(ConnectionError, match="superseded"):
            await soft
        assert events == ["civ-close", "token-remove", "control-close", "reconnect"]
    finally:
        finish.set()
        for task in (soft, full):
            if task is not None and not task.done():
                task.cancel()
        await asyncio.gather(
            *(task for task in (soft, full) if task is not None),
            return_exceptions=True,
        )
        await radio._control_phase.release()


@pytest.mark.timeout(10)
async def test_repeated_session_free_notices_keep_the_running_full_recovery(
    monkeypatch: pytest.MonkeyPatch, virtual_clock: VirtualClock
) -> None:
    events: list[str] = []
    radio = _radio(events)
    runtime = radio._civ_runtime
    lifecycle = radio._session_lifecycle
    started = asyncio.Event()
    finished = asyncio.Event()
    attempt = 0

    async def reconnect() -> None:
        nonlocal attempt
        attempt += 1
        radio._conn_state = RadioConnectionState.CONNECTING
        started.set()
        await finished.wait()
        radio._conn_state = RadioConnectionState.CONNECTED

    monkeypatch.setattr(lifecycle._mech, "soft_reconnect_once", reconnect)
    notice = bytes(0x90)
    radio._control_phase._on_conninfo_notice(notice)
    full = runtime._reconnect_task
    assert full is not None
    try:
        await started.wait()
        for _ in range(4):
            await virtual_clock.elapse(1)
            radio._control_phase._on_conninfo_notice(notice)
            assert runtime._reconnect_task is full
            assert lifecycle.is_full_recovery_owner(full)

        finished.set()
        await full
        assert attempt == 1
        assert lifecycle.status.state is LifecycleState.CONNECTED
        assert radio._conn_state is RadioConnectionState.CONNECTED
    finally:
        await radio._civ_runtime.stop_data_watchdog()
        await radio.disconnect()
        await asyncio.gather(full, return_exceptions=True)


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

    async def parked_watchdog() -> None:
        await asyncio.Event().wait()

    monkeypatch.setattr(lifecycle._mech, "soft_reconnect_once", fail_first_attempt)
    monkeypatch.setattr(radio, "_force_cleanup_civ", pause_before_join)
    monkeypatch.setattr(radio._civ_runtime, "_civ_data_watchdog_loop", parked_watchdog)
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
        assert radio._civ_data_watchdog_task is None
        assert lifecycle._recovery_waiters == {}
    finally:
        for task in (direct, watchdog):
            if not task.done():
                task.cancel()
        await asyncio.gather(direct, watchdog, return_exceptions=True)
        await radio._civ_runtime.stop_data_watchdog()
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

    async def parked_watchdog() -> None:
        events.append("late-watchdog")
        await asyncio.Event().wait()

    monkeypatch.setattr(lifecycle._mech, "soft_reconnect_once", blocked_attempt)
    monkeypatch.setattr(radio._civ_runtime, "_civ_data_watchdog_loop", parked_watchdog)
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
        assert "late-watchdog" not in events
        assert radio._civ_runtime._reconnect_task is None
        assert radio._civ_data_watchdog_task is None
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
        await radio._civ_runtime.stop_data_watchdog()
        if radio._ctrl_transport._udp_transport is not None:
            await radio._control_phase.release()
