"""Recovery after a full radio power-cycle.

Pins the CI-V data watchdog's exhaustion handoff to the resident reconnect
loop and the control-transport state gate in
``ControlPhaseRuntime.soft_reconnect``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from rigplane.core.exceptions import ConnectionError as RigplaneConnectionError
from rigplane.core.exceptions import TimeoutError as RigplaneTimeoutError
from rigplane.core.transport import ConnectionState
from rigplane.radio import IcomRadio
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime.session_lifecycle import AttemptOutcome, AttemptResult


def _exhaustion_radio() -> tuple[IcomRadio, list[dict]]:
    """Radio whose next lifecycle recovery exhausts on its only attempt."""
    radio = IcomRadio(
        "127.0.0.1",
        model="IC-7610",
        auto_reconnect=True,
        reconnect_delay=0.05,
    )

    async def _noop_disconnect() -> None:
        return None

    radio._civ_transport = None
    radio._ctrl_transport = SimpleNamespace(
        _udp_transport=object(),
        disconnect=_noop_disconnect,
    )
    statuses: list[dict] = []
    radio._on_reconnect_status = statuses.append

    lifecycle = radio._session_lifecycle
    lifecycle._max_recovery_attempts = 1
    lifecycle._recovery_backoff_s = (0.0,)

    async def always_fail() -> None:
        raise RigplaneConnectionError("injected per-attempt failure")

    lifecycle._mech.soft_reconnect_once = always_fail  # type: ignore[method-assign]

    async def noop_cleanup() -> None:
        return None

    radio._force_cleanup_civ = noop_cleanup  # type: ignore[method-assign]
    return radio, statuses


@pytest.mark.parametrize("failure", ["timeout", "busy_reject"])
async def test_handed_off_loop_survives_failed_attempts_until_user_disconnect(
    failure: str,
) -> None:
    """Recovery exhaustion hands off to the real resident reconnect loop.

    ``CoreRadioSessionLifecycle._attempt_with_release`` releases the session
    after every failed connect attempt; that release must not end the loop
    that drives the attempts, while the user's ``disconnect`` does."""
    radio, statuses = _exhaustion_radio()
    status_states: list[RadioConnectionState] = []

    def record_status(status: dict) -> None:
        statuses.append(status)
        status_states.append(radio._conn_state)

    radio._on_reconnect_status = record_status
    lifecycle = radio._session_lifecycle
    # One mechanism attempt per loop round, so each busy reject ends the
    # round instead of entering the lifecycle's own resident retry.
    lifecycle._max_connect_attempts = 1
    entry_states: list[RadioConnectionState] = []
    parked = asyncio.Event()
    unpark = asyncio.Event()

    async def connect_attempt() -> AttemptResult:
        entry_states.append(radio._conn_state)
        if len(entry_states) >= 3:
            parked.set()
            await unpark.wait()
        if failure == "timeout":
            raise RigplaneTimeoutError("radio still booting")
        return AttemptResult(AttemptOutcome.SESSION_BUSY_REJECT)

    loop_task: asyncio.Task[None] | None = None
    try:
        with (
            patch.object(lifecycle._mech, "connect_attempt", connect_attempt),
            patch.object(radio._civ_runtime, "start_data_watchdog", lambda: None),
        ):
            await radio._civ_runtime._watchdog_recover()
            loop_task = radio._reconnect_task
            assert loop_task is not None, "exhaustion must start the resident loop"
            assert statuses == [
                {
                    "state": "reconnecting",
                    "attempt": 1,
                    "next_retry_seconds": radio._reconnect_delay,
                }
            ]
            parked_wait = asyncio.create_task(parked.wait())
            await asyncio.wait(
                {parked_wait, loop_task},
                timeout=5.0,
                return_when=asyncio.FIRST_COMPLETED,
            )
            parked_wait.cancel()
            assert not loop_task.done(), "a failed attempt ended the loop"
            assert radio._reconnect_task is loop_task
            assert len(entry_states) == 3
            assert entry_states == [RadioConnectionState.RECONNECTING] * 3
            assert radio._conn_state is RadioConnectionState.RECONNECTING
            assert [s["attempt"] for s in statuses] == [1, 1, 2, 3]
            assert status_states == [RadioConnectionState.RECONNECTING] * 4

            await radio.disconnect()
            await asyncio.wait({loop_task}, timeout=2.0)
            assert loop_task.done()
            assert radio._conn_state is RadioConnectionState.DISCONNECTED
            await asyncio.sleep(0.3)
            assert len(entry_states) == 3
            assert len(statuses) == 4
    finally:
        unpark.set()
        if loop_task is not None and not loop_task.done():
            radio._conn_state = RadioConnectionState.DISCONNECTED
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)
        radio._conn_state = RadioConnectionState.DISCONNECTED


async def test_user_disconnect_during_lifecycle_recovery_starts_no_loop() -> None:
    """A user ``disconnect`` while the escalated recovery's lifecycle attempt
    is in flight cancels that recovery; the cancellation is not taken for
    exhaustion, so no reconnect loop starts afterwards."""
    radio, statuses = _exhaustion_radio()
    entered = asyncio.Event()
    unpark = asyncio.Event()

    async def parked_attempt() -> None:
        entered.set()
        await unpark.wait()

    radio._session_lifecycle._mech.soft_reconnect_once = parked_attempt  # type: ignore[method-assign]
    radio._conn_state = RadioConnectionState.CONNECTED
    radio._civ_runtime.request_recovery_now(
        "radio reported the LAN session free (busy=0)"
    )
    recovery = radio._civ_runtime._reconnect_task
    assert recovery is not None
    try:
        await asyncio.wait_for(entered.wait(), 5.0)
        await radio.disconnect()
        await asyncio.wait({recovery}, timeout=2.0)
        assert recovery.cancelled()
        assert radio._reconnect_task is None
        assert radio._conn_state is RadioConnectionState.DISCONNECTED
        await asyncio.sleep(0.3)
        assert radio._reconnect_task is None
        assert statuses == []
    finally:
        unpark.set()
        if not recovery.done():
            recovery.cancel()
            await asyncio.gather(recovery, return_exceptions=True)
        loop_task = radio._reconnect_task
        if loop_task is not None and not loop_task.done():
            radio._conn_state = RadioConnectionState.DISCONNECTED
            loop_task.cancel()
            await asyncio.gather(loop_task, return_exceptions=True)
        radio._conn_state = RadioConnectionState.DISCONNECTED


