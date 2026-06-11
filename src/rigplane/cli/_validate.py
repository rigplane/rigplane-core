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
* ``1`` — ``--gate`` detected a regression against the golden artifact.
* ``2`` — template missing, unreadable, or schema-invalid; or ``--template``
  and ``--model`` both absent; or model name is unknown; or the ``--gate``
  golden file is missing/unreadable.
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
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from rigplane.backends.hamlib_models import HamlibCaps

from rigplane import __version__
from rigplane.validation import (
    HARDWARE_OPT_IN_ENV,
    InteractivePrompter,
    MatrixTemplate,
    OperatorSafetyBlock,
    TransportInfo,
    ValidationArtifact,
    build_validation_artifact,
    compute_comparison_dimensions,
    dry_run_results,
    format_gate_report,
    gate_artifacts,
    human_summary,
    load_template,
    normalize_artifact,
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


def _overrides_dir() -> Path:
    """Return the path to the per-profile override directory (ADR §4.1).

    Override files live at ``docs/validation/templates/<profile_id>.json``.
    Mirrors the dev/installed resolution used by ``cli.__init__._rigs_dir``:
    prefer a copy shipped next to the package, else resolve relative to the
    repo root in a development checkout.
    """
    # Installed layout: rigplane/docs/validation/templates/ next to the package.
    # (this file is rigplane/cli/_validate.py → up two levels to rigplane/)
    pkg_dir = (
        Path(__file__).resolve().parent.parent / "docs" / "validation" / "templates"
    )
    if pkg_dir.is_dir():
        return pkg_dir
    # Development layout: repo_root/docs/validation/templates/
    # (parents[3] from src/rigplane/cli/_validate.py = repo root)
    return Path(__file__).resolve().parents[3] / "docs" / "validation" / "templates"


def _apply_overrides(
    template: MatrixTemplate, profile_id: str
) -> tuple[MatrixTemplate, dict[str, Any] | None]:
    """Auto-apply a per-profile override FILE onto a generated *template*.

    Returns ``(possibly-merged template, audit dict or None)``. The audit dict,
    when present, mirrors the :class:`~rigplane.validation.MergeReport`:
    ``{"applied": [...], "appended": [...], "excluded": [...], "rejected": [...]}``.

    Resolution and policy (ADR §4.1):

    * No ``<profile_id>.json`` in the override dir → ``(template, None)``: a rig
      with no override file still gets the full generated matrix.
    * The file lacks a top-level ``"override": true`` → ``(template, None)``: it
      is a FULL legacy template, not a sparse patch, and is not auto-applied.
    * Otherwise the file is parsed and merged via the pure override layer.

    Never raises: a malformed or unreadable override file degrades to a stderr
    warning and ``(template, None)`` so a broken override can never block
    validation. The pure :func:`merge_overrides` enforces the tx/tuner safety
    invariant, so unsafe relaxations are refused and recorded in ``rejected``.
    """
    path = _overrides_dir() / f"{profile_id}.json"
    if not path.is_file():
        return template, None

    try:
        from rigplane.validation import merge_overrides, parse_override_dict

        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("override") is not True:
            # Full template, not a sparse patch — do not auto-apply.
            return template, None
        patch = parse_override_dict(data)
        merged, report = merge_overrides(template, patch)
    except (OSError, ValueError, SchemaValidationError) as exc:
        print(
            f"Warning: ignoring override file {path}: {exc}",
            file=sys.stderr,
        )
        return template, None

    audit = {
        "applied": list(report.applied),
        "appended": list(report.appended),
        "excluded": list(report.excluded),
        "rejected": list(report.rejected),
    }
    return merged, audit


def _hamlib_caps_to_tokens(caps: HamlibCaps) -> frozenset[str]:
    """Flatten a HamlibCaps into the set of registry hamlib_token strings the
    model supports, for Generator B."""
    tokens: set[str] = set()
    tokens |= caps.get_levels | caps.set_levels | caps.get_funcs | caps.set_funcs
    if caps.has_set_freq:
        tokens.add("f")
    if caps.modes:
        tokens.add("m")
    if caps.ptt_type is not None:
        tokens.add("t")
    return frozenset(tokens)


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


def _build_prompter(args: argparse.Namespace) -> InteractivePrompter | None:
    """Construct an :class:`InteractivePrompter` for ``--interactive`` runs.

    Returns ``None`` (→ unchanged MANUAL_REQUIRED behaviour) unless ``--interactive``
    is set AND stdin is a real TTY. The TTY guard is the no-hang safeguard
    (MOR-667): a CI/piped run must never block on ``input()``, so without a TTY
    the perception checks stay MANUAL_REQUIRED. ``--assume-yes`` only auto-answers
    perception prompts; it never satisfies the prompter's ``confirm`` gate (used by
    the MOR-666 TX prompt), which always reads a real answer.
    """
    if not getattr(args, "interactive", False):
        return None
    if not sys.stdin.isatty():
        print(
            "Warning: --interactive ignored (stdin is not a TTY); "
            "manual-perception checks stay MANUAL_REQUIRED.",
            file=sys.stderr,
        )
        return None
    return InteractivePrompter(assume_yes=bool(getattr(args, "assume_yes", False)))


def _tx_actuate_enabled(args: argparse.Namespace) -> bool:
    """Whether the TX checks may ACTUALLY transmit (MOR-666).

    Returns ``True`` only when EVERY gate in the opt-in stack is open:
    ``--tx-actuate`` AND ``--tx-allowed`` AND ``--allow-hardware`` AND
    ``RIGPLANE_VALIDATION_ALLOW_HARDWARE=1`` AND ``--hardware`` (running on real
    hardware). Missing ANY gate → ``False`` → today's behaviour (the TX checks
    stay MANUAL_REQUIRED/BLOCKED and never key the transmitter). This is only the
    *static* gate stack; the actuating handlers additionally require an explicit
    interactive ``confirm()`` YES at runtime before any transmission, so even an
    all-gates-open unattended run cannot transmit.
    """
    return (
        bool(getattr(args, "tx_actuate", False))
        and bool(getattr(args, "tx_allowed", False))
        and bool(getattr(args, "allow_hardware", False))
        and bool(getattr(args, "hardware", False))
        and os.environ.get(HARDWARE_OPT_IN_ENV) == "1"
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
        "--tx-actuate",
        dest="tx_actuate",
        action="store_true",
        help=(
            "ACTUALLY exercise the TX checks (tx.ptt keys PTT at MINIMUM power "
            "for ~1s then unkeys/restores; tuner.tune runs a tune-cycle). "
            "Quadruple-gated: also requires --tx-allowed, --allow-hardware, "
            f"{HARDWARE_OPT_IN_ENV}=1, and --hardware, PLUS an explicit "
            "interactive confirm YES at runtime. Missing any → MANUAL_REQUIRED "
            "(no transmission). VOX stays manual."
        ),
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
        choices=["native", "hamlib", "both", "hamlib-external"],
        default="native",
        help=(
            "Validation provider: native (default) drives the radio directly; "
            "hamlib drives it through an internally-spawned Hamlib rigctld and "
            "compares results; both runs native then hamlib sequentially and "
            "attaches cross-implementation comparison dimensions to the primary "
            "(native) artifact. hamlib-external runs the matrix against an "
            "ARBITRARY external rigctld already listening (any Hamlib rig) via "
            "--rigctld-host/--rigctld-port; no RigPlane profile required. "
            "Model is auto-detected (or use --model)."
        ),
    )
    p.add_argument(
        "--rigctld-host",
        dest="rigctld_host",
        default="127.0.0.1",
        help=(
            "Host of an external Hamlib rigctld to validate against "
            "(--provider hamlib-external). Default 127.0.0.1."
        ),
    )
    p.add_argument(
        "--rigctld-port",
        dest="rigctld_port",
        type=int,
        default=4532,
        help=(
            "TCP port of the external Hamlib rigctld "
            "(--provider hamlib-external). Default 4532."
        ),
    )
    p.add_argument(
        "--rigctld-model",
        dest="rigctld_model",
        default=None,
        help=(
            "Optional human label for the external rig, recorded in the "
            "artifact (--provider hamlib-external). Default 'External rigctld'."
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
        "--gate",
        default=None,
        metavar="GOLDEN",
        help=(
            "Gate this run against a golden normalized artifact JSON: print a "
            "diff summary and exit 1 on any regression (a check that went "
            "pass->fail, a new fail/blocked check, a missing check, or "
            "declaration drift). Improvements never block."
        ),
    )
    p.add_argument(
        "--regen-golden",
        dest="regen_golden",
        default=None,
        metavar="PATH",
        help=(
            "Write the NORMALIZED artifact (volatile fields stripped) to PATH "
            "for use as a --gate golden baseline. Parent directories are "
            "created as needed."
        ),
    )
    p.add_argument(
        "--no-overrides",
        dest="no_overrides",
        action="store_true",
        help=(
            "Skip auto-applying the per-profile override file "
            "(docs/validation/templates/<profile_id>.json). Escape hatch for "
            "deterministic CI runs. No effect when --template is given."
        ),
    )
    p.add_argument(
        "--interactive",
        action="store_true",
        help=(
            "Prompt the operator for the manual-perception checks "
            "(audio.rx, scope.capture, bsr.select) and record their y/n answer "
            "as PASS/FAIL instead of MANUAL_REQUIRED. Requires a TTY on stdin; "
            "with no TTY (CI/piped) these stay MANUAL_REQUIRED and never block."
        ),
    )
    p.add_argument(
        "--assume-yes",
        dest="assume_yes",
        action="store_true",
        help=(
            "With --interactive, auto-answer YES to non-TX perception prompts "
            "for unattended runs. Does NOT apply to any TX-transmit gate, which "
            "always requires a real interactive YES."
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


def _generate_template(
    args: argparse.Namespace, profile: Any, provider: str
) -> MatrixTemplate:
    """Build a per-provider in-memory template from *profile*.

    *provider* must be ``"native"`` (Gen-A) or ``"hamlib"`` (Gen-B).
    The caller is responsible for resolving the profile before calling this.
    """
    if provider == "hamlib":
        from rigplane.backends.hamlib_models import load_hamlib_caps
        from rigplane.validation.registry import build_hamlib_template_from_capabilities

        caps = load_hamlib_caps(profile.hamlib_model_id)
        if caps.degraded_reason:
            print(
                f"Warning: Hamlib dump_caps unavailable for model "
                f"{profile.hamlib_model_id} ({caps.degraded_reason}); "
                f"all checks will be N/A.",
                file=sys.stderr,
            )
        tokens = _hamlib_caps_to_tokens(caps)
        return build_hamlib_template_from_capabilities(
            profile.capabilities,
            tokens,
            model=profile.model,
            profile_id=profile.id,
        )
    # native (Gen-A)
    from rigplane.validation.registry import build_template_from_capabilities

    return build_template_from_capabilities(
        profile.capabilities,
        model=profile.model,
        profile_id=profile.id,
    )


def _registry_capabilities() -> frozenset[str]:
    """Return the full set of functional capabilities the registry can exercise.

    Used by the ``hamlib-external`` provider to build a profile-free upfront
    template (for the dry-run and safety-block paths). The hardware path
    rebuilds the template from the connected rig's advertised capabilities.
    """
    from rigplane.validation.registry import REGISTRY

    return frozenset(spec.capability for spec in REGISTRY if spec.capability)


def run(args: argparse.Namespace) -> int:
    provider = getattr(args, "provider", "native")

    if args.template:
        try:
            template = load_template(Path(args.template))
        except (SchemaValidationError, OSError) as exc:
            print(f"Error: cannot load template: {exc}", file=sys.stderr)
            return 2
        profile = None
    elif provider == "hamlib-external":
        # Arbitrary external rig: no RigPlane profile required. Build a
        # profile-free upfront template from the full registry capability set
        # so dry-run / safety paths work; the hardware path rebuilds the
        # template from the connected rig's advertised capabilities.
        from rigplane.validation.registry import build_template_from_capabilities

        model_label = getattr(args, "rigctld_model", None) or "External rigctld"
        template = build_template_from_capabilities(
            _registry_capabilities(),
            model=model_label,
            profile_id="hamlib_external",
        )
        profile = None
        args._overrides_audit = None
    else:
        if not getattr(args, "model", None):
            print("Error: provide --template or --model", file=sys.stderr)
            return 2
        from rigplane.profiles import get_radio_profile

        try:
            profile = get_radio_profile(args.model)
        except KeyError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 2
        if provider in ("hamlib", "native"):
            template = _generate_template(args, profile, provider)
        else:
            # "both": will build per-provider templates below; use native for
            # dry-run / safety-block path.
            template = _generate_template(args, profile, "native")
        # Auto-apply the per-profile override file (single chokepoint). The
        # "both" path re-merges per provider inside _run_hardware_both; for the
        # single-provider + dry-run paths this is the merge that executes.
        overrides_audit: dict[str, Any] | None = None
        if not getattr(args, "no_overrides", False):
            template, overrides_audit = _apply_overrides(template, profile.id)
        args._overrides_audit = overrides_audit

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

        if provider == "both":
            return asyncio.run(_run_hardware_both(args, profile, safety))

        if provider == "hamlib-external":
            rc, artifact = asyncio.run(_run_hardware_hamlib_external(args, safety))
        elif provider == "hamlib":
            rc, artifact = asyncio.run(_run_hardware_hamlib(args, template, safety))
        else:
            rc, artifact = asyncio.run(_run_hardware(args, template, safety))
        artifact = _stamp_overrides(artifact, getattr(args, "_overrides_audit", None))
        if getattr(args, "compare", None):
            artifact = _attach_comparison(artifact, args.compare)
        _emit_artifact(artifact, args)
        return _apply_golden_flags(artifact, args, rc)

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
    artifact = _stamp_overrides(artifact, getattr(args, "_overrides_audit", None))
    if getattr(args, "compare", None):
        artifact = _attach_comparison(artifact, args.compare)
    _emit_artifact(artifact, args)
    return _apply_golden_flags(artifact, args, 0)


async def _run_hardware_both(
    args: argparse.Namespace,
    profile: Any,
    safety: OperatorSafetyBlock,
) -> int:
    """Run native then hamlib sequentially, attach comparison dims to native artifact."""
    if getattr(args, "compare", None):
        print(
            "Warning: --compare is ignored under --provider both",
            file=sys.stderr,
        )

    tmpl_native = _generate_template(args, profile, "native")
    tmpl_hamlib = _generate_template(args, profile, "hamlib")

    # Auto-apply the per-profile override file to BOTH provider templates so
    # the merged matrices execute and the (native) audit reaches the artifact.
    overrides_audit: dict[str, Any] | None = None
    if not getattr(args, "no_overrides", False):
        tmpl_native, overrides_audit = _apply_overrides(tmpl_native, profile.id)
        tmpl_hamlib, _ = _apply_overrides(tmpl_hamlib, profile.id)

    # Sequential: native first, then hamlib (serial port must release between).
    rc_n, art_n = await _run_hardware(args, tmpl_native, safety)
    rc_h, art_h = await _run_hardware_hamlib(args, tmpl_hamlib, safety)

    dims = compute_comparison_dimensions(art_n.to_dict(), art_h.to_dict())

    # Build per-check rows from both status maps.
    map_n = _check_status_map(art_n)
    map_h = _check_status_map(art_h)
    all_ids = sorted(set(map_n) | set(map_h))
    rows = [
        {
            "check_id": cid,
            "this": map_n.get(cid),
            "other": map_h.get(cid),
            "agree": map_n.get(cid) == map_h.get(cid),
        }
        for cid in all_ids
    ]

    comparison: dict[str, Any] = {
        "other_provider": "hamlib",
        "rows": rows,
        "dimensions": dims,
    }
    metadata: dict[str, Any] = {
        **art_n.metadata,
        "generated_from": "profile",
        "comparison": comparison,
    }
    if overrides_audit is not None:
        metadata["overrides"] = overrides_audit
    art_n = dataclasses.replace(art_n, metadata=metadata)
    _emit_artifact(art_n, args)
    return _apply_golden_flags(art_n, args, rc_n if rc_n else rc_h)


def _json_default(obj: object) -> object:
    """Last-resort JSON coercion so artifact emission can never crash.

    ``CheckResult.to_dict`` already coerces evidence to JSON-safe types; this
    is belt-and-suspenders for any unforeseen object that still reaches
    ``json.dumps`` (mirrors the "always produce output" principle): a
    dataclass becomes its ``asdict``, anything else its ``str``.
    """
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return dataclasses.asdict(obj)
    return str(obj)


def _apply_golden_flags(
    artifact: ValidationArtifact, args: argparse.Namespace, exit_code: int
) -> int:
    """Apply ``--regen-golden`` and ``--gate`` after the artifact is emitted.

    ``--regen-golden`` writes the normalized artifact to the given path (the
    golden baseline format). ``--gate`` normalizes both sides (the golden may
    be stored normalized already — the projection is idempotent), prints a
    concise diff summary to stderr, and elevates a successful run to exit code
    ``1`` on any regression. Existing non-zero exit codes are preserved; a
    missing/unreadable golden is ``2``.
    """
    regen_path = getattr(args, "regen_golden", None)
    if regen_path:
        normalized = normalize_artifact(artifact.to_dict())
        path = Path(regen_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(normalized, indent=2, default=_json_default) + "\n",
            encoding="utf-8",
        )
        print(f"Golden written to: {regen_path}", file=sys.stderr)

    gate_path = getattr(args, "gate", None)
    if not gate_path:
        return exit_code
    try:
        golden_obj = json.loads(Path(gate_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"Error: cannot load golden artifact: {exc}", file=sys.stderr)
        return 2
    if not isinstance(golden_obj, dict):
        print("Error: cannot load golden artifact: not a JSON object", file=sys.stderr)
        return 2
    report = gate_artifacts(
        normalize_artifact(artifact.to_dict()), normalize_artifact(golden_obj)
    )
    print(format_gate_report(report, golden_path=str(gate_path)), file=sys.stderr)
    if not report.ok and exit_code == 0:
        return 1
    return exit_code


def _stamp_overrides(
    artifact: ValidationArtifact, audit: dict[str, Any] | None
) -> ValidationArtifact:
    """Fold the override audit into ``metadata["overrides"]`` when non-None."""
    if audit is None:
        return artifact
    return dataclasses.replace(
        artifact, metadata={**artifact.metadata, "overrides": audit}
    )


def _emit_artifact(artifact: ValidationArtifact, args: argparse.Namespace) -> None:
    """Render the artifact: file on --output, JSON on --json, else human text."""
    if args.output:
        text = json.dumps(artifact.to_dict(), indent=2, default=_json_default)
        Path(args.output).write_text(text + "\n", encoding="utf-8")
        print(f"Artifact written to: {args.output}", file=sys.stderr)
    elif args.json:
        print(json.dumps(artifact.to_dict(), indent=2, default=_json_default))
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


# MOR-668 — live RX-audio probe capture parameters. A short RX-only window is
# captured once per hardware run and fed to the audio.rx.rms / scope.fft.presence
# probes. RX-only: this never opens a TX path and never keys the transmitter.
#
# Capture goes through the radio-owned ``AudioSession`` (``radio.audio_session``)
# — the SAME codec-negotiated RX path the web server/bridge use — NOT the
# Opus-only ``start_audio_rx_pcm`` decoder. Direct IC-7610 LAN audio is
# PCM-first (``rigs/ic7610.toml`` ``codec_preference`` = PCM_*; Opus is only a
# browser transcode), so the Opus decoder rejected every on-wire PCM frame and
# the probe saw ZERO frames. Subscribing through the session yields the radio's
# negotiated on-wire payload, which we decode to s16le PCM here (PCM pass-through,
# uLaw → PCM16) exactly like the web relay loop.
# Number of decoded PCM frames to collect (each ~20 ms → ~0.5 s of audio).
_AUDIO_PROBE_TARGET_FRAMES = 25
# Hard wall-clock ceiling so a silent/dead RX path can never hang the run.
_AUDIO_PROBE_CAPTURE_TIMEOUT = 3.0


def _decode_probe_frame(data: bytes, codec: Any) -> bytes:
    """Decode one on-wire RX payload to s16le PCM for the probe (MOR-668).

    Mirrors the web relay loop's RX decode policy: PCM payloads pass through
    untouched, uLaw is expanded to PCM16, and any other / unknown codec (incl.
    Opus, which is never on the wire for the PCM-first direct-LAN path the probe
    targets) is left as-is — the downstream RMS/FFT check then assesses it.
    """
    from rigplane.audio._codecs import decode_ulaw_to_pcm16
    from rigplane.core.types import AudioCodec

    if codec in (AudioCodec.ULAW_1CH, AudioCodec.ULAW_2CH):
        try:
            return decode_ulaw_to_pcm16(data)
        except Exception:  # noqa: BLE001 — fall back to raw on decode failure.
            return data
    return data


async def _capture_rx_audio_probe(radio: Any) -> list[bytes | None] | None:
    """Capture a short live RX-audio window via the AudioSession (MOR-668).

    Subscribes RX demand through the radio-owned :class:`AudioSession`
    (``radio.audio_session``) — the codec-negotiated path the web server and
    bridge use — and collects decoded s16le PCM frames. This is what makes the
    probe work on direct IC-7610 LAN audio, which is PCM-first: the previous
    Opus-only ``start_audio_rx_pcm`` decoder rejected every on-wire PCM frame
    and captured ZERO frames.

    Returns the captured list of decoded PCM frames (``None`` entries are jitter
    gap placeholders, preserved for the probe's evidence) on success, or ``None``
    when the radio is not :class:`AudioCapable` OR exposes no ``audio_session``
    (the caller then leaves the audio probes MANUAL_REQUIRED). RX-only — never
    opens a TX path, never transmits.

    The RX subscription is ALWAYS released in a ``finally`` even if capture
    raises or times out, so no stream/task leaks. Any capture error degrades to
    an EMPTY captured window (``[]``), which the probes turn into a clean FAIL
    with the reason in evidence rather than aborting the run.
    """
    from rigplane.core.radio_protocol import AudioCapable

    if not isinstance(radio, AudioCapable):
        return None

    session = getattr(radio, "audio_session", None)
    if session is None:
        # No codec-negotiated session for this radio → leave probes MANUAL.
        return None

    frames: list[bytes | None] = []
    codec = getattr(radio, "audio_codec", None)
    loop = asyncio.get_running_loop()
    deadline = loop.time() + _AUDIO_PROBE_CAPTURE_TIMEOUT
    subscription = None
    try:
        # RX demand through the session arms the radio's negotiated RX leg.
        subscription = await session.subscribe_rx("validate-rx-probe")
        while len(frames) < _AUDIO_PROBE_TARGET_FRAMES:
            remaining = deadline - loop.time()
            if remaining <= 0:
                break
            packet = await subscription.get(timeout=remaining)
            if packet is None:
                # Jitter gap placeholder — preserved for the probe's evidence.
                frames.append(None)
                continue
            frames.append(_decode_probe_frame(packet.data, codec))
    except (asyncio.TimeoutError, asyncio.CancelledError):
        # Partial (or empty) capture: the probes assess whatever arrived.
        pass
    except Exception as exc:  # noqa: BLE001 — any capture error → clean probe FAIL.
        print(
            f"Warning: live RX-audio probe capture failed: {exc}",
            file=sys.stderr,
        )
    finally:
        if subscription is not None:
            try:
                await subscription.release()
            except Exception as exc:  # noqa: BLE001 — teardown must never raise.
                print(
                    f"Warning: live RX-audio probe teardown failed: {exc}",
                    file=sys.stderr,
                )
    return frames


async def _run_hardware(
    args: argparse.Namespace,
    template: MatrixTemplate,
    safety: OperatorSafetyBlock,
) -> tuple[int, ValidationArtifact]:
    """Connect to the configured radio and execute the validation checks.

    Returns ``(exit_code, artifact)``; the caller is responsible for attaching
    comparison metadata and emitting the artifact.  Imports of the CLI
    backend-config builder, the backend factory, and the hardware runner are
    deferred to function scope to avoid a circular import between this module
    and ``rigplane.cli``.
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
        # Build a minimal failure artifact so callers always get a tuple.
        transport = TransportInfo(backend="fixture")
        levels = _transport_failure_levels(template, exc)
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
        return 3, artifact

    # Resolve the radio profile for per-radio validation classification
    # (write-only controls, MOR-208). Best-effort: unknown model → no profile.
    write_only_capabilities: frozenset[str] = frozenset()
    if config.model:
        from rigplane.profiles import get_radio_profile

        try:
            profile = get_radio_profile(config.model)
        except KeyError:
            profile = None
        if profile is not None:
            write_only_capabilities = profile.write_only_controls

    transport = _transport_info_from_config(config)
    radio = create_radio(config)
    exit_code = 0
    try:
        async with radio:
            # MOR-668: capture a short live RX-audio window so the audio.rx.rms /
            # scope.fft.presence probes run for real on hardware. RX-only; the
            # session is always torn down inside the helper's finally. A
            # non-AudioCapable radio returns None → probes stay MANUAL_REQUIRED.
            audio_probe_frames = await _capture_rx_audio_probe(radio)
            levels = await execute_hardware_checks(
                radio,
                template,
                safety,
                allow_writes=not bool(getattr(args, "read_only", False)),
                per_check_timeout=getattr(args, "timeout", 5.0) or 5.0,
                write_only_capabilities=write_only_capabilities,
                prompter=_build_prompter(args),
                tx_actuate=_tx_actuate_enabled(args),
                audio_probe_frames=audio_probe_frames,
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
    return exit_code, artifact


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
) -> tuple[int, ValidationArtifact]:
    """Drive the radio through an internally-spawned Hamlib ``rigctld``.

    Returns ``(exit_code, artifact)``; the caller is responsible for attaching
    comparison metadata and emitting the artifact.  A :class:`HamlibBridge`
    owns the real (RigPlane-controlled) radio and proxies raw CI-V to a stock
    ``rigctld``; the rigctld client backend then runs the same hardware checks
    through Hamlib, so results can be compared to the native provider. Imports
    are deferred to function scope to avoid a circular import with
    ``rigplane.cli`` and to keep ``hamlib_bridge`` / backend assembly off this
    module's import graph (matching ``_run_hardware``).
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
        transport = TransportInfo(backend="fixture")
        levels = _transport_failure_levels(template, exc)
        artifact = _build_hamlib_artifact(
            template, levels, transport, safety, None, None
        )
        return 3, artifact

    transport = _transport_info_from_config(config)

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
        return 3, artifact

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
        return 3, artifact

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
                        write_only_capabilities=(
                            profile.write_only_controls if profile else frozenset()
                        ),
                        prompter=_build_prompter(args),
                        tx_actuate=_tx_actuate_enabled(args),
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
    return exit_code, artifact


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


async def _run_hardware_hamlib_external(
    args: argparse.Namespace,
    safety: OperatorSafetyBlock,
) -> tuple[int, ValidationArtifact]:
    """Run the matrix against an ARBITRARY external Hamlib ``rigctld``.

    Unlike the ``hamlib`` provider (which spawns its own rigctld over a
    RigPlane-owned radio), this connects :class:`RigctldClientRadio` directly
    to a rigctld already listening on ``--rigctld-host:--rigctld-port`` — any
    Hamlib-supported rig, no RigPlane profile required (goal 2 of the harness).

    The template is rebuilt from the connected rig's advertised capabilities so
    only the controls the backend actually implements are exercised; every
    other registry check resolves to ``unsupported`` cleanly (the hardware
    runner already maps a missing op to UNSUPPORTED, never crashing). Imports
    are deferred to function scope to keep backend assembly off this module's
    import graph (matching ``_run_hardware``).
    """
    from rigplane.backends.config import RigctldBackendConfig
    from rigplane.backends.factory import create_radio
    from rigplane.core.exceptions import (
        AuthenticationError,
        ConnectionError as RigConnectionError,
        RigplaneError,
    )
    from rigplane.validation.hardware import execute_hardware_checks
    from rigplane.validation.registry import build_template_from_capabilities

    host = getattr(args, "rigctld_host", None) or "127.0.0.1"
    port = int(getattr(args, "rigctld_port", None) or 4532)
    model_label = getattr(args, "rigctld_model", None) or None
    timeout = getattr(args, "timeout", 5.0) or 5.0

    try:
        config = RigctldBackendConfig(
            host=host, port=port, timeout=timeout, model=model_label
        )
    except ValueError as exc:
        print(f"Error: cannot build backend config: {exc}", file=sys.stderr)
        placeholder = build_template_from_capabilities(
            _registry_capabilities(),
            model=model_label or "External rigctld",
            profile_id="hamlib_external",
        )
        transport = TransportInfo(backend="rigctld")
        levels = _transport_failure_levels(placeholder, exc)
        return 3, _build_hamlib_external_artifact(
            placeholder, levels, transport, safety
        )

    transport = _transport_info_from_config(config)
    radio = create_radio(config)
    exit_code = 0
    try:
        async with radio:
            # Rebuild the template from the rig's LIVE advertised capabilities
            # (includes the post-connect VFO probe); no profile needed.
            template = build_template_from_capabilities(
                frozenset(radio.capabilities),
                model=radio.model,
                profile_id="hamlib_external",
            )
            levels = await execute_hardware_checks(
                radio,
                template,
                safety,
                allow_writes=not bool(getattr(args, "read_only", False)),
                per_check_timeout=timeout,
                prompter=_build_prompter(args),
                tx_actuate=_tx_actuate_enabled(args),
            )
    except (RigConnectionError, AuthenticationError, OSError, RigplaneError) as exc:
        template = build_template_from_capabilities(
            _registry_capabilities(),
            model=model_label or "External rigctld",
            profile_id="hamlib_external",
        )
        levels = _transport_failure_levels(template, exc)
        exit_code = 3

    return exit_code, _build_hamlib_external_artifact(
        template, levels, transport, safety
    )


def _build_hamlib_external_artifact(
    template: MatrixTemplate,
    levels: list[LevelResult],
    transport: TransportInfo,
    safety: OperatorSafetyBlock,
) -> ValidationArtifact:
    """Assemble a hardware artifact stamped with hamlib-external metadata."""
    artifact = build_validation_artifact(
        template=template,
        levels=levels,
        transport=transport,
        safety=safety,
        core_version=__version__,
        core_commit=None,
        mode="hardware",
    )
    return dataclasses.replace(
        artifact,
        metadata={**artifact.metadata, "provider": "hamlib-external"},
        generated_at=_utcnow_iso(),
    )


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
