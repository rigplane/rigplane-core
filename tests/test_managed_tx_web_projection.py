"""Pure public projection for one managed-transmit authority snapshot."""

from datetime import UTC, datetime, timedelta, timezone

import pytest

from rigplane.core.tx_observation import ObservedPtt
from rigplane.runtime.managed_tx_authority import ManagedTxProjection
from rigplane.runtime.managed_tx_state import (
    AbortError,
    AbortOperation,
    ActuationDiagnostic,
    ActuationOperation,
    ActuationResult,
    ManagedTxIntent,
    ManagedTxIntentKind,
    ManagedTxState,
    ReleasePlan,
)
from rigplane.web.managed_tx_view import build_managed_tx_view


_SAMPLED_AT = datetime(2026, 9, 4, 12, 34, 56, 789_000, tzinfo=UTC)


def _projection(
    state: ManagedTxState | None = None,
    *,
    configured_tot_seconds: float | None = 180.0,
    remaining_tot_seconds: float | None = None,
) -> ManagedTxProjection:
    return ManagedTxProjection(
        state or ManagedTxState(),
        configured_tot_seconds,
        remaining_tot_seconds,
        provider_generation=123,
    )


def _active_state() -> ManagedTxState:
    return ManagedTxState(
        intent=ManagedTxIntent(ManagedTxIntentKind.TRANSMIT),
        release_plan=ReleasePlan.PTT_RELEASE,
        tx_started_at_monotonic=10.0,
        tot_deadline_monotonic=55.0,
    )


def test_ptt_snapshot_serializes_the_authority_state_and_separate_observation() -> None:
    state = ManagedTxState(
        intent=ManagedTxIntent.ptt("opaque-owner-token"),
        release_plan=ReleasePlan.PTT_RELEASE,
        tx_started_at_monotonic=10.0,
        tot_deadline_monotonic=55.0,
    )

    assert build_managed_tx_view(
        _projection(state, remaining_tot_seconds=42.5009),
        ObservedPtt.UNKNOWN,
        sampled_at=_SAMPLED_AT,
    ) == {
        "schemaVersion": 1,
        "sampledAt": "2026-09-04T12:34:56.789Z",
        "managedTransmit": {
            "status": "available",
            "intent": {"kind": "ptt", "owner": "opaque-owner-token"},
            "releaseRequired": True,
            "lastError": None,
            "lastActuation": None,
            "abortErrors": [],
            "tot": {
                "configuredSeconds": 180.0,
                "active": True,
                "remainingMs": 42500,
                "expiresAt": "2026-09-04T12:35:39.289Z",
            },
        },
        "txObservation": {"observedPtt": "unknown"},
    }


def test_rx_release_debt_is_not_inferred_from_observed_ptt() -> None:
    state = ManagedTxState(release_plan=ReleasePlan.FORCE_RELEASE)

    assert build_managed_tx_view(
        _projection(state), ObservedPtt.OFF, sampled_at=_SAMPLED_AT
    )["managedTransmit"] == {
        "status": "available",
        "intent": {"kind": "rx"},
        "releaseRequired": True,
        "lastError": None,
        "lastActuation": None,
        "abortErrors": [],
        "tot": {
            "configuredSeconds": 180.0,
            "active": False,
            "remainingMs": None,
            "expiresAt": None,
        },
    }


@pytest.mark.parametrize("observed_ptt", list(ObservedPtt))
def test_observation_changes_only_the_diagnostic_block(
    observed_ptt: ObservedPtt,
) -> None:
    view = build_managed_tx_view(_projection(), observed_ptt, sampled_at=_SAMPLED_AT)

    assert view["managedTransmit"] == {
        "status": "available",
        "intent": {"kind": "rx"},
        "releaseRequired": False,
        "lastError": None,
        "lastActuation": None,
        "abortErrors": [],
        "tot": {
            "configuredSeconds": 180.0,
            "active": False,
            "remainingMs": None,
            "expiresAt": None,
        },
    }
    assert view["txObservation"] == {"observedPtt": observed_ptt.value}


