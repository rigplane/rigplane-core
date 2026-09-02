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
_Clock: TypeAlias = Callable[[], float]
_PoisonGeneration: TypeAlias = Callable[[int], Awaitable[None]]
_ON_OPERATIONS = frozenset({ActuationOperation.PTT_ON, ActuationOperation.TRANSMIT_ON})


@runtime_checkable
class ManagedTxActuator(Protocol):
    """Execute one runtime-tokened semantic operation."""

    async def actuate(
        self, token: EffectToken, operation: _Operation
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
    driver: asyncio.Task[None] | None = None
    provider: asyncio.Task[ActuationResult] | None = None
    started: bool = False


async def _ignore_poison(_: int) -> None:
    return None


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
        self._poison_generation = poison_generation or _ignore_poison
        self._lock = asyncio.Lock()
        self._on_lane = asyncio.Lock()
        self._claims: dict[_ClaimKey, _Claim] = {}
        self._poisoned_through_generation = -1

    async def settle(
        self, effect: ManagedTxEffect, *, deadline_monotonic: float
    ) -> ActuationSettled:
        outcome = await self._settle(effect.token, effect.operation, deadline_monotonic)
        return ActuationSettled(
            effect.token, effect.operation, outcome.result, outcome.error
        )

    async def settle_abort(
        self,
        token: EffectToken,
        operation: AbortOperation,
        *,
        deadline_monotonic: float,
    ) -> AbortFailed | None:
        outcome = await self._settle(token, operation, deadline_monotonic)
        if outcome.result is ActuationResult.ACCEPTED:
            return None
        return AbortFailed(token, operation, outcome.error or outcome.result.value)

    async def _settle(
        self, token: EffectToken, operation: _Operation, deadline: float
    ) -> _Outcome:
        claim = await self._claim(token, operation, deadline)
        try:
            return await asyncio.shield(claim.result)
        except asyncio.CancelledError:
            return await self._cancel_waiter(claim)

    async def _claim(
        self, token: EffectToken, operation: _Operation, deadline: float
    ) -> _Claim:
        key = (token, operation)
        async with self._lock:
            if existing := self._claims.get(key):
                return existing
            loop = asyncio.get_running_loop()
            claim = _Claim(token, operation, deadline, loop.create_future())
            self._claims[key] = claim
            if operation is ActuationOperation.FORCE_RECEIVE:
                active_ons = tuple(
                    active
                    for active in self._claims.values()
                    if active is not claim and active.operation in _ON_OPERATIONS
                )
                token_order = (token.provider_generation, token.effect_epoch)
                if any(
                    (item.token.provider_generation, item.token.effect_epoch)
                    > token_order
                    for item in active_ons
                ):
                    self._claims.pop(key)
                    claim.result.set_result(
                        _Outcome(
                            ActuationResult.REJECTED, "stale attempt before dispatch"
                        )
                    )
                    return claim
                for active in active_ons:
                    self._displace_locked(active)
            claim.driver = asyncio.create_task(self._drive(claim))
            claim.driver.add_done_callback(self._harvest)
            return claim

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
            claim.started = True
            claim.provider = asyncio.create_task(
                self._actuator.actuate(claim.token, claim.operation)
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

    async def _finish(
        self, claim: _Claim, outcome: _Outcome, *, poison: bool = False
    ) -> None:
        async with self._lock:
            if claim.result.done():
                return
            self._claims.pop((claim.token, claim.operation), None)
            claim.result.set_result(outcome)
            if poison:
                self._poison_locked(claim.token.provider_generation)

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
    ) -> None:
        if claim.result.done():
            return
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
            self._poison_locked(claim.token.provider_generation)

    def _poison_locked(self, provider_generation: int) -> None:
        if provider_generation <= self._poisoned_through_generation:
            return
        self._poisoned_through_generation = provider_generation
        task = asyncio.ensure_future(self._poison_generation(provider_generation))
        task.add_done_callback(self._harvest)

    @staticmethod
    def _harvest(task: asyncio.Future[Any]) -> None:
        if not task.cancelled():
            task.exception()


def _error_text(error: BaseException) -> str:
    return str(error) or type(error).__name__
