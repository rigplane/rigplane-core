"""Single production composition root for managed transmit."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from rigplane.core.radio_protocol import ManagedTxSupervisor
from rigplane.core.state_store import StateStore
from rigplane.core.tx_safety import (
    RadioTx,
    TxOutcome,
    TxOwner,
    TxPhase,
    TxReleaseReason,
    TxSafetySnapshot,
    TxTransition,
)
from rigplane.runtime.local_tx_work import LocalTxWorkRunner
from rigplane.runtime.managed_tx_authority import ManagedTxAuthority, ShutdownResult
from rigplane.runtime.managed_tx_config import ManagedTxTotConfigStore
from rigplane.runtime.managed_tx_effect_lane import (
    ManagedTxActuator,
    ManagedTxEffectLane,
)
from rigplane.runtime.managed_tx_fence import TxAbortFence
from rigplane.runtime.managed_tx_state import ManagedTxIntentKind


@dataclass(frozen=True, slots=True)
class _ProviderEvent:
    provider_generation: int
    observation_generation: int
    transport_identity: object


_ProviderHook = Callable[[_ProviderEvent], Awaitable[None]]


async def _no_provider_hook(_event: _ProviderEvent) -> None:
    return None


@runtime_checkable
class ManagedTxCompositionPort(Protocol):
    @property
    def authority(self) -> ManagedTxAuthority: ...

    @property
    def local_tx_work_runner(self) -> LocalTxWorkRunner: ...

    @property
    def legacy_supervisor(self) -> ManagedTxSupervisor: ...

    async def bind_state_store(self, store: StateStore) -> None: ...

    async def transport_ready(self, identity: object) -> None: ...

    async def transport_unavailable(self, identity: object) -> None: ...

    def validate_state_store(self, store: StateStore) -> None: ...

    async def shutdown(self, termination: asyncio.Event) -> ShutdownResult: ...


class _LegacyManagedTxCutoverGate:
    def __init__(self, authority: ManagedTxAuthority) -> None:
        self._authority = authority

    async def request_on(self, _owner: TxOwner) -> TxTransition:
        raise RuntimeError("legacy PTT ON is blocked by production composition")

    async def release_owner(
        self, _owner: TxOwner, *, reason: TxReleaseReason
    ) -> TxTransition:
        del reason
        submission = await self._authority.submit_force_off()
        await submission.wait_settlement()
        projection = await self._authority.snapshot()
        active = projection.state.intent.kind is not ManagedTxIntentKind.RX
        snapshot = TxSafetySnapshot(
            phase=TxPhase.RELEASE_REQUIRED if active else TxPhase.IDLE,
            radio_tx=RadioTx.UNKNOWN,
            provider_generation=projection.provider_generation,
            provider_ready=projection.provider_generation is not None,
            lease_id=None,
            owner=None,
            release_reason=None,
            terminal_release_reason=None,
            release_attempt_count=0,
            release_last_error=projection.state.last_error,
            active_attempt=None,
            watchdog_deadline_monotonic=None,
            watchdog_enabled=False,
            external_conflict=False,
        )
        return TxTransition(TxOutcome.IDEMPOTENT, snapshot)


class ManagedTxComposition:
    """Own the one authority graph and every production provider transition."""

    def __init__(
        self,
        actuator: ManagedTxActuator,
        *,
        config_path: Path,
        prepare_provider: _ProviderHook = _no_provider_hook,
        retire_provider: _ProviderHook = _no_provider_hook,
    ) -> None:
        if not isinstance(actuator, ManagedTxActuator):
            raise TypeError("production managed TX requires a normalized actuator")
        self._prepare_provider = prepare_provider
        self._retire_provider = retire_provider
        self._abort_fence = TxAbortFence()
        self._local_tx_work_runner = LocalTxWorkRunner(self._abort_fence)
        self._tot_config_store = ManagedTxTotConfigStore(config_path)
        self._effect_lane = ManagedTxEffectLane(
            actuator, poison_generation=self._poison_tx_generation
        )
        self._authority = ManagedTxAuthority(
            self._effect_lane,
            self._tot_config_store,
            self._abort_fence,
            provider_generation=None,
        )
        self._legacy_supervisor = _LegacyManagedTxCutoverGate(self._authority)
        self._state_store: StateStore | None = None
        self._unsubscribe: Callable[[], None] | None = None
        self._observation_generation: int | None = None
        self._live_transport_identity: object | None = None
        self._stale_transport_identities: list[object] = []
        self._active_provider: _ProviderEvent | None = None
        self._events: dict[int, _ProviderEvent] = {}
        self._retired_generations: set[int] = set()
        self._next_provider_generation = 0
        self._transition_lock = asyncio.Lock()
        self._transition_task: asyncio.Task[None] | None = None
        self._shutdown_task: asyncio.Task[ShutdownResult] | None = None

    @property
    def authority(self) -> ManagedTxAuthority:
        return self._authority

    @property
    def local_tx_work_runner(self) -> LocalTxWorkRunner:
        return self._local_tx_work_runner

    @property
    def legacy_supervisor(self) -> ManagedTxSupervisor:
        return self._legacy_supervisor

    async def bind_state_store(self, store: StateStore) -> None:
        if not isinstance(store, StateStore):
            raise TypeError("managed TX requires the exact Web StateStore")
        async with self._transition_lock:
            if self._state_store is not None:
                raise RuntimeError("managed TX StateStore is already bound")
            self._state_store = store
            self._observation_generation = int(store.provider_generation)
            self._unsubscribe = store.subscribe_provider_generation(
                self._provider_generation_changed
            )
            await self._ensure_current_locked()

    def _provider_generation_changed(self, generation: int) -> None:
        self._observation_generation = int(generation)
        identity = self._live_transport_identity
        if identity is not None:
            self._stale_transport_identities.append(identity)
        self._live_transport_identity = None
        self._poison_active()

    async def transport_ready(self, identity: object) -> None:
        async with self._transition_lock:
            if self._shutdown_task is not None:
                return
            if any(identity is stale for stale in self._stale_transport_identities):
                return
            if self._state_store is None and self._live_transport_identity is not None:
                return
            active = self._active_provider
            if active is not None and active.transport_identity is not identity:
                self._stale_transport_identities.append(active.transport_identity)
                self._poison_active()
            self._live_transport_identity = identity
            await self._join_transition_locked()
            await self._ensure_current_locked()

    async def transport_unavailable(self, identity: object) -> None:
        async with self._transition_lock:
            if self._shutdown_task is not None:
                return
            if self._live_transport_identity is not identity:
                return
            if self._active_provider is not None:
                submission = await self._authority.submit_force_off()
                await submission.wait_settlement()
            self._stale_transport_identities.append(identity)
            self._live_transport_identity = None
            self._poison_active()
            await self._join_transition_locked()

    def validate_state_store(self, store: StateStore) -> None:
        if self._state_store is not store:
            raise RuntimeError("managed TX StateStore identity mismatch")
        generation = int(store.provider_generation)
        active = self._active_provider
        if self._observation_generation != generation:
            raise RuntimeError("managed TX observation generation mismatch")
        if active is None or active.observation_generation != generation:
            raise RuntimeError("managed TX provider is not current")

    async def _ensure_current_locked(self) -> None:
        store = self._state_store
        identity = self._live_transport_identity
        if store is None or identity is None or self._active_provider is not None:
            return
        observation_generation = int(store.provider_generation)
        if observation_generation != self._observation_generation:
            return
        self._next_provider_generation += 1
        event = _ProviderEvent(
            self._next_provider_generation, observation_generation, identity
        )
        await self._prepare_provider(event)
        if not self._candidate_is_current(event):
            await self._retire_once(event)
            return
        try:
            await self._authority.provider_available(event.provider_generation)
        except BaseException:
            await self._retire_once(event)
            raise
        if not self._candidate_is_current(event):
            cleanup = self._authority.start_provider_unavailable()
            await asyncio.shield(cleanup)
            await self._retire_once(event)
            return
        self._events[event.provider_generation] = event
        self._active_provider = event

    def _candidate_is_current(self, event: _ProviderEvent) -> bool:
        store = self._state_store
        return (
            self._shutdown_task is None
            and store is not None
            and self._live_transport_identity is event.transport_identity
            and self._observation_generation == event.observation_generation
            and int(store.provider_generation) == event.observation_generation
        )

    def _poison_active(self) -> None:
        event = self._active_provider
        if event is None:
            return
        cleanup = self._authority.start_provider_unavailable()
        self._active_provider = None
        previous = self._transition_task

        async def finish() -> None:
            if previous is not None:
                await asyncio.shield(previous)
            await asyncio.shield(cleanup)
            await self._retire_once(event)

        task = asyncio.create_task(finish())
        task.add_done_callback(self._harvest)
        self._transition_task = task

    async def _poison_tx_generation(self, generation: int) -> None:
        async with self._transition_lock:
            active = self._active_provider
            if active is None or active.provider_generation != generation:
                return
            identity = self._live_transport_identity
            if identity is not None:
                self._stale_transport_identities.append(identity)
            self._live_transport_identity = None
            self._poison_active()
            await self._join_transition_locked()

    async def _retire_once(self, event: _ProviderEvent) -> None:
        if event.provider_generation in self._retired_generations:
            return
        await self._retire_provider(event)
        self._retired_generations.add(event.provider_generation)

    async def _join_transition_locked(self) -> None:
        task = self._transition_task
        if task is not None:
            await asyncio.shield(task)
            if self._transition_task is task:
                self._transition_task = None

    @staticmethod
    def _harvest(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()

    async def shutdown(self, termination: asyncio.Event) -> ShutdownResult:
        async with self._transition_lock:
            task = self._shutdown_task
            if task is None:
                task = asyncio.create_task(self._complete_shutdown(termination))
                self._shutdown_task = task
        return await asyncio.shield(task)

    async def _complete_shutdown(self, termination: asyncio.Event) -> ShutdownResult:
        try:
            task = self._transition_task
            if task is not None:
                await asyncio.shield(task)
            if not self._events:
                await self._authority.close()
                return ShutdownResult.DRAINED
            if self._active_provider is None:
                try:
                    await self._authority.close()
                except RuntimeError:
                    pass
                else:
                    return ShutdownResult.DRAINED

            async def retire(generation: int) -> None:
                event = self._events.get(generation)
                if event is None:
                    raise RuntimeError("managed TX retirement lost provider event")
                await self._retire_once(event)

            result = await self._authority.shutdown(
                retire_provider=retire, termination=termination
            )
            terminal = self._active_provider
            self._active_provider = None
            self._live_transport_identity = None
            if result is ShutdownResult.TERMINATED and terminal is not None:
                await self._retire_once(terminal)
            return result
        finally:
            unsubscribe, self._unsubscribe = self._unsubscribe, None
            if unsubscribe is not None:
                unsubscribe()


def install_managed_tx_composition(
    radio: object, composition: ManagedTxCompositionPort
) -> None:
    if getattr(radio, "_managed_tx_composition", None) is not None:
        raise RuntimeError("managed TX composition is already installed")
    try:
        radio_namespace = vars(radio)
    except TypeError:
        radio_namespace = {}
    has_local_tx_work = "_local_tx_work" in radio_namespace
    prior_local_tx_work = radio_namespace.get("_local_tx_work")
    if has_local_tx_work and prior_local_tx_work is not None:
        raise RuntimeError("radio local TX work runner is already installed")
    installer = getattr(radio, "install_managed_tx_composition", None)
    raw_set_ptt = getattr(radio, "set_ptt", None)
    if not callable(raw_set_ptt):
        raise RuntimeError("radio cannot block raw PTT fallback")

    async def guarded_set_ptt(on: bool) -> None:
        if type(on) is not bool:
            raise TypeError("PTT requires a bool")
        if on:
            raise RuntimeError("raw PTT ON is blocked by production composition")
        submission = await composition.authority.submit_force_off()
        await submission.wait_settlement()

    try:
        if has_local_tx_work:
            setattr(radio, "_local_tx_work", composition.local_tx_work_runner)
        setattr(radio, "set_ptt", guarded_set_ptt)
        if callable(installer):
            installer(composition)
        else:
            setattr(radio, "_managed_tx_composition", composition)
            setattr(radio, "managed_tx", composition.legacy_supervisor)
        if getattr(radio, "_managed_tx_composition", None) is not composition:
            raise RuntimeError("radio did not retain the managed TX composition")
    except BaseException:
        setattr(radio, "set_ptt", raw_set_ptt)
        if has_local_tx_work:
            setattr(radio, "_local_tx_work", prior_local_tx_work)
        if getattr(radio, "_managed_tx_composition", None) is composition:
            setattr(radio, "_managed_tx_composition", None)
        raise
