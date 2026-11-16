"""Checks journal contract (MOR-4464 B1, RED first).

The journal is written next to the output file before a check's first
write and deleted after a verified restore. A run that finds one
prints its originals and leaves the file for the user to delete.
"""

from __future__ import annotations

from pathlib import Path


def test_journal_round_trip_through_parser(tmp_path: Path) -> None:
    from rigplane.probe.journal import ChecksJournal, read_journal, write_journal

    path = tmp_path / "probe.json.checks-journal.json"
    journal = ChecksJournal(
        check_id="synth-freq-mode",
        check_title="Synthetic frequency and mode",
        originals={"get_freq": "FA014074000;", "get_mode": "MD2;"},
        saved_utc="2026-11-16T00:00:00+00:00",
    )
    write_journal(path, journal)
    assert read_journal(path) == journal


def test_journal_rejects_bad_shape(tmp_path: Path) -> None:
    import pytest

    from rigplane.probe.journal import read_journal

    path = tmp_path / "probe.json.checks-journal.json"
    path.write_text('{"check_id": 1}', encoding="utf-8")
    with pytest.raises(ValueError):
        read_journal(path)


def test_find_pending_journal_next_to_out(tmp_path: Path) -> None:
    from rigplane.probe.journal import find_pending_journal, write_journal
    from rigplane.probe.journal import ChecksJournal

    out = tmp_path / "probe.json"
    journal_path = tmp_path / "probe.json.checks-journal.json"
    write_journal(
        journal_path,
        ChecksJournal(
            check_id="synth",
            check_title="Synthetic",
            originals={"get_freq": "FA014074000;"},
            saved_utc="2026-11-16T00:00:00+00:00",
        ),
    )
    assert find_pending_journal(str(out), tmp_path) == journal_path


def test_no_journal_no_pending(tmp_path: Path) -> None:
    from rigplane.probe.journal import find_pending_journal

    assert (
        find_pending_journal(str(tmp_path / "probe.json"), tmp_path) is None
    )
