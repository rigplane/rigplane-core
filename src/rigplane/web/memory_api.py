"""``GET /api/v1/memories``: query parsing and the JSON shape (MOR-3627).

Pure helpers: the server method owns the radio access and the HTTP replies.
The response body is a typed wire contract (``MemoryPagePayload`` and the
nested ``MemoryEntryPayload``/``MemoryBlockPayload``); the HTTP consumer's
DTO (``frontend/src/lib/types/memory-list.ts``) declares the same fields.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Literal, NotRequired, Self, TypedDict

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


#: The query keys of ``GET /api/v1/memories``: the published allowlist
#: (MOR-4252). Core's own parse stays lenient for released clients — it
#: ignores keys outside this set — but a gateway may refuse them; either
#: way this set is the single source both sides track. Add a key here
#: AND in :class:`MemoriesQuery` when the endpoint grows one;
#: ``tests/contracts/test_query_contracts.py`` fails when the two drift
#: apart.
MEMORIES_QUERY_KEYS: Final[frozenset[str]] = frozenset({"count", "group", "start"})


@dataclass(frozen=True, slots=True)
class MemoriesQuery:
    """The published query contract of ``GET /api/v1/memories`` (MOR-4252).

    One definition, two acceptance levels:

    * :meth:`accept` — Core's own parse, kept lenient for released
      clients: keys outside :data:`MEMORIES_QUERY_KEYS` are ignored, a
      repeated key keeps its first value, and a non-integer value is a
      ``ValueError``. ``None`` fields take the server's defaults
      (the profile's first group/channel, ``DEFAULT_PAGE``).
    * :meth:`parse` — the strict contract a gateway may enforce: only
      :data:`MEMORIES_QUERY_KEYS`, each key at most once, every value an
      integer, and ``count`` within ``1..MAX_PAGE``. Returns ``None`` on
      any violation. The ``group``/``start`` bounds depend on the radio
      profile, which only the producer knows; they stay with
      :func:`parse_memory_query`.
    """

    group: int | None = None
    start: int | None = None
    count: int | None = None

    @staticmethod
    def _int(values: list[str], name: str) -> int:
        try:
            return int(values[0])
        except ValueError:
            raise ValueError(f"{name} must be an integer") from None

    @classmethod
    def accept(cls, query: Mapping[str, list[str]]) -> Self:
        """Core's own acceptance (lenient; see the class docstring)."""
        return cls(
            group=(cls._int(raw, "group") if (raw := query.get("group")) else None),
            start=(cls._int(raw, "start") if (raw := query.get("start")) else None),
            count=(cls._int(raw, "count") if (raw := query.get("count")) else None),
        )

    @classmethod
    def parse(cls, query: Mapping[str, list[str]]) -> Self | None:
        """The strict gateway-grade contract; ``None`` means refuse."""
        if any(key not in MEMORIES_QUERY_KEYS for key in query):
            return None
        values: dict[str, int] = {}
        for name, raw in query.items():
            if len(raw) != 1:
                return None
            try:
                values[name] = int(raw[0])
            except ValueError:
                return None
        count = values.get("count")
        if count is not None and not 1 <= count <= MAX_PAGE:
            return None
        return cls(group=values.get("group"), start=values.get("start"), count=count)


def parse_memory_query(
    query: Mapping[str, list[str]], memory: MemoryProfile
) -> tuple[int, list[int]]:
    """Return ``(group, channels)`` for ``?group=&start=&count=``.

    Parses through :class:`MemoriesQuery` — the published contract
    (:data:`MEMORIES_QUERY_KEYS`) is the single source — then applies the
    profile's bounds. Defaults to the first group, the first channel and
    ``DEFAULT_PAGE`` channels; the page is clipped at the profile's last
    channel. Anything outside the profile's bounds is a ``ValueError``.
    """
    parsed = MemoriesQuery.accept(query)
    group = parsed.group if parsed.group is not None else memory.first_group
    start = parsed.start if parsed.start is not None else memory.first_channel
    count = parsed.count if parsed.count is not None else DEFAULT_PAGE
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
