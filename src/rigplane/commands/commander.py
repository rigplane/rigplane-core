from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, TypeVar

from rigplane.core.exceptions import BackgroundSendDropped, ConnectionError
from rigplane.core.types import CivFrame

__all__ = ["IcomCommander", "Priority"]


class Priority(IntEnum):
    FORCE_RELEASE = -20
    ABORT = -10
    IMMEDIATE = 0
    NORMAL = 10
    BACKGROUND = 20


T = TypeVar("T")

# Defensive upper bound on outstanding fire-and-forget BACKGROUND sends
# (``wait_dispatch=False``).  Background polls are already bounded by the
# scheduler's in-flight guard (~25 cadence groups), so this is a safety net
# against pathological growth, not a normal-operation limit.  When the cap is
# reached, the newest BACKGROUND fire-and-forget send is dropped: ``send``
# raises ``BackgroundSendDropped`` instead of returning ``None``.  The cap
# NEVER applies to non-background sends or to ``wait_dispatch=True`` sends.
_MAX_BG_INFLIGHT = 64


@dataclass(slots=True)
class _QueueItem:
    priority: int
    seq: int
    payload: bytes
    future: asyncio.Future[CivFrame | None]
    key: str | None = None
    wait_response: bool = True
    # True only for fire-and-forget BACKGROUND sends counted against the
    # ``_MAX_BG_INFLIGHT`` cap; the worker decrements the counter for these.
    counts_bg_inflight: bool = False
    # True for any fire-and-forget send (``wait_dispatch=False``): no caller
    # ever awaits ``future``, so whoever fails it must retrieve the exception
    # itself or GC logs "Future exception was never retrieved" (MOR-595).
    fire_and_forget: bool = False
    # Authority-owned currency predicate.  The commander only transports it
    # to the executor; the final wire seam decides whether the attempt still
    # belongs to the live authority effect.
    is_current: Callable[[], bool] | None = None
    # Task that queued the frame.  An exclusive reservation runs that task's
    # frames and leaves every other task's frames queued.
    enqueued_by: asyncio.Task[Any] | None = None
    # True for a TX release (MOR-3743).  Queued while a reservation is
    # held, such an item opens a seq barrier: it and every item queued
    # before it become runnable during the hold, in (priority, seq)
    # order, so a de-key never waits out a hold nor overtakes an earlier
    # queued key-on.  Items queued after it stay deferred as before.
    releases_backlog: bool = False


def _fail_item(item: _QueueItem, exc: BaseException) -> None:
    """Fail a queue item's future, retrieving the exception for orphans.

    Fire-and-forget futures have no consumer, so teardown (``stop()`` and
    worker cancellation) marks their exception as retrieved immediately to
    keep a failed ``connect()``/disconnect from flooding the log with
    ``Future exception was never retrieved: ConnectionError('Commander
    stopped')`` warnings at GC time (MOR-595).
    """
    if item.future.done():
        return
    item.future.set_exception(exc)
    if item.fire_and_forget:
        item.future.exception()


