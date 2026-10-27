"""``GET /api/v1/memories``: query parsing and the JSON shape (MOR-3627).

Pure helpers: the server method owns the radio access and the HTTP replies.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from rigplane.core.types import MemoryEntry, MemoryProfile, RepeaterShiftDirection

DEFAULT_PAGE = 25
MAX_PAGE = 50

_DUPLEX = {
    RepeaterShiftDirection.SIMPLEX: "simplex",
    RepeaterShiftDirection.PLUS: "plus",
    RepeaterShiftDirection.MINUS: "minus",
}


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


def entry_payload(entry: MemoryEntry) -> dict[str, Any]:
    """One slot as JSON; an empty slot carries only its address."""
    payload: dict[str, Any] = {
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
        }
    )
    return payload


def page_payload(
    memory: MemoryProfile, group: int, entries: list[MemoryEntry]
) -> dict[str, Any]:
    """The response body: the profile's bounds plus the requested page."""
    return {
        "addressing": memory.addressing,
        "group": group,
        "groups": {"first": memory.first_group, "last": memory.last_group},
        "channels": {"first": memory.first_channel, "last": memory.last_channel},
        "recall": memory.recall,
        "entries": [entry_payload(entry) for entry in entries],
    }
