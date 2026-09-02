"""Canonical transmit-state observation contract."""

from __future__ import annotations

from dataclasses import dataclass


TX_READ_DEADLINE_SECONDS: float = 0.3

RADIO_READBACK_SOURCES: frozenset[str] = frozenset(
    {"poll_response", "civ_unsolicited", "hamlib_response", "yaesu_poll_response"}
)


@dataclass(frozen=True, slots=True)
class TxStateReading:
    """One solicited transmit-state observation."""

    value: bool | None
    attributed: str | None = None
    source: str | None = None
    verified_readback: bool = False
    failure: str | None = None
