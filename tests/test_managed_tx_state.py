from __future__ import annotations

from dataclasses import fields, replace
from typing import get_args

import pytest

import rigplane.runtime.managed_tx_state as subject
from rigplane.runtime.managed_tx_state import (
    AbortFailed,
    AbortError,
    AbortOperation,
    ActuationDiagnostic,
    ActuationOperation,
    ActuationResult,
    ActuationSettled,
    ForceOff,
    ManagedTxEvent,
    ManagedTxIntent,
    ManagedTxIntentKind,
    ManagedTxOutcome,
    ManagedTxState,
    PttDown,
    PttUp,
    ReleasePlan,
    TransmitOn,
    reduce_managed_tx,
)


def ptt_down(owner: str = "web-1", attempt: str = "on-1") -> PttDown:
    return PttDown(owner, 7, attempt, 10.0, 190.0)


def keyed() -> ManagedTxState:
    return reduce_managed_tx(ManagedTxState(), ptt_down()).state


def force_off(state: ManagedTxState | None = None, attempt: str = "off-1"):
    return reduce_managed_tx(state or ManagedTxState(), ForceOff(9, attempt))


def settle(
    state: ManagedTxState,
    result: ActuationResult,
    *,
    operation: ActuationOperation | None = None,
    error: str | None = "failed",
):
    pending = state.pending_effect
    assert pending is not None
    return reduce_managed_tx(
        state,
        ActuationSettled(pending.token, operation or pending.operation, result, error),
    )


def test_state_vocabulary_cannot_encode_invalid_authority_or_debt() -> None:
    assert ManagedTxIntent.rx() == ManagedTxIntent(ManagedTxIntentKind.RX)
    with pytest.raises(ValueError):
        ManagedTxIntent(ManagedTxIntentKind.PTT)
    with pytest.raises(ValueError):
        ManagedTxIntent(ManagedTxIntentKind.TRANSMIT, "owner")
    with pytest.raises(ValueError):
        ManagedTxState(intent=ManagedTxIntent.ptt("owner"))
    assert "release_required" not in {field.name for field in fields(ManagedTxState)}


def test_observation_is_structurally_not_an_authority_event() -> None:
    assert not hasattr(subject, "ObservedPtt")
    assert set(get_args(ManagedTxEvent)) == {
        PttDown,
        PttUp,
        TransmitOn,
        ForceOff,
        ActuationSettled,
        AbortFailed,
    }
    assert "IGNORED" not in ManagedTxOutcome.__members__


def test_ptt_down_records_debt_before_returning_non_optional_effect() -> None:
    result = reduce_managed_tx(ManagedTxState(), ptt_down())
    assert result.outcome is ManagedTxOutcome.ACCEPTED
    assert result.state.intent == ManagedTxIntent.ptt("web-1")
    assert result.state.release_required is True
    assert result.state.release_plan is ReleasePlan.PTT_RELEASE
    assert result.state.tx_started_at_monotonic == 10.0
    assert result.state.tot_deadline_monotonic == 190.0
    assert result.effects == (result.state.pending_effect,)
    effect = result.effects[0]
    assert effect.operation is ActuationOperation.PTT_ON
    assert effect.token.effect_epoch == result.state.effect_epoch == 0


@pytest.mark.parametrize(
    "event", [ptt_down(owner="web-2"), TransmitOn(7, "tx", 20, 200)]
)
def test_incompatible_on_is_rejected_while_ptt_active(event: ManagedTxEvent) -> None:
    state = keyed()
    result = reduce_managed_tx(state, event)
    assert result.outcome is ManagedTxOutcome.REJECTED
    assert result.state == state
    assert result.effects == ()


def test_idempotent_on_preserves_tot_debt_pending_effect_and_diagnostics() -> None:
    for event in (ptt_down(attempt="again"), TransmitOn(7, "again", 90, 270)):
        first = (
            keyed()
            if isinstance(event, PttDown)
            else reduce_managed_tx(
                ManagedTxState(), TransmitOn(7, "first", 10, 190)
            ).state
        )
        marked = replace(first, last_error="old")
        result = reduce_managed_tx(marked, event)
        assert result.outcome is ManagedTxOutcome.ACCEPTED
        assert result.state == marked
        assert result.effects == ()


@pytest.mark.parametrize(
    "state,owner",
    [
        (ManagedTxState(), "web-1"),
        (keyed(), "web-2"),
        (
            reduce_managed_tx(ManagedTxState(), TransmitOn(7, "tx", 10, 190)).state,
            "web-1",
        ),
    ],
)
def test_ptt_up_requires_the_current_ptt_owner(
    state: ManagedTxState, owner: str
) -> None:
    result = reduce_managed_tx(state, PttUp(owner, 7, "off"))
    assert result.outcome is ManagedTxOutcome.REJECTED
    assert result.state == state


def test_matching_ptt_up_returns_rx_with_release_effect_and_debt() -> None:
    result = reduce_managed_tx(keyed(), PttUp("web-1", 7, "off"))
    assert result.state.intent == ManagedTxIntent.rx()
    assert result.state.release_plan is ReleasePlan.PTT_RELEASE
    assert result.state.release_required is True
    assert result.state.tx_started_at_monotonic is None
    assert result.effects == (result.state.pending_effect,)
    assert result.effects[0].operation is ActuationOperation.FORCE_RECEIVE


