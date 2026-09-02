"""Dormant transmit-authority vocabulary and classification skeleton."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from .tx_observation import (
    RADIO_READBACK_SOURCES,  # noqa: F401
    TX_READ_DEADLINE_SECONDS,  # noqa: F401
    TxStateReading,  # noqa: F401
)


class TxWriteClass(StrEnum):
    """The closed vocabulary of write classes."""

    PASS = "pass"
    HAZARD = "hazard"
    KEYING = "keying"


class TxFamily(StrEnum):
    """Neutral, radio-independent command families."""

    FREQUENCY = "frequency"
    RIT_XIT = "rit-xit"
    MODE = "mode"
    VFO_TOPOLOGY = "vfo-topology"
    LEVELS = "levels"
    RX_PATH = "rx-path"
    MEMORY_WRITE = "memory-write"
    SCAN_START = "scan-start"
    SCAN_STOP = "scan-stop"
    POWER_ON = "power-on"
    CW_STOP = "cw-stop"
    BAND = "band"
    TUNER = "tuner"
    ANTENNA = "antenna"
    VFO_SELECT = "vfo-select"
    PTT_ON = "ptt-on"
    CW_TEXT = "cw-text"


#: The neutral family → class table. Pinned literal, never computed: PASS is
#: an explicit entry for every permitted family, and hazard membership is a
#: code-level constant because the four-family rule rests on universal
#: evidence (relay physics, both vendors, the measured band-change relay
#: throw under RF) rather than on any one radio.
FAMILY_WRITE_CLASS: Mapping[TxFamily, TxWriteClass] = MappingProxyType(
    {
        TxFamily.FREQUENCY: TxWriteClass.PASS,
        TxFamily.RIT_XIT: TxWriteClass.PASS,
        TxFamily.MODE: TxWriteClass.PASS,
        TxFamily.VFO_TOPOLOGY: TxWriteClass.PASS,
        TxFamily.LEVELS: TxWriteClass.PASS,
        TxFamily.RX_PATH: TxWriteClass.PASS,
        TxFamily.MEMORY_WRITE: TxWriteClass.PASS,
        TxFamily.SCAN_START: TxWriteClass.PASS,
        TxFamily.SCAN_STOP: TxWriteClass.PASS,
        TxFamily.POWER_ON: TxWriteClass.PASS,
        TxFamily.CW_STOP: TxWriteClass.PASS,
        TxFamily.BAND: TxWriteClass.HAZARD,
        TxFamily.TUNER: TxWriteClass.HAZARD,
        TxFamily.ANTENNA: TxWriteClass.HAZARD,
        TxFamily.VFO_SELECT: TxWriteClass.HAZARD,
        TxFamily.PTT_ON: TxWriteClass.KEYING,
        TxFamily.CW_TEXT: TxWriteClass.KEYING,
    }
)


@dataclass(frozen=True, slots=True)
class TxMethodEntry:
    """One row of a per-backend method-name → family map.

    The per-backend maps themselves land beside the methods they pin.
    """

    family: TxFamily


@dataclass
class TxAdmission:
    """Classification metadata handed to a mapped write."""

    family: TxFamily
    write_class: TxWriteClass


class TransmitAuthority:
    """Dormant method classifier with no observation or I/O behavior."""

    def __init__(self, *, method_map: Mapping[str, TxMethodEntry]) -> None:
        self._method_map = dict(method_map)

    @asynccontextmanager
    async def admit(
        self,
        method: str,
        args: Sequence[object] = (),
        kwargs: Mapping[str, object] | None = None,
    ) -> AsyncIterator[TxAdmission]:
        """Yield classification metadata for one mapped write."""
        family = self._classify(method)
        write_class = FAMILY_WRITE_CLASS[family]
        yield TxAdmission(family, write_class)

    def _classify(self, method: str) -> TxFamily:
        return self._method_map[method].family
