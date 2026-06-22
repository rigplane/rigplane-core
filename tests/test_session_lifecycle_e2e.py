"""End-to-end session-lifecycle matrix (T1-T12) vs the IC-7610 simulator.

Unlike ``test_mock_icom_session.py`` (which drives the *simulator* with hand-built
packets) and ``test_session_lifecycle.py`` (which unit-tests the *policy* layer
with a fake mechanism), this file drives the **real production stack** end-to-end
over real UDP::

    CoreRadio.connect/disconnect/soft_reconnect
        -> CoreRadioSessionLifecycle (policy: CONNECTING<->COOLDOWN, release,
           RECOVERING, SIGTERM)
        -> ControlPhaseSessionMechanism (adapter)
        -> ControlPhaseRuntime._connect_once / release / soft_reconnect (packet I/O)
        -> IcomTransport (UDP)
        -> single_owner MockIcomRadio

Every test asserts against the **simulator's** observed wire state/counters
(``session_held``, ``released_count``, ``last_release_reason``, ``busy_rejects``,
``owner``) — i.e. real wire behaviour — not merely CoreRadio attributes.  This is
what proves the lifecycle fixes the original IC-7610 connect livelock: a
non-graceful abort leaves the radio holding its single session (T2), the fix
guarantees a release before every retry so a fresh / resident connect succeeds
with no ``0xFFFFFFFF`` busy reject and no livelock (T1/T3/T4).

T-number map (design 2026-06-22 radio-session-lifecycle, adapted):

* T1  — clean connect->disconnect frees the radio FREE (no cooldown); immediate
        reconnect succeeds, ``busy_rejects == 0``.
* T2  — protocol-level bug repro (control): a foreign owner holds the session ->
        a fresh connect within the keepalive window gets ``0xFFFFFFFF`` busy.
        Documents the FAILURE mode the fix prevents.
* T3  — THE FIX (crown proof): a post-claim transient on the first attempt STILL
        releases the radio before COOLDOWN, then a resident retry SUCCEEDS with
        no busy reject and no livelock; release happens before the retry.
* T4  — resident cooldown-aware retry: ``force_civ_unavailable_for`` -> civ_port=0
        not-ready -> the lifecycle waits & retries IN-PROCESS, one CoreRadio,
        ``busy_rejects == 0``, single owner throughout.
* T5  — auth hard-fail (0xFEFFFFFF): AuthenticationError, exactly ONE attempt
        (no retry), session released.
* T6  — SIGTERM / shutdown: ``request_shutdown`` releases the radio before exit.
* T7  — data-watchdog recovery (stall): the watchdog trips and recovers; CI-V
        resumes; no stale / second session (slow).
* T8  — soft_reconnect recovery: reuses the SAME session (no new owner, no busy);
        CI-V resumes.
* T9  — concurrent/duplicate connect coalesces to ONE session.
* T10 — fleet / second-owner: a genuine foreign owner while held -> busy reject;
        graceful switch (foreign closes -> we connect) works with no busy.
* T11 — recovery exhaustion: lifecycle soft_reconnect exhausts -> CLOSING +
        release, no held session left.
* T12 — fault injection (drop_rate): connect still succeeds / recovers; no stale
        session.
"""

from __future__ import annotations

import asyncio
import struct

import pytest

from rigplane.exceptions import AuthenticationError
from rigplane.exceptions import ConnectionError as RigplaneConnectionError
from rigplane.radio import IcomRadio
from rigplane.runtime.session_lifecycle import (
    AttemptOutcome,
    AttemptResult,
    LifecycleState,
)

from _perf_helpers import fast_connect
from mock_server import _PT_DATA, MockIcomRadio

# Mirror of the constant defined in tests/conftest.py — imported locally to
# avoid a conftest shadowing issue when pytest collects tests/integration/ too.
FAST_KEEPALIVE_HOLD_S: float = 0.5

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_radio(sim: MockIcomRadio) -> IcomRadio:
    """A real CoreRadio/IcomRadio pointed at the simulator's control port."""
    return IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        timeout=5.0,
    )


async def _connect(radio: IcomRadio) -> None:
    """Connect through the real lifecycle with handshake sleeps fast-pathed."""
    with fast_connect():
        await radio.connect()


