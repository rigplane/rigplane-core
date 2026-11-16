"""Typed contracts for the ``civ_pairs`` / ``cat_pairs`` fixture rows.

Every ``tests/fixtures/<radio>/civ_pairs.jsonl`` and ``cat_pairs.jsonl``
row is one wire frame of a recorded request/reply exchange. Producers (the
probe converter, the serial/CAT extractors), the file readers and the
replay tables all cross a module or file boundary on these rows, so they
share one contract instead of each re-guessing dictionary keys:

- :class:`CivPairRow` — ``window``, ``transport``, ``t``, ``dir``, ``frame``
  (CI-V frame as hex in JSON, ``bytes`` here);
- :class:`CatPairRow` — ``window``, ``transport``, ``t``, ``dir``, ``line``
  (one CAT line, ``;`` included).

``dir`` is ``c2r`` (the client sent it) or ``r2c`` (the radio sent it).
``from_json`` rejects a missing or unknown key, a wrong type and an
unknown ``dir`` with a :class:`FixtureRowError` naming the field;
``to_json`` returns the JSON object of the committed shape, and
:func:`dump_rows` writes it with sorted keys, compact separators and one
trailing newline per row.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

Direction = Literal["c2r", "r2c"]
_DIRECTIONS: tuple[Direction, ...] = ("c2r", "r2c")
_COMMON_KEYS = ("window", "transport", "t", "dir")


class FixtureRowError(ValueError):
    """A fixture row does not satisfy its contract."""


def _parse_common(
    obj: object, payload_key: str
) -> tuple[str, str, float, Direction, str]:
    """Validate the keys shared by both row kinds and return their values."""
    if not isinstance(obj, Mapping):
        raise FixtureRowError(
            f"fixture row must be an object, got {type(obj).__name__}"
        )
    expected = {*_COMMON_KEYS, payload_key}
    missing = sorted(expected - obj.keys())
    if missing:
        raise FixtureRowError(f"fixture row is missing {missing}")
    extra = sorted(str(key) for key in obj.keys() - expected)
    if extra:
        raise FixtureRowError(f"fixture row has unknown keys {extra}")
    for key in ("window", "transport", payload_key):
        if not isinstance(obj[key], str):
            raise FixtureRowError(f"fixture row {key!r} must be a string")
    stamp = obj["t"]
    if isinstance(stamp, bool) or not isinstance(stamp, (int, float)):
        raise FixtureRowError("fixture row 't' must be a number")
    direction = obj["dir"]
    if direction not in _DIRECTIONS:
        raise FixtureRowError(
            f"fixture row 'dir' must be 'c2r' or 'r2c', got {direction!r}"
        )
    return (
        cast(str, obj["window"]),
        cast(str, obj["transport"]),
        float(stamp),
        cast(Direction, direction),
        cast(str, obj[payload_key]),
    )


@dataclass(frozen=True, slots=True)
class CivPairRow:
    """One CI-V frame of a recorded exchange."""

    window: str
    transport: str
    t: float
    dir: Direction
    frame: bytes

    @classmethod
    def from_json(cls, obj: object) -> CivPairRow:
        """Parse one JSON object, rejecting anything off-contract."""
        window, transport, t, direction, frame_hex = _parse_common(obj, "frame")
        try:
            frame = bytes.fromhex(frame_hex)
        except ValueError as error:
            raise FixtureRowError(
                f"fixture row 'frame' is not hex: {frame_hex!r}"
            ) from error
        return cls(window, transport, t, direction, frame)

    def to_json(self) -> dict[str, Any]:
        return {
            "dir": self.dir,
            "frame": self.frame.hex(),
            "t": self.t,
            "transport": self.transport,
            "window": self.window,
        }


@dataclass(frozen=True, slots=True)
class CatPairRow:
    """One CAT line of a recorded exchange."""

    window: str
    transport: str
    t: float
    dir: Direction
    line: str

    @classmethod
    def from_json(cls, obj: object) -> CatPairRow:
        """Parse one JSON object, rejecting anything off-contract."""
        window, transport, t, direction, line = _parse_common(obj, "line")
        return cls(window, transport, t, direction, line)

    def to_json(self) -> dict[str, Any]:
        return {
            "dir": self.dir,
            "line": self.line,
            "t": self.t,
            "transport": self.transport,
            "window": self.window,
        }


def civ_rows(rows: Iterable[CivPairRow | Mapping[str, Any]]) -> list[CivPairRow]:
    """The typed rows of ``rows``: typed rows pass, JSON objects are parsed."""
    return [
        row if isinstance(row, CivPairRow) else CivPairRow.from_json(row)
        for row in rows
    ]


def cat_rows(rows: Iterable[CatPairRow | Mapping[str, Any]]) -> list[CatPairRow]:
    """The typed rows of ``rows``: typed rows pass, JSON objects are parsed."""
    return [
        row if isinstance(row, CatPairRow) else CatPairRow.from_json(row)
        for row in rows
    ]


def _json_lines(path: Path) -> Iterable[tuple[int, object]]:
    for number, text in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if text.strip():
            try:
                yield number, json.loads(text)
            except json.JSONDecodeError as error:
                raise FixtureRowError(f"{path}:{number}: not JSON: {error}") from error


def read_civ_rows(path: Path) -> list[CivPairRow]:
    """Every row of a ``civ_pairs.jsonl`` file, in file order."""
    rows: list[CivPairRow] = []
    for number, obj in _json_lines(path):
        try:
            rows.append(CivPairRow.from_json(obj))
        except FixtureRowError as error:
            raise FixtureRowError(f"{path}:{number}: {error}") from error
    return rows


def read_cat_rows(path: Path) -> list[CatPairRow]:
    """Every row of a ``cat_pairs.jsonl`` file, in file order."""
    rows: list[CatPairRow] = []
    for number, obj in _json_lines(path):
        try:
            rows.append(CatPairRow.from_json(obj))
        except FixtureRowError as error:
            raise FixtureRowError(f"{path}:{number}: {error}") from error
    return rows


def dump_rows(rows: Iterable[CivPairRow | CatPairRow]) -> str:
    """JSONL text of ``rows``: sorted keys, compact, one newline per row."""
    return "".join(
        json.dumps(row.to_json(), sort_keys=True, separators=(",", ":")) + "\n"
        for row in rows
    )
