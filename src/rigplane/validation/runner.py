"""Validation matrix runner helpers (dry-run + artifact assembly).

This PR ships only the dry-run path: it maps a planned ``MatrixTemplate`` into
``CheckResult`` skeletons, gating TX-adjacent and tuner checks behind explicit
operator authorization. Hardware execution is intentionally out of scope and
guarded by ``HARDWARE_OPT_IN_ENV`` plus a CLI refusal.
"""

from __future__ import annotations

import json
from pathlib import Path

from rigplane.validation.registry import CheckKind, get_spec
from rigplane.validation.schema import (
    CapabilityDeclaration,
    CapabilityDeclarationEntry,
    CheckResult,
    CheckStatus,
    FailureDomain,
    LevelResult,
    MatrixTemplate,
    OperatorSafetyBlock,
    TransportInfo,
    ValidationArtifact,
    ValidationLevel,
    validate_template_dict,
)

HARDWARE_OPT_IN_ENV = "RIGPLANE_VALIDATION_ALLOW_HARDWARE"

# Capability tags whose checks require explicit operator authorization.
_TUNER_CAPABILITY = "tuner"
_TX_CAPABILITY = "tx"


class HardwareExecutionBlocked(RuntimeError):
    """Raised when a hardware validation run is attempted without opt-in."""


_DECLARATION_TO_STATUS: dict[CapabilityDeclaration, CheckStatus] = {
    CapabilityDeclaration.SUPPORTED: CheckStatus.SKIP,
    CapabilityDeclaration.UNSUPPORTED_PENDING_EVIDENCE: CheckStatus.UNSUPPORTED,
    CapabilityDeclaration.MANUAL_REQUIRED: CheckStatus.MANUAL_REQUIRED,
}


def load_template(path: Path) -> MatrixTemplate:
    """Read and validate a template JSON file.

    Propagates ``OSError`` on read failure and ``SchemaValidationError`` on bad
    content.
    """
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    return validate_template_dict(data)


def _registry_gated_capability(entry: CapabilityDeclarationEntry) -> str | None:
    """Return the IMMUTABLE registry capability for ``entry.check_id`` when its
    registry ``CheckSpec`` is ``TX_ADJACENT_BLOCKED``; else ``None``. This is the
    authoritative safety class — it cannot be relaxed by mutating the template's
    ``tx_adjacent`` flag."""
    spec = get_spec(entry.check_id)
    if spec is not None and spec.kind == CheckKind.TX_ADJACENT_BLOCKED:
        return spec.capability
    return None


def _is_safety_gated(entry: CapabilityDeclarationEntry) -> bool:
    """True if the entry requires operator authorization — by the template flag
    OR (defense-in-depth) by the registry safety class."""
    return entry.tx_adjacent or _registry_gated_capability(entry) is not None


def _is_authorized(
    entry: CapabilityDeclarationEntry, safety: OperatorSafetyBlock
) -> bool:
    """Return True when the operator is authorized to run ``entry``.

    Fail-closed: any safety-gated entry is blocked by default.

    - Entries that are neither ``tx_adjacent`` nor registry-gated are always
      authorized.
    - The capability used for the tuner/tx split is the IMMUTABLE registry
      capability whenever the check is registry-gated (``TX_ADJACENT_BLOCKED``);
      a mutated ``entry.capability`` cannot dodge the split. Only when the entry
      is gated solely by the template flag do we fall back to ``entry.capability``.
    - Tuner capability is gated solely by ``tuner_allowed`` (independent of
      ``tx_allowed``), because the tuner keys TX into the ATU and has its own
      operator gate.
    - ALL other safety-gated entries require ``tx_allowed``. There is no
      residual fail-open path.
    """
    gated_cap = _registry_gated_capability(entry)
    if not entry.tx_adjacent and gated_cap is None:
        return True
    capability = gated_cap if gated_cap is not None else entry.capability
    if capability == _TUNER_CAPABILITY:
        return safety.tuner_allowed
    return safety.tx_allowed


def dry_run_results(
    template: MatrixTemplate, safety: OperatorSafetyBlock
) -> list[LevelResult]:
    """Map a template into per-level dry-run ``CheckResult`` skeletons.

    Each entry's declaration maps to a base status; TX-adjacent entries that
    are not authorized are overridden to ``BLOCKED`` with a
    ``command_execution`` failure domain. Empty levels are omitted; levels are
    returned in ascending order.
    """
    by_level: dict[ValidationLevel, list[CheckResult]] = {}
    for entry in template.entries:
        status = _DECLARATION_TO_STATUS[entry.declaration]
        failure_domain: FailureDomain | None = None
        if _is_safety_gated(entry) and not _is_authorized(entry, safety):
            status = CheckStatus.BLOCKED
            failure_domain = FailureDomain.COMMAND_EXECUTION
        result = CheckResult(
            check_id=entry.check_id,
            capability=entry.capability,
            level=entry.level,
            status=status,
            declaration=entry.declaration,
            summary=entry.summary,
            failure_domain=failure_domain,
        )
        by_level.setdefault(entry.level, []).append(result)

    return [
        LevelResult(level=level, checks=by_level[level]) for level in sorted(by_level)
    ]


def summarize_results(levels: list[LevelResult]) -> dict[str, int]:
    """Count checks by status across all levels, zero-filling every status."""
    summary = {status.value: 0 for status in CheckStatus}
    for level in levels:
        for check in level.checks:
            summary[check.status.value] += 1
    return summary


def build_validation_artifact(
    *,
    template: MatrixTemplate,
    levels: list[LevelResult],
    transport: TransportInfo,
    safety: OperatorSafetyBlock,
    core_version: str,
    core_commit: str | None = None,
    logs_path: str | None = None,
    mode: str = "dry-run",
) -> ValidationArtifact:
    """Assemble a ``ValidationArtifact`` with a stable status summary."""
    metadata: dict[str, object] = {"summary": summarize_results(levels)}
    return ValidationArtifact(
        radio=template.radio,
        transport=transport,
        safety=safety,
        levels=levels,
        core_version=core_version,
        core_commit=core_commit,
        logs_path=logs_path,
        mode=mode,
        metadata=metadata,
    )


def human_summary(artifact: ValidationArtifact) -> str:
    """Render a human-readable multi-line summary of a validation artifact."""
    lines: list[str] = []
    lines.append(f"Radio:  {artifact.radio.model} ({artifact.radio.profile_id})")
    lines.append(f"Mode:   {artifact.mode}")
    lines.append(
        f"Safety: tx_allowed={artifact.safety.tx_allowed} "
        f"tuner_allowed={artifact.safety.tuner_allowed}"
    )
    blocked: list[str] = []
    for level in artifact.levels:
        counts: dict[str, int] = {}
        for check in level.checks:
            counts[check.status.value] = counts.get(check.status.value, 0) + 1
            if check.status is CheckStatus.BLOCKED:
                blocked.append(check.check_id)
        count_str = ", ".join(
            f"{status}={count}" for status, count in sorted(counts.items())
        )
        lines.append(f"  L{int(level.level)} {level.level.name.lower()}: {count_str}")
    if blocked:
        lines.append("Blocked checks (authorization required):")
        for check_id in blocked:
            lines.append(f"  - {check_id}")
    else:
        lines.append("Blocked checks: none")
    return "\n".join(lines)


__all__ = [
    "HARDWARE_OPT_IN_ENV",
    "HardwareExecutionBlocked",
    "_is_safety_gated",
    "load_template",
    "dry_run_results",
    "summarize_results",
    "build_validation_artifact",
    "human_summary",
]
