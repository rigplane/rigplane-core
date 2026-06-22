"""Unit tests for the RadioSessionLifecycle state machine (task A2).

These tests drive the state machine through a *fake mechanism* — the policy
layer (``RadioSessionLifecycle``) is decoupled from the packet mechanism
(``ControlPhaseRuntime``) via the ``SessionMechanism`` protocol, so the state
machine can be exercised deterministically without real sockets.

They prove the design's governing invariants:

* every connect-failure path RELEASES the claimed session (token-remove +
  close) — graceful-close Holes 1, 2, 5, 8;
* the cooldown path RELEASES first, THEN waits (no held session during the
  wait) — the root-cause fix (Hole 4 / Cause A);
* a ``0xFEFFFFFF`` auth-credentials failure HARD-FAILS (no resident retry,
  D3);
* state transitions + events are emitted with correct reasons / countdown
  (D1 rich feedback);
* ``__aexit__`` and ``disconnect()`` ALWAYS release;
* SIGTERM-style ``request_shutdown()`` releases before returning (Hole 3).

The fake mechanism follows the queued-response spirit of
``tests/test_radio.py:MockTransport`` — outcomes are queued and consumed
in order.
"""

from __future__ import annotations

import asyncio

import pytest

from rigplane.core.exceptions import AuthenticationError, ConnectionError
from rigplane.runtime.session_lifecycle import (
    AttemptOutcome,
    AttemptResult,
    CoreRadioSessionLifecycle,
    LifecycleErrorReason,
    LifecycleEvent,
    LifecycleState,
    RadioPresence,
)

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fake mechanism — queued per-attempt outcomes; records release calls
# ---------------------------------------------------------------------------


class FakeMechanism:
    """A queued-outcome stand-in for ``ControlPhaseRuntime`` (the mechanism).

    Each ``connect_attempt()`` pops one queued :class:`AttemptResult`.  The
    mechanism records whether a release obligation is currently outstanding
    (``claimed``) and counts ``release()`` / ``soft_reconnect_once()`` calls so
    tests can assert release-on-every-exit.
    """

    def __init__(self) -> None:
        self._outcomes: list[AttemptResult] = []
        self.connect_calls = 0
        self.release_calls = 0
        self.soft_reconnect_calls = 0
        self.scan_calls = 0
        # True between a successful/partial claim and its release.
        self.claimed = False
        # Configurable scan result and soft-reconnect behaviour.
        self._scan_result: list[RadioPresence] = []
        self._soft_reconnect_failures = 0
        # Records the order of mechanism operations for ordering assertions.
        self.ops: list[str] = []

    def queue_outcome(self, result: AttemptResult) -> None:
        self._outcomes.append(result)

    def queue_ok(self) -> None:
        self.queue_outcome(AttemptResult(AttemptOutcome.CONNECTED))

    def queue_not_ready(self) -> None:
        self.queue_outcome(AttemptResult(AttemptOutcome.SESSION_NOT_READY))

    def queue_busy_reject(self) -> None:
        self.queue_outcome(AttemptResult(AttemptOutcome.SESSION_BUSY_REJECT))

    def queue_auth_fail(self) -> None:
        self.queue_outcome(AttemptResult(AttemptOutcome.AUTH_CREDENTIALS))

    def queue_exception(self, exc: BaseException) -> None:
        self.queue_outcome(AttemptResult(AttemptOutcome.CONNECTED, raises=exc))

    def set_scan_result(self, presences: list[RadioPresence]) -> None:
        self._scan_result = presences

    def set_soft_reconnect_failures(self, n: int) -> None:
        self._soft_reconnect_failures = n

    # -- mechanism protocol ------------------------------------------------

    async def connect_attempt(self) -> AttemptResult:
        self.connect_calls += 1
        self.ops.append("connect_attempt")
        if not self._outcomes:
            raise AssertionError("FakeMechanism ran out of queued outcomes")
        result = self._outcomes.pop(0)
        # A claim happens on auth success, which for the fake is any outcome
        # except a pre-auth-style AUTH_CREDENTIALS reject.  CONNECTED/
        # NOT_READY/BUSY_REJECT all imply auth succeeded → session claimed.
        if result.outcome is not AttemptOutcome.AUTH_CREDENTIALS:
            self.claimed = True
        if result.raises is not None:
            raise result.raises
        return result

    async def release(self) -> None:
        self.release_calls += 1
        self.ops.append("release")
        self.claimed = False

    async def soft_reconnect_once(self) -> None:
        self.soft_reconnect_calls += 1
        self.ops.append("soft_reconnect_once")
        if self._soft_reconnect_failures > 0:
            self._soft_reconnect_failures -= 1
            raise ConnectionError("soft reconnect failed (fake)")

    async def scan(
        self, targets: list[str] | None, *, timeout: float
    ) -> list[RadioPresence]:
        self.scan_calls += 1
        self.ops.append("scan")
        return list(self._scan_result)


