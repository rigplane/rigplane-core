"""Which field writes move the radio's TX target (MOR-3632)."""

from __future__ import annotations

from collections.abc import Iterable

from rigplane.core.state_pipeline_contracts import (
    FieldFamily,
    FieldPath,
    FieldScope,
)

__all__ = ["writes_tx_target_input"]

_TX_SPLIT_PATH = FieldPath.global_("tx_state", "split")
_ACTIVE_RECEIVER_PATH = FieldPath.global_("slow_state", "active")


def writes_tx_target_input(paths: Iterable[FieldPath]) -> bool:
    """Whether a write naming ``paths`` moves what the radio transmits on.

    The TX target is the frequency of the VFO that transmits, which the
    split flag and the VFO and receiver selection pick: a write of any
    receiver frequency (in any slot spelling), of the split flag, of the
    selected VFO or of the selected receiver moves it. A write only has to
    be recognised here, not located: the consequence
    (:meth:`StateStore.fence_tx_target`) withdraws the whole target until it
    is read back, never one input of it.
    """

    for path in paths:
        if path in (_TX_SPLIT_PATH, _ACTIVE_RECEIVER_PATH):
            return True
        if path.scope is not FieldScope.RECEIVER:
            continue
        if path.family is FieldFamily.FREQ_MODE and path.name == "freq_hz":
            return True
        if path.family is FieldFamily.VFO and path.name == "active_slot":
            return True
    return False
