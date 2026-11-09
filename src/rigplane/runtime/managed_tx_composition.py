"""Single production composition root for managed transmit."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, runtime_checkable

from rigplane.core.radio_protocol import ManagedTxSupervisor, TransmitStateReadable
from rigplane.core.state_pipeline_contracts import FieldPath
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
from rigplane.runtime.managed_tx_authority import (
    ManagedTxAuthority,
    ShutdownResult,
)
from rigplane.runtime.managed_tx_config import ManagedTxTotConfigStore
from rigplane.runtime.managed_tx_effect_lane import (
    ManagedTxActuator,
    ManagedTxEffectLane,
)
from rigplane.runtime.managed_tx_fence import TxAbortFence
from rigplane.runtime.managed_tx_state import ManagedTxIntentKind
from rigplane.runtime.tx_interlock import RfState, resolve_rf_state

logger = logging.getLogger(__name__)

_PTT_PATH = FieldPath.global_("tx_state", "ptt")


@dataclass(frozen=True, slots=True)
class _ProviderEvent:
    provider_generation: int
    observation_generation: int
    transport_identity: object


@dataclass(frozen=True, slots=True)
class _StartupRelease:
    """A startup release debt recorded before its provider is published."""

    backend_label: str
    model_label: str


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

    def validate_state_store(
        self, store: StateStore, *, transport_pending: bool = False
    ) -> None: ...

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
        self._actuator = actuator
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
            radio_tx=self,
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
        self._recovery_task: asyncio.Task[None] | None = None
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

    def radio_tx_since(self, since: float) -> bool:
        """Whether the radio reported TX in an observation newer than ``since``.

        MOR-3537: the observed-RF truth every TX seat already resolves; the
        observation time is read from the same validated field, on the
        ``time.monotonic`` clock the authority reads (see
        ``RigctldHandler._ptt_observed_after`` for the clock-domain note).
        Whoever keyed the radio, such a TX refuses every TX start; it never
        gates force_off (MOR-3921: no origin classification).
        """
        store = self._state_store
        if store is None:
            return False
        snapshot = store.snapshot()
        if resolve_rf_state(snapshot) is not RfState.TX:
            return False
        if snapshot.field(_PTT_PATH).last_observed_monotonic <= since:
            return False
        return True

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

    def validate_state_store(
        self, store: StateStore, *, transport_pending: bool = False
    ) -> None:
        if self._state_store is not store:
            raise RuntimeError("managed TX StateStore identity mismatch")
        generation = int(store.provider_generation)
        active = self._active_provider
        if self._observation_generation != generation:
            raise RuntimeError("managed TX observation generation mismatch")
        if transport_pending and self._live_transport_identity is None:
            # MOR-2876: the caller reports that the radio's port is not open,
            # so there is no transport for a provider to stand on yet.
            return
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
        self._events[event.provider_generation] = event
        await self._publish_prepared_provider(event)

    async def _publish_prepared_provider(self, event: _ProviderEvent) -> None:
        """Publish a prepared provider as one cancellation-joined unit.

        The bounded sequence — startup PTT read, release-debt recording,
        provider availability, currency re-check, activation — runs as
        its own task, and a cancellation of the activating caller joins
        it to completion instead of bisecting it (round-2 review P1):
        the read still reaches its answer, any debt is still recorded,
        and the provider is still published so its retry force-offs a
        rig the read found keyed. Shutdown can therefore never drain a
        half-activated provider as clean while the radio stays keyed.

        A shutdown-time blanket cancellation may also cancel the
        publication task itself — before its first instruction, so none
        of its own bookkeeping ran (round-3 review P1). The owning
        wrapper therefore performs the fail-closed abort bookkeeping
        for every publication that did not complete, and the
        ``done()``-guarded join exits without spinning (the shield of a
        cancelled task re-raises without yielding).
        """
        publication = asyncio.create_task(
            self._publish_provider_once(event),
            name="managed-tx-provider-publication",
        )
        publication.add_done_callback(self._harvest)
        cancelled = False
        while not publication.done():
            try:
                await asyncio.shield(publication)
            except asyncio.CancelledError:
                cancelled = True
        if publication.cancelled():
            await self._abort_prepared_publication(event)
            raise asyncio.CancelledError
        if cancelled:
            raise asyncio.CancelledError
        await publication

    async def _abort_prepared_publication(self, event: _ProviderEvent) -> None:
        """Fail-closed bookkeeping for a publication that did not complete.

        An aborted publication leaves an unresolved PTT read behind, so
        the release is owed, never assumed clean: the debt keeps every
        shutdown path from draining the provider as clean while the rig
        may still be keyed. Idempotent — a publication whose own error
        path already recorded the debt or retired the event no-ops
        here.
        """
        try:
            # Admitted after ``close_ingress_for_shutdown`` too: a closed
            # ingress refuses keying, never a release (Codex gate P0).
            await self._authority.submit_force_off()
        except Exception:
            # Only a closed, terminated or already shutting-down
            # authority refuses; its own shutdown path then owes and
            # drives the release.
            logger.info(
                "managed_tx.startup_unkey: recording the release debt of "
                "an aborted publication failed; the authority's own "
                "shutdown path owes the release",
                exc_info=True,
            )
        await self._retire_once(event)

    async def _publish_provider_once(self, event: _ProviderEvent) -> None:
        # MOR-4186 review P1-1: PTT is read and any release debt is
        # recorded BEFORE the provider is published. While the read is
        # in flight the authority holds no provider, so no managed
        # ingress can be admitted and this release can never hit a TX
        # this process owns; provider_available() then issues the
        # force_receive under its own lock, before any ingress can key.
        try:
            startup_release = await self._record_startup_release()
            await self._authority.provider_available(event.provider_generation)
        except BaseException:
            # Any abort — a failed publication or a cancellation of the
            # publication task itself — retires the prepared provider;
            # the owning wrapper records the fail-closed release debt.
            await self._retire_once(event)
            raise
        if not self._candidate_is_current(event):
            cleanup = self._authority.start_provider_unavailable()
            await asyncio.shield(cleanup)
            await self._retire_once(event)
            return
        self._active_provider = event
        if startup_release is not None:
            await self._log_startup_release(startup_release)

    async def _record_startup_release(self) -> _StartupRelease | None:
        """MOR-4186: record a release debt for a rig still keyed at the edge.

        Runs once at every provider activation — process start and every
        radio (re)connect, the one edge every backend already reports
        through :meth:`transport_ready` — while the authority still holds
        no provider: no managed ingress can be admitted yet, so a TX
        this process owns can never be caught by this release (the
        review's P1-1 race). PTT is read through the backend's normal
        read path (:class:`~rigplane.core.radio_protocol.TransmitStateReadable`);
        only a reading that positively says transmitting with a verified
        readback (the protocol's freshness proof — a bare value is not)
        while this process owes no release (the state a SIGKILLed Core
        leaves behind on a USB-serial rig with no link watchdog)
        records exactly one release debt:
        :meth:`ManagedTxAuthority.submit_force_off` with no provider,
        i.e. ``ManagedTxSubmission.pending_release``.
        :meth:`ManagedTxAuthority.provider_available` then issues the
        ``RetryForceReceive`` under its own lock before any ingress can
        key, and every TX ON is refused while the debt stays owed. An
        unconfirmed release keeps the authority's fail-closed retry
        policy and its visible release debt, surfaced by
        :meth:`_log_startup_release` as "may still be transmitting";
        nothing here reports the rig as receiving. A PTT read that
        fails or is unreadable does nothing (no blind writes) and logs
        at INFO; an unverified "transmitting" reading also leaves the
        rig untouched and logs at WARNING. Never raises: this is part
        of the connect path.
        """
        actuator = self._actuator
        if not isinstance(actuator, TransmitStateReadable):
            return None
        backend = getattr(actuator, "backend_id", None)
        backend_label = (
            backend
            if isinstance(backend, str) and backend
            else (type(actuator).__name__)
        )
        model = getattr(actuator, "model", None)
        model_label = model if isinstance(model, str) and model else "unknown"
        try:
            projection = await self._authority.snapshot()
            state = projection.state
            if (
                state.intent.kind is not ManagedTxIntentKind.RX
                or state.release_required
                or state.pending_effect is not None
            ):
                # A managed-TX owner or a release this process already
                # owes covers the rig; the startup release is only for
                # a key nothing in this process made.
                return None
            reading = await actuator.read_transmit_state()
        except Exception:
            # Not an error: a radio whose PTT cannot be established is
            # left untouched (no blind writes).
            logger.info(
                "managed_tx.startup_unkey: PTT read at connect failed; "
                "leaving the rig untouched",
                exc_info=True,
            )
            return None
        if reading.value is not True:
            if reading.value is None:
                logger.info(
                    "managed_tx.startup_unkey: PTT unreadable at connect "
                    "(%s); leaving the rig untouched",
                    reading.failure or "no value",
                )
            return None
        if reading.failure is not None or not reading.verified_readback:
            # The read contract: a returned value does not by itself prove
            # a fresh physical-radio read, so an unverified "transmitting"
            # is not the positive answer rule 4 requires.
            logger.warning(
                "managed_tx.startup_unkey: backend=%s model=%s PTT "
                "reading at connect says transmitting without a "
                "verified readback (failure=%s verified=%s); leaving "
                "the rig untouched",
                backend_label,
                model_label,
                reading.failure,
                reading.verified_readback,
            )
            return None
        try:
            await self._authority.submit_force_off()
        except Exception:
            logger.warning(
                "managed_tx.startup_unkey: backend=%s model=%s was "
                "transmitting at connect with no managed-TX owner; "
                "force_receive could not be submitted",
                backend_label,
                model_label,
                exc_info=True,
            )
            return None
        return _StartupRelease(backend_label, model_label)

    async def _log_startup_release(self, release: _StartupRelease) -> None:
        """Log the startup release outcome from the post-availability state.

        The outcome is read from the authority snapshot after
        :meth:`ManagedTxAuthority.provider_available` issued the
        force_receive: ``release_required`` is cleared only by an
        accepted readback, so a debt still visible here means the rig
        may still be transmitting.
        """
        projection = await self._authority.snapshot()
        if projection.state.release_required:
            logger.warning(
                "managed_tx.startup_unkey: backend=%s model=%s was "
                "transmitting at connect with no managed-TX owner; the "
                "release stays owed and the rig may still be transmitting",
                release.backend_label,
                release.model_label,
            )
        else:
            logger.warning(
                "managed_tx.startup_unkey: backend=%s model=%s was "
                "transmitting at connect with no managed-TX owner; "
                "force_receive confirmed",
                release.backend_label,
                release.model_label,
            )

    def _candidate_is_current(self, event: _ProviderEvent) -> bool:
        store = self._state_store
        return (
            (self._shutdown_task is None or not self._shutdown_task.done())
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
            self._poison_active()
            await self._join_transition_locked()

            async def recover() -> None:
                async with self._transition_lock:
                    if self._shutdown_task is None or not self._shutdown_task.done():
                        await self._ensure_current_locked()

            self._recovery_task = asyncio.create_task(recover())
            self._recovery_task.add_done_callback(self._harvest)

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
            if self._active_provider is None:
                # The clean-close shortcut never discards a release: close()
                # refuses an authority that still owes one, and the owed
                # release then goes through the authority's shutdown, which
                # reports DRAINED only after a confirmed release.
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
            recovery = self._recovery_task
            if recovery is not None:
                recovery.cancel()
                await asyncio.gather(recovery, return_exceptions=True)
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
