#!/usr/bin/env python3
"""Decide whether a merge-queue run may reuse the pull-request run (MOR-3878).

The first entry of a merge group is the pull request merged onto the current
main. When that tree is byte-identical to the tree of the pull request head,
the required checks that already passed on the head tested exactly the same
code, so the queue run does not repeat the heavy jobs: their job-level `if:`
skips them, and a skipped job satisfies the required check.

The answer is `reuse=true` only when all of these hold; any doubt, any API
error and any malformed input answers `reuse=false`, which runs the tests:

* the queue ref names exactly one pull request;
* the tree id of the merge-group commit equals the tree id of that pull
  request's current head commit;
* every check named with `--check` has completed with `success` on that head.

Run this file from a checkout of the merge group's *base* commit, never from
the queued commit: a pull request must not be able to change the code that
decides to skip its own tests.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from typing import Callable, Sequence

QUEUE_REF = re.compile(r"^(?:refs/heads/)?gh-readonly-queue/.+/pr-(\d+)-[0-9a-f]{40}$")
TREE_ID = re.compile(r"^[0-9a-f]{40}$")

Getter = Callable[[str], object]


def decide(
    get: Getter, repo: str, head_ref: str, merge_tree: str, checks: Sequence[str]
) -> tuple[bool, str]:
    match = QUEUE_REF.fullmatch(head_ref)
    if not match:
        return False, f"queue ref {head_ref!r} does not name one pull request"
    if not TREE_ID.fullmatch(merge_tree):
        return False, "the merge group commit has no usable tree id"
    if not checks:
        return False, "no check was named"
    number = int(match.group(1))
    pull = get(f"repos/{repo}/pulls/{number}")
    head_sha = pull["head"]["sha"]  # type: ignore[index]
    head_tree = get(f"repos/{repo}/git/commits/{head_sha}")["tree"]["sha"]  # type: ignore[index]
    if head_tree != merge_tree:
        return False, f"PR #{number} head tree {head_tree[:7]} differs from the queue tree"
    for name in checks:
        query = urllib.parse.urlencode(
            {"check_name": name, "filter": "latest", "per_page": 100}
        )
        runs = get(f"repos/{repo}/commits/{head_sha}/check-runs?{query}")["check_runs"]  # type: ignore[index]
        if not runs:
            return False, f"PR #{number} head has no '{name}' check run"
        for run in runs:  # type: ignore[attr-defined]
            if run["status"] != "completed" or run["conclusion"] != "success":
                return False, f"PR #{number} head '{name}' is {run['status']}/{run['conclusion']}"
    return True, f"PR #{number} head {head_sha[:7]} has the queue tree and passed {', '.join(checks)}"


def github_getter(token: str, api_url: str) -> Getter:
    def get(path: str) -> object:
        request = urllib.request.Request(
            f"{api_url.rstrip('/')}/{path}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            return json.load(response)

    return get


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="append", default=[], help="required check name")
    args = parser.parse_args(argv)
    try:
        reuse, reason = decide(
            github_getter(
                os.environ["GH_TOKEN"], os.environ.get("GITHUB_API_URL", "https://api.github.com")
            ),
            os.environ["QUEUE_REPO"],
            os.environ["QUEUE_REF"],
            os.environ["QUEUE_TREE"],
            args.check,
        )
    except Exception as error:  # fail closed: any doubt runs the tests
        reuse, reason = False, f"cannot decide ({type(error).__name__}: {error})"
    print(f"queue tree reuse: {reuse} - {reason}")
    output = os.environ.get("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as handle:
            handle.write(f"reuse={'true' if reuse else 'false'}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
