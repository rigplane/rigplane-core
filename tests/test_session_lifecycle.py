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

from rigplane.core.exceptions import (
    AuthenticationError,
    ConnectionError,
    DiscoveryTimeout,
    TimeoutError,
)
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

    def queue_discovery_timeout(self) -> None:
        """Pre-login discovery timeout, as production raises it.

        An unanswered control-channel discovery surfaces as a
        ``DiscoveryTimeout`` from the transport: auth never ran, so the
        attempt sent no login and claimed nothing.  The fake mirrors
        that by raising instead of returning an outcome.
        """
        self.queue_outcome(
            AttemptResult(
                AttemptOutcome.CONNECTED,
                raises=DiscoveryTimeout(
                    "Radio did not respond to discovery after 10 attempts"
                ),
            )
        )

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
        # A pre-auth timeout (discovery, login-response wait) claims
        # nothing: auth never ran, so the attempt holds no session.
        is_pre_auth_timeout = isinstance(result.raises, TimeoutError)
        if (
            result.outcome is not AttemptOutcome.AUTH_CREDENTIALS
            and not is_pre_auth_timeout
        ):
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


async def test_first_connect_busy_passes_do_not_consume_the_attempt_budget() -> None:
    """MOR-3391: first-connect busy re-logins stay out of the retry budget.

    ``CoreRadio`` constructs the lifecycle with ``max_connect_attempts=4``;
    busy passes consumed by the start-up hold must not leave a following
    not-ready with zero retries (main gave three). The busy passes retry
    under the start-up busy budget instead.
    """
    lc, mech, _events = make_lifecycle(
        max_connect_attempts=4,
        first_connect_busy_cooldown_s=0.0,
        first_connect_busy_budget_s=600.0,
    )
    for _ in range(3):
        mech.queue_busy_reject()
    mech.queue_not_ready()
    mech.queue_ok()

    await lc.connect()

    assert lc.state is LifecycleState.CONNECTED
    # 3 busy re-logins + 1 not-ready + the connecting attempt.
    assert mech.connect_calls == 5


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


# ---------------------------------------------------------------------------
# MOR-3355 — a connect attempt failing with a connection error leaves the
# lifecycle DISCONNECTED (not stuck at CONNECTING)
# ---------------------------------------------------------------------------


async def test_connection_error_attempt_drives_machine_to_disconnected() -> None:
    """MOR-3355: RigplaneConnectionError from the attempt ends CLOSING→DISCONNECTED.

    Models the startup-readiness abort inside ``ControlPhaseRuntime._connect_once``
    (or any other ConnectionError from the attempt): ``_attempt_with_release``
    already released the claim, so ``_run_connect`` must drive the observable
    machine home before re-raising instead of leaving it at CONNECTING.
    """
    lc, mech, events = make_lifecycle()
    mech.queue_exception(ConnectionError("startup-readiness abort (fake)"))

    with pytest.raises(ConnectionError, match="startup-readiness abort"):
        await lc.connect()

    assert mech.release_calls >= 1
    assert not mech.claimed
    assert lc.state is LifecycleState.DISCONNECTED
    assert [e.to_state for e in events] == [
        LifecycleState.CONNECTING,
        LifecycleState.CLOSING,
        LifecycleState.DISCONNECTED,
    ]
    assert events[1].reason is LifecycleErrorReason.CANCELLED


async def test_auth_hard_fail_emits_disconnected_exactly_once() -> None:
    """MOR-3355: the auth hard-fail path still emits DISCONNECTED exactly once."""
    lc, mech, events = make_lifecycle()
    mech.queue_auth_fail()

    with pytest.raises(AuthenticationError):
        await lc.connect()

    assert lc.state is LifecycleState.DISCONNECTED
    assert sum(1 for e in events if e.to_state is LifecycleState.CLOSING) == 1
    assert sum(1 for e in events if e.to_state is LifecycleState.DISCONNECTED) == 1


async def test_exhaustion_emits_disconnected_exactly_once() -> None:
    """MOR-3355: the attempt-exhaustion path still emits DISCONNECTED exactly once."""
    lc, mech, events = make_lifecycle(max_connect_attempts=3)
    for _ in range(3):
        mech.queue_busy_reject()

    with pytest.raises(ConnectionError, match="never became ready"):
        await lc.connect()

    assert lc.state is LifecycleState.DISCONNECTED
    assert sum(1 for e in events if e.to_state is LifecycleState.DISCONNECTED) == 1


