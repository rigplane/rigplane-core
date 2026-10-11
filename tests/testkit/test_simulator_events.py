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
from rigplane.types import AudioCodec

from rigplane_testkit.icom import MockIcomRadio

from _perf_helpers import fast_connect

_WATCHDOG_TIMEOUT_S = 1.5


@pytest.fixture
async def sim() -> AsyncGenerator[MockIcomRadio]:
    server = MockIcomRadio(single_owner=True, keepalive_hold_s=0.5)
    await server.start()
    yield server
    await server.stop()


def _client(sim: MockIcomRadio, **overrides) -> IcomRadio:
    kwargs = dict(
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
    kwargs.update(overrides)
    return IcomRadio(**kwargs)


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


async def test_reboot_busy_window_longer_than_reconnect_rounds_recovers(
    sim: MockIcomRadio,
) -> None:
    """A busy window spanning several reconnect rounds still recovers.

    A real IC-7610 busy-rejected new logins for ~90 s after the previous
    client died without a release, so the reconnect loop must keep backing
    off across a busy window longer than one connect() call instead of
    treating the reject as permanent.

    Two deliberate differences from the bench shape, both forced by this
    simulator's model (verified with a probe against this stack):
    ``down_for`` is 3.0 s, not 0.5 s — the simulator answers control pings
    from any client outside the down window, so a 0.5 s outage (0.5 s of
    ping silence) never trips the 1.5 s packet watchdog and no reconnect
    happens at all; and the client passes an explicit rx codec — with the
    profile-selected stereo codec a status-stage busy reject enters
    ``_connect_once``'s busy-retry loop with the 30 s
    ``_STATUS_REJECT_COOLDOWN`` pause, which parks the first connect()
    for ~30 s (recovery at ~34 s, one busy reject) and cannot meet the
    20 s deadline. The explicit codec makes each busy connect() fail
    fast, so what is pinned here is the reconnect loop's own retry
    policy: several rounds inside the busy window, then a clean recovery.
    """
    radio = _client(
        sim,
        audio_codec=AudioCodec.PCM_2CH_16BIT,
        audio_codec_explicit=True,
    )
    statuses: list[dict] = []
    radio.set_reconnect_status_callback(statuses.append)
    with fast_connect():
        await radio.connect()
        try:
            old_token = radio._token
            assert old_token == sim.token

            sim.reboot(at=0.1, down_for=3.0, busy_for=4.0)

            def recovered(seen: list[RadioConnectionState]) -> bool:
                return (
                    seen[-1] is RadioConnectionState.CONNECTED
                    and bool(statuses)
                    and statuses[-1]["state"] == "connected"
                )

            seen = await _states_until(radio, 20.0, recovered)
            assert recovered(seen), seen
            assert sim.busy_rejects >= 2, sim.busy_rejects
            assert sum(1 for s in statuses if s["state"] == "reconnecting") >= 2, (
                statuses
            )
            assert statuses[-1]["state"] == "connected", statuses
            await _wait_usable(radio, 5.0)
            assert radio.connected
            assert sim.reboot_count == 1
            assert radio._token == sim.token
            assert radio._token != old_token
            assert await radio.get_freq() == 14_074_000
        finally:
            await radio.disconnect()
