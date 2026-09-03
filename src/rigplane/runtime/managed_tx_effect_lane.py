"""Provider-neutral execution lane for managed-transmit effects."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol, TypeAlias, runtime_checkable

from rigplane.runtime.managed_tx_state import (
    AbortFailed,
    AbortOperation,
    ActuationOperation,
    ActuationResult,
    ActuationSettled,
    EffectToken,
    ManagedTxEffect,
)

_Operation: TypeAlias = ActuationOperation | AbortOperation
_ClaimKey: TypeAlias = tuple[EffectToken, _Operation]
_Slot: TypeAlias = str | AbortOperation
_Clock: TypeAlias = Callable[[], float]
_PoisonGeneration: TypeAlias = Callable[[int], Awaitable[None]]
_ON_OPERATIONS = frozenset({ActuationOperation.PTT_ON, ActuationOperation.TRANSMIT_ON})
_ON_SLOT = "on"
_FORCE_SLOT = "force_receive"


@runtime_checkable
class ManagedTxActuator(Protocol):
    """Execute one runtime-tokened semantic operation."""

    async def actuate(
        self,
        token: EffectToken,
        operation: _Operation,
        *,
        is_current: Callable[[], bool],
    ) -> ActuationResult: ...


@dataclass(frozen=True, slots=True)
class _Outcome:
    result: ActuationResult
    error: str | None


@dataclass(slots=True)
class _Claim:
    token: EffectToken
    operation: _Operation
    deadline: float
    result: asyncio.Future[_Outcome]
    is_current: Callable[[], bool] | None = None
    driver: asyncio.Task[None] | None = None
    provider: asyncio.Task[ActuationResult] | None = None
    started: bool = False
    isolation: tuple[asyncio.Task[None], ...] = ()


class ManagedTxEffectLane:
    """Claim, prioritize, and normalize managed actuator attempts."""

    def __init__(
        self,
        actuator: ManagedTxActuator,
        *,
        clock: _Clock | None = None,
        poison_generation: _PoisonGeneration | None = None,
    ) -> None:
        self._actuator = actuator
        self._clock = clock or time.monotonic
        self._poison_generation = poison_generation
        self._lock = asyncio.Lock()
        self._on_lane = asyncio.Lock()
        self._claims: dict[_ClaimKey, _Claim] = {}
        self._slots: dict[_Slot, tuple[EffectToken, _Operation]] = {}
        self._scope = (-1, -1)
        self._release_token: EffectToken | None = None
        self._isolation: tuple[int, asyncio.Task[None]] | None = None

    async def settle(
        self,
        effect: ManagedTxEffect,
        *,
        deadline_monotonic: float,
        is_current: Callable[[], bool] | None = None,
    ) -> ActuationSettled | None:
        outcome = await self._settle(
            effect.token, effect.operation, deadline_monotonic, is_current
        )
        if outcome is None:
            return None
        return ActuationSettled(
            effect.token, effect.operation, outcome.result, outcome.error
        )

    async def settle_abort(
        self,
        token: EffectToken,
        operation: AbortOperation,
        *,
        deadline_monotonic: float,
        is_current: Callable[[], bool] | None = None,
    ) -> AbortFailed | None:
        outcome = await self._settle(token, operation, deadline_monotonic, is_current)
        if outcome is None:
            return None
        if outcome.result is ActuationResult.ACCEPTED:
            return None
        return AbortFailed(token, operation, outcome.error or outcome.result.value)

    async def _settle(
        self,
        token: EffectToken,
        operation: _Operation,
        deadline: float,
        is_current: Callable[[], bool] | None,
    ) -> _Outcome | None:
        claim = await self._claim(token, operation, deadline, is_current)
        if claim is None:
            return None
        try:
            return await asyncio.shield(claim.result)
        except asyncio.CancelledError:
            return await self._cancel_waiter(claim)

    async def _claim(
        self,
        token: EffectToken,
        operation: _Operation,
        deadline: float,
        is_current: Callable[[], bool] | None,
    ) -> _Claim | None:
        async with self._lock:
            scope = (token.provider_generation, token.effect_epoch)
            if scope < self._scope:
                return None
            if scope > self._scope:
                self._scope = scope
                self._slots.clear()
                self._release_token = None
                for active in tuple(self._claims.values()):
                    active_scope = (
                        active.token.provider_generation,
                        active.token.effect_epoch,
                    )
                    if active_scope < scope:
                        self._displace_locked(active)
            slot = _slot(operation)
            if slot in self._slots or not self._bind_family_locked(token, operation):
                return None
            self._slots[slot] = (token, operation)
            loop = asyncio.get_running_loop()
            claim = _Claim(
                token, operation, deadline, loop.create_future(), is_current
            )
            self._claims[(token, operation)] = claim
            if operation is ActuationOperation.FORCE_RECEIVE:
                active_ons = tuple(
                    active
                    for active in self._claims.values()
                    if active is not claim and active.operation in _ON_OPERATIONS
                )
                for active in active_ons:
                    if barrier := self._displace_locked(active):
                        claim.isolation += (barrier,)
                if self._isolation is not None:
                    generation, task = self._isolation
                    if (
                        generation <= token.provider_generation
                        and task not in claim.isolation
                    ):
                        claim.isolation += (task,)
            claim.driver = asyncio.create_task(self._drive(claim))
            claim.driver.add_done_callback(self._harvest)
            return claim

    def _bind_family_locked(self, token: EffectToken, operation: _Operation) -> bool:
        if operation in _ON_OPERATIONS:
            return self._release_token is None
        if (
            operation is ActuationOperation.FORCE_RECEIVE
            and (primary := self._slots.get(_ON_SLOT))
            and primary[0] == token
        ):
            return False
        if self._release_token is None:
            if _ON_SLOT in self._slots and isinstance(operation, AbortOperation):
                return False
            self._release_token = token
        return bool(self._release_token == token)

    async def _drive(self, claim: _Claim) -> None:
        try:
            if claim.operation in _ON_OPERATIONS:
                async with self._on_lane:
                    await self._invoke(claim)
            else:
                await self._invoke(claim)
        except asyncio.CancelledError:
            pass

    async def _invoke(self, claim: _Claim) -> None:
        if claim.result.done():
            return
        if claim.deadline <= self._clock():
            await self._finish(
                claim,
                _Outcome(ActuationResult.REJECTED, "deadline expired before dispatch"),
            )
            return
        async with self._lock:
            if claim.result.done():
                return
            isolation_pending = any(not task.done() for task in claim.isolation)
            claim.started = True
            claim.provider = asyncio.create_task(
                self._actuator.actuate(
                    claim.token,
                    claim.operation,
                    is_current=lambda: self._is_current(claim),
                )
            )
            provider = claim.provider
        done, _ = await asyncio.wait(
            (provider,), timeout=max(0.0, claim.deadline - self._clock())
        )
        if not done:
            provider.cancel()
            provider.add_done_callback(self._harvest)
            await self._finish(
                claim,
                _Outcome(ActuationResult.UNCERTAIN, "attempt deadline expired"),
                poison=claim.operation in _ON_OPERATIONS,
            )
            return
        try:
            result = provider.result()
            if not isinstance(result, ActuationResult):
                raise TypeError("actuator returned a non-normalized result")
        except BaseException as error:
            await self._finish(
                claim,
                _Outcome(ActuationResult.UNCERTAIN, _error_text(error)),
                poison=claim.operation in _ON_OPERATIONS,
            )
            return
        if result is ActuationResult.ACCEPTED and claim.isolation:
            isolation_error = await self._await_isolation(
                claim.isolation, claim.deadline
            )
            if isolation_error is None and isolation_pending:
                isolation_error = "release preceded ON isolation"
            if isolation_error:
                result = ActuationResult.UNCERTAIN
                await self._finish(claim, _Outcome(result, isolation_error))
                return
        await self._finish(
            claim,
            _Outcome(
                result,
                None if result is ActuationResult.ACCEPTED else result.value,
            ),
            poison=(
                claim.operation in _ON_OPERATIONS
                and result is ActuationResult.UNCERTAIN
            ),
        )

    def _is_current(self, claim: _Claim) -> bool:
        return (
            self._claims.get((claim.token, claim.operation)) is claim
            and not claim.result.done()
            and self._clock() < claim.deadline
            and (claim.is_current is None or claim.is_current())
        )

    async def _finish(
        self, claim: _Claim, outcome: _Outcome, *, poison: bool = False
    ) -> None:
        isolation: asyncio.Task[None] | None = None
        async with self._lock:
            if claim.result.done():
                return
            self._claims.pop((claim.token, claim.operation), None)
            if poison:
                isolation = self._isolate_locked(claim)
        if isolation is not None:
            await self._await_isolation((isolation,), claim.deadline)
        async with self._lock:
            if claim.result.done():
                return
            claim.result.set_result(outcome)

    async def _cancel_waiter(self, claim: _Claim) -> _Outcome:
        async with self._lock:
            if not claim.result.done():
                self._displace_locked(
                    claim,
                    before="attempt cancelled before dispatch",
                    after="attempt cancelled after dispatch",
                )
            return claim.result.result()

    def _displace_locked(
        self,
        claim: _Claim,
        *,
        before: str = "superseded before dispatch",
        after: str = "superseded after dispatch",
    ) -> asyncio.Task[None] | None:
        if claim.result.done():
            return None
        self._claims.pop((claim.token, claim.operation), None)
        if claim.provider is not None:
            claim.provider.cancel()
            claim.provider.add_done_callback(self._harvest)
        if claim.driver is not None:
            claim.driver.cancel()
        result = (
            ActuationResult.UNCERTAIN if claim.started else ActuationResult.REJECTED
        )
        claim.result.set_result(_Outcome(result, after if claim.started else before))
        if claim.started and claim.operation in _ON_OPERATIONS:
            return self._isolate_locked(claim)
        return None

    def _isolate_locked(self, claim: _Claim) -> asyncio.Task[None]:
        provider_generation = claim.token.provider_generation
        if (
            self._poison_generation is not None
            and self._isolation is not None
            and self._isolation[0] == provider_generation
        ):
            return self._isolation[1]
        previous = None if self._isolation is None else self._isolation[1]
        task = asyncio.create_task(self._isolate_provider(claim, previous))
        task.add_done_callback(self._harvest)
        self._isolation = (provider_generation, task)
        return task

    async def _isolate_provider(
        self, claim: _Claim, previous: asyncio.Task[None] | None
    ) -> None:
        if self._poison_generation is not None:
            await self._poison_generation(claim.token.provider_generation)
        else:
            assert claim.provider is not None
            pending: list[asyncio.Future[Any]] = [asyncio.shield(claim.provider)]
            if previous is not None:
                pending.append(asyncio.shield(previous))
            await asyncio.gather(*pending, return_exceptions=True)

    async def _await_isolation(
        self, tasks: tuple[asyncio.Task[None], ...], deadline: float
    ) -> str | None:
        done, pending = await asyncio.wait(
            tasks, timeout=max(0.0, deadline - self._clock())
        )
        if pending:
            return "isolation deadline expired"
        for task in done:
            if task.cancelled():
                return "CancelledError"
            if error := task.exception():
                return _error_text(error)
        return None

    @staticmethod
    def _harvest(task: asyncio.Future[Any]) -> None:
        if not task.cancelled():
            task.exception()


def _error_text(error: BaseException) -> str:
    return str(error) or type(error).__name__


def _slot(operation: _Operation) -> _Slot:
    if operation in _ON_OPERATIONS:
        return _ON_SLOT
    if operation is ActuationOperation.FORCE_RECEIVE:
        return _FORCE_SLOT
    return operation
