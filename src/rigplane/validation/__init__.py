"""Real-radio validation matrix: schema, validators, and dry-run runner.

Public surface for the ``rigplane validate`` vertical. The schema dataclasses
and validators form a versioned contract (see
``docs/contracts/validation-matrix-v1.md``); the runner provides the dry-run
path that plans checks from a template without touching hardware.
"""

from __future__ import annotations

from rigplane.validation.comparison import compute_comparison_dimensions
from rigplane.validation.hardware import execute_hardware_checks
from rigplane.validation.runner import (
    HARDWARE_OPT_IN_ENV,
    HardwareExecutionBlocked,
    build_validation_artifact,
    dry_run_results,
    human_summary,
    load_template,
    summarize_results,
)
from rigplane.validation.schema import (
    SCHEMA_VERSION,
    TOOL_NAME,
    CapabilityDeclaration,
    CapabilityDeclarationEntry,
    CheckResult,
    CheckStatus,
    FailureDomain,
    LevelResult,
    MatrixTemplate,
    OperatorSafetyBlock,
    RadioTarget,
    SchemaValidationError,
    TransportInfo,
    ValidationArtifact,
    ValidationLevel,
    validate_artifact_dict,
    validate_template_dict,
)

__all__ = [
    "SCHEMA_VERSION",
    "TOOL_NAME",
    "CheckStatus",
    "CapabilityDeclaration",
    "ValidationLevel",
    "FailureDomain",
    "CheckResult",
    "LevelResult",
    "OperatorSafetyBlock",
    "TransportInfo",
    "RadioTarget",
    "CapabilityDeclarationEntry",
    "MatrixTemplate",
    "ValidationArtifact",
    "SchemaValidationError",
    "validate_template_dict",
    "validate_artifact_dict",
    "HARDWARE_OPT_IN_ENV",
    "HardwareExecutionBlocked",
    "load_template",
    "dry_run_results",
    "summarize_results",
    "build_validation_artifact",
    "human_summary",
    "execute_hardware_checks",
    "compute_comparison_dimensions",
]
