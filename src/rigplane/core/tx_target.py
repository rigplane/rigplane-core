"""Backend-neutral transmit-target state values."""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, ClassVar, Literal, cast

if TYPE_CHECKING:
    from rigplane.core.state_pipeline_contracts import FieldPath
    from rigplane.core.state_store import StateSnapshot

TX_TARGET_PATH = "global.tx_state.tx_target"
TX_PROOF_CLOCK = "monotonic"
_dispatch_invalidation: ContextVar[Callable[[], None] | None] = ContextVar(
    "tx_write_invalidation", default=None
)


@contextmanager
def tx_write_dispatch(invalidate: Callable[[], None] | None) -> Iterator[None]:
    """Carry this command's invalidation to an existing physical-write hook."""
    token = _dispatch_invalidation.set(invalidate)
    try:
        yield
    finally:
        _dispatch_invalidation.reset(token)


def current_tx_write_invalidation() -> Callable[[], None] | None:
    return _dispatch_invalidation.get()


def is_tx_target_input(path: FieldPath) -> bool:
    return str(path) == TX_TARGET_PATH or path.name in {
        "freq_hz",
        "split",
        "active_slot",
        "active",
    }


def tx_target_write_paths(
    snapshot: StateSnapshot, name: str, params: Mapping[str, Any]
) -> tuple[FieldPath, ...]:
    """Facts a dispatched operation can change, independent of readback routing."""
    from rigplane.core.state_pipeline_contracts import FieldPath

    receiver = str(params.get("receiver", 0))
    frequency: str | None = None
    split = False
    identity = False
    if name in {"set_freq", "set_frequency"}:
        frequency = "active"
    elif name == "set_vfo_freq":
        frequency = (
            "active"
            if params.get("slot") == params.get("expected_active_slot")
            else "unselected"
        )
    elif name == "set_split_freq":
        frequency = "unselected"
    elif name in {"set_split", "set_split_vfo"}:
        split = True
    elif name in {
        "set_vfo",
        "select_vfo",
        "vfo_swap",
        "vfo_equalize",
        "set_band",
        "set_memory_mode",
        "memory_to_vfo",
        "quick_split",
        "quick_split_trigger",
    }:
        frequency, split, identity = "both", True, True
    elif name in {"send_civ", "send_raw", "raw_civ_transaction"}:
        if "receiver" not in params:
            try:
                active_receiver = snapshot.field(
                    FieldPath.global_("slow_state", "active")
                ).value
            except KeyError:
                active_receiver = None
            if active_receiver in ("MAIN", "SUB"):
                receiver = "1" if active_receiver == "SUB" else "0"
        if name == "send_raw":
            frame = params.get("frame_bytes")
            if (
                not isinstance(frame, bytes)
                or len(frame) < 6
                or frame[:2] != b"\xfe\xfe"
                or frame[-1:] != b"\xfd"
            ):
                return ()
            command, data = frame[4], frame[5:-1]
        else:
            command, data = params.get("command"), params.get("data", b"")
            data = bytes.fromhex(data) if isinstance(data, str) else bytes(data)
            if params.get("sub") is not None:
                data = bytes([params["sub"]]) + data
        if command == 0x29 and len(data) >= 2:
            receiver, command, data = str(data[0]), data[1], data[2:]
        if command == 0x05 and len(data) == 5:
            frequency = "active"
        elif command == 0x25 and len(data) == 6:
            frequency = "unselected" if data[0] == 1 else "active"
        elif (
            command == 0x07
            and data in (b"", b"\x00", b"\x01", b"\xa0", b"\xb0", b"\xd0", b"\xd1")
        ) or command in (0x08, 0x0A):
            frequency, split, identity = "both", True, True
        elif command == 0x0F and data in (b"\x00", b"\x01"):
            split = True
    if frequency is None and not split and not identity:
        return ()
    paths = {FieldPath.parse(TX_TARGET_PATH)}
    if split:
        paths.add(FieldPath.global_("tx_state", "split"))
    if identity:
        paths.add(FieldPath.active_slot(receiver))
    if frequency is not None:
        factories = (
            (FieldPath.active, FieldPath.unselected)
            if frequency == "both"
            else (FieldPath.active if frequency == "active" else FieldPath.unselected,)
        )
        paths.update(factory(receiver, "freq_mode", "freq_hz") for factory in factories)
        # Preserve the other receiver and the untouched relative VFO.
        if frequency in {"active", "both"}:
            paths.add(FieldPath.receiver(receiver, "freq_mode", "freq_hz"))
        try:
            active = snapshot.field(FieldPath.active_slot(receiver)).value
        except KeyError:
            active = None
        for slot in ("A", "B"):
            if (
                frequency == "both"
                or active not in ("A", "B")
                or (slot == active) == (frequency == "active")
            ):
                paths.add(FieldPath.vfo_slot(receiver, slot, "freq_mode", "freq_hz"))
    return tuple(paths)


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
