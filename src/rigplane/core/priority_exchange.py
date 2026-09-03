"""Cancellation-safe serialization for urgent and ordinary exchanges."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

__all__ = ["PriorityExchangeGate"]


class PriorityExchangeGate:
    """Serialize one active exchange, preferring urgent queued waiters."""

    def __init__(self) -> None:
        self._active = False
        self._urgent_waiters: deque[asyncio.Future[None]] = deque()
        self._ordinary_waiters: deque[asyncio.Future[None]] = deque()

    async def _admit(self, *, urgent: bool) -> None:
        if not self._active:
            self._active = True
            return
        queue = self._urgent_waiters if urgent else self._ordinary_waiters
        waiter = asyncio.get_running_loop().create_future()
        queue.append(waiter)
        try:
            await waiter
        except asyncio.CancelledError:
            if not waiter.cancelled():
                self._release()
            elif waiter in queue:
                queue.remove(waiter)
            raise

    def _release(self) -> None:
        for queue in (self._urgent_waiters, self._ordinary_waiters):
            while queue:
                waiter = queue.popleft()
                if not waiter.done():
                    waiter.set_result(None)
                    return
        self._active = False

    @asynccontextmanager
    async def exchange(self, *, urgent: bool = False) -> AsyncIterator[None]:
        """Hold the single exchange slot until the context exits."""

        await self._admit(urgent=urgent)
        try:
            yield
        finally:
            self._release()
