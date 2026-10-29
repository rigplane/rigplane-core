"""TCI command grammar: parse and format as pure functions.

A command is ``name:arg,arg;``. Several commands may share one line,
each terminated by ``;``. An argument-less command (``ready;``) is a
read; anything carrying arguments is a set. The rules below are spec
4.7: empty arguments are discarded, booleans are ``true``/``1``
(case-insensitive), names are case-insensitive, and a structurally
unknown name still parses — the leaf has no command dictionary, so the
adapter decides what a name means (spec 4.7 rule 5).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

__all__ = [
    "TciCommand",
    "format_command",
    "parse_bool",
    "parse_int",
    "parse_line",
]

_INT_RE = re.compile(r"[+-]?\d+")

_TRUE_STRINGS = frozenset({"true", "1"})
_FALSE_STRINGS = frozenset({"false", "0"})


@dataclass(frozen=True)
class TciCommand:
    """One parsed TCI command: a lowercased name plus string arguments."""

    name: str
    args: tuple[str, ...] = ()

    @property
    def is_read(self) -> bool:
        """True when the command carries no arguments (a read)."""
        return not self.args


def parse_line(line: str) -> list[TciCommand]:
    """Parse one text line into commands, skipping empty segments.

    Segments without a name (``:1;``) are skipped: a command with an
    empty name can never be answered or routed.
    """
    commands: list[TciCommand] = []
    for segment in line.split(";"):
        segment = segment.strip()
        if not segment:
            continue
        name, separator, rest = segment.partition(":")
        name = name.strip().lower()
        if not name:
            continue
        if not separator:
            commands.append(TciCommand(name=name))
            continue
        args = tuple(arg for arg in (a.strip() for a in rest.split(",")) if arg)
        commands.append(TciCommand(name=name, args=args))
    return commands


def format_command(name: str, args: Iterable[str] = ()) -> str:
    """Format one command in canonical form (lowercased name, ``;`` end)."""
    body = ",".join(args)
    canonical = name.strip().lower()
    if body:
        return f"{canonical}:{body};"
    return f"{canonical};"


def parse_bool(value: str) -> bool | None:
    """Parse a TCI boolean; ``None`` when the value is not a boolean."""
    lowered = value.strip().lower()
    if lowered in _TRUE_STRINGS:
        return True
    if lowered in _FALSE_STRINGS:
        return False
    return None


def parse_int(value: str) -> int | None:
    """Parse a TCI integer (frequencies, levels); ``None`` if not one."""
    text = value.strip()
    if _INT_RE.fullmatch(text) is None:
        return None
    return int(text, 10)
