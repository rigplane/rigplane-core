from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
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

logger = logging.getLogger(__name__)

TxService = Callable[[TxSafetySupervisor, TxTransition], Awaitable[None]]
ProviderRelease = Callable[[], Awaitable[None]]
_PttObserver = Callable[[ProviderPttObservation], None]
_ProviderLifecycleState = Literal["unbound", "bound", "invalidating"]


class ProviderTxLifecycle(Protocol):
    def _unbind_authoritative_ptt_observer(self) -> None: ...

    async def _request_authoritative_ptt_read(
        self, *, provider_generation: int, observer: _PttObserver
    ) -> None: ...

    def _capture_managed_tx_port(
        self, provider_generation: int, observer: _PttObserver
    ) -> bool: ...

    async def _write_managed_ptt(self, provider_generation: int, on: bool) -> None: ...

    async def _retire_managed_tx_port(self, provider_generation: int) -> None: ...


@dataclass(frozen=True, slots=True)
class _ManagedTxEffectHost:
    _clock: Clock
    write: Callable[[int, bool], Awaitable[None]]
    read: Callable[[int], Awaitable[None]]
    retire: Callable[[int], Awaitable[None]]


TxServiceFactory = Callable[[_ManagedTxEffectHost], TxService]
_ShutdownTask = asyncio.Task[tuple[TxTransition, BaseException | None]]


