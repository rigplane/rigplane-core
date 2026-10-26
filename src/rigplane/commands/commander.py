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
        self._pending_by_key: dict[str, _QueueItem] = {}
        # Count of outstanding fire-and-forget BACKGROUND sends (see
        # ``_MAX_BG_INFLIGHT``).  Incremented at enqueue, decremented in the
        # worker's ``finally`` for items flagged ``counts_bg_inflight``.
        self._bg_inflight = 0
        self._reservation_owner: asyncio.Task[Any] | None = None
        self._reservation_depth = 0
        self._reservation_gate = asyncio.Condition()
        self._deferred: list[tuple[int, int, _QueueItem]] = []
        self._work_ready = asyncio.Event()

    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._queue = asyncio.PriorityQueue()
            self._deferred.clear()
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
        return item.enqueued_by is owner

    def _fail_pending(self, item: _QueueItem) -> None:
        if item.key is not None and self._pending_by_key.get(item.key) is item:
            self._pending_by_key.pop(item.key, None)
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
        ``Priority.FORCE_RELEASE`` and ``Priority.ABORT``, which still run.
        Cancellation releases the hold. A keyed dedupe send by the owner
        never joins a deferred foreign item; it enqueues its own frame.
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
                if self._reservation_owner is None:
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
        """
        if (
            self._queue is None
            or self._worker is None
            or self._worker.done()
            or self._worker.cancelling()
        ):
            raise ConnectionError("Commander is not started")

        if dedupe and key is not None:
            existing = self._pending_by_key.get(key)
            if (
                existing is not None
                and not existing.future.done()
                # The reservation owner never joins a deferred foreign item:
                # that item cannot run until the hold ends, and only the
                # owner ends the hold, so joining it would deadlock
                # (MOR-3743). The owner enqueues its own item instead.
                and not (
                    self._reservation_owner is asyncio.current_task()
                    and not self._reservation_allows(existing)
                )
            ):
                if timeout is not None:
                    return await asyncio.wait_for(existing.future, timeout=timeout)
                return await existing.future

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[CivFrame | None] = loop.create_future()
        self._seq += 1

        # Defensive cap on fire-and-forget BACKGROUND sends only.  NORMAL,
        # IMMEDIATE, and any wait_dispatch=True send is never capped.
        counts_bg_inflight = not wait_dispatch and priority == Priority.BACKGROUND
        if counts_bg_inflight and self._bg_inflight >= _MAX_BG_INFLIGHT:
            # Drop-newest: do not enqueue. Raising (rather than returning
            # ``None``) is what tells a fire-and-forget caller the send never
            # left; ``None`` is also what an enqueued send returns.
            raise BackgroundSendDropped("background send dropped: commander at cap")

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
        )

        if key is not None:
            self._pending_by_key[key] = item

        if counts_bg_inflight:
            self._bg_inflight += 1

        await self._queue.put((item.priority, item.seq, item))
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
            if key is not None and self._pending_by_key.get(key) is item:
                self._pending_by_key.pop(key, None)
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
                    if item.counts_bg_inflight and self._bg_inflight > 0:
                        self._bg_inflight -= 1
                    if (
                        item.key is not None
                        and self._pending_by_key.get(item.key) is item
                    ):
                        self._pending_by_key.pop(item.key, None)
                    queue.task_done()
        except asyncio.CancelledError:
            pass
