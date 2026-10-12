"""Pins the certification-status script and the wiring of its workflow.

Jobs are shaped like the Actions jobs API (an ordered ``steps`` list); the
workflow files are read as text, as ``tests/test_artifact_cleanup.py`` reads
its workflow.
"""

from __future__ import annotations

import importlib.util
import io
import json
import urllib.error
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / ".github" / "scripts" / "certification_status.py"
WORKFLOWS = ROOT / ".github" / "workflows"
WORKFLOW = (WORKFLOWS / "certification-status.yml").read_text(encoding="utf-8")
BENCH_NIGHTLY = (WORKFLOWS / "bench-nightly.yml").read_text(encoding="utf-8")
_SPEC = importlib.util.spec_from_file_location("certification_status", SCRIPT)
assert _SPEC and _SPEC.loader
script = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(script)

RO, WR, PASS = "read-only", "write-and-restore", "PASS"
PYTEST = "Hardware pytest subset"
NOT_RUN = {"Validate": "skipped"}
STEPS = ["Set up job", "Bench lock", "Validate", "Night-to-night diff", PYTEST]
STEPS += [
    "Outcome: not configured",
    "Outcome: busy",
    "Outcome: dry run",
    "Complete job",
]
Job = dict[str, Any]


def job(
    name: str, conclusion: str = "success", over: dict[str, str] | None = None
) -> Job:
    """A completed job: every step succeeded (Outcome steps skipped) unless overridden."""
    done = {s: "skipped" if s.startswith("Outcome: ") else "success" for s in STEPS}
    done |= over or {}
    steps = [
        {"name": s, "conclusion": done[s], "number": n} for n, s in enumerate(STEPS, 1)
    ]
    return {"id": 1, "name": name, "conclusion": conclusion, "steps": steps}


def busy(name: str) -> Job:
    over = {"Night-to-night diff": "skipped", "Outcome: busy": "failure"}
    return job(name, "failure", NOT_RUN | over)


def day(back: int) -> str:
    """created_at of the run `back` days before Sunday 2026-10-11."""
    return f"{date(2026, 10, 11) - timedelta(days=back)}T01:31:07Z"


def history(*nights: tuple[str, list[Job]]) -> tuple[list[Job], Any]:
    """Runs newest first, and a fetch_jobs serving their jobs; `.asked` lists run ids."""
    runs = [{"id": i, "created_at": at} for i, (at, _) in enumerate(nights, 1)]
    served = {i: jobs for i, (_, jobs) in enumerate(nights, 1)}

    def fetch(run_id: int) -> list[Job]:
        fetch.asked.append(run_id)  # type: ignore[attr-defined]
        return served[run_id]

    fetch.asked = []  # type: ignore[attr-defined]
    return runs, fetch


def night(back: int, kind: str, outcome: str) -> Any:
    return script.Night(day(back)[:10], kind, outcome)


@pytest.mark.parametrize(
    ("over", "conclusion", "expected"),
    [
        ({}, "success", PASS),
        (NOT_RUN | {"Outcome: busy": "failure"}, "failure", "busy"),
        (NOT_RUN | {"Outcome: not configured": "failure"}, "failure", "not configured"),
        (NOT_RUN | {"Outcome: dry run": "failure"}, "failure", "dry run"),
        ({"Validate": "failure"}, "failure", "validate failed"),
        ({"Night-to-night diff": "failure"}, "failure", "diff failed"),
        ({PYTEST: "failure"}, "failure", f"failed: {PYTEST}"),
        ({"Validate": "failure", PYTEST: "failure"}, "failure", "validate failed"),
        (NOT_RUN, "success", "validate not run"),
        (NOT_RUN, "cancelled", "job cancelled"),
    ],
)
def test_classification_names_the_outcome_of_one_job(
    over: dict[str, str], conclusion: str, expected: str
) -> None:
    assert script.classify_job(job("IC-7610", conclusion, over)) == expected


def test_a_radio_without_a_job_is_missing() -> None:
    assert script.classify_job(None) == "missing"


@pytest.mark.parametrize(
    ("created_at", "kind"),
    [
        ("2026-10-09T01:31:07Z", RO),  # Friday
        ("2026-10-10T00:00:00Z", WR),  # Saturday
        ("2026-10-11T23:59:59Z", WR),  # Sunday
        ("2026-10-12T00:00:00Z", RO),  # Monday
    ],
)
def test_weekend_nights_are_write_and_restore_and_the_rest_read_only(
    created_at: str, kind: str
) -> None:
    assert script.night_kind(created_at) == kind


def test_streak_counts_back_from_newest_and_stops_at_the_first_non_pass() -> None:
    rows = [night(0, WR, PASS), night(1, WR, PASS), night(2, RO, "busy")]
    assert script.streak(rows + [night(3, RO, PASS)]) == (2, True)
    assert script.streak([night(0, WR, "busy")]) == (0, False)
    # a write-and-restore night past the first non-PASS is not in the streak
    rows = [night(2, RO, PASS), night(3, RO, "busy"), night(4, WR, PASS)]
    assert script.streak(rows) == (1, False)


