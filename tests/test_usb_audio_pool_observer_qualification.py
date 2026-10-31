"""Event-controlled observer qualification; historical CI causes remain UNKNOWN."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
from concurrent.futures import Future, ThreadPoolExecutor

import pytest

from rigplane.audio import usb_driver

from _mor3777_pool_observer import (
    OP_STATES_MAX,
    RING_MAX,
    PoolPhaseObserver,
    current_for,
    set_current,
)
from _timing import await_until

_WORKERS = usb_driver._CAPTURE_OPEN_MAX_WORKERS


async def _teardown_observed_pool(
    pool: object,
    observer: PoolPhaseObserver,
    logger: logging.Logger,
    ambient_level: int,
    release_events: "tuple[threading.Event, ...]",
    queued: "asyncio.Task[int] | None",
    blockers: "list[Future[object]]",
) -> None:
    """Restore qualification resources despite interrupted waits."""
    # --- MOR3777-RED-REGION-START (guarded cleanup steps) ---
    for event in release_events:
        event.set()
    try:
        if queued is not None and not queued.done():
            await asyncio.gather(queued, return_exceptions=True)
    finally:
        try:
            async with asyncio.timeout(10):
                while pool.inflight:
                    await asyncio.sleep(0.01)
        finally:
            try:
                for blocker_future in blockers:
                    if not blocker_future.cancelled():
                        try:
                            blocker_future.result(timeout=10)
                        except Exception:  # noqa: BLE001 — never mask the original
                            pass
            finally:
                try:
                    set_current(None)
                finally:
                    try:
                        observer.flush()
                    finally:
                        try:
                            observer.close()
                        finally:
                            try:
                                logger.setLevel(ambient_level)
                            finally:
                                pool._executor.shutdown(wait=True)
    # --- MOR3777-RED-REGION-END ---


@pytest.mark.timeout(30)
async def test_pool_phase_observer_qualification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = usb_driver._BoundedPortAudioPool()
    observer = PoolPhaseObserver(pool)
    logger = usb_driver.logger
    ambient_level = logger.level
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
        logger.setLevel(logging.WARNING)
        assert not logger.isEnabledFor(logging.INFO)
        observer.attach(monkeypatch, logger)
        assert logger.isEnabledFor(logging.INFO)
        set_current(observer)
        for index in range(_WORKERS):
            blockers.append(pool._executor.submit(lambda i=index: blocker(i)))
        await await_until(lambda: all(e.is_set() for e in entered))
        assert pool.inflight == 0

        before_queued = observer.latest_sub()

        def gated_body() -> int:
            body_entered.set()
            assert release_body.wait(10)
            return 7

        queued = asyncio.create_task(
            pool.run_bounded(gated_body, what="qual", direction="rx", timeout=10.0)
        )
        await await_until(lambda: observer.latest_sub() > before_queued)
        sub = observer.latest_sub()
        assert not observer.has(sub, "start")
        assert not observer.has(sub, "settled")

        release_blockers.set()
        await await_until(body_entered.is_set)
        assert observer.has(sub, "start")
        assert not observer.has(sub, "body_end")

        release_body.set()
        assert await queued == 7
        await await_until(lambda: observer.has(sub, "settled"))
        assert observer.outcome(sub) == "ok"
        assert (
            observer.order(sub, "submit")
            < observer.order(sub, "start")
            < observer.order(sub, "body_end")
            <= observer.order(sub, "settled")
        )

        def failing_body() -> None:
            raise RuntimeError("deliberate observer qualification payload")

        before_fail = observer.latest_sub()
        with pytest.raises(RuntimeError, match="deliberate"):
            await pool.run_bounded(
                failing_body, what="qual", direction="rx", timeout=5.0
            )
        await await_until(lambda: observer.latest_sub() > before_fail)
        fail_sub = observer.latest_sub()
        await await_until(lambda: observer.has(fail_sub, "settled"))
        assert observer.outcome(fail_sub) == "err"
        report = observer.report()
        assert "deliberate" not in "\n".join(report)
        assert len(report) <= 1 + OP_STATES_MAX + RING_MAX

        before_cancel = observer.latest_sub()

        def cancelled_body() -> None:
            release_cancel.wait(10)

        victim = asyncio.create_task(
            pool.run_bounded(cancelled_body, what="qual", direction="rx", timeout=10.0)
        )
        await await_until(lambda: observer.latest_sub() > before_cancel)
        cancel_sub = observer.latest_sub()
        await await_until(lambda: observer.has(cancel_sub, "start"))
        victim.cancel()
        with pytest.raises(asyncio.CancelledError):
            await victim
        assert "cancelled" in observer.caller_states()
        release_cancel.set()
        await await_until(lambda: observer.has(cancel_sub, "settled"))
        assert (
            await pool.run_bounded(lambda: 3, what="qual", direction="rx", timeout=5.0)
            == 3
        )

        await await_until(lambda: pool.inflight == 0)
        observer.mark_pool_state("after_drain")
        state = [m for m in observer.events() if m["ev"] == "after_drain"][-1]
        assert state["inflight"] == 0

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

        monkeypatch.undo()
        assert "submit_tracked" not in vars(pool)
        assert (
            await pool.run_bounded(lambda: 5, what="qual", direction="rx", timeout=5.0)
            == 5
        )

        observer.close()
        assert logger.level == logging.WARNING
        assert not logger.isEnabledFor(logging.INFO)
    finally:
        await _teardown_observed_pool(
            pool,
            observer,
            logger,
            ambient_level,
            (release_blockers, release_body, release_cancel),
            queued,
            blockers,
        )


@pytest.mark.timeout(30)
async def test_pool_phase_observer_cleanup_restores_after_drain_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Injected drain timeout must propagate after restoring every resource."""
    pool = usb_driver._BoundedPortAudioPool()
    observer = PoolPhaseObserver(pool)
    logger = usb_driver.logger
    ambient_level = logger.level
    ambient_info = logger.isEnabledFor(logging.INFO)
    handlers_before = list(logger.handlers)
    release_events = (threading.Event(), threading.Event(), threading.Event())

    class _InjectedDrainTimeout:
        async def __aenter__(self) -> "_InjectedDrainTimeout":
            return self

        async def __aexit__(self, *exc_info: object) -> None:
            raise asyncio.TimeoutError("injected drain timeout")

    try:
        monkeypatch.setattr(asyncio, "timeout", lambda *_: _InjectedDrainTimeout())
        logger.setLevel(logging.WARNING)
        assert not logger.isEnabledFor(logging.INFO)
        observer.attach(monkeypatch, logger)
        assert logger.isEnabledFor(logging.INFO)
        set_current(observer)
        assert current_for(pool) is observer
        with pytest.raises(asyncio.TimeoutError, match="injected drain timeout"):
            await _teardown_observed_pool(
                pool, observer, logger, ambient_level, release_events, None, []
            )
        monkeypatch.undo()
        assert current_for(pool) is None
        assert logger.level == ambient_level
        assert logger.isEnabledFor(logging.INFO) == ambient_info
        assert logger.handlers == handlers_before
        assert "submit_tracked" not in vars(pool)
        assert all(event.is_set() for event in release_events)
        with pytest.raises(RuntimeError, match="shutdown"):
            pool._executor.submit(lambda: None)
    finally:
        monkeypatch.undo()
        set_current(None)
        observer.close()
        logger.setLevel(ambient_level)
        pool._executor.shutdown(wait=True)


