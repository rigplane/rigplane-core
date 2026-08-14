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
    "TX_INTERLOCK_COMMAND_FAMILY_METADATA",
    "TxInterlockCommandFamily",
    "TxInterlockCommandFamilyMetadata",
    "TxInterlockDecision",
    "TxInterlockDisposition",
    "classify_tx_interlock",
    "evaluate_tx_interlock",
    "get_tx_interlock_command_family_metadata",
]


class TxInterlockDisposition(StrEnum):
    """The fixed safety class assigned to a typed command."""

    ALWAYS_PASS = "always-pass"
    TX_SAFE = "tx-safe"
    DEFER = "defer"
    BLOCK = "block"


class TxInterlockCommandFamily(StrEnum):
    """Stable, generic families with an owner-ruled base disposition."""

    PTT_OFF = "ptt-off"
    POWER_OFF = "power-off"
    SCAN_STOP = "scan-stop"
    TUNER_OFF = "tuner-off"
    POWER_ON = "power-on"
    PTT_ON = "ptt-on"
    RAW_CIV = "raw-civ"
    SCAN_START = "scan-start"
    ANTENNA_SWITCH = "antenna-switch"
    TUNER_ENGAGE = "tuner-engage"
    FREQUENCY = "frequency"
    MODE = "mode"
    BAND = "band"
    VFO_SELECT = "vfo-select"
    VFO_TOPOLOGY = "vfo-topology"
    MEMORY = "memory"
    RIT_XIT = "rit-xit"


@dataclass(frozen=True, slots=True)
class TxInterlockCommandFamilyMetadata:
    """One generic family and its non-negotiable typed-policy disposition."""

    family: TxInterlockCommandFamily
    base_disposition: TxInterlockDisposition


TX_INTERLOCK_COMMAND_FAMILY_METADATA = (
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.PTT_OFF, TxInterlockDisposition.ALWAYS_PASS
    ),
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.POWER_OFF, TxInterlockDisposition.ALWAYS_PASS
    ),
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.SCAN_STOP, TxInterlockDisposition.ALWAYS_PASS
    ),
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.TUNER_OFF, TxInterlockDisposition.ALWAYS_PASS
    ),
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.POWER_ON, TxInterlockDisposition.TX_SAFE
    ),
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.PTT_ON, TxInterlockDisposition.BLOCK
    ),
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.RAW_CIV, TxInterlockDisposition.BLOCK
    ),
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.SCAN_START, TxInterlockDisposition.BLOCK
    ),
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.ANTENNA_SWITCH, TxInterlockDisposition.BLOCK
    ),
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.TUNER_ENGAGE, TxInterlockDisposition.BLOCK
    ),
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.FREQUENCY, TxInterlockDisposition.DEFER
    ),
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.MODE, TxInterlockDisposition.DEFER
    ),
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.BAND, TxInterlockDisposition.DEFER
    ),
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.VFO_SELECT, TxInterlockDisposition.DEFER
    ),
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.VFO_TOPOLOGY, TxInterlockDisposition.DEFER
    ),
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.MEMORY, TxInterlockDisposition.DEFER
    ),
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.RIT_XIT, TxInterlockDisposition.DEFER
    ),
)

_METADATA_BY_FAMILY = {
    metadata.family: metadata for metadata in TX_INTERLOCK_COMMAND_FAMILY_METADATA
}


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


# These are the antenna-switch variants; all other typed-policy groupings live
# directly in ``get_tx_interlock_command_family_metadata`` so the exported
# family metadata remains the one source for classification disposition.
_ANTENNA_SWITCH_TYPES = (
    SetAntenna1,
    SetAntenna2,
    SetRxAntenna,
    SetRxAntennaAnt1,
    SetRxAntennaAnt2,
    SetCivOutputAnt,
)


def get_tx_interlock_command_family_metadata(
    command: object,
) -> TxInterlockCommandFamilyMetadata | None:
    """Return stable metadata for an explicitly classified typed command.

    Commands covered only by the generic TX-SAFE default intentionally have no
    family metadata.  This prevents consumers from mistaking a missing policy
    classification for a profile-addressable family.
    """

    if isinstance(command, PttOff):
        family = TxInterlockCommandFamily.PTT_OFF
    elif isinstance(command, SetPowerstat):
        family = (
            TxInterlockCommandFamily.POWER_OFF
            if not command.on
            else TxInterlockCommandFamily.POWER_ON
        )
    elif isinstance(command, ScanStop):
        family = TxInterlockCommandFamily.SCAN_STOP
    elif isinstance(command, SetTunerStatus) and command.value == 0:
        family = TxInterlockCommandFamily.TUNER_OFF
    elif isinstance(command, PttOn):
        family = TxInterlockCommandFamily.PTT_ON
    elif isinstance(command, SendCiv):
        family = TxInterlockCommandFamily.RAW_CIV
    elif isinstance(command, ScanStart):
        family = TxInterlockCommandFamily.SCAN_START
    elif isinstance(command, _ANTENNA_SWITCH_TYPES):
        family = TxInterlockCommandFamily.ANTENNA_SWITCH
    elif isinstance(command, SetTunerStatus) and command.value in (1, 2):
        family = TxInterlockCommandFamily.TUNER_ENGAGE
    elif isinstance(command, SetFreq):
        family = TxInterlockCommandFamily.FREQUENCY
    elif isinstance(command, SetMode):
        family = TxInterlockCommandFamily.MODE
    elif isinstance(command, SetBand):
        family = TxInterlockCommandFamily.BAND
    elif isinstance(command, SelectVfo):
        family = TxInterlockCommandFamily.VFO_SELECT
    elif isinstance(
        command,
        (
            VfoSwap,
            VfoEqualize,
            SetSplit,
            SetDualWatch,
            SetMainSubTracking,
            QuickSplit,
            QuickDualWatch,
            QuickDwTrigger,
            QuickSplitTrigger,
        ),
    ):
        family = TxInterlockCommandFamily.VFO_TOPOLOGY
    elif isinstance(command, (SetMemoryMode, MemoryToVfo)):
        family = TxInterlockCommandFamily.MEMORY
    elif isinstance(command, (SetRitTxStatus, SetRitFrequency)):
        family = TxInterlockCommandFamily.RIT_XIT
    else:
        return None
    return _METADATA_BY_FAMILY[family]


def classify_tx_interlock(command: object) -> TxInterlockDisposition:
    """Return the non-negotiable disposition for one typed command.

    Commands not explicitly disruptive are TX-SAFE by default.  A later
    profile/provider layer may tighten that default, but this generic policy
    never loosens structural or hard-block classifications.
    """

    metadata = get_tx_interlock_command_family_metadata(command)
    if metadata is not None:
        return metadata.base_disposition
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
