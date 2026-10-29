"""Which field writes make the derived TX target wrong (MOR-3632)."""

from __future__ import annotations

from collections.abc import Iterable

from rigplane.core.state_pipeline_contracts import (
    FieldFamily,
    FieldPath,
    FieldScope,
    VfoSlot,
)

__all__ = ["tx_target_inputs_written"]

_TX_SPLIT_PATH = FieldPath.global_("tx_state", "split")
_RECEIVER_INDEX = {"main": "0", "sub": "1", "0": "0", "1": "1"}


def tx_target_inputs_written(paths: Iterable[FieldPath]) -> tuple[FieldPath, ...]:
    """The TX-target derivation inputs a write to ``paths`` makes wrong.

    A command names a path by index (``receiver.0.freq_mode``) or by profile
    name (``receiver.main.active...``); the derivation of
    ``tx_state.tx_target`` reads the relative-slot spelling with the index
    receiver id. A slot-less frequency write sets the selected VFO, so it maps
    to the ``active`` slot. Anything that is not a frequency, the split flag,
    or the active-slot identity is not an input and is ignored.
    """

    inputs: list[FieldPath] = []
    for path in paths:
        if path == _TX_SPLIT_PATH:
            inputs.append(path)
            continue
        if path.scope is not FieldScope.RECEIVER:
            continue
        receiver_id = _RECEIVER_INDEX.get(path.receiver_id or "")
        if receiver_id is None:
            continue
        if path.family is FieldFamily.FREQ_MODE and path.name == "freq_hz":
            build = (
                FieldPath.unselected
                if path.slot is VfoSlot.UNSELECTED
                else FieldPath.active
            )
            inputs.append(build(receiver_id, "freq_mode", "freq_hz"))
        elif path.family is FieldFamily.VFO and path.name == "active_slot":
            inputs.append(FieldPath.active_slot(receiver_id))
    return tuple(inputs)
