"""The ``civ_pairs`` / ``cat_pairs`` row contracts (MOR-4464).

Rows cross file and module boundaries (probe converter, extractors, file
readers, replay tables); they share :class:`CivPairRow` / :class:`CatPairRow`
instead of re-guessing dictionary keys.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rigplane_testkit.fixture_rows import (
    CatPairRow,
    CivPairRow,
    FixtureRowError,
    cat_rows,
    civ_rows,
    dump_rows,
    read_cat_rows,
    read_civ_rows,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"

_CIV = {
    "window": "w",
    "transport": "usb",
    "t": 0.5,
    "dir": "c2r",
    "frame": "fefea0e01401fd",
}
_CAT = {"window": "w", "transport": "usb", "t": 0.5, "dir": "r2c", "line": "FA;"}


def test_civ_row_parses_and_serialises_back_to_the_same_object() -> None:
    row = CivPairRow.from_json(_CIV)
    assert row == CivPairRow("w", "usb", 0.5, "c2r", bytes.fromhex("fefea0e01401fd"))
    assert row.to_json() == _CIV


def test_cat_row_parses_and_serialises_back_to_the_same_object() -> None:
    row = CatPairRow.from_json(_CAT)
    assert row == CatPairRow("w", "usb", 0.5, "r2c", "FA;")
    assert row.to_json() == _CAT


def test_an_integer_time_reads_as_the_same_number() -> None:
    assert CivPairRow.from_json({**_CIV, "t": 2}).t == 2.0


@pytest.mark.parametrize(
    ("change", "needle"),
    [
        ({"dir": "both"}, "dir"),
        ({"dir": None}, "dir"),
        ({"t": "0.5"}, "'t'"),
        ({"t": True}, "'t'"),
        ({"window": 3}, "window"),
        ({"transport": None}, "transport"),
        ({"frame": 7}, "frame"),
        ({"frame": "not hex"}, "frame"),
        ({"extra": 1}, "unknown"),
    ],
)
def test_civ_row_rejects_off_contract_values(change: dict, needle: str) -> None:
    with pytest.raises(FixtureRowError, match=needle):
        CivPairRow.from_json({**_CIV, **change})


@pytest.mark.parametrize("key", ["window", "transport", "t", "dir", "frame"])
def test_civ_row_rejects_a_missing_key(key: str) -> None:
    obj = {name: value for name, value in _CIV.items() if name != key}
    with pytest.raises(FixtureRowError, match=key):
        CivPairRow.from_json(obj)


@pytest.mark.parametrize("key", ["window", "transport", "t", "dir", "line"])
def test_cat_row_rejects_a_missing_key(key: str) -> None:
    obj = {name: value for name, value in _CAT.items() if name != key}
    with pytest.raises(FixtureRowError, match=key):
        CatPairRow.from_json(obj)


def test_a_civ_object_is_not_a_cat_row_and_the_other_way_round() -> None:
    with pytest.raises(FixtureRowError):
        CatPairRow.from_json(_CIV)
    with pytest.raises(FixtureRowError):
        CivPairRow.from_json(_CAT)


def test_a_non_object_is_rejected() -> None:
    with pytest.raises(FixtureRowError, match="object"):
        CivPairRow.from_json(["c2r"])


def test_the_normalisers_pass_typed_rows_and_parse_objects() -> None:
    typed = CivPairRow.from_json(_CIV)
    assert civ_rows([typed, _CIV]) == [typed, typed]
    cat = CatPairRow.from_json(_CAT)
    assert cat_rows([cat, _CAT]) == [cat, cat]
    with pytest.raises(FixtureRowError):
        civ_rows([{**_CIV, "dir": "x"}])


def test_dump_is_sorted_compact_and_newline_terminated() -> None:
    text = dump_rows([CivPairRow.from_json(_CIV), CatPairRow.from_json(_CAT)])
    lines = text.splitlines()
    assert text.endswith("\n") and len(lines) == 2
    assert lines[0] == json.dumps(_CIV, sort_keys=True, separators=(",", ":"))
    assert lines[1] == json.dumps(_CAT, sort_keys=True, separators=(",", ":"))


@pytest.mark.parametrize(
    "path",
    [
        "ic705/civ_pairs.jsonl",
        "ic705/civ_pairs_vhf_uhf.jsonl",
        "ic7300/civ_pairs.jsonl",
        "ic7610/civ_pairs.jsonl",
    ],
)
def test_committed_civ_fixtures_parse_and_reserialise_byte_identically(
    path: str,
) -> None:
    file = FIXTURES / path
    rows = read_civ_rows(file)
    assert rows
    assert dump_rows(rows) == file.read_text(encoding="utf-8")


def test_committed_cat_fixture_parses_and_round_trips() -> None:
    file = FIXTURES / "ftx1" / "cat_pairs.jsonl"
    rows = read_cat_rows(file)
    assert rows
    assert [CatPairRow.from_json(json.loads(line)) for line in dump_rows(rows).splitlines()] == rows


def test_a_bad_file_row_names_the_file_and_line(tmp_path: Path) -> None:
    file = tmp_path / "civ_pairs.jsonl"
    file.write_text(
        json.dumps(_CIV) + "\n\n" + json.dumps({**_CIV, "dir": "x"}) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(FixtureRowError, match=r"civ_pairs\.jsonl:3"):
        read_civ_rows(file)
    file.write_text("{not json\n", encoding="utf-8")
    with pytest.raises(FixtureRowError, match=r"civ_pairs\.jsonl:1"):
        read_civ_rows(file)
