"""Transmit-authority vocabulary, classification table and pure engine.

The authority answers one question per write: *would this command throw a
contact under RF?* — not "are we transmitting". Only the four owner-ruled
hazard families (band, tuner, antenna, VFO select) and the keying commands
consult transmit truth; everything the manufacturers permit passes without
consulting it at all.

The engine performs no I/O of its own: a backend injects the solicited
transmit-state read. Nothing in the product consumes this module yet — it lands
ahead of the backend admission rows so the contracts exist before any call site
cites them.

Design: ``docs/plans/2026-08-20-transmit-authority.md`` §3.3-§3.7.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from types import MappingProxyType
from typing import NoReturn

from .tx_observation import (
    RADIO_READBACK_SOURCES,  # noqa: F401
    TX_READ_DEADLINE_SECONDS,
    TxStateReading,
)

_LOGGER = logging.getLogger(__name__)


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


class TxRefusalCode(StrEnum):
    """Every reason the authority declines a write."""

    REFUSED_WHILE_TRANSMITTING = "refused-while-transmitting"
    TX_TRUTH_UNAVAILABLE = "tx-truth-unavailable"


@dataclass(frozen=True, slots=True)
class TxEvidence:
    """What a decision observed. Required on every refusal, no exceptions."""

    value: bool | None = None
    attributed: str | None = None
    age_seconds: float | None = None
    source: str | None = None
    solicited: bool = False
    verified_readback: bool = False
    failure: str | None = None


class TxRefusal(Exception):
    """The one exception consumers of the authority handle."""

    def __init__(self, code: TxRefusalCode, evidence: TxEvidence) -> None:
        super().__init__(str(code))
        self.code = code
        self.evidence = evidence


@dataclass(frozen=True, slots=True)
class TxMethodEntry:
    """One row of a per-backend method-name → family map.

    The per-backend maps themselves land beside the methods they pin.
    """

    family: TxFamily


@dataclass
class TxAdmission:
    """Handed to the caller for the duration of an admitted write."""

    family: TxFamily
    write_class: TxWriteClass
    evidence: TxEvidence | None = None


class TransmitAuthority:
    """One instance per radio. Pure: no sockets, no tasks, no module state."""

    def __init__(
        self,
        *,
        read_transmit_state: Callable[[], Awaitable[TxStateReading]],
        method_map: Mapping[str, TxMethodEntry],
        clock: Callable[[], float] = time.monotonic,
        read_deadline_seconds: float = TX_READ_DEADLINE_SECONDS,
    ) -> None:
        self._read_transmit_state = read_transmit_state
        self._method_map = dict(method_map)
        self._clock = clock
        self._read_deadline_seconds = read_deadline_seconds

        self._lock = asyncio.Lock()
        self._transmit_epoch = 0

    # -- intake ------------------------------------------------------------

    def note_transmit_observation(self, transmitting: bool) -> None:
        """Record that the radio was seen transmitting.

        Only the transmitting direction has an effect: it invalidates any read
        an in-flight hazard admission is holding. A receive report clears
        nothing — inferring *receive* from anything but a fresh solicited read
        is what the programme forbids.
        """
        if transmitting:
            self._transmit_epoch += 1

    # -- admission ---------------------------------------------------------

    @asynccontextmanager
    async def admit(
        self,
        method: str,
        args: Sequence[object] = (),
        kwargs: Mapping[str, object] | None = None,
    ) -> AsyncIterator[TxAdmission]:
        """Gate one write. The body performs the write; the lock spans both."""
        family = self._classify(method)
        if family is None:
            # INV-1's fail direction: nothing defaults to PASS by omission.
            self._refuse(
                method,
                "unclassified",
                TxWriteClass.HAZARD,
                TxRefusalCode.TX_TRUTH_UNAVAILABLE,
                TxEvidence(failure="unclassified"),
            )

        write_class = FAMILY_WRITE_CLASS[family]

        if write_class is TxWriteClass.PASS:
            yield TxAdmission(family, write_class)
            return

        async with self._lock:
            if write_class is TxWriteClass.KEYING:
                ticket = self._admit_keying(family)
            else:
                ticket = await self._admit_hazard(method, family)
            yield ticket

    # -- internals ---------------------------------------------------------

    def _classify(self, method: str) -> TxFamily | None:
        entry = self._method_map.get(method)
        if entry is None:
            return None
        return entry.family

    def _admit_keying(self, family: TxFamily) -> TxAdmission:
        self._transmit_epoch += 1
        return TxAdmission(family, TxWriteClass.KEYING)

    async def _admit_hazard(self, method: str, family: TxFamily) -> TxAdmission:
        def refuse(code: TxRefusalCode, evidence: TxEvidence) -> NoReturn:
            self._refuse(method, family, TxWriteClass.HAZARD, code, evidence)

        unavailable = TxRefusalCode.TX_TRUTH_UNAVAILABLE

        # One solicited read on this admission's own deadline.
        epoch = self._transmit_epoch
        started = self._clock()
        try:
            reading = await asyncio.wait_for(
                self._read_transmit_state(), self._read_deadline_seconds
            )
        except asyncio.TimeoutError:
            refuse(unavailable, TxEvidence(solicited=True, failure="timeout"))
        except (OSError, RuntimeError):
            refuse(unavailable, TxEvidence(solicited=True, failure="transport"))
        except Exception:
            # TxRefusal is the one exception a consumer of the gate handles, so
            # a parser fault in the read primitive becomes one too (§3.4).
            refuse(unavailable, TxEvidence(solicited=True, failure="read-error"))

        evidence = TxEvidence(
            value=reading.value,
            attributed=reading.attributed,
            age_seconds=self._clock() - started,
            source=reading.source,
            solicited=True,
            verified_readback=reading.verified_readback,
            failure=reading.failure,
        )
        if reading.value is None:
            refuse(unavailable, evidence)
        if not reading.verified_readback:
            # This admission has no verified-readback evidence.
            refuse(
                unavailable,
                replace(evidence, failure="unverifiable-provenance"),
            )
        if reading.value or epoch != self._transmit_epoch:
            refuse(TxRefusalCode.REFUSED_WHILE_TRANSMITTING, evidence)

        return TxAdmission(family, TxWriteClass.HAZARD, evidence=evidence)

    def _refuse(
        self,
        method: str,
        family: TxFamily | str,
        write_class: TxWriteClass,
        code: TxRefusalCode,
        evidence: TxEvidence,
    ) -> NoReturn:
        _LOGGER.warning(
            "transmit authority refused write",
            extra={
                "method": method,
                "family": str(family),
                "writeClass": str(write_class),
                "code": str(code),
                "evidence": asdict(evidence),
            },
        )
        raise TxRefusal(code, evidence)
