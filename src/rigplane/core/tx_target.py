"""Backend-neutral transmit-target state values."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, cast

TxReceiver = Literal["MAIN", "SUB"]
TxSlot = Literal["A", "B"]
TxTargetUnknownReason = Literal[
    "not-observed",
    "stale",
    "unsupported",
    "contradiction",
]

_RECEIVERS = frozenset(("MAIN", "SUB"))
_SLOTS = frozenset(("A", "B"))
_UNKNOWN_REASONS = frozenset(("not-observed", "stale", "unsupported", "contradiction"))
_KNOWN_KEYS = frozenset(("status", "receiver", "slot", "frequencyHz"))
_UNKNOWN_KEYS = frozenset(("status", "reason"))


@dataclass(frozen=True, slots=True)
class KnownTxTarget:
    status: ClassVar[Literal["known"]] = "known"
    receiver: TxReceiver
    slot: TxSlot | None
    frequency_hz: int | None

    def __post_init__(self) -> None:
        if not isinstance(self.receiver, str) or self.receiver not in _RECEIVERS:
            raise ValueError(f"invalid TX target receiver: {self.receiver!r}")
        if self.slot is not None and (
            not isinstance(self.slot, str) or self.slot not in _SLOTS
        ):
            raise ValueError(f"invalid TX target slot: {self.slot!r}")
        frequency = self.frequency_hz
        if frequency is not None and (
            not isinstance(frequency, int)
            or isinstance(frequency, bool)
            or frequency <= 0
        ):
            raise ValueError(
                "TX target frequency_hz must be a positive integer or None"
            )

    def to_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "receiver": self.receiver,
            "slot": self.slot,
            "frequencyHz": self.frequency_hz,
        }


@dataclass(frozen=True, slots=True)
class UnknownTxTarget:
    status: ClassVar[Literal["unknown"]] = "unknown"
    reason: TxTargetUnknownReason

    def __post_init__(self) -> None:
        if not isinstance(self.reason, str) or self.reason not in _UNKNOWN_REASONS:
            raise ValueError(f"invalid unknown TX target reason: {self.reason!r}")

    def to_dict(self) -> dict[str, object]:
        return {"status": self.status, "reason": self.reason}


TxTarget = KnownTxTarget | UnknownTxTarget


def tx_target_from_dict(value: Mapping[str, Any]) -> TxTarget:
    status = value.get("status")
    if status == "known":
        if frozenset(value) != _KNOWN_KEYS:
            raise ValueError("known TX target must contain only its canonical fields")
        return KnownTxTarget(
            receiver=cast(TxReceiver, value["receiver"]),
            slot=cast(TxSlot | None, value["slot"]),
            frequency_hz=cast(int | None, value["frequencyHz"]),
        )
    if status == "unknown":
        if frozenset(value) != _UNKNOWN_KEYS:
            raise ValueError("unknown TX target must contain only status and reason")
        return UnknownTxTarget(
            reason=cast(TxTargetUnknownReason, value["reason"]),
        )
    raise ValueError(f"invalid TX target status: {status!r}")


def validate_tx_target(value: object) -> TxTarget:
    if not isinstance(value, (KnownTxTarget, UnknownTxTarget)):
        raise TypeError("TX target observation value must be a TxTarget")
    return value
