"""Stable, dependency-free vocabulary for TX interlock policy metadata."""

from dataclasses import dataclass
from enum import StrEnum

__all__ = [
    "REPEATER_WRITE_INTENTS",
    "TX_INTERLOCK_COMMAND_FAMILY_METADATA",
    "TxInterlockCommandFamily",
    "TxInterlockCommandFamilyMetadata",
    "TxInterlockDisposition",
]

# MOR-3633: the one table that classifies repeater writes. A shift, tone,
# TSQL or tone-frequency write while keyed moves the TX frequency (up to
# 5 MHz on 70 cm) mid-transmission, so every member defers like the VFO
# topology and memory families — never a hard block, so a known-RX pass
# stays easy. These commands are descriptor-backed: their only name source
# is the command descriptor (``core/command_dispatch.py``), so the table is
# keyed by descriptor intent name and no typed command exists for them. A
# later repeater command joins by adding one name here; the interlock
# classification, the family metadata, the control channel's reply handling
# and the queue's request-withdrawal rule all read this set.
REPEATER_WRITE_INTENTS = frozenset(
    {
        "set_repeater_shift",
        "set_repeater_tone",
        "set_repeater_tsql",
        "set_tone_freq",
        "set_tsql_freq",
    }
)


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
    VFO_CONTENTS = "vfo-contents"
    VFO_TOPOLOGY = "vfo-topology"
    MEMORY = "memory"
    REPEATER = "repeater"
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
        TxInterlockCommandFamily.FREQUENCY, TxInterlockDisposition.TX_SAFE
    ),
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.MODE, TxInterlockDisposition.TX_SAFE
    ),
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.BAND, TxInterlockDisposition.TX_SAFE
    ),
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.VFO_SELECT, TxInterlockDisposition.TX_SAFE
    ),
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.VFO_CONTENTS, TxInterlockDisposition.TX_SAFE
    ),
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.VFO_TOPOLOGY, TxInterlockDisposition.DEFER
    ),
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.MEMORY, TxInterlockDisposition.DEFER
    ),
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.REPEATER, TxInterlockDisposition.DEFER
    ),
    TxInterlockCommandFamilyMetadata(
        TxInterlockCommandFamily.RIT_XIT, TxInterlockDisposition.TX_SAFE
    ),
)