def make_lifecycle(
    mech: FakeMechanism | None = None,
    **kwargs: object,
) -> tuple[CoreRadioSessionLifecycle, FakeMechanism, list[LifecycleEvent]]:
    """Build a lifecycle with a fast (near-zero) cooldown for deterministic tests."""
    mech = mech or FakeMechanism()
    lc = CoreRadioSessionLifecycle(
        mechanism=mech,
        not_ready_cooldown_s=0.0,
        reject_cooldown_s=0.0,
        max_recovery_attempts=3,
        recovery_backoff_s=(0.0, 0.0, 0.0),
        **kwargs,  # type: ignore[arg-type]
    )
    events: list[LifecycleEvent] = []
    lc.add_event_listener(events.append)
    return lc, mech, events


# ---------------------------------------------------------------------------
# Initial state / observable surface
# ---------------------------------------------------------------------------


async def test_initial_state_is_disconnected() -> None:
    lc, _mech, _events = make_lifecycle()
    assert lc.state is LifecycleState.DISCONNECTED
    assert lc.status.state is LifecycleState.DISCONNECTED
    assert lc.status.last_error is LifecycleErrorReason.NONE


# ---------------------------------------------------------------------------
# T1 — clean connect → disconnect, NO cooldown; release on disconnect
# ---------------------------------------------------------------------------


async def test_clean_connect_then_disconnect_releases_no_cooldown() -> None:
    lc, mech, events = make_lifecycle()
    mech.queue_ok()

    await lc.connect()
    assert lc.state is LifecycleState.CONNECTED
    assert mech.connect_calls == 1
    # No cooldown should have been entered on a clean connect.
    assert not any(e.to_state is LifecycleState.COOLDOWN for e in events)

    await lc.disconnect()
    assert lc.state is LifecycleState.DISCONNECTED
    # Release (token-remove + close) happened on disconnect.
    assert mech.release_calls >= 1
    assert not mech.claimed
    # Transition events present.
    states = [(e.from_state, e.to_state) for e in events]
    assert (LifecycleState.DISCONNECTED, LifecycleState.CONNECTING) in states
    assert (LifecycleState.CONNECTING, LifecycleState.CONNECTED) in states
    assert any(e.to_state is LifecycleState.CLOSING for e in events)
    assert events[-1].to_state is LifecycleState.DISCONNECTED


# ---------------------------------------------------------------------------
# Hole 1 / 5 / 8 — post-auth/pre-CONNECTED exception still releases
# ---------------------------------------------------------------------------


async def test_exception_after_claim_releases_session() -> None:
    lc, mech, _events = make_lifecycle()
    boom = RuntimeError("post-auth explosion")
    mech.queue_exception(boom)

    with pytest.raises(RuntimeError, match="post-auth explosion"):
        await lc.connect()

    # The claim was registered (auth side-effect of the fake) and MUST be
    # released even though the attempt raised mid-way (Hole 1).
    assert mech.release_calls >= 1
    assert not mech.claimed
    assert lc.state is LifecycleState.DISCONNECTED


# ---------------------------------------------------------------------------
# Hole 2 — __aenter__ raises before __aexit__ still releases
# ---------------------------------------------------------------------------


async def test_aenter_failure_releases_via_context_manager() -> None:
    lc, mech, _events = make_lifecycle()
    mech.queue_exception(RuntimeError("connect blew up in aenter"))

    with pytest.raises(RuntimeError):
        async with lc:
            pytest.fail("body must not run when __aenter__ raises")  # pragma: no cover

    assert mech.release_calls >= 1
    assert not mech.claimed
    assert lc.state is LifecycleState.DISCONNECTED


async def test_aexit_always_releases_on_clean_path() -> None:
    lc, mech, _events = make_lifecycle()
    mech.queue_ok()

    async with lc as entered:
        assert entered is lc
        assert lc.state is LifecycleState.CONNECTED
    assert lc.state is LifecycleState.DISCONNECTED
    assert mech.release_calls >= 1
    assert not mech.claimed


