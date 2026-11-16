"""The write-check journal (MOR-4464 B1b, design S1): write-ahead originals.

Before a check's first write byte, the values the check will put back are
on disk in hand-settable form (frequency in MHz, mode name, VFO name; never
internal codes), flushed and fsynced together with their directory. It is
removed only after a verified restore. A journal left behind means the
previous run died mid-check; the next probe start prints the originals
(``leftover_journal_line``) and restores nothing by itself.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from rigplane.probe.model import _need_mapping, _need_str

__all__ = [
    "ChecksJournal",
    "journal_path_for",
    "leftover_journal_line",
    "read_journal",
    "remove_journal",
    "write_journal",
]

_SUFFIX = ".checks-journal.json"
_KEYS = ("check_id", "check_title", "profile", "originals", "saved_utc", "now")


@dataclass(frozen=True, slots=True)
class ChecksJournal:
    """The originals of one unfinished check: setting name ("VFO-A") to the
    value to set by hand ("14.074 MHz"); ``now`` (after a failed restore) is
    the last value the check saw for each setting."""

    check_id: str
    check_title: str
    profile: str
    originals: tuple[tuple[str, str], ...]
    saved_utc: str
    now: tuple[tuple[str, str], ...] = ()

    def to_json(self) -> str:
        plain = {key: getattr(self, key) for key in _KEYS}
        plain["originals"], plain["now"] = dict(self.originals), dict(self.now)
        return json.dumps(plain, indent=1) + "\n"

    @classmethod
    def from_json(cls, text: str) -> ChecksJournal:
        """Parse and validate; a bad shape refuses with the field named."""
        try:
            document = _need_mapping(json.loads(text), "journal")
        except json.JSONDecodeError as error:
            raise ValueError(f"not a checks journal: invalid JSON ({error})") from None
        if missing := [key for key in _KEYS if key not in document]:
            raise ValueError(f"missing field {missing[0]}")
        pairs = {
            key: tuple(
                (name, _need_str(value, f"{key}.{name}"))
                for name, value in _need_mapping(document[key], key).items()
            )
            for key in ("originals", "now")
        }
        return cls(
            check_id=_need_str(document["check_id"], "check_id"),
            check_title=_need_str(document["check_title"], "check_title"),
            profile=_need_str(document["profile"], "profile"),
            originals=pairs["originals"],
            saved_utc=_need_str(document["saved_utc"], "saved_utc"),
            now=pairs["now"],
        )


def journal_path_for(out: Path) -> Path:
    """The journal next to one output file."""
    return out.with_name(out.name + _SUFFIX)


def write_journal(path: Path, journal: ChecksJournal) -> None:
    """Write, flush and fsync the journal, then fsync its directory (POSIX:
    the rename itself must survive a power cut). Returns only when durable."""
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        stream.write(journal.to_json())
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    if os.name == "posix":
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def read_journal(path: Path) -> ChecksJournal | None:
    """The journal at *path*, or ``None`` when there is none."""
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    return ChecksJournal.from_json(text)


def remove_journal(path: Path) -> None:
    path.unlink(missing_ok=True)


def leftover_journal_line(path: Path) -> str | None:
    """The one message a probe start prints for a journal left behind (B1c
    wires it into every probe command); ``None`` without one. Print only:
    nothing is sent to the radio."""
    journal = read_journal(path)
    if journal is None:
        return None
    now = dict(journal.now)
    if changed := [(n, now[n], v) for n, v in journal.originals if now.get(n, v) != v]:
        said = ". ".join(f"{n} is now {new}, it was {old}" for n, new, old in changed)
        them = "it" if len(changed) == 1 else "them"
        return (
            f"A check could not put the radio back: {said}. "
            f"Set {them} back by hand before transmitting."
        )
    values = ", ".join(f"{name} {value}" for name, value in journal.originals)
    return (
        "A check did not finish last time. "
        f"Before it the radio had: {values}. Please set these by hand."
    )
