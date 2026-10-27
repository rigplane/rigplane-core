"""Pure, shared disposition policy for writes made while RF state matters.

The policy deliberately classifies concrete command dataclasses instead of the
``Command`` union.  Enforcement seats may therefore use it while that union is
being corrected, and a missing union member cannot create a privileged path.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Any

from rigplane.commands._frame import build_civ_frame
from rigplane.core.exceptions import CommandError
from rigplane.core.state_pipeline_contracts import CommandIntent, FieldPath
from rigplane.core.state_store import FreshnessState, StateSnapshot
from rigplane.core.tx_interlock_contract import (
    TX_INTERLOCK_COMMAND_FAMILY_METADATA,
    TxInterlockCommandFamily,
    TxInterlockCommandFamilyMetadata,
    TxInterlockDisposition,
)
from rigplane.core.tx_target import KnownTxTarget, UnknownTxTarget
from rigplane.runtime._poller_types import (
    MemoryClear,
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
    SetMemoryContents,
    SetVfoFreq,
    SetMainSubTracking,
    SetMemoryMode,
    SetMode,
    SetPowerstat,
    SetRepeaterShift,
    SetRepeaterTone,
    SetRepeaterTsql,
    SetRitFrequency,
    SetRitTxStatus,
    SetRxAntenna,
    SetRxAntennaAnt1,
    SetRxAntennaAnt2,
    SetSplit,
    SetToneFreq,
    SetTsqlFreq,
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
    "interlock_policy_token",
    "is_exact_raw_dekey_frame",
    "is_exact_raw_transmission_end_frame",
    "ptt_on_tx_target_refusal",
    "resolve_rf_state",
    "send_civ_frame",
    "tx_frequency_unknown_refusal",
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
    ``details`` carries optional machine-readable refusal evidence (MOR-3633:
    the TX-target reason and the receiver that would transmit); seats merge
    it into the failed-lifecycle details beside ``reason_code``.
    """

    def __init__(
        self,
        message: str,
        *,
        reason_code: str,
        details: Mapping[str, object] | None = None,
    ) -> None:
        super().__init__(message)
        self.reason_code = reason_code
        self.details: dict[str, object] = dict(details) if details is not None else {}


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

# MOR-3633: the one table that classifies repeater writes. A shift, tone,
# TSQL or tone-frequency write while keyed moves the TX frequency (up to
# 5 MHz on 70 cm) mid-transmission, so every member defers like the VFO
# topology and memory families — never a hard block, so a known-RX pass
# stays easy. A later repeater command joins by adding one row here; the
# DEFER table and the family branch below both read this tuple.
_REPEATER_DEFER_TYPES = (
    SetRepeaterShift,
    SetRepeaterTone,
    SetRepeaterTsql,
    SetToneFreq,
    SetTsqlFreq,
)

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
    # MOR-3644: writing or clearing a stored channel while keyed can leave
    # a wrong-frequency channel behind; deferred like the other memory
    # family members, never a hard block (a known-RX pass must stay easy).
    MemoryClear,
    SetMemoryContents,
    *_REPEATER_DEFER_TYPES,
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
    elif isinstance(
        command, (SetMemoryMode, MemoryToVfo, MemoryClear, SetMemoryContents)
    ):
        family = TxInterlockCommandFamily.MEMORY
    elif isinstance(command, _REPEATER_DEFER_TYPES):
        family = TxInterlockCommandFamily.REPEATER
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
# Descriptor bridge: one table from intent names to typed policy tokens
# ---------------------------------------------------------------------------
#
# Descriptor-backed intents (``set_repeater_shift`` and friends, WS/HTTP)
# never become typed commands on the dispatch path — the drain invokes the
# Radio method directly — so classifying the tokens above does not gate
# them by itself. This bridge is the one mapping the dispatch seat
# (``ManagedTxAuthority.admit_managed_write``) uses to reach the same typed
# policy; the descriptors keep no second list of which commands defer.


def _repeater_shift_policy_token(params: Mapping[str, Any]) -> SetRepeaterShift:
    return SetRepeaterShift(params["direction"], params.get("receiver", 0))


def _repeater_tone_policy_token(params: Mapping[str, Any]) -> SetRepeaterTone:
    return SetRepeaterTone(params["on"], params.get("receiver", 0))


def _repeater_tsql_policy_token(params: Mapping[str, Any]) -> SetRepeaterTsql:
    return SetRepeaterTsql(params["on"], params.get("receiver", 0))


