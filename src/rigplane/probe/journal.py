"""The write-check journal: the originals on disk before a check's first
write, next to the output file as ``<out>.checks-journal.json``. It is
deleted after a verified restore; a journal left behind means the link
died mid-check and the next ``rigplane probe`` run prints the originals.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from rigplane.probe.model import _need_fields, _need_mapping, _need_str, _need_str_map

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
        return json.dumps(asdict(self), indent=1) + "\n"

    @classmethod
    def from_json(cls, text: str) -> ChecksJournal:
        """Parse and validate; bad shapes refuse with the field named."""
        try:
            document = json.loads(text)
        except json.JSONDecodeError as error:
            raise ValueError(f"not a checks journal: invalid JSON ({error})") from error
        mapping = _need_mapping(document, "journal")
        _need_fields(mapping, "journal", cls)
        return cls(
            check_id=_need_str(mapping["check_id"], "check_id"),
            check_title=_need_str(mapping["check_title"], "check_title"),
            originals=_need_str_map(mapping["originals"], "originals"),
            saved_utc=_need_str(mapping["saved_utc"], "saved_utc"),
        )


def write_journal(path: Path, journal: ChecksJournal) -> None:
    path.write_text(journal.to_json(), encoding="utf-8")


def read_journal(path: Path) -> ChecksJournal:
    return ChecksJournal.from_json(path.read_text(encoding="utf-8"))


def delete_journal(path: Path) -> None:
    path.unlink(missing_ok=True)


def find_pending_journal(out: str | None, default_dir: Path) -> Path | None:
    """A journal left by the previous run: next to ``--out``, else the first
    ``*.checks-journal.json`` in the default save folder."""
    if out is not None:
        candidate = Path(f"{out}.checks-journal.json")
        return candidate if candidate.exists() else None
    return next(iter(sorted(default_dir.glob("*.checks-journal.json"))), None)