async def test_aexit_releases_even_when_body_raises() -> None:
    lc, mech, _events = make_lifecycle()
    mech.queue_ok()

    with pytest.raises(ValueError, match="body error"):
        async with lc:
            raise ValueError("body error")
    assert lc.state is LifecycleState.DISCONNECTED
    assert mech.release_calls >= 1
    assert not mech.claimed


# ---------------------------------------------------------------------------
# Hole 4 / Cause A — cooldown RELEASES first, THEN waits, THEN retries
# ---------------------------------------------------------------------------


async def test_not_ready_releases_before_cooldown_then_connects() -> None:
    lc, mech, events = make_lifecycle()
    mech.queue_not_ready()  # first attempt: civ_port == 0
    mech.queue_ok()  # retry succeeds

    await lc.connect()
    assert lc.state is LifecycleState.CONNECTED
    assert mech.connect_calls == 2

    # The CRITICAL invariant: release happened BEFORE the cooldown wait, i.e.
    # the session was NOT held during the cooldown.  In the op log, the first
    # release must precede the second connect_attempt.
    first_release = mech.ops.index("release")
    second_connect = [i for i, op in enumerate(mech.ops) if op == "connect_attempt"][1]
    assert first_release < second_connect, mech.ops
    assert not mech.claimed or lc.state is LifecycleState.CONNECTED

    # A COOLDOWN transition was emitted with the not-ready reason + countdown.
    cooldown_events = [e for e in events if e.to_state is LifecycleState.COOLDOWN]
    assert cooldown_events
    assert cooldown_events[0].reason is LifecycleErrorReason.SESSION_NOT_READY
    assert cooldown_events[0].cooldown_remaining_s is not None


async def test_busy_reject_releases_before_cooldown_then_connects() -> None:
    lc, mech, events = make_lifecycle()
    mech.queue_busy_reject()  # first attempt: 0xFFFFFFFF
    mech.queue_ok()

    await lc.connect()
    assert lc.state is LifecycleState.CONNECTED
    assert mech.connect_calls == 2
    cooldown_events = [e for e in events if e.to_state is LifecycleState.COOLDOWN]
    assert cooldown_events
    assert cooldown_events[0].reason is LifecycleErrorReason.SESSION_BUSY_REJECT
    # release-before-wait ordering
    first_release = mech.ops.index("release")
    second_connect = [i for i, op in enumerate(mech.ops) if op == "connect_attempt"][1]
    assert first_release < second_connect, mech.ops


# ---------------------------------------------------------------------------
# D3 — hard-fail on 0xFEFFFFFF (auth credentials); NO resident retry
# ---------------------------------------------------------------------------


async def test_auth_credentials_hard_fails_no_retry() -> None:
    lc, mech, events = make_lifecycle()
    mech.queue_auth_fail()
    # Even if a follow-up OK were queued, it must NOT be consumed.
    mech.queue_ok()

    with pytest.raises(AuthenticationError):
        await lc.connect()

    assert mech.connect_calls == 1  # no retry
    assert lc.state is LifecycleState.DISCONNECTED
    assert lc.status.last_error is LifecycleErrorReason.AUTH_CREDENTIALS
    # Even a pre-claim auth failure must not leave a held session.
    assert not mech.claimed
    closing = [e for e in events if e.to_state is LifecycleState.CLOSING]
    assert closing
    assert closing[-1].reason is LifecycleErrorReason.AUTH_CREDENTIALS


# ---------------------------------------------------------------------------
# disconnect() idempotency
# ---------------------------------------------------------------------------


async def test_disconnect_on_idle_is_noop() -> None:
    lc, mech, _events = make_lifecycle()
    await lc.disconnect()  # never connected
    assert lc.state is LifecycleState.DISCONNECTED
    assert mech.release_calls == 0  # nothing claimed → nothing to release


async def test_double_disconnect_is_idempotent() -> None:
    lc, mech, _events = make_lifecycle()
    mech.queue_ok()
    await lc.connect()
    await lc.disconnect()
    releases_after_first = mech.release_calls
    await lc.disconnect()
    assert lc.state is LifecycleState.DISCONNECTED
    # Second disconnect releases nothing new.
    assert mech.release_calls == releases_after_first


# ---------------------------------------------------------------------------
# T9 — concurrent / duplicate connect coalesced
# ---------------------------------------------------------------------------