def _tone_freq_policy_token(params: Mapping[str, Any]) -> SetToneFreq:
    return SetToneFreq(params["freq_hz"], params.get("receiver", 0))


def _tsql_freq_policy_token(params: Mapping[str, Any]) -> SetTsqlFreq:
    return SetTsqlFreq(params["freq_hz"], params.get("receiver", 0))


_INTERLOCK_POLICY_TOKEN_BUILDERS: Mapping[
    str, Callable[[Mapping[str, Any]], object]
] = {
    "set_repeater_shift": _repeater_shift_policy_token,
    "set_repeater_tone": _repeater_tone_policy_token,
    "set_repeater_tsql": _repeater_tsql_policy_token,
    "set_tone_freq": _tone_freq_policy_token,
    "set_tsql_freq": _tsql_freq_policy_token,
}


def interlock_policy_token(intent: object) -> object | None:
    """Bridge one descriptor-backed intent to its typed interlock token.

    Bound intents carry canonical descriptor names, so aliases resolve
    without a second lookup. Anything the table does not name — other
    descriptors, non-descriptor intents, typed commands, anything else —
    bridges to ``None`` and keeps its existing admission path.
    """

    if not isinstance(intent, CommandIntent):
        return None
    builder = _INTERLOCK_POLICY_TOKEN_BUILDERS.get(intent.name)
    if builder is None:
        return None
    return builder(intent.params)


# ---------------------------------------------------------------------------
# PTT-on guard: the TX side's frequency must be known (MOR-3633)
# ---------------------------------------------------------------------------

_TX_TARGET_PATH = FieldPath.global_("tx_state", "tx_target")


def _unknown_tx_frequency_refusal(
    reason: str, receiver: str | None
) -> TxInterlockRefusal:
    details: dict[str, object] = {"txTargetReason": reason}
    transmit_suffix = f"; {receiver} would transmit" if receiver is not None else ""
    message = f"PTT refused: the TX frequency is unknown ({reason}{transmit_suffix})."
    if receiver is not None:
        details["receiver"] = receiver
    return TxInterlockRefusal(
        message, reason_code="ptt_tx_frequency_unknown", details=details
    )


def tx_frequency_unknown_refusal(value: object) -> TxInterlockRefusal | None:
    """Refuse a TX-side frequency value that cannot key a transmitter.

    The value-level core of the PTT-on guard, for seats that retain the
    ``tx_state.tx_target`` observation rather than a snapshot. Only a known
    target carrying a frequency passes; ``None`` (no observation) reads
    ``not-observed``, and anything outside the ``core.tx_target``
    vocabulary reads ``contradiction``. The receiver is named only when a
    trusted known value carries it.
    """

    if value is None:
        return _unknown_tx_frequency_refusal("not-observed", None)
    if isinstance(value, KnownTxTarget):
        if value.frequency_hz is None:
            return _unknown_tx_frequency_refusal("not-observed", value.receiver)
        return None
    if isinstance(value, UnknownTxTarget):
        return _unknown_tx_frequency_refusal(value.reason, None)
    return _unknown_tx_frequency_refusal("contradiction", None)


def ptt_on_tx_target_refusal(
    snapshot: StateSnapshot, *, tx_target_supported: bool
) -> TxInterlockRefusal | None:
    """Refuse PTT-on while the TX side's frequency is unknown.

    Reads the one ``global.tx_state.tx_target`` observation through the
    ``core.tx_target`` vocabulary — never a second target resolver — with
    the same fail-closed strictness as :func:`resolve_rf_state`: a missing
    field, a stale flag or TTL, and a foreign provider generation all
    refuse, as does a malformed observation. ``tx_target_supported`` is
    the profile's answer from
    :func:`rigplane.runtime._state_queries.profile_publishes_tx_target`;
    radios that can never publish the target skip the guard and keep
    keying. This is unknown-ness only — no frequency-permit logic, which
    is MOR-3632 slice B.
    """

    if not tx_target_supported:
        return None
    try:
        field = snapshot.field(_TX_TARGET_PATH)
    except KeyError:
        return tx_frequency_unknown_refusal(None)
    if field.freshness is FreshnessState.STALE:
        return _unknown_tx_frequency_refusal("stale", None)
    if field.freshness is not FreshnessState.FRESH:
        return _unknown_tx_frequency_refusal("not-observed", None)
    max_age = field.max_age
    generated_at = snapshot.generated_at_monotonic
    last_observed = field.last_observed_monotonic
    if (
        type(field.provider_generation) is not int
        or type(snapshot.provider_generation) is not int
        or snapshot.provider_generation < 0
        or any(
            not isinstance(value, (int, float))
            or isinstance(value, bool)
            or not isfinite(value)
            for value in (generated_at, last_observed, max_age)
        )
        or max_age is None
        or max_age <= 0
        or last_observed < 0
        or generated_at < last_observed
    ):
        return _unknown_tx_frequency_refusal("contradiction", None)
    if field.provider_generation != snapshot.provider_generation:
        return _unknown_tx_frequency_refusal("stale", None)
    assert isinstance(max_age, (int, float)) and not isinstance(max_age, bool)
    if generated_at - last_observed >= max_age:
        return _unknown_tx_frequency_refusal("stale", None)
    return tx_frequency_unknown_refusal(field.value)


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
#
# The voice TX memory transmit command 0x28 sub 0x00 joins the family by
# parity with a TX start: a non-zero data byte starts RF with no operator
# key-down and has no typed counterpart, so unlike 0x1C every payload form
# stays in the family (the stop ``28 00 00``, no data byte, and extra data
# bytes included), and exactly one zero data byte is the de-key.  Raw CW
# ``0x17`` is not family: the typed ``send_cw_text`` path is not interlocked.

