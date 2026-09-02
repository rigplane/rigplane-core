from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Protocol

from rigplane.runtime.managed_tx_config import (
    ManagedTxTotConfig,
    ManagedTxTotConfigStore,
)
from rigplane.runtime.managed_tx_effect_lane import ManagedTxEffectLane
from rigplane.runtime.managed_tx_fence import TxAbortFence
from rigplane.runtime.managed_tx_state import (
    AbortOperation,
    ForceOff,
    ManagedTxEffect,
    ManagedTxEvent,
    ManagedTxIntent,
    ManagedTxIntentKind,
    ManagedTxOutcome,
    ManagedTxState,
    ManagedTxTransition,
    PttDown,
    PttUp,
    RetryForceReceive,
    TransmitOn,
    reduce_managed_tx,
)


class _Wakeup(Protocol):
    async def wait_until(self, deadline: float | None) -> None: ...

    def wake(self) -> None: ...


class _EventWakeup:
    def __init__(self, clock: Callable[[], float]) -> None:
        self._clock = clock
        self._event = asyncio.Event()

    async def wait_until(self, deadline: float | None) -> None:
        event = self._event
        if deadline is None:
            await event.wait()
        else:
            timeout = max(0.0, deadline - self._clock())
            if timeout:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(event.wait(), timeout)
        if self._event is event:
            self._event = asyncio.Event()

    def wake(self) -> None:
        self._event.set()


@dataclass(frozen=True, slots=True)
class ManagedTxProjection:
    state: ManagedTxState
    configured_tot_seconds: float | None
    remaining_tot_seconds: float | None
    provider_generation: int | None


