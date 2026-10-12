#!/usr/bin/env python3
"""Report whether the bench radios have enough consecutive PASS nights.

Reads the scheduled runs of bench-nightly.yml on main from the Actions API and
prints a Markdown summary. Exit status: 0 certified, 1 not certified, 2 the run
history could not be read.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from datetime import date, datetime, timedelta
from typing import Any, NamedTuple

WORKFLOW = "bench-nightly.yml"
PER_PAGE = 100
MAX_NIGHTS = 30
PASS = "PASS"
MISSING_NIGHT = "missing night"
READ_ONLY, WRITE_RESTORE = "read-only", "write-and-restore"
SATURDAY, SUNDAY = 5, 6  # datetime.weekday(); the cron "30 1 * * 0,6" of the workflow
VALIDATE_STEP = "Validate"
DIFF_STEP = "Night-to-night diff"
OUTCOME_PREFIX = "Outcome: "
Run = dict[str, Any]
Job = dict[str, Any]


class Night(NamedTuple):
    date: str
    kind: str
    outcome: str


def classify_job(job: Job | None) -> str:
    """PASS, or the named reason the night is not a pass for the radio."""
    if job is None:
        return "missing"
    steps = job.get("steps") or []
    validated = any(
        step["name"] == VALIDATE_STEP and step.get("conclusion") == "success"
        for step in steps
    )
    if job.get("conclusion") == "success" and validated:
        return PASS
    failed = next(
        (step["name"] for step in steps if step.get("conclusion") == "failure"), None
    )
    if failed is None:
        if job.get("conclusion") == "success":
            return "validate not run"
        return "job " + str(job.get("conclusion")).replace("_", " ")
    if failed.startswith(OUTCOME_PREFIX):
        return failed[len(OUTCOME_PREFIX) :]
    if failed == VALIDATE_STEP:
        return "validate failed"
    if failed == DIFF_STEP:
        return "diff failed"
    return f"failed: {failed}"


def night_date(created_at: str) -> date:
    """UTC date of a run's created_at (a Z-suffixed stamp).

    For the 01:30 UTC crons of the workflow this is the cron's own date unless the
    run is created 22 h 30 min or more after its cron.
    """
    return datetime.fromisoformat(created_at).date()


def night_kind(day: date) -> str:
    """write-and-restore on Saturday and Sunday, read-only on the other days."""
    return WRITE_RESTORE if day.weekday() in (SATURDAY, SUNDAY) else READ_ONLY


def streak(nights: Sequence[Night]) -> tuple[int, bool]:
    """Leading PASS nights (newest first) and whether one is write-and-restore."""
    count, write_restore = 0, False
    for night in nights:
        if night.outcome != PASS:
            break
        count += 1
        write_restore = write_restore or night.kind == WRITE_RESTORE
    return count, write_restore


def judge(history: Mapping[str, Sequence[Night]], nights: int) -> dict[str, str]:
    """Reason per radio that is not certified; an empty result certifies all.

    A radio is certified when its streak reaches `nights` and contains a
    write-and-restore night. Interpretation: that night may be any night of the
    streak, not only one of the last `nights`.
    """
    reasons: dict[str, str] = {}
    for radio, rows in history.items():
        count, write_restore = streak(rows)
        if count < nights:
            stop = (
                f"stopped by {rows[count].outcome} on {rows[count].date}"
                if count < len(rows)
                else "no earlier night inspected"
            )
            reasons[radio] = f"streak {count} of {nights} nights; {stop}"
        elif not write_restore:
            reasons[radio] = f"no {WRITE_RESTORE} night in the streak of {count}"
    return reasons


def night_slots(runs: Iterable[Run]) -> Iterator[tuple[date, Run | None]]:
    """One (date, run) per UTC date from the newest run's date back to the oldest run's.

    The newest run of a date is the one used; a date between two runs that has none
    yields None. A listing that is not newest first raises ValueError.
    """
    newest, previous = "", None
    for run in runs:
        created = run["created_at"]
        if newest and created > newest:
            raise ValueError("scheduled runs are not listed newest first")
        newest, day = created, night_date(created)
        if day == previous:
            continue
        while previous is not None and previous - day > timedelta(days=1):
            previous -= timedelta(days=1)
            yield previous, None
        yield day, run
        previous = day


def collect(
    runs: Iterable[Run],
    fetch_jobs: Callable[[int], list[Job]],
    radios: Sequence[str],
    nights: int,
) -> dict[str, list[Night]]:
    """Per radio its nights, newest first. Radios default to the newest run's jobs.

    Runs are read lazily. Reading stops after MAX_NIGHTS nights, once `nights` nights
    are in and every radio's streak has ended, or when no radio is known after the first.
    """
    history: dict[str, list[Night]] = {radio: [] for radio in radios}
    for count, (day, run) in enumerate(night_slots(runs), 1):
        jobs = {job["name"]: job for job in fetch_jobs(run["id"])} if run else {}
        if count == 1 and not history:
            history = {name: [] for name in jobs}
        for radio, rows in history.items():
            outcome = classify_job(jobs.get(radio)) if run else MISSING_NIGHT
            rows.append(Night(day.isoformat(), night_kind(day), outcome))
        ended = all(streak(rows)[0] < len(rows) for rows in history.values())
        if not history or count >= MAX_NIGHTS or (ended and count >= nights):
            break
    return history


def render(
    history: Mapping[str, Sequence[Night]], nights: int, reasons: Mapping[str, str]
) -> str:
    verdict = "NOT CERTIFIED" if reasons or not history else "CERTIFIED"
    lines = [
        f"### Bench certification: {verdict}",
        "",
        f"Rule: {nights} consecutive PASS nights per radio, newest backwards, with a "
        f"{WRITE_RESTORE} night (Saturday or Sunday UTC) in the streak. A night is a "
        f"UTC date: its newest completed scheduled run of {WORKFLOW} on main, or "
        f"{MISSING_NIGHT} if it has none. At most {MAX_NIGHTS} nights are read.",
        "",
    ]
    if not history:
        lines.append(
            "No radio to certify: the newest completed scheduled run has no radio "
            "job, or there is no such run."
        )
        return "\n".join(lines)
    lines += [
        "| Radio | Streak | Write-and-restore in streak | Result |",
        "| --- | ---: | --- | --- |",
    ]
    for radio, rows in history.items():
        count, write_restore = streak(rows)
        result = f"not certified: {reasons[radio]}" if radio in reasons else "certified"
        lines.append(
            f"| {radio} | {count} | {'yes' if write_restore else 'no'} | {result} |"
        )
    for radio, rows in history.items():
        lines += [
            "",
            f"#### {radio}: last {nights} nights",
            "",
            "| Night (UTC) | Type | Outcome |",
            "| --- | --- | --- |",
        ]
        lines += [f"| {n.date} | {n.kind} | {n.outcome} |" for n in rows[:nights]]
    return "\n".join(lines)


def certify(
    runs: Iterable[Run],
    fetch_jobs: Callable[[int], list[Job]],
    radios: Sequence[str],
    nights: int,
) -> tuple[int, str]:
    """Exit status (0 certified, 1 not) and the Markdown summary."""
    history = collect(runs, fetch_jobs, radios, nights)
    reasons = judge(history, nights)
    return (0 if history and not reasons else 1), render(history, nights, reasons)


def _get_json(url: str, token: str) -> Any:
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def list_runs(api: str, repo: str, token: str) -> Iterator[Run]:
    """Completed scheduled runs of the workflow on main, as the API returns them."""
    query = f"event=schedule&branch=main&status=completed&per_page={PER_PAGE}"
    page = 1
    while True:
        url = (
            f"{api}/repos/{repo}/actions/workflows/{WORKFLOW}/runs?{query}&page={page}"
        )
        batch = _get_json(url, token)["workflow_runs"]
        yield from batch
        if len(batch) < PER_PAGE:
            return
        page += 1


def list_jobs(api: str, repo: str, token: str, run_id: int) -> list[Job]:
    url = f"{api}/repos/{repo}/actions/runs/{run_id}/jobs?filter=latest&per_page={PER_PAGE}"
    return _get_json(url, token)["jobs"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--radios", default="", help="comma-separated radio labels")
    parser.add_argument("--nights", type=int, default=3)
    args = parser.parse_args(argv)
    if args.nights < 1:
        parser.error("--nights must be at least 1")
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    repo, token = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
    radios = [radio.strip() for radio in args.radios.split(",") if radio.strip()]
    try:
        status, summary = certify(
            list_runs(api, repo, token),
            lambda run_id: list_jobs(api, repo, token, run_id),
            radios,
            args.nights,
        )
    except (OSError, ValueError) as error:
        print(
            f"certification status: cannot read the run history: {error}",
            file=sys.stderr,
        )
        return 2
    print(summary)
    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(summary + "\n")
    return status


if __name__ == "__main__":
    sys.exit(main())