def test_judge_counts_a_write_and_restore_night_older_than_n_nights() -> None:
    rows = [night(3, RO, PASS), night(4, RO, PASS), night(5, RO, PASS)]
    rows += [night(6, WR, PASS), night(7, RO, "busy")]
    assert script.judge({"IC-7610": rows}, 3) == {}


def test_judge_refuses_a_long_enough_streak_without_a_write_and_restore_night() -> None:
    rows = [night(2, RO, PASS), night(3, RO, PASS), night(4, RO, PASS)]
    reason = "no write-and-restore night in the streak of 3"
    assert script.judge({"IC-7610": rows}, 3) == {"IC-7610": reason}


def test_judge_names_the_night_that_ended_a_short_streak() -> None:
    rows = [night(2, RO, PASS), night(3, RO, "busy"), night(4, RO, PASS)]
    reason = "streak 1 of 3 nights; stopped by busy on 2026-10-08"
    assert script.judge({"IC-7610": rows}, 3) == {"IC-7610": reason}
    reason = "streak 1 of 3 nights; no earlier night inspected"
    assert script.judge({"IC-7610": rows[:1]}, 3) == {"IC-7610": reason}


def test_default_radios_are_the_newest_runs_and_a_dropped_one_is_missing() -> None:
    runs, fetch = history(
        (day(0), [job("IC-7610"), job("FTX-1")]), (day(1), [job("IC-7610")])
    )
    found = script.collect(runs, fetch, [], 2)
    assert list(found) == ["IC-7610", "FTX-1"]
    assert [row.outcome for row in found["FTX-1"]] == [PASS, "missing"]


def test_explicit_radios_replace_the_default_and_an_unknown_label_is_missing() -> None:
    runs, fetch = history((day(0), [job("IC-7610"), job("FTX-1")]))
    found = script.collect(runs, fetch, ["IC-9700", "FTX-1"], 1)
    assert {r: [n.outcome for n in rows] for r, rows in found.items()} == {
        "IC-9700": ["missing"],
        "FTX-1": [PASS],
    }


def test_collect_stops_once_every_streak_has_ended_and_n_nights_are_in() -> None:
    runs, fetch = history(*[(day(i), [busy("IC-7610")]) for i in range(10)])
    script.collect(iter(runs), fetch, [], 3)
    assert fetch.asked == [1, 2, 3]


def test_collect_stops_at_a_newest_run_without_jobs_and_at_max_nights() -> None:
    runs, fetch = history((day(0), []), (day(1), [job("IC-7610")]))
    assert script.collect(iter(runs), fetch, [], 3) == {}
    assert fetch.asked == [1]
    runs, fetch = history(*[(day(i), [job("IC-7610")]) for i in range(40)])
    assert len(script.collect(runs, fetch, [], 3)["IC-7610"]) == script.MAX_NIGHTS
    assert len(fetch.asked) == script.MAX_NIGHTS


def test_runs_listed_oldest_first_are_refused() -> None:
    runs, fetch = history((day(1), [job("IC-7610")]), (day(0), [job("IC-7610")]))
    with pytest.raises(ValueError, match="newest first"):
        script.collect(runs, fetch, [], 3)


def test_certify_exit_status_follows_the_verdict() -> None:
    nights = [(day(i), [job("IC-7610"), job("FTX-1")]) for i in range(3)]
    code, text = script.certify(*history(*nights), [], 3)
    assert (code, text.splitlines()[0]) == (0, "### Bench certification: CERTIFIED")
    nights[1] = (day(1), [busy("IC-7610"), job("FTX-1")])
    code, text = script.certify(*history(*nights), [], 3)
    assert (code, text.splitlines()[0]) == (1, "### Bench certification: NOT CERTIFIED")
    code, text = script.certify(iter([]), history()[1], [], 3)
    assert (code, "No radio to certify" in text) == (1, True)


def test_summary_table_text() -> None:
    nights = [(day(2), [job("IC-7610")]), (day(3), [busy("IC-7610")])]
    code, text = script.certify(*history(*nights, (day(4), [job("IC-7610")])), [], 3)
    assert code == 1
    assert text == (
        "### Bench certification: NOT CERTIFIED\n"
        "\n"
        "Rule: 3 consecutive PASS nights per radio, newest backwards, with a "
        "write-and-restore night (Saturday or Sunday UTC) in the streak. Source: "
        "completed scheduled runs of bench-nightly.yml on main, at most "
        f"{script.MAX_NIGHTS} read.\n"
        "\n"
        "| Radio | Streak | Write-and-restore in streak | Result |\n"
        "| --- | ---: | --- | --- |\n"
        "| IC-7610 | 1 | no | not certified: streak 1 of 3 nights; "
        "stopped by busy on 2026-10-08 |\n"
        "\n"
        "#### IC-7610: last 3 nights\n"
        "\n"
        "| Night (UTC) | Type | Outcome |\n"
        "| --- | --- | --- |\n"
        "| 2026-10-09 | read-only | PASS |\n"
        "| 2026-10-08 | read-only | busy |\n"
        "| 2026-10-07 | read-only | PASS |"
    )