_CIV_FRAME_HEAD = 0xFE
_CIV_FRAME_TAIL = 0xFD
_CIV_FRAME_COLLISION = 0xFC
_CIV_TX_FAMILY_COMMAND = 0x1C
_CIV_TX_FAMILY_SUBS = (0x00, 0x01)  # PTT, tuner
_CIV_VOICE_TX_MEMORY_COMMAND = 0x28
_CIV_VOICE_TX_MEMORY_SUB = 0x00
_CIV_RECEIVER_PREFIX_COMMAND = 0x29
# Commands whose payload carries a sub byte right after the command byte.
_CIV_SUB_BYTE_COMMANDS = (_CIV_TX_FAMILY_COMMAND, _CIV_VOICE_TX_MEMORY_COMMAND)


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
        if inner == _CIV_TX_FAMILY_COMMAND:
            if len(data) < 3:
                return True
            return data[2] in _CIV_TX_FAMILY_SUBS and bool(data[3:])
        if inner == _CIV_VOICE_TX_MEMORY_COMMAND:
            if len(data) < 3:
                return True
            return data[2] == _CIV_VOICE_TX_MEMORY_SUB
        return False
    if command == _CIV_TX_FAMILY_COMMAND:
        return sub in _CIV_TX_FAMILY_SUBS and bool(data)
    if command == _CIV_VOICE_TX_MEMORY_COMMAND:
        return sub == _CIV_VOICE_TX_MEMORY_SUB
    return False


