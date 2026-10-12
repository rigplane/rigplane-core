"""Pure, shared disposition policy for writes made while RF state matters.

The policy deliberately classifies concrete command dataclasses instead of the
``Command`` union.  Enforcement seats may therefore use it while that union is
being corrected, and a missing union member cannot create a privileged path.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite

from rigplane.core.exceptions import CommandError
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.state_store import FreshnessState, StateSnapshot
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
    SetVfoFreq,
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
    "TxInterlockRefusal",
    "classify_raw_civ_frame_tx_family",
    "classify_tx_interlock",
    "evaluate_tx_interlock",
    "get_tx_interlock_command_family_metadata",
    "resolve_rf_state",
]


_METADATA_BY_FAMILY = {
    metadata.family: metadata for metadata in TX_INTERLOCK_COMMAND_FAMILY_METADATA
}


class TxInterlockRefusal(CommandError):
    """Interlock refusal carrying a machine-readable reason for the issuer.

    Lives beside the policy it reports rather than at a seat that raises it:
    the reason codes are policy vocabulary, and every enforcement seat may
    need them. ``reason_code`` rides the failed-lifecycle details whitelist so
    a localized UI never has to parse English prose off the wire (MOR-1879).
    """

    def __init__(self, message: str, *, reason_code: str) -> None:
        super().__init__(message)
        self.reason_code = reason_code


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
        rf_state: RfState = RfState.TX,
    ) -> TxInterlockDeferredResult:
        """Hold a ``DEFER`` command, explicitly replacing a prior held command.

        Active supersession carries forward the original absolute deadline but
        resets quiet progress for the new payload.  If the prior command was
        already expired, expiry is reported truthfully while the replacement
        starts a separate fresh hold.
        """

        effective = evaluate_tx_interlock(command, rf_state=rf_state)
        if (
            effective.disposition is not TxInterlockDisposition.DEFER
            or effective.allowed
            or rf_state is not RfState.TX
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


_PTT_PATH = FieldPath.global_("tx_state", "ptt")


def resolve_rf_state(snapshot: StateSnapshot) -> RfState:
    """Resolve RF state from the bool ``tx_state.ptt`` observation.

    Applies the predicate set of ``core.tx_observation.project_observed_ptt``,
    adapted to a bool value, and additionally refuses a negative snapshot
    provider generation: the age is recomputed from the snapshot instead
    of trusting the FRESH flag, so an observation past its TTL reads UNKNOWN
    even when no freshness tick has cleared the flag yet.
    """

    try:
        field = snapshot.field(_PTT_PATH)
    except KeyError:
        return RfState.UNKNOWN
    timing = (
        snapshot.generated_at_monotonic,
        field.last_observed_monotonic,
        field.max_age,
    )
    if (
        field.freshness is not FreshnessState.FRESH
        or type(field.value) is not bool
        or type(field.provider_generation) is not int
        or type(snapshot.provider_generation) is not int
        or snapshot.provider_generation < 0
        or field.provider_generation != snapshot.provider_generation
        or any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not isfinite(value)
            for value in timing
        )
        or field.max_age is None
        or field.max_age <= 0
        or field.last_observed_monotonic < 0
        or snapshot.generated_at_monotonic < field.last_observed_monotonic
        or snapshot.generated_at_monotonic - field.last_observed_monotonic
        >= field.max_age
    ):
        return RfState.UNKNOWN
    return RfState.TX if field.value else RfState.RX


# These structural exemptions are intentionally checked before all disruptive
# tables.  A future incomplete or corrupt disruptive table must not make a
# de-key/stop operation harder to execute.
_ALWAYS_PASS_TYPES = (PttOff, ScanStop)

_DEFER_TYPES = (
    SetSplit,
    SetDualWatch,
    SetMainSubTracking,
    QuickSplit,
    QuickDualWatch,
    QuickDwTrigger,
    QuickSplitTrigger,
    SetMemoryMode,
    MemoryToVfo,
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
    family metadata.
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
    elif isinstance(command, (SetFreq, SetVfoFreq)):
        family = TxInterlockCommandFamily.FREQUENCY
    elif isinstance(command, SetMode):
        family = TxInterlockCommandFamily.MODE
    elif isinstance(command, SetBand):
        family = TxInterlockCommandFamily.BAND
    elif isinstance(command, SelectVfo):
        family = TxInterlockCommandFamily.VFO_SELECT
    elif isinstance(command, (VfoSwap, VfoEqualize)):
        family = TxInterlockCommandFamily.VFO_CONTENTS
    elif isinstance(
        command,
        (
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

    Commands not explicitly disruptive are TX-SAFE by default.
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


def evaluate_tx_interlock(
    command: object,
    *,
    rf_state: RfState,
) -> TxInterlockDecision:
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


# ---------------------------------------------------------------------------
# Raw CI-V TX-family classification (byte level)
# ---------------------------------------------------------------------------
#
# The typed policy above classifies command dataclasses.  Raw CI-V frames
# (rigctld ``w`` frames, HTTP raw_civ_transaction requests, WebSocket
# ``send_civ`` commands) carry no typed command, so managed-mode enforcement
# needs the same TX family at the byte level.  The family follows what the
# typed policy already treats as a TX actuation: the PTT command 0x1C sub
# 0x00 in both directions (PttOn is ``_HARD_BLOCK_TYPES``; PttOff stays
# inside the family here) and the tuner
# command 0x1C sub 0x01 (``SetTunerStatus`` values 1/2 are BLOCK).  Raw
# tuner-off (``1C 01 00``) is refused too, which is stricter than the typed
# policy: ``classify_tx_interlock`` lets ``SetTunerStatus`` value 0 through.
# A PTT or tuner read (``1C 00`` / ``1C 01`` with no data byte) is not an actuation.

_CIV_FRAME_HEAD = 0xFE
_CIV_FRAME_TAIL = 0xFD
_CIV_FRAME_COLLISION = 0xFC
_CIV_TX_FAMILY_COMMAND = 0x1C
_CIV_TX_FAMILY_SUBS = (0x00, 0x01)  # PTT, tuner
_CIV_RECEIVER_PREFIX_COMMAND = 0x29


def _is_tx_family_civ_command(command: int, sub: int | None, data: bytes) -> bool:
    """True when an unwrapped raw CI-V ``(command, sub, data)`` triple is TX.

    The triple is the payload of one whole frame as parsed by
    :func:`classify_raw_civ_frame_tx_family`: for a 0x29 receiver-prefix
    wrapper ``sub`` is None and ``data`` starts at the receiver byte
    (``receiver + inner command + inner sub + inner data``), unwrapped one
    level here.  Anything whose inner command cannot be identified fails
    closed (True): a managed caller must refuse what it cannot classify.
    """

    if command == _CIV_RECEIVER_PREFIX_COMMAND:
        if len(data) < 2 or data[1] == _CIV_RECEIVER_PREFIX_COMMAND:
            return True
        inner = data[1]
        if inner != _CIV_TX_FAMILY_COMMAND:
            return False
        if len(data) < 3:
            return True
        return data[2] in _CIV_TX_FAMILY_SUBS and bool(data[3:])
    if command != _CIV_TX_FAMILY_COMMAND:
        return False
    return sub in _CIV_TX_FAMILY_SUBS and bool(data)


def classify_raw_civ_frame_tx_family(frame: bytes) -> bool | None:
    """Byte-level TX-family verdict for one whole CI-V frame.

    Parses the ``FE FE to from cmd [sub] [data] FD`` envelope, then defers to
    :func:`_is_tx_family_civ_command`.  A payload holds exactly one frame: any
    ``FE``, ``FD``, or ``FC`` byte between the leading ``FE FE`` and the
    final ``FD`` is unparseable.  Returns None when the envelope cannot be
    parsed; a managed caller must refuse on None as well as on True.
    """

    if (
        len(frame) < 6
        or frame[0] != _CIV_FRAME_HEAD
        or frame[1] != _CIV_FRAME_HEAD
        or frame[-1] != _CIV_FRAME_TAIL
    ):
        return None
    if any(
        byte in (_CIV_FRAME_HEAD, _CIV_FRAME_TAIL, _CIV_FRAME_COLLISION)
        for byte in frame[2:-1]
    ):
        return None
    command = frame[4]
    if command == _CIV_TX_FAMILY_COMMAND:
        if len(frame) < 7:
            return None
        return _is_tx_family_civ_command(command, frame[5], frame[6:-1])
    if command == _CIV_RECEIVER_PREFIX_COMMAND:
        return _is_tx_family_civ_command(command, None, frame[5:-1])
    return False
