"""A client restarted after an unclean death inherits the dead CI-V stream state (MOR-3418 phase 1).

Characterization only: no production change. Lane A (real IC-7610, captures
w41-w50) found that after a client dies WITHOUT logout the radio keeps that
client's CI-V stream state (frontier + gap list) and the next client inherits
it: packets at/behind the frontier are silently ignored, so a restarted
production client (tracked CI-V seq restarts at 0, advances ~10/s while
waiting) stays deaf for roughly frontier/10 s.

The simulator models this behind opt-in knobs (slice G, pinned by
``tests/testkit/test_simulator_civ_orphan.py``); these tests run a production
``IcomRadio`` as the dead client AND as the restarted client:

1. ``test_restarted_client_stays_deaf_behind_orphan_frontier`` — RED on main:
   B must be CONNECTED with identity answered within 10 s; it stays
   NO_RESPONSE / not connected behind the inherited frontier.
2. ``test_clean_logout_restart_connects`` — control, GREEN on main.
3. ``test_expiry_restart_connects`` — control, GREEN on main.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time

import pytest

from rigplane.core.radio_protocol import RadioIdentityStatus
from rigplane.core.transport import ConnectionState
from rigplane.radio import IcomRadio
from rigplane.runtime._connection_state import RadioConnectionState

from rigplane_testkit.icom import MockIcomRadio

from _perf_helpers import fast_connect

_HOST = "127.0.0.1"
_CONNECTED = RadioConnectionState.CONNECTED

# Frontier the dead client leaves behind. A restarted production client
# numbers from 0 at ~10 tracked packets/s while waiting, so reaching 320
# naturally takes >= 30 s — well past the 10 s bound below, which cleanly
# separates "fixed" from "natural catch-up".
_FRONTIER_TARGET = 320
# Bound a fixed client must meet: CONNECTED with identity answered this soon
# after its login is accepted.
_RESTART_BOUND_S = 10.0


def _orphan_sim(**kwargs) -> MockIcomRadio:
    """Single-owner simulator with orphan CI-V state on (slice G knobs)."""
    kwargs.setdefault("single_owner", True)
    kwargs.setdefault("keepalive_hold_s", 0.3)
    kwargs.setdefault("civ_state_survives_unclean_end", True)
    return MockIcomRadio(**kwargs)


def _client(sim: MockIcomRadio) -> IcomRadio:
    """A fresh production client (default settings, as after a restart)."""
    return IcomRadio(
        host=_HOST,
        port=sim.control_port,
        username="testuser",
        password="testpass",
        timeout=5.0,
        model="IC-7610",
    )


async def _run_until_frontier(radio: IcomRadio, target: int) -> int:
    """Drive the client's tracked CI-V seq to ``target`` via identity reads."""
    for _ in range(target + 200):
        assert radio._civ_transport is not None
        if radio._civ_transport.send_seq >= target:
            break
        await radio.get_freq()
    assert radio._civ_transport is not None
    frontier = radio._civ_transport.send_seq
    assert frontier >= target, f"client stalled at tracked seq {frontier}"
    return frontier


