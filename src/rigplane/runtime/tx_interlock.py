"""Pure, shared disposition policy for writes made while RF state matters.

The policy deliberately classifies concrete command dataclasses instead of the
``Command`` union.  Enforcement seats may therefore use it while that union is
being corrected, and a missing union member cannot create a privileged path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from rigplane.runtime._poller_types import (
    MemoryToVfo,
    PttOff,
    PttOn,
    QuickDualWatch,
    QuickDwTrigger,
    QuickSplit,
    QuickSplitTrigger,
    ScanStart,
    ScanStop,
    SelectVfo,
    SendCiv,
    SetAntenna1,
    SetAntenna2,
    SetBand,
    SetCivOutputAnt,
    SetDualWatch,
    SetFreq,
    SetMainSubTracking,
    SetMemoryMode,
    SetMode,
    SetPowerstat,
    SetRitFrequency,
    SetRitTxStatus,
    SetRxAntenna,
    SetRxAntennaAnt1,
    SetRxAntennaAnt2,
    SetSplit,
    SetTunerStatus,
    VfoEqualize,
    VfoSwap,
)

__all__ = [
    "RfState",
    "TxInterlockDecision",
    "TxInterlockDisposition",
    "classify_tx_interlock",
    "evaluate_tx_interlock",
]


class TxInterlockDisposition(StrEnum):
    """The fixed safety class assigned to a typed command."""

    ALWAYS_PASS = "always-pass"
    TX_SAFE = "tx-safe"
    DEFER = "defer"
    BLOCK = "block"


class RfState(StrEnum):
    """The observed RF state available to an enforcement seat."""

    RX = "rx"
    TX = "tx"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class TxInterlockDecision:
    """A disposition plus whether a seat may attempt the write immediately."""

    disposition: TxInterlockDisposition
    allowed: bool
    reason: str


# These structural exemptions are intentionally checked before all disruptive
# tables.  A future incomplete or corrupt disruptive table must not make a
# de-key/stop operation harder to execute.
_ALWAYS_PASS_TYPES = (PttOff, ScanStop)

_DEFER_TYPES = (
    SetFreq,
    SetMode,
    SetBand,
    SelectVfo,
    VfoSwap,
    VfoEqualize,
    SetSplit,
    SetDualWatch,
    SetMainSubTracking,
    QuickSplit,
    QuickDualWatch,
    QuickDwTrigger,
    QuickSplitTrigger,
    SetMemoryMode,
    MemoryToVfo,
    SetRitTxStatus,
    SetRitFrequency,
)

_HARD_BLOCK_TYPES = (
    PttOn,
    SendCiv,
    ScanStart,
    SetAntenna1,
    SetAntenna2,
    SetRxAntenna,
    SetRxAntennaAnt1,
    SetRxAntennaAnt2,
    SetCivOutputAnt,
)


def classify_tx_interlock(command: object) -> TxInterlockDisposition:
    """Return the non-negotiable disposition for one typed command.

    Commands not explicitly disruptive are TX-SAFE by default.  A later
    profile/provider layer may tighten that default, but this generic policy
    never loosens structural or hard-block classifications.
    """

    if isinstance(command, _ALWAYS_PASS_TYPES):
        return TxInterlockDisposition.ALWAYS_PASS
    if isinstance(command, SetPowerstat) and not command.on:
        return TxInterlockDisposition.ALWAYS_PASS
    if isinstance(command, SetTunerStatus) and command.value == 0:
        return TxInterlockDisposition.ALWAYS_PASS

    if isinstance(command, SetTunerStatus) and command.value in (1, 2):
        return TxInterlockDisposition.BLOCK
    if isinstance(command, _HARD_BLOCK_TYPES):
        return TxInterlockDisposition.BLOCK
    if isinstance(command, _DEFER_TYPES):
        return TxInterlockDisposition.DEFER
    return TxInterlockDisposition.TX_SAFE


def evaluate_tx_interlock(command: object, *, rf_state: RfState) -> TxInterlockDecision:
    """Evaluate whether a command may be attempted now at an enforcement seat.

    Hard BLOCK and DEFER commands both require *known* RX for an immediate
    attempt.  The caller owns deferred-lane timing and lifecycle reporting;
    this pure policy only preserves the fail-closed result and truthful reason.
    """

    disposition = classify_tx_interlock(command)
    if disposition in (
        TxInterlockDisposition.ALWAYS_PASS,
        TxInterlockDisposition.TX_SAFE,
    ):
        return TxInterlockDecision(
            disposition, True, "TX interlock permits this command."
        )
    if rf_state is RfState.RX:
        return TxInterlockDecision(disposition, True, "RF state is known RX.")
    if rf_state is RfState.UNKNOWN:
        return TxInterlockDecision(
            disposition,
            False,
            "RF state is unknown; this command must not be attempted yet.",
        )
    if disposition is TxInterlockDisposition.DEFER:
        return TxInterlockDecision(
            disposition, False, "RF state is TX; command is deferred."
        )
    return TxInterlockDecision(
        disposition, False, "RF state is TX; command is blocked."
    )
