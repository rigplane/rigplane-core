from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from rigplane.core.tx_safety import (
    Clock,
    IdFactory,
    TxOutcome,
    TxOwner,
    TxReleaseReason,
    TxSafetySupervisor,
    TxTransition,
)

TxService = Callable[[TxSafetySupervisor, TxTransition], Awaitable[None]]
ProviderRelease = Callable[[], Awaitable[None]]


class ManagedRadioRuntime:
    def __init__(
        self,
        target_id: str,
        *,
        service: TxService,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
        shutdown_timeout_seconds: float = 3.0,
    ) -> None:
        if not 0 < shutdown_timeout_seconds < float("inf"):
            raise ValueError("shutdown_timeout_seconds must be finite-positive")
        self.target_id = target_id
        self._tx_safety = TxSafetySupervisor(clock=clock, id_factory=id_factory)
        self._service = service
        self._provider_generation = 0
        self._shutdown_timeout = shutdown_timeout_seconds

    @property
    def tx_safety(self) -> TxSafetySupervisor:
        return self._tx_safety

    async def _service_effects(self, transition: TxTransition) -> None:
        if transition.effects:
            await self._service(self._tx_safety, transition)

    async def replace_provider(self, *, ready: bool) -> TxTransition:
        self._provider_generation += 1
        transition = self.tx_safety.replace_provider(
            self._provider_generation, ready=ready
        )
        await self._service_effects(transition)
        return transition

    async def set_provider_ready(self, *, ready: bool) -> TxTransition:
        transition = self.tx_safety.set_provider_ready(
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
        transition = self.tx_safety.emergency_release(
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
            await release_provider()
        return transition
