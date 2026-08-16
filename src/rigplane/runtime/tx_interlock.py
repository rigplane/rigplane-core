"""Pure, shared disposition policy for writes made while RF state matters.

The policy deliberately classifies concrete command dataclasses instead of the
``Command`` union.  Enforcement seats may therefore use it while that union is
being corrected, and a missing union member cannot create a privileged path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from rigplane.core.tx_interlock_contract import (
    TX_INTERLOCK_COMMAND_FAMILY_METADATA,
    TxInterlockCommandFamily,
    TxInterlockCommandFamilyMetadata,
    TxInterlockDisposition,
)
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
    "DeferredTxCommandLane",
    "RfState",
    "TX_INTERLOCK_COMMAND_FAMILY_METADATA",
    "TxInterlockCommandFamily",
    "TxInterlockCommandFamilyMetadata",
    "TxInterlockDeferredOutcome",
    "TxInterlockDeferredResult",
    "TxInterlockDecision",
    "TxInterlockDisposition",
    "TxInterlockDispositionOverrides",
    "classify_tx_interlock",
    "evaluate_tx_interlock",
    "get_tx_interlock_command_family_metadata",
]


_METADATA_BY_FAMILY = {
    metadata.family: metadata for metadata in TX_INTERLOCK_COMMAND_FAMILY_METADATA
}

TxInterlockDispositionOverrides = Mapping[
    TxInterlockCommandFamily, TxInterlockDisposition
]


class RfState(StrEnum):
    """The observed RF state available to an enforcement seat."""

    RX = "rx"
    TX = "tx"
    UNKNOWN = "unknown"


class TxInterlockDeferredOutcome(StrEnum):
    """A state transition produced by the one-slot deferred command lane."""

    HELD = "held"
    RELEASED = "released"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class TxInterlockDecision:
    """A disposition plus whether a seat may attempt the write immediately."""

    disposition: TxInterlockDisposition
    allowed: bool
    reason: str
    rf_state: RfState | None = None


@dataclass(frozen=True, slots=True)
class TxInterlockDeferredResult:
    """A pure lane result for the enforcement seat to act on or report.

    The lane never executes ``command`` and never emits lifecycle events.  For
    supersession or expiry while accepting a replacement, ``replacement`` is
    the newly held command.  Active supersession retains the current absolute
    deadline; a replacement accepted after expiry starts a fresh hold.
    """

    outcome: TxInterlockDeferredOutcome
    command: object
    expires_at: float
    replacement: object | None = None


@dataclass(slots=True)
class _DeferredTxCommand:
    command: object
    deferred_at: float
    expires_at: float
    quiet_since: float | None = None


class DeferredTxCommandLane:
    """Pure, single-slot timing policy for commands classified as ``DEFER``.

    Seats supply their monotonic clock and observed RF state.  This class does
    not know about radios, tasks, execution, or lifecycle transports: it only
    decides whether the one held command remains held, may be released, has
    expired, or was explicitly superseded.
    """

    _TTL_SECONDS = 3.0
    _QUIET_SECONDS = 1.0

    def __init__(self) -> None:
        self._entry: _DeferredTxCommand | None = None

    @property
    def pending(self) -> object | None:
        """Return the held command, if any, without exposing lane internals."""

        return self._entry.command if self._entry is not None else None

    def defer(
        self,
        command: object,
        *,
        now: float,
        decision: TxInterlockDecision | None = None,
    ) -> TxInterlockDeferredResult:
        """Hold a ``DEFER`` command, explicitly replacing a prior held command.

        Active supersession carries forward the original absolute deadline but
        resets quiet progress for the new payload.  If the prior command was
        already expired, expiry is reported truthfully while the replacement
        starts a separate fresh hold.
        """

        effective = decision or evaluate_tx_interlock(command, rf_state=RfState.TX)
        if (
            effective.disposition is not TxInterlockDisposition.DEFER
            or effective.allowed
            or effective.rf_state is not RfState.TX
        ):
            raise ValueError("only denied DEFER decisions may be held in the lane")

        previous = self._entry
        if previous is not None and now < previous.expires_at:
            replacement = _DeferredTxCommand(
                command=command,
                deferred_at=previous.deferred_at,
                expires_at=previous.expires_at,
            )
        else:
            replacement = _DeferredTxCommand(
                command=command,
                deferred_at=now,
                expires_at=now + self._TTL_SECONDS,
            )
        self._entry = replacement
        if previous is None:
            return self._result(TxInterlockDeferredOutcome.HELD, replacement)
        if now >= previous.expires_at:
            return TxInterlockDeferredResult(
                TxInterlockDeferredOutcome.EXPIRED,
                previous.command,
                previous.expires_at,
                replacement=command,
            )
        return TxInterlockDeferredResult(
            TxInterlockDeferredOutcome.SUPERSEDED,
            previous.command,
            previous.expires_at,
            replacement=command,
        )

    def observe(
        self, *, rf_state: RfState, now: float
    ) -> TxInterlockDeferredResult | None:
        """Advance the held command against one RF observation and clock value."""

        entry = self._entry
        if entry is None:
            return None
        if now >= entry.expires_at:
            self._entry = None
            return self._result(TxInterlockDeferredOutcome.EXPIRED, entry)
        if rf_state is not RfState.RX:
            entry.quiet_since = None
            return self._result(TxInterlockDeferredOutcome.HELD, entry)
        if entry.quiet_since is None:
            entry.quiet_since = now
            return self._result(TxInterlockDeferredOutcome.HELD, entry)
        if now - entry.quiet_since < self._QUIET_SECONDS:
            return self._result(TxInterlockDeferredOutcome.HELD, entry)
        self._entry = None
        return self._result(TxInterlockDeferredOutcome.RELEASED, entry)

    @staticmethod
    def _result(
        outcome: TxInterlockDeferredOutcome, entry: _DeferredTxCommand
    ) -> TxInterlockDeferredResult:
        return TxInterlockDeferredResult(outcome, entry.command, entry.expires_at)


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
    elif isinstance(command, _HARD_BLOCK_TYPES):
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


def _effective_tx_interlock_disposition(
    command: object,
    disposition_overrides: TxInterlockDispositionOverrides | None,
) -> TxInterlockDisposition:
    base = classify_tx_interlock(command)
    if base is TxInterlockDisposition.ALWAYS_PASS or disposition_overrides is None:
        return base
    for family, override in disposition_overrides.items():
        metadata = _METADATA_BY_FAMILY.get(family)
        if (
            not isinstance(family, TxInterlockCommandFamily)
            or metadata is None
            or metadata.base_disposition is not TxInterlockDisposition.TX_SAFE
            or override is not TxInterlockDisposition.DEFER
        ):
            raise ValueError("invalid or loosening TX interlock override")
    metadata = get_tx_interlock_command_family_metadata(command)
    if metadata is None:
        return base
    return disposition_overrides.get(metadata.family, base)


def evaluate_tx_interlock(
    command: object,
    *,
    rf_state: RfState,
    disposition_overrides: TxInterlockDispositionOverrides | None = None,
) -> TxInterlockDecision:
    """Evaluate whether a command may be attempted now at an enforcement seat.

    Hard BLOCK and DEFER commands both require *known* RX for an immediate
    attempt.  The caller owns deferred-lane timing and lifecycle reporting;
    this pure policy only preserves the fail-closed result and truthful reason.
    """

    disposition = _effective_tx_interlock_disposition(command, disposition_overrides)
    if disposition in (
        TxInterlockDisposition.ALWAYS_PASS,
        TxInterlockDisposition.TX_SAFE,
    ):
        return TxInterlockDecision(
            disposition, True, "TX interlock permits this command.", rf_state
        )
    if rf_state is RfState.RX:
        return TxInterlockDecision(disposition, True, "RF state is known RX.", rf_state)
    if rf_state is RfState.UNKNOWN:
        return TxInterlockDecision(
            disposition,
            False,
            "RF state is unknown; this command must not be attempted yet.",
            rf_state,
        )
    if disposition is TxInterlockDisposition.DEFER:
        return TxInterlockDecision(
            disposition, False, "RF state is TX; command is deferred.", rf_state
        )
    return TxInterlockDecision(
        disposition, False, "RF state is TX; command is blocked.", rf_state
    )