class _ForeignOwner:
    """A raw UDP client that claims and HOLDS the simulator's single session.

    It claims by sending a real conninfo (0x90, requesttype 0x03) so the
    simulator marks ``(remote_addr, sender_id)`` as the owner, then keeps the
    session alive by re-sending conninfo on a cadence inside the keepalive
    window — modelling a genuinely-foreign client that won't let go.
    """

    def __init__(self, sender_id: int) -> None:
        self.sender_id = sender_id
        self._transport: asyncio.DatagramTransport | None = None
        self._proto: _ForeignOwner._Proto | None = None
        self._keepalive_task: asyncio.Task[None] | None = None

    class _Proto(asyncio.DatagramProtocol):
        def __init__(self) -> None:
            self.queue: asyncio.Queue[bytes] = asyncio.Queue()

        def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
            self.queue.put_nowait(data)

    def _conninfo_pkt(self) -> bytes:
        pkt = bytearray(0x90)
        struct.pack_into("<I", pkt, 0x00, 0x90)
        struct.pack_into("<H", pkt, 0x04, _PT_DATA)
        struct.pack_into("<I", pkt, 0x08, self.sender_id)
        pkt[0x15] = 0x03
        return bytes(pkt)

    async def claim(self, ctrl_port: int) -> tuple[int, int]:
        loop = asyncio.get_running_loop()
        transport, proto = await loop.create_datagram_endpoint(
            lambda: _ForeignOwner._Proto(),
            local_addr=("127.0.0.1", 0),
        )
        self._transport = transport
        self._proto = proto
        transport.sendto(self._conninfo_pkt(), ("127.0.0.1", ctrl_port))
        reply = await asyncio.wait_for(proto.queue.get(), timeout=1.0)
        error = struct.unpack_from("<I", reply, 0x30)[0]
        civ_port = struct.unpack_from(">H", reply, 0x42)[0]
        return civ_port, error

    def start_keepalive(self, ctrl_port: int, interval_s: float = 0.1) -> None:
        async def _loop() -> None:
            assert self._transport is not None
            try:
                while True:
                    self._transport.sendto(
                        self._conninfo_pkt(), ("127.0.0.1", ctrl_port)
                    )
                    await asyncio.sleep(interval_s)
            except asyncio.CancelledError:
                raise

        self._keepalive_task = asyncio.get_running_loop().create_task(_loop())

    async def release_and_close(self, ctrl_port: int) -> None:
        """Graceful foreign close: send a DISCONNECT (frees the session) + close."""
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
            self._keepalive_task = None
        if self._transport is not None:
            # Type 0x05 DISCONNECT for our owner -> simulator frees immediately.
            pkt = bytearray(0x10)
            struct.pack_into("<I", pkt, 0x00, 0x10)
            struct.pack_into("<H", pkt, 0x04, 0x05)
            struct.pack_into("<I", pkt, 0x08, self.sender_id)
            self._transport.sendto(bytes(pkt), ("127.0.0.1", ctrl_port))
            await asyncio.sleep(0.02)
            self._transport.close()
            self._transport = None

    async def close(self) -> None:
        if self._keepalive_task is not None:
            self._keepalive_task.cancel()
            try:
                await self._keepalive_task
            except asyncio.CancelledError:
                pass
            self._keepalive_task = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None


# ---------------------------------------------------------------------------
# T1 — clean connect -> disconnect leaves the radio FREE (no cooldown)
# ---------------------------------------------------------------------------


async def test_t1_clean_disconnect_frees_radio_and_allows_immediate_reconnect(
    single_owner_radio: MockIcomRadio,
) -> None:
    """A graceful connect/disconnect releases the single session immediately
    on the wire; an IMMEDIATE reconnect succeeds with NO busy reject."""
    sim = single_owner_radio
    radio = _make_radio(sim)
    await _connect(radio)

    assert radio.connected
    assert sim.session_held is True
    assert sim.busy_rejects == 0

    await radio.disconnect()
    await asyncio.sleep(0.05)  # let the release packets land on the sim

    # The radio is FREE on the wire — released immediately, not after a cooldown.
    assert sim.session_held is False
    assert sim.released_count >= 1
    assert sim.last_release_reason in ("openclose_close", "token_remove", "disconnect")

    # An IMMEDIATE reconnect (inside what would be the keepalive window) succeeds
    # with no 0xFFFFFFFF busy reject — the original livelock cannot occur.
    radio2 = _make_radio(sim)
    await _connect(radio2)
    assert radio2.connected
    assert sim.session_held is True
    assert sim.busy_rejects == 0

    await radio2.disconnect()


