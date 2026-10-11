"""Simulator reboot / keepalive-silence events against the real client stack.

A production ``IcomRadio`` (control + CI-V ``IcomTransport`` over loopback
UDP, ``auto_reconnect=True``) is connected to a single-owner
``MockIcomRadio``; the events are scheduled on the simulator and the client's
reaction is read from its public ``conn_state`` (and, for the reboot, from
the session token it holds).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncGenerator, Callable

import pytest

from rigplane.radio import IcomRadio
from rigplane.runtime._connection_state import RadioConnectionState

from rigplane_testkit.icom import MockIcomRadio

from _perf_helpers import fast_connect

_WATCHDOG_TIMEOUT_S = 1.5


@pytest.fixture
async def sim() -> AsyncGenerator[MockIcomRadio]:
    server = MockIcomRadio(single_owner=True, keepalive_hold_s=0.5)
    await server.start()
    yield server
    await server.stop()


def _client(sim: MockIcomRadio) -> IcomRadio:
    return IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        timeout=5.0,
        model="IC-7610",
        auto_reconnect=True,
        reconnect_delay=0.2,
        reconnect_max_delay=0.5,
        watchdog_timeout=_WATCHDOG_TIMEOUT_S,
    )


async def _states_until(
    radio: IcomRadio,
    deadline_s: float,
    done: Callable[[list[RadioConnectionState]], bool] = lambda _: False,
) -> list[RadioConnectionState]:
    """Sample ``conn_state`` every 20 ms until ``done`` or the deadline."""
    seen: list[RadioConnectionState] = []
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        state = radio.conn_state
        if not seen or seen[-1] is not state:
            seen.append(state)
        if done(seen):
            break
        await asyncio.sleep(0.02)
    return seen


async def test_reboot_mid_session_client_reconnects_with_new_token(
    sim: MockIcomRadio,
) -> None:
    radio = _client(sim)
    with fast_connect():
        await radio.connect()
        try:
            old_token = radio._token
            assert old_token == sim.token

            sim.reboot(at=0.1, down_for=3.0, busy_for=0.5)

            def reconnected(seen: list[RadioConnectionState]) -> bool:
                return (
                    RadioConnectionState.RECONNECTING in seen
                    and seen[-1] is RadioConnectionState.CONNECTED
                )

            seen = await _states_until(radio, 20.0, reconnected)
            assert reconnected(seen), seen
            assert sim.reboot_count == 1
            assert radio._token == sim.token
            assert radio._token != old_token
            assert await radio.get_freq() == 14_074_000
        finally:
            await radio.disconnect()


async def test_keepalive_silence_shorter_than_watchdog_keeps_session(
    sim: MockIcomRadio,
) -> None:
    radio = _client(sim)
    with fast_connect():
        await radio.connect()
        try:
            sim.keepalive_silence(at=0.1, for_seconds=0.3)
            seen = await _states_until(radio, _WATCHDOG_TIMEOUT_S + 1.5)
            assert seen == [RadioConnectionState.CONNECTED]
            assert radio.connected
        finally:
            await radio.disconnect()


async def test_keepalive_silence_longer_than_watchdog_fires_link_down(
    sim: MockIcomRadio,
) -> None:
    radio = _client(sim)
    with fast_connect():
        await radio.connect()
        try:
            sim.keepalive_silence(at=0.1, for_seconds=_WATCHDOG_TIMEOUT_S + 1.5)
            seen = await _states_until(
                radio,
                _WATCHDOG_TIMEOUT_S + 1.5,
                lambda s: RadioConnectionState.RECONNECTING in s,
            )
            assert seen[0] is RadioConnectionState.CONNECTED
            assert RadioConnectionState.RECONNECTING in seen, seen
        finally:
            await radio.disconnect()


async def test_keepalive_silence_watchdog_recovery_returns_connected(
    sim: MockIcomRadio,
) -> None:
    """After the silence trips the watchdog the client recovers a live session.

    The release the reconnect loop performs must reach the simulator as a
    token remove, and the fresh login must be answered inside the silence
    window (only ping replies are suppressed), so the client ends CONNECTED
    with the session token the simulator currently holds and a working
    CI-V read path.
    """
    radio = _client(sim)
    with fast_connect():
        await radio.connect()
        try:
            sim.keepalive_silence(at=0.1, for_seconds=_WATCHDOG_TIMEOUT_S + 1.5)

            def recovered(seen: list[RadioConnectionState]) -> bool:
                return (
                    RadioConnectionState.RECONNECTING in seen
                    and seen[-1] is RadioConnectionState.CONNECTED
                )

            seen = await _states_until(radio, 12.0, recovered)
            assert recovered(seen), seen
            assert sim.last_release_reason == "token_remove"
            assert radio._token == sim.token
            assert await radio.get_freq() == 14_074_000
        finally:
            await radio.disconnect()
