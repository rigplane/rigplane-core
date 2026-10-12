#!/usr/bin/env python3
"""Delete the repository's Actions artifacts that are older than a maximum age."""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

PER_PAGE = 100
Artifact = dict[str, Any]


def select_expired(
    artifacts: list[Artifact], now: datetime, max_age: timedelta
) -> tuple[list[Artifact], list[Artifact]]:
    """Split into (strictly older than max_age, not older); expired ones are in neither."""
    to_delete: list[Artifact] = []
    to_keep: list[Artifact] = []
    for artifact in artifacts:
        if artifact["expired"]:
            continue
        created = datetime.fromisoformat(artifact["created_at"])
        (to_delete if now - created > max_age else to_keep).append(artifact)
    return to_delete, to_keep


def cleanup(
    artifacts: list[Artifact],
    now: datetime,
    max_age: timedelta,
    delete: Callable[[int], None],
    dry_run: bool,
) -> tuple[str, int]:
    """Delete what select_expired picks (nothing in a dry run); return summary and failures."""
    to_delete, kept = select_expired(artifacts, now, max_age)
    deleted: list[Artifact] = []
    failed: list[Artifact] = []
    if not dry_run:
        for artifact in to_delete:
            try:
                delete(artifact["id"])
            except OSError as error:
                print(
                    f"delete failed for artifact {artifact['id']}: {error}",
                    file=sys.stderr,
                )
                failed.append(artifact)
            else:
                deleted.append(artifact)
    rows = [
        ("Would delete", to_delete) if dry_run else ("Deleted", deleted),
        ("Failed to delete", failed),
        ("Kept", kept),
    ]
    title = f"### Artifact cleanup: older than {max_age / timedelta(days=1):g} days"
    lines = [title + (" (dry run)" if dry_run else ""), ""]
    lines += ["| Outcome | Artifacts | Bytes |", "| --- | ---: | ---: |"]
    for label, items in rows:
        total = sum(item["size_in_bytes"] for item in items)
        lines.append(f"| {label} | {len(items)} | {total} |")
    return "\n".join(lines), len(failed)


def _request(method: str, url: str, token: str) -> Any:
    request = urllib.request.Request(
        url,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    return urllib.request.urlopen(request, timeout=30)


def list_artifacts(api: str, repo: str, token: str) -> list[Artifact]:
    artifacts: list[Artifact] = []
    page = 1
    while True:
        url = f"{api}/repos/{repo}/actions/artifacts?per_page={PER_PAGE}&page={page}"
        with _request("GET", url, token) as response:
            batch = json.load(response)["artifacts"]
        artifacts += batch
        if len(batch) < PER_PAGE:
            return artifacts
        page += 1


def delete_artifact(api: str, repo: str, token: str, artifact_id: int) -> None:
    url = f"{api}/repos/{repo}/actions/artifacts/{artifact_id}"
    _request("DELETE", url, token).close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-age-days", type=float, default=3)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    api = os.environ.get("GITHUB_API_URL", "https://api.github.com")
    repo, token = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_TOKEN"]
    summary, failures = cleanup(
        list_artifacts(api, repo, token),
        datetime.now(timezone.utc),
        timedelta(days=args.max_age_days),
        lambda artifact_id: delete_artifact(api, repo, token, artifact_id),
        args.dry_run,
    )
    print(summary)
    if summary_path := os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write(summary + "\n")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