async def _kill_uncleanly(radio: IcomRadio) -> None:
    """Silence a production client the way a dead process does: no packets.

    Stops every client loop and closes the UDP sockets WITHOUT the logout
    sequence (no CI-V OpenClose(close), no token-remove, no disconnect
    packet). The simulator therefore frees the session through the hold
    expiry (``keepalive_timeout``) and keeps the CI-V stream state as orphan
    state — the unclean path; any of the three logout packets would clear it
    (``MockIcomRadio._release_session``: every reason but
    ``keepalive_timeout`` drops ``_civ_orphan``).
    """
    cp = radio._control_phase
    cp._stop_token_renewal()
    cp._stop_watchdog()
    cp._stop_reconnect()
    for stopper in (
        "_stop_civ_data_watchdog",
        "_stop_civ_worker",
        "_stop_civ_rx_pump",
        "_stop_audio_watchdog",
    ):
        with contextlib.suppress(Exception):
            await getattr(radio, stopper)()
    radio._cancel_lan_identity_reread()
    stream = getattr(radio, "_audio_stream", None)
    if stream is not None:
        with contextlib.suppress(Exception):
            await stream.stop_rx()
        with contextlib.suppress(Exception):
            await stream.stop_tx()
    for transport in (
        radio._civ_transport,
        radio._audio_transport,
        radio._ctrl_transport,
    ):
        if transport is None:
            continue
        pending = [
            task
            for attr in ("_ping_task", "_idle_task", "_retransmit_task")
            if (task := getattr(transport, attr, None)) is not None
            and not task.done()
        ]
        for task in pending:
            task.cancel()
        udp = getattr(transport, "_udp_transport", None)
        if udp is not None:
            udp.close()  # raw close: no DISCONNECT packet on the wire
        transport._udp_transport = None
        transport.state = ConnectionState.DISCONNECTED
        for task in pending:
            with contextlib.suppress(
                asyncio.CancelledError, Exception
            ):
                await asyncio.wait_for(task, 2.0)
    radio._conn_state = RadioConnectionState.DISCONNECTED


async def _wait_hold_released(sim: MockIcomRadio, budget_s: float = 5.0) -> bool:
    """True once the simulator holds no session (bounded wait)."""
    deadline = time.monotonic() + budget_s
    while sim.session_held:
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.01)
    return True


def _answered(radio: IcomRadio) -> bool:
    """CONNECTED with the identity question answered (not hold-owned)."""
    if radio.conn_state is not _CONNECTED or not radio.connected:
        return False
    identity = radio.connection_identity
    return identity is not None and identity.status in (
        RadioIdentityStatus.VERIFIED,
        RadioIdentityStatus.UNVERIFIED,
    )


async def _wait_answered(radio: IcomRadio, budget_s: float) -> bool:
    """True once ``_answered`` holds (bounded poll every 50 ms)."""
    deadline = time.monotonic() + budget_s
    while time.monotonic() < deadline:
        if _answered(radio):
            return True
        await asyncio.sleep(0.05)
    return _answered(radio)


