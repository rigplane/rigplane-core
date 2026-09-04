from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar

from .managed_tx_fence import TxAbortFence


Currency = Callable[[], bool]
T = TypeVar("T")
LocalTxOperation = Callable[[Currency], Awaitable[T]]


class GuardedCwCapable(Protocol):
    async def send_cw_text(
        self, text: str, *, is_current: Currency | None = None
    ) -> None: ...


class GuardedTunerCapable(Protocol):
    async def set_tuner_status(
        self, value: int, *, is_current: Currency | None = None
    ) -> None: ...


async def _drain(task: asyncio.Task[object]) -> None:
    try:
        await task
    except asyncio.CancelledError:
        pass


class LocalTxWorkRunner:
    def __init__(self, fence: TxAbortFence) -> None:
        self._fence = fence

    async def run(self, operation: LocalTxOperation[T]) -> T:
        token = self._fence.issue()

        async def invoke() -> T:
            return await operation(lambda: self._fence.is_current(token))

        task = asyncio.create_task(invoke())

        def cancel_and_drain() -> Awaitable[None]:
            if not task.done():
                task.cancel()
            return _drain(task)

        self._fence.register(token, cancel_and_drain, scope=None)
        try:
            result = await task
            if not self._fence.is_current(token):
                raise asyncio.CancelledError
            return result
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
            await _drain(task)
            raise
        finally:
            self._fence.remove(token)
