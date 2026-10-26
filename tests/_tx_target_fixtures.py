"""Explicit canonical TX-target observations for tests of other TX policies."""

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.core.tx_target import KnownTxTarget, TxReceiver, TxSlot


def observe_tx_target(
    store: StateStore,
    frequency_hz: int,
    *,
    receiver: TxReceiver = "MAIN",
    slot: TxSlot | None = None,
    max_age: float = 60.0,
) -> None:
    store.apply_current(
        Observation(
            path=FieldPath.global_("tx_state", "tx_target"),
            value=KnownTxTarget(receiver, slot, frequency_hz),
            source=SourceMetadata(source="poll_response", provider="test_fixture"),
            timestamp_monotonic=store.snapshot().generated_at_monotonic,
            max_age=max_age,
        )
    )
