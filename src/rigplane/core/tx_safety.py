"""Pure, single-target policy for managed radio PTT.

The supervisor emits bounded provider operations but performs no I/O. Runtime
integration enforces operation and cancellation-settlement deadlines, then
reports settlement plus field-specific PTT observations back to this model.
"""

from __future__ import annotations

import time
import uuid
from math import isfinite
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import TypeAlias

Clock = Callable[[], float]
IdFactory = Callable[[], str]

#: Longest a managed key-down may last before the watchdog releases it with
#: :attr:`TxReleaseReason.BACKEND_MAX_KEY_DOWN`. Named, not inlined as a default,
#: because ``web.radio_poller``'s unmanaged backstop (MOR-1220) is the SAME
#: bound: one key-down limit per operator, not one per backend class.
BACKEND_MAX_KEY_DOWN_SECONDS: float = 180.0


class TxSource(StrEnum):
    WEBSOCKET = "websocket"
    RIGCTLD = "rigctld"
    SDK = "sdk"
    HAMLIB_BRIDGE = "hamlib_bridge"
    INTERNAL = "internal"


class RadioTx(StrEnum):
    OFF = "off"
    ON = "on"
    UNKNOWN = "unknown"


class TxPhase(StrEnum):
    IDLE = "idle"
    KEY_PENDING = "key_pending"
    KEYED = "keyed"
    RELEASE_REQUIRED = "release_required"
    RELEASE_CONFIRMING = "release_confirming"
    FAULTED = "faulted"
    EXTERNAL_UNOWNED = "external_unowned"


class TxOutcome(StrEnum):
    ACCEPTED = "accepted"
    IDEMPOTENT = "idempotent"
    BUSY = "tx_busy"
    NOT_READY = "provider_not_ready"
    RADIO_NOT_OFF = "radio_not_off"
    RELEASE_PENDING = "release_pending"
    STALE = "stale"
    APPLIED = "applied"
    NOOP = "noop"


class TxReleaseReason(StrEnum):
    OPERATOR_RELEASE = "operator_release"
    OPERATOR_FORCED_UNKEY = "operator_forced_unkey"
    SOURCE_DETACHED = "source_detached"
    PRESENTATION_REPLACED = "presentation_replaced"
    CLIENT_DISCONNECTED = "client_disconnected"
    AUDIO_FAILED = "audio_failed"
    CONTROL_TRANSPORT_LOST = "control_transport_lost"
    RADIO_TRANSPORT_LOST = "radio_transport_lost"
    CAPABILITY_LOST = "capability_lost"
    PERMIT_LOST = "permit_lost"
    FRONTEND_SAFETY_TIMEOUT = "frontend_safety_timeout"
    BACKEND_MAX_KEY_DOWN = "backend_max_key_down"
    EXTERNAL_CAT_PREEMPTED = "external_cat_preempted"
    APP_SHUTDOWN = "app_shutdown"
    SERVER_SHUTDOWN = "server_shutdown"
    RECOVERY_EXHAUSTED = "recovery_exhausted"
    ADMIN_EMERGENCY_STOP = "admin_emergency_stop"


class ProviderAttemptKind(StrEnum):
    WRITE_ON = "write_on"
    WRITE_OFF = "write_off"
    READ_PTT = "read_ptt"


@dataclass(frozen=True, slots=True)
class TxOwner:
    source: TxSource
    session_id: str

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("session_id must not be empty")


@dataclass(frozen=True, slots=True)
class ProviderPttObservation:
    value: RadioTx
    provider_generation: int
    ptt_observation_seq: int
    observed_at_monotonic: float

    def __post_init__(self) -> None:
        if self.value is RadioTx.UNKNOWN:
            raise ValueError("PTT observations must decode to ON or OFF")
        if self.provider_generation < 0:
            raise ValueError("provider_generation must be non-negative")
        if self.ptt_observation_seq <= 0:
            raise ValueError("ptt_observation_seq must be positive")
        if not isfinite(self.observed_at_monotonic):
            raise ValueError("observed_at_monotonic must be finite")


@dataclass(frozen=True, slots=True)
class ProviderAttempt:
    id: str
    kind: ProviderAttemptKind
    provider_generation: int
    lease_id: str
    started_at_monotonic: float
    timeout_seconds: float


@dataclass(frozen=True, slots=True)
class CancelProviderAttempt:
    """Cancel an active lane; runtime must settle it by the deadline."""

    attempt_id: str
    provider_generation: int
    requested_at_monotonic: float
    settlement_timeout_seconds: float
    settlement_deadline_monotonic: float


TxEffect: TypeAlias = ProviderAttempt | CancelProviderAttempt


