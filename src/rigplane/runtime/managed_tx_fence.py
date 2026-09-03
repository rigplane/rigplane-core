from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, field
from inspect import isawaitable
from typing import Any


Cancellation = Callable[[], Awaitable[None] | None]


@dataclass(frozen=True, slots=True, eq=False)
class TxAbortToken:
    epoch: int
    _fence: object = field(repr=False, compare=False)
    _number: int = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class TxAbortFailure:
    token: TxAbortToken
    error: BaseException


@dataclass(frozen=True, slots=True)
class TxAbortResult:
    epoch: int
    failures: tuple[TxAbortFailure, ...]


class TxAbortFence:
    def __init__(self) -> None:
        self._epoch = 0
        self._next_number = 0
        self._cancellations: dict[TxAbortToken, tuple[Cancellation, str | None]] = {}

    @property
    def epoch(self) -> int:
        return self._epoch

    def issue(self) -> TxAbortToken:
        token = TxAbortToken(self._epoch, self, self._next_number)
        self._next_number += 1
        return token

    def is_current(self, token: TxAbortToken) -> bool:
        return token._fence is self and token.epoch == self._epoch

    def register(
        self,
        token: TxAbortToken,
        cancellation: Cancellation,
        *,
        scope: str | None = None,
    ) -> None:
        if scope is not None and type(scope) is not str:
            raise TypeError("scope must be a built-in str or None")
        if not self.is_current(token):
            raise ValueError("cancellation requires a current token")
        if token in self._cancellations:
            raise ValueError("token is already registered")
        self._cancellations[token] = (cancellation, scope)

    def remove(self, token: TxAbortToken) -> bool:
        return self._cancellations.pop(token, None) is not None

    def force_off(self) -> Coroutine[Any, Any, TxAbortResult]:
        """Invalidate immediately; await the returned coroutine to finish cleanup."""
        self._epoch += 1
        cancellations = tuple(
            (token, cancellation)
            for token, (cancellation, _scope) in self._cancellations.items()
        )
        self._cancellations.clear()
        return self._cancel(self._epoch, cancellations)

    def cancel_scope(self, scope: str) -> Coroutine[Any, Any, TxAbortResult]:
        if type(scope) is not str:
            raise TypeError("scope must be a built-in str")
        cancellations = tuple(
            (token, cancellation)
            for token, (cancellation, registered_scope) in self._cancellations.items()
            if registered_scope == scope
        )
        for token, _cancellation in cancellations:
            del self._cancellations[token]
        return self._cancel(self._epoch, cancellations)

    @staticmethod
    async def _cancel(
        epoch: int, cancellations: tuple[tuple[TxAbortToken, Cancellation], ...]
    ) -> TxAbortResult:
        async def cancel_one(
            token: TxAbortToken, cancellation: Cancellation
        ) -> TxAbortFailure | None:
            try:
                result = cancellation()
                if isawaitable(result):
                    await result
            except BaseException as error:
                return TxAbortFailure(token, error)
            return None

        failures = await asyncio.gather(
            *(cancel_one(token, cancellation) for token, cancellation in cancellations)
        )
        return TxAbortResult(epoch, tuple(failure for failure in failures if failure))
