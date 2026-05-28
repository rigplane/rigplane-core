"""``rigplane validate`` subcommand — real-radio validation matrix runner.

By default this runs the dry-run path: it loads a capability-declaration
template, applies operator-safety gating, and emits a machine-readable
validation artifact (or a human summary). The ``--hardware`` flag is
double-gated by ``--allow-hardware`` and the
``RIGPLANE_VALIDATION_ALLOW_HARDWARE=1`` environment variable; when both gates
are open it connects to the configured radio and executes the checks. The
default posture is read/write with automatic restore: each RX-safe write check
reads the original value, writes a different one, verifies the readback, then
restores the original. ``--read-only`` disables all writes (write checks SKIP).
TX and tuner are never auto-actuated.

Exit codes:

* ``0`` — success (dry-run or hardware artifact emitted). Failed/blocked checks
  do NOT change the exit code.
* ``2`` — template missing, unreadable, or schema-invalid.
* ``3`` — hardware run requested but blocked (gates closed), or the radio could
  not connect / authenticate / build a backend config (artifact still emitted
  on connect failure).
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import datetime
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

from rigplane import __version__
from rigplane.validation import (
    HARDWARE_OPT_IN_ENV,
    MatrixTemplate,
    OperatorSafetyBlock,
    TransportInfo,
    ValidationArtifact,
    build_validation_artifact,
    dry_run_results,
    human_summary,
    load_template,
)
from rigplane.validation.schema import (
    CapabilityDeclaration,
    CheckResult,
    CheckStatus,
    FailureDomain,
    LevelResult,
    SchemaValidationError,
    ValidationLevel,
)


def _utcnow_iso() -> str:
    """Return the current UTC time as an ISO-8601 millisecond ``...Z`` string.

    Local duplicate of ``rigplane.validation.hardware._utcnow_iso`` so this
    module keeps its ``validation.hardware`` import deferred (function-local).
    """
    return (
        datetime.datetime.now(datetime.UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


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
        help="Run against a real connected radio (double-gated; read/write with restore by default).",
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
        "--read-only",
        dest="read_only",
        action="store_true",
        help=(
            "Disable all writes (write checks SKIP). Default is read/write "
            "with automatic restore: read the original, write a different "
            "value, verify the readback, then restore the original."
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
        return asyncio.run(_run_hardware(args, template, safety))

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
    artifact = dataclasses.replace(artifact, generated_at=_utcnow_iso())
    _emit_artifact(artifact, args)
    return 0


def _emit_artifact(artifact: ValidationArtifact, args: argparse.Namespace) -> None:
    """Render the artifact: file on --output, JSON on --json, else human text."""
    if args.output:
        text = json.dumps(artifact.to_dict(), indent=2)
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"Artifact written to: {args.output}", file=sys.stderr)
    elif args.json:
        print(json.dumps(artifact.to_dict(), indent=2))
    else:
        print(human_summary(artifact))


def _transport_info_from_config(config: Any) -> TransportInfo:
    """Map a backend config into a :class:`TransportInfo` (no device path)."""
    backend = config.backend
    if backend in ("serial", "yaesu-cat"):
        return TransportInfo(backend=backend, baud=getattr(config, "baudrate", None))
    return TransportInfo(
        backend=backend,
        host=getattr(config, "host", None),
        port=getattr(config, "port", None),
    )


def _transport_failure_levels(
    template: MatrixTemplate, exc: BaseException
) -> list[LevelResult]:
    """Build a single DISCOVERY-level FAIL/TRANSPORT result for a connect failure."""
    check = CheckResult(
        check_id="discovery.identify",
        capability="",
        level=ValidationLevel.DISCOVERY,
        status=CheckStatus.FAIL,
        declaration=CapabilityDeclaration.SUPPORTED,
        summary="Radio failed to connect on the configured transport.",
        failure_domain=FailureDomain.TRANSPORT,
        evidence={"error_type": type(exc).__name__},
        error=str(exc),
    )
    return [LevelResult(level=ValidationLevel.DISCOVERY, checks=[check])]


async def _run_hardware(
    args: argparse.Namespace,
    template: MatrixTemplate,
    safety: OperatorSafetyBlock,
) -> int:
    """Connect to the configured radio and execute the validation checks.

    Imports of the CLI backend-config builder, the backend factory, and the
    hardware runner are deferred to function scope to avoid a circular import
    between this module and ``rigplane.cli``.
    """
    from rigplane.backends.factory import create_radio
    from rigplane.cli import _build_backend_config
    from rigplane.core.exceptions import (
        AuthenticationError,
        ConnectionError as RigConnectionError,
        RigplaneError,
    )
    from rigplane.validation.hardware import execute_hardware_checks

    try:
        config = await _build_backend_config(args)
    except ValueError as exc:
        print(f"Error: cannot build backend config: {exc}", file=sys.stderr)
        return 3

    transport = _transport_info_from_config(config)
    radio = create_radio(config)
    exit_code = 0
    try:
        async with radio:
            levels = await execute_hardware_checks(
                radio,
                template,
                safety,
                allow_writes=not bool(getattr(args, "read_only", False)),
                per_check_timeout=getattr(args, "timeout", 5.0) or 5.0,
            )
    except (RigConnectionError, AuthenticationError, OSError, RigplaneError) as exc:
        levels = _transport_failure_levels(template, exc)
        exit_code = 3

    artifact = build_validation_artifact(
        template=template,
        levels=levels,
        transport=transport,
        safety=safety,
        core_version=__version__,
        core_commit=None,
        mode="hardware",
    )
    artifact = dataclasses.replace(artifact, generated_at=_utcnow_iso())
    _emit_artifact(artifact, args)
    return exit_code
