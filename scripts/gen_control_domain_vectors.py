#!/usr/bin/env python3
"""Regenerate the control-domain contract vector fixture (MOR-2477).

Pipeline:

    rigs/*.toml
        -> load_rig(...).to_profile().controls   (the published normalized
           domain mapping, exactly what the frontend receives)
        -> rigplane.profiles.control_domain decode/encode/quantize
        -> tests/fixtures/control-domain-vectors.json

The fixture is the single shared truth consumed by BOTH suites: pytest
(``tests/contracts/test_control_domain_vectors.py``) asserts the committed
fixture is fresh, and vitest
(``frontend/src/lib/radio/__tests__/control-domain.vectors.test.ts``)
asserts ``control-domain.ts`` reproduces every vector. A rounding or
tie-rule change on either side therefore fails CI instead of drifting.

Vector sections per domain:

* ``decode``  — sampled raw lattice points -> canonical display strings;
* ``encode``  — the decoded display strings -> raw (plus a half-step
  midpoint and an out-of-range display, where the profile's own
  quantization decides the outcome);
* ``quantize`` — a half-step and a quarter-step display probe under every
  quantization posture, pinning the floor/ceil/tie rules.

Usage:
    python scripts/gen_control_domain_vectors.py           # write the fixture
    python scripts/gen_control_domain_vectors.py --check   # exit 1 if stale
    python scripts/gen_control_domain_vectors.py --stdout  # print it only
"""

from __future__ import annotations

import argparse
import json
import sys
from decimal import Decimal, localcontext
from pathlib import Path
from typing import Any

# Repo layout: scripts/ -> repo root; the package lives under src/.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from rigplane.profiles.control_domain import (  # noqa: E402
    decode_control_domain,
    encode_control_domain,
    quantize_control_domain,
)
from rigplane.profiles.rig_loader import discover_rigs  # noqa: E402

FIXTURE_PATH = _REPO_ROOT / "tests" / "fixtures" / "control-domain-vectors.json"

# Mappings that publish a scalar raw<->display band; "encoded" publishes
# discrete choices and has no arithmetic to pin.
_SCALAR_MAPPINGS = frozenset({"identity", "linear", "centered", "lookup"})
_QUANTIZATIONS = (
    "nearest_ties_down",
    "nearest_ties_up",
    "floor",
    "ceil",
    "reject",
)
_MAX_RAW_SAMPLES = 5
_DECIMAL_PRECISION = 200


def _canonical(value: Decimal) -> str:
    """Render an exact Decimal by the published canonical-string rule."""
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return "0" if rendered in {"0", "-0"} else rendered


def _sampled_raws(domain: dict[str, Any]) -> list[int]:
    """Raw lattice points: endpoints, origin, and an even stride when long."""
    low, high = int(domain["raw_min"]), int(domain["raw_max"])
    step, origin = int(domain["raw_step"]), int(domain["raw_origin"])
    count = (high - low) // step + 1
    picks = {0, count - 1, (origin - low) // step}
    if count > _MAX_RAW_SAMPLES:
        last = count - 1
        for index in range(_MAX_RAW_SAMPLES):
            picks.add(round(index * last / (_MAX_RAW_SAMPLES - 1)))
    return sorted(low + index * step for index in picks)


def _display_probes(domain: dict[str, Any]) -> list[str]:
    """Canonical displays: a lattice point and its half/quarter-step offsets."""
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        origin = Decimal(str(domain["display_origin"]))
        step = Decimal(str(domain["display_step"]))
        top = Decimal(str(domain["display_max"]))
        low = origin
        if origin + step > top:
            low = origin - step
        return [
            _canonical(low),
            _canonical(low + step / 2),
            _canonical(low + step / 4),
        ]


def _domain_entry(rig: str, control: str, domain: dict[str, Any]) -> dict[str, Any]:
    decode: list[list[Any]] = []
    encode: list[list[Any]] = []
    for raw in _sampled_raws(domain):
        display = decode_control_domain(domain, raw)
        assert display is not None, f"{rig}/{control}: raw {raw} failed to decode"
        encoded = encode_control_domain(domain, display)
        assert encoded == raw, f"{rig}/{control}: {display} encodes to {encoded}"
        decode.append([raw, display])
        encode.append([display, raw])
    probes = _display_probes(domain)
    lattice_point, midpoint, quarter = probes
    encode.append([midpoint, encode_control_domain(domain, midpoint)])
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        out_of_range = _canonical(
            Decimal(str(domain["display_max"])) + Decimal(str(domain["display_step"]))
        )
    encode.append([out_of_range, encode_control_domain(domain, out_of_range)])
    quantize: list[list[Any]] = []
    for quantization in _QUANTIZATIONS:
        for display in (midpoint, quarter):
            quantize.append(
                [
                    quantization,
                    display,
                    quantize_control_domain(
                        {**domain, "quantization": quantization}, display
                    ),
                ]
            )
    quantize.append(
        [
            "reject",
            probes[0],
            quantize_control_domain({**domain, "quantization": "reject"}, probes[0]),
        ]
    )
    return {
        "rig": rig,
        "control": control,
        "domain": domain,
        "decode": decode,
        "encode": encode,
        "quantize": quantize,
    }


def _domain_entries() -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for config in sorted(
        discover_rigs(_REPO_ROOT / "rigs").values(), key=lambda rig: rig.model
    ):
        profile = config.to_profile()
        controls = dict(profile.controls or {})
        for control in sorted(controls):
            domain = controls[control]
            if (
                not isinstance(domain, dict)
                or domain.get("mapping") not in _SCALAR_MAPPINGS
            ):
                continue
            entries.append(_domain_entry(profile.id, control, domain))
    return entries


def fixture_document() -> str:
    """Serialize the vector fixture: one line per domain and per section."""
    lines = ["{", '  "domains": [']
    entries = _domain_entries()
    for position, entry in enumerate(entries):
        lines.append("    {")
        lines.append(f'      "rig": {json.dumps(entry["rig"])},')
        lines.append(f'      "control": {json.dumps(entry["control"])},')
        lines.append(
            '      "domain": '
            f"{json.dumps(entry['domain'], sort_keys=True, separators=(',', ':'))},"
        )
        for section in ("decode", "encode", "quantize"):
            comma = "," if section != "quantize" else ""
            lines.append(
                f'      "{section}": '
                f"{json.dumps(entry[section], separators=(',', ':'))}{comma}"
            )
        lines.append("    }" if position == len(entries) - 1 else "    },")
    lines.append("  ]")
    lines.append("}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail on drift")
    parser.add_argument("--stdout", action="store_true", help="print fixture only")
    args = parser.parse_args()

    rendered = fixture_document()
    if args.stdout:
        sys.stdout.write(rendered)
        return 0

    existing = (
        FIXTURE_PATH.read_text(encoding="utf-8") if FIXTURE_PATH.exists() else None
    )
    if args.check:
        if existing != rendered:
            sys.stderr.write(
                f"ERROR: {FIXTURE_PATH.relative_to(_REPO_ROOT)} is stale.\n"
                "Run `uv run python scripts/gen_control_domain_vectors.py` "
                "and commit the result.\n"
            )
            return 1
        sys.stdout.write("Control-domain vector fixture is up to date.\n")
        return 0

    FIXTURE_PATH.parent.mkdir(parents=True, exist_ok=True)
    FIXTURE_PATH.write_text(rendered, encoding="utf-8")
    sys.stdout.write(f"Wrote {FIXTURE_PATH}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
