#!/usr/bin/env python3
"""Regenerate the GENERATED block of ``frontend/src/lib/types/state.ts`` (MOR-881).

Pipeline (validated in the contract-codegen spike, all-MIT toolchain):

    ServerStatePublic / StateUpdateEnvelope  (pydantic v2 models)
        -> .model_json_schema()              (JSON Schema, draft 2020-12)
        -> json-schema-to-typescript (json2ts)
        -> spliced into state.ts between BEGIN/END GENERATED markers

The hand-written portion of ``state.ts`` (UiState, PendingCommand, the
client-only ``meterSource`` derived type, and any prose) lives OUTSIDE the
markers and is preserved verbatim.

Usage:
    python scripts/gen_state_types.py            # write into state.ts
    python scripts/gen_state_types.py --check     # exit 1 if state.ts is stale
    python scripts/gen_state_types.py --stdout     # print generated block only

``pydantic`` (dev/codegen extra) and ``json-schema-to-typescript`` (frontend
devDependency, run via ``npx``) are dev/build-only. Neither is a runtime
dependency.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

# Repo layout: scripts/ -> repo root; the schema lives under src/.
_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "src"))

from rigplane.web.state_schema import (  # noqa: E402
    ServerStatePublic,
    StateUpdateEnvelope,
)

_STATE_TS = _REPO_ROOT / "frontend" / "src" / "lib" / "types" / "state.ts"
_FRONTEND = _REPO_ROOT / "frontend"

BEGIN_MARKER = "// BEGIN GENERATED — do not edit by hand (scripts/gen_state_types.py)"
END_MARKER = "// END GENERATED"

_BANNER = (
    "// This block is generated from the pydantic schema in\n"
    "// src/rigplane/web/state_schema.py (the public state-payload contract,\n"
    "// MOR-881). Regenerate with `python scripts/gen_state_types.py`; the CI\n"
    "// state-types-gate fails on drift. Edit the pydantic model, not this block."
)

# Top-level pydantic models whose interfaces (and their shared $defs) form the
# server-sent contract. Order is stable for deterministic output.
_MODELS = [ServerStatePublic, StateUpdateEnvelope]

# Per-model ``required`` allowlists for the generated TypeScript.
#
# pydantic omits any field with a default from the JSON-Schema ``required``
# list AND would otherwise make every defaulted field optional in TS. We
# instead pin ``required`` explicitly so the generated contract is intentional,
# not an accident of which model fields happen to carry defaults.
#
# Policy (state-payload spec §8):
#   - Required = the fields existing consumers already depend on (the old
#     hand-written state.ts required set). Loosening these would break
#     production consumers (e.g. ``rx.att > 0``), so they stay required.
#   - Optional = (a) genuinely conditional wire fields (``sub`` for 1-RX,
#     ``dcd``/``fieldStatus`` snapshot-only, ``publicStateSeq`` server-added,
#     the per-observation FieldStatus extras) and (b) purely ADDITIVE fields
#     newly surfaced by this contract (``vfoA``/``vfoB``/``activeSlot``/
#     ``filterNum`` and the extra receiver scalars). Additive-as-optional is
#     safe: existing consumers ignore unknown optional fields.
# Any model not listed here requires ALL of its properties.
_REQUIRED_BY_MODEL: dict[str, set[str]] = {
    # The old hand-written state.ts required set. ``scopeControls`` /
    # ``radioDetail`` / ``radioHealth`` / ``txBandEdges`` are always present in
    # the real payload but were optional in the old contract and consumers
    # access them defensively (``?.``); keep them optional to avoid churning
    # consumers/fixtures (spec §8.7: a benign tightening the migration may relax).
    "ServerStatePublic": {
        "revision",
        "stateRevision",
        "freshnessRevision",
        "observationSeq",
        "updatedAt",
        "active",
        "ptt",
        "split",
        "dualWatch",
        "tunerStatus",
        "main",
        "connection",
    },
    "ReceiverStatePublic": {
        "freqHz",
        "mode",
        "filter",
        "dataMode",
        "sMeter",
        "att",
        "preamp",
        "nb",
        "nr",
        "afLevel",
        "rfGain",
        "squelch",
    },
    # Only ``observed=true`` entries carry the extras; the bare missing-entry is
    # {storePath, observed, freshness, availability}.
    "FieldStatusPublic": {"storePath", "observed", "freshness", "availability"},
    # Envelope framing is always present; ``data`` (full) vs ``changed`` +
    # ``removed`` (delta) and the echoed revisions are frame-conditional.
    "StateUpdateEnvelope": {"type", "revision", "transportSeq"},
}


def _promote_required(defs: dict[str, object]) -> None:
    """Pin each model's ``required`` set per :data:`_REQUIRED_BY_MODEL`.

    Models absent from the map require all their properties (e.g. the small
    synthetic objects ``ConnectionPublic`` / ``ScopeControlsPublic`` /
    ``RadioHealthPublic``, whose fields are always present in the payload).
    """
    for model_name, definition in defs.items():
        if not isinstance(definition, dict):
            continue
        props = definition.get("properties")
        if not isinstance(props, dict):
            continue
        if model_name in _REQUIRED_BY_MODEL:
            required = _REQUIRED_BY_MODEL[model_name]
            definition["required"] = [name for name in props if name in required]
        else:
            definition["required"] = list(props)


def _strip_property_titles(node: object) -> None:
    """Recursively drop pydantic's auto-generated per-property ``title`` keys.

    pydantic titles every field (``freqHz`` -> ``"Freqhz"``); json2ts hoists
    each titled property into a noisy standalone ``export type`` alias. Removing
    property-level titles makes json2ts inline the scalar/enum directly into the
    parent interface. Model-level ``$defs`` titles (the interface names) are kept
    because they live one level up, on the definition object, not on a property.
    """
    if isinstance(node, dict):
        props = node.get("properties")
        if isinstance(props, dict):
            for prop_schema in props.values():
                if isinstance(prop_schema, dict):
                    prop_schema.pop("title", None)
        for value in node.values():
            _strip_property_titles(value)
    elif isinstance(node, list):
        for item in node:
            _strip_property_titles(item)


def _combined_schema() -> dict[str, object]:
    """Build one JSON-Schema document holding every model as a shared ``$defs``.

    Emitting one document (rather than one per model) means shared sub-models
    (ScopeControlsPublic, FieldStatusPublic, …) are defined exactly once, so
    json2ts produces no duplicate interfaces.
    """
    defs: dict[str, object] = {}
    for model in _MODELS:
        schema = model.model_json_schema(ref_template="#/$defs/{model}")
        for name, definition in schema.pop("$defs", {}).items():
            defs.setdefault(name, definition)
        defs[model.__name__] = schema
    _promote_required(defs)
    # json2ts only walks definitions reachable from the root, so reference each
    # top-level model from the root ``properties``. The root carrier interface
    # itself is stripped from the output; every reachable ``$defs`` entry is
    # emitted once as a named interface.
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "RigPlaneStateContract",
        "type": "object",
        "additionalProperties": False,
        "properties": {
            model.__name__: {"$ref": f"#/$defs/{model.__name__}"} for model in _MODELS
        },
        "$defs": defs,
    }


def _run_json2ts(schema: dict[str, object]) -> str:
    _strip_property_titles(schema)
    with tempfile.NamedTemporaryFile(
        "w", suffix=".json", delete=False, encoding="utf-8"
    ) as handle:
        json.dump(schema, handle)
        schema_path = handle.name
    try:
        result = subprocess.run(
            [
                "npx",
                "--no-install",
                "json2ts",
                "-i",
                schema_path,
                "--no-additionalProperties",
                "--bannerComment",
                "",
            ],
            cwd=_FRONTEND,
            capture_output=True,
            text=True,
        )
    finally:
        Path(schema_path).unlink(missing_ok=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(
            "json2ts failed. Ensure `json-schema-to-typescript` is installed "
            "in frontend devDependencies (`npm --prefix frontend ci`)."
        )
    return result.stdout


# The synthetic carrier interface json2ts emits for the schema root. It only
# exists to make json2ts walk the per-model $defs; drop it from the output.
_CARRIER_RE = re.compile(
    r"export interface RigPlaneStateContract \{[^}]*\}\n?",
)


def _generated_block() -> str:
    body = _run_json2ts(_combined_schema()).strip()
    body = _CARRIER_RE.sub("", body).strip()
    # No trailing newline after END_MARKER: the surrounding text (the spliced
    # tail, or the prepend join below) owns the separating whitespace, so the
    # output is byte-stable across regenerations.
    return f"{BEGIN_MARKER}\n{_BANNER}\n\n{body}\n{END_MARKER}"


def _splice(existing: str, block: str) -> str:
    if BEGIN_MARKER in existing and END_MARKER in existing:
        head = existing.split(BEGIN_MARKER, 1)[0]
        tail = existing.split(END_MARKER, 1)[1]
        return f"{head}{block}{tail}"
    # First run: prepend the generated block above the hand-written section.
    return f"{block}\n{existing}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail on drift")
    parser.add_argument(
        "--stdout", action="store_true", help="print generated block only"
    )
    args = parser.parse_args()

    block = _generated_block()
    if args.stdout:
        sys.stdout.write(block)
        return 0

    existing = _STATE_TS.read_text(encoding="utf-8")
    updated = _splice(existing, block)

    if args.check:
        if existing != updated:
            sys.stderr.write(
                "ERROR: frontend/src/lib/types/state.ts is stale.\n"
                "Run `python scripts/gen_state_types.py` and commit the result.\n"
            )
            return 1
        sys.stdout.write("state.ts generated block is up to date.\n")
        return 0

    _STATE_TS.write_text(updated, encoding="utf-8")
    sys.stdout.write(f"Wrote generated block into {_STATE_TS}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
