"""Abort-fenced execution for local transmit-adjacent provider work."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

from .managed_tx_fence import TxAbortFence

__all__ = ["LocalTxWorkRunner"]


_T = TypeVar("_T")
CurrencyCheck = Callable[[], bool]
LocalTxWork = Callable[[CurrencyCheck], Awaitable[_T]]


async def _drain(task: asyncio.Task[object]) -> None:
    await asyncio.gather(task, return_exceptions=True)


class LocalTxWorkRunner:
    """Register one caller-owned operation with an injected abort fence.

    The runner owns no fence, queue, authority, or lifecycle.  It owns and
    drains the provider task for one call while lending it a fence token and
    live currency check.
    """

    def __init__(self, abort_fence: TxAbortFence) -> None:
        self._abort_fence = abort_fence

    async def run(self, work: LocalTxWork[_T]) -> _T:
        token = self._abort_fence.issue()

        async def invoke() -> _T:
            return await work(lambda: self._abort_fence.is_current(token))

        task = asyncio.create_task(invoke())

        def cancel_and_drain() -> Awaitable[None]:
            if not task.done():
                task.cancel()
            return _drain(task)

        self._abort_fence.register(token, cancel_and_drain)

        try:
            result = await task
            if not self._abort_fence.is_current(token):
                raise asyncio.CancelledError
            return result
        except asyncio.CancelledError:
            if not task.done():
                task.cancel()
            await _drain(task)
            raise
        finally:
            self._abort_fence.remove(token)
