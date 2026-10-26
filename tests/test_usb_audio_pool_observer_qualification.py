"""MOR-3777 observer qualification — event-controlled, one bounded case.

Proves the fixture observer distinguishes the three unanswered phases
(queued-not-started, body-not-returned, body-done-then-settled) via
explicit thread events, never sleep calibration; that output is
bounded and redacted; that INFO capture survives caller cancellation;
and that the seam restores. The phase-1 queue is artificial: workers
are kept busy by TEST-ONLY blockers submitted straight to the pool's
native executor, so tracked admission stays at zero and run_bounded's
saturation fail-fast (which a tracked-submission fill would trigger)
never fires. This qualifies the OBSERVER only — it is not evidence
that any historical failure reached such a queue or was caused by it.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from rigplane.audio import usb_driver

from _mor3777_pool_observer import (
    OP_STATES_MAX,
    RING_MAX,
    PoolPhaseObserver,
    set_current,
)

_WORKERS = usb_driver._CAPTURE_OPEN_MAX_WORKERS


async def _wait_until(predicate, timeout: float = 5.0) -> None:
    """Bounded event polling — no fixed-sleep calibration."""
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.005)


@pytest.mark.timeout(30)
async def test_pool_phase_observer_qualification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = usb_driver._BoundedPortAudioPool()
    observer = PoolPhaseObserver(pool)
    observer.attach(monkeypatch, usb_driver.logger)
    set_current(observer)
    release_blockers = threading.Event()
    release_body = threading.Event()
    release_cancel = threading.Event()
    entered = [threading.Event() for _ in range(_WORKERS)]
    body_entered = threading.Event()
    blockers = []
    queued: "asyncio.Task[int] | None" = None

    def blocker(index: int) -> None:
        entered[index].set()
        assert release_blockers.wait(10)

    try:
        # Occupy every worker NATIVELY (test-only): pool.inflight stays 0,
        # so run_bounded passes its saturation check and queues for real.
        for index in range(_WORKERS):
            blockers.append(pool._executor.submit(lambda i=index: blocker(i)))
        await _wait_until(lambda: all(e.is_set() for e in entered))
        assert pool.inflight == 0

        # Phase 1 — queued, not picked up. Bind the NEW submission via the
        # counter captured BEFORE create_task (the task may not have
        # started when we look); never assume it started.
        before_queued = observer.latest_sub()

        def gated_body() -> int:
            body_entered.set()
            assert release_body.wait(10)
            return 7

        queued = asyncio.create_task(
            pool.run_bounded(gated_body, what="qual", direction="rx", timeout=10.0)
        )
        await _wait_until(lambda: observer.latest_sub() > before_queued)
        sub = observer.latest_sub()
        assert not observer.has(sub, "start")
        assert not observer.has(sub, "settled")

        # Phase 2 — started, body not returned.
        release_blockers.set()
        await _wait_until(body_entered.is_set)
        assert observer.has(sub, "start")
        assert not observer.has(sub, "body_end")

        # Phase 3 — body ends before the bound; caller gets the value.
        release_body.set()
        assert await queued == 7
        await _wait_until(lambda: observer.has(sub, "settled"))
        assert observer.outcome(sub) == "ok"
        assert (
            observer.order(sub, "submit")
            < observer.order(sub, "start")
            < observer.order(sub, "body_end")
            <= observer.order(sub, "settled")
        )

        # Injected body failure: distinct settled outcome; the payload
        # (exception text) never reaches the bounded report.
        def failing_body() -> None:
            raise RuntimeError("deliberate observer qualification payload")

        before_fail = observer.latest_sub()
        with pytest.raises(RuntimeError, match="deliberate"):
            await pool.run_bounded(
                failing_body, what="qual", direction="rx", timeout=5.0
            )
        await _wait_until(lambda: observer.latest_sub() > before_fail)
        fail_sub = observer.latest_sub()
        await _wait_until(lambda: observer.has(fail_sub, "settled"))
        assert observer.outcome(fail_sub) == "err"
        report = observer.report()
        assert "deliberate" not in "\n".join(report)
        assert len(report) <= 1 + OP_STATES_MAX + RING_MAX

        # Caller cancellation: INFO caller=cancelled captured at emission;
        # the abandoned body still settles; the pool stays usable. The
        # victim's own future outcome is NOT asserted — whether the wait
        # cancels the member future is runtime behaviour we do not claim.
        before_cancel = observer.latest_sub()

        def cancelled_body() -> None:
            release_cancel.wait(10)

        victim = asyncio.create_task(
            pool.run_bounded(cancelled_body, what="qual", direction="rx", timeout=10.0)
        )
        await _wait_until(lambda: observer.latest_sub() > before_cancel)
        cancel_sub = observer.latest_sub()
        await _wait_until(lambda: observer.has(cancel_sub, "start"))
        victim.cancel()
        with pytest.raises(asyncio.CancelledError):
            await victim
        assert "cancelled" in observer.caller_states()
        release_cancel.set()
        await _wait_until(lambda: observer.has(cancel_sub, "settled"))
        assert (
            await pool.run_bounded(lambda: 3, what="qual", direction="rx", timeout=5.0)
            == 3
        )

        # after_drain snapshot semantics (what _drain records per test).
        await _wait_until(lambda: pool.inflight == 0)
        observer.mark_pool_state("after_drain")
        state = [m for m in observer.events() if m["ev"] == "after_drain"][-1]
        assert state["inflight"] == 0

        # Passivity: an unobserved pool behaves the same.
        plain = usb_driver._BoundedPortAudioPool()
        try:
            assert (
                await plain.run_bounded(
                    lambda: 7, what="qual", direction="rx", timeout=5.0
                )
                == 7
            )
            assert plain.inflight == 0
        finally:
            plain._executor.shutdown(wait=True)

        # Seam restoration: the raw method is back and functional.
        monkeypatch.undo()
        assert "submit_tracked" not in vars(pool)
        assert (
            await pool.run_bounded(lambda: 5, what="qual", direction="rx", timeout=5.0)
            == 5
        )
    finally:
        release_blockers.set()
        release_body.set()
        release_cancel.set()
        if queued is not None and not queued.done():
            await asyncio.gather(queued, return_exceptions=True)
        async with asyncio.timeout(10):
            while pool.inflight:
                await asyncio.sleep(0.01)
        for blocker_future in blockers:
            if not blocker_future.cancelled():
                try:
                    blocker_future.result(timeout=10)
                except Exception:  # noqa: BLE001 — never mask the original failure
                    pass
        set_current(None)
        observer.flush()
        observer.close()
        pool._executor.shutdown(wait=True)
