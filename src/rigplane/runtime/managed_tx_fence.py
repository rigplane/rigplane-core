from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from inspect import isawaitable


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
        self._cancellations: dict[TxAbortToken, Cancellation] = {}

    @property
    def epoch(self) -> int:
        return self._epoch

    def issue(self) -> TxAbortToken:
        token = TxAbortToken(self._epoch, self, self._next_number)
        self._next_number += 1
        return token

    def is_current(self, token: TxAbortToken) -> bool:
        return token._fence is self and token.epoch == self._epoch

    def register(self, token: TxAbortToken, cancellation: Cancellation) -> None:
        if not self.is_current(token):
            raise ValueError("cancellation requires a current token")
        if token in self._cancellations:
            raise ValueError("token is already registered")
        self._cancellations[token] = cancellation

    def remove(self, token: TxAbortToken) -> bool:
        return self._cancellations.pop(token, None) is not None

    async def force_off(self) -> TxAbortResult:
        cancellations = tuple(self._cancellations.items())
        self._cancellations.clear()
        failures: list[TxAbortFailure] = []
        for token, cancellation in cancellations:
            try:
                result = cancellation()
                if isawaitable(result):
                    await result
            except BaseException as error:
                failures.append(TxAbortFailure(token, error))
        self._epoch += 1
        return TxAbortResult(self._epoch, tuple(failures))
