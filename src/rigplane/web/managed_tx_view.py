"""Normalized read-only view of one managed-transmit authority snapshot."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import floor
from typing import assert_never

from rigplane.core.tx_observation import ObservedPtt
from rigplane.runtime.managed_tx_authority import ManagedTxProjection
from rigplane.runtime.managed_tx_state import ManagedTxIntent, ManagedTxIntentKind

__all__ = ["build_managed_tx_view"]


def build_managed_tx_view(
    projection: ManagedTxProjection,
    observed_ptt: ObservedPtt,
    *,
    sampled_at: datetime,
) -> dict[str, object]:
    """Serialize one authority snapshot and independent observed PTT evidence."""

    state = projection.state
    active = (
        state.tot_deadline_monotonic is not None
        and projection.remaining_tot_seconds is not None
    )
    remaining_ms = (
        None if not active else max(0, floor(projection.remaining_tot_seconds * 1000))
    )
    sampled_at_text = _utc_milliseconds(sampled_at)
    return {
        "schemaVersion": 1,
        "sampledAt": sampled_at_text,
        "managedTransmit": {
            "status": "available",
            "intent": _intent_view(state.intent),
            "releaseRequired": state.release_required,
            "lastError": state.last_error,
            "lastActuation": (
                None
                if state.last_actuation is None
                else {
                    "operation": state.last_actuation.operation.value,
                    "result": state.last_actuation.result.value,
                    "attemptId": state.last_actuation.attempt_id,
                }
            ),
            "abortErrors": [
                {"operation": error.operation.value, "error": error.error}
                for error in state.abort_errors
            ],
            "tot": {
                "configuredSeconds": projection.configured_tot_seconds,
                "active": active,
                "remainingMs": remaining_ms,
                "expiresAt": (
                    None
                    if remaining_ms is None
                    else _utc_milliseconds(
                        sampled_at + timedelta(milliseconds=remaining_ms)
                    )
                ),
            },
        },
        "txObservation": {"observedPtt": observed_ptt.value},
    }


def _intent_view(intent: ManagedTxIntent) -> dict[str, str]:
    if intent.kind is ManagedTxIntentKind.RX:
        return {"kind": "rx"}
    if intent.kind is ManagedTxIntentKind.TRANSMIT:
        return {"kind": "transmit"}
    if intent.kind is ManagedTxIntentKind.PTT:
        assert intent.owner_token is not None
        return {"kind": "ptt", "owner": intent.owner_token}
    assert_never(intent.kind)


def _utc_milliseconds(value: datetime) -> str:
    return (
        value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    )
