"""``rigplane validate`` subcommand — real-radio validation matrix runner.

This ships the dry-run path only: it loads a capability-declaration template,
applies operator-safety gating, and emits a machine-readable validation
artifact (or a human summary). Hardware execution is intentionally not
implemented in this version; the ``--hardware`` flag is double-gated by
``--allow-hardware`` and the ``RIGPLANE_VALIDATION_ALLOW_HARDWARE=1`` environment
variable, and even when both gates are open the command refuses with exit 3
because no hardware path exists yet.

Exit codes:

* ``0`` — success (dry-run artifact emitted). Failed/blocked dry-run checks do
  NOT change the exit code.
* ``2`` — template missing, unreadable, or schema-invalid.
* ``3`` — hardware run requested but blocked (gates closed or not implemented).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from rigplane import __version__
from rigplane.validation import (
    HARDWARE_OPT_IN_ENV,
    OperatorSafetyBlock,
    TransportInfo,
    build_validation_artifact,
    dry_run_results,
    human_summary,
    load_template,
)
from rigplane.validation.schema import SchemaValidationError


def add_subparser(sub: Any) -> argparse.ArgumentParser:
    """Register the ``validate`` subparser on ``sub`` (an ``_SubParsersAction``).

    Typed as ``Any`` because ``argparse._SubParsersAction`` is private and the
    surrounding parser code in ``cli/__init__.py`` follows the same convention.
    """
    p: argparse.ArgumentParser = sub.add_parser(
        "validate",
        help="Run the real-radio validation matrix (dry-run by default).",
    )
    p.add_argument(
        "--template",
        required=True,
        help="Path to a validation matrix template JSON file.",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="Plan checks without touching hardware (default behavior).",
    )
    p.add_argument(
        "--hardware",
        action="store_true",
        help="Request a real-radio run (double-gated; not implemented this release).",
    )
    p.add_argument(
        "--allow-hardware",
        action="store_true",
        help=f"First hardware gate; also requires {HARDWARE_OPT_IN_ENV}=1.",
    )
    p.add_argument(
        "--tx-allowed",
        dest="tx_allowed",
        action="store_true",
        help="Authorize TX-adjacent checks (PTT/TX).",
    )
    p.add_argument(
        "--tuner-allowed",
        dest="tuner_allowed",
        action="store_true",
        help="Authorize tuner tune-cycle checks.",
    )
    p.add_argument(
        "--operator-id",
        dest="operator_id",
        default=None,
        help="Operator identifier recorded in the safety block.",
    )
    p.add_argument(
        "--output",
        default=None,
        help="Write the JSON artifact to this path instead of stdout.",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Emit the artifact as JSON (default: human summary).",
    )
    return p


def run(args: argparse.Namespace) -> int:
    try:
        template = load_template(Path(args.template))
    except (SchemaValidationError, OSError) as exc:
        print(f"Error: cannot load template: {exc}", file=sys.stderr)
        return 2

    authorized = bool(args.tx_allowed or args.tuner_allowed)
    safety = OperatorSafetyBlock(
        tx_allowed=args.tx_allowed,
        tuner_allowed=args.tuner_allowed,
        operator_id=args.operator_id,
        authorized_at_unix=int(time.time()) if authorized else None,
    )

    if args.hardware:
        if not (args.allow_hardware and os.environ.get(HARDWARE_OPT_IN_ENV) == "1"):
            print(
                "Error: hardware validation is blocked. Both gates are required:\n"
                "  1. pass --allow-hardware on the command line, and\n"
                f"  2. set {HARDWARE_OPT_IN_ENV}=1 in the environment.",
                file=sys.stderr,
            )
            return 3
        # Even with both gates open, the hardware path is not implemented in
        # this release. Refuse explicitly rather than silently dry-running.
        print(
            "Error: hardware validation is not implemented in this release; "
            "run without --hardware for a dry-run plan.",
            file=sys.stderr,
        )
        return 3

    levels = dry_run_results(template, safety)
    transport = TransportInfo(backend="fixture")
    artifact = build_validation_artifact(
        template=template,
        levels=levels,
        transport=transport,
        safety=safety,
        core_version=__version__,
        core_commit=None,
        mode="dry-run",
    )

    if args.json or args.output:
        text = json.dumps(artifact.to_dict(), indent=2)
        if args.output:
            Path(args.output).write_text(text + "\n", encoding="utf-8")
            print(f"Artifact written to: {args.output}", file=sys.stderr)
        else:
            print(text)
    else:
        print(human_summary(artifact))
    return 0
