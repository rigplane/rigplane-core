"""``GET /api/v1/memories``: query parsing and the JSON shape (MOR-3627).

Pure helpers: the server method owns the radio access and the HTTP replies.
The response body is a typed wire contract (``MemoryPagePayload`` and the
nested ``MemoryEntryPayload``/``MemoryBlockPayload``); the HTTP consumer's
DTO (``frontend/src/lib/types/memory-list.ts``) declares the same fields.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, NotRequired, TypedDict

from rigplane.core.types import (
    MemoryBlock,
    MemoryEntry,
    MemoryProfile,
    RepeaterShiftDirection,
)

DEFAULT_PAGE = 25
MAX_PAGE = 50

DuplexValue = Literal["simplex", "plus", "minus"]

_DUPLEX: dict[RepeaterShiftDirection, DuplexValue] = {
    RepeaterShiftDirection.SIMPLEX: "simplex",
    RepeaterShiftDirection.PLUS: "plus",
    RepeaterShiftDirection.MINUS: "minus",
}


class MemoryRangePayload(TypedDict):
    """An inclusive ``first``/``last`` bound pair of the page response."""

    first: int
    last: int


class MemoryBlockPayload(TypedDict):
    """The second (TX/split) channel block of a stored entry (MOR-3648).

    Carried so a read-modify-write sees what the write API cannot set
    instead of silently losing it.
    """

    frequencyHz: int
    mode: str
    filter: int
    dataMode: bool
    duplex: DuplexValue | None
    offsetHz: int
    toneMode: str | None
    toneCentiHz: int
    tsqlCentiHz: int
    dtcsCode: int
    dtcsPolarity: int


class MemoryEntryPayload(TypedDict):
    """One slot as JSON; an empty slot carries only its address.

    The MOR-3648 additions -- ``filter``, ``dtcsPolarity``, ``split`` and
    ``txBlock`` -- are contract fields, typed here, not re-guessed dict
    keys at the consumer.
    """

    group: int
    channel: int
    empty: bool
    name: NotRequired[str]
    frequencyHz: NotRequired[int | None]
    mode: NotRequired[str | None]
    dataMode: NotRequired[bool]
    duplex: NotRequired[DuplexValue | None]
    offsetHz: NotRequired[int | None]
    toneMode: NotRequired[str | None]
    toneCentiHz: NotRequired[int | None]
    tsqlCentiHz: NotRequired[int | None]
    dtcsCode: NotRequired[int | None]
    filter: NotRequired[int | None]
    dtcsPolarity: NotRequired[int | None]
    split: NotRequired[bool]
    txBlock: NotRequired[MemoryBlockPayload | None]


class MemoryPagePayload(TypedDict):
    """The response body: the profile's bounds plus the requested page."""

    addressing: Literal["group_channel", "band_channel", "channel"]
    group: int
    groups: MemoryRangePayload
    channels: MemoryRangePayload
    recall: bool
    entries: list[MemoryEntryPayload]


def _int_param(query: Mapping[str, list[str]], name: str, default: int) -> int:
    raw = query.get(name)
    if not raw:
        return default
    try:
        return int(raw[0])
    except ValueError:
        raise ValueError(f"{name} must be an integer") from None


def parse_memory_query(
    query: Mapping[str, list[str]], memory: MemoryProfile
) -> tuple[int, list[int]]:
    """Return ``(group, channels)`` for ``?group=&start=&count=``.

    Defaults to the first group, the first channel and ``DEFAULT_PAGE``
    channels; the page is clipped at the profile's last channel. Anything
    outside the profile's bounds is a ``ValueError``.
    """
    group = _int_param(query, "group", memory.first_group)
    start = _int_param(query, "start", memory.first_channel)
    count = _int_param(query, "count", DEFAULT_PAGE)
    if not memory.first_group <= group <= memory.last_group:
        raise ValueError(
            f"group must be {memory.first_group}-{memory.last_group}, got {group}"
        )
    if not memory.first_channel <= start <= memory.last_channel:
        raise ValueError(
            f"start must be {memory.first_channel}-{memory.last_channel}, got {start}"
        )
    if not 1 <= count <= MAX_PAGE:
        raise ValueError(f"count must be 1-{MAX_PAGE}, got {count}")
    return group, list(range(start, min(start + count, memory.last_channel + 1)))


def _block_payload(block: MemoryBlock) -> MemoryBlockPayload:
    """The second (TX/split) channel block as JSON (MOR-3648)."""
    return {
        "frequencyHz": block.frequency_hz,
        "mode": block.mode,
        "filter": block.filter,
        "dataMode": block.data_mode,
        "duplex": _DUPLEX.get(block.duplex) if block.duplex is not None else None,
        "offsetHz": block.offset_hz,
        "toneMode": block.tone_mode.value if block.tone_mode else None,
        "toneCentiHz": block.tone_centihz,
        "tsqlCentiHz": block.tsql_centihz,
        "dtcsCode": block.dtcs_code,
        "dtcsPolarity": block.dtcs_polarity,
    }


def entry_payload(entry: MemoryEntry) -> MemoryEntryPayload:
    """One slot as JSON; an empty slot carries only its address."""
    payload: MemoryEntryPayload = {
        "group": entry.group,
        "channel": entry.channel,
        "empty": entry.empty,
    }
    if entry.empty:
        return payload
    payload.update(
        {
            "name": entry.name,
            "frequencyHz": entry.frequency_hz,
            "mode": entry.mode,
            "dataMode": entry.data_mode,
            "duplex": _DUPLEX.get(entry.duplex) if entry.duplex is not None else None,
            "offsetHz": entry.offset_hz,
            "toneMode": entry.tone_mode.value if entry.tone_mode else None,
            "toneCentiHz": entry.tone_centihz,
            "tsqlCentiHz": entry.tsql_centihz,
            "dtcsCode": entry.dtcs_code,
            # MOR-3648: what a read-modify-write must not silently lose.
            "filter": entry.filter,
            "dtcsPolarity": entry.dtcs_polarity,
            "split": entry.split,
            "txBlock": (
                _block_payload(entry.tx_block) if entry.tx_block is not None else None
            ),
        }
    )
    return payload


def page_payload(
    memory: MemoryProfile, group: int, entries: list[MemoryEntry]
) -> MemoryPagePayload:
    """The response body: the profile's bounds plus the requested page."""
    return {
        "addressing": memory.addressing,
        "group": group,
        "groups": {"first": memory.first_group, "last": memory.last_group},
        "channels": {"first": memory.first_channel, "last": memory.last_channel},
        "recall": memory.recall,
        "entries": [entry_payload(entry) for entry in entries],
    }
