"""The write-check journal (MOR-4464 B1b, design S1): the hand-settable
originals, fsynced before a check's first write, removed after a verified
restore. A journal left behind is printed (``leftover_journal_line``), never
restored by itself."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import platformdirs

from rigplane.probe.model import _need_mapping, _need_str
from rigplane.probe.render import journal_unreadable_line

__all__ = [
    "ChecksJournal",
    "journal_path",
    "leftover_journal_line",
    "read_journal",
    "remove_journal",
    "write_journal",
]

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


def journal_path() -> Path:
    """The one journal place per user, whatever the output file is called."""
    return Path(platformdirs.user_state_path("rigplane")) / "probe-checks-journal.json"


def write_journal(path: Path, journal: ChecksJournal, *, new: bool = False) -> None:
    """Write and fsync a whole temporary file, publish it, fsync the folder;
    returns only when durable. ``new``: publish by hard link, which raises
    ``FileExistsError`` when any journal is already there (another run's)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    with os.fdopen(handle, "w", encoding="utf-8") as stream:
        stream.write(journal.to_json())
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(name, path) if new else os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)
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


def leftover_journal_line(path: Path, *, with_path: bool = True) -> str | None:
    """The one message a probe start prints for a journal left behind (B1c
    wires it into every probe command); ``None`` without one, a plain line
    when it cannot be read (naming the file unless ``with_path`` is false,
    as for a refusal saved in the capture). Print only: nothing is sent."""
    try:
        journal = read_journal(path)
    except (OSError, ValueError):
        return journal_unreadable_line(str(path) if with_path else None)
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
