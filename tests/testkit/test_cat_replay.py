"""``RecordedCatAnswerTable`` — recorded CAT request -> reply pairing.

Rows are built by hand here (the fixture shape: ``window``, ``dir``,
``line``); the table never sees a socket.
"""

from __future__ import annotations

from rigplane_testkit.yaesu.replay import RecordedCatAnswer, RecordedCatAnswerTable


def _rows(*pairs: tuple[str, str], window: str = "w") -> list[dict]:
    return [
        {"window": window, "t": i * 0.1, "dir": d, "line": line}
        for i, (d, line) in enumerate(pairs)
    ]


def test_data_refusal_and_silence_are_told_apart() -> None:
    table = RecordedCatAnswerTable.from_cat_pairs(
        _rows(
            ("c2r", "ID;"),
            ("r2c", "ID0840;"),
            ("c2r", "KS;"),
            ("r2c", "?;"),
            ("c2r", "LK;"),  # no reply before the next request: timed out
            ("c2r", "FA;"),
            ("r2c", "FA014074000;"),
        )
    )
    assert table.lookup("ID;") == RecordedCatAnswer("data", "ID0840;")
    assert table.lookup("KS;") == RecordedCatAnswer("refusal", "?;")
    assert table.lookup("LK;") == RecordedCatAnswer("silence", None)
    assert table.lookup("FA;") == RecordedCatAnswer("data", "FA014074000;")
    assert table.lookup("PS;") is None  # never asked: no recorded fact


def test_trailing_unanswered_request_is_silence() -> None:
    table = RecordedCatAnswerTable.from_cat_pairs(_rows(("c2r", "SD;")))
    assert table.lookup("SD;") == RecordedCatAnswer("silence", None)


def test_a_reply_for_another_verb_is_unsolicited_not_an_answer() -> None:
    table = RecordedCatAnswerTable.from_cat_pairs(
        _rows(("c2r", "FA;"), ("r2c", "MD02;"), ("r2c", "FA007074000;"))
    )
    assert table.unsolicited == ["MD02;"]
    assert table.lookup("FA;") == RecordedCatAnswer("data", "FA007074000;")


def test_selector_with_parameter_is_its_own_key() -> None:
    table = RecordedCatAnswerTable.from_cat_pairs(
        _rows(
            ("c2r", "RM7;"),
            ("r2c", "RM7050000;"),
            ("c2r", "RM8;"),
            ("r2c", "?;"),
        )
    )
    assert table.lookup("RM7;") == RecordedCatAnswer("data", "RM7050000;")
    assert table.lookup("RM8;") == RecordedCatAnswer("refusal", "?;")
    assert table.requests() == ("RM7;", "RM8;")


def test_latest_recording_wins_and_windows_filter() -> None:
    rows = _rows(("c2r", "FA;"), ("r2c", "FA007074000;"), window="early") + _rows(
        ("c2r", "FA;"), ("r2c", "FA014074000;"), window="late"
    )
    assert RecordedCatAnswerTable.from_cat_pairs(rows).lookup("FA;") == (
        RecordedCatAnswer("data", "FA014074000;")
    )
    assert RecordedCatAnswerTable.from_cat_pairs(rows, window="early").lookup(
        "FA;"
    ) == (RecordedCatAnswer("data", "FA007074000;"))


def test_extract_keeps_first_closed_read_pair_per_window(tmp_path) -> None:
    import json
    from pathlib import Path

    from rigplane.profiles.rig_loader import load_rig

    from rigplane_testkit.yaesu import CatReadOnlyGuard
    from rigplane_testkit.yaesu.fixtures import extract_cat_pairs

    def row(t, d, text):
        return {"t": t, "dir": d, "port": "serial", "hex": text.encode().hex()}

    rec = tmp_path / "rec.jsonl"
    rows = [
        row(0.1, "c2r", "FA;"),
        row(0.2, "r2c", "MD02;"),  # stale / unsolicited: skipped
        row(0.3, "r2c", "FA007074000;"),
        row(0.4, "c2r", "FA;"),  # repeat: dropped
        row(0.5, "r2c", "FA007075000;"),
        row(0.6, "c2r", "KS;"),
        row(0.7, "r2c", "?;"),
        row(0.8, "c2r", "LK;"),  # never closed: not emitted
        row(0.9, "c2r", "MD0C;"),  # a write: never a fact
        row(1.0, "c2r", "ID;"),
        row(1.1, "r2c", "ID0840;"),
    ]
    rec.write_text("".join(json.dumps(r) + "\n" for r in rows))
    guard = CatReadOnlyGuard.from_config(
        load_rig(Path(__file__).parents[2] / "rigs" / "ftx1.toml")
    )
    got = extract_cat_pairs(rec, "w", guard)
    assert [(r["dir"], r["line"]) for r in got] == [
        ("c2r", "FA;"),
        ("r2c", "FA007074000;"),
        ("c2r", "KS;"),
        ("r2c", "?;"),
        ("c2r", "ID;"),
        ("r2c", "ID0840;"),
    ]
    assert {r["window"] for r in got} == {"w"}
