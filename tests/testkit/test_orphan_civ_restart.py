"""A client restarted after an unclean death inherits the dead CI-V stream state (MOR-3418).

Lane A (real IC-7610, captures w41-w50) found that after a client dies
WITHOUT logout the radio keeps that client's CI-V stream state (frontier + gap
list) and the next client inherits it: packets at/behind the frontier are
silently ignored, so a restarted production client (tracked CI-V seq restarts
at 0, advances ~10/s while waiting) stays deaf for roughly frontier/10 s.

The simulator models this behind opt-in knobs (slice G, pinned by
``tests/testkit/test_simulator_civ_orphan.py``); these tests run a production
``IcomRadio`` as the dead client AND as the restarted client. The client
records its CI-V seq while connected (``runtime/_civ_seq_persistence.py``) and
a fresh login starts just past that record:

1. ``test_restarted_client_connects_past_orphan_frontier`` — B must be
   CONNECTED with identity answered within 10 s. Without the record (phase 1,
   commit 273a9c4c) it stayed NO_RESPONSE / not connected behind the
   inherited frontier.
2. ``test_clean_logout_restart_connects`` — control.
3. ``test_expiry_restart_connects`` — control.
4. ``test_restart_without_a_record_reports_the_held_session`` and
   ``test_restart_short_of_the_frontier_reports_the_wait`` — a B that still
   stays behind the frontier says so through ``last_error``.
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
from rigplane.runtime import _civ_seq_persistence as civ_seq
from rigplane.runtime._connection_state import RadioConnectionState

from rigplane_testkit.icom import MockIcomRadio

from _perf_helpers import fast_connect

_HOST = "127.0.0.1"
_CONNECTED = RadioConnectionState.CONNECTED

# The GUID the simulator sends at login (``MockIcomRadio._guid_conninfo``).
_SIM_GUID = bytes(range(0x01, 0x11))
# Record interval for these tests (the shipped value is 5 s).
_RECORD_INTERVAL_S = 0.2
# Wait the status line shows at this interval: the step is
# ceil(0.2 * ADVANCE_RATE_PER_S) + ADVANCE_MARGIN = 3 + 30 = 33 packets, and
# ceil(33 / FILL_RATE_PER_S) = 4 s.
_WAIT_AT_TEST_INTERVAL_S = 4

# Frontier the dead client leaves behind. A restarted production client
# numbers from 0 at ~10 tracked packets/s while waiting, so reaching 320
# naturally takes >= 30 s — well past the 10 s bound below, which cleanly
# separates "fixed" from "natural catch-up".
_FRONTIER_TARGET = 320
# Bound a fixed client must meet: CONNECTED with identity answered this soon
# after its login is accepted.
_RESTART_BOUND_S = 10.0


@pytest.fixture(autouse=True)
def _short_record_interval(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(civ_seq, "WRITE_INTERVAL_S", _RECORD_INTERVAL_S)


def _orphan_sim(**kwargs) -> MockIcomRadio:
    """Single-owner simulator with orphan CI-V state on (slice G knobs).

    The hold is 2.0 s: longer than the production client's ~0.5 s ping
    period, so a live session survives a multi-second run (its pings keep
    re-arming the hold), and short enough that a dead session releases
    within ~2 s of its last refresh.
    """
    kwargs.setdefault("single_owner", True)
    kwargs.setdefault("keepalive_hold_s", 2.0)
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
    """Drive the client's tracked CI-V seq to ``target`` via frequency reads."""
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
    cp._stop_civ_seq_persistence()
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
        pending = []
        for attr in ("_ping_task", "_idle_task", "_retransmit_task"):
            task = getattr(transport, attr, None)
            if task is not None and not task.done():
                pending.append(task)
        for task in pending:
            task.cancel()
        udp = getattr(transport, "_udp_transport", None)
        if udp is not None:
            udp.close()  # raw close: no DISCONNECT packet on the wire
        transport._udp_transport = None
        transport.state = ConnectionState.DISCONNECTED
        for task in pending:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await asyncio.wait_for(task, 2.0)
    radio._conn_state = RadioConnectionState.DISCONNECTED


async def _wait_hold_released(sim: MockIcomRadio, budget_s: float = 8.0) -> bool:
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


