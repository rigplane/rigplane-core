from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

from rigplane.core.tx_safety import (
    Clock,
    IdFactory,
    TxOutcome,
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
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
        shutdown_timeout_seconds: float = 3.0,
    ) -> None:
        if not 0 < shutdown_timeout_seconds < float("inf"):
            raise ValueError("shutdown_timeout_seconds must be finite-positive")
        self.target_id = target_id
        self._tx_safety = TxSafetySupervisor(clock=clock, id_factory=id_factory)
        self._provider_generation = 0
        self._shutdown_timeout = shutdown_timeout_seconds

    @property
    def tx_safety(self) -> TxSafetySupervisor:
        return self._tx_safety

    async def replace_provider(
        self, *, ready: bool, service: TxService
    ) -> TxTransition:
        self._provider_generation += 1
        transition = self.tx_safety.replace_provider(
            self._provider_generation, ready=ready
        )
        if transition.effects:
            await service(self.tx_safety, transition)
        return transition

    async def set_provider_ready(
        self, *, ready: bool, service: TxService
    ) -> TxTransition:
        transition = self.tx_safety.set_provider_ready(
            self._provider_generation, ready=ready
        )
        if transition.effects:
            await service(self.tx_safety, transition)
        return transition

    async def shutdown(
        self, *, dekey: TxService, release_provider: ProviderRelease
    ) -> TxTransition:
        transition = self.tx_safety.emergency_release(
            reason=TxReleaseReason.SERVER_SHUTDOWN
        )
        try:
            if transition.outcome is not TxOutcome.NOOP:
                await asyncio.wait_for(
                    dekey(self.tx_safety, transition),
                    timeout=self._shutdown_timeout,
                )
        except TimeoutError:
            pass
        finally:
            await release_provider()
        return transition