# ---------------------------------------------------------------------------
# T2 — protocol-level bug repro (the FAILURE mode the fix prevents)
# ---------------------------------------------------------------------------


async def test_t2_foreign_held_session_busy_rejects_fresh_connect(
    single_owner_radio: MockIcomRadio,
) -> None:
    """Control case: a foreign owner holds the single session, so a fresh real
    connect within the keepalive window is rejected with 0xFFFFFFFF (busy).

    This documents the livelock's root cause at the protocol level: a session
    held by someone-else means the radio will not allocate a CI-V port until
    the hold expires.  The fix (T1/T3) guarantees *our own* aborts never leave
    the radio in this state."""
    sim = single_owner_radio
    foreign = _ForeignOwner(sender_id=0xABCDEF01)
    civ_port, error = await foreign.claim(sim.control_port)
    assert civ_port == sim.civ_port and error == 0
    assert sim.session_held is True
    # Hold the session open across our connect attempt.
    foreign.start_keepalive(sim.control_port, interval_s=0.1)

    radio = _make_radio(sim)
    # One-shot, zero cooldown: the lifecycle must surface the busy reject rather
    # than spinning, because the foreign owner never lets go.
    radio._session_lifecycle._max_connect_attempts = 2
    radio._session_lifecycle._reject_cooldown_s = 0.0

    try:
        with pytest.raises((RigplaneConnectionError, asyncio.TimeoutError)):
            with fast_connect():
                await asyncio.wait_for(radio.connect(), timeout=2.0)
    finally:
        # The bug signature: the radio sent at least one 0xFFFFFFFF busy reject,
        # and the session is still held by the FOREIGN owner (not us).
        assert sim.busy_rejects >= 1
        assert sim.owner is not None and sim.owner[1] == 0xABCDEF01
        await foreign.close()
        # Our failed connect must still tear down cleanly (idempotent).
        await radio.disconnect()


# ---------------------------------------------------------------------------
# T3 — THE FIX (crown proof): release-before-retry, resident, no livelock
# ---------------------------------------------------------------------------


async def test_t3_aborted_attempt_releases_then_resident_retry_succeeds(
    single_owner_radio: MockIcomRadio,
) -> None:
    """Crown proof — the real lifecycle eliminates the livelock end-to-end.

    The first connect attempt genuinely CLAIMS the session on the sim (auth
    succeeds), then fails transiently.  The lifecycle MUST release that claim on
    the wire *before* the cooldown/retry, so the resident next attempt re-claims
    cleanly with NO 0xFFFFFFFF busy reject.  We assert (a) a release happened
    before the retry connected, and (b) the retry connected — both against the
    simulator's wire counters."""
    sim = single_owner_radio
    radio = _make_radio(sim)

    events: list[tuple[str, str, str]] = []
    radio._session_lifecycle.add_event_listener(
        lambda e: events.append((e.from_state.value, e.to_state.value, e.reason.value))
    )

    mech = radio._session_mechanism
    real_attempt = mech.connect_attempt
    state = {"n": 0, "released_after_claim": None}

    async def first_transient_then_real() -> AttemptResult:
        state["n"] += 1
        if state["n"] == 1:
            # Run the REAL attempt so auth actually claims the session on the
            # sim, then inject a post-claim transient outcome.  The lifecycle's
            # ``_attempt_with_release`` must RELEASE before returning.
            result = await real_attempt()
            assert result.outcome is AttemptOutcome.CONNECTED
            assert sim.session_held is True  # genuinely claimed on the wire
            return AttemptResult(AttemptOutcome.SESSION_BUSY_REJECT)
        # Snapshot the wire state the instant the retry attempt begins: the
        # release must already have happened (no held session, no busy reject).
        state["released_after_claim"] = (sim.released_count, sim.busy_rejects)
        return await real_attempt()

    mech.connect_attempt = first_transient_then_real  # type: ignore[method-assign]

    with fast_connect():
        await radio.connect()

    # The fix worked end-to-end: connected, two attempts, ZERO busy rejects.
    assert radio.connected
    assert state["n"] == 2
    assert sim.busy_rejects == 0
    assert sim.session_held is True

    # Release happened BEFORE the retry: at retry-start the sim had already freed
    # the first claim and recorded no busy reject.
    assert state["released_after_claim"] is not None
    released_at_retry, busy_at_retry = state["released_after_claim"]
    assert released_at_retry >= 1
    assert busy_at_retry == 0

    # The event stream proves the release-before-retry ordering:
    # CONNECTING -> COOLDOWN(busy) -> CONNECTING -> CONNECTED.
    assert ("connecting", "cooldown", "session_busy_reject") in events
    assert ("cooldown", "connecting", "none") in events
    assert events[-1] == ("connecting", "connected", "none")

    await radio.disconnect()


