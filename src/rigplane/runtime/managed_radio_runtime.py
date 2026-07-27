from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Literal, Protocol

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
_PttObserver = Callable[[ProviderPttObservation], None]
_ProviderLifecycleState = Literal["unbound", "bound", "invalidating"]


class ProviderTxLifecycle(Protocol):
    def _bind_authoritative_ptt_observer(
        self, *, provider_generation: int, observer: _PttObserver
    ) -> None: ...

    def _unbind_authoritative_ptt_observer(self) -> None: ...

    async def _request_authoritative_ptt_read(
        self, *, provider_generation: int, observer: _PttObserver
    ) -> None: ...


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
        self._bound_generation: int | None = None
        self._provider_state: _ProviderLifecycleState = "unbound"
        self._shutdown_timeout = shutdown_timeout_seconds

    @property
    def tx_snapshot(self) -> TxSafetySnapshot:
        return self._tx_safety.snapshot

    async def _service_effects(self, transition: TxTransition) -> None:
        if transition.effects:
            await self._service(self._tx_safety, transition)

    def _not_ready(self) -> TxTransition:
        return TxTransition(TxOutcome.NOT_READY, self.tx_snapshot)

    def _observe_ptt(self, observation: ProviderPttObservation) -> None:
        if (
            self._provider_state == "bound"
            and observation.provider_generation
            == self._bound_generation
            == self._provider_generation
        ):
            self._tx_safety.observe_ptt(observation)

    def _unbind(self) -> BaseException | None:
        lifecycle = self._provider_lifecycle
        if self._provider_state != "bound" or lifecycle is None:
            return None
        self._provider_state = "invalidating"
        self._bound_generation = None
        try:
            lifecycle._unbind_authoritative_ptt_observer()
        except BaseException as exc:
            return exc
        return None

    async def replace_provider(self, *, ready: bool) -> TxTransition:
        unbind_error = self._unbind()
        self._provider_generation += 1
        generation = self._provider_generation
        transition = self._tx_safety.replace_provider(generation, ready=False)
        self._provider_state = "unbound"
        if unbind_error is not None:
            raise unbind_error
        lifecycle = self._provider_lifecycle
        if lifecycle is None:
            return transition
        try:
            lifecycle._bind_authoritative_ptt_observer(
                provider_generation=generation, observer=self._observe_ptt
            )
        except BaseException:
            try:
                lifecycle._unbind_authoritative_ptt_observer()
            except BaseException:
                pass
            raise
        self._bound_generation = generation
        self._provider_state = "bound"
        if ready:
            transition = self._tx_safety.set_provider_ready(generation, ready=True)
        await self._service_effects(transition)
        return transition

    async def invalidate_provider(self, provider_generation: int) -> TxTransition:
        if provider_generation != self._provider_generation:
            return TxTransition(TxOutcome.STALE, self.tx_snapshot)
        unbind_error = self._unbind()
        self._provider_generation += 1
        transition = self._tx_safety.replace_provider(
            self._provider_generation, ready=False
        )
        self._provider_state = "unbound"
        if unbind_error is not None:
            raise unbind_error
        return transition

    async def set_provider_ready(self, *, ready: bool) -> TxTransition:
        if ready and (
            self._provider_state != "bound"
            or self._bound_generation != self._provider_generation
        ):
            return self._not_ready()
        transition = self._tx_safety.set_provider_ready(
            self._provider_generation, ready=ready
        )
        await self._service_effects(transition)
        return transition

    async def request_on(self, owner: TxOwner) -> TxTransition:
        if (
            self._provider_state != "bound"
            or self._bound_generation != self._provider_generation
        ):
            return self._not_ready()
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
            await release_provider()
        return transition