class IcomCommander:
    """wfview-style serialized command executor with priorities.

    Features:
    - strict in-order execution within priority levels
    - optional dedup for background polling keys
    - transaction helper (snapshot/restore)
    - exclusive reservation around one task's read-modify-write
    """

    def __init__(
        self,
        execute: Callable[[bytes, bool], Awaitable[CivFrame | None]],
    ) -> None:
        self._execute = execute
        self._queue: asyncio.PriorityQueue[tuple[int, int, _QueueItem]] | None = None
        self._worker: asyncio.Task[None] | None = None
        self._seq = 0
        # Pending keyed items per key, oldest first.  Several items may be
        # pending under one key while a reservation defers some of them
        # (MOR-3743); a joiner joins the first item it may join, and each
        # item's completion removes exactly that item.
        self._pending_by_key: dict[str, list[_QueueItem]] = {}
        # Count of outstanding fire-and-forget BACKGROUND sends (see
        # ``_MAX_BG_INFLIGHT``).  Incremented at enqueue, decremented in the
        # worker's ``finally`` for items flagged ``counts_bg_inflight``.
        self._bg_inflight = 0
        self._reservation_owner: asyncio.Task[Any] | None = None
        self._reservation_depth = 0
        self._reservation_gate = asyncio.Condition()
        self._deferred: list[tuple[int, int, _QueueItem]] = []
        self._work_ready = asyncio.Event()
        # Highest seq opened by a ``releases_backlog`` item queued during
        # the current hold; 0 = no barrier.  Cleared when the hold ends.
        self._release_barrier_seq = 0
        # Items still to dispatch before the next reservation may start
        # (everything queued when the previous hold ended).  The worker
        # counts down as it dispatches items from that snapshot — bounded
        # by ``_reservation_drain_mark``, the highest seq it covers — and
        # notifies the gate at zero, so alternating holds cannot starve
        # older deferred work (MOR-3743).
        self._reservation_drain_pending = 0
        self._reservation_drain_mark = 0

    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._queue = asyncio.PriorityQueue()
            self._deferred.clear()
            self._release_barrier_seq = 0
            self._reservation_drain_pending = 0
            self._reservation_drain_mark = 0
            self._worker = asyncio.create_task(self._loop())

    def _accepting(self) -> bool:
        worker = self._worker
        return (
            self._queue is not None
            and worker is not None
            and not worker.done()
            and not worker.cancelling()
        )

    def _reservation_allows(self, item: _QueueItem) -> bool:
        owner = self._reservation_owner
        if owner is None:
            return True
        if item.priority <= int(Priority.ABORT):
            return True
        if item.enqueued_by is owner:
            return True
        # A TX release runs during the hold (MOR-3743): once queued, it
        # opens the barrier at its own seq, which also makes it runnable.
        if item.releases_backlog:
            return True
        return item.seq <= self._release_barrier_seq

    def _unregister_pending(self, item: _QueueItem) -> None:
        """Remove exactly this item from its key's pending list."""
        if item.key is None:
            return
        pending = self._pending_by_key.get(item.key)
        if pending is None:
            return
        for index, candidate in enumerate(pending):
            if candidate is item:
                del pending[index]
                break
        if not pending:
            self._pending_by_key.pop(item.key, None)

    def _fail_pending(self, item: _QueueItem) -> None:
        self._unregister_pending(item)
        if item.counts_bg_inflight and self._bg_inflight > 0:
            self._bg_inflight -= 1
        _fail_item(item, ConnectionError("Commander stopped"))

    def _drain_into_deferred(
        self, queue: asyncio.PriorityQueue[tuple[int, int, _QueueItem]]
    ) -> None:
        while True:
            try:
                self._deferred.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                return

    def _pop_runnable(self) -> tuple[int, int, _QueueItem] | None:
        best_at = -1
        best_key: tuple[int, int] | None = None
        for index, queued in enumerate(self._deferred):
            if not self._reservation_allows(queued[2]):
                continue
            key = (queued[0], queued[1])
            if best_key is None or key < best_key:
                best_key = key
                best_at = index
        if best_at < 0:
            return None
        return self._deferred.pop(best_at)

    async def _next_item(
        self, queue: asyncio.PriorityQueue[tuple[int, int, _QueueItem]]
    ) -> tuple[int, int, _QueueItem]:
        while True:
            self._drain_into_deferred(queue)
            chosen = self._pop_runnable()
            if chosen is not None:
                return chosen
            if self._reservation_owner is None and not self._deferred:
                queued = await queue.get()
                if self._reservation_allows(queued[2]):
                    return queued
                self._deferred.append(queued)
                continue
            self._work_ready.clear()
            self._drain_into_deferred(queue)
            chosen = self._pop_runnable()
            if chosen is not None:
                return chosen
            await self._work_ready.wait()

    @asynccontextmanager
    async def exclusive(self) -> AsyncIterator[None]:
        """Keep this task's frames together until the context ends.

        Frames queued by any other task stay queued, except
        ``Priority.FORCE_RELEASE``, ``Priority.ABORT`` and TX releases: a
        ``releases_backlog`` send runs during the hold together with every
        command queued before it, in (priority, seq) order, so an earlier
        deferred key-on still keys first and a read-modify-write whose
        preserved fields that traffic changes fails its own readback
        instead of committing a mixed state. Cancellation releases the
        hold. A keyed dedupe send never joins an item the caller's own
        send could outrun (the owner joining a deferred foreign item); it
        enqueues its own frame. A new reservation starts only after every
        command queued before the previous release has been dispatched.
        """
        task = asyncio.current_task()
        if task is None:
            raise RuntimeError("exclusive reservation requires a running task")
        if self._reservation_owner is task:
            if not self._accepting():
                raise ConnectionError("Commander is not started")
            self._reservation_depth += 1
            try:
                yield
            finally:
                self._reservation_depth -= 1
            return
        async with self._reservation_gate:
            while True:
                if not self._accepting():
                    raise ConnectionError("Commander is not started")
                if (
                    self._reservation_owner is None
                    and self._reservation_drain_pending == 0
                ):
                    break
                await self._reservation_gate.wait()
            self._reservation_owner = task
            self._reservation_depth = 1
        try:
            yield
        finally:
            async with self._reservation_gate:
                if self._reservation_owner is task:
                    self._reservation_depth -= 1
                    if self._reservation_depth <= 0:
                        self._reservation_owner = None
                        self._reservation_depth = 0
                        self._release_barrier_seq = 0
                        # Grant the next hold only after the worker has
                        # dispatched everything queued before this release
                        # (MOR-3743): snapshot the backlog now and let the
                        # worker notify the gate as it drains to zero.
                        queue = self._queue
                        if queue is not None:
                            self._drain_into_deferred(queue)
                        self._reservation_drain_pending = len(self._deferred)
                        self._reservation_drain_mark = max(
                            (item.seq for _, _, item in self._deferred), default=0
                        )
                        self._reservation_gate.notify_all()
            self._work_ready.set()

    async def stop(self) -> None:
        worker, queue = self._worker, self._queue
        # Close admission before yielding; overlapping stops cancel only once.
        if worker is not None and not worker.done() and not worker.cancelling():
            worker.cancel()
        if queue is not None:
            while True:
                try:
                    _, _, item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                self._fail_pending(item)
                queue.task_done()
            while self._deferred:
                _, _, item = self._deferred.pop()
                self._fail_pending(item)
                queue.task_done()
        self._work_ready.set()
        async with self._reservation_gate:
            self._reservation_gate.notify_all()

        try:
            if worker is not None:
                await asyncio.shield(worker)
        except asyncio.CancelledError:
            me = asyncio.current_task()
            if me is not None and me.cancelling():
                raise
        finally:
            # A cancelled waiter retains the live join handle. A completed old
            # worker may already have restarted through a done callback.
            if self._worker is worker and (worker is None or worker.done()):
                while self._deferred:
                    _, _, item = self._deferred.pop()
                    self._fail_pending(item)
                    if queue is not None:
                        queue.task_done()
                self._worker = None
                self._queue = None
                self._pending_by_key.clear()
                self._bg_inflight = 0
                self._release_barrier_seq = 0
                self._reservation_drain_pending = 0
                self._reservation_drain_mark = 0

    async def send(
        self,
        payload: bytes,
        *,
        priority: Priority = Priority.NORMAL,
        key: str | None = None,
        dedupe: bool = False,
        timeout: float | None = None,
        wait_response: bool = True,
        wait_dispatch: bool = True,
        is_current: Callable[[], bool] | None = None,
        releases_backlog: bool = False,
    ) -> CivFrame | None:
        """Enqueue a CI-V command.

        Args:
            wait_dispatch: When True (default), await the worker dispatching
                this item and return its result — the historical blocking
                contract for user commands.  When False, return ``None``
                immediately after enqueueing without awaiting the worker; the
                item is still executed, and its future resolved by the
                worker, but the caller does not observe it.  Used by the
                background poller so the poll burst does not park the poll loop
                (responses arrive via the RX path, not this future).  For
                ``Priority.BACKGROUND`` fire-and-forget sends a defensive
                ``_MAX_BG_INFLIGHT`` cap bounds outstanding work (drop-newest,
                raising ``BackgroundSendDropped``).
            releases_backlog: True for a TX release (MOR-3743).  Queued
                while a reservation is held, the item opens a seq barrier
                so it and everything queued before it run during the hold
                in (priority, seq) order.  It changes no priority.
        """
        if (
            self._queue is None
            or self._worker is None
            or self._worker.done()
            or self._worker.cancelling()
        ):
            raise ConnectionError("Commander is not started")

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[CivFrame | None] = loop.create_future()
        self._seq += 1

        # Defensive cap on fire-and-forget BACKGROUND sends only.  NORMAL,
        # IMMEDIATE, and any wait_dispatch=True send is never capped.
        counts_bg_inflight = not wait_dispatch and priority == Priority.BACKGROUND

        # Built before the dedupe decision: the join rule asks whether the
        # item this caller would enqueue can run during a hold (MOR-3743).
        item = _QueueItem(
            int(priority),
            self._seq,
            payload,
            fut,
            key=key,
            wait_response=wait_response,
            counts_bg_inflight=counts_bg_inflight,
            fire_and_forget=not wait_dispatch,
            is_current=is_current,
            enqueued_by=asyncio.current_task(),
            releases_backlog=releases_backlog,
        )

        if dedupe and key is not None:
            caller_runs_during_hold = self._reservation_allows(item)
            for existing in self._pending_by_key.get(key, ()):
                if existing.future.done():
                    continue
                # A caller whose own item would run during the hold (the
                # owner, an abort-tier send, or a TX release) never joins
                # a deferred item: that item cannot run until the hold
                # ends, and the owner of the hold may be this very caller,
                # so joining it would deadlock (MOR-3743). Such a caller
                # enqueues its own item instead.
                if caller_runs_during_hold and not self._reservation_allows(existing):
                    continue
                if timeout is not None:
                    return await asyncio.wait_for(existing.future, timeout=timeout)
                return await existing.future

        if counts_bg_inflight and self._bg_inflight >= _MAX_BG_INFLIGHT:
            # Drop-newest: do not enqueue. Raising (rather than returning
            # ``None``) is what tells a fire-and-forget caller the send never
            # left; ``None`` is also what an enqueued send returns.
            raise BackgroundSendDropped("background send dropped: commander at cap")

        if key is not None:
            self._pending_by_key.setdefault(key, []).append(item)

        if counts_bg_inflight:
            self._bg_inflight += 1

        await self._queue.put((item.priority, item.seq, item))
        if (
            item.releases_backlog
            and self._reservation_owner is not None
            and item.seq > self._release_barrier_seq
        ):
            self._release_barrier_seq = item.seq
        self._work_ready.set()

        if not wait_dispatch:
            # Fire-and-forget: the worker still executes and resolves
            # the future, but the caller does not wait for dispatch.
            return None

        try:
            if timeout is not None:
                return await asyncio.wait_for(fut, timeout=timeout)
            return await fut
        except asyncio.CancelledError:
            # Caller went away (e.g. rigctld command timeout/client disconnect).
            # Cancel queued/inflight future so worker can skip abandoned work.
            if not fut.done():
                fut.cancel()
            self._unregister_pending(item)
            raise

    async def transaction(
        self,
        *,
        snapshot: Callable[[], Awaitable[T]],
        restore: Callable[[T], Awaitable[None]],
        body: Callable[[], Awaitable[T]],
    ) -> T:
        state = await snapshot()
        try:
            return await body()
        finally:
            await restore(state)

    async def _loop(self) -> None:
        queue, worker = self._queue, asyncio.current_task()
        assert queue is not None and worker is not None
        try:
            while True:
                _, _, item = await self._next_item(queue)
                execute_task: asyncio.Future[CivFrame | None] | None = None
                try:
                    # Skip abandoned requests (caller cancelled/timed out
                    # before worker even started this item).
                    if item.future.done():
                        continue

                    # Run execute as an inner task so that a caller-side
                    # timeout (asyncio.wait_for in `send`) cancels JUST this
                    # in-flight command and the worker can move on, instead
                    # of blocking on a dropped reply while the rest of the
                    # queue piles up with pre-cancelled futures (#1188).
                    if item.is_current is None:
                        execution = self._execute(item.payload, item.wait_response)
                    else:
                        execution = self._execute(
                            item.payload,
                            item.wait_response,
                            is_current=item.is_current,
                        )
                    execute_task = asyncio.ensure_future(execution)
                    inflight = execute_task

                    def _propagate_cancel(
                        f: asyncio.Future[CivFrame | None],
                        t: asyncio.Future[CivFrame | None] = inflight,
                    ) -> None:
                        # Caller's wait_for fired, or caller went away.
                        # Cancel the in-flight execute so the worker
                        # unblocks immediately.
                        if f.cancelled() and not t.done():
                            t.cancel()

                    item.future.add_done_callback(_propagate_cancel)

                    try:
                        resp = await execute_task
                    except asyncio.CancelledError:
                        # Distinguish caller-driven cancel from worker
                        # teardown (c.stop()).  item.future.cancelled() alone
                        # is not a safe discriminator: when a caller timeout
                        # has already cancelled both the future and the
                        # in-flight execute, stop()'s worker.cancel() is
                        # delivered through this same await — swallowing it
                        # would park _loop on queue.get() forever and hang
                        # stop().  Task.cancelling() (3.11+) exposes the
                        # worker's own pending cancel request.
                        if item.future.cancelled() and not worker.cancelling():
                            continue
                        raise

                    if worker.cancelling():
                        raise asyncio.CancelledError
                    if not item.future.done():
                        item.future.set_result(resp)
                except asyncio.CancelledError:
                    if execute_task is not None and not execute_task.done():
                        execute_task.cancel()
                    _fail_item(item, ConnectionError("Commander stopped"))
                    raise
                except Exception as exc:
                    _fail_item(item, exc)
                    if worker.cancelling():
                        raise asyncio.CancelledError
                finally:
                    if (
                        self._reservation_drain_pending > 0
                        and item.seq <= self._reservation_drain_mark
                    ):
                        self._reservation_drain_pending -= 1
                        if self._reservation_drain_pending == 0:
                            self._reservation_drain_mark = 0
                            # Backlog from the previous hold fully
                            # dispatched: the next reservation may start
                            # (MOR-3743).
                            async with self._reservation_gate:
                                self._reservation_gate.notify_all()
                    if item.counts_bg_inflight and self._bg_inflight > 0:
                        self._bg_inflight -= 1
                    self._unregister_pending(item)
                    queue.task_done()
        except asyncio.CancelledError:
            pass