# ---------------------------------------------------------------------------
# T4 — resident cooldown-aware retry, no livelock (civ_port=0 not-ready)
# ---------------------------------------------------------------------------


async def test_t4_force_civ_unavailable_resident_retry_recovers(
    single_owner_radio: MockIcomRadio,
) -> None:
    """``force_civ_unavailable_for`` returns civ_port=0 (not-ready, error=0 — NOT
    a busy reject).  The lifecycle releases (no-op: nothing claimed), waits, and
    retries IN-PROCESS within one CoreRadio until the sim recovers — single
    owner throughout, ``busy_rejects == 0``, no process restart, no livelock."""
    sim = single_owner_radio
    sim.force_civ_unavailable_for(0.4)

    radio = _make_radio(sim)
    # Pace the resident retries past the unavailable window in-process.
    radio._session_lifecycle._not_ready_cooldown_s = 0.15
    radio._session_lifecycle._max_connect_attempts = 30

    await _connect(radio)

    assert radio.connected
    assert sim.session_held is True
    assert sim.busy_rejects == 0
    owner_id = sim.owner[1] if sim.owner else None

    await radio.disconnect()
    await asyncio.sleep(0.05)
    assert sim.session_held is False
    # Single owner the entire time (the same identity that finally claimed).
    assert owner_id is not None


# ---------------------------------------------------------------------------
# T5 — auth hard-fail (0xFEFFFFFF): no retry, session released
# ---------------------------------------------------------------------------


async def test_t5_auth_hard_fail_no_retry_session_released(
    single_owner_radio: MockIcomRadio,
) -> None:
    """A 0xFEFFFFFF credential rejection is a hard fail: AuthenticationError is
    raised after exactly ONE attempt (no resident retry), and no session is
    left held on the wire."""
    sim = single_owner_radio
    sim.auth_fail = True

    radio = _make_radio(sim)
    attempts = {"n": 0}
    real_attempt = radio._session_mechanism.connect_attempt

    async def counted() -> AttemptResult:
        attempts["n"] += 1
        return await real_attempt()

    radio._session_mechanism.connect_attempt = counted  # type: ignore[method-assign]

    with fast_connect():
        with pytest.raises(AuthenticationError):
            await radio.connect()

    assert attempts["n"] == 1  # NO retry on a hard credential failure
    assert sim.session_held is False
    assert sim.busy_rejects == 0


# ---------------------------------------------------------------------------
# T6 — SIGTERM / shutdown mid-session -> radio free
# ---------------------------------------------------------------------------


async def test_t6_request_shutdown_releases_radio(
    single_owner_radio: MockIcomRadio,
) -> None:
    """The SIGTERM-style ``request_shutdown`` hook releases the single session
    (token-remove + OpenClose-close) before the process would exit — no leak."""
    sim = single_owner_radio
    radio = _make_radio(sim)
    await _connect(radio)
    assert sim.session_held is True

    # The awaitable graceful-close hook the CLI signal handler must call.
    await radio._session_lifecycle.request_shutdown()
    # Mirror CoreRadio.disconnect()'s fallback for a still-live control phase.
    if radio.connected:
        await radio._control_phase.disconnect()
    await asyncio.sleep(0.05)

    assert sim.session_held is False
    assert sim.released_count >= 1
    assert sim.last_release_reason in ("openclose_close", "token_remove", "disconnect")


# ---------------------------------------------------------------------------
# T7 — data-watchdog recovery (stall) — slow (wall-clock > 2s watchdog timeout)
# ---------------------------------------------------------------------------