def test_on_is_rejected_while_release_debt_is_outstanding() -> None:
    state = ManagedTxState(release_plan=ReleasePlan.FORCE_RELEASE)
    result = reduce_managed_tx(state, TransmitOn(1, "tx", 10, 190))
    assert result.outcome is ManagedTxOutcome.REJECTED
    assert result.state == state


def test_force_off_always_fences_debt_and_repeated_call_replaces_effect() -> None:
    error = AbortError(AbortOperation.STOP_CW, "cw")
    state = replace(keyed(), abort_errors=(error,))
    first = reduce_managed_tx(state, ForceOff(None, "offline"))
    second = reduce_managed_tx(first.state, ForceOff(9, "online"))
    assert first.state.release_plan is ReleasePlan.FORCE_RELEASE
    assert first.state.release_required is True
    assert first.state.intent == ManagedTxIntent.rx()
    assert first.state.pending_effect is None
    assert first.effects == ()
    assert first.state.abort_errors == ()
    assert second.state.effect_epoch == state.effect_epoch + 2
    assert second.effects == (second.state.pending_effect,)
    assert second.effects[0].token.attempt_id == "online"


@pytest.mark.parametrize(
    "field,value",
    [("provider_generation", 8), ("effect_epoch", 0), ("attempt_id", "old")],
)
def test_stale_token_cannot_settle_or_clear_debt(field: str, value: object) -> None:
    state = force_off().state
    pending = state.pending_effect
    assert pending is not None
    token = replace(pending.token, **{field: value})
    result = reduce_managed_tx(
        state,
        ActuationSettled(
            token,
            ActuationOperation.FORCE_RECEIVE,
            ActuationResult.ACCEPTED,
        ),
    )
    assert result.outcome is ManagedTxOutcome.STALE
    assert result.state == state


def test_current_token_with_wrong_operation_is_stale_and_keeps_debt() -> None:
    state = force_off().state
    wrong = ActuationOperation.PTT_ON
    result = settle(state, ActuationResult.ACCEPTED, operation=wrong)
    assert result.outcome is ManagedTxOutcome.STALE
    assert result.state == state


@pytest.mark.parametrize("result", list(ActuationResult))
def test_force_receive_results_have_exact_debt_and_diagnostics(
    result: ActuationResult,
) -> None:
    prior = ActuationDiagnostic(
        ActuationOperation.PTT_ON, ActuationResult.ACCEPTED, "old"
    )
    state = replace(force_off().state, last_actuation=prior, last_error="old")
    settled = settle(state, result)
    accepted = result is ActuationResult.ACCEPTED
    assert settled.outcome is ManagedTxOutcome.APPLIED
    assert settled.state.release_required is not accepted
    assert settled.state.pending_effect is None
    assert settled.state.last_actuation == ActuationDiagnostic(
        ActuationOperation.FORCE_RECEIVE, result, "off-1"
    )
    assert settled.state.last_error == (None if accepted else "failed")


@pytest.mark.parametrize(
    "operation", [ActuationOperation.PTT_ON, ActuationOperation.TRANSMIT_ON]
)
@pytest.mark.parametrize("result", list(ActuationResult))
def test_on_results_have_exact_state_deltas(
    operation: ActuationOperation, result: ActuationResult
) -> None:
    state = (
        keyed()
        if operation is ActuationOperation.PTT_ON
        else reduce_managed_tx(ManagedTxState(), TransmitOn(7, "on-1", 10, 190)).state
    )
    settled = settle(state, result)
    accepted = result is ActuationResult.ACCEPTED
    assert settled.state.intent == (state.intent if accepted else ManagedTxIntent.rx())
    assert settled.state.release_required is True
    assert settled.state.release_plan is (
        ReleasePlan.PTT_RELEASE if accepted else ReleasePlan.FORCE_RELEASE
    )
    assert settled.state.tx_started_at_monotonic == (10 if accepted else None)
    assert settled.state.tot_deadline_monotonic == (190 if accepted else None)
    assert settled.state.last_error == (None if accepted else "failed")
    assert settled.state.last_actuation == ActuationDiagnostic(
        operation, result, "on-1"
    )


def test_transition_diagnostic_reset_and_preservation_follow_field_table() -> None:
    settled = settle(keyed(), ActuationResult.REJECTED).state
    on = reduce_managed_tx(
        replace(settled, release_plan=None), TransmitOn(7, "new", 20, 200)
    ).state
    released = reduce_managed_tx(
        replace(on, last_error="keep"), ForceOff(None, "off")
    ).state
    assert on.last_error is None
    assert on.last_actuation == settled.last_actuation
    assert released.last_error == "keep"
    assert released.last_actuation == settled.last_actuation


@pytest.mark.parametrize("operation", list(AbortOperation))
def test_typed_abort_failures_are_diagnostic_only(operation: AbortOperation) -> None:
    state = force_off().state
    recorded = reduce_managed_tx(state, AbortFailed(operation, "stuck"))
    assert recorded.outcome is ManagedTxOutcome.APPLIED
    assert replace(recorded.state, abort_errors=()) == state
    assert recorded.state.abort_errors == (AbortError(operation, "stuck"),)
