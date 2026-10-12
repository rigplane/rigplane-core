"""Write a soak report's verdict and measured criteria as a Markdown summary.

    python -m scripts.soak.report_summary REPORT

appends the summary to the file named by ``$GITHUB_STEP_SUMMARY``. A missing
or unusable REPORT gets a "NO REPORT" summary and exit status 1; a FAIL
verdict in a usable report leaves the exit status 0. Standard library only.
"""

from __future__ import annotations

import json
import os
import sys
from typing import Any

MIB = 1024 * 1024


def render(report: dict[str, Any]) -> str:
    """The Markdown summary of one report, one line per pass criterion."""
    criteria = report["criteria"]
    rss, recovery = criteria["rssGrowth"], criteria["recovery"]
    slope = rss["slopeBytesPerHour"]
    lines = [
        f"## Soak {report['verdict']}: {report['durationActualS']:.0f} s"
        f" of {report['durationConfiguredS']:.0f} s",
        f"- RSS growth {rss['status']}: "
        + ("n/a" if slope is None else f"{slope / MIB:+.1f} MiB/h")
        + f" (limit {rss['limitBytesPerHour'] / MIB:.0f} MiB/h)",
    ]
    for label, key in (("FDs", "fdsStability"), ("Tasks", "tasksStability")):
        stability = criteria[key]
        checks = stability["checks"]
        worst = max((abs(check["delta"]) for check in checks), default=0)
        lines.append(
            f"- {label} {stability['status']}: max abs delta {worst}"
            f" ({len(checks)} checked, tolerance {stability['tolerance']})"
        )
    lines.append(
        f"- Unrecovered disconnects {recovery['status']}: {recovery['unrecovered']}"
        f" of {recovery['forced']} (budget {recovery['budgetS']:.0f} s)"
    )
    if report["error"]:
        lines.append(f"- Driver error: `{report['error']}`")
    return "\n".join(lines) + "\n"


def main(path: str) -> int:
    try:
        with open(path, encoding="utf-8") as fh:
            text, status = render(json.load(fh)), 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        text = (
            f"## Soak: NO REPORT\n\n`{path}` is missing or unusable"
            f" ({type(exc).__name__}).\n"
        )
        status = 1
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as summary:
        summary.write(text)
    return status


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