def test_diagnostics_preserve_normalized_values_and_hide_internal_tokens() -> None:
    state = ManagedTxState(
        intent=ManagedTxIntent(ManagedTxIntentKind.TRANSMIT),
        release_plan=ReleasePlan.PTT_RELEASE,
        tx_started_at_monotonic=10.0,
        last_actuation=ActuationDiagnostic(
            ActuationOperation.TRANSMIT_ON,
            ActuationResult.UNCERTAIN,
            "opaque-attempt-id",
        ),
        last_error="provider error text",
        abort_errors=(
            AbortError(AbortOperation.STOP_CW, "cw error"),
            AbortError(AbortOperation.STOP_TUNE, "tune error"),
        ),
    )

    view = build_managed_tx_view(
        _projection(state, configured_tot_seconds=None),
        ObservedPtt.ON,
        sampled_at=_SAMPLED_AT,
    )

    assert view["managedTransmit"] == {
        "status": "available",
        "intent": {"kind": "transmit"},
        "releaseRequired": True,
        "lastError": "provider error text",
        "lastActuation": {
            "operation": "transmit_on",
            "result": "uncertain",
            "attemptId": "opaque-attempt-id",
        },
        "abortErrors": [
            {"operation": "stop_cw", "error": "cw error"},
            {"operation": "stop_tune", "error": "tune error"},
        ],
        "tot": {
            "configuredSeconds": None,
            "active": False,
            "remainingMs": None,
            "expiresAt": None,
        },
    }
    assert "providerGeneration" not in str(view)
    assert "effectEpoch" not in str(view)


@pytest.mark.parametrize(
    ("state", "remaining_tot_seconds"),
    [
        (_active_state(), None),
        (ManagedTxState(), 1.0),
    ],
)
def test_mismatched_tot_deadline_and_remaining_time_are_rejected(
    state: ManagedTxState, remaining_tot_seconds: float | None
) -> None:
    with pytest.raises(ValueError, match="TOT deadline and remaining time disagree"):
        build_managed_tx_view(
            _projection(state, remaining_tot_seconds=remaining_tot_seconds),
            ObservedPtt.UNKNOWN,
            sampled_at=_SAMPLED_AT,
        )


@pytest.mark.parametrize(
    ("remaining_tot_seconds", "remaining_ms", "expires_at"),
    [
        (0.0, 0, "2026-09-04T12:34:56.789Z"),
        (-0.0001, 0, "2026-09-04T12:34:56.789Z"),
        (604800.9999, 604800999, "2026-09-11T12:34:56.788Z"),
    ],
)
def test_active_tot_clamps_expired_time_and_preserves_large_remaining_time(
    remaining_tot_seconds: float, remaining_ms: int, expires_at: str
) -> None:
    tot = build_managed_tx_view(
        _projection(_active_state(), remaining_tot_seconds=remaining_tot_seconds),
        ObservedPtt.UNKNOWN,
        sampled_at=_SAMPLED_AT,
    )["managedTransmit"]["tot"]  # type: ignore[index]

    assert tot == {
        "configuredSeconds": 180.0,
        "active": True,
        "remainingMs": remaining_ms,
        "expiresAt": expires_at,
    }


def test_naive_sample_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="sampled_at must be timezone-aware"):
        build_managed_tx_view(
            _projection(),
            ObservedPtt.UNKNOWN,
            sampled_at=datetime(2026, 9, 4, 12, 34, 56, 789_000),
        )


def test_non_utc_sample_time_converts_sample_and_expiry_to_utc() -> None:
    view = build_managed_tx_view(
        _projection(_active_state(), remaining_tot_seconds=1.0),
        ObservedPtt.UNKNOWN,
        sampled_at=datetime(
            2026,
            9,
            4,
            8,
            34,
            56,
            789_000,
            tzinfo=timezone(-timedelta(hours=4)),
        ),
    )

    assert view["sampledAt"] == "2026-09-04T12:34:56.789Z"
    assert view["managedTransmit"]["tot"]["expiresAt"] == "2026-09-04T12:34:57.789Z"  # type: ignore[index]
