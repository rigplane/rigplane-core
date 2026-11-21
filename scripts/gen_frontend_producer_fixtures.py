"""Regenerate the frontend producer fixtures consumed by vitest suites.

Three vitest suites need documents built by Core's own producers (the
``EiBiStation`` serializer and the web server's public-state builder). They
used to spawn ``uv run python`` synchronously at test time; on a saturated
4-CPU CI slot the interpreter start alone outlasts the subprocess wall budget
(MOR-4214, run 44869233407: all three failed with ``spawnSync uv
ETIMEDOUT``). The suites now import committed fixtures instead — same pattern
as ``gen_state_wire_goldens.py`` — and this script regenerates them:

    uv run python scripts/gen_frontend_producer_fixtures.py

``tests/web/test_frontend_producer_fixtures.py`` keeps the files equal to the
producers' output (``--check``), so a producer change fails that suite until
the fixtures are regenerated and the diff reviewed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[1]
# ``tests/liveness_ws_trace.py`` and ``tests/test_host_api_v3_wire_contract.py``
# import their siblings bare (``from test_web_server import ...``), exactly as
# when vitest used to run them as scripts with ``tests/`` on ``sys.path``.
sys.path.insert(0, str(_REPO_ROOT / "tests"))

_FIXTURES = {
    "liveness-ws-trace": _REPO_ROOT
    / "frontend/src/lib/transport/__tests__/fixtures/liveness-ws-trace.json",
    "eibi-identify-replies": _REPO_ROOT
    / "frontend/src/components-v2/layout/__tests__/fixtures/eibi-identify-replies.json",
    "host-api-v3-documents": _REPO_ROOT
    / "frontend/src/lib/local-extensions/__tests__/fixtures/host-api-v3-documents.json",
}

# Fields the liveness trace derives from the real wall/monotonic clock; the
# test only asserts the patched-clock liveness ages, so they are pinned to
# stable sentinels to keep the committed fixture byte-stable.
_VOLATILE_SENTINELS: dict[str, Any] = {
    "updatedAt": "2000-01-01T00:00:00+00:00",
    "sinceMs": 0,
    "lastObservedMonotonic": 0.0,
}


def _mask_volatile(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                _VOLATILE_SENTINELS[key]
                if key in _VOLATILE_SENTINELS
                else _mask_volatile(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_mask_volatile(item) for item in value]
    return value


def _render_liveness_ws_trace() -> Any:
    from liveness_ws_trace import capture_liveness_deliveries

    return _mask_volatile(asyncio.run(capture_liveness_deliveries()))


def _eibi_station(khz: float, name: str) -> dict:
    from rigplane.web.eibi import EiBiStation

    return EiBiStation(
        freq_khz=khz, time_start=0, time_end=2400, days="", country="USA",
        station=name, language="E", target="NAm", remarks="", persistence=1,
        start_date="", end_date="",
    ).to_dict()


def _render_eibi_identify_replies() -> Any:
    return {
        14_074_000: {
            "stations": [_eibi_station(14_074.0, "Station A")],
            "freq_hz": 14_074_000,
        },
        7_074_000: {
            "stations": [_eibi_station(7_074.0, "Station B")],
            "freq_hz": 7_074_000,
        },
    }


def _render_host_api_v3_documents() -> Any:
    from test_host_api_v3_wire_contract import (
        FieldPath,
        WebConfig,
        WebServer,
        _capable_radio,
        _observe,
    )

    server = WebServer(_capable_radio(), WebConfig(radio_model="IC-7610"))
    _observe(server, FieldPath.receiver("0", "freq_mode", "freq_hz"), 14_205_000)
    fields = server.build_public_state()["fieldStatus"]
    return {
        "station": {"stations": [_eibi_station(14_074, "Host Test Station")]},
        "fields": {"main.freqHz": fields["main.freqHz"]},
    }


_RENDERERS = {
    "liveness-ws-trace": _render_liveness_ws_trace,
    "eibi-identify-replies": _render_eibi_identify_replies,
    "host-api-v3-documents": _render_host_api_v3_documents,
}


def render_fixtures() -> dict[str, str]:
    """Canonical JSON per fixture: sorted keys, minified, trailing newline."""
    return {
        name: json.dumps(renderer(), sort_keys=True, separators=(",", ":")) + "\n"
        for name, renderer in _RENDERERS.items()
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail on drift")
    args = parser.parse_args()

    rendered = render_fixtures()
    stale = []
    for name, path in _FIXTURES.items():
        existing = path.read_text(encoding="utf-8") if path.exists() else None
        if existing != rendered[name]:
            stale.append(name)
            if not args.check:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(rendered[name], encoding="utf-8")
                sys.stdout.write(f"Wrote {path}\n")

    if args.check:
        if stale:
            sys.stderr.write(
                "ERROR: frontend producer fixtures are stale: "
                + ", ".join(sorted(stale))
                + "\nRun `uv run python "
                "scripts/gen_frontend_producer_fixtures.py` and commit the "
                "result.\n"
            )
            return 1
        sys.stdout.write("Frontend producer fixtures are up to date.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
