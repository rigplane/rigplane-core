"""Minimal bounded async queue helper used by inter-task buffers.

Asyncio-only. Single-event-loop semantics; no threading, no thread-safety
locks — relies on the GIL for ordering of single-step operations the way
``asyncio.Queue`` does. The wrapper is a thin facade over ``asyncio.Queue``
with one shared idiom extracted: :meth:`put_drop_oldest`, which sites 2 & 3
in radio.py used as a 5-line ``if full: get_nowait(); put_nowait(item)``.

Drop policy is *caller-managed*: ``put_nowait`` raises ``asyncio.QueueFull``
on overflow exactly like the underlying queue, so each callsite keeps its
own logging / metrics / fast-path branching. This module deliberately does
not introduce a ``drop_policy`` parameter — log messages and severities
differ across callsites and forcing a single policy would erase that.
"""

from __future__ import annotations

import asyncio
from typing import Generic, TypeVar

T = TypeVar("T")


class BoundedQueue(Generic[T]):
    """Thin asyncio bounded queue with a drop-oldest helper.

    Exposes the subset of ``asyncio.Queue`` operations actually used by the
    four migrated callsites (transport RX, radio scope/civ-event, web
    fanout). All operations are single-event-loop only.
    """

    __slots__ = ("_queue",)

    def __init__(self, maxsize: int) -> None:
        self._queue: asyncio.Queue[T] = asyncio.Queue(maxsize=maxsize)

    async def get(self) -> T:
        """Await and return the next item."""
        return await self._queue.get()

    def get_nowait(self) -> T:
        """Return next item or raise :class:`asyncio.QueueEmpty`."""
        return self._queue.get_nowait()

    async def put(self, item: T) -> None:
        """Await space and put ``item``."""
        await self._queue.put(item)

    def put_nowait(self, item: T) -> None:
        """Put ``item`` or raise :class:`asyncio.QueueFull` when full."""
        self._queue.put_nowait(item)

    def put_drop_oldest(self, item: T) -> None:
        """Put ``item``, evicting the oldest entry if the queue is full.

        Encapsulates the ``if full: get_nowait(); put_nowait(item)`` recipe
        used by the radio scope-frame and CI-V event publishers. Eviction
        is silent — callers that need to log/count drops should check
        :meth:`full` themselves before calling this.
        """
        if self._queue.full():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        self._queue.put_nowait(item)

    def full(self) -> bool:
        """Return ``True`` if the queue has reached ``maxsize``."""
        return self._queue.full()

    def empty(self) -> bool:
        """Return ``True`` if the queue currently holds no items."""
        return self._queue.empty()

    def qsize(self) -> int:
        """Return the current number of queued items."""
        return self._queue.qsize()

    def task_done(self) -> None:
        """Mark a previously-fetched task as done (mirrors ``asyncio.Queue``)."""
        self._queue.task_done()

    @property
    def maxsize(self) -> int:
        """Return the configured maximum size."""
        return self._queue.maxsize
