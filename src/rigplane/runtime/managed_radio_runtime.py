from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from rigplane.core.tx_safety import (
    Clock,
    IdFactory,
    ProviderPttObservation,
    TxOutcome,
    TxOwner,
    TxReleaseReason,
    TxSafetySnapshot,
    TxSafetySupervisor,
    TxTransition,
)

TxService = Callable[[TxSafetySupervisor, TxTransition], Awaitable[None]]
ProviderRelease = Callable[[], Awaitable[None]]
PttObserver = Callable[[ProviderPttObservation], None]


@dataclass(frozen=True, slots=True)
class ProviderTxLifecycle:
    bind: Callable[[int, PttObserver], None]
    unbind: Callable[[], None]
    read_ptt: Callable[[], Awaitable[None]]


class ManagedRadioRuntime:
    def __init__(
        self,
        target_id: str,
        *,
        service: TxService,
        provider_lifecycle: ProviderTxLifecycle | None = None,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
        shutdown_timeout_seconds: float = 3.0,
    ) -> None:
        if not 0 < shutdown_timeout_seconds < float("inf"):
            raise ValueError("shutdown_timeout_seconds must be finite-positive")
        self.target_id = target_id
        self._tx_safety = TxSafetySupervisor(clock=clock, id_factory=id_factory)
        self._service = service
        self._provider_lifecycle = provider_lifecycle
        self._provider_generation = 0
        self._shutdown_timeout = shutdown_timeout_seconds

    @property
    def tx_snapshot(self) -> TxSafetySnapshot:
        return self._tx_safety.snapshot

    async def _service_effects(self, transition: TxTransition) -> None:
        if transition.effects:
            await self._service(self._tx_safety, transition)

    async def replace_provider(self, *, ready: bool) -> TxTransition:
        generation = self._provider_generation + 1
        if self._provider_lifecycle:
            self._provider_lifecycle.bind(generation, self._observe_ptt)
        self._provider_generation = generation
        transition = self._tx_safety.replace_provider(
            self._provider_generation, ready=ready
        )
        await self._service_effects(transition)
        return transition

    def _observe_ptt(self, observation: ProviderPttObservation) -> None:
        self._tx_safety.observe_ptt(observation)

    async def request_fresh_ptt(self, provider_generation: int) -> TxTransition:
        lifecycle = self._provider_lifecycle
        if lifecycle is None or provider_generation != self._provider_generation:
            return TxTransition(TxOutcome.STALE, self.tx_snapshot)
        await lifecycle.read_ptt()
        outcome = (
            TxOutcome.APPLIED
            if provider_generation == self._provider_generation
            else TxOutcome.STALE
        )
        return TxTransition(outcome, self.tx_snapshot)

    async def invalidate_provider(self, provider_generation: int) -> TxTransition:
        if provider_generation != self._provider_generation:
            return TxTransition(TxOutcome.STALE, self.tx_snapshot)
        if self._provider_lifecycle:
            self._provider_lifecycle.unbind()
        self._provider_generation += 1
        transition = self._tx_safety.replace_provider(
            self._provider_generation, ready=False
        )
        await self._service_effects(transition)
        return transition

    async def set_provider_ready(self, *, ready: bool) -> TxTransition:
        transition = self._tx_safety.set_provider_ready(
            self._provider_generation, ready=ready
        )
        await self._service_effects(transition)
        return transition

    async def request_on(self, owner: TxOwner) -> TxTransition:
        transition = self._tx_safety.request_on(owner)
        await self._service_effects(transition)
        return transition

    async def request_off(
        self,
        owner: TxOwner,
        lease_id: str,
        *,
        reason: TxReleaseReason = TxReleaseReason.OPERATOR_RELEASE,
    ) -> TxTransition:
        transition = self._tx_safety.request_off(owner, lease_id, reason=reason)
        await self._service_effects(transition)
        return transition

    async def release_owner(
        self, owner: TxOwner, *, reason: TxReleaseReason
    ) -> TxTransition:
        transition = self._tx_safety.release_owner(owner, reason=reason)
        await self._service_effects(transition)
        return transition

    async def shutdown(self, *, release_provider: ProviderRelease) -> TxTransition:
        transition = self._tx_safety.emergency_release(
            reason=TxReleaseReason.SERVER_SHUTDOWN
        )
        try:
            if transition.outcome is not TxOutcome.NOOP:
                await asyncio.wait_for(
                    self._service_effects(transition),
                    timeout=self._shutdown_timeout,
                )
        except TimeoutError:
            pass
        finally:
            try:
                if self._provider_lifecycle:
                    self._provider_lifecycle.unbind()
            finally:
                await release_provider()
        return transition