async def test_concurrent_connect_coalesced_single_session() -> None:
    lc, mech, _events = make_lifecycle()

    started = asyncio.Event()

    class SlowMech(FakeMechanism):
        async def connect_attempt(self) -> AttemptResult:
            started.set()
            await asyncio.sleep(0.02)
            return await super().connect_attempt()

    slow = SlowMech()
    slow.queue_ok()
    lc2, _m, _e = make_lifecycle(mech=slow)

    t1 = asyncio.create_task(lc2.connect())
    await started.wait()
    t2 = asyncio.create_task(lc2.connect())
    await asyncio.gather(t1, t2)

    assert lc2.state is LifecycleState.CONNECTED
    # Exactly one attempt — the second connect coalesced onto the first.
    assert slow.connect_calls == 1
    await lc2.disconnect()


# ---------------------------------------------------------------------------
# disconnect() during cooldown cancels the resident runner → CLOSING + release
# ---------------------------------------------------------------------------


async def test_disconnect_during_cooldown_cancels_and_releases() -> None:
    mech = FakeMechanism()
    mech.queue_not_ready()
    # Use a long cooldown so we can interrupt it mid-wait.
    lc = CoreRadioSessionLifecycle(
        mechanism=mech,
        not_ready_cooldown_s=10.0,
        reject_cooldown_s=10.0,
    )
    events: list[LifecycleEvent] = []
    lc.add_event_listener(events.append)

    connect_task = asyncio.create_task(lc.connect())
    # Wait until we are in COOLDOWN (release already happened).
    for _ in range(200):
        if lc.state is LifecycleState.COOLDOWN:
            break
        await asyncio.sleep(0.005)
    assert lc.state is LifecycleState.COOLDOWN
    assert mech.release_calls >= 1  # released BEFORE the wait

    await lc.disconnect()
    with pytest.raises(asyncio.CancelledError):
        await connect_task
    assert lc.state is LifecycleState.DISCONNECTED
    assert not mech.claimed


# ---------------------------------------------------------------------------
# SIGTERM-style graceful shutdown (Hole 3) — releases before returning
# ---------------------------------------------------------------------------


async def test_request_shutdown_releases_session() -> None:
    lc, mech, events = make_lifecycle()
    mech.queue_ok()
    await lc.connect()
    assert lc.state is LifecycleState.CONNECTED

    await lc.request_shutdown()
    assert lc.state is LifecycleState.DISCONNECTED
    assert mech.release_calls >= 1
    assert not mech.claimed


async def test_request_shutdown_during_cooldown_releases() -> None:
    mech = FakeMechanism()
    mech.queue_not_ready()
    lc = CoreRadioSessionLifecycle(
        mechanism=mech,
        not_ready_cooldown_s=10.0,
        reject_cooldown_s=10.0,
    )
    connect_task = asyncio.create_task(lc.connect())
    for _ in range(200):
        if lc.state is LifecycleState.COOLDOWN:
            break
        await asyncio.sleep(0.005)
    assert lc.state is LifecycleState.COOLDOWN

    await lc.request_shutdown()
    with pytest.raises(asyncio.CancelledError):
        await connect_task
    assert lc.state is LifecycleState.DISCONNECTED
    assert not mech.claimed


# ---------------------------------------------------------------------------
# T6 — scan() opens NO session, does not disturb ownership
# ---------------------------------------------------------------------------


async def test_scan_opens_no_session() -> None:
    lc, mech, events = make_lifecycle()
    mech.set_scan_result([RadioPresence(host="192.168.1.10", remote_id=0xDEADBEEF)])
    result = await lc.scan()
    assert result == [RadioPresence(host="192.168.1.10", remote_id=0xDEADBEEF)]
    # scan must never claim a session or release one.
    assert not mech.claimed
    assert mech.release_calls == 0
    assert "connect_attempt" not in mech.ops
    assert lc.state is LifecycleState.DISCONNECTED
    # SCANNING transitions emitted around the probe.
    assert any(e.to_state is LifecycleState.SCANNING for e in events)


async def test_scan_while_connected_does_not_disturb_session() -> None:
    lc, mech, _events = make_lifecycle()
    mech.queue_ok()
    await lc.connect()
    mech.set_scan_result([])
    await lc.scan()
    # Still connected; no release happened from scan.
    assert lc.state is LifecycleState.CONNECTED
    assert mech.release_calls == 0
    await lc.disconnect()


# ---------------------------------------------------------------------------
# T7 / T8 — soft_reconnect: RECOVERING → CONNECTED, control+token reused
# ---------------------------------------------------------------------------


