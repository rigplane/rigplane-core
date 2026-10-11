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


async def _wait_usable(radio: IcomRadio, deadline_s: float) -> None:
    """Wait until ``radio.connected`` (transport healthy, identity accepted).

    ``conn_state`` turns CONNECTED before the reconnect's identity gate has
    answered, so a test that asserts right after the state flip races the
    gate; ``connected`` is false until the radio has answered its ``19 00``
    identification.
    """
    end = time.monotonic() + deadline_s
    while time.monotonic() < end:
        if radio.connected:
            return
        await asyncio.sleep(0.02)


async def test_reboot_mid_session_client_reconnects_with_new_token(
    sim: MockIcomRadio,
) -> None:
    radio = _client(sim)
    statuses: list[dict] = []
    radio.set_reconnect_status_callback(statuses.append)
    with fast_connect():
        await radio.connect()
        try:
            old_token = radio._token
            assert old_token == sim.token

            sim.reboot(at=0.1, down_for=3.0, busy_for=0.5)

            def reconnected(seen: list[RadioConnectionState]) -> bool:
                # The RECONNECTING window can be shorter than the 20 ms
                # sampling step, so the reconnect evidence is the status
                # callback (emitted synchronously by watchdog_loop, one per
                # loop attempt), not the sampled states.
                return (
                    seen[-1] is RadioConnectionState.CONNECTED
                    and bool(statuses)
                    and statuses[-1]["state"] == "connected"
                )

            seen = await _states_until(radio, 20.0, reconnected)
            assert reconnected(seen), seen
            assert any(s["state"] == "reconnecting" for s in statuses), statuses
            assert statuses[-1]["state"] == "connected", statuses
            await _wait_usable(radio, 5.0)
            assert radio.connected
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


async def test_keepalive_silence_longer_than_watchdog_fires_link_down() -> None:
    """Silence longer than the watchdog trips it and the client recovers.

    The simulator here holds a session for ``keepalive_hold_s=2.0`` rather
    than the shared fixture's 0.5 s: that equals the client's control ping
    period (``transport.py: PING_PERIOD``), so during the silence window
    the simulator's own keepalive timer races every ping and can release
    the session on its own (reason ``keepalive_timeout``), overwriting the
    ``token_remove`` record this test asserts.  With the longer hold the
    client's release is the only release.

    The RECONNECTING window can be shorter than the 20 ms sampling step,
    so the reconnect evidence is the status callback (emitted
    synchronously by watchdog_loop, one per loop attempt), not the sampled
    states.
    """
    server = MockIcomRadio(single_owner=True, keepalive_hold_s=2.0)
    await server.start()
    try:
        radio = _client(server)
        statuses: list[dict] = []
        radio.set_reconnect_status_callback(statuses.append)
        with fast_connect():
            await radio.connect()
            try:
                server.keepalive_silence(at=0.1, for_seconds=_WATCHDOG_TIMEOUT_S + 1.5)

                def recovered(seen: list[RadioConnectionState]) -> bool:
                    return (
                        seen[-1] is RadioConnectionState.CONNECTED
                        and bool(statuses)
                        and statuses[-1]["state"] == "connected"
                    )

                seen = await _states_until(radio, 12.0, recovered)
                assert seen[0] is RadioConnectionState.CONNECTED
                assert recovered(seen), seen
                assert any(s["state"] == "reconnecting" for s in statuses), statuses
                assert statuses[-1]["state"] == "connected", statuses
                await _wait_usable(radio, 5.0)
                assert radio.connected
                assert server.last_release_reason == "token_remove"
                assert radio._token == server.token
                assert await radio.get_freq() == 14_074_000
            finally:
                await radio.disconnect()
    finally:
        await server.stop()
