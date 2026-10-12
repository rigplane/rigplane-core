"""Summarize ``mutmut results --all`` text per mutated module.

Reads the four-space-indented ``<mutant name>: <status>`` lines that
``uv run mutmut results --all=true`` prints (mutmut 3.8 takes a value,
not a bare flag), attributes each mutant to the
longest matching module name given on the command line, and emits a JSON
object of the form::

    {
      "total":   {"killed": 0, "survived": 0, "timeout": 0, "suspicious": 0,
                  "skipped": 0, "no_tests": 0, "other": 0},
      "modules": {"<module>": {…same counters…}},
      "unmatched": 0
    }

Mutant names follow mutmut 3.x ``get_mutant_name``:
``<dotted module>.<mangled function>`` where mangled functions start with
``x_`` (plain functions) or ``xǁ`` (methods), so a module prefix plus one
dot-separator is an unambiguous attribution as long as the modules passed
in do not mangle names themselves. Lines that match no module are counted
under ``unmatched`` instead of being dropped, so a truncated or stale
module list is visible in the output. Statuses mutmut can print but this
script does not count individually (``segfault``, ``not checked``, …)
bucket into ``other``.

Usage::

    uv run python scripts/mutation/summarize.py \
        --results mutmut-results.txt --output mutmut-summary.json \
        rigplane.commands._frame rigplane.core.civ ...
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

COUNTER_KEYS = (
    "killed",
    "survived",
    "timeout",
    "suspicious",
    "skipped",
    "no_tests",
    "other",
)

STATUS_TO_KEY = {
    "killed": "killed",
    "survived": "survived",
    "timeout": "timeout",
    "suspicious": "suspicious",
    "skipped": "skipped",
    "no tests": "no_tests",
}

RESULT_LINE_RE = re.compile(r"^\s+(?P<name>\S+): (?P<status>.+?)\s*$")


def _zero_counts() -> dict[str, int]:
    return dict.fromkeys(COUNTER_KEYS, 0)


def parse_results(text: str) -> list[tuple[str, str]]:
    """Return ``(mutant_name, status)`` for every result line in ``text``."""
    results: list[tuple[str, str]] = []
    for line in text.splitlines():
        match = RESULT_LINE_RE.match(line)
        if match:
            results.append((match.group("name"), match.group("status")))
    return results


def _owning_module(name: str, modules: list[str]) -> str | None:
    for module in sorted(modules, key=len, reverse=True):
        if name == module or name.startswith(module + "."):
            return module
    return None


def summarize(text: str, modules: list[str]) -> dict:
    """Aggregate ``mutmut results`` text into per-module and total counters."""
    if len(set(modules)) != len(modules):
        raise ValueError(f"duplicate module names: {modules}")
    counts = {module: _zero_counts() for module in modules}
    total = _zero_counts()
    unmatched = 0
    for name, status in parse_results(text):
        key = STATUS_TO_KEY.get(status, "other")
        total[key] += 1
        owner = _owning_module(name, modules)
        if owner is None:
            unmatched += 1
        else:
            counts[owner][key] += 1
    return {
        "total": total,
        "modules": {module: counts[module] for module in sorted(modules)},
        "unmatched": unmatched,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Summarize `mutmut results --all` output per module.",
    )
    parser.add_argument(
        "--results",
        default="-",
        help="path to `mutmut results --all` text; '-' reads stdin",
    )
    parser.add_argument(
        "--output",
        default="-",
        help="path for the JSON summary; '-' writes stdout",
    )
    parser.add_argument(
        "modules",
        nargs="+",
        metavar="modules",
        help="dotted module names to attribute mutants to (longest match wins)",
    )
    args = parser.parse_args(argv)

    text = sys.stdin.read() if args.results == "-" else Path(args.results).read_text()
    summary = summarize(text, args.modules)
    payload = json.dumps(summary, indent=2, sort_keys=False) + "\n"
    if args.output == "-":
        sys.stdout.write(payload)
    else:
        Path(args.output).write_text(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
