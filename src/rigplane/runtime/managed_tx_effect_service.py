from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from rigplane.core import tx_safety as tx
from rigplane.runtime.managed_radio_runtime import _ManagedTxEffectHost, TxService


@dataclass(slots=True)
class _Claim:
    effect: tx.ProviderAttempt
    task: asyncio.Task[None] | None = None
    done: asyncio.Event = field(default_factory=asyncio.Event)
    cancel_deadline: float | None = None
    retirement: asyncio.Task[None] | None = None
    retirement_deadline: float = 0.0
    lane: asyncio.Lock | None = None


class _Service:
    def __init__(self, host: _ManagedTxEffectHost) -> None:
        self._host, self._lock, self._lane = host, asyncio.Lock(), asyncio.Lock()
        self._claims: dict[tuple[int, str], _Claim] = {}

    async def __call__(
        self, supervisor: tx.TxSafetySupervisor, transition: tx.TxTransition
    ) -> None:
        queue = list(transition.effects)
        for effect in queue:
            if isinstance(effect, tx.CancelProviderAttempt):
                followup = await self._cancel(supervisor, effect)
            else:
                followup = await self._attempt(supervisor, effect)
            queue.extend(followup)

    async def _attempt(
        self, supervisor: tx.TxSafetySupervisor, effect: tx.ProviderAttempt
    ) -> tuple[tx.TxEffect, ...]:
        key = (effect.provider_generation, effect.id)
        async with self._lock:
            if key in self._claims or any(
                claim.effect.provider_generation > effect.provider_generation
                for claim in self._claims.values()
            ):
                return ()
            blockers = tuple(
                claim.done
                for claim in self._claims.values()
                if claim.effect.provider_generation != effect.provider_generation
                and not claim.done.is_set()
            )
            claim = self._claims[key] = _Claim(effect)
            claim.task = asyncio.create_task(self._run(claim, blockers))
        deadline = effect.started_at_monotonic + effect.timeout_seconds
        return await self._wait(supervisor, claim, deadline)

    async def _run(self, claim: _Claim, blockers: tuple[asyncio.Event, ...]) -> None:
        await asyncio.gather(*(blocker.wait() for blocker in blockers))
        async with self._lock:
            claim.lane = lane = self._lane
        async with lane:
            if claim.retirement is not None:
                return
            effect = claim.effect
            if effect.kind is tx.ProviderAttemptKind.READ_PTT:
                await self._host.read(effect.provider_generation)
            else:
                await self._host.write(
                    effect.provider_generation,
                    effect.kind is tx.ProviderAttemptKind.WRITE_ON,
                )

    async def _wait(
        self, supervisor: tx.TxSafetySupervisor, claim: _Claim, deadline: float
    ) -> tuple[tx.TxEffect, ...]:
        assert claim.task is not None
        done, _ = await asyncio.wait(
            (claim.task,), timeout=max(0.0, deadline - self._host._clock())
        )
        if not done:
            return await self._poison(claim)
        async with self._lock:
            if claim.retirement is not None or claim.done.is_set():
                return ()
            try:
                claim.task.result()
            except BaseException as exc:
                error: str | None = str(exc) or type(exc).__name__
            else:
                error = None
            transition = supervisor.settle_attempt(
                claim.effect.id,
                claim.effect.provider_generation,
                succeeded=error is None,
                error=error,
            )
            claim.done.set()
            return tuple(transition.effects)

    async def _cancel(
        self, supervisor: tx.TxSafetySupervisor, effect: tx.CancelProviderAttempt
    ) -> tuple[tx.TxEffect, ...]:
        async with self._lock:
            claim = self._claims.get((effect.provider_generation, effect.attempt_id))
            if claim is None or claim.done.is_set():
                return ()
            if claim.cancel_deadline is None:
                claim.cancel_deadline = effect.settlement_deadline_monotonic
                assert claim.task is not None
                claim.task.cancel()
            deadline = claim.cancel_deadline
        return await self._wait(supervisor, claim, deadline)

    async def _poison(self, claim: _Claim) -> tuple[tx.TxEffect, ...]:
        async with self._lock:
            if claim.done.is_set():
                return ()
            if claim.retirement is None:
                assert claim.task is not None
                claim.task.cancel()
                claim.task.add_done_callback(self._harvest)
                if claim.lane is self._lane:
                    self._lane = asyncio.Lock()
                claim.retirement = asyncio.create_task(self._retire(claim))
                claim.retirement_deadline = (
                    self._host._clock() + claim.effect.timeout_seconds
                )
                claim.retirement.add_done_callback(self._harvest)
            retirement = claim.retirement
        done, _ = await asyncio.wait(
            (retirement,),
            timeout=max(0.0, claim.retirement_deadline - self._host._clock()),
        )
        if done:
            await retirement
        return ()

    async def _retire(self, claim: _Claim) -> None:
        try:
            await self._host.retire(claim.effect.provider_generation)
        finally:
            async with self._lock:
                claim.done.set()

    @staticmethod
    def _harvest(task: asyncio.Task[object]) -> None:
        if not task.cancelled():
            task.exception()


def managed_tx_effect_service(host: _ManagedTxEffectHost) -> TxService:
    return _Service(host)
