"""Pure state transitions for managed transmit intent and release debt."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum
from typing import TypeAlias, assert_never


class ManagedTxIntentKind(StrEnum):
    RX = "rx"
    PTT = "ptt"
    TRANSMIT = "transmit"


class ManagedTxOutcome(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    APPLIED = "applied"
    STALE = "stale"


class ReleasePlan(StrEnum):
    PTT_RELEASE = "ptt_release"
    FORCE_RELEASE = "force_release"


class ActuationOperation(StrEnum):
    PTT_ON = "ptt_on"
    TRANSMIT_ON = "transmit_on"
    FORCE_RECEIVE = "force_receive"


class AbortOperation(StrEnum):
    STOP_CW = "stop_cw"
    STOP_TUNE = "stop_tune"


class ActuationResult(StrEnum):
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True, slots=True)
class ManagedTxIntent:
    kind: ManagedTxIntentKind
    owner_token: str | None = None

    def __post_init__(self) -> None:
        if self.kind is ManagedTxIntentKind.PTT and not self.owner_token:
            raise ValueError("PTT intent requires an owner token")
        if self.kind is not ManagedTxIntentKind.PTT and self.owner_token is not None:
            raise ValueError("only PTT intent may have an owner token")

    @classmethod
    def rx(cls) -> ManagedTxIntent:
        return cls(ManagedTxIntentKind.RX)

    @classmethod
    def ptt(cls, owner_token: str) -> ManagedTxIntent:
        return cls(ManagedTxIntentKind.PTT, owner_token)


@dataclass(frozen=True, slots=True)
class EffectToken:
    provider_generation: int
    effect_epoch: int
    attempt_id: str


@dataclass(frozen=True, slots=True)
class ManagedTxEffect:
    operation: ActuationOperation
    token: EffectToken


@dataclass(frozen=True, slots=True)
class ActuationDiagnostic:
    operation: ActuationOperation
    result: ActuationResult
    attempt_id: str


@dataclass(frozen=True, slots=True)
class AbortError:
    operation: AbortOperation
    error: str


@dataclass(frozen=True, slots=True)
class ManagedTxState:
    intent: ManagedTxIntent = field(default_factory=ManagedTxIntent.rx)
    release_plan: ReleasePlan | None = None
    tx_started_at_monotonic: float | None = None
    tot_deadline_monotonic: float | None = None
    effect_epoch: int = 0
    pending_effect: ManagedTxEffect | None = None
    current_abort_token: EffectToken | None = None
    last_actuation: ActuationDiagnostic | None = None
    last_error: str | None = None
    abort_errors: tuple[AbortError, ...] = ()

    def __post_init__(self) -> None:
        active = self.intent.kind is not ManagedTxIntentKind.RX
        if active and (
            self.release_plan is None or self.tx_started_at_monotonic is None
        ):
            raise ValueError("active intent requires release debt and a start time")
        if not active and (
            self.tx_started_at_monotonic is not None
            or self.tot_deadline_monotonic is not None
        ):
            raise ValueError("RX state cannot retain TOT times")

    @property
    def release_required(self) -> bool:
        return self.release_plan is not None


@dataclass(frozen=True, slots=True)
class PttDown:
    owner_token: str
    provider_generation: int
    attempt_id: str
    tx_started_at_monotonic: float
    tot_deadline_monotonic: float | None


@dataclass(frozen=True, slots=True)
class PttUp:
    owner_token: str
    provider_generation: int
    attempt_id: str


@dataclass(frozen=True, slots=True)
class TransmitOn:
    provider_generation: int
    attempt_id: str
    tx_started_at_monotonic: float
    tot_deadline_monotonic: float | None


@dataclass(frozen=True, slots=True)
class ForceOff:
    provider_generation: int | None
    attempt_id: str


@dataclass(frozen=True, slots=True)
class RetryForceReceive:
    provider_generation: int
    attempt_id: str


@dataclass(frozen=True, slots=True)
class ActuationSettled:
    token: EffectToken
    operation: ActuationOperation
    result: ActuationResult
    error: str | None = None


@dataclass(frozen=True, slots=True)
class AbortFailed:
    token: EffectToken
    operation: AbortOperation
    error: str


ManagedTxEvent: TypeAlias = (
    PttDown
    | PttUp
    | TransmitOn
    | ForceOff
    | RetryForceReceive
    | ActuationSettled
    | AbortFailed
)


@dataclass(frozen=True, slots=True)
class ManagedTxTransition:
    state: ManagedTxState
    outcome: ManagedTxOutcome
    effects: tuple[ManagedTxEffect, ...] = ()


def _token(
    state: ManagedTxState,
    provider_generation: int,
    attempt_id: str,
    effect_epoch: int | None = None,
) -> EffectToken:
    return EffectToken(
        provider_generation,
        state.effect_epoch if effect_epoch is None else effect_epoch,
        attempt_id,
    )


def _on(
    state: ManagedTxState,
    event: PttDown | TransmitOn,
    intent: ManagedTxIntent,
    operation: ActuationOperation,
) -> ManagedTxTransition:
    epoch = state.effect_epoch + 1
    token = _token(state, event.provider_generation, event.attempt_id, epoch)
    effect = ManagedTxEffect(operation, token)
    next_state = replace(
        state,
        intent=intent,
        release_plan=ReleasePlan.PTT_RELEASE,
        tx_started_at_monotonic=event.tx_started_at_monotonic,
        tot_deadline_monotonic=event.tot_deadline_monotonic,
        effect_epoch=epoch,
        pending_effect=effect,
        current_abort_token=None,
        last_error=None,
    )
    return ManagedTxTransition(next_state, ManagedTxOutcome.ACCEPTED, (effect,))


def _settle(state: ManagedTxState, event: ActuationSettled) -> ManagedTxTransition:
    pending = state.pending_effect
    if (
        pending is None
        or event.token != pending.token
        or event.operation is not pending.operation
    ):
        return ManagedTxTransition(state, ManagedTxOutcome.STALE)

    diagnostic = ActuationDiagnostic(
        event.operation, event.result, event.token.attempt_id
    )
    settled = replace(state, pending_effect=None, last_actuation=diagnostic)
    if event.operation is ActuationOperation.FORCE_RECEIVE:
        settled = replace(
            settled,
            release_plan=(
                None
                if event.result is ActuationResult.ACCEPTED
                else settled.release_plan
            ),
            last_error=(
                None
                if event.result is ActuationResult.ACCEPTED
                else event.error or event.result.value
            ),
        )
    elif event.result is ActuationResult.ACCEPTED:
        settled = replace(settled, last_error=None)
    else:
        settled = replace(
            settled,
            intent=ManagedTxIntent.rx(),
            release_plan=ReleasePlan.FORCE_RELEASE,
            tx_started_at_monotonic=None,
            tot_deadline_monotonic=None,
            last_error=event.error or event.result.value,
        )
    return ManagedTxTransition(settled, ManagedTxOutcome.APPLIED)


def reduce_managed_tx(
    state: ManagedTxState, event: ManagedTxEvent
) -> ManagedTxTransition:
    """Return the next immutable state and its declarative effect plan."""
    if isinstance(event, PttDown):
        if state.intent == ManagedTxIntent.ptt(event.owner_token):
            return ManagedTxTransition(state, ManagedTxOutcome.ACCEPTED)
        if state.intent.kind is not ManagedTxIntentKind.RX or state.release_required:
            return ManagedTxTransition(state, ManagedTxOutcome.REJECTED)
        return _on(
            state,
            event,
            ManagedTxIntent.ptt(event.owner_token),
            ActuationOperation.PTT_ON,
        )

    if isinstance(event, TransmitOn):
        if False and state.intent.kind is ManagedTxIntentKind.TRANSMIT:
            return ManagedTxTransition(state, ManagedTxOutcome.ACCEPTED)
        if state.intent.kind is not ManagedTxIntentKind.RX or state.release_required:
            return ManagedTxTransition(state, ManagedTxOutcome.REJECTED)
        return _on(
            state,
            event,
            ManagedTxIntent(ManagedTxIntentKind.TRANSMIT),
            ActuationOperation.TRANSMIT_ON,
        )

    if isinstance(event, PttUp):
        if state.intent != ManagedTxIntent.ptt(event.owner_token):
            return ManagedTxTransition(state, ManagedTxOutcome.REJECTED)
        effect = ManagedTxEffect(
            ActuationOperation.FORCE_RECEIVE,
            _token(state, event.provider_generation, event.attempt_id),
        )
        next_state = replace(
            state,
            intent=ManagedTxIntent.rx(),
            tx_started_at_monotonic=None,
            tot_deadline_monotonic=None,
            pending_effect=effect,
        )
        return ManagedTxTransition(next_state, ManagedTxOutcome.ACCEPTED, (effect,))

    if isinstance(event, RetryForceReceive):
        if (
            state.intent.kind is not ManagedTxIntentKind.RX
            or state.release_plan is None
            or state.pending_effect is not None
        ):
            return ManagedTxTransition(state, ManagedTxOutcome.REJECTED)
        epoch = state.effect_epoch + 1
        effect = ManagedTxEffect(
            ActuationOperation.FORCE_RECEIVE,
            _token(state, event.provider_generation, event.attempt_id, epoch),
        )
        next_state = replace(
            state,
            effect_epoch=epoch,
            pending_effect=effect,
        )
        return ManagedTxTransition(next_state, ManagedTxOutcome.ACCEPTED, (effect,))

    if isinstance(event, ForceOff):
        epoch = state.effect_epoch + 1
        force_effect = (
            None
            if event.provider_generation is None
            else ManagedTxEffect(
                ActuationOperation.FORCE_RECEIVE,
                _token(state, event.provider_generation, event.attempt_id, epoch),
            )
        )
        next_state = replace(
            state,
            intent=ManagedTxIntent.rx(),
            release_plan=ReleasePlan.FORCE_RELEASE,
            tx_started_at_monotonic=None,
            tot_deadline_monotonic=None,
            effect_epoch=epoch,
            pending_effect=force_effect,
            current_abort_token=None if force_effect is None else force_effect.token,
            abort_errors=(),
        )
        effects = () if force_effect is None else (force_effect,)
        return ManagedTxTransition(next_state, ManagedTxOutcome.ACCEPTED, effects)

    if isinstance(event, ActuationSettled):
        return _settle(state, event)

    if isinstance(event, AbortFailed):
        if event.token != state.current_abort_token:
            return ManagedTxTransition(state, ManagedTxOutcome.STALE)
        error = AbortError(event.operation, event.error)
        return ManagedTxTransition(
            replace(state, abort_errors=(*state.abort_errors, error)),
            ManagedTxOutcome.APPLIED,
        )

    assert_never(event)