def test_listing_reads_completed_scheduled_runs_on_main_page_by_page(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pages = {"1": [{"id": n} for n in range(100)], "2": [{"id": 100}]}
    requested: list[str] = []

    def get_json(url: str, token: str) -> Any:
        requested.append(url)
        return {"workflow_runs": pages[parse_qs(urlparse(url).query)["page"][0]]}

    monkeypatch.setattr(script, "_get_json", get_json)
    listing = script.list_runs("https://api.example", "owner/repo", "token")
    assert requested == []  # lazy: nothing is read until the caller asks
    assert len(list(listing)) == 101
    first = urlparse(requested[0])
    assert first.path == "/repos/owner/repo/actions/workflows/bench-nightly.yml/runs"
    assert parse_qs(first.query) == {
        "event": ["schedule"],
        "branch": ["main"],
        "status": ["completed"],
        "per_page": ["100"],
        "page": ["1"],
    }
    assert len(requested) == 2


def test_jobs_come_from_the_latest_attempt_of_the_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: list[str] = []

    def get_json(url: str, token: str) -> Any:
        requested.append(url)
        return {"jobs": [{"name": "IC-7610"}]}

    monkeypatch.setattr(script, "_get_json", get_json)
    jobs = script.list_jobs("https://api.example", "owner/repo", "token", 42)
    assert jobs == [{"name": "IC-7610"}]
    url = urlparse(requested[0])
    assert url.path == "/repos/owner/repo/actions/runs/42/jobs"
    assert parse_qs(url.query)["filter"] == ["latest"]


def test_requests_are_authenticated_gets(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[Any] = []

    def urlopen(request: Any, timeout: float) -> io.BytesIO:
        sent.append(request)
        return io.BytesIO(json.dumps({"ok": True}).encode())

    monkeypatch.setattr(script.urllib.request, "urlopen", urlopen)
    assert script._get_json("https://api.example/x", "secret") == {"ok": True}
    assert sent[0].get_method() == "GET"
    assert sent[0].get_header("Authorization") == "Bearer secret"


@pytest.mark.parametrize("error", [urllib.error.URLError("boom"), ValueError("boom")])
def test_main_exits_two_when_the_run_history_cannot_be_read(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    error: Exception,
) -> None:
    def get_json(url: str, token: str) -> Any:
        raise error

    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setattr(script, "_get_json", get_json)
    assert script.main(["--nights", "3"]) == 2
    assert "boom" in capsys.readouterr().err


def test_main_refuses_fewer_than_one_night() -> None:
    with pytest.raises(SystemExit) as refused:
        script.main(["--nights", "0"])
    assert refused.value.code == 2


def test_main_prints_the_summary_and_appends_it_to_the_step_summary(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
) -> None:
    runs, serve = history((day(0), [job("IC-7610")]))
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_REPOSITORY", "owner/repo")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setattr(script, "list_runs", lambda api, repo, token: iter(runs))
    monkeypatch.setattr(script, "list_jobs", lambda api, repo, token, id: serve(id))
    assert script.main(["--radios", " IC-7610, ,FTX-1", "--nights", "1"]) == 1
    printed = capsys.readouterr().out
    row = "| FTX-1 | 0 | no | not certified: streak 0 of 1 nights; stopped by missing"
    assert row in printed
    assert summary.read_text(encoding="utf-8") == printed


def test_workflow_is_a_read_only_manual_dispatch() -> None:
    assert "on:\n  workflow_dispatch:\n    inputs:\n      radios:" in WORKFLOW
    assert "\n      nights:" in WORKFLOW
    assert "schedule:" not in WORKFLOW
    assert "permissions:\n  actions: read\n  contents: read\n" in WORKFLOW
    assert "runs-on: ubuntu-latest" in WORKFLOW
    assert 'certification_status.py --radios "$RADIOS" --nights "$NIGHTS"' in WORKFLOW


def test_script_reads_the_names_bench_nightly_actually_uses() -> None:
    assert script.WORKFLOW == "bench-nightly.yml"
    assert f"      - name: {script.VALIDATE_STEP}\n" in BENCH_NIGHTLY
    assert f"      - name: {script.DIFF_STEP}\n" in BENCH_NIGHTLY
    assert "    name: ${{ matrix.label }}\n" in BENCH_NIGHTLY
    assert '- cron: "30 1 * * 0,6"' in BENCH_NIGHTLY
    assert '- cron: "30 1 * * 1-5"' in BENCH_NIGHTLY
