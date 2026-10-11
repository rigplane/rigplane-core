"""Unit tests for scripts/mutation/summarize.py against a fixture results file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "mutmut-results.sample.txt"

MODULES = [
    "rigplane.commands._frame",
    "rigplane.core.civ",
    "rigplane.core.state_store",
    "rigplane.core.tx_safety",
    "rigplane.core.tx_target",
]


def _summarize_fixture() -> dict:
    from summarize import summarize

    return summarize(FIXTURE.read_text(), MODULES)


def test_per_module_counts() -> None:
    modules = _summarize_fixture()["modules"]
    assert modules["rigplane.commands._frame"] == {
        "killed": 1,
        "survived": 1,
        "timeout": 1,
        "suspicious": 0,
        "skipped": 0,
        "no_tests": 0,
        "other": 0,
    }
    assert modules["rigplane.core.civ"]["killed"] == 2
    assert modules["rigplane.core.civ"]["suspicious"] == 1
    assert modules["rigplane.core.state_store"]["no_tests"] == 1
    assert modules["rigplane.core.tx_safety"]["survived"] == 1
    assert modules["rigplane.core.tx_safety"]["skipped"] == 1
    # Unknown statuses (e.g. "segfault") bucket into "other" instead of
    # being dropped.
    assert modules["rigplane.core.tx_safety"]["other"] == 1


def test_totals_sum_per_status() -> None:
    summary = _summarize_fixture()
    total = summary["total"]
    assert total["killed"] == 5
    assert total["survived"] == 3
    assert total["timeout"] == 1
    assert total["no_tests"] == 1


def test_unmatched_lines_are_counted_not_hidden() -> None:
    summary = _summarize_fixture()
    assert summary["unmatched"] == 1


def test_longest_module_prefix_wins() -> None:
    from summarize import summarize

    text = "    rigplane.core.civ.x_parse__mutmut_1: killed\n"
    summary = summarize(text, ["rigplane.core", "rigplane.core.civ"])
    assert summary["modules"]["rigplane.core.civ"]["killed"] == 1
    assert summary["modules"]["rigplane.core"]["killed"] == 0


def test_mangled_method_names_stay_on_their_module() -> None:
    from summarize import summarize

    text = "    rigplane.commands._frame.xǁFrameClsǁencode__mutmut_3: survived\n"
    summary = summarize(text, MODULES)
    assert summary["modules"]["rigplane.commands._frame"]["survived"] == 1


def test_blank_and_non_result_lines_are_ignored() -> None:
    from summarize import summarize

    text = (
        "some header\n"
        "\n"
        "    rigplane.core.civ.x_parse__mutmut_1: killed\n"
        "not a result line\n"
    )
    summary = summarize(text, MODULES)
    assert summary["total"]["killed"] == 1
    assert summary["unmatched"] == 0


def test_main_writes_json_with_sorted_modules(tmp_path: Path) -> None:
    from summarize import main

    out = tmp_path / "summary.json"
    rc = main(["--results", str(FIXTURE), "--output", str(out), *MODULES])
    assert rc == 0
    data = json.loads(out.read_text())
    assert list(data["modules"]) == sorted(data["modules"])
    assert data["modules"]["rigplane.core.civ"]["killed"] == 2


def test_main_requires_at_least_one_module(capsys: pytest.CaptureFixture[str]) -> None:
    from summarize import main

    with pytest.raises(SystemExit) as excinfo:
        main(["--results", str(FIXTURE)])
    assert excinfo.value.code == 2
    assert "modules" in capsys.readouterr().err
