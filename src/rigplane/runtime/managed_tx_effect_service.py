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
        self._barrier: tuple[int, str | None] = (-1, None)

    async def __call__(
        self, supervisor: tx.TxSafetySupervisor, transition: tx.TxTransition
    ) -> None:
        queue = list(transition.effects)
        for effect in queue:
            if isinstance(effect, tx.CancelProviderAttempt):
                queue.extend(await self._cancel(supervisor, effect))
            else:
                queue.extend(await self._attempt(supervisor, effect))

    async def _attempt(
        self, supervisor: tx.TxSafetySupervisor, effect: tx.ProviderAttempt
    ) -> tuple[tx.TxEffect, ...]:
        key = (effect.provider_generation, effect.id)
        async with self._lock:
            active = supervisor.snapshot.active_attempt
            if key in self._claims or key == self._barrier or active != effect:
                return ()
            if effect.provider_generation < self._barrier[0]:
                return ()
            self._barrier = (effect.provider_generation, None)
            blockers = tuple(claim.done for claim in self._claims.values())
            claim = self._claims[key] = _Claim(effect)
            claim.task = asyncio.create_task(self._run(claim, blockers))
        deadline = effect.started_at_monotonic + effect.timeout_seconds
        return await self._wait(supervisor, claim, deadline)

    async def _run(self, claim: _Claim, blockers: tuple[asyncio.Event, ...]) -> None:
        await asyncio.gather(*(blocker.wait() for blocker in blockers))
        async with self._lock:
            if claim.done.is_set():
                return
            claim.lane = lane = self._lane
        async with lane:
            if claim.done.is_set() or claim.retirement is not None:
                return
            effect = claim.effect
            if effect.kind is tx.ProviderAttemptKind.READ_PTT:
                await self._host.read(effect.provider_generation)
            else:
                on = effect.kind is tx.ProviderAttemptKind.WRITE_ON
                await self._host.write(effect.provider_generation, on)

    async def _wait(
        self, supervisor: tx.TxSafetySupervisor, claim: _Claim, deadline: float
    ) -> tuple[tx.TxEffect, ...]:
        if (task := claim.task) is None:
            return ()
        done, _ = await asyncio.wait(
            (task,), timeout=max(0.0, deadline - self._host._clock())
        )
        if not done:
            return await self._poison(claim)
        async with self._lock:
            if claim.retirement is not None or claim.done.is_set():
                return ()
            exc = asyncio.CancelledError() if task.cancelled() else task.exception()
            error = None if exc is None else str(exc) or type(exc).__name__
            transition = supervisor.settle_attempt(
                claim.effect.id,
                claim.effect.provider_generation,
                succeeded=error is None,
                error=error,
            )
            self._finish(claim)
            return tuple(transition.effects)

    async def _cancel(
        self, supervisor: tx.TxSafetySupervisor, effect: tx.CancelProviderAttempt
    ) -> tuple[tx.TxEffect, ...]:
        key = (effect.provider_generation, effect.attempt_id)
        async with self._lock:
            claim = self._claims.get(key)
            if claim is None:
                active = supervisor.snapshot.active_attempt
                if active is not None:
                    active_key = (active.provider_generation, active.id)
                    if active_key == key and key[0] >= self._barrier[0]:
                        self._barrier = key
                        end = supervisor.settle_attempt
                        done: tx.TxTransition = end(active.id, key[0], succeeded=False)
                        return tuple(done.effects)
                return ()
            if claim.cancel_deadline is None:
                claim.cancel_deadline = effect.settlement_deadline_monotonic
                assert claim.task is not None
                claim.task.cancel()
        return await self._wait(supervisor, claim, claim.cancel_deadline)

    async def _poison(self, claim: _Claim) -> tuple[tx.TxEffect, ...]:
        clock = self._host._clock
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
                claim.retirement_deadline = clock() + claim.effect.timeout_seconds
                claim.retirement.add_done_callback(self._harvest)
            retirement = claim.retirement
        timeout = max(0.0, claim.retirement_deadline - clock())
        done, _ = await asyncio.wait((retirement,), timeout=timeout)
        if done:
            await retirement
        return ()

    async def _retire(self, claim: _Claim) -> None:
        try:
            await self._host.retire(claim.effect.provider_generation)
        finally:
            async with self._lock:
                self._finish(claim)

    def _finish(self, claim: _Claim) -> None:
        self._claims.pop((claim.effect.provider_generation, claim.effect.id), None)
        claim.done.set()
        claim.task = claim.retirement = claim.lane = None

    def _harvest(self, task: asyncio.Task[object]) -> None:
        if not task.cancelled():
            task.exception()


def managed_tx_effect_service(host: _ManagedTxEffectHost) -> TxService:
    return _Service(host)