@dataclass(frozen=True, slots=True)
class TxSafetySnapshot:
    phase: TxPhase
    radio_tx: RadioTx
    provider_generation: int | None
    provider_ready: bool
    lease_id: str | None
    owner: TxOwner | None
    release_reason: TxReleaseReason | None
    terminal_release_reason: TxReleaseReason | None
    release_attempt_count: int
    release_last_error: str | None
    active_attempt: ProviderAttempt | None
    watchdog_deadline_monotonic: float | None
    # Configured *and* driven: a watchdog nothing ticks cannot fire, and this
    # field must never advertise one (MOR-1191).
    watchdog_enabled: bool
    external_conflict: bool


@dataclass(frozen=True, slots=True)
class TxTransition:
    outcome: TxOutcome
    snapshot: TxSafetySnapshot
    effects: tuple[TxEffect, ...] = ()


@dataclass(frozen=True, slots=True)
class _Lease:
    id: str
    owner: TxOwner


@dataclass(frozen=True, slots=True)
class _Boundary:
    generation: int
    sequence: int
    not_before: float

    def accepts(self, observation: ProviderPttObservation | None) -> bool:
        return observation is not None and (
            observation.provider_generation == self.generation
            and observation.ptt_observation_seq > self.sequence
            and observation.observed_at_monotonic >= self.not_before
        )


@dataclass(frozen=True, slots=True)
class _Release:
    requested_reason: TxReleaseReason
    terminal_reason: TxReleaseReason
    boundary: _Boundary
    attempts: int = 0
    retry_due: float | None = None
    error: str | None = None


_SYSTEM_RELEASE_REASONS = frozenset(
    {
        TxReleaseReason.AUDIO_FAILED,
        TxReleaseReason.CONTROL_TRANSPORT_LOST,
        TxReleaseReason.RADIO_TRANSPORT_LOST,
        TxReleaseReason.CAPABILITY_LOST,
        TxReleaseReason.PERMIT_LOST,
        TxReleaseReason.BACKEND_MAX_KEY_DOWN,
        TxReleaseReason.EXTERNAL_CAT_PREEMPTED,
        TxReleaseReason.APP_SHUTDOWN,
        TxReleaseReason.SERVER_SHUTDOWN,
        TxReleaseReason.RECOVERY_EXHAUSTED,
        TxReleaseReason.ADMIN_EMERGENCY_STOP,
    }
)

# Forcing an unkey is an operator act, so it carries one operator-attributed
# reason the system lane must never be able to claim: ``emergency_release``
# keeps rejecting ``OPERATOR_FORCED_UNKEY``, and ``force_unkey`` keeps
# rejecting ``OPERATOR_RELEASE``.
_FORCE_UNKEY_REASONS = _SYSTEM_RELEASE_REASONS | {TxReleaseReason.OPERATOR_FORCED_UNKEY}


