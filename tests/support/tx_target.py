"""Shared fixture state for the PTT-on TX-target guard (MOR-3633).

The guard refuses PTT-on on a profile that publishes
``global.tx_state.tx_target`` until a FRESH, provider-generation-bound
observation of a known TX-side frequency sits in the store. A live radio
reaches that state through its poller; a fixture that builds a keyed-ready
radio without a poller must seed it, and must seed it exactly as the
production publisher does: bound to the store's current provider generation
and carrying a positive ``max_age``.
"""

from __future__ import annotations

import time

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.core.tx_target import KnownTxTarget, TxReceiver, TxSlot, TxTarget

TX_TARGET_PATH = FieldPath.global_("tx_state", "tx_target")

# Long enough that a fixture running on the real monotonic clock never sees the
# seeded target age out mid-test; a fixture that wants staleness drives its own
# manual ``FreshnessClock`` and passes a short ``max_age``.
SEEDED_TX_TARGET_MAX_AGE = 3600.0


def seed_tx_target_value(
    store: StateStore,
    value: TxTarget,
    *,
    timestamp_monotonic: float | None = None,
    max_age: float = SEEDED_TX_TARGET_MAX_AGE,
) -> None:
    """Publish ``value`` as the TX target, bound to the store's generation.

    ``apply_current`` stamps the store's present provider generation: a plain
    ``apply`` stamps generation 0, which a started server's rolled store
    silently drops.
    """

    store.apply_current(
        Observation(
            path=TX_TARGET_PATH,
            value=value,
            source=SourceMetadata(
                source="local_reconcile",
                provider="tests",
                native_id="tx_target_derivation",
            ),
            timestamp_monotonic=(
                time.monotonic() if timestamp_monotonic is None else timestamp_monotonic
            ),
            max_age=max_age,
        )
    )


def seed_tx_target(
    store: StateStore,
    frequency_hz: int | None = 14_074_000,
    *,
    receiver: TxReceiver = "MAIN",
    slot: TxSlot | None = "A",
    timestamp_monotonic: float | None = None,
    max_age: float = SEEDED_TX_TARGET_MAX_AGE,
) -> None:
    """Publish a FRESH known TX target: the state a keyed-ready radio sits on."""

    seed_tx_target_value(
        store,
        KnownTxTarget(receiver=receiver, slot=slot, frequency_hz=frequency_hz),
        timestamp_monotonic=timestamp_monotonic,
        max_age=max_age,
    )
