"""Which writes move the radio's TX target (MOR-3632).

ONE table answers it for every write seat: :data:`TX_TARGET_MOVING_FAMILIES`,
in the TX-interlock family vocabulary. Every spelling of a write reaches it
through its family, never through a second list:

* a typed command -- its interlock family
  (``runtime.tx_interlock.moves_tx_target``);
* a descriptor-backed intent -- the family its ``CommandDescriptor`` declares
  (``CommandDescriptor.tx_family``: ``set_split_freq`` is a frequency write
  even though its target receiver depends on the profile);
* any other intent -- the family of the field it names as written
  (:func:`written_field_family`).

The consequence is the same for all of them
(:meth:`~rigplane.core.state_store.StateStore.fence_tx_target`): the whole
target is withdrawn until it is read again, so a write only has to be
recognised here, never located.
"""

from __future__ import annotations

from collections.abc import Iterable

from rigplane.core.command_dispatch import command_descriptor
from rigplane.core.state_pipeline_contracts import (
    CommandIntent,
    FieldFamily,
    FieldPath,
    FieldScope,
)
from rigplane.core.tx_interlock_contract import TxInterlockCommandFamily

__all__ = [
    "TX_TARGET_MOVING_FAMILIES",
    "intent_moves_tx_target",
    "writes_tx_target_input",
    "written_field_family",
]

# The TX target is the frequency of the VFO that transmits; the split flag and
# the VFO and receiver selection pick that VFO. A write moves it when it sets
# a frequency, jumps a band, selects, exchanges or copies a VFO, changes the
# split/dual-watch topology, or recalls a memory. Mode, RIT/XIT and repeater
# offsets do not change the VFO frequency this target names.
TX_TARGET_MOVING_FAMILIES: frozenset[TxInterlockCommandFamily] = frozenset(
    {
        TxInterlockCommandFamily.FREQUENCY,
        TxInterlockCommandFamily.BAND,
        TxInterlockCommandFamily.VFO_SELECT,
        TxInterlockCommandFamily.VFO_CONTENTS,
        TxInterlockCommandFamily.VFO_TOPOLOGY,
        TxInterlockCommandFamily.MEMORY,
    }
)

_TX_SPLIT_PATH = FieldPath.global_("tx_state", "split")
_ACTIVE_RECEIVER_PATH = FieldPath.global_("slow_state", "active")


def written_field_family(path: FieldPath) -> TxInterlockCommandFamily | None:
    """The interlock family of a write that names ``path`` as written.

    Only the fields the TX target is made of are classified: a receiver
    frequency in any slot spelling, the split flag, the selected VFO and the
    selected receiver. Every other field answers ``None``.
    """

    if path == _TX_SPLIT_PATH:
        return TxInterlockCommandFamily.VFO_TOPOLOGY
    if path == _ACTIVE_RECEIVER_PATH:
        return TxInterlockCommandFamily.VFO_SELECT
    if path.scope is not FieldScope.RECEIVER:
        return None
    if path.family is FieldFamily.FREQ_MODE and path.name == "freq_hz":
        return TxInterlockCommandFamily.FREQUENCY
    if path.family is FieldFamily.VFO and path.name == "active_slot":
        return TxInterlockCommandFamily.VFO_SELECT
    return None


def writes_tx_target_input(paths: Iterable[FieldPath]) -> bool:
    """Whether a write naming ``paths`` as written moves the TX target."""

    return any(
        written_field_family(path) in TX_TARGET_MOVING_FAMILIES for path in paths
    )


def intent_moves_tx_target(intent: CommandIntent) -> bool:
    """Whether executing ``intent`` moves what the radio transmits on.

    A descriptor that declares its family answers by that family; any other
    intent by the fields it names as written (its expected observations, else
    its target).
    """

    descriptor = command_descriptor(intent.name)
    if descriptor is not None and descriptor.tx_family is not None:
        return descriptor.tx_family in TX_TARGET_MOVING_FAMILIES
    paths = intent.expected_observations or (
        () if intent.target is None else (intent.target,)
    )
    return writes_tx_target_input(paths)