def _unwrap_civ_frame(frame: bytes) -> tuple[int, int | None, bytes] | None:
    """Parse one whole CI-V envelope into its ``FE FE to from cmd [sub] data
    FD`` parts.

    Returns ``(command, sub, data)``: for a TX-family command (0x1C or the
    voice TX memory 0x28) ``sub`` is its sub byte and ``data`` the bytes
    after it up to the trailing ``FD``; for the 0x29 receiver prefix
    ``sub`` is None and ``data`` is every byte after the command up to the
    trailing ``FD``; for any other command ``sub`` is None and ``data`` is
    empty.  Returns None when the envelope cannot be parsed: a frame
    shorter than the minimum, a bad head or tail, an ``FE``/``FD``/``FC``
    byte between the leading ``FE FE`` and the final ``FD``, or a
    sub-byte command without its sub byte.  This is the one envelope
    parser shared by :func:`classify_raw_civ_frame_tx_family` and
    :func:`is_exact_raw_dekey_frame`.
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
    if command in _CIV_SUB_BYTE_COMMANDS:
        if len(frame) < 7:
            return None
        return command, frame[5], frame[6:-1]
    if command == _CIV_RECEIVER_PREFIX_COMMAND:
        return command, None, frame[5:-1]
    return command, None, b""


def classify_raw_civ_frame_tx_family(frame: bytes) -> bool | None:
    """Byte-level TX-family verdict for one whole CI-V frame.

    Parses the envelope with :func:`_unwrap_civ_frame`, then defers to
    :func:`_is_tx_family_civ_command`.  A payload holds exactly one frame:
    any ``FE``, ``FD``, or ``FC`` byte between the leading ``FE FE`` and the
    final ``FD`` is unparseable.  Returns None when the envelope cannot be
    parsed; a managed caller must refuse on None as well as on True.
    """

    unwrapped = _unwrap_civ_frame(frame)
    if unwrapped is None:
        return None
    return _is_tx_family_civ_command(*unwrapped)


def _is_exact_dekey_triple(command: int, sub: int | None, data: bytes) -> bool:
    """True when an unwrapped ``(command, sub, data)`` triple is a de-key.

    Covers the PTT/tuner off actuation (``1C`` with sub 0x00/0x01 and a
    single zero data byte) and the voice TX memory stop (``28 00 00``).
    """

    if command == _CIV_TX_FAMILY_COMMAND:
        return sub in _CIV_TX_FAMILY_SUBS and data == b"\x00"
    if command == _CIV_VOICE_TX_MEMORY_COMMAND:
        return sub == _CIV_VOICE_TX_MEMORY_SUB and data == b"\x00"
    return False


def is_exact_raw_dekey_frame(frame: bytes) -> bool:
    """True when one whole well-formed frame is exactly a raw de-key.

    The frame must unwrap — through the same :func:`_unwrap_civ_frame`
    parser the TX-family classifier uses — to the PTT-off actuation
    (``1C 00 00``), the tuner-off actuation (``1C 01 00``), or the voice
    TX memory stop (``28 00 00``), bare or inside a 0x29 receiver prefix;
    the 0x29 form must be exactly receiver plus inner actuation.
    Anything with extra payload bytes, a different sub or data value, or
    an unparseable envelope is not a de-key.  The unmanaged HTTP raw
    seat, the unmanaged rigctld ``w`` seat, and the poller's
    ``send_civ`` seat exempt exactly these frames from their RF-state
    refusal so that a raw de-key stays as easy to execute as the typed
    one, which ``classify_tx_interlock`` never refuses (``PttOff`` and
    tuner-off are ALWAYS_PASS).
    """

    unwrapped = _unwrap_civ_frame(frame)
    if unwrapped is None:
        return False
    command, sub, data = unwrapped
    if command == _CIV_RECEIVER_PREFIX_COMMAND:
        return len(data) == 4 and _is_exact_dekey_triple(data[1], data[2], data[3:])
    return _is_exact_dekey_triple(command, sub, data)


def send_civ_frame(command: int, sub: int | None, data: bytes) -> bytes | None:
    """Build the wire frame a ``send_civ`` / ``SendCiv`` would send.

    The same ``build_civ_frame(0x00, 0x00, command, sub, data)``
    construction the managed ingress refuses on.  Returns None when
    the frame cannot be built (a command or sub byte outside 0-0xFF);
    each gate then treats the command as not exempt and captures or
    refuses it.
    """

    try:
        return build_civ_frame(0x00, 0x00, command, sub, data)
    except (TypeError, ValueError):
        return None


def _is_transmission_end_triple(command: int, sub: int | None, data: bytes) -> bool:
    """True when an unwrapped ``(command, sub, data)`` triple ends a transmission.

    The PTT-off actuation (``1C 00 00``) and the voice TX memory stop
    (``28 00 00``) end one; the tuner-off actuation (``1C 01 00``) does
    not.
    """

    if command == _CIV_TX_FAMILY_COMMAND:
        return sub == 0x00 and data == b"\x00"  # the PTT sub only
    if command == _CIV_VOICE_TX_MEMORY_COMMAND:
        return sub == _CIV_VOICE_TX_MEMORY_SUB and data == b"\x00"
    return False


def is_exact_raw_transmission_end_frame(frame: bytes) -> bool:
    """True when one whole well-formed frame exactly ends a transmission.

    The frame must unwrap — through the same :func:`_unwrap_civ_frame`
    parser the TX-family classifier uses — to the PTT-off actuation
    (``1C 00 00``) or the voice TX memory stop (``28 00 00``), bare or
    inside a 0x29 receiver prefix; the 0x29 form must be exactly
    receiver plus inner actuation.  The tuner-off actuation
    (``1C 01 00``) does not end a transmission here, and neither does
    anything with extra payload bytes, a different sub or data value,
    or an unparseable envelope.
    """

    unwrapped = _unwrap_civ_frame(frame)
    if unwrapped is None:
        return False
    command, sub, data = unwrapped
    if command == _CIV_RECEIVER_PREFIX_COMMAND:
        return len(data) == 4 and _is_transmission_end_triple(
            data[1], data[2], data[3:]
        )
    return _is_transmission_end_triple(command, sub, data)
