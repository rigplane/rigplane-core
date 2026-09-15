"""Cancellation-safe serialization for urgent and ordinary exchanges."""

from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import IntEnum

__all__ = ["ExchangeTier", "PriorityExchangeGate"]


class ExchangeTier(IntEnum):
    """Strict transport admission order for one indivisible exchange."""

    FORCE_RELEASE = 0
    ABORT = 10
    URGENT = 20
    ORDINARY = 30


class PriorityExchangeGate:
    """Serialize one active exchange, preferring urgent queued waiters."""

    def __init__(self) -> None:
        self._active = False
        self._waiters: dict[ExchangeTier, deque[asyncio.Future[None]]] = {
            tier: deque() for tier in ExchangeTier
        }

    async def _admit(self, *, tier: ExchangeTier) -> None:
        if not self._active:
            self._active = True
            return
        queue = self._waiters[tier]
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
        for tier in ExchangeTier:
            queue = self._waiters[tier]
            while queue:
                waiter = queue.popleft()
                if not waiter.done():
                    waiter.set_result(None)
                    return
        self._active = False

    @asynccontextmanager
    async def exchange(
        self, *, tier: ExchangeTier = ExchangeTier.ORDINARY
    ) -> AsyncIterator[None]:
        """Hold the single exchange slot until the context exits."""

        await self._admit(tier=tier)
        try:
            yield
        finally:
            self._release()