async def _stop_handed_off_loop(radio: IcomRadio) -> None:
    radio._conn_state = RadioConnectionState.DISCONNECTED
    loop_task = radio._reconnect_task
    if loop_task is not None and not loop_task.done():
        loop_task.cancel()
        await asyncio.gather(loop_task, return_exceptions=True)


async def test_exhaustion_after_an_owner_stop_starts_no_loop() -> None:
    """Recovery that exhausts after an owner stop (``CoreRadio.disconnect``
    increments ``_owner_stop_epoch``) hands nothing to the reconnect loop."""
    radio, statuses = _exhaustion_radio()

    async def owner_stop_then_fail() -> None:
        radio._owner_stop_epoch += 1
        raise RigplaneConnectionError("injected per-attempt failure")

    radio._session_lifecycle._mech.soft_reconnect_once = owner_stop_then_fail  # type: ignore[method-assign]
    try:
        with patch.object(radio._civ_runtime, "start_data_watchdog", lambda: None):
            await radio._civ_runtime._watchdog_recover()
        assert radio._conn_state is RadioConnectionState.DISCONNECTED
        assert radio._reconnect_task is None
        assert statuses == []
    finally:
        await _stop_handed_off_loop(radio)


async def test_user_disconnect_during_recovery_cleanup_starts_no_loop() -> None:
    """A user ``disconnect`` that lands while ``_watchdog_recover`` is in
    ``_force_cleanup_civ`` — before the lifecycle recovery exists for the
    disconnect to cancel — starts no reconnect loop when that recovery
    later exhausts."""
    radio, statuses = _exhaustion_radio()
    user_disconnects: list[asyncio.Task[None]] = []

    async def cleanup_with_user_disconnect() -> None:
        user = asyncio.ensure_future(radio.disconnect())
        user_disconnects.append(user)
        await asyncio.wait({user})

    radio._force_cleanup_civ = cleanup_with_user_disconnect  # type: ignore[method-assign]
    try:
        with patch.object(radio._civ_runtime, "start_data_watchdog", lambda: None):
            await radio._civ_runtime._watchdog_recover()
        assert len(user_disconnects) == 1
        assert user_disconnects[0].exception() is None
        assert radio._conn_state is RadioConnectionState.DISCONNECTED
        assert radio._reconnect_task is None
        assert statuses == []
    finally:
        await _stop_handed_off_loop(radio)


async def test_exhaustion_handoff_does_not_stack_loops() -> None:
    """A handoff while the resident loop is alive leaves that loop in place."""
    radio, _statuses = _exhaustion_radio()
    radio._conn_state = RadioConnectionState.DISCONNECTED
    started = asyncio.Event()
    release = asyncio.Event()

    async def parked() -> None:
        started.set()
        await release.wait()

    alive = asyncio.create_task(parked())
    try:
        await started.wait()
        radio._reconnect_task = alive
        radio._civ_runtime._hand_off_exhaustion_to_reconnect_loop(
            RigplaneConnectionError("Soft reconnect exhausted after 1 attempts"),
            radio._owner_stop_epoch,
        )
        assert radio._reconnect_task is alive
        assert radio._conn_state is RadioConnectionState.DISCONNECTED
    finally:
        release.set()
        await alive


class _RefusedTransport:
    """Transport double whose connect always fails the local port bind."""

    async def connect(self, *args: object, **kwargs: object) -> None:
        raise OSError(48, "Address already in use")


async def test_soft_reconnect_full_connect_when_ctrl_never_logged_in() -> None:
    """A control transport ``_connect_once`` never marked CONNECTED is no
    session to reuse.

    ``IcomTransport.connect``/``reconnect`` leave the transport CONNECTING;
    only ``_connect_once`` marks it CONNECTED. After a failed full
    connect the control UDP socket can still be open, and the CI-V-rebuild
    branch then declared a session that does not exist. With the
    login gate the rebuild branch requires a CONNECTED control transport and
    this recovery takes the full-connect branch."""
    radio = IcomRadio("127.0.0.1", model="IC-7610")
    radio._civ_transport = None
    radio._ctrl_transport = SimpleNamespace(
        _udp_transport=object(),
        state=ConnectionState.CONNECTING,
    )
    connect = AsyncMock()
    radio.rearm_managed_tx = AsyncMock()  # type: ignore[method-assign]
    radio._ensure_audio_transport = AsyncMock()  # type: ignore[method-assign]

    try:
        with (
            patch("rigplane.transport.IcomTransport", _RefusedTransport),
            patch.object(radio._control_phase, "_connect_once", connect),
            patch.object(
                radio,
                "_read_lan_identity_payload",
                new=AsyncMock(return_value=b"\x98"),
            ),
        ):
            await radio._control_phase.soft_reconnect()
        assert connect.await_count == 1
    finally:
        radio._conn_state = RadioConnectionState.DISCONNECTED
