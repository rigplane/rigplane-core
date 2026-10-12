"""Recovery after a full radio power-cycle (MOR-3296).

Second PR for the power-cycle defect family: the CI-V data watchdog's
recovery handoff, the control-socket login gate in ``soft_reconnect``, and
the resident reconnect loop that restarts recovery after the lifecycle
exhausts. The packet-count watchdog's own ``reconnect_loop`` fix lives in a
sibling PR; nothing here touches it.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rigplane.core.exceptions import ConnectionError as RigplaneConnectionError
from rigplane.core.transport import ConnectionState
from rigplane.radio import IcomRadio
from rigplane.runtime._connection_state import RadioConnectionState

from rigplane_testkit.icom import MockIcomRadio

from _perf_helpers import fast_connect


@pytest.fixture
async def sim() -> AsyncGenerator[MockIcomRadio]:
    server = MockIcomRadio(single_owner=True, keepalive_hold_s=0.5)
    await server.start()
    yield server
    await server.stop()


def _client(radio_sim: MockIcomRadio) -> IcomRadio:
    return IcomRadio(
        host="127.0.0.1",
        port=radio_sim.control_port,
        username="testuser",
        password="testpass",
        timeout=5.0,
        model="IC-7610",
        auto_reconnect=True,
        reconnect_delay=0.2,
        reconnect_max_delay=0.5,
    )


async def _states_until(
    radio: IcomRadio,
    deadline_s: float,
    done: Callable[[list[RadioConnectionState]], bool] = lambda _: False,
) -> list[RadioConnectionState]:
    """Sample ``conn_state`` every 50 ms until ``done(seen)`` or the deadline."""
    seen: list[RadioConnectionState] = []
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        state = radio.conn_state
        if not seen or seen[-1] is not state:
            seen.append(state)
        if done(seen):
            break
        await asyncio.sleep(0.05)
    return seen


async def test_power_cycle_recovery_relogins_with_new_token(sim: MockIcomRadio) -> None:
    """A radio power-cycle must end in a fresh session, not a dead client.

    The client keeps the DEFAULT ``watchdog_timeout`` (30 s) so the CI-V data
    watchdog, not the packet-count watchdog, drives recovery. At the defect
    the lifecycle's recovery exhausts and nothing restarts it: the client
    stays down for good. With the handoff to the resident reconnect loop the
    client re-logins after the radio returns and answers commands with the
    radio's post-reboot token (MOR-3296)."""
    radio = _client(sim)
    statuses: list[dict] = []
    radio._on_reconnect_status = statuses.append
    with fast_connect():
        await radio.connect()
    try:
        old_token = radio._token
        assert old_token == sim.token

        sim.reboot(at=0.1, down_for=3.0, busy_for=0.5)

        def recovered(seen: list[RadioConnectionState]) -> bool:
            return radio.connected and radio._token != old_token

        seen = await _states_until(radio, 25.0, recovered)
        assert recovered(seen), (seen, radio._token, sim.token, statuses)
        assert sim.reboot_count == 1
        assert radio._token == sim.token
        assert radio._token != old_token
        freq = await asyncio.wait_for(radio.get_freq(), timeout=5.0)
        assert freq == 14_074_000
        assert any(s.get("state") == "reconnecting" for s in statuses)
    finally:
        await radio.disconnect()


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


async def test_watchdog_recover_hands_exhaustion_to_resident_reconnect_loop() -> None:
    """Lifecycle recovery exhaustion must start the resident reconnect loop.

    ``CoreRadioSessionLifecycle._run_recover`` raises its exhaustion
    ``ConnectionError`` only after releasing the session and leaving the host
    DISCONNECTED. Without the handoff nothing restarts recovery there, so the
    client stayed down after a radio power-cycle (MOR-3296)."""
    radio, statuses = _exhaustion_radio()

    loop_entries: list[RadioConnectionState] = []
    release_loop = asyncio.Event()

    async def resident_loop() -> None:
        loop_entries.append(radio._conn_state)
        radio._conn_state = RadioConnectionState.DISCONNECTED
        await release_loop.wait()

    radio._reconnect_loop = resident_loop  # type: ignore[method-assign]

    try:
        with patch.object(radio._civ_runtime, "start_data_watchdog", MagicMock()):
            await radio._civ_runtime._watchdog_recover()
        task = radio._reconnect_task
        assert task is not None, "exhaustion must start the resident loop"
        assert loop_entries == [RadioConnectionState.RECONNECTING]
        assert statuses == [
            {
                "state": "reconnecting",
                "attempt": 1,
                "next_retry_seconds": radio._reconnect_delay,
            }
        ]
        release_loop.set()
        await task
    finally:
        release_loop.set()
        task = radio._reconnect_task
        if task is not None and not task.done():
            await task
        radio._conn_state = RadioConnectionState.DISCONNECTED


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
            RigplaneConnectionError("Soft reconnect exhausted after 1 attempts")
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
    """A control transport that never completed a login is no session to reuse.

    ``IcomTransport.connect``/``reconnect`` leave the transport CONNECTING;
    only the end of ``_connect_once`` marks it CONNECTED. After a failed full
    connect the control UDP socket can still be open, and the CI-V-rebuild
    branch then declared a session that does not exist (MOR-3296). With the
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
        connect.assert_awaited_once()
    finally:
        radio._conn_state = RadioConnectionState.DISCONNECTED
