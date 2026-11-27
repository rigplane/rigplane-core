#!/usr/bin/env python3
"""Regenerate tests/assertion_free_uncovered.txt (MOR-4890).

The one-command fix the assertion-free gate's failure message names: the
gate (tests/test_meta_assertions.py) regenerates the ledger in memory
and fails WITH THE DIFF, and this script writes it. A bare run rewrites
the ledger in place; --check regenerates in memory, prints the diff with
the fix command, and exits 1 when stale.

The scan and render live in tests/test_meta_assertions.py (loaded by
path, mirroring scripts/pre_push_scope.py's use of CI's classifier);
this script is only the entry point, so the counting rule has one home.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path
from typing import Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]


def _load_meta():
    """The gate module: the single home of the counting rule."""
    spec = importlib.util.spec_from_file_location(
        "regen_assertion_free_ledger_meta",
        ROOT / "tests/test_meta_assertions.py",
    )
    assert spec is not None and spec.loader is not None
    meta = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = meta
    spec.loader.exec_module(meta)
    return meta


_meta = _load_meta()


def scan_files(tests_dir: Path):
    return _meta._scan_files(tests_dir)


def render_ledger(offending: Mapping[str, Sequence]) -> str:
    return _meta._render_ledger(offending)


def check(root: Path = ROOT) -> str:
    """Diff of the committed ledger against a fresh scan ("" when fresh)."""
    tests_dir = root / "tests"
    ledger = tests_dir / "assertion_free_uncovered.txt"
    committed = ledger.read_text(encoding="utf-8") if ledger.exists() else ""
    offending = scan_files(tests_dir)
    listed = _meta._parse_ledger_text(committed)
    unlisted, stale = _meta._ratchet_failures(offending, listed)
    detail_parts = []
    if unlisted:
        detail_parts.append(_meta._describe({p: offending[p] for p in unlisted}))
    if stale:
        detail_parts.append("no longer offend: " + ", ".join(stale))
    return _meta._stale_ledger_message(committed, offending, "\n".join(detail_parts))


def main(argv: Sequence[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="regenerate in memory and fail WITH THE DIFF instead of writing",
    )
    args = parser.parse_args(argv)
    tests_dir = root / "tests"
    ledger = tests_dir / "assertion_free_uncovered.txt"
    if args.check:
        message = check(root)
        if not message:
            return 0
        sys.stdout.write(message)
        return 1
    offending = scan_files(tests_dir)
    ledger.write_text(render_ledger(offending), encoding="utf-8")
    print(f"{ledger.name}: {len(offending)} offending files listed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