class ManagedTxAuthority:
    def __init__(
        self,
        lane: ManagedTxEffectLane,
        config_store: ManagedTxTotConfigStore,
        abort_fence: TxAbortFence,
        *,
        provider_generation: int | None,
        clock: Callable[[], float] | None = None,
        wakeup: _Wakeup | None = None,
        attempt_timeout_seconds: float = 3.0,
        retry_delay_seconds: float = 1.0,
    ) -> None:
        if not 0 < attempt_timeout_seconds < float("inf") or not 0 < (
            retry_delay_seconds
        ) < float("inf"):
            raise ValueError("managed TX timeouts must be positive")
        if provider_generation is not None and provider_generation < 0:
            raise ValueError("provider generation must be non-negative")
        self._lane = lane
        self._config_store = config_store
        self._abort_fence = abort_fence
        self._clock = clock or time.monotonic
        self._wakeup = wakeup or _EventWakeup(self._clock)
        self._attempt_timeout = attempt_timeout_seconds
        self._retry_delay = retry_delay_seconds
        self._lock = asyncio.Lock()
        self._state = ManagedTxState()
        self._provider_generation = provider_generation
        self._generation_high_water = (
            provider_generation if provider_generation is not None else -1
        )
        self._attempt = 0
        self._retry_due: float | None = None
        self._closing = False
        self._closed = False
        self._scheduler_task = asyncio.create_task(self._scheduler())

    async def ptt_down(self, owner: str) -> ManagedTxOutcome:
        return await self._ingress("ptt_down", owner)

    async def ptt_up(self, owner: str) -> ManagedTxOutcome:
        return await self._ingress("ptt_up", owner)

    async def transmit_on(self) -> ManagedTxOutcome:
        return await self._ingress("transmit_on")

    async def force_off(self) -> ManagedTxOutcome:
        return await self._ingress("force_off")

    async def owner_disconnect(self, owner: str) -> ManagedTxOutcome:
        async with self._lock:
            self._require_open_locked()
            if self._state.intent != ManagedTxIntent.ptt(owner):
                return ManagedTxOutcome.REJECTED
            transition = self._force_off_locked()
            self._wakeup.wake()
        await self._execute(transition.effects, full_force=True)
        return transition.outcome

    async def get_tot_config(self) -> ManagedTxTotConfig:
        async with self._lock:
            return self._config_store.config

    async def set_tot_seconds(self, value: object) -> ManagedTxTotConfig:
        transition = None
        async with self._lock:
            self._require_open_locked()
            config = self._config_store.set_timeout_seconds(value)
            deadline = self._tot_deadline_locked(config.timeout_seconds)
            if deadline is not None and deadline <= self._clock():
                transition = self._force_off_locked()
            self._wakeup.wake()
        if transition is not None:
            await self._execute(transition.effects, full_force=True)
        return config

    async def snapshot(self) -> ManagedTxProjection:
        async with self._lock:
            deadline = self._tot_deadline_locked()
            state = replace(self._state, tot_deadline_monotonic=deadline)
            remaining = None if deadline is None else max(0.0, deadline - self._clock())
            return ManagedTxProjection(
                state,
                self._config_store.config.timeout_seconds,
                remaining,
                self._provider_generation,
            )

    async def set_provider_generation(self, generation: int | None) -> None:
        async with self._lock:
            self._require_open_locked()
            if generation is not None:
                if generation <= self._generation_high_water:
                    if generation == self._provider_generation:
                        return
                    raise ValueError("provider generation must increase")
                self._generation_high_water = generation
            self._provider_generation = generation
            if generation is None:
                self._retry_due = None
            elif self._release_is_retryable_locked():
                self._retry_due = self._clock()
            self._wakeup.wake()

    async def close(self) -> None:
        async with self._lock:
            if self._closed or self._closing:
                return
            if (
                self._state.intent.kind is not ManagedTxIntentKind.RX
                or self._state.release_required
                or self._state.pending_effect is not None
            ):
                raise RuntimeError("managed TX disposal requires clean RX state")
            self._closing = True
            scheduler = self._scheduler_task
        scheduler.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scheduler
        async with self._lock:
            self._closed = True
            self._closing = False

    async def _ingress(self, action: str, owner: str | None = None) -> ManagedTxOutcome:
        async with self._lock:
            self._require_open_locked()
            transition, full_force = self._transition_locked(action, owner)
            self._wakeup.wake()
        if transition is None:
            return ManagedTxOutcome.REJECTED
        await self._execute(transition.effects, full_force=full_force)
        return transition.outcome

    def _transition_locked(
        self, action: str, owner: str | None
    ) -> tuple[ManagedTxTransition | None, bool]:
        generation = self._provider_generation
        if action == "force_off":
            return self._force_off_locked(), True
        if owner is not None and self._state.intent == ManagedTxIntent.ptt(owner):
            if action == "ptt_down":
                return self._reduce_locked(
                    PttDown(owner, generation or 0, self._attempt_id_locked(), 0, None)
                ), False
            if action == "ptt_up" and generation is None:
                return self._force_off_locked(), True
        if (
            action == "transmit_on"
            and self._state.intent.kind is ManagedTxIntentKind.TRANSMIT
        ):
            return self._reduce_locked(
                TransmitOn(
                    generation or max(0, self._generation_high_water),
                    self._attempt_id_locked(),
                    0,
                    None,
                )
            ), False
        if generation is None:
            return None, False
        now = self._clock()
        attempt = self._attempt_id_locked()
        if action == "ptt_down":
            assert owner is not None
            event: ManagedTxEvent = PttDown(
                owner, generation, attempt, now, self._deadline_from(now)
            )
        elif action == "ptt_up":
            assert owner is not None
            event = PttUp(owner, generation, attempt)
        else:
            event = TransmitOn(generation, attempt, now, self._deadline_from(now))
        return self._reduce_locked(event), False

    def _force_off_locked(self) -> ManagedTxTransition:
        self._retry_due = None
        return self._reduce_locked(
            ForceOff(self._provider_generation, self._attempt_id_locked())
        )

    def _reduce_locked(self, event: ManagedTxEvent) -> ManagedTxTransition:
        transition = reduce_managed_tx(self._state, event)
        self._state = transition.state
        return transition

    async def _execute(
        self, effects: tuple[ManagedTxEffect, ...], *, full_force: bool
    ) -> None:
        deadline = self._clock() + self._attempt_timeout
        events: list[ManagedTxEvent] = []
        if full_force:
            await self._abort_fence.force_off()
        for effect in effects:
            if full_force:
                aborts = [
                    asyncio.create_task(
                        self._lane.settle_abort(
                            effect.token, operation, deadline_monotonic=deadline
                        )
                    )
                    for operation in AbortOperation
                ]
            if settled := await self._lane.settle(effect, deadline_monotonic=deadline):
                events.append(settled)
            if full_force:
                events.extend(item for item in await asyncio.gather(*aborts) if item)
        async with self._lock:
            for event in events:
                self._reduce_locked(event)
            self._refresh_retry_locked()
            self._wakeup.wake()

    async def _scheduler(self) -> None:
        while True:
            async with self._lock:
                deadline = self._next_due_locked()
            await self._wakeup.wait_until(deadline)
            await self._process_due()

    async def _process_due(self) -> None:
        transition = None
        full_force = False
        async with self._lock:
            now = self._clock()
            tot_due = self._tot_deadline_locked()
            if tot_due is not None and tot_due <= now:
                transition = self._force_off_locked()
                full_force = True
            elif self._retry_due is not None and self._retry_due <= now:
                generation = self._provider_generation
                self._retry_due = None
                if generation is not None and self._release_is_retryable_locked():
                    transition = self._reduce_locked(
                        RetryForceReceive(generation, self._attempt_id_locked())
                    )
        if transition is not None:
            await self._execute(transition.effects, full_force=full_force)

    def _tot_deadline_locked(self, seconds: float | None = None) -> float | None:
        if seconds is None:
            seconds = self._config_store.config.timeout_seconds
        started = self._state.tx_started_at_monotonic
        return None if seconds is None or started is None else started + seconds

    def _deadline_from(self, started: float) -> float | None:
        seconds = self._config_store.config.timeout_seconds
        return None if seconds is None else started + seconds

    def _next_due_locked(self) -> float | None:
        due = [
            item
            for item in (self._tot_deadline_locked(), self._retry_due)
            if item is not None
        ]
        return min(due, default=None)

    def _release_is_retryable_locked(self) -> bool:
        return (
            self._state.intent.kind is ManagedTxIntentKind.RX
            and self._state.release_required
            and self._state.pending_effect is None
        )

    def _refresh_retry_locked(self) -> None:
        if (
            self._provider_generation is not None
            and self._release_is_retryable_locked()
        ):
            if self._retry_due is None:
                self._retry_due = self._clock() + self._retry_delay
        else:
            self._retry_due = None

    def _attempt_id_locked(self) -> str:
        self._attempt += 1
        return str(self._attempt)

    def _require_open_locked(self) -> None:
        if self._closing or self._closed:
            raise RuntimeError("managed TX authority is closed")