class TxSafetySupervisor:
    """Owner, watchdog, causal confirmation, and durable OFF reducer."""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        id_factory: IdFactory | None = None,
        watchdog_seconds: float | None = BACKEND_MAX_KEY_DOWN_SECONDS,
        write_timeout_seconds: float = 2.0,
        read_timeout_seconds: float = 2.0,
        cancel_timeout_seconds: float = 0.5,
        retry_schedule_seconds: Sequence[float] = (0.25, 1.0, 2.0, 5.0),
    ) -> None:
        if watchdog_seconds is not None and (
            not isfinite(watchdog_seconds) or watchdog_seconds <= 0
        ):
            raise ValueError("watchdog_seconds must be finite-positive or None")
        timeouts = (write_timeout_seconds, read_timeout_seconds, cancel_timeout_seconds)
        if any(not isfinite(value) or value <= 0 for value in timeouts):
            raise ValueError("provider timeouts must be positive")
        retries = tuple(float(delay) for delay in retry_schedule_seconds)
        if (
            not retries
            or any(not isfinite(delay) or delay < 0 for delay in retries)
            or any(b < a for a, b in zip(retries, retries[1:]))
        ):
            raise ValueError("retry schedule must be non-empty and non-decreasing")

        self._clock = clock or time.monotonic
        self._id_factory = id_factory or (lambda: uuid.uuid4().hex)
        self._watchdog_seconds = watchdog_seconds
        self._write_timeout = float(write_timeout_seconds)
        self._read_timeout = float(read_timeout_seconds)
        self._cancel_timeout = float(cancel_timeout_seconds)
        self._retries = retries
        self._generation: int | None = None
        self._ready = False
        self._observation: ProviderPttObservation | None = None
        self._lease: _Lease | None = None
        self._acquisition: _Boundary | None = None
        self._release: _Release | None = None
        self._active: ProviderAttempt | None = None
        self._cancel_pending: CancelProviderAttempt | None = None
        self._watchdog_deadline: float | None = None
        self._driven = False

    @property
    def snapshot(self) -> TxSafetySnapshot:
        radio = self._observation.value if self._observation else RadioTx.UNKNOWN
        managed_on = bool(
            self._lease
            and not self._release
            and radio is RadioTx.ON
            and self._acquisition
        )
        confirmed = bool(
            managed_on
            and self._acquisition
            and self._acquisition.accepts(self._observation)
        )
        if self._release:
            if self._release.error:
                phase = TxPhase.FAULTED
            elif self._active and self._active.kind is ProviderAttemptKind.READ_PTT:
                phase = TxPhase.RELEASE_CONFIRMING
            else:
                phase = TxPhase.RELEASE_REQUIRED
        elif not self._lease:
            phase = TxPhase.EXTERNAL_UNOWNED if radio is RadioTx.ON else TxPhase.IDLE
        else:
            phase = TxPhase.KEYED if confirmed else TxPhase.KEY_PENDING
        return TxSafetySnapshot(
            phase=phase,
            radio_tx=radio,
            provider_generation=self._generation,
            provider_ready=self._ready,
            lease_id=self._lease.id if self._lease else None,
            owner=self._lease.owner if self._lease else None,
            release_reason=self._release.requested_reason if self._release else None,
            terminal_release_reason=(
                self._release.terminal_reason if self._release else None
            ),
            release_attempt_count=self._release.attempts if self._release else 0,
            release_last_error=self._release.error if self._release else None,
            active_attempt=self._active,
            watchdog_deadline_monotonic=self._watchdog_deadline,
            watchdog_enabled=self._watchdog_seconds is not None and self._driven,
            external_conflict=managed_on and not confirmed,
        )

    def replace_provider(self, generation: int, *, ready: bool) -> TxTransition:
        if generation < 0:
            raise ValueError("provider_generation must be non-negative")
        if self._generation is not None and generation <= self._generation:
            return self._result(TxOutcome.STALE)
        now = self._clock()
        had_lease = self._lease is not None
        self._generation, self._ready = generation, ready
        self._observation = None
        self._active = None
        self._cancel_pending = None
        self._acquisition = None
        if had_lease:
            if not self._release:
                self._begin_release(TxReleaseReason.CONTROL_TRANSPORT_LOST, now)
            assert self._release is not None
            self._release = replace(
                self._release, boundary=self._boundary(now), retry_due=now
            )
        return self._result(TxOutcome.APPLIED, self._service_release(force=ready))

    def set_provider_ready(self, generation: int, *, ready: bool) -> TxTransition:
        if generation != self._generation:
            return self._result(TxOutcome.STALE)
        if ready == self._ready:
            return self._result(TxOutcome.IDEMPOTENT)
        now = self._clock()
        self._ready = ready
        if not ready:
            self._observation = None
            if self._lease and not self._release:
                self._begin_release(TxReleaseReason.CONTROL_TRANSPORT_LOST, now)
            effects = self._cancel(self._active, now) if self._active else ()
            return self._result(TxOutcome.APPLIED, effects)
        return self._result(
            TxOutcome.APPLIED, self._service_release(force=True, now=now)
        )

    def observe_ptt(self, observation: ProviderPttObservation) -> TxTransition:
        """Record globally newer truth, then apply acquisition/release causality."""
        previous = self._observation
        if observation.provider_generation != self._generation or (
            previous is not None
            and (
                observation.ptt_observation_seq <= previous.ptt_observation_seq
                or observation.observed_at_monotonic < previous.observed_at_monotonic
            )
        ):
            return self._result(TxOutcome.STALE)
        self._observation = observation
        if (
            self._release
            and observation.value is RadioTx.OFF
            and self._release.boundary.accepts(observation)
        ):
            self._clear_managed()
        return self._result(TxOutcome.APPLIED)

    def request_on(self, owner: TxOwner) -> TxTransition:
        """Acquire once and emit the only WRITE_ON this lease can produce."""
        if self._release:
            return self._result(TxOutcome.RELEASE_PENDING)
        if self._lease:
            return self._result(
                TxOutcome.IDEMPOTENT if self._lease.owner == owner else TxOutcome.BUSY
            )
        if self._active:
            return self._result(TxOutcome.BUSY)
        if not self._ready or self._generation is None:
            return self._result(TxOutcome.NOT_READY)
        if not self._observation or self._observation.value is not RadioTx.OFF:
            return self._result(TxOutcome.RADIO_NOT_OFF)
        now = self._clock()
        self._lease = _Lease(self._id_factory(), owner)
        self._acquisition = self._boundary(now)
        self._watchdog_deadline = (
            now + self._watchdog_seconds if self._watchdog_seconds else None
        )
        return self._result(
            TxOutcome.ACCEPTED,
            (self._start(ProviderAttemptKind.WRITE_ON, self._write_timeout, now),),
        )

    def request_off(
        self,
        owner: TxOwner,
        lease_id: str,
        *,
        reason: TxReleaseReason = TxReleaseReason.OPERATOR_RELEASE,
    ) -> TxTransition:
        if not self._lease or (self._lease.owner, self._lease.id) != (owner, lease_id):
            return self._result(TxOutcome.STALE)
        return self._release_current(reason)

    def release_owner(self, owner: TxOwner, *, reason: TxReleaseReason) -> TxTransition:
        if not self._lease or self._lease.owner != owner:
            return self._result(TxOutcome.STALE)
        return self._release_current(reason)

    def emergency_release(self, *, reason: TxReleaseReason) -> TxTransition:
        if reason not in _SYSTEM_RELEASE_REASONS:
            raise ValueError("unqualified release requires a trusted system reason")
        return (
            self._release_current(reason)
            if self._lease
            else self._result(TxOutcome.NOOP)
        )

    def force_unkey(self, owner: TxOwner, *, reason: TxReleaseReason) -> TxTransition:
        """Adopt an unowned key so the durable OFF has a lease to run under.

        The minted lease is a release obligation, never a key-down: it exists
        only so the OFF inherits the retry ladder, generation-change survival
        and readiness-loss parking every managed release already gets, and it
        self-clears on the confirming observation.

        This must never gate on ``TxPhase.EXTERNAL_UNOWNED`` nor require a
        fresh PTT read first. A supervisor in a freshly started process has no
        observation at all, so it reports ``IDLE``/``UNKNOWN`` while the rig is
        keyed -- exactly the case this exists for (MOR-1182) -- and the read
        that would settle the question may be broken by the same fault. Live
        leases are refused rather than preempted; preemption is MOR-1179's
        territory, reached by exposing ``emergency_release``.
        """
        if reason not in _FORCE_UNKEY_REASONS:
            raise ValueError("forced unkey requires a trusted or operator reason")
        if self._release is not None:
            # A durable OFF is already owed; routing this through
            # ``_release_current`` would rewrite a foreign terminal reason.
            return self._result(TxOutcome.IDEMPOTENT)
        if self._lease is not None:
            return self._result(TxOutcome.BUSY)
        if self._active is not None:
            return self._result(TxOutcome.BUSY)
        if not self._ready or self._generation is None:
            return self._result(TxOutcome.NOT_READY)
        now = self._clock()
        self._lease = _Lease(self._id_factory(), owner)
        self._begin_release(reason, now)
        return self._result(
            TxOutcome.ACCEPTED, self._service_release(force=True, now=now)
        )

    def settle_attempt(
        self,
        attempt_id: str,
        provider_generation: int,
        *,
        succeeded: bool,
        error: str | None = None,
    ) -> TxTransition:
        attempt = self._active
        if (
            not attempt
            or (attempt.id, attempt.provider_generation)
            != (attempt_id, provider_generation)
            or provider_generation != self._generation
        ):
            return self._result(TxOutcome.STALE)
        self._active = None
        self._cancel_pending = None
        now = self._clock()
        if not self._lease:
            return self._result(TxOutcome.APPLIED)

        if attempt.kind is ProviderAttemptKind.WRITE_ON:
            if not self._release and not succeeded:
                self._begin_release(
                    TxReleaseReason.CONTROL_TRANSPORT_LOST,
                    now,
                    error or "write_on_failed",
                )
            if self._release:
                return self._result(
                    TxOutcome.APPLIED, self._service_release(force=True, now=now)
                )
            return self._result(
                TxOutcome.APPLIED,
                (self._start(ProviderAttemptKind.READ_PTT, self._read_timeout, now),),
            )

        if not self._release:
            if attempt.kind is ProviderAttemptKind.READ_PTT and not succeeded:
                self._begin_release(
                    TxReleaseReason.CONTROL_TRANSPORT_LOST,
                    now,
                    error or "read_ptt_failed",
                )
                return self._result(
                    TxOutcome.APPLIED, self._service_release(force=True, now=now)
                )
            return self._result(TxOutcome.APPLIED)

        if self._release.attempts == 0:
            return self._result(
                TxOutcome.APPLIED, self._service_release(force=True, now=now)
            )
        if attempt.kind is ProviderAttemptKind.WRITE_OFF:
            if not succeeded:
                self._schedule_retry(error or "write_off_failed")
                return self._result(TxOutcome.APPLIED)
            return self._result(
                TxOutcome.APPLIED,
                (self._start(ProviderAttemptKind.READ_PTT, self._read_timeout, now),),
            )
        self._schedule_retry(
            error or ("read_ptt_unconfirmed" if succeeded else "read_ptt_failed")
        )
        return self._result(TxOutcome.APPLIED)

    def tick(self) -> TxTransition:
        self._driven = True
        now = self._clock()
        effects: tuple[TxEffect, ...] = ()
        if (
            self._lease
            and not self._release
            and self._watchdog_deadline is not None
            and now >= self._watchdog_deadline
        ):
            active = self._active
            self._begin_release(TxReleaseReason.BACKEND_MAX_KEY_DOWN, now)
            if active:
                effects = self._cancel(active, now)
        if not effects:
            effects = self._service_release(force=False, now=now)
        outcome = TxOutcome.APPLIED if effects or self._release else TxOutcome.NOOP
        return self._result(outcome, effects)

    def retire_driver(self) -> None:
        """The driver of ``tick`` is gone; stop claiming a watchdog with it."""
        self._driven = False

    def _release_current(self, reason: TxReleaseReason) -> TxTransition:
        if self._release:
            self._release = replace(self._release, terminal_reason=reason)
            return self._result(TxOutcome.IDEMPOTENT)
        now = self._clock()
        self._begin_release(reason, now)
        if self._active:
            return self._result(
                TxOutcome.ACCEPTED,
                self._cancel(self._active, now),
            )
        return self._result(TxOutcome.ACCEPTED, self._service_release(force=True))

    def _begin_release(
        self, reason: TxReleaseReason, now: float, error: str | None = None
    ) -> None:
        if not self._lease:
            return
        self._release = _Release(
            requested_reason=reason,
            terminal_reason=reason,
            boundary=self._boundary(now),
            retry_due=now,
            error=error,
        )
        self._watchdog_deadline = None

    def _service_release(
        self, *, force: bool, now: float | None = None
    ) -> tuple[TxEffect, ...]:
        release = self._release
        instant = self._clock() if now is None else now
        if (
            not release
            or not self._ready
            or self._generation is None
            or self._active
            or (
                not force and (release.retry_due is None or instant < release.retry_due)
            )
        ):
            return ()
        attempt = self._start(
            ProviderAttemptKind.WRITE_OFF, self._write_timeout, instant
        )
        self._release = replace(
            release, attempts=release.attempts + 1, retry_due=None, error=None
        )
        return (attempt,)

    def _schedule_retry(self, error: str) -> None:
        if not self._release:
            return
        index = max(self._release.attempts - 1, 0)
        delay = self._retries[min(index, len(self._retries) - 1)]
        self._release = replace(
            self._release, retry_due=self._clock() + delay, error=error
        )

    def _start(
        self, kind: ProviderAttemptKind, timeout: float, now: float
    ) -> ProviderAttempt:
        if self._active or self._generation is None or not self._lease:
            raise RuntimeError("provider attempt requires one generation and lease")
        attempt = ProviderAttempt(
            self._id_factory(),
            kind,
            self._generation,
            self._lease.id,
            now,
            timeout,
        )
        self._active = attempt
        self._cancel_pending = None
        return attempt

    def _cancel(
        self, attempt: ProviderAttempt, now: float
    ) -> tuple[CancelProviderAttempt, ...]:
        pending = self._cancel_pending
        if pending and (
            pending.attempt_id,
            pending.provider_generation,
        ) == (attempt.id, attempt.provider_generation):
            return ()
        pending = CancelProviderAttempt(
            attempt.id,
            attempt.provider_generation,
            now,
            self._cancel_timeout,
            now + self._cancel_timeout,
        )
        self._cancel_pending = pending
        return (pending,)

    def _boundary(self, now: float) -> _Boundary:
        if self._generation is None:
            raise RuntimeError("causal boundary requires provider generation")
        sequence = self._observation.ptt_observation_seq if self._observation else 0
        return _Boundary(self._generation, sequence, now)

    def _clear_managed(self) -> None:
        self._lease = self._acquisition = self._release = None
        self._watchdog_deadline = None

    def _result(
        self, outcome: TxOutcome, effects: Sequence[TxEffect] = ()
    ) -> TxTransition:
        return TxTransition(outcome, self.snapshot, tuple(effects))
