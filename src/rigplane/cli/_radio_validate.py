"""``rigplane radio-validate <model>`` — profile-driven validation entrypoint.

This is the first-class, profile-driven form of the universal validation
matrix. It is a THIN WRAPPER (ADR D7,
``docs/plans/2026-05-28-universal-validation-matrix.md`` §9) over the existing
``validate`` run path in :mod:`rigplane.cli._validate` — NOT a fork. The
``validate`` subcommand already performs profile-driven Gen-A/Gen-B template
generation, ``--provider native|hamlib|both``, comparison, and safety gating;
``radio-validate`` simply makes the profile-driven form ergonomic with a
positional ``model`` and adds ``--write-template`` to dump the generated
in-memory matrix.

``run`` normalises the parsed namespace (maps the positional model onto
``args.model``, forces ``args.template = None`` so the shared path generates
from the profile) and then delegates to :func:`rigplane.cli._validate.run`.
The only logic that lives here is argument normalisation and the small
``--write-template`` dump.

.. note::
   MOR-220 (slice 204b) will add a ``convert`` subcommand to this verb. The
   positional-model form here is forward-compatible: 204b may restructure the
   parser into subparsers (``radio-validate <model>`` vs
   ``radio-validate convert ...``) without changing this delegation contract.

Exit codes mirror ``validate``: ``0`` success (artifact or template emitted),
``2`` model missing/unknown, ``3`` hardware blocked / connect failure.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

import rigplane.cli._validate as _validate


def add_subparser(sub: Any) -> argparse.ArgumentParser:
    """Register the ``radio-validate`` subparser on ``sub``.

    Typed as ``Any`` because ``argparse._SubParsersAction`` is private and the
    surrounding parser code in ``cli/__init__.py`` follows the same convention.
    """
    p: argparse.ArgumentParser = sub.add_parser(
        "radio-validate",
        help=(
            "Validate a radio against the universal matrix, generated from its "
            "profile (dry-run by default). Thin wrapper over 'validate'."
        ),
    )
    # Distinct dest so the positional does not shadow the global --model option
    # (which lives on the root parser). run() reconciles the two.
    p.add_argument(
        "radio_validate_model",
        nargs="?",
        default=None,
        metavar="MODEL",
        help=(
            "Radio model (e.g. X6200, IC-7610). If omitted, the global --model "
            "is used; if neither is given the command errors."
        ),
    )
    p.add_argument(
        "--provider",
        choices=["native", "hamlib", "both"],
        default="native",
        help=(
            "Validation provider: native (default) drives the radio directly; "
            "hamlib drives it through an internally-spawned Hamlib rigctld and "
            "compares results; both runs native then hamlib sequentially."
        ),
    )
    p.add_argument(
        "--read-only",
        dest="read_only",
        action="store_true",
        help=(
            "Disable all writes (write checks SKIP). Default is read/write "
            "with automatic restore."
        ),
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
        "--hardware",
        action="store_true",
        help=(
            "Run against a real connected radio (double-gated; read/write with "
            "restore by default)."
        ),
    )
    p.add_argument(
        "--allow-hardware",
        action="store_true",
        help="First hardware gate; also requires RIGPLANE_VALIDATION_ALLOW_HARDWARE=1.",
    )
    p.add_argument(
        "--compare",
        default=None,
        help=(
            "Path to a prior validation artifact JSON to diff this run against "
            "(per-check status)."
        ),
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
    p.add_argument(
        "--write-template",
        dest="write_template",
        default=None,
        metavar="PATH",
        help=(
            "Dump the generated in-memory validation matrix to PATH as JSON "
            "and exit (no hardware). Useful for inspecting or hand-editing the "
            "template before a hardware run."
        ),
    )
    return p


def run(args: argparse.Namespace) -> int:
    # Reconcile the positional model with the global --model: positional wins,
    # else fall back to the global option.
    positional = getattr(args, "radio_validate_model", None)
    if positional:
        args.model = positional
    if not getattr(args, "model", None):
        print(
            "Error: no radio model given. Pass a positional model "
            "(e.g. 'rigplane radio-validate X6200') or the global --model.",
            file=sys.stderr,
        )
        return 2

    # This verb is always profile-driven: the shared run path generates the
    # template in-memory from the profile when --template is None.
    args.template = None

    provider = getattr(args, "provider", "native")

    # --write-template: build the in-memory matrix and dump it, no hardware.
    if getattr(args, "write_template", None):
        from rigplane.profiles import get_radio_profile

        try:
            profile = get_radio_profile(args.model)
        except KeyError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        gen_provider = "native" if provider == "both" else provider
        template = _validate._generate_template(args, profile, gen_provider)
        text = json.dumps(template.to_dict(), indent=2)
        Path(args.write_template).write_text(text + "\n", encoding="utf-8")
        print(f"Template written to: {args.write_template}", file=sys.stderr)
        return 0

    # Delegate to the existing validate run path — single source of truth for
    # generation, hardware orchestration, comparison, and artifact emission.
    return _validate.run(args)