async def test_soft_reconnect_recovers_to_connected() -> None:
    lc, mech, events = make_lifecycle()
    mech.queue_ok()
    await lc.connect()

    await lc.soft_reconnect()
    assert lc.state is LifecycleState.CONNECTED
    assert mech.soft_reconnect_calls == 1
    # No new login (no second connect_attempt) — control + token reused.
    assert mech.connect_calls == 1
    recovering = [e for e in events if e.to_state is LifecycleState.RECOVERING]
    assert recovering
    assert recovering[0].recovery_attempt == 1
    assert recovering[0].recovery_max == 3
    await lc.disconnect()


async def test_soft_reconnect_retries_then_recovers() -> None:
    lc, mech, events = make_lifecycle()
    mech.queue_ok()
    await lc.connect()
    mech.set_soft_reconnect_failures(2)  # fail twice, succeed on 3rd

    await lc.soft_reconnect()
    assert lc.state is LifecycleState.CONNECTED
    assert mech.soft_reconnect_calls == 3
    attempts = [
        e.recovery_attempt
        for e in events
        if e.to_state is LifecycleState.RECOVERING and e.recovery_attempt is not None
    ]
    assert attempts == [1, 2, 3]
    await lc.disconnect()


# ---------------------------------------------------------------------------
# T11 — recovery exhaustion → CLOSING + full release
# ---------------------------------------------------------------------------


async def test_recovery_exhaustion_closes_and_releases() -> None:
    lc, mech, events = make_lifecycle()
    mech.queue_ok()
    await lc.connect()
    mech.set_soft_reconnect_failures(99)  # never recovers

    with pytest.raises(ConnectionError):
        await lc.soft_reconnect()

    assert mech.soft_reconnect_calls == 3  # max_recovery_attempts
    assert lc.state is LifecycleState.DISCONNECTED
    assert mech.release_calls >= 1  # full release through CLOSING
    assert not mech.claimed
    closing = [e for e in events if e.to_state is LifecycleState.CLOSING]
    assert closing
    assert closing[-1].reason is LifecycleErrorReason.RECOVERY_EXHAUSTED


# ---------------------------------------------------------------------------
# D1 — event listener add/remove + exceptions swallowed
# ---------------------------------------------------------------------------


async def test_listener_add_remove_idempotent() -> None:
    lc, mech, _events = make_lifecycle()
    seen: list[LifecycleEvent] = []
    cb = seen.append
    lc.add_event_listener(cb)
    lc.add_event_listener(cb)  # duplicate ignored
    mech.queue_ok()
    await lc.connect()
    before = len(seen)
    assert before > 0
    lc.remove_event_listener(cb)
    lc.remove_event_listener(cb)  # double-remove no-op
    await lc.disconnect()
    assert len(seen) == before  # no events after removal


async def test_listener_exception_is_swallowed() -> None:
    lc, mech, _events = make_lifecycle()

    def boom(_e: LifecycleEvent) -> None:
        raise RuntimeError("listener blew up")

    lc.add_event_listener(boom)
    mech.queue_ok()
    # Must not propagate the listener error.
    await lc.connect()
    assert lc.state is LifecycleState.CONNECTED
    await lc.disconnect()


async def test_status_snapshot_in_cooldown_has_countdown() -> None:
    mech = FakeMechanism()
    mech.queue_not_ready()
    lc = CoreRadioSessionLifecycle(
        mechanism=mech,
        not_ready_cooldown_s=10.0,
        reject_cooldown_s=10.0,
    )
    connect_task = asyncio.create_task(lc.connect())
    for _ in range(200):
        if lc.state is LifecycleState.COOLDOWN:
            break
        await asyncio.sleep(0.005)
    snap = lc.status
    assert snap.state is LifecycleState.COOLDOWN
    assert snap.cooldown_total_s == 10.0
    assert snap.cooldown_remaining_s is not None
    assert 0.0 <= snap.cooldown_remaining_s <= 10.0
    assert snap.last_error is LifecycleErrorReason.SESSION_NOT_READY
    await lc.disconnect()
    with pytest.raises(asyncio.CancelledError):
        await connect_task


# ---------------------------------------------------------------------------
# A3 — tests moved from tests/test_radio_connect.py, re-expressed against the
# lifecycle (the legacy ControlPhaseRuntime.connect() retry wrapper they used to
# exercise was deleted in A3; the cooldown-retry + release policy now lives
# here).
# ---------------------------------------------------------------------------


