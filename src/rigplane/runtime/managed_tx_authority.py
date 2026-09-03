from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Coroutine
from dataclasses import dataclass, replace
from enum import StrEnum
from functools import partial
from typing import Any, Protocol

from rigplane.core.state_pipeline_contracts import CommandIntent
from rigplane.runtime.managed_tx_config import (
    ManagedTxTotConfig,
    ManagedTxTotConfigStore,
)
from rigplane.runtime.managed_tx_effect_lane import ManagedTxEffectLane
from rigplane.runtime.managed_tx_fence import TxAbortFence, TxAbortResult
from rigplane.runtime.managed_tx_state import (
    AbortOperation,
    ActuationOperation,
    ActuationResult,
    ActuationSettled,
    EffectToken,
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


_SubmissionCompletion = asyncio.Task[
    tuple[ManagedTxTransition, ActuationSettled | None]
]


@dataclass(frozen=True, slots=True)
class ManagedTxSubmission:
    """Accepted or rejected intent plus its separately owned actuation result."""

    transition: ManagedTxTransition
    _completion: _SubmissionCompletion

    @property
    def outcome(self) -> ManagedTxOutcome:
        return self.transition.outcome

    @property
    def settlement_done(self) -> bool:
        return self._completion.done()

    async def wait_settlement(self) -> ActuationSettled | None:
        """Join provider settlement without transferring cancellation ownership."""
        _transition, settlement = await asyncio.shield(self._completion)
        return settlement


class ShutdownResult(StrEnum):
    DRAINED = "drained"
    TERMINATED = "terminated"


_ProviderRetirement = Callable[[int], Awaitable[None]]

_ANTENNA_WRITE_NAMES = frozenset(
    {"set_antenna", "set_antenna_1", "set_antenna_2", "set_rx_antenna"}
)


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
        self._pending_abort_cleanup: list[Coroutine[Any, Any, TxAbortResult]] = []
        self._abort_cleanup: set[asyncio.Task[TxAbortResult]] = set()
        self._settlement_tasks: set[_SubmissionCompletion] = set()
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
        self._release_drained = asyncio.Event()
        self._release_drained.set()
        self._shutting_down = False
        self._terminated = False
        self._shutdown_termination: asyncio.Event | None = None
        self._shutdown_task: asyncio.Task[ShutdownResult] | None = None
        self._closing = False
        self._closed = False
        self._scheduler_task = asyncio.create_task(self._scheduler())

    async def ptt_down(self, owner: str) -> ManagedTxOutcome:
        """Compatibility helper which deliberately drains provider settlement."""
        submission = await self.submit_ptt(True, owner)
        await submission.wait_settlement()
        return submission.outcome

    async def ptt_up(self, owner: str) -> ManagedTxOutcome:
        """Compatibility helper which deliberately drains provider settlement."""
        submission = await self.submit_ptt(False, owner)
        await submission.wait_settlement()
        return submission.outcome

    async def submit_ptt(
        self, on: bool, owner: str, *, ready: asyncio.Future[Any] | None = None
    ) -> ManagedTxSubmission:
        """Return after owner-scoped admission, before provider settlement."""
        worker, admitted = self._begin_ptt_operation(on, owner, ready=ready)
        try:
            transition = await asyncio.shield(admitted)
        except asyncio.CancelledError:
            worker.cancel()
            await self._drain_cancelled(worker)
            raise
        return ManagedTxSubmission(transition, worker)

    async def _start_ptt_operation(
        self, on: bool, owner: str, *, ready: asyncio.Future[Any] | None = None
    ) -> _SubmissionCompletion:
        """Compatibility seam for internal pre-admission cancellation tests.

        Production ingress must use :meth:`submit_ptt`; this returns the private
        operation task which the abort fence may cancel before admission.
        """
        worker, admitted = self._begin_ptt_operation(on, owner, ready=ready)
        if not on:
            try:
                await asyncio.shield(admitted)
            except asyncio.CancelledError:
                worker.cancel()
                await self._drain_cancelled(worker)
                raise
        return worker

    def _begin_ptt_operation(
        self, on: bool, owner: str, *, ready: asyncio.Future[Any] | None = None
    ) -> tuple[_SubmissionCompletion, asyncio.Future[ManagedTxTransition]]:
        """Build the one cancellable operation and its admission signal."""
        if type(on) is not bool or type(owner) is not str:
            raise TypeError("PTT requires a bool and a builtin str owner")
        if not owner:
            raise ValueError("PTT intent requires an owner token")
        if ready is not None and not isinstance(ready, asyncio.Future):
            raise TypeError("PTT readiness must be an existing Future or Task")
        generation = self._provider_generation
        token = self._abort_fence.issue() if on else None
        admitted: asyncio.Future[ManagedTxTransition] = (
            asyncio.get_running_loop().create_future()
        )
        if not on:
            self._cancel_pending_ptt(owner)

        async def run() -> tuple[ManagedTxTransition, ActuationSettled | None]:
            execution: asyncio.Task[ActuationSettled | None] | None = None
            try:
                if on and ready is not None:
                    await asyncio.wait((ready,))
                async with self._lock:
                    self._require_ingress_open_locked()
                    if token is not None and not (
                        self._provider_generation == generation
                        and self._abort_fence.is_current(token)
                        and self._abort_fence.remove(token)
                    ):
                        return ManagedTxTransition(
                            self._state, ManagedTxOutcome.REJECTED
                        ), None
                    transition, full_force = self._transition_locked(
                        "ptt_down" if on else "ptt_up", owner
                    )
                    if transition is None:
                        transition = ManagedTxTransition(
                            self._state, ManagedTxOutcome.REJECTED
                        )
                    self._wakeup.wake()
                    execution = asyncio.create_task(
                        self._execute(transition.effects, full_force=full_force)
                    )
                    if not admitted.done():
                        admitted.set_result(transition)
                return transition, await asyncio.shield(execution)
            except asyncio.CancelledError:
                if execution is not None:
                    await self._drain_cancelled(execution)
                raise

        def finished(
            task: asyncio.Task[tuple[ManagedTxTransition, ActuationSettled | None]],
        ) -> None:
            if token is not None:
                self._abort_fence.remove(token)
            error = None if task.cancelled() else task.exception()
            if not admitted.done():
                if task.cancelled():
                    admitted.cancel()
                elif error is not None:
                    admitted.set_exception(error)
                else:
                    admitted.set_result(task.result()[0])

        worker = asyncio.create_task(run())
        self._own_submission(worker)
        worker.add_done_callback(finished)
        if token is not None:

            def cancel_pending() -> None:
                worker.cancel()

            try:
                self._abort_fence.register(token, cancel_pending, scope=owner)
            except BaseException:
                worker.cancel()
                raise
        return worker, admitted

    def _cancel_pending_ptt(self, owner: str) -> None:
        self._pending_abort_cleanup.append(self._abort_fence.cancel_scope(owner))
        self._start_abort_cleanup()

    def _own_submission(self, task: _SubmissionCompletion) -> None:
        self._settlement_tasks.add(task)

        def finished(owned: _SubmissionCompletion) -> None:
            self._settlement_tasks.discard(owned)
            if not owned.cancelled():
                owned.exception()

        task.add_done_callback(finished)

    @staticmethod
    async def _drain_cancelled(task: asyncio.Task[Any]) -> None:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if not task.cancelled():
            task.exception()

    async def transmit_on(self) -> ManagedTxOutcome:
        """Compatibility helper which deliberately drains provider settlement."""
        submission = await self.submit_transmit_on()
        await submission.wait_settlement()
        return submission.outcome

    async def force_off(self) -> ManagedTxOutcome:
        """Compatibility helper which deliberately drains provider settlement."""
        submission = await self.submit_force_off()
        await submission.wait_settlement()
        return submission.outcome

    async def submit_transmit_on(self) -> ManagedTxSubmission:
        """Return the latched-ON admission separately from provider settlement."""
        return await self._submit_ingress("transmit_on")

    async def submit_force_off(self) -> ManagedTxSubmission:
        """Advance the abort fence and return before provider settlement."""
        return await self._submit_ingress("force_off")

    async def admit_managed_write(self, intent: CommandIntent) -> bool:
        """Apply the sole managed-intent relay policy without altering ``intent``."""
        if not isinstance(intent, CommandIntent):
            raise TypeError("managed write admission requires a CommandIntent")
        async with self._lock:
            managed_tx = self._state.intent.kind is not ManagedTxIntentKind.RX
            if not managed_tx:
                return True
            if intent.name in _ANTENNA_WRITE_NAMES:
                return False
            if intent.name == "set_tuner_status":
                return intent.params.get("value") == 0
            if intent.name == "set_func" and intent.params.get("func") == "TUNER":
                return intent.params.get("on") is False
            return True

    def is_effect_current(
        self, token: EffectToken, operation: ActuationOperation | AbortOperation
    ) -> bool:
        """Check write currency synchronously against the live pending attempt."""
        pending = self._state.pending_effect
        if isinstance(operation, AbortOperation):
            operation = ActuationOperation.FORCE_RECEIVE
        return bool(
            not (self._closing or self._closed or self._terminated)
            and not (
                self._shutdown_termination is not None
                and self._shutdown_termination.is_set()
            )
            and self._provider_generation == token.provider_generation
            and pending is not None
            and pending.token == token
            and pending.operation is operation
        )

    async def owner_disconnect(self, owner: str) -> ManagedTxOutcome:
        self._cancel_pending_ptt(owner)
        async with self._lock:
            self._require_ingress_open_locked()
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
            self._require_ingress_open_locked()
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

    async def provider_unavailable(self) -> None:
        transition = None
        async with self._lock:
            self._require_open_locked()
            self._require_provider_change_locked()
            if self._provider_generation is None:
                return
            self._provider_generation = None
            self._retry_due = None
            if self._state.release_required:
                transition = self._force_off_locked()
            self._wakeup.wake()
        if transition is not None:
            await self._execute(transition.effects, full_force=True)

    async def provider_available(self, generation: int) -> None:
        transition = None
        async with self._lock:
            self._require_open_locked()
            self._require_provider_change_locked()
            if generation == self._provider_generation:
                return
            if self._provider_generation is not None:
                raise RuntimeError("current provider must become unavailable first")
            if generation <= self._generation_high_water:
                raise ValueError("provider generation must increase")
            self._generation_high_water = generation
            self._provider_generation = generation
            if self._release_is_retryable_locked():
                transition = self._reduce_locked(
                    RetryForceReceive(generation, self._attempt_id_locked())
                )
            self._wakeup.wake()
        if transition is not None:
            await self._execute(transition.effects, full_force=False)

    async def shutdown(
        self,
        *,
        retire_provider: _ProviderRetirement,
        termination: asyncio.Event,
    ) -> ShutdownResult:
        async with self._lock:
            task = self._shutdown_task
            if task is None:
                self._require_open_locked()
                self._shutting_down = True
                self._shutdown_termination = termination
                transition = self._force_off_locked()
                self._wakeup.wake()
        if task is None:
            task = asyncio.create_task(
                self._complete_shutdown(transition, retire_provider, termination)
            )
            self._shutdown_task = task
        return await asyncio.shield(task)

    async def close(self) -> None:
        await self._dispose_clean(from_shutdown=False)

    async def _complete_shutdown(
        self,
        transition: ManagedTxTransition,
        retire_provider: _ProviderRetirement,
        termination: asyncio.Event,
    ) -> ShutdownResult:
        try:
            self._start_abort_cleanup()
            drain = asyncio.create_task(
                self._wait_for_release_or_termination(termination)
            )
            release = asyncio.create_task(
                self._execute(transition.effects, full_force=True)
            )
            try:
                done, _ = await asyncio.wait(
                    (drain, release), return_when=asyncio.FIRST_COMPLETED
                )
                if release in done:
                    await release
                drained = await drain
            finally:
                for task in (drain, release):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(drain, release, return_exceptions=True)
            if not drained:
                async with self._lock:
                    scheduler = self._scheduler_task
                await self._stop_scheduler(scheduler)
                await self._cancel_abort_cleanup()
                return ShutdownResult.TERMINATED

            async with self._lock:
                if not self._state_is_clean_locked():
                    raise RuntimeError("managed TX shutdown drain lost clean state")
                generation = self._provider_generation
            if generation is not None:
                await retire_provider(generation)
            async with self._lock:
                if self._provider_generation != generation:
                    raise RuntimeError("managed TX provider changed during retirement")
                self._provider_generation = None
            await self._dispose_clean(from_shutdown=True)
            return ShutdownResult.DRAINED
        except BaseException:
            async with self._lock:
                self._shutting_down = False
                if self._shutdown_task is asyncio.current_task():
                    self._shutdown_termination = None
                    self._shutdown_task = None
            raise

    async def _wait_for_release_or_termination(
        self, termination: asyncio.Event
    ) -> bool:
        async with self._lock:
            if (
                self._state_is_clean_locked()
                and not self._abort_cleanup
                and not self._pending_settlements_locked()
            ):
                return True
        drained = asyncio.create_task(self._wait_for_clean_release())
        terminated = asyncio.create_task(termination.wait())
        try:
            done, _ = await asyncio.wait(
                (drained, terminated), return_when=asyncio.FIRST_COMPLETED
            )
            async with self._lock:
                if self._terminated:
                    return False
                if (
                    drained in done
                    and self._state_is_clean_locked()
                    and not self._pending_settlements_locked()
                ):
                    return True
                if terminated in done:
                    self._terminated = True
                    return False
                raise RuntimeError("managed TX drain signalled before clean RX state")
        finally:
            for waiter in (drained, terminated):
                if not waiter.done():
                    waiter.cancel()
            await asyncio.gather(drained, terminated, return_exceptions=True)

    async def _wait_for_clean_release(self) -> None:
        while True:
            await self._release_drained.wait()
            await self._finish_abort_cleanup()
            async with self._lock:
                settlements = self._pending_settlements_locked()
            if settlements:
                await asyncio.gather(
                    *(asyncio.shield(task) for task in settlements),
                    return_exceptions=True,
                )
                continue
            if self._release_drained.is_set():
                return

    def _start_abort_cleanup(self) -> None:
        pending, self._pending_abort_cleanup = self._pending_abort_cleanup, []
        for cleanup in pending:
            task = asyncio.create_task(cleanup)
            self._abort_cleanup.add(task)
            task.add_done_callback(self._abort_cleanup.discard)

    async def _finish_abort_cleanup(self) -> None:
        self._start_abort_cleanup()
        while self._abort_cleanup:
            for task in tuple(self._abort_cleanup):
                if task.done():
                    self._abort_cleanup.discard(task)
                    task.result()
            await asyncio.gather(
                *(asyncio.shield(task) for task in tuple(self._abort_cleanup))
            )
            self._start_abort_cleanup()

    async def _cancel_abort_cleanup(self) -> None:
        self._start_abort_cleanup()
        tasks = tuple(self._abort_cleanup)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _dispose_clean(self, *, from_shutdown: bool) -> None:
        async with self._lock:
            if self._closed:
                return
            if not self._state_is_clean_locked():
                raise RuntimeError("managed TX disposal requires clean RX state")
            if self._pending_settlements_locked():
                raise RuntimeError(
                    "managed TX disposal requires settled provider operations"
                )
            if self._shutting_down and not from_shutdown:
                raise RuntimeError("managed TX shutdown is in progress")
            self._closing = True
            scheduler = self._scheduler_task
        await self._stop_scheduler(scheduler)
        await self._finish_abort_cleanup()
        async with self._lock:
            self._closed = True
            self._closing = False

    @staticmethod
    async def _stop_scheduler(scheduler: asyncio.Task[None]) -> None:
        scheduler.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await scheduler

    async def _submit_ingress(
        self, action: str, owner: str | None = None
    ) -> ManagedTxSubmission:
        async with self._lock:
            self._require_ingress_open_locked()
            transition, full_force = self._transition_locked(action, owner)
            if transition is None:
                transition = ManagedTxTransition(
                    self._state, ManagedTxOutcome.REJECTED
                )
            self._wakeup.wake()

        async def settle() -> tuple[ManagedTxTransition, ActuationSettled | None]:
            result = await self._execute(
                transition.effects,
                full_force=full_force,
            )
            return transition, result

        completion = asyncio.create_task(settle())
        self._own_submission(completion)
        return ManagedTxSubmission(transition, completion)

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
                state = replace(
                    self._state,
                    intent=ManagedTxIntent.rx(),
                    tx_started_at_monotonic=None,
                    tot_deadline_monotonic=None,
                    pending_effect=None,
                )
                transition = ManagedTxTransition(state, ManagedTxOutcome.ACCEPTED)
                self._state = state
                self._release_drained.clear()
                return transition, False
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
        self._pending_abort_cleanup.append(self._abort_fence.force_off())
        return self._reduce_locked(
            ForceOff(self._provider_generation, self._attempt_id_locked())
        )

    def _reduce_locked(self, event: ManagedTxEvent) -> ManagedTxTransition:
        transition = reduce_managed_tx(self._state, event)
        self._state = transition.state
        if self._state_is_clean_locked():
            self._release_drained.set()
        else:
            self._release_drained.clear()
        return transition

    async def _execute(
        self, effects: tuple[ManagedTxEffect, ...], *, full_force: bool
    ) -> ActuationSettled | None:
        original_effects = effects
        original_settlement = None
        while True:
            self._start_abort_cleanup()
            deadline = self._clock() + self._attempt_timeout
            events: list[ManagedTxEvent] = []
            for effect in effects:
                if full_force:
                    aborts = [
                        asyncio.create_task(
                            self._lane.settle_abort(
                                effect.token,
                                operation,
                                deadline_monotonic=deadline,
                                is_current=partial(
                                    self.is_effect_current, effect.token, operation
                                ),
                            )
                        )
                        for operation in AbortOperation
                    ]
                if settled := await self._lane.settle(
                    effect,
                    deadline_monotonic=deadline,
                    is_current=partial(
                        self.is_effect_current, effect.token, effect.operation
                    ),
                ):
                    events.append(settled)
                    if any(
                        settled.token == requested.token
                        and settled.operation is requested.operation
                        for requested in original_effects
                    ):
                        original_settlement = settled
                if full_force:
                    events.extend(
                        item for item in await asyncio.gather(*aborts) if item
                    )
            followup = None
            async with self._lock:
                if self._terminated or (
                    self._shutdown_termination is not None
                    and self._shutdown_termination.is_set()
                ):
                    self._terminated = True
                    return None
                for event in events:
                    transition = self._reduce_locked(event)
                    if (
                        transition.outcome is ManagedTxOutcome.APPLIED
                        and isinstance(event, ActuationSettled)
                        and event.operation
                        in (ActuationOperation.PTT_ON, ActuationOperation.TRANSMIT_ON)
                        and event.result is ActuationResult.UNCERTAIN
                    ):
                        followup = self._force_off_locked()
                self._refresh_retry_locked()
                self._wakeup.wake()
            if followup is None:
                return original_settlement
            effects, full_force = followup.effects, True

    async def _scheduler(self) -> None:
        while True:
            async with self._lock:
                if self._closing or self._closed or self._terminated:
                    return
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
        if self._terminated:
            raise RuntimeError("managed TX authority runtime has terminated")
        if self._closing or self._closed:
            raise RuntimeError("managed TX authority is closed")

    def _require_ingress_open_locked(self) -> None:
        self._require_open_locked()
        if self._shutting_down:
            raise RuntimeError("managed TX authority is shutting down")

    def _require_provider_change_locked(self) -> None:
        if self._shutting_down and self._state_is_clean_locked():
            raise RuntimeError("managed TX shutdown drain is complete")

    def _state_is_clean_locked(self) -> bool:
        return (
            self._state.intent.kind is ManagedTxIntentKind.RX
            and not self._state.release_required
            and self._state.pending_effect is None
        )

    def _pending_settlements_locked(self) -> tuple[_SubmissionCompletion, ...]:
        return tuple(task for task in self._settlement_tasks if not task.done())