class ManagedRadioRuntime:
    def __init__(
        self,
        target_id: str,
        *,
        service_factory: TxServiceFactory,
        provider_lifecycle: ProviderTxLifecycle | None = None,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
        shutdown_timeout_seconds: float = 3.0,
        tick_interval_seconds: float = 0.25,
    ) -> None:
        if not all(
            0 < seconds < float("inf")
            for seconds in (shutdown_timeout_seconds, tick_interval_seconds)
        ):
            raise ValueError("shutdown timeout and tick interval must be positive")
        self.target_id, self._clock = target_id, clock or time.monotonic
        self._tx_safety = TxSafetySupervisor(clock=self._clock, id_factory=id_factory)
        self._provider_lifecycle, self._provider_generation = provider_lifecycle, 0
        self._bound_generation: int | None = None
        self._provider_state: _ProviderLifecycleState = "unbound"
        self._provider_observer = self._observe_ptt
        self._lifecycle_lock, self._lifecycle_change = asyncio.Lock(), asyncio.Lock()
        self._retirement_task: asyncio.Task[None] | None = None
        self._retirement_generation: int | None = None
        self._retirement_error: BaseException | None = None
        self._observation_version = 0
        self._shutdown_task: _ShutdownTask | None = None
        self._shutdown_pending, self._shutdown_timeout = False, shutdown_timeout_seconds
        self._tick_interval = tick_interval_seconds
        self._tick_task: asyncio.Task[None] | None = None
        self._effect_host = _ManagedTxEffectHost(
            self._clock, self._host_write, self._host_read, self._host_retire
        )
        self._service = service_factory(self._effect_host)

    @property
    def tx_snapshot(self) -> TxSafetySnapshot:
        return self._tx_safety.snapshot

    async def _service_effects(self, transition: TxTransition) -> None:
        if transition.effects:
            await self._service(self._tx_safety, transition)

    async def _tick_loop(self) -> None:
        """The production driver of ``tick``: max key-down plus the timed retry.

        Only ``_lifecycle_lock`` is taken, and on the normal path only around
        the reducer call: it is the lock that guards supervisor mutation, while
        ``_lifecycle_change`` guards provider identity, which a tick never
        changes. Waiting on the latter would park the watchdog behind a
        retirement — exactly when a keyed rig most needs it. The loop retires
        itself once the lease is gone so an idle target costs nothing and
        leaves no task behind.

        The ``finally`` below is the exception: when a cancel or a shutdown
        ends the loop it retires outside the lock, because the canceller may
        already hold it. Safe only because the two writes it makes have no
        ``await`` between them and the loop is single-threaded.

        The interval bounds retry latency rather than setting it: an OFF that
        failed comes due one ``retry_schedule_seconds[0]`` after the failure and
        is picked up by the next tick, so it lands within one interval of that.
        """
        try:
            while True:
                async with self._lifecycle_lock:
                    if self._shutdown_pending or self.tx_snapshot.lease_id is None:
                        self._retire_ticker()
                        return
                    transition = self._tx_safety.tick()
                try:
                    await self._service_effects(transition)
                except Exception:
                    logger.warning("managed TX tick service failed", exc_info=True)
                await asyncio.sleep(self._tick_interval)
        except asyncio.CancelledError:
            pass
        finally:
            self._retire_ticker()

    def _retire_ticker(self) -> None:
        """The loop owns its own slot, so no canceller can strand the watchdog.

        Withdrawing the drive with it is what keeps ``watchdog_enabled`` honest:
        clearing the slot alone still leaves the flag latched on by the last
        tick, advertising a watchdog that is no longer running.
        """
        if self._tick_task is asyncio.current_task():
            self._tick_task = None
            self._tx_safety.retire_driver()

    def _not_ready(self) -> TxTransition:
        return TxTransition(TxOutcome.NOT_READY, self.tx_snapshot)

    def _observe_ptt(self, observation: ProviderPttObservation) -> None:
        if (
            not self._shutdown_pending
            and self._provider_state == "bound"
            and observation.provider_generation
            == self._bound_generation
            == self._provider_generation
        ):
            transition = self._tx_safety.observe_ptt(
                replace(observation, observed_at_monotonic=self._clock())
            )
            if transition.outcome is TxOutcome.APPLIED:
                self._observation_version += 1

    def _host_is_current(self, provider_generation: int) -> bool:
        return self._provider_state == "bound" and (
            provider_generation == self._bound_generation == self._provider_generation
        )

    async def _host_write(self, provider_generation: int, on: bool) -> None:
        if (
            (lifecycle := self._provider_lifecycle) is None
            or (on and self._shutdown_pending)
            or not self._host_is_current(provider_generation)
        ):
            raise ConnectionError("managed TX effect host is stale")
        await lifecycle._write_managed_ptt(provider_generation, on)

    async def _host_read(self, provider_generation: int) -> None:
        lifecycle = self._provider_lifecycle
        if lifecycle is None or not self._host_is_current(provider_generation):
            raise ConnectionError("managed TX effect host is stale")
        await lifecycle._request_authoritative_ptt_read(
            provider_generation=provider_generation, observer=self._provider_observer
        )

    def _advance_provider(self) -> TxTransition:
        self._provider_generation += 1
        return self._tx_safety.replace_provider(self._provider_generation, ready=False)

    def _begin_retirement(self) -> asyncio.Task[None] | None:
        if self._provider_state == "invalidating":
            return self._retirement_task
        lifecycle, generation = self._provider_lifecycle, self._bound_generation
        self._bound_generation, self._provider_state = None, "invalidating"
        if lifecycle is None or generation is None:
            self._provider_state = "unbound"
            return None
        task = asyncio.create_task(lifecycle._retire_managed_tx_port(generation))
        self._retirement_task, self._retirement_generation = task, generation
        self._retirement_error = None
        return task

    async def _await_retirement(
        self, task: asyncio.Task[None] | None
    ) -> BaseException | None:
        if task is None:
            return None
        if task is self._retirement_task and self._retirement_error is not None:
            return self._retirement_error
        cancellation: BaseException | None = None
        while not task.done():
            try:
                await asyncio.shield(task)
            except BaseException as exc:
                if not task.cancelled():
                    cancellation = exc
        try:
            error = task.exception()
        except asyncio.CancelledError as exc:
            error = exc
        if error is not None:
            self._retirement_error = error
            return error
        self._retirement_task = self._retirement_generation = None
        self._provider_state = "unbound"
        return cancellation

    async def _host_retire(self, provider_generation: int) -> None:
        async with self._lifecycle_change:
            async with self._lifecycle_lock:
                if (
                    self._provider_state == "invalidating"
                    and provider_generation == self._retirement_generation
                ):
                    task = self._retirement_task
                else:
                    if not self._host_is_current(provider_generation):
                        raise ConnectionError("managed TX effect host is stale")
                    task = self._begin_retirement()
                    self._advance_provider()
            if error := await self._await_retirement(task):
                raise error

    async def replace_provider(self, *, ready: bool) -> TxTransition:
        async with self._lifecycle_change:
            async with self._lifecycle_lock:
                if self._shutdown_pending:
                    return self._not_ready()
                task, transition = self._begin_retirement(), self._advance_provider()
            error = await self._await_retirement(task)
            async with self._lifecycle_lock:
                if self._shutdown_pending:
                    return self._not_ready()
                if error is not None:
                    raise error
                lifecycle = self._provider_lifecycle
                if lifecycle is None:
                    return transition
                try:
                    captured = lifecycle._capture_managed_tx_port(
                        self._provider_generation, self._provider_observer
                    )
                except BaseException:
                    try:
                        lifecycle._unbind_authoritative_ptt_observer()
                    except BaseException:
                        pass
                    raise
                if captured:
                    self._bound_generation = self._provider_generation
                    self._provider_state = "bound"
                    if ready:
                        transition = self._tx_safety.set_provider_ready(
                            self._provider_generation, ready=True
                        )
        await self._service_effects(transition)
        return self._not_ready() if self._shutdown_pending else transition

    async def invalidate_provider(self, provider_generation: int) -> TxTransition:
        async with self._lifecycle_change:
            async with self._lifecycle_lock:
                if self._shutdown_pending:
                    return self._not_ready()
                if provider_generation != self._provider_generation:
                    return TxTransition(TxOutcome.STALE, self.tx_snapshot)
                task, transition = self._begin_retirement(), self._advance_provider()
            error = await self._await_retirement(task)
            async with self._lifecycle_lock:
                if self._shutdown_pending:
                    return self._not_ready()
            if error is not None:
                raise error
            return transition

    async def request_fresh_ptt(self) -> TxTransition:
        async with self._lifecycle_change:
            async with self._lifecycle_lock:
                generation = self._provider_generation
                if self._shutdown_pending or not self._host_is_current(generation):
                    return self._not_ready()
                version = self._observation_version
            error: BaseException | None = None
            try:
                await self._effect_host.read(generation)
            except BaseException as exc:
                error = exc
            async with self._lifecycle_lock:
                if self._shutdown_pending:
                    return self._not_ready()
                if not self._host_is_current(generation):
                    return self._not_ready()
                if error is None and self._observation_version != version:
                    return TxTransition(TxOutcome.APPLIED, self.tx_snapshot)
                retirement_task = self._begin_retirement()
                self._advance_provider()
            retirement_error = await self._await_retirement(retirement_task)
            async with self._lifecycle_lock:
                if self._shutdown_pending:
                    return self._not_ready()
            if retirement_error is not None:
                raise retirement_error
            if error is not None:
                raise error
            raise RuntimeError("provider returned no authoritative PTT")

    async def set_provider_ready(self, *, ready: bool) -> TxTransition:
        async with self._lifecycle_lock:
            if self._shutdown_pending or (
                ready and not self._host_is_current(self._provider_generation)
            ):
                return self._not_ready()
            transition = self._tx_safety.set_provider_ready(
                self._provider_generation, ready=ready
            )
        await self._service_effects(transition)
        async with self._lifecycle_lock:
            return self._not_ready() if self._shutdown_pending else transition

    async def request_on(self, owner: TxOwner) -> TxTransition:
        async with self._lifecycle_lock:
            if self._shutdown_pending or not self._host_is_current(
                self._provider_generation
            ):
                return self._not_ready()
            transition = self._tx_safety.request_on(owner)
            if self._tick_task is None and transition.snapshot.lease_id is not None:
                self._tick_task = asyncio.create_task(self._tick_loop())
        await self._service_effects(transition)
        return transition

    async def request_off(
        self,
        owner: TxOwner,
        lease_id: str,
        *,
        reason: TxReleaseReason = TxReleaseReason.OPERATOR_RELEASE,
    ) -> TxTransition:
        async with self._lifecycle_lock:
            if self._shutdown_pending:
                return TxTransition(TxOutcome.IDEMPOTENT, self.tx_snapshot)
            transition = self._tx_safety.request_off(owner, lease_id, reason=reason)
        await self._service_effects(transition)
        return transition

    async def release_owner(
        self, owner: TxOwner, *, reason: TxReleaseReason
    ) -> TxTransition:
        async with self._lifecycle_lock:
            if self._shutdown_pending:
                return TxTransition(TxOutcome.IDEMPOTENT, self.tx_snapshot)
            transition = self._tx_safety.release_owner(owner, reason=reason)
        await self._service_effects(transition)
        return transition

    async def shutdown(self, *, release_provider: ProviderRelease) -> TxTransition:
        async with self._lifecycle_lock:
            task = self._shutdown_task
            if task is None:
                self._shutdown_pending = True
                transition = self._tx_safety.emergency_release(
                    reason=TxReleaseReason.SERVER_SHUTDOWN
                )
                task = asyncio.create_task(
                    self._complete_shutdown(transition, release_provider)
                )
                self._shutdown_task = task
        transition, error = await asyncio.shield(task)
        if error is not None:
            raise error
        return transition

    async def _complete_shutdown(
        self, transition: TxTransition, release_provider: ProviderRelease
    ) -> tuple[TxTransition, BaseException | None]:
        error: BaseException | None = None
        if (ticker := self._tick_task) is not None:
            ticker.cancel()  # the loop clears the slot on its way out
            await asyncio.gather(ticker, return_exceptions=True)
        try:
            if transition.outcome is not TxOutcome.NOOP:
                await asyncio.wait_for(
                    self._service_effects(transition),
                    timeout=self._shutdown_timeout,
                )
        except TimeoutError:
            pass
        except BaseException as exc:
            error = exc
        async with self._lifecycle_change:
            async with self._lifecycle_lock:
                task = self._begin_retirement()
                self._advance_provider()
            retirement_error = await self._await_retirement(task)
            error = error or retirement_error
        try:
            await release_provider()
        except BaseException as exc:
            error = error or exc
        return transition, error
