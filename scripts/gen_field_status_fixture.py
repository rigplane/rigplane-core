#!/usr/bin/env python3
"""Regenerate the golden frontend field-status fixture (MOR-429).

Pipeline:

    StateSnapshot.empty()
        -> build_public_state_payload_from_snapshot(radio=None, receiver_count=2)
        -> payload["fieldStatus"]
        -> frontend/src/lib/state/__tests__/fixtures/empty-store-field-status.json

The fixture pins the exact public `field_status` key shape the backend emits so
the frontend availability resolver (`$lib/state/field-status`) cannot drift away
from it. `tests/test_field_status_frontend_fixture.py` asserts the committed
fixture against the live projection read-only, so backend key changes fail CI
instead of being silently absorbed; regenerating is a deliberate step whose diff
is reviewed like any other change.

Usage:
    python scripts/gen_field_status_fixture.py           # write the fixture
    python scripts/gen_field_status_fixture.py --check   # exit 1 if it is stale
    python scripts/gen_field_status_fixture.py --stdout  # print it only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Repo layout: scripts/ -> repo root; the package lives under src/.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from rigplane.core.state_store import StateSnapshot  # noqa: E402
from rigplane.web.runtime_helpers import (  # noqa: E402
    build_public_state_payload_from_snapshot,
)

_FIXTURE_PATH = (
    _REPO_ROOT
    / "frontend"
    / "src"
    / "lib"
    / "state"
    / "__tests__"
    / "fixtures"
    / "empty-store-field-status.json"
)

# Two receivers so the per-receiver leaves the LCD panels gate on (main.*/sub.*)
# are present in the projection.
_RECEIVER_COUNT = 2


def _render_fixture() -> str:
    """Serialize the empty-store `field_status` projection in canonical form."""
    payload = build_public_state_payload_from_snapshot(
        StateSnapshot.empty(),
        radio=None,
        receiver_count=_RECEIVER_COUNT,
    )
    field_status: Any = payload["fieldStatus"]
    if not isinstance(field_status, dict):
        raise TypeError(f"expected a fieldStatus mapping, got {type(field_status)!r}")
    return json.dumps(field_status, indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail on drift")
    parser.add_argument("--stdout", action="store_true", help="print fixture only")
    args = parser.parse_args()

    rendered = _render_fixture()
    if args.stdout:
        sys.stdout.write(rendered)
        return 0

    existing = (
        _FIXTURE_PATH.read_text(encoding="utf-8") if _FIXTURE_PATH.exists() else None
    )

    if args.check:
        if existing != rendered:
            sys.stderr.write(
                f"ERROR: {_FIXTURE_PATH.relative_to(_REPO_ROOT)} is stale.\n"
                "Run `uv run python scripts/gen_field_status_fixture.py` "
                "and commit the result.\n"
            )
            return 1
        sys.stdout.write("Field-status fixture is up to date.\n")
        return 0

    _FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _FIXTURE_PATH.write_text(rendered, encoding="utf-8")
    sys.stdout.write(f"Wrote {_FIXTURE_PATH}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