async def test_cancel_during_attempt_reraises_without_extra_emits() -> None:
    """MOR-3355: CancelledError still propagates with no CLOSING/DISCONNECTED emits."""

    class CancelMech(FakeMechanism):
        async def connect_attempt(self) -> AttemptResult:
            self.connect_calls += 1
            self.ops.append("connect_attempt")
            self.claimed = True
            raise asyncio.CancelledError()

    mech = CancelMech()
    lc, _m, events = make_lifecycle(mech=mech)

    with pytest.raises(asyncio.CancelledError):
        await lc.connect()

    assert mech.release_calls >= 1
    assert not mech.claimed
    assert events
    assert events[-1].to_state is LifecycleState.CONNECTING
    assert all(
        e.to_state is not LifecycleState.CLOSING
        and e.to_state is not LifecycleState.DISCONNECTED
        for e in events
    )


# ---------------------------------------------------------------------------
# MOR-3391 part 1b — stop the start-up busy retry on a proven LIVE holder
# ---------------------------------------------------------------------------


class _VirtualClock:
    """Controllable clock seam for the start-up busy-cadence tests.

    The lifecycle sizes the start-up busy wait in its own clock seam
    (``session_lifecycle.time.monotonic``); advancing it inside the fake
    cooldown sleep plays a 0/30/60/90 s cadence in real milliseconds.
    """

    def __init__(self) -> None:
        self.now = 1_000.0

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, delay: float) -> None:
        self.now += delay


def _busy_lifecycle(
    clock: _VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
    *,
    busy_cooldown_s: float = 30.0,
    live_holder_after_s: float | None = 90.0,
) -> tuple[CoreRadioSessionLifecycle, FakeMechanism, list[LifecycleEvent]]:
    """Lifecycle with the production start-up busy wiring on a virtual clock."""
    monkeypatch.setattr(
        "rigplane.runtime.session_lifecycle.time.monotonic", clock.monotonic
    )
    monkeypatch.setattr("rigplane.runtime.session_lifecycle.asyncio.sleep", clock.sleep)
    mech = FakeMechanism()
    lc = CoreRadioSessionLifecycle(
        mechanism=mech,
        not_ready_cooldown_s=busy_cooldown_s,
        reject_cooldown_s=busy_cooldown_s,
        max_connect_attempts=4,
        first_connect_busy_cooldown_s=busy_cooldown_s,
        first_connect_busy_budget_s=600.0,
        first_connect_busy_live_holder_after_s=live_holder_after_s,
    )
    events: list[LifecycleEvent] = []
    lc.add_event_listener(events.append)
    return lc, mech, events


async def test_live_holder_stop_on_the_threshold_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MOR-3391 part 1b: busy at 0/30/60 then at the 90 s threshold stops.

    A busy reject on an attempt that starts at or after first-busy + 90 s
    proves a LIVE session holds the radio, so the loop raises the honest
    error instead of retrying into the 600 s budget. Exactly four logins,
    each released, none after the stop.
    """
    clock = _VirtualClock()
    lc, mech, _events = _busy_lifecycle(clock, monkeypatch)
    for _ in range(5):
        mech.queue_busy_reject()

    with pytest.raises(ConnectionError) as excinfo:
        await lc.connect()

    message = str(excinfo.value)
    assert "another client holds the session" in message
    assert "Check for another active radio client before retrying." in message
    assert "this computer" not in message
    assert "runs here" not in message
    assert "stop one of them" not in message
    assert mech.connect_calls == 4
    assert mech.release_calls == 4
    assert not mech.claimed
    assert lc.state is LifecycleState.DISCONNECTED


async def test_live_holder_stop_raises_held_subclass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The live-owner stop raises RadioSessionHeldError.

    The stop keeps raising the established connection error (every
    existing ``except`` still catches it) through a dedicated subclass,
    so the CLI can map it to exit code 3.
    """
    from rigplane.core.exceptions import (
        ConnectionError as RigplaneConnectionError,
    )
    from rigplane.core.exceptions import RadioSessionHeldError

    clock = _VirtualClock()
    lc, mech, _events = _busy_lifecycle(clock, monkeypatch)
    for _ in range(5):
        mech.queue_busy_reject()

    with pytest.raises(
        RadioSessionHeldError, match="another client holds the session"
    ) as excinfo:
        await lc.connect()

    assert isinstance(excinfo.value, RigplaneConnectionError)
    assert mech.connect_calls == 4
    assert mech.release_calls == 4
    assert not mech.claimed
    assert lc.state is LifecycleState.DISCONNECTED