async def test_data_port_not_ready_retries_in_process_then_connects() -> None:
    """Moved from ``test_data_port_discovery_timeout_retries_*``.

    A CI-V data-port discovery timeout surfaces as SESSION_NOT_READY; the
    resident runner RELEASES the partial attempt, waits out the cooldown, then
    retries CONNECTING in-process and connects on the second attempt — exactly
    what the deleted ``connect()`` wrapper's ``_DATA_PORT_COOLDOWN_RETRIES``
    loop did, but now release-before-cooldown.
    """
    lc, mech, events = make_lifecycle(max_connect_attempts=4)
    mech.queue_not_ready()  # attempt 1: data-port discovery timeout
    mech.queue_ok()  # attempt 2: succeeds

    await lc.connect()
    assert lc.state is LifecycleState.CONNECTED
    assert mech.connect_calls == 2
    # release ran before the second attempt (release-before-cooldown).
    first_release = mech.ops.index("release")
    second_connect = [i for i, op in enumerate(mech.ops) if op == "connect_attempt"][1]
    assert first_release < second_connect, mech.ops
    not_ready = [
        e for e in events if e.reason is LifecycleErrorReason.SESSION_NOT_READY
    ]
    assert not_ready
    await lc.disconnect()


async def test_persistent_busy_reject_raises_after_resident_attempts() -> None:
    """Moved from ``test_connect_raises_on_status_rejection_after_retries``.

    A radio that persistently busy-rejects (0xFFFFFFFF) is retried in-process up
    to ``max_connect_attempts`` (each attempt RELEASED first), then surfaces a
    ConnectionError rather than spinning forever — and never holds a session.
    """
    lc, mech, _events = make_lifecycle(max_connect_attempts=3)
    for _ in range(3):
        mech.queue_busy_reject()

    with pytest.raises(ConnectionError, match="never became ready"):
        await lc.connect()

    assert mech.connect_calls == 3  # bounded resident retry
    # Every attempt released its partial claim (release-before-cooldown).
    assert mech.release_calls >= 3
    assert not mech.claimed
    assert lc.state is LifecycleState.DISCONNECTED


# ---------------------------------------------------------------------------
# A2-verifier-recommended regressions (A3)
# ---------------------------------------------------------------------------


async def test_cancel_after_claim_still_releases() -> None:
    """A cancel arriving AFTER the session is claimed still token-removes.

    Models the SIGTERM/disconnect-mid-CONNECTING race: ``connect_attempt`` is
    cancelled after auth succeeded (session claimed).  ``_attempt_with_release``
    MUST release the (now-claimed) session before the CancelledError propagates,
    so the token-remove always goes out (Note 2 — release must not be truncated
    by the cancel).
    """

    class CancelAfterClaimMech(FakeMechanism):
        async def connect_attempt(self) -> AttemptResult:
            # Claim the session (auth success side-effect), THEN get cancelled
            # before returning an outcome.
            self.connect_calls += 1
            self.ops.append("connect_attempt")
            self.claimed = True
            raise asyncio.CancelledError()

    mech = CancelAfterClaimMech()
    lc = CoreRadioSessionLifecycle(mechanism=mech)

    with pytest.raises(asyncio.CancelledError):
        await lc.connect()

    # The claimed session was released (token-remove sent) despite the cancel.
    assert mech.release_calls >= 1
    assert not mech.claimed
    # The op log proves release ran after the claim.
    assert "release" in mech.ops
    assert mech.ops.index("connect_attempt") < mech.ops.index("release")


async def test_release_raising_does_not_crash_disconnect() -> None:
    """A mechanism ``release()`` that raises must not crash ``disconnect()``.

    The lifecycle swallows release errors in teardown and still ends in
    DISCONNECTED, so a flaky/raising release never propagates out of
    disconnect() (defensive graceful-close).
    """

    class ReleaseBoomMech(FakeMechanism):
        async def release(self) -> None:
            self.release_calls += 1
            self.ops.append("release")
            self.claimed = False
            raise RuntimeError("release blew up")

    mech = ReleaseBoomMech()
    mech.queue_ok()
    lc = CoreRadioSessionLifecycle(mechanism=mech)

    await lc.connect()
    assert lc.state is LifecycleState.CONNECTED

    # disconnect() must NOT raise even though release() does.
    await lc.disconnect()
    assert lc.state is LifecycleState.DISCONNECTED
    assert mech.release_calls >= 1
