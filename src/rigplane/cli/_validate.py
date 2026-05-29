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

Template resolution: ``--template`` is optional.  If omitted, ``--model`` is
required and the template is generated in-memory from the radio profile's
declared capabilities via ``build_template_from_capabilities``.

Exit codes:

* ``0`` — success (dry-run or hardware artifact emitted). Failed/blocked checks
  do NOT change the exit code.
* ``2`` — template missing, unreadable, or schema-invalid; or ``--template``
  and ``--model`` both absent; or model name is unknown.
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
import tempfile
import time
from pathlib import Path
from typing import Any, cast

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
        required=False,
        default=None,
        help=(
            "Path to a validation matrix template JSON file.  Optional: "
            "if omitted, supply --model to generate the template from the "
            "radio profile's declared capabilities."
        ),
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
        "--provider",
        choices=["native", "hamlib"],
        default="native",
        help=(
            "Validation provider: native (default) drives the radio directly; "
            "hamlib drives it through an internally-spawned Hamlib rigctld and "
            "compares results. Model is auto-detected (or use --model)."
        ),
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
    return p


def run(args: argparse.Namespace) -> int:
    if args.template:
        try:
            template = load_template(Path(args.template))
        except (SchemaValidationError, OSError) as exc:
            print(f"Error: cannot load template: {exc}", file=sys.stderr)
            return 2
    else:
        if not getattr(args, "model", None):
            print("Error: provide --template or --model", file=sys.stderr)
            return 2
        from rigplane.profiles import get_radio_profile
        from rigplane.validation.registry import build_template_from_capabilities

        try:
            profile = get_radio_profile(args.model)
        except KeyError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        template = build_template_from_capabilities(
            profile.capabilities,
            model=profile.model,
            profile_id=profile.id,
        )

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
        if getattr(args, "provider", "native") == "hamlib":
            return asyncio.run(_run_hardware_hamlib(args, template, safety))
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
    artifact = dataclasses.replace(
        artifact,
        metadata={**artifact.metadata, "provider": "native"},
        generated_at=_utcnow_iso(),
    )
    if getattr(args, "compare", None):
        artifact = _attach_comparison(artifact, args.compare)
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
    artifact = dataclasses.replace(
        artifact,
        metadata={**artifact.metadata, "provider": "native"},
        generated_at=_utcnow_iso(),
    )
    if getattr(args, "compare", None):
        artifact = _attach_comparison(artifact, args.compare)
    _emit_artifact(artifact, args)
    return exit_code


async def _await_tcp_ready(host: str, port: int, *, timeout: float = 10.0) -> bool:
    """Poll until a TCP listener accepts connections or *timeout* seconds elapse.

    Returns ``True`` if the port accepted a connection, ``False`` on timeout.
    Used to wait for stock ``rigctld`` to finish its ``rig_open`` sequence and
    bind its front-side TCP port before we attempt to connect the rigctld client.
    """
    deadline = time.monotonic() + timeout
    delay = 0.1
    while time.monotonic() < deadline:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=1.0
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001
                pass
            return True
        except (OSError, asyncio.TimeoutError):
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 1.0)
    return False