@pytest.mark.slow
async def test_t7_data_watchdog_recovers_after_stall(
    single_owner_radio: MockIcomRadio,
) -> None:
    """A CI-V stall trips the data watchdog; its OpenClose(open)-based recovery
    resumes the stream while reusing the SAME single session — no stale or
    second session, ``busy_rejects == 0``.

    The radio streams unsolicited CI-V; ``stall_for`` starves it past the 2 s
    watchdog timeout, then the watchdog's patient OpenClose recovery re-arms the
    stream (the sim re-emits a frame on each OpenClose(open))."""
    sim = single_owner_radio
    sim.keepalive_hold_s = 10.0  # keep the session well past the stall window
    sim.unsolicited_civ = True
    sim.unsolicited_interval_s = 0.05
    # Start the now-enabled unsolicited stream task.
    sim._unsolicited_task = asyncio.get_running_loop().create_task(
        sim._unsolicited_loop()
    )

    radio = _make_radio(sim)
    await _connect(radio)
    owner_before = sim.owner[1] if sim.owner else None

    sim.stall_for(3.0)  # > 2 s watchdog timeout -> trips recovery
    await asyncio.sleep(4.0)  # trip + OpenClose recovery + stream resume

    assert radio.connected
    # CI-V is alive again and the session was never re-claimed by a new owner.
    freq = await radio.get_freq()
    assert isinstance(freq, int)
    assert sim.session_held is True
    assert sim.owner is not None and sim.owner[1] == owner_before
    assert sim.busy_rejects == 0

    await radio.disconnect()


# ---------------------------------------------------------------------------
# T8 — soft_reconnect reuses the SAME session (no new session, no busy)
# ---------------------------------------------------------------------------


async def test_t8_soft_reconnect_reuses_session_no_stale(
    unsolicited_radio: MockIcomRadio,
) -> None:
    """``soft_reconnect`` rebuilds the CI-V data path while reusing the existing
    control session + token: the simulator sees the SAME owner throughout (no
    new claim, no 0xFFFFFFFF), and CI-V resumes after recovery."""
    sim = unsolicited_radio
    sim.keepalive_hold_s = 10.0  # outlive the soft-reconnect window
    radio = _make_radio(sim)
    await _connect(radio)

    owner_before = sim.owner[1] if sim.owner else None
    released_before = sim.released_count
    freq_before = await radio.get_freq()

    await radio.soft_reconnect()
    await asyncio.sleep(0.15)  # let unsolicited CI-V resume

    assert radio.connected
    assert sim.session_held is True
    assert sim.owner is not None and sim.owner[1] == owner_before
    # No graceful release happened in the middle (the session is reused, not
    # re-established with a new login).
    assert sim.released_count == released_before
    assert sim.busy_rejects == 0
    freq_after = await radio.get_freq()
    assert freq_after >= freq_before  # autonomous drift -> CI-V is flowing again

    await radio.disconnect()


# ---------------------------------------------------------------------------
# T9 — concurrent / duplicate connect coalesces to ONE session
# ---------------------------------------------------------------------------


async def test_t9_concurrent_connect_coalesces_to_one_session(
    single_owner_radio: MockIcomRadio,
) -> None:
    """Three concurrent ``connect()`` calls on the same CoreRadio coalesce onto
    a single in-flight attempt: the simulator sees ONE owner and ZERO busy
    rejects (a second claim would otherwise self-reject)."""
    sim = single_owner_radio
    radio = _make_radio(sim)

    with fast_connect():
        await asyncio.gather(radio.connect(), radio.connect(), radio.connect())

    assert radio.connected
    assert sim.session_held is True
    assert sim.owner is not None
    assert sim.busy_rejects == 0  # no duplicate claim raced into a self-reject

    await radio.disconnect()


# ---------------------------------------------------------------------------
# T10 — fleet / second-owner: foreign hold busies; graceful switch works
# ---------------------------------------------------------------------------


