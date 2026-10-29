"""Backend-neutral transmit-target state values."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar, Literal, Protocol, cast

TxReceiver = Literal["MAIN", "SUB"]
TxSlot = Literal["A", "B"]
TxTargetUnknownReason = Literal[
    "not-observed",
    "stale",
    "unsupported",
    "contradiction",
]

TxPermitRefusalReason = Literal[
    "receive-only-band",
    "outside-tx-range",
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


class TxBandRow(Protocol):
    """The band-row facts the permit reads (structurally ``BandInfo``).

    ``core`` sits below ``profiles`` in the layer order, so the permit takes
    rows through this protocol instead of importing the profile types.
    """

    @property
    def name(self) -> str: ...

    @property
    def start(self) -> int: ...

    @property
    def end(self) -> int: ...

    @property
    def rx_only(self) -> bool: ...


@dataclass(frozen=True, slots=True)
class TxPermitDecision:
    """Verdict of :func:`evaluate_tx_frequency_permit` (MOR-3632).

    ``permitted`` True also covers every not-applicable case (see the
    function).  On a refusal ``reason`` is the stable machine vocabulary;
    ``frequency_hz`` and ``band_name`` carry the evidence for a truthful
    message.
    """

    permitted: bool
    reason: TxPermitRefusalReason | None = None
    frequency_hz: int | None = None
    band_name: str | None = None


def evaluate_tx_frequency_permit(
    bands: Iterable[TxBandRow],
    target: TxTarget | None,
) -> TxPermitDecision:
    """Pure PTT-on band permit over the profile band rows (MOR-3632).

    The adversary is an operator typo or an external client keying up on a
    frequency the profile never permits for transmit.  ``bands`` is every
    band row of the active profile (``FreqRangeInfo.bands`` flattened);
    ``target`` is the server's current published TX target (already
    split-aware upstream) or None when the field is missing, stale, or
    malformed.  The permit is band-only and decides solely from a known
    target that carries a frequency:

    - no band rows at all: not applicable, PTT keeps the pre-permit
      behaviour (permitted);
    - target unknown (None, ``UnknownTxTarget``, or a known target with no
      frequency): not applicable (permitted) — refusing an unknown target is
      the job of the unknown-target guard (MOR-3633), so a radio that never
      publishes a target keeps keying;
    - frequency inside a non-``rx_only`` band: permitted (a TX-permitting row
      wins over an overlapping receive-only row, e.g. an HF band inside a
      general-coverage row);
    - frequency inside an ``rx_only`` band only: refused
      ``receive-only-band``;
    - frequency outside every band row: refused ``outside-tx-range``.

    A front-panel retune inside one poll interval is deliberately NOT
    covered here; the radio firmware remains the last line of defence.
    """

    rows = tuple(bands)
    if not rows:
        return TxPermitDecision(permitted=True)
    frequency = target.frequency_hz if isinstance(target, KnownTxTarget) else None
    if frequency is None:
        return TxPermitDecision(permitted=True)
    for band in rows:
        if not band.rx_only and band.start <= frequency <= band.end:
            return TxPermitDecision(
                permitted=True, frequency_hz=frequency, band_name=band.name
            )
    for band in rows:
        if band.rx_only and band.start <= frequency <= band.end:
            return TxPermitDecision(
                permitted=False,
                reason="receive-only-band",
                frequency_hz=frequency,
                band_name=band.name,
            )
    return TxPermitDecision(
        permitted=False, reason="outside-tx-range", frequency_hz=frequency
    )