async def test_restarted_client_stays_deaf_behind_orphan_frontier(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """RED on main: a restarted client stays deaf behind the orphan frontier.

    A disinherited production client B numbers from 0 while the radio still
    holds A's frontier (>= 320): every B packet lands at/behind it and is
    silently ignored, so B's identity read is never answered and B never
    reaches CONNECTED within the bound. A fix must make B CONNECTED with
    identity answered within the bound.
    """
    caplog.set_level(logging.INFO, logger="rigplane")
    sim = _orphan_sim()
    await sim.start()
    b: IcomRadio | None = None
    try:
        a = _client(sim)
        with fast_connect():
            await a.connect()
        assert a.conn_state is _CONNECTED and a.connected, "A never connected"
        frontier = await _run_until_frontier(a, _FRONTIER_TARGET)
        await _kill_uncleanly(a)

        assert await _wait_hold_released(sim), "held session never expired"
        assert sim.last_release_reason == "keepalive_timeout", (
            f"session ended {sim.last_release_reason!r}, not by hold expiry: "
            "the death was not unclean"
        )
        orphan = sim._civ_orphan
        assert orphan is not None, "no orphan state kept after the unclean end"
        assert orphan[0] == frontier, (
            f"orphan frontier {orphan[0]} != A's tracked seq {frontier}"
        )

        # The production client renews, so lengthen the hold: the fresh
        # session must stay held through the asserts below.
        sim.keepalive_hold_s = 30.0
        b = _client(sim)
        first_record = len(caplog.records)
        with fast_connect():
            await b.connect()  # returns: the identity gate gets no answer
        inherited = [t for t in sim._civ_gap.values() if t.inherited]
        assert len(inherited) == 1, "B's open did not seed one inherited tracker"
        tracker = inherited[0]
        assert tracker.next_expected == frontier, (
            f"inherited frontier {tracker.next_expected} != {frontier}"
        )
        assert tracker.inherited_armed is False
        logins_after_login = sim.login_count

        answered = await _wait_answered(b, _RESTART_BOUND_S)

        requests = list(sim.civ_retransmit_request_log)
        assert requests == [], (
            f"radio asked for fills while B stayed behind the frontier: {requests}"
        )
        assert sim.login_count == logins_after_login, (
            f"B re-logged in ({sim.login_count - logins_after_login} extra logins)"
        )
        dead_lines = [
            record.getMessage()
            for record in caplog.records[first_record:]
            if record.name == "rigplane" or record.name.startswith("rigplane.")
        ]
        dead_lines = [line for line in dead_lines if "control session dead" in line]
        identity = b.connection_identity
        report = (
            f"B answered={answered} connected={b.connected} "
            f"state={b.conn_state.value} "
            f"identity={identity.status if identity is not None else None} "
            f"A frontier={frontier} B tracked seq={b._civ_transport.send_seq} "
            f"inherited_armed={tracker.inherited_armed} "
            f"retransmit requests={len(requests)} "
            f"extra logins={sim.login_count - logins_after_login} "
            f"control-session-dead lines={len(dead_lines)}"
        )
        assert answered, f"RED (MOR-3418): restarted client stays deaf: {report}"
    finally:
        if b is not None:
            await _kill_uncleanly(b)
        await sim.stop()


async def test_clean_logout_restart_connects() -> None:
    """Control, GREEN on main: after a clean logout B connects in the bound."""
    sim = _orphan_sim()
    await sim.start()
    b: IcomRadio | None = None
    try:
        a = _client(sim)
        with fast_connect():
            await a.connect()
        assert a.conn_state is _CONNECTED and a.connected, "A never connected"
        frontier = await _run_until_frontier(a, _FRONTIER_TARGET)
        await asyncio.wait_for(a.disconnect(), 10.0)

        assert not sim.session_held, "clean logout did not free the session"
        assert sim._civ_orphan is None, "clean logout left orphan state behind"

        sim.keepalive_hold_s = 30.0
        b = _client(sim)
        with fast_connect():
            await b.connect()
        assert await _wait_answered(b, _RESTART_BOUND_S), (
            f"B not CONNECTED within {_RESTART_BOUND_S} s after a clean logout "
            f"(A frontier was {frontier})"
        )
        assert await asyncio.wait_for(b.get_freq(), 5.0) == 14_074_000
    finally:
        if b is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(b.disconnect(), 10.0)
        await sim.stop()


async def test_expiry_restart_connects() -> None:
    """Control, GREEN on main: past the orphan expiry B connects in the bound."""
    sim = _orphan_sim(orphan_civ_state_expiry_s=2.0)
    await sim.start()
    b: IcomRadio | None = None
    try:
        a = _client(sim)
        with fast_connect():
            await a.connect()
        assert a.conn_state is _CONNECTED and a.connected, "A never connected"
        frontier = await _run_until_frontier(a, _FRONTIER_TARGET)
        await _kill_uncleanly(a)

        assert await _wait_hold_released(sim), "held session never expired"
        assert sim._civ_orphan is not None, "no orphan state kept after the unclean end"
        await asyncio.sleep(2.5)  # outlast the 2 s orphan expiry
        assert sim._civ_orphan is None, "orphan state survived its expiry"

        sim.keepalive_hold_s = 30.0
        b = _client(sim)
        with fast_connect():
            await b.connect()
        assert await _wait_answered(b, _RESTART_BOUND_S), (
            f"B not CONNECTED within {_RESTART_BOUND_S} s past the orphan expiry "
            f"(A frontier was {frontier})"
        )
        assert await asyncio.wait_for(b.get_freq(), 5.0) == 14_074_000
    finally:
        if b is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(b.disconnect(), 10.0)
        await sim.stop()
