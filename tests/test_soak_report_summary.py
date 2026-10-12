"""Pins ``scripts/soak/report_summary.py`` on a report from a real driver run.

The ``smoke_report`` fixture runs the soak driver against the simulator, so
the keys the script reads are the ones the driver writes. Expected numbers
come from the driver's own ``summary_line``; values a short run cannot
produce (a measured RSS slope, a failure) are set on a copy of that report.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from scripts.soak import report_summary, soak_driver

ROOT = Path(__file__).resolve().parent.parent
MIB = 1024 * 1024


@pytest.fixture(scope="module")
def smoke_report(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Any]:
    path = tmp_path_factory.mktemp("soak") / "soak-report.json"
    config = soak_driver.SoakConfig(
        duration_s=10.0,
        report_path=str(path),
        reconnect_interval_s=4.0,
        metrics_interval_s=1.0,
        rss_warmup_s=3.0,
    )
    asyncio.run(soak_driver.run_soak(config))
    report: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return report


def _summarize(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report: Any, earlier: str = ""
) -> tuple[int, list[str]]:
    """Run ``main`` on ``report`` (None: no file, str: raw text)."""
    summary = tmp_path / "summary.md"
    summary.write_text(earlier, encoding="utf-8")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    if report is not None:
        text = report if isinstance(report, str) else json.dumps(report)
        (tmp_path / "soak-report.json").write_text(text, encoding="utf-8")
    code = report_summary.main(str(tmp_path / "soak-report.json"))
    return code, summary.read_text(encoding="utf-8").splitlines()


def test_real_report_is_summarized_with_the_drivers_own_numbers(
    smoke_report: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    code, lines = _summarize(tmp_path, monkeypatch, smoke_report, "earlier step\n")
    assert code == 0
    assert lines[0] == "earlier step"
    assert lines[1].startswith(f"## Soak {smoke_report['verdict']}: ")
    criteria = smoke_report["criteria"]
    assert criteria["fdsStability"]["checks"], "the smoke run recorded no reconnect"
    by_label = {}
    for label, key in (
        ("RSS growth", "rssGrowth"),
        ("FDs", "fdsStability"),
        ("Tasks", "tasksStability"),
        ("Unrecovered disconnects", "recovery"),
    ):
        prefix = f"- {label} {criteria[key]['status']}: "
        (by_label[label],) = [line for line in lines if line.startswith(prefix)]
    numbers = re.search(
        r"fds_max_delta=(\d+) tasks_max_delta=(\d+) unrecovered=(\d+)/(\d+)",
        soak_driver.summary_line(smoke_report),
    )
    assert numbers, soak_driver.summary_line(smoke_report)
    fds, tasks, unrecovered, forced = numbers.groups()
    assert f": max abs delta {fds} (" in by_label["FDs"]
    assert f": max abs delta {tasks} (" in by_label["Tasks"]
    assert f": {unrecovered} of {forced} (" in by_label["Unrecovered disconnects"]


def test_measured_values_and_a_driver_error_are_formatted(
    smoke_report: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = copy.deepcopy(smoke_report)
    criteria = report["criteria"]
    criteria["rssGrowth"].update(status="PASS", slopeBytesPerHour=12 * MIB)
    criteria["fdsStability"].update(checks=[{"delta": 3}, {"delta": -4}], status="PASS")
    criteria["tasksStability"].update(checks=[{"delta": -2}], status="FAIL")
    criteria["recovery"].update(status="FAIL", unrecovered=1, forced=3)
    report.update(verdict="FAIL", error="ValueError: boom")
    code, lines = _summarize(tmp_path, monkeypatch, report)
    assert code == 0
    assert "- RSS growth PASS: +12.0 MiB/h (limit 50 MiB/h)" in lines
    assert "- FDs PASS: max abs delta 4 (2 checked, tolerance 5)" in lines
    assert "- Tasks FAIL: max abs delta 2 (1 checked, tolerance 5)" in lines
    assert any(
        line.startswith("- Unrecovered disconnects FAIL: 1 of 3 (") for line in lines
    )
    assert "- Driver error: `ValueError: boom`" in lines


def test_an_incomplete_verdict_is_shown_with_its_unevaluated_criterion(
    smoke_report: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = copy.deepcopy(smoke_report)
    report["criteria"]["rssGrowth"].update(
        status="NOT_EVALUATED", slopeBytesPerHour=None
    )
    report["verdict"] = soak_driver.VERDICT_INCOMPLETE
    code, lines = _summarize(tmp_path, monkeypatch, report)
    assert code == 0
    assert lines[0].startswith("## Soak INCOMPLETE: ")
    assert "- RSS growth NOT_EVALUATED: n/a (limit 50 MiB/h)" in lines


@pytest.mark.parametrize("report_text", [None, "{not json", "[]"])
def test_missing_or_unusable_report_exits_1_with_a_no_report_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, report_text: str | None
) -> None:
    code, lines = _summarize(tmp_path, monkeypatch, report_text)
    assert code == 1
    assert lines[0] == "## Soak: NO REPORT"
    assert "soak-report.json" in lines[2]


def test_module_runs_as_a_script_from_the_repository_root_without_site_packages(
    tmp_path: Path,
) -> None:
    summary = tmp_path / "summary.md"
    done = subprocess.run(
        [sys.executable, "-S", "-m", "scripts.soak.report_summary", "missing.json"],
        cwd=ROOT,
        env={**os.environ, "GITHUB_STEP_SUMMARY": str(summary)},
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert done.returncode == 1, done.stderr
    assert summary.read_text(encoding="utf-8").startswith("## Soak: NO REPORT")


def test_script_imports_only_the_standard_library() -> None:
    tree = ast.parse(Path(report_summary.__file__).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            imported.add("." if node.level else (node.module or "").split(".")[0])
    assert imported
    assert imported <= set(sys.stdlib_module_names), imported
