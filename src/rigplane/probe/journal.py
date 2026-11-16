"""The write-check journal: the originals on disk before a first write.

Before a check's first write the engine records the originals (name
and exact reply text) next to the output file as
``<out>.checks-journal.json`` and deletes that file after a verified
restore. A journal left behind means the link died (or the program
stopped) mid-check: the next ``rigplane probe`` run prints the
originals and leaves the file until the user deletes it.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "ChecksJournal",
    "delete_journal",
    "find_pending_journal",
    "read_journal",
    "write_journal",
]


@dataclass(frozen=True, slots=True)
class ChecksJournal:
    """The originals of one unfinished check: what to set by hand."""

    check_id: str
    check_title: str
    originals: dict[str, str]
    saved_utc: str

    def to_json(self) -> str:
        """Pretty-printed JSON, one trailing newline."""
        return (
            json.dumps(
                {
                    "check_id": self.check_id,
                    "check_title": self.check_title,
                    "originals": dict(self.originals),
                    "saved_utc": self.saved_utc,
                },
                indent=1,
            )
            + "\n"
        )

    @classmethod
    def from_json(cls, text: str) -> ChecksJournal:
        """Parse and validate; bad shapes refuse with the field named."""
        try:
            document = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError(f"not a checks journal: invalid JSON ({error})") from error
        return _from_plain(document)


def _from_plain(document: Any) -> ChecksJournal:
    if not isinstance(document, dict):
        raise ValueError("field journal must be an object")
    for key in ("check_id", "check_title", "originals", "saved_utc"):
        if key not in document:
            raise ValueError(f"missing field {key}")
    for key in ("check_id", "check_title", "saved_utc"):
        if not isinstance(document[key], str):
            raise ValueError(f"field {key} must be a string")
    originals = document["originals"]
    if not isinstance(originals, dict) or not all(
        isinstance(name, str) and isinstance(value, str)
        for name, value in originals.items()
    ):
        raise ValueError("field originals must be an object of strings")
    return ChecksJournal(
        check_id=document["check_id"],
        check_title=document["check_title"],
        originals=dict(originals),
        saved_utc=document["saved_utc"],
    )


def write_journal(path: Path, journal: ChecksJournal) -> None:
    """Write the originals before the check's first write."""
    path.write_text(journal.to_json(), encoding="utf-8")


def read_journal(path: Path) -> ChecksJournal:
    """Read and validate a journal left by an unfinished check."""
    return ChecksJournal.from_json(path.read_text(encoding="utf-8"))


def delete_journal(path: Path) -> None:
    """Remove the journal after a verified restore; missing is fine."""
    path.unlink(missing_ok=True)


def find_pending_journal(out: str | None, default_dir: Path) -> Path | None:
    """A journal left by the previous run, if any.

    With ``--out`` the journal sits next to that path; otherwise the
    default save folder is scanned for ``*.checks-journal.json``.
    """
    if out is not None:
        candidate = Path(f"{out}.checks-journal.json")
        return candidate if candidate.exists() else None
    matches = sorted(default_dir.glob("*.checks-journal.json"))
    return matches[0] if matches else None