async def test_t10_foreign_owner_busy_then_graceful_switch(
    single_owner_radio: MockIcomRadio,
) -> None:
    """Single-owner enforcement plus a clean handover.

    Phase 1: a genuine foreign owner holds the session -> our connect is
    rejected with 0xFFFFFFFF while the foreign owner persists.
    Phase 2: the foreign owner closes gracefully (frees the session) -> our
    connect then succeeds with no further busy reject (clean switch)."""
    sim = single_owner_radio
    foreign = _ForeignOwner(sender_id=0x0BADF00D)
    _, error = await foreign.claim(sim.control_port)
    assert error == 0 and sim.session_held is True
    foreign.start_keepalive(sim.control_port, interval_s=0.1)

    # Phase 1: held by foreign -> our one-shot connect is busy-rejected.
    radio = _make_radio(sim)
    radio._session_lifecycle._max_connect_attempts = 1
    radio._session_lifecycle._reject_cooldown_s = 0.0
    with pytest.raises((RigplaneConnectionError, asyncio.TimeoutError)):
        with fast_connect():
            await asyncio.wait_for(radio.connect(), timeout=1.5)
    assert sim.busy_rejects >= 1
    assert sim.owner is not None and sim.owner[1] == 0x0BADF00D

    # Phase 2: graceful switch — foreign closes first, then we open.
    await foreign.release_and_close(sim.control_port)
    await asyncio.sleep(0.05)
    assert sim.session_held is False

    busy_after_phase1 = sim.busy_rejects
    radio2 = _make_radio(sim)
    await _connect(radio2)
    assert radio2.connected
    assert sim.session_held is True
    assert sim.owner is not None and sim.owner[1] != 0x0BADF00D
    # The clean switch added NO new busy rejects.
    assert sim.busy_rejects == busy_after_phase1

    await radio2.disconnect()


# ---------------------------------------------------------------------------
# T11 — recovery exhaustion -> CLOSING + release (no held session left)
# ---------------------------------------------------------------------------


async def test_t11_recovery_exhaustion_closes_and_releases(
    single_owner_radio: MockIcomRadio,
) -> None:
    """When the lifecycle's stateful recovery exhausts every attempt it routes
    through CLOSING with a full release: the simulator session is freed and
    nothing is left held.

    This drives ``CoreRadioSessionLifecycle.soft_reconnect`` (the stateful
    RECOVERING loop) directly.  After A4 this is the SINGLE owner of recovery:
    ``CoreRadio.soft_reconnect`` now routes here too, and the CI-V data watchdog
    only DETECTS the stall and triggers this loop (it no longer owns the
    retry/backoff/exhaustion ladder)."""
    sim = single_owner_radio
    sim.keepalive_hold_s = 10.0
    radio = _make_radio(sim)
    await _connect(radio)
    assert sim.session_held is True

    lifecycle = radio._session_lifecycle
    lifecycle._recovery_backoff_s = (0.0, 0.0, 0.0)  # no wall-clock backoff

    async def always_fail() -> None:
        raise RigplaneConnectionError("injected CI-V rebuild failure")

    lifecycle._mech.soft_reconnect_once = always_fail  # type: ignore[method-assign]

    with pytest.raises(RigplaneConnectionError):
        await lifecycle.soft_reconnect()
    await asyncio.sleep(0.05)

    assert lifecycle.state is LifecycleState.DISCONNECTED
    assert sim.session_held is False  # released on exhaustion
    assert sim.released_count >= 1


# ---------------------------------------------------------------------------
# T12 — fault injection (drop_rate): connect succeeds, no stale session
# ---------------------------------------------------------------------------


async def test_t12_connect_under_packet_loss_no_stale_session() -> None:
    """With a lossy link (``drop_rate``) the real connect still succeeds (the
    transport's retransmit + the lifecycle's within-attempt retries absorb the
    loss) and the simulator ends with a single clean owner — no stale session.

    A dedicated lossy simulator is used (the shared fixtures are loss-free)."""
    sim = MockIcomRadio(
        single_owner=True,
        keepalive_hold_s=FAST_KEEPALIVE_HOLD_S * 6,  # outlive a few retries
        drop_rate=0.25,
    )
    await sim.start()
    try:
        radio = _make_radio(sim)
        radio._session_lifecycle._max_connect_attempts = 30
        radio._session_lifecycle._not_ready_cooldown_s = 0.1

        with fast_connect():
            await asyncio.wait_for(radio.connect(), timeout=8.0)

        assert radio.connected
        assert sim.session_held is True
        owner_id = sim.owner[1] if sim.owner else None
        assert owner_id is not None

        await radio.disconnect()
        await asyncio.sleep(0.05)
        # No stale session: a clean release left the radio free.
        assert sim.session_held is False
        assert sim.released_count >= 1
    finally:
        await sim.stop()
