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


class LocalTxWorkRunner:
    """Register one caller-owned operation with an injected abort fence.

    The runner owns no fence, queue, authority, task, or lifecycle.  It only
    lends the operation a fence token and passes its live currency check down
    to the provider's transport write.
    """

    def __init__(self, abort_fence: TxAbortFence) -> None:
        self._abort_fence = abort_fence

    async def run(self, work: LocalTxWork[_T]) -> _T:
        task = asyncio.current_task()
        if task is None:  # pragma: no cover - an async call always has a task
            raise RuntimeError("local TX work requires an asyncio task")

        token = self._abort_fence.issue()

        def cancel() -> None:
            task.cancel()

        self._abort_fence.register(token, cancel)

        def is_current() -> bool:
            return self._abort_fence.is_current(token)

        try:
            if not is_current():
                raise asyncio.CancelledError
            return await work(is_current)
        finally:
            self._abort_fence.remove(token)