def _free_tcp_port() -> int:
    """Return an ephemeral free TCP port on the loopback interface."""
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def _run_hardware_hamlib(
    args: argparse.Namespace,
    template: MatrixTemplate,
    safety: OperatorSafetyBlock,
) -> int:
    """Drive the radio through an internally-spawned Hamlib ``rigctld``.

    A :class:`HamlibBridge` owns the real (RigPlane-controlled) radio and
    proxies raw CI-V to a stock ``rigctld``; the rigctld client backend then
    runs the same hardware checks through Hamlib, so results can be compared to
    the native provider. Imports are deferred to function scope to avoid a
    circular import with ``rigplane.cli`` and to keep ``hamlib_bridge`` /
    backend assembly off this module's import graph (matching ``_run_hardware``).
    """
    from rigplane.backends.config import RigctldBackendConfig
    from rigplane.backends.factory import create_radio
    from rigplane.cli import _build_backend_config
    from rigplane.core.exceptions import (
        AuthenticationError,
        ConnectionError as RigConnectionError,
        RigplaneError,
    )
    from rigplane.hamlib_bridge import HamlibBridge, RawCivPipe
    from rigplane.profiles import get_radio_profile
    from rigplane.validation.hardware import execute_hardware_checks

    try:
        config = await _build_backend_config(args)
    except ValueError as exc:
        print(f"Error: cannot build backend config: {exc}", file=sys.stderr)
        return 3

    transport = _transport_info_from_config(config)
    levels: list[LevelResult]

    # Resolve the radio profile (model is required for the hamlib provider).
    profile = None
    if config.model:
        try:
            profile = get_radio_profile(config.model)
        except KeyError:
            profile = None
    if profile is None:
        levels = _transport_failure_levels(
            template,
            ValueError("Hamlib provider requires a known radio model; use --model"),
        )
        artifact = _build_hamlib_artifact(
            template, levels, transport, safety, profile, None
        )
        if getattr(args, "compare", None):
            artifact = _attach_comparison(artifact, args.compare)
        _emit_artifact(artifact, args)
        return 3

    # The bridge speaks raw CI-V; non-CI-V rigs (Yaesu/Kenwood) cannot be driven.
    if profile.protocol_type != "civ":
        levels = _transport_failure_levels(
            template,
            ValueError(
                f"Hamlib provider supports only CI-V radios; "
                f"{config.model} is {profile.protocol_type}"
            ),
        )
        artifact = _build_hamlib_artifact(
            template, levels, transport, safety, profile, profile.hamlib_model_id
        )
        if getattr(args, "compare", None):
            artifact = _attach_comparison(artifact, args.compare)
        _emit_artifact(artifact, args)
        return 3

    hamlib_model = profile.hamlib_model_id
    civaddr = getattr(config, "radio_addr", None) or profile.civ_addr
    front_port = _free_tcp_port()
    timeout = getattr(args, "timeout", 5.0) or 5.0

    stderr_fd, stderr_path = tempfile.mkstemp(prefix="rigplane-rigctld-", suffix=".log")
    os.close(stderr_fd)

    native_radio = create_radio(config)
    exit_code = 0
    try:
        async with native_radio:
            # CoreRadio-derived Icom backends structurally satisfy the raw
            # CI-V pipe the bridge needs; the Radio protocol doesn't declare it.
            bridge = HamlibBridge(
                cast(RawCivPipe, native_radio),
                model=str(hamlib_model),
                civaddr=civaddr,
                front_port=front_port,
                stderr_path=stderr_path,
            )
            try:
                # start() is inside the try so a partial start (e.g. rigctld
                # missing → spawn_rigctld raises after open_transport bound the
                # listener and began the CAT session) is still torn down by stop().
                await bridge.start()
                ready = await _await_tcp_ready("127.0.0.1", front_port, timeout=10.0)
                if not ready:
                    # Read stderr tail for diagnostics (best-effort).
                    stderr_tail = ""
                    try:
                        raw = Path(stderr_path).read_bytes()
                        if raw:
                            stderr_tail = "\nrigctld stderr tail:\n" + raw[
                                -2048:
                            ].decode(errors="replace")
                    except OSError:
                        pass
                    raise RigConnectionError(
                        f"Hamlib rigctld did not become ready on "
                        f"127.0.0.1:{front_port} within 10s "
                        f"(rig_open may have failed; see {stderr_path})" + stderr_tail
                    )
                hamlib_cfg = RigctldBackendConfig(
                    host="127.0.0.1",
                    port=front_port,
                    timeout=timeout,
                    model=config.model,
                )
                hamlib_radio = create_radio(hamlib_cfg)
                async with hamlib_radio:
                    levels = await execute_hardware_checks(
                        hamlib_radio,
                        template,
                        safety,
                        allow_writes=not bool(getattr(args, "read_only", False)),
                        per_check_timeout=timeout,
                    )
            finally:
                await bridge.stop()
    except (RigConnectionError, AuthenticationError, OSError, RigplaneError) as exc:
        levels = _transport_failure_levels(template, exc)
        exit_code = 3
    else:
        # Clean up the stderr log only on success (leave it for debugging on failure).
        try:
            os.unlink(stderr_path)
        except OSError:
            pass

    artifact = _build_hamlib_artifact(
        template, levels, transport, safety, profile, hamlib_model
    )
    if getattr(args, "compare", None):
        artifact = _attach_comparison(artifact, args.compare)
    _emit_artifact(artifact, args)
    return exit_code


def _build_hamlib_artifact(
    template: MatrixTemplate,
    levels: list[LevelResult],
    transport: TransportInfo,
    safety: OperatorSafetyBlock,
    profile: Any,
    hamlib_model: int | None,
) -> ValidationArtifact:
    """Assemble a hardware artifact stamped with hamlib-provider metadata."""
    artifact = build_validation_artifact(
        template=template,
        levels=levels,
        transport=transport,
        safety=safety,
        core_version=__version__,
        core_commit=None,
        mode="hardware",
    )
    metadata: dict[str, Any] = {**artifact.metadata, "provider": "hamlib"}
    if hamlib_model is not None:
        metadata["hamlib_model_id"] = hamlib_model
    if profile is not None:
        metadata["profile_id"] = profile.id
    return dataclasses.replace(artifact, metadata=metadata, generated_at=_utcnow_iso())


def _check_status_map(artifact: ValidationArtifact) -> dict[str, str]:
    """Map ``check_id`` → status value across all levels of *artifact*."""
    statuses: dict[str, str] = {}
    for level in artifact.levels:
        for check in level.checks:
            statuses[check.check_id] = check.status.value
    return statuses


def _compare_artifacts(this: ValidationArtifact, other_path: str) -> dict[str, Any]:
    """Diff per-check status between *this* artifact and one loaded from disk."""
    from rigplane.validation.schema import validate_artifact_dict

    other = validate_artifact_dict(
        json.loads(Path(other_path).read_text(encoding="utf-8"))
    )
    a = _check_status_map(this)
    b = _check_status_map(other)
    rows = [
        {
            "check_id": k,
            "this": a.get(k),
            "other": b.get(k),
            "agree": a.get(k) == b.get(k),
        }
        for k in sorted(set(a) | set(b))
    ]
    return {"other_provider": other.metadata.get("provider"), "rows": rows}


def _attach_comparison(
    artifact: ValidationArtifact, other_path: str
) -> ValidationArtifact:
    """Fold a per-check comparison into metadata and print a compact table."""
    comp = _compare_artifacts(artifact, other_path)
    print("check_id | this | other | verdict", file=sys.stderr)
    for row in comp["rows"]:
        verdict = "AGREE" if row["agree"] else "DIFFER"
        print(
            f"{row['check_id']} | {row['this']} | {row['other']} | {verdict}",
            file=sys.stderr,
        )
    return dataclasses.replace(
        artifact, metadata={**artifact.metadata, "comparison": comp}
    )
