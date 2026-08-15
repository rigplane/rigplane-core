"""Stable, dependency-free vocabulary for TX interlock policy metadata."""

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "TX_INTERLOCK_COMMAND_FAMILY_METADATA",
    "TxInterlockCommandFamily",
    "TxInterlockCommandFamilyMetadata",
    "TxInterlockDisposition",
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