async def test_restarted_client_connects_past_orphan_frontier(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A restarted client starts past the orphan frontier from A's record.

    The radio holds A's frontier (>= 320). Numbering from 0, every B packet
    would land at/behind it and be silently ignored, so B's identity read
    would never be answered. B reads A's record and starts just past it, so
    its packets pass the frontier and B reaches CONNECTED with identity
    answered within the bound.
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
        await _run_until_frontier(a, _FRONTIER_TARGET)
        # Let A record its numbering again after the loop above, then kill
        # it. A sends nothing after the kill, so its numbering then is the
        # frontier it leaves behind.
        await asyncio.sleep(3 * _RECORD_INTERVAL_S)
        await _kill_uncleanly(a)
        assert a._civ_transport is not None
        frontier = a._civ_transport.send_seq
        recorded = civ_seq.read_record(_HOST, sim.control_port, _SIM_GUID, time.time())
        assert recorded is not None, "A never recorded its numbering"
        assert 0 <= frontier - recorded < civ_seq.advance(), (
            f"A's record {recorded} is not within one step of its frontier {frontier}"
        )

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
        assert orphan[1] == frozenset(), f"orphan gap list is not empty: {orphan[1]}"

        # The production client renews slowly: lengthen the hold so the
        # fresh session stays held through the asserts below.
        sim.keepalive_hold_s = 30.0
        b = _client(sim)
        first_record = len(caplog.records)
        with fast_connect():
            await b.connect()
        assert sim._civ_orphan is None, "B's open did not consume the orphan"
        inherited = [t for t in sim._civ_gap.values() if t.inherited]
        assert len(inherited) == 1, "B's open did not seed one inherited tracker"
        tracker = inherited[0]
        assert tracker.inherited_frontier == frontier, (
            f"inherited frontier {tracker.inherited_frontier} != {frontier}"
        )
        assert tracker.inherited_armed is True, "B's packets never passed the frontier"
        logins_after_login = sim.login_count

        answered = await _wait_answered(b, _RESTART_BOUND_S)

        requests = list(sim.civ_retransmit_request_log)
        assert sim.login_count == logins_after_login, (
            f"B re-logged in ({sim.login_count - logins_after_login} extra logins)"
        )
        dead_lines = [
            record.getMessage()
            for record in caplog.records[first_record:]
            if "control session dead" in record.getMessage()
        ]
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
        assert answered, f"restarted client stays deaf: {report}"
    finally:
        if b is not None:
            await _kill_uncleanly(b)
        await sim.stop()


async def test_clean_logout_restart_connects() -> None:
    """Control: after a clean logout B connects in the bound."""
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
    """Control: past the orphan expiry B connects in the bound."""
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
        # The orphan expiry is checked lazily at the next open
        # (``_seed_tracker_from_orphan``), so sleep outlasts it from the
        # death stamp, then prove the clean start through B's tracker.
        await asyncio.sleep(3.0)

        sim.keepalive_hold_s = 30.0
        b = _client(sim)
        with fast_connect():
            await b.connect()
        assert await _wait_answered(b, _RESTART_BOUND_S), (
            f"B not CONNECTED within {_RESTART_BOUND_S} s past the orphan expiry "
            f"(A frontier was {frontier})"
        )
        assert sim._civ_orphan is None, "orphan state survived past B's open"
        inherited = [t for t in sim._civ_gap.values() if t.inherited]
        assert inherited == [], "B inherited orphan state past its expiry"
        assert await asyncio.wait_for(b.get_freq(), 5.0) == 14_074_000
    finally:
        if b is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(b.disconnect(), 10.0)
        await sim.stop()


async def _restart_behind_the_frontier(
    sim: MockIcomRadio, *, record_seq: int | None
) -> IcomRadio:
    """Kill A uncleanly at the frontier, then connect a B that falls short of it.

    ``record_seq`` None leaves A's record absent, which holds only while the
    caller keeps ``WRITE_INTERVAL_S`` beyond the test (an unclean death writes
    nothing); otherwise that number is recorded for B to find. Returns B after
    its ``connect()``.
    """
    a = _client(sim)
    with fast_connect():
        await a.connect()
    assert a.conn_state is _CONNECTED and a.connected, "A never connected"
    await _run_until_frontier(a, _FRONTIER_TARGET)
    await _kill_uncleanly(a)
    assert await _wait_hold_released(sim), "held session never expired"
    if record_seq is not None:
        civ_seq.write_record(
            _HOST, sim.control_port, _SIM_GUID, record_seq, time.time()
        )
    sim.keepalive_hold_s = 30.0
    b = _client(sim)
    with fast_connect():
        await b.connect()
    return b


async def test_restart_without_a_record_reports_the_held_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No record: B numbers from 0, stays deaf, and says the radio holds a session.

    The line carries no wait: there is no record to estimate one from.
    """
    monkeypatch.setattr(civ_seq, "WRITE_INTERVAL_S", 3600.0)
    sim = _orphan_sim()
    await sim.start()
    b: IcomRadio | None = None
    try:
        b = await _restart_behind_the_frontier(sim, record_seq=None)
        identity = b.connection_identity
        assert identity is not None
        assert identity.status is RadioIdentityStatus.NO_RESPONSE
        assert b.last_error == "radio still holds the previous session; waiting"
    finally:
        if b is not None:
            await _kill_uncleanly(b)
        await sim.stop()


async def test_restart_short_of_the_frontier_reports_the_wait() -> None:
    """A record too low to pass the frontier: B stays deaf and states the wait."""
    sim = _orphan_sim()
    await sim.start()
    b: IcomRadio | None = None
    try:
        b = await _restart_behind_the_frontier(sim, record_seq=0)
        identity = b.connection_identity
        assert identity is not None
        assert identity.status is RadioIdentityStatus.NO_RESPONSE
        assert b.last_error == (
            f"radio still holds the previous session; waiting "
            f"~{_WAIT_AT_TEST_INTERVAL_S} s"
        )
    finally:
        if b is not None:
            await _kill_uncleanly(b)
        await sim.stop()
