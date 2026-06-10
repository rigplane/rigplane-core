from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import IntEnum
from typing import TypeVar

from rigplane.core.exceptions import ConnectionError
from rigplane.core.types import CivFrame

__all__ = ["IcomCommander", "Priority"]


class Priority(IntEnum):
    IMMEDIATE = 0
    NORMAL = 10
    BACKGROUND = 20


T = TypeVar("T")

# Defensive upper bound on outstanding fire-and-forget BACKGROUND sends
# (``wait_dispatch=False``).  Background polls are already bounded by the
# scheduler's in-flight guard (~25 cadence groups), so this is a safety net
# against pathological growth, not a normal-operation limit.  When the cap is
# reached, the newest BACKGROUND fire-and-forget send is dropped (its future is
# resolved with ``None`` so the caller still returns immediately).  The cap
# NEVER applies to NORMAL/IMMEDIATE sends or to ``wait_dispatch=True`` sends.
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
    - configurable pacing between commands
    - optional dedup for background polling keys
    - transaction helper (snapshot/restore)
    """

    def __init__(
        self,
        execute: Callable[[bytes, bool], Awaitable[CivFrame | None]],
        *,
        min_interval: float = 0.035,
    ) -> None:
        self._execute = execute
        self._min_interval = min_interval
        self._queue: asyncio.PriorityQueue[tuple[int, int, _QueueItem]] | None = None
        self._worker: asyncio.Task[None] | None = None
        self._seq = 0
        self._last_send = 0.0
        self._pending_by_key: dict[str, asyncio.Future[CivFrame | None]] = {}
        # Count of outstanding fire-and-forget BACKGROUND sends (see
        # ``_MAX_BG_INFLIGHT``).  Incremented at enqueue, decremented in the
        # worker's ``finally`` for items flagged ``counts_bg_inflight``.
        self._bg_inflight = 0

    def start(self) -> None:
        if self._worker is None or self._worker.done():
            self._queue = asyncio.PriorityQueue()
            self._worker = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._queue is not None:
            while True:
                try:
                    _, _, item = self._queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                if item.key is not None:
                    self._pending_by_key.pop(item.key, None)
                _fail_item(item, ConnectionError("Commander stopped"))
                self._queue.task_done()

        if self._worker is not None and not self._worker.done():
            self._worker.cancel()
            try:
                await self._worker
            except asyncio.CancelledError:
                pass

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
    ) -> CivFrame | None:
        """Enqueue a CI-V command.

        Args:
            wait_dispatch: When True (default), await the worker dispatching
                this item and return its result — the historical blocking
                contract for user commands.  When False, return ``None``
                immediately after enqueueing without awaiting the worker; the
                item is still paced, executed, and its future resolved by the
                worker, but the caller does not observe it.  Used by the
                background poller so the poll burst does not park the poll loop
                (responses arrive via the RX path, not this future).  For
                ``Priority.BACKGROUND`` fire-and-forget sends a defensive
                ``_MAX_BG_INFLIGHT`` cap bounds outstanding work (drop-newest).
        """
        if self._queue is None or self._worker is None:
            raise ConnectionError("Commander is not started")

        if dedupe and key is not None:
            existing = self._pending_by_key.get(key)
            if existing is not None and not existing.done():
                if timeout is not None:
                    return await asyncio.wait_for(existing, timeout=timeout)
                return await existing

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[CivFrame | None] = loop.create_future()
        self._seq += 1

        # Defensive cap on fire-and-forget BACKGROUND sends only.  NORMAL,
        # IMMEDIATE, and any wait_dispatch=True send is never capped.
        counts_bg_inflight = not wait_dispatch and priority == Priority.BACKGROUND
        if counts_bg_inflight and self._bg_inflight >= _MAX_BG_INFLIGHT:
            # Drop-newest: resolve the future so the caller still returns
            # immediately, and do NOT enqueue (so no key registration either).
            fut.set_result(None)
            return None

        item = _QueueItem(
            int(priority),
            self._seq,
            payload,
            fut,
            key=key,
            wait_response=wait_response,
            counts_bg_inflight=counts_bg_inflight,
            fire_and_forget=not wait_dispatch,
        )

        if key is not None:
            self._pending_by_key[key] = fut

        if counts_bg_inflight:
            self._bg_inflight += 1

        await self._queue.put((item.priority, item.seq, item))

        if not wait_dispatch:
            # Fire-and-forget: the worker still paces, executes, and resolves
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
            if key is not None and self._pending_by_key.get(key) is fut:
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
        assert self._queue is not None
        try:
            while True:
                _, _, item = await self._queue.get()
                execute_task: asyncio.Future[CivFrame | None] | None = None
                try:
                    # Skip abandoned requests (caller cancelled/timed out
                    # before worker even started this item).
                    if item.future.done():
                        continue

                    now = asyncio.get_running_loop().time()
                    delta = now - self._last_send
                    if delta < self._min_interval:
                        await asyncio.sleep(self._min_interval - delta)

                    # Could be cancelled during pacing sleep.
                    if item.future.done():
                        continue

                    # Run execute as an inner task so that a caller-side
                    # timeout (asyncio.wait_for in `send`) cancels JUST this
                    # in-flight command and the worker can move on, instead
                    # of blocking on a dropped reply while the rest of the
                    # queue piles up with pre-cancelled futures (#1188).
                    execute_task = asyncio.ensure_future(
                        self._execute(item.payload, item.wait_response)
                    )
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
                        # teardown (c.stop()): on caller-cancel, item.future
                        # is already in cancelled state; on worker stop, the
                        # outer except below handles it.
                        if item.future.cancelled():
                            # Packet was sent on the wire — honor pacing
                            # for the next item.
                            self._last_send = asyncio.get_running_loop().time()
                            continue
                        raise

                    self._last_send = asyncio.get_running_loop().time()
                    if not item.future.done():
                        item.future.set_result(resp)
                except asyncio.CancelledError:
                    if execute_task is not None and not execute_task.done():
                        execute_task.cancel()
                    _fail_item(item, ConnectionError("Commander stopped"))
                    raise
                except Exception as exc:
                    _fail_item(item, exc)
                finally:
                    if item.counts_bg_inflight and self._bg_inflight > 0:
                        self._bg_inflight -= 1
                    if (
                        item.key is not None
                        and self._pending_by_key.get(item.key) is item.future
                    ):
                        self._pending_by_key.pop(item.key, None)
                    self._queue.task_done()
        except asyncio.CancelledError:
            pass