@pytest.mark.timeout(30)
async def test_pool_phase_observer_cleanup_restores_after_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Cancel a suspended drain; preserve cancellation and restore resources."""
    inflight_reads = [0]

    class _HangingDrainPool:
        def __init__(self) -> None:
            self._executor = ThreadPoolExecutor(max_workers=1)
            self.submit_tracked = staticmethod(lambda fn: None)

        @property
        def inflight(self) -> int:
            inflight_reads[0] += 1
            return 1

    pool = _HangingDrainPool()
    original_submit = pool.submit_tracked
    observer = PoolPhaseObserver(pool)
    logger = usb_driver.logger
    ambient_level = logger.level
    ambient_info = logger.isEnabledFor(logging.INFO)
    handlers_before = list(logger.handlers)
    release_events = (threading.Event(), threading.Event(), threading.Event())
    task = None
    try:
        logger.setLevel(logging.WARNING)
        observer.attach(monkeypatch, logger)
        set_current(observer)
        task = asyncio.create_task(
            _teardown_observed_pool(
                pool, observer, logger, ambient_level, release_events, None, []
            )
        )
        await await_until(lambda: inflight_reads[0] >= 3)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()
        monkeypatch.undo()
        assert current_for(pool) is None
        assert logger.level == ambient_level
        assert logger.isEnabledFor(logging.INFO) == ambient_info
        assert logger.handlers == handlers_before
        assert pool.submit_tracked is original_submit
        assert all(event.is_set() for event in release_events)
        with pytest.raises(RuntimeError, match="shutdown"):
            pool._executor.submit(lambda: None)
    finally:
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        monkeypatch.undo()
        set_current(None)
        observer.close()
        logger.setLevel(ambient_level)
        pool._executor.shutdown(wait=True)
