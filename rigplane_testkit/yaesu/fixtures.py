"""Extract committed CAT conformance fixtures from private bridge recordings.

``extract_cat_pairs`` turns one recorder JSONL (``python -m
rigplane_testkit.yaesu record``) into ``cat_pairs.jsonl`` rows — the CAT
sibling of the IC-7610 ``civ_pairs.jsonl`` pipeline. The trim rule: per
window, the FIRST occurrence of every distinct request line and the reply
that closes it. A reply closes the pending request when it is ``?;`` or
carries the request's two-letter verb; any other line (an auto-information
push, a stale late reply) is skipped. A request with no closing reply is
not emitted: silence needs an explicit recording of the timeout, not a
guess. Only requests the rig profile declares as READS are emitted, so a
write that a capture allowed (a mode change for a calibration clip) never
becomes a "request that got no answer" fact.

CLI::

    python -m rigplane_testkit.yaesu.fixtures extract --profile ftx1 \
        --window NAME=RECORDING.jsonl [--window ...] --out cat_pairs.jsonl

Rows: ``{"window", "transport": "usb", "t", "dir", "line"}``; ``t`` is
seconds since the recording started (rounded, never asserted).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from rigplane.profiles.rig_loader import load_rig

from rigplane_testkit.yaesu.cat_fake import _RIGS_DIR
from rigplane_testkit.yaesu.recorder import CatReadOnlyGuard, cat_lines


def extract_cat_pairs(
    path: str | Path, window: str, guard: CatReadOnlyGuard
) -> list[dict[str, Any]]:
    """The trimmed request/reply rows of one recording (see module docstring)."""
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    pending: tuple[float, str] | None = None
    for t, direction, line in cat_lines(path):
        if direction == "c2r":
            pending = (t, line) if guard.allows(line.encode("ascii")) else None
            continue
        if pending is None:
            continue
        request_t, request = pending
        if line != "?;" and line[:2] != request[:2]:
            continue  # unsolicited or stale: not the answer to this request
        pending = None
        if request in seen:
            continue
        seen.add(request)
        for when, d, text in ((request_t, "c2r", request), (t, "r2c", line)):
            rows.append(
                {
                    "window": window,
                    "transport": "usb",
                    "t": round(when, 3),
                    "dir": d,
                    "line": text,
                }
            )
    return rows


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m rigplane_testkit.yaesu.fixtures")
    sub = parser.add_subparsers(dest="command", required=True)
    ext = sub.add_parser("extract", help="trim bridge recordings into cat_pairs.jsonl")
    ext.add_argument("--profile", default="ftx1", help="rig profile name or .toml path")
    ext.add_argument(
        "--window",
        action="append",
        required=True,
        metavar="NAME=PATH",
        help="repeatable",
    )
    ext.add_argument("--out", required=True)
    args = parser.parse_args(argv)
    profile = Path(args.profile)
    config = load_rig(profile if profile.suffix else _RIGS_DIR / f"{args.profile}.toml")
    guard = CatReadOnlyGuard.from_config(config)
    out: list[dict[str, Any]] = []
    for spec in args.window:
        name, _, path = spec.partition("=")
        out += extract_cat_pairs(path, name, guard)
    Path(args.out).write_text(
        "".join(json.dumps(r) + "\n" for r in out), encoding="utf-8"
    )
    print(f"wrote {len(out)} rows to {args.out}")


if __name__ == "__main__":
    main()
