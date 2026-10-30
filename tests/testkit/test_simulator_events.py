"""Simulator reboot / keepalive-silence events against the real client stack.

A production ``IcomRadio`` (control + CI-V ``IcomTransport`` over loopback
UDP, ``auto_reconnect=True``) is connected to a single-owner
``MockIcomRadio``; the events are scheduled on the simulator and the client's
reaction is read from its public ``conn_state`` (and, for the reboot, from
the session token it holds).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator, Callable
from unittest.mock import patch

import pytest

from rigplane.radio import IcomRadio
from rigplane.runtime import radio_reconnect
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

            # MOR-3920 repro: a 25 s busy window forces the post-dark
            # reconnect attempt into the status-stage busy reject on the
            # profile-selected stereo codec, which parks the attempt in the
            # 30 s _STATUS_REJECT_COOLDOWN and blows the 20 s sampling
            # deadline below deterministically. The committed 0.5 s window
            # loses this lottery only sometimes (CI runs 41008174982,
            # 41039389222, 41058873610).
            sim.reboot(at=0.1, down_for=3.0, busy_for=25.0)

            def reconnected(seen: list[RadioConnectionState]) -> bool:
                # The RECONNECTING window can be shorter than the 20 ms
                # sampling step, so the reconnect evidence is the status
                # callback, not the sampled states.
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
    so the reconnect evidence is the status callback, not the sampled
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


async def test_user_disconnect_during_reconnect_ends_loop(
    sim: MockIcomRadio,
) -> None:
    """A user ``disconnect()`` during RECONNECTING ends the reconnect loop.

    The disconnect lands during the backoff sleep that follows a failed
    attempt: by then the lifecycle is already released and unclaimed and
    ``conn_state`` is RECONNECTING, so neither the lifecycle disconnect
    nor the CONNECTED-gated control-phase teardown stops the loop —
    ``disconnect()`` must stop it explicitly, or the client logs back in
    after the user has disconnected.
    """
    radio = _client(sim, reconnect_delay=3.0, reconnect_max_delay=3.0)
    statuses: list[dict] = []
    radio.set_reconnect_status_callback(statuses.append)
    with fast_connect():
        await radio.connect()
        try:
            # Long outage: the attempt fails against a radio that is
            # off, so the failure is a discovery timeout, not a busy
            # reject.
            sim.reboot(at=0.1, down_for=22.0, busy_for=0.0)
            radio_back_at = time.monotonic() + 0.1 + 22.0

            attempt_failed = asyncio.Event()

            class _FailureHandler(logging.Handler):
                def emit(self, record: logging.LogRecord) -> None:
                    msg = record.getMessage()
                    if "Reconnect attempt" in msg and "failed" in msg:
                        attempt_failed.set()

            failure_handler = _FailureHandler(level=logging.WARNING)
            radio_reconnect.logger.addHandler(failure_handler)
            try:
                await asyncio.wait_for(attempt_failed.wait(), timeout=40.0)
            finally:
                radio_reconnect.logger.removeHandler(failure_handler)

            await radio.disconnect()  # the user's disconnect, mid-backoff

            n_status = len(statuses)
            # Watch past the radio's return: a loop that survived the
            # user's disconnect logs back in as soon as the radio answers
            # again, so the window must cover it.
            await asyncio.sleep(max(0.0, radio_back_at - time.monotonic()) + 5.0)
            assert statuses[n_status:] == [], statuses[n_status:]
            assert radio.conn_state is RadioConnectionState.DISCONNECTED
            assert radio._reconnect_task is None or radio._reconnect_task.done()
            # No new login claimed the simulator's session.
            assert sim.session_held is False
        finally:
            await radio.disconnect()


async def _assert_user_disconnect_ends_loop(inject_at: str) -> None:
    """Start the user's ``disconnect()`` from inside the reconnect loop task
    the first time the loop calls ``inject_at`` (a ``CivRuntime`` method, or
    ``connect`` on the radio), then assert the loop ends with no new login.
    """
    server = MockIcomRadio(single_owner=True, keepalive_hold_s=2.0)
    await server.start()
    try:
        radio = _client(server)
        statuses: list[dict] = []
        radio.set_reconnect_status_callback(statuses.append)
        user_task: asyncio.Task[None] | None = None
        loop_task: asyncio.Task[None] | None = None
        logins_at_stop = -1
        with fast_connect():
            await radio.connect()
            target: object = radio if inject_at == "connect" else radio._civ_runtime
            original = getattr(target, inject_at)

            async def spy(*args: object, **kwargs: object) -> object:
                nonlocal user_task, loop_task, logins_at_stop
                current = asyncio.current_task()
                if user_task is None and current is radio._reconnect_task:
                    loop_task = current
                    logins_at_stop = server.login_count
                    user_task = asyncio.ensure_future(radio.disconnect())
                return await original(*args, **kwargs)

            try:
                with patch.object(target, inject_at, spy):
                    server.keepalive_silence(at=0.1, for_seconds=3.0)
                    deadline = time.monotonic() + 15.0
                    while user_task is None and time.monotonic() < deadline:
                        await asyncio.sleep(0.02)
                    assert user_task is not None, (
                        f"disconnect never injected at the loop's {inject_at}",
                        statuses,
                    )
                    await asyncio.wait_for(user_task, timeout=10.0)
                    n_status = len(statuses)
                    # Watch past the silence window: a loop that survived
                    # the user's disconnect logs back in as soon as the
                    # radio answers again, so the window must cover it.
                    await asyncio.sleep(7.0)
                assert statuses[n_status:] == [], statuses[n_status:]
                assert radio.conn_state is RadioConnectionState.DISCONNECTED
                assert loop_task is not None
                assert loop_task.done()
                # No new login after the user's stop, and none holds the
                # simulator's session.
                assert server.login_count == logins_at_stop
                assert server.session_held is False
            finally:
                if loop_task is not None and not loop_task.done():
                    loop_task.cancel()
                    with contextlib.suppress(BaseException):
                        await loop_task
                with contextlib.suppress(Exception):
                    await radio.disconnect()
    finally:
        await server.stop()


@pytest.mark.parametrize("helper", ["stop_data_watchdog", "stop_pump"])
async def test_user_disconnect_inside_loop_release_ends_loop(helper: str) -> None:
    """A user ``disconnect()`` landing inside the loop's own release ends it.

    The reconnect loop awaits ``CoreRadioSessionLifecycle.disconnect()`` at
    the start of every round.  Inside that release the CI-V helper stops
    cancel their helper tasks and await them; ``stop_data_watchdog``
    swallows the ``CancelledError`` of that await, which is also how the
    ``CoreRadio.disconnect`` cancel aimed at the loop task arrives there.
    The owner stop still reaches the loop through
    ``CoreRadio._owner_stop_epoch``, which the loop re-reads when the
    release returns.
    """
    await _assert_user_disconnect_ends_loop(helper)


async def test_user_disconnect_at_loop_connect_entry_ends_loop() -> None:
    """A user ``disconnect()`` started as the loop enters ``connect()``
    ends the loop with no new login."""
    await _assert_user_disconnect_ends_loop("connect")


@pytest.mark.parametrize("failure_point", ["identity_gate", "startup_readiness"])
async def test_failed_attempt_inside_loop_connect_keeps_the_loop(
    failure_point: str,
) -> None:
    """A failure inside the loop's own connect attempt is one failed round.

    The attempt's teardown runs inside the lifecycle connect task the loop
    awaits.  It must not stop the loop: the loop retries, logs in, and the
    handle still names the same, never-cancelled task.  The identity of the
    failed attempt is retired before the next round.
    """
    server = MockIcomRadio(single_owner=True, keepalive_hold_s=2.0)
    await server.start()
    try:
        radio = _client(server)
        statuses: list[dict] = []
        radio.set_reconnect_status_callback(statuses.append)
        failed_in: list[asyncio.Task[None]] = []
        identity_after_failure: list[object] = []

        def take_injection() -> bool:
            loop = radio._reconnect_task
            if failed_in or loop is None or loop.done():
                return False
            if asyncio.current_task() is not radio._session_lifecycle._connect_task:
                return False
            failed_in.append(loop)
            return True

        class _FailedRound(logging.Handler):
            def emit(self, record: logging.LogRecord) -> None:
                msg = record.getMessage()
                if (
                    failed_in
                    and not identity_after_failure
                    and "Reconnect attempt" in msg
                    and "failed" in msg
                ):
                    identity_after_failure.append(radio.connection_identity)

        handler = _FailedRound(level=logging.WARNING)
        with fast_connect():
            await radio.connect()
            orig_gate = radio._gate_lan_identity

            async def gate(*, revalidate_origin: bool = False) -> bool:
                if failure_point == "identity_gate" and take_injection():
                    raise RuntimeError("injected identity-gate failure")
                return await orig_gate(revalidate_origin=revalidate_origin)

            async def readiness(*args: object, **kwargs: object) -> None:
                if failure_point == "startup_readiness" and take_injection():
                    raise RuntimeError("injected startup-readiness failure")

            radio_reconnect.logger.addHandler(handler)
            try:
                with (
                    patch.object(radio, "_gate_lan_identity", gate),
                    patch(
                        "rigplane.runtime._control_phase.wait_for_radio_startup_ready",
                        readiness,
                    ),
                ):
                    server.keepalive_silence(at=0.1, for_seconds=3.0)
                    deadline = time.monotonic() + 15.0
                    while not failed_in and time.monotonic() < deadline:
                        await asyncio.sleep(0.02)
                    assert failed_in, ("no failure injected in the loop", statuses)
                    loop_task = failed_in[0]

                    def recovered(seen: list[RadioConnectionState]) -> bool:
                        return (
                            seen[-1] is RadioConnectionState.CONNECTED
                            and bool(statuses)
                            and statuses[-1]["state"] == "connected"
                        )

                    seen = await _states_until(radio, 10.0, recovered)
                    assert recovered(seen), (seen, statuses)
                    assert radio._reconnect_task is loop_task
                    await asyncio.wait_for(loop_task, timeout=5.0)
                    assert not loop_task.cancelled()
                    assert loop_task.cancelling() == 0
                    assert identity_after_failure == [None]
                    await _wait_usable(radio, 5.0)
                    assert radio.connected
                    assert server.session_held is True
                    assert radio._token == server.token
                await radio.disconnect()
                assert server.session_held is False
            finally:
                radio_reconnect.logger.removeHandler(handler)
                for task in failed_in:
                    if not task.done():
                        task.cancel()
                        with contextlib.suppress(BaseException):
                            await task
                with contextlib.suppress(Exception):
                    await radio.disconnect()
    finally:
        await server.stop()


async def test_reboot_busy_window_longer_than_reconnect_rounds_recovers(
    sim: MockIcomRadio,
) -> None:
    """A busy window spanning several reconnect rounds still recovers.

    The reconnect task is pinned to be the SAME task object across the
    busy window: a per-attempt release that stopped the loop would end
    reconnecting inside the window instead of recovering after it.

    Two deliberate differences from the bench shape, both forced by this
    simulator's model:
    ``down_for`` is 3.0 s, not 0.5 s — the simulator answers control pings
    from any client outside the down window, so a 0.5 s outage (0.5 s of
    ping silence) never trips the 1.5 s packet watchdog and no reconnect
    happens at all; and the client passes an explicit rx codec — with the
    profile-selected stereo codec a status-stage busy reject enters
    ``_connect_once``'s busy-retry loop with the 30 s
    ``_STATUS_REJECT_COOLDOWN`` pause, which parks the first connect()
    and cannot meet the 20 s deadline. The explicit codec makes each
    busy connect() fail fast, so what is pinned here is the reconnect
    loop's own retry policy: several rounds inside the busy window, then
    a clean recovery.
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

            # Capture the resident loop's task when the second attempt
            # announces itself — the first attempt's release has already
            # run by then, so the handle is only non-None if that release
            # preserved it.
            deadline = time.monotonic() + 20.0
            while time.monotonic() < deadline and not any(
                s["state"] == "reconnecting" and s["attempt"] >= 2 for s in statuses
            ):
                await asyncio.sleep(0.02)
            assert any(
                s["state"] == "reconnecting" and s["attempt"] >= 2 for s in statuses
            ), statuses
            loop_task = radio._reconnect_task
            assert loop_task is not None
            assert not loop_task.done()

            def recovered(seen: list[RadioConnectionState]) -> bool:
                return (
                    seen[-1] is RadioConnectionState.CONNECTED
                    and bool(statuses)
                    and statuses[-1]["state"] == "connected"
                )

            seen = await _states_until(radio, 20.0, recovered)
            assert recovered(seen), seen
            assert radio._reconnect_task is loop_task
            await asyncio.wait_for(loop_task, timeout=5.0)
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
