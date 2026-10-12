#!/usr/bin/env python3
"""Report whether the radios in scope have enough consecutive PASS nights.

Reads the scheduled runs of bench-nightly.yml on main from the Actions API and
prints a Markdown summary. Only the radios named by --radios are judged; the others
seen in the newest run are listed as out of scope. Exit status: 0 certified, 1 not
certified, 2 the run history could not be read or an argument is invalid.
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
NO_VERDICT = "no verdict (bench lease held by another certification item)"
READ_ONLY, WRITE_RESTORE = "read-only", "write-and-restore"
SATURDAY, SUNDAY = 5, 6  # datetime.weekday(); the cron "30 1 * * 0,6" of the workflow
VALIDATE_STEP = "Validate"
DIFF_STEP = "Night-to-night diff"
OUTCOME_PREFIX = "Outcome: "
Run = dict[str, Any]
Job = dict[str, Any]
NoVerdict = frozenset[tuple[str, str | None]]  # (YYYY-MM-DD, radio label or None)


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
    """Leading PASS nights (newest first) and whether one is write-and-restore.

    A no verdict night is walked over: it adds nothing and does not end the streak.
    """
    count, write_restore = 0, False
    for night in nights:
        if night.outcome == NO_VERDICT:
            continue
        if night.outcome != PASS:
            break
        count += 1
        write_restore = write_restore or night.kind == WRITE_RESTORE
    return count, write_restore


def stopper(nights: Sequence[Night]) -> Night | None:
    """The night that ends the streak: the first that is neither PASS nor no verdict."""
    return next((n for n in nights if n.outcome not in (PASS, NO_VERDICT)), None)


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
            end = stopper(rows)
            stop = (
                f"stopped by {end.outcome} on {end.date}"
                if end
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
    scope: Sequence[str],
    nights: int,
    no_verdict: NoVerdict = frozenset(),
) -> dict[str, list[Night]]:
    """Per radio its nights, newest first: the radios in scope, then any other radio
    that has a job on the newest night (out of scope, shown but not judged).

    A night of a radio in scope that `no_verdict` names, or names for every radio,
    is NO_VERDICT whatever its job did. Runs are read lazily. Reading stops after
    MAX_NIGHTS nights, or once `nights` nights are in and the streak of every radio
    in scope has ended.
    """
    history: dict[str, list[Night]] = {radio: [] for radio in scope}
    for count, (day, run) in enumerate(night_slots(runs), 1):
        jobs = {job["name"]: job for job in fetch_jobs(run["id"])} if run else {}
        if count == 1:
            for name in jobs:
                history.setdefault(name, [])
        iso = day.isoformat()
        for radio, rows in history.items():
            outcome = classify_job(jobs.get(radio)) if run else MISSING_NIGHT
            if radio in scope and {(iso, radio), (iso, None)} & no_verdict:
                outcome = NO_VERDICT
            rows.append(Night(iso, night_kind(day), outcome))
        ended = all(stopper(history[radio]) is not None for radio in scope)
        if count >= MAX_NIGHTS or (ended and count >= nights):
            break
    return history


def render(
    history: Mapping[str, Sequence[Night]],
    scope: Sequence[str],
    nights: int,
    reasons: Mapping[str, str],
) -> str:
    names = ", ".join(scope)
    why = "; ".join(
        reason if len(scope) == 1 else f"{radio}: {reason}"
        for radio, reason in reasons.items()
    )
    verdict = f"CERTIFIED for {names}"
    if reasons:
        verdict = f"NOT CERTIFIED for {names}: {why}"
    lines = [
        f"### Bench certification: {verdict}",
        "",
        f"Rule: {nights} consecutive PASS nights per radio in scope, newest backwards, "
        f"with a {WRITE_RESTORE} night (Saturday or Sunday UTC) in the streak. A night "
        f"is a UTC date: its newest completed scheduled run of {WORKFLOW} on main, or "
        f"{MISSING_NIGHT} if it has none. A no verdict night neither counts nor ends "
        f"the streak. At most {MAX_NIGHTS} nights are read.",
        "",
        "| Radio | Streak | Write-and-restore in streak | Result |",
        "| --- | ---: | --- | --- |",
    ]
    for radio, rows in history.items():
        if radio not in scope:
            lines.append(f"| {radio} |  |  | out of scope |")
            continue
        count, write_restore = streak(rows)
        result = f"not certified: {reasons[radio]}" if radio in reasons else "certified"
        lines.append(
            f"| {radio} | {count} | {'yes' if write_restore else 'no'} | {result} |"
        )
    for radio, rows in history.items():
        scoped = "" if radio in scope else " (out of scope)"
        lines += [
            "",
            f"#### {radio}{scoped}: last {nights} nights",
            "",
            "| Night (UTC) | Type | Outcome |",
            "| --- | --- | --- |",
        ]
        lines += [f"| {n.date} | {n.kind} | {n.outcome} |" for n in rows[:nights]]
    return "\n".join(lines)


def certify(
    runs: Iterable[Run],
    fetch_jobs: Callable[[int], list[Job]],
    scope: Sequence[str],
    nights: int,
    no_verdict: NoVerdict = frozenset(),
) -> tuple[int, str]:
    """Exit status (0 certified, 1 not) for the radios in scope, and the summary."""
    history = collect(runs, fetch_jobs, scope, nights, no_verdict)
    reasons = judge({radio: history[radio] for radio in scope}, nights)
    return (1 if reasons else 0), render(history, scope, nights, reasons)


def parse_radios(text: str) -> list[str]:
    radios = [radio.strip() for radio in text.split(",") if radio.strip()]
    if not radios:
        raise argparse.ArgumentTypeError("the radio scope is empty")
    return radios


def parse_no_verdict(text: str) -> NoVerdict:
    """Entries `YYYY-MM-DD:RADIO`, or `YYYY-MM-DD` for every radio, comma-separated."""
    entries: set[tuple[str, str | None]] = set()
    for item in filter(None, (part.strip() for part in text.split(","))):
        day, _, radio = item.partition(":")
        try:
            entries.add((date.fromisoformat(day).isoformat(), radio.strip() or None))
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"bad no verdict entry {item!r}, expected YYYY-MM-DD or YYYY-MM-DD:RADIO"
            ) from None
    return frozenset(entries)


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
    parser.add_argument(
        "--radios",
        required=True,
        type=parse_radios,
        help="comma-separated labels of the radios in scope",
    )
    parser.add_argument("--nights", type=int, default=3)
    parser.add_argument(
        "--no-verdict",
        type=parse_no_verdict,
        default="",
        help="comma-separated YYYY-MM-DD:RADIO (or YYYY-MM-DD, every radio) nights "
        "that are neither a pass nor a failure",
    )
    args = parser.parse_args(argv)
    if args.nights < 1:
        parser.error("--nights must be at least 1")
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    repo, token = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
    try:
        status, summary = certify(
            list_runs(api, repo, token),
            lambda run_id: list_jobs(api, repo, token, run_id),
            args.radios,
            args.nights,
            args.no_verdict,
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