async def test_dead_hold_granted_before_the_threshold_connects(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MOR-3391 part 1b: a hold that ends between attempts grants in time.

    Two busy passes, then the radio grants on the third attempt (started at
    +60 s, before the +90 s threshold): the loop connects and never trips
    the live-holder stop.
    """
    clock = _VirtualClock()
    lc, mech, _events = _busy_lifecycle(clock, monkeypatch)
    mech.queue_busy_reject()
    mech.queue_busy_reject()
    mech.queue_ok()

    await lc.connect()

    assert lc.state is LifecycleState.CONNECTED
    assert mech.connect_calls == 3


async def test_live_holder_boundary_just_before_threshold_continues(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MOR-3391 part 1b: an attempt starting just before +90 s does not stop.

    With a 29 s cadence the fourth attempt starts at +87 s — still a dead
    hold as far as the rule knows — so only the fifth attempt (at +116 s)
    trips the stop.
    """
    clock = _VirtualClock()
    lc, mech, _events = _busy_lifecycle(
        clock, monkeypatch, busy_cooldown_s=29.0, live_holder_after_s=90.0
    )
    for _ in range(6):
        mech.queue_busy_reject()

    with pytest.raises(ConnectionError, match="another client holds the session"):
        await lc.connect()

    assert mech.connect_calls == 5
    assert mech.release_calls == 5


async def test_startup_busy_budget_cap_without_live_holder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MOR-3391 part 1b: with the live-holder stop unset the 600 s cap stays.

    The outer budget is the backstop for lifecycles constructed without the
    threshold: a persistent busy still ends in the resident never-ready
    error, every pass released.
    """
    clock = _VirtualClock()
    lc, mech, _events = _busy_lifecycle(clock, monkeypatch, live_holder_after_s=None)
    for _ in range(22):
        mech.queue_busy_reject()

    with pytest.raises(ConnectionError, match="never became ready"):
        await lc.connect()

    # Attempts start at +0/+30/...; the first start at or past the +600 s
    # deadline is the 21st.
    assert mech.connect_calls == 21
    assert mech.release_calls == 21
    assert not mech.claimed


# ---------------------------------------------------------------------------
# MOR-3391 part 1b round 2 — a discovery timeout inside the busy loop is a
# busy pass
# ---------------------------------------------------------------------------


async def test_discovery_timeout_after_busy_is_a_busy_pass_then_connects(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """MOR-3391 round 2 (bench second run): busy, a retry whose discovery
    times out, then a grant.

    The mid-loop discovery timeout counts as one more busy pass — same
    30 s cadence, one status line naming its kind — instead of exiting on
    the discovery error, so the later attempt connects.
    """
    clock = _VirtualClock()
    lc, mech, _events = _busy_lifecycle(clock, monkeypatch)
    mech.queue_busy_reject()
    mech.queue_discovery_timeout()
    mech.queue_ok()

    with caplog.at_level("WARNING", logger="rigplane.runtime.session_lifecycle"):
        await lc.connect()

    assert lc.state is LifecycleState.CONNECTED
    assert mech.connect_calls == 3
    # The granted attempt holds its session: only the two failed attempts
    # released.
    assert mech.release_calls == 2
    busy_lines = [
        rec.getMessage()
        for rec in caplog.records
        if rec.name == "rigplane.runtime.session_lifecycle"
        and "Radio session busy" in rec.getMessage()
    ]
    discovery_lines = [
        rec.getMessage()
        for rec in caplog.records
        if rec.name == "rigplane.runtime.session_lifecycle"
        and "discovery" in rec.getMessage().lower()
    ]
    assert len(busy_lines) == 1
    assert len(discovery_lines) == 1
    assert "attempt 1" in busy_lines[0]
    assert "attempt 2" in discovery_lines[0]
    assert "fresh login" not in discovery_lines[0]


async def test_discovery_timeouts_until_threshold_stop_with_busy_reason(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MOR-3391 round 2: busy, then unanswered discoveries until the
    threshold attempt stops with the busy reason — never the discovery
    message.  A hold that still answers nothing at the threshold proves a
    live session either way.
    """
    clock = _VirtualClock()
    lc, mech, _events = _busy_lifecycle(clock, monkeypatch)
    mech.queue_busy_reject()
    mech.queue_discovery_timeout()
    mech.queue_discovery_timeout()
    mech.queue_discovery_timeout()

    with pytest.raises(ConnectionError) as excinfo:
        await lc.connect()

    message = str(excinfo.value)
    assert "another client holds the session" in message
    assert "Check for another active radio client before retrying." in message
    assert "this computer" not in message
    assert "runs here" not in message
    assert "stop one of them" not in message
    assert "did not respond to discovery" not in message
    # Attempts start at +0/+30/+60/+90 s; the fourth is at the threshold.
    assert mech.connect_calls == 4
    assert mech.release_calls == 4
    assert not mech.claimed
    assert lc.state is LifecycleState.DISCONNECTED


async def test_discovery_timeout_before_any_busy_stays_fatal(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """MOR-3391 part 1b(c) (bench first run): silent first discoveries
    retry twice, then stay fatal with today's message.

    Three unanswered first-connect discoveries — before any busy reject —
    raise the discovery error (never the busy reason) after exactly three
    attempts, one log line per attempt, about two cooldowns apart.  A radio
    that is off looks the same and fails here, only slower.
    """
    clock = _VirtualClock()
    lc, mech, _events = _busy_lifecycle(clock, monkeypatch)
    for _ in range(3):
        mech.queue_discovery_timeout()

    with pytest.raises(TimeoutError, match="did not respond to discovery"):
        await lc.connect()

    message_lines = [
        rec.getMessage()
        for rec in caplog.records
        if rec.name == "rigplane.runtime.session_lifecycle"
    ]
    assert mech.connect_calls == 3
    assert [f"discovery attempt {n}/3 got no answer" for n in (1, 2, 3)] == [
        line for line in message_lines if line.startswith("discovery attempt")
    ]
    assert not any("busy" in line.lower() for line in message_lines)
    assert clock.now - 1_000.0 == pytest.approx(2 * 30.0)
    assert lc.state is LifecycleState.DISCONNECTED


async def test_non_discovery_timeout_inside_loop_stays_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MOR-3391 round 2 scope pin: only the discovery timeout converts.

    A timeout from a later stage (here a login-response wait, which runs
    after discovery already answered) stays fatal even after a busy
    reject — the busy-pass conversion covers discovery attempts, which
    sent no login, not attempts that already spoke to the radio.
    """
    clock = _VirtualClock()
    lc, mech, _events = _busy_lifecycle(clock, monkeypatch)
    mech.queue_busy_reject()
    mech.queue_exception(TimeoutError("login response timed out"))

    with pytest.raises(TimeoutError, match="login response timed out"):
        await lc.connect()

    assert mech.connect_calls == 2


async def test_discovery_timeout_after_a_clean_connect_stays_fatal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MOR-3391 round 2: reconnects keep today's behaviour.

    A discovery timeout on a later connect — with no busy reject on this
    connect — is fatal even though an earlier connect succeeded.
    """
    clock = _VirtualClock()
    lc, mech, _events = _busy_lifecycle(clock, monkeypatch)
    mech.queue_ok()
    await lc.connect()
    await lc.disconnect()
    mech.queue_discovery_timeout()

    with pytest.raises(TimeoutError, match="did not respond to discovery"):
        await lc.connect()

    assert lc.state is LifecycleState.DISCONNECTED


# ---------------------------------------------------------------------------
# MOR-3391 part 1b(c) — a silent first discovery retries twice, then gives up
# ---------------------------------------------------------------------------


async def test_silent_first_discovery_then_grant_connects(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """MOR-3391 part 1b(c) (bench second run): a silent first discovery
    retries once and connects.

    The unanswered first attempt logs exactly one ``discovery attempt 1/3
    got no answer`` line and waits out one busy-cadence cooldown; the
    granted retry connects as today.
    """
    clock = _VirtualClock()
    lc, mech, _events = _busy_lifecycle(clock, monkeypatch)
    mech.queue_discovery_timeout()
    mech.queue_ok()

    with caplog.at_level("WARNING", logger="rigplane.runtime.session_lifecycle"):
        await lc.connect()

    assert lc.state is LifecycleState.CONNECTED
    assert mech.connect_calls == 2
    lines = [
        rec.getMessage()
        for rec in caplog.records
        if rec.name == "rigplane.runtime.session_lifecycle"
    ]
    assert lines.count("discovery attempt 1/3 got no answer") == 1
    assert not any(
        line.startswith("discovery attempt")
        and line != "discovery attempt 1/3 got no answer"
        for line in lines
    )
    assert not any("busy" in line.lower() for line in lines)
    assert clock.now - 1_000.0 == pytest.approx(30.0)


async def test_silent_first_discovery_then_busy_uses_busy_rules(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """MOR-3391 part 1b(c): a busy reject after a silent discovery takes
    over under the #4178 rules.

    One silent discovery, then busy rejects until the live-holder stop:
    the loop stops with "radio busy: another client holds the session"
    (never the discovery message), and the single silent attempt logged
    its one line.
    """
    clock = _VirtualClock()
    lc, mech, _events = _busy_lifecycle(clock, monkeypatch)
    mech.queue_discovery_timeout()
    for _ in range(4):
        mech.queue_busy_reject()

    with pytest.raises(ConnectionError) as excinfo:
        await lc.connect()

    message = str(excinfo.value)
    assert "another client holds the session" in message
    assert "did not respond to discovery" not in message
    # Silent attempt at +0, busy passes at +30/+60/+90, stop at +120.
    assert mech.connect_calls == 5
    lines = [
        rec.getMessage()
        for rec in caplog.records
        if rec.name == "rigplane.runtime.session_lifecycle"
    ]
    assert lines.count("discovery attempt 1/3 got no answer") == 1
    assert lc.state is LifecycleState.DISCONNECTED
