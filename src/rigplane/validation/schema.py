"""Real-radio validation matrix schema models and validators.

This module defines the frozen, machine-readable contract for the
``rigplane validate`` vertical: capability-declaration templates (the planned
matrix per radio) and validation artifacts (the recorded evidence). Both
shapes are versioned by ``SCHEMA_VERSION`` and emitted by
``rigplane-validation-matrix``.

The dataclasses mirror the audio-probe style (frozen + slots, ``to_dict``
shaping) and the validators narrow ``object`` with ``isinstance`` before
indexing so the public API stays type-safe without ``type: ignore``.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field
from enum import Enum, IntEnum, StrEnum

from rigplane.core.capabilities import KNOWN_CAPABILITIES

SCHEMA_VERSION = 1
TOOL_NAME = "rigplane-validation-matrix"


def _jsonify(value: object) -> object:
    """Recursively coerce ``value`` into JSON-serializable types.

    Evidence dicts can hold non-primitive objects (a ``ScopeFixedEdge``
    dataclass, an ``Enum``, ``bytes``) that ``json.dumps`` cannot encode.
    This walker normalizes them so the artifact can ALWAYS be emitted:

    - dataclass instance -> its ``to_dict()`` if present, else ``asdict`` (recurse);
    - ``Enum`` -> its ``.value``;
    - ``bytes``/``bytearray`` -> hex string;
    - ``set``/``tuple`` -> list of recursed elements;
    - ``dict`` -> recursed values (non-str keys coerced via ``str``);
    - ``list`` -> recursed elements;
    - primitives (str/int/float/bool/None) -> unchanged.
    """
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Enum):
        return _jsonify(value.value)
    if isinstance(value, (bytes, bytearray)):
        return value.hex()
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        to_dict = getattr(value, "to_dict", None)
        if callable(to_dict):
            return _jsonify(to_dict())
        return _jsonify(dataclasses.asdict(value))
    if isinstance(value, dict):
        return {
            (key if isinstance(key, str) else str(key)): _jsonify(item)
            for key, item in value.items()
        }
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonify(item) for item in value]
    return value


class CheckStatus(StrEnum):
    """Normalized outcome of a single validation check."""

    PASS = "pass"
    FAIL = "fail"
    SKIP = "skip"
    UNSUPPORTED = "unsupported"
    MANUAL_REQUIRED = "manual_required"
    BLOCKED = "blocked"


class CapabilityDeclaration(StrEnum):
    """Declared support posture for a capability in a template."""

    SUPPORTED = "supported"
    UNSUPPORTED_PENDING_EVIDENCE = "unsupported_pending_evidence"
    MANUAL_REQUIRED = "manual_required"


class ValidationLevel(IntEnum):
    """Validation depth, ascending from static inspection to stress."""

    STATIC_PROFILE = 0
    DISCOVERY = 1
    BASIC_CONTROL = 2
    CAPABILITY_MATRIX = 3
    COMPATIBILITY_SURFACES = 4
    STRESS_RECOVERY = 5


class FailureDomain(StrEnum):
    """Subsystem responsible for a failed or blocked check."""

    DISCOVERY = "discovery"
    TRANSPORT = "transport"
    COMMAND_EXECUTION = "command_execution"
    READBACK = "readback"
    STATE_PUBLISHING = "state_publishing"
    RIGCTLD = "rigctld"
    AUDIO = "audio"
    SCOPE_WATERFALL = "scope_waterfall"


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Observed result for a single validation check."""

    check_id: str
    capability: str
    level: ValidationLevel
    status: CheckStatus
    declaration: CapabilityDeclaration
    summary: str
    failure_domain: FailureDomain | None = None
    evidence: dict[str, object] = field(default_factory=dict)
    error: str | None = None
    started_at: str | None = None
    finished_at: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "check_id": self.check_id,
            "capability": self.capability,
            "level": int(self.level),
            "level_name": self.level.name.lower(),
            "status": self.status.value,
            "declaration": self.declaration.value,
            "summary": self.summary,
        }
        if self.failure_domain is not None:
            payload["failure_domain"] = self.failure_domain.value
        if self.evidence:
            payload["evidence"] = {
                key: _jsonify(value) for key, value in self.evidence.items()
            }
        if self.error:
            payload["error"] = self.error
        if self.started_at is not None:
            payload["started_at"] = self.started_at
        if self.finished_at is not None:
            payload["finished_at"] = self.finished_at
        return payload


@dataclass(frozen=True, slots=True)
class LevelResult:
    """All checks observed at one validation level."""

    level: ValidationLevel
    checks: list[CheckResult]

    def to_dict(self) -> dict[str, object]:
        return {
            "level": int(self.level),
            "level_name": self.level.name.lower(),
            "checks": [check.to_dict() for check in self.checks],
        }


@dataclass(frozen=True, slots=True)
class OperatorSafetyBlock:
    """Operator authorization gating TX-adjacent and tuner checks."""

    tx_allowed: bool = False
    tuner_allowed: bool = False
    operator_id: str | None = None
    authorized_at_unix: int | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "tx_allowed": self.tx_allowed,
            "tuner_allowed": self.tuner_allowed,
        }
        if self.operator_id is not None:
            payload["operator_id"] = self.operator_id
        if self.authorized_at_unix is not None:
            payload["authorized_at_unix"] = self.authorized_at_unix
        return payload


@dataclass(frozen=True, slots=True)
class TransportInfo:
    """Transport the validation run was executed against."""

    backend: str
    host: str | None = None
    port: int | None = None
    baud: int | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"backend": self.backend}
        if self.host is not None:
            payload["host"] = self.host
        if self.port is not None:
            payload["port"] = self.port
        if self.baud is not None:
            payload["baud"] = self.baud
        return payload


@dataclass(frozen=True, slots=True)
class RadioTarget:
    """Radio under validation."""

    model: str
    profile_id: str

    def to_dict(self) -> dict[str, object]:
        return {"model": self.model, "profile_id": self.profile_id}


@dataclass(frozen=True, slots=True)
class CapabilityDeclarationEntry:
    """One planned capability check in a validation template."""

    check_id: str
    capability: str
    level: ValidationLevel
    declaration: CapabilityDeclaration
    summary: str
    tx_adjacent: bool = False

    def to_dict(self) -> dict[str, object]:
        return {
            "check_id": self.check_id,
            "capability": self.capability,
            "level": int(self.level),
            "declaration": self.declaration.value,
            "summary": self.summary,
            "tx_adjacent": self.tx_adjacent,
        }


@dataclass(frozen=True, slots=True)
class MatrixTemplate:
    """Planned validation matrix for one radio."""

    radio: RadioTarget
    entries: list[CapabilityDeclarationEntry]
    schema_version: int = SCHEMA_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "radio": self.radio.to_dict(),
            "entries": [entry.to_dict() for entry in self.entries],
        }


@dataclass(frozen=True, slots=True)
class ValidationArtifact:
    """Machine-readable evidence artifact emitted by a validation run."""

    radio: RadioTarget
    transport: TransportInfo
    safety: OperatorSafetyBlock
    levels: list[LevelResult]
    core_version: str
    core_commit: str | None = None
    logs_path: str | None = None
    mode: str = "dry-run"
    schema_version: int = SCHEMA_VERSION
    tool: str = TOOL_NAME
    metadata: dict[str, object] = field(default_factory=dict)
    generated_at: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": self.schema_version,
            "tool": self.tool,
            "mode": self.mode,
            "core_version": self.core_version,
            "radio": self.radio.to_dict(),
            "transport": self.transport.to_dict(),
            "safety": self.safety.to_dict(),
            "levels": [level.to_dict() for level in self.levels],
            "metadata": dict(self.metadata),
        }
        if self.core_commit is not None:
            payload["core_commit"] = self.core_commit
        if self.logs_path is not None:
            payload["logs_path"] = self.logs_path
        if self.generated_at is not None:
            payload["generated_at"] = self.generated_at
        return payload


class SchemaValidationError(ValueError):
    """Raised when a template or artifact dict violates the schema."""


_LEVEL_RANGE = range(0, 6)


def _require_dict(value: object, where: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise SchemaValidationError(f"{where} must be an object")
    return value


def _require_str(value: object, where: str, *, non_empty: bool = False) -> str:
    if not isinstance(value, str):
        raise SchemaValidationError(f"{where} must be a string")
    if non_empty and not value:
        raise SchemaValidationError(f"{where} must be a non-empty string")
    return value


def _require_int(value: object, where: str) -> int:
    # bool is a subclass of int; reject it so flags don't masquerade as levels.
    if isinstance(value, bool) or not isinstance(value, int):
        raise SchemaValidationError(f"{where} must be an integer")
    return value


def validate_template_dict(data: object) -> MatrixTemplate:
    """Validate a template dict and build a ``MatrixTemplate``.

    Raises ``SchemaValidationError`` on the first violation. Does not mutate
    the input.
    """
    root = _require_dict(data, "template")

    if "schema_version" not in root:
        raise SchemaValidationError("template.schema_version is required")
    schema_version = _require_int(root["schema_version"], "template.schema_version")
    if schema_version != SCHEMA_VERSION:
        raise SchemaValidationError(
            f"template.schema_version must be {SCHEMA_VERSION}, got {schema_version}"
        )

    if "radio" not in root:
        raise SchemaValidationError("template.radio is required")
    radio_dict = _require_dict(root["radio"], "template.radio")
    model = _require_str(
        radio_dict.get("model"), "template.radio.model", non_empty=True
    )
    profile_id = _require_str(
        radio_dict.get("profile_id"), "template.radio.profile_id", non_empty=True
    )
    radio = RadioTarget(model=model, profile_id=profile_id)

    entries_obj = root.get("entries")
    if not isinstance(entries_obj, list):
        raise SchemaValidationError("template.entries must be a list")
    if not entries_obj:
        raise SchemaValidationError("template.entries must be non-empty")

    seen_ids: set[str] = set()
    entries: list[CapabilityDeclarationEntry] = []
    for index, raw_entry in enumerate(entries_obj):
        where = f"template.entries[{index}]"
        entry_dict = _require_dict(raw_entry, where)
        check_id = _require_str(
            entry_dict.get("check_id"), f"{where}.check_id", non_empty=True
        )
        if check_id in seen_ids:
            raise SchemaValidationError(f"{where}.check_id {check_id!r} is duplicated")
        seen_ids.add(check_id)

        capability = _require_str(entry_dict.get("capability"), f"{where}.capability")
        if capability and capability not in KNOWN_CAPABILITIES:
            raise SchemaValidationError(
                f"{where}.capability {capability!r} is not a known capability"
            )

        level_int = _require_int(entry_dict.get("level"), f"{where}.level")
        if level_int not in _LEVEL_RANGE:
            raise SchemaValidationError(f"{where}.level must be in 0..5")

        declaration_str = _require_str(
            entry_dict.get("declaration"), f"{where}.declaration"
        )
        if declaration_str not in {d.value for d in CapabilityDeclaration}:
            raise SchemaValidationError(
                f"{where}.declaration {declaration_str!r} is invalid"
            )

        summary = _require_str(entry_dict.get("summary"), f"{where}.summary")

        tx_adjacent_obj = entry_dict.get("tx_adjacent", False)
        if not isinstance(tx_adjacent_obj, bool):
            raise SchemaValidationError(f"{where}.tx_adjacent must be a boolean")

        entries.append(
            CapabilityDeclarationEntry(
                check_id=check_id,
                capability=capability,
                level=ValidationLevel(level_int),
                declaration=CapabilityDeclaration(declaration_str),
                summary=summary,
                tx_adjacent=tx_adjacent_obj,
            )
        )

    return MatrixTemplate(radio=radio, entries=entries)


def _validate_safety_dict(data: object) -> OperatorSafetyBlock:
    safety_dict = _require_dict(data, "artifact.safety")
    tx_allowed = safety_dict.get("tx_allowed", False)
    if not isinstance(tx_allowed, bool):
        raise SchemaValidationError("artifact.safety.tx_allowed must be a boolean")
    tuner_allowed = safety_dict.get("tuner_allowed", False)
    if not isinstance(tuner_allowed, bool):
        raise SchemaValidationError("artifact.safety.tuner_allowed must be a boolean")
    operator_id_obj = safety_dict.get("operator_id")
    operator_id = (
        None
        if operator_id_obj is None
        else _require_str(operator_id_obj, "artifact.safety.operator_id")
    )
    authorized_obj = safety_dict.get("authorized_at_unix")
    authorized_at_unix = (
        None
        if authorized_obj is None
        else _require_int(authorized_obj, "artifact.safety.authorized_at_unix")
    )
    return OperatorSafetyBlock(
        tx_allowed=tx_allowed,
        tuner_allowed=tuner_allowed,
        operator_id=operator_id,
        authorized_at_unix=authorized_at_unix,
    )


def _validate_transport_dict(data: object) -> TransportInfo:
    transport_dict = _require_dict(data, "artifact.transport")
    backend = _require_str(
        transport_dict.get("backend"), "artifact.transport.backend", non_empty=True
    )
    host_obj = transport_dict.get("host")
    host = (
        None if host_obj is None else _require_str(host_obj, "artifact.transport.host")
    )
    port_obj = transport_dict.get("port")
    port = (
        None if port_obj is None else _require_int(port_obj, "artifact.transport.port")
    )
    baud_obj = transport_dict.get("baud")
    baud = (
        None if baud_obj is None else _require_int(baud_obj, "artifact.transport.baud")
    )
    return TransportInfo(backend=backend, host=host, port=port, baud=baud)


def _validate_check_dict(data: object, where: str) -> CheckResult:
    check_dict = _require_dict(data, where)
    check_id = _require_str(check_dict.get("check_id"), f"{where}.check_id")
    capability = _require_str(check_dict.get("capability"), f"{where}.capability")
    if capability and capability not in KNOWN_CAPABILITIES:
        raise SchemaValidationError(
            f"{where}.capability {capability!r} is not a known capability"
        )

    level_int = _require_int(check_dict.get("level"), f"{where}.level")
    if level_int not in _LEVEL_RANGE:
        raise SchemaValidationError(f"{where}.level must be in 0..5")

    status_str = _require_str(check_dict.get("status"), f"{where}.status")
    if status_str not in {s.value for s in CheckStatus}:
        raise SchemaValidationError(f"{where}.status {status_str!r} is invalid")
    status = CheckStatus(status_str)

    declaration_str = _require_str(
        check_dict.get("declaration"), f"{where}.declaration"
    )
    if declaration_str not in {d.value for d in CapabilityDeclaration}:
        raise SchemaValidationError(
            f"{where}.declaration {declaration_str!r} is invalid"
        )

    summary = _require_str(check_dict.get("summary"), f"{where}.summary")

    failure_domain_obj = check_dict.get("failure_domain")
    failure_domain: FailureDomain | None = None
    if failure_domain_obj is not None:
        domain_str = _require_str(failure_domain_obj, f"{where}.failure_domain")
        if domain_str not in {d.value for d in FailureDomain}:
            raise SchemaValidationError(
                f"{where}.failure_domain {domain_str!r} is invalid"
            )
        failure_domain = FailureDomain(domain_str)

    if status in {CheckStatus.FAIL, CheckStatus.BLOCKED} and failure_domain is None:
        raise SchemaValidationError(
            f"{where}.failure_domain is required when status is {status.value!r}"
        )

    evidence_obj = check_dict.get("evidence", {})
    if not isinstance(evidence_obj, dict):
        raise SchemaValidationError(f"{where}.evidence must be an object")

    error_obj = check_dict.get("error")
    error = None if error_obj is None else _require_str(error_obj, f"{where}.error")

    started_at_obj = check_dict.get("started_at")
    started_at = (
        None
        if started_at_obj is None
        else _require_str(started_at_obj, f"{where}.started_at")
    )
    finished_at_obj = check_dict.get("finished_at")
    finished_at = (
        None
        if finished_at_obj is None
        else _require_str(finished_at_obj, f"{where}.finished_at")
    )

    return CheckResult(
        check_id=check_id,
        capability=capability,
        level=ValidationLevel(level_int),
        status=status,
        declaration=CapabilityDeclaration(declaration_str),
        summary=summary,
        failure_domain=failure_domain,
        evidence=dict(evidence_obj),
        error=error,
        started_at=started_at,
        finished_at=finished_at,
    )


def validate_artifact_dict(data: object) -> ValidationArtifact:
    """Validate an artifact dict and build a ``ValidationArtifact``.

    Raises ``SchemaValidationError`` on the first violation. Does not mutate
    the input.
    """
    root = _require_dict(data, "artifact")

    if "schema_version" not in root:
        raise SchemaValidationError("artifact.schema_version is required")
    schema_version = _require_int(root["schema_version"], "artifact.schema_version")
    if schema_version != SCHEMA_VERSION:
        raise SchemaValidationError(
            f"artifact.schema_version must be {SCHEMA_VERSION}, got {schema_version}"
        )

    if "tool" not in root:
        raise SchemaValidationError("artifact.tool is required")
    tool = _require_str(root["tool"], "artifact.tool")

    if "radio" not in root:
        raise SchemaValidationError("artifact.radio is required")
    radio_dict = _require_dict(root["radio"], "artifact.radio")
    radio = RadioTarget(
        model=_require_str(
            radio_dict.get("model"), "artifact.radio.model", non_empty=True
        ),
        profile_id=_require_str(
            radio_dict.get("profile_id"), "artifact.radio.profile_id", non_empty=True
        ),
    )

    if "transport" not in root:
        raise SchemaValidationError("artifact.transport is required")
    transport = _validate_transport_dict(root["transport"])

    if "safety" not in root:
        raise SchemaValidationError("artifact.safety is required")
    safety = _validate_safety_dict(root["safety"])

    levels_obj = root.get("levels")
    if not isinstance(levels_obj, list):
        raise SchemaValidationError("artifact.levels must be a list")

    levels: list[LevelResult] = []
    for index, raw_level in enumerate(levels_obj):
        where = f"artifact.levels[{index}]"
        level_dict = _require_dict(raw_level, where)
        level_int = _require_int(level_dict.get("level"), f"{where}.level")
        if level_int not in _LEVEL_RANGE:
            raise SchemaValidationError(f"{where}.level must be in 0..5")
        checks_obj = level_dict.get("checks")
        if not isinstance(checks_obj, list):
            raise SchemaValidationError(f"{where}.checks must be a list")
        checks = [
            _validate_check_dict(raw_check, f"{where}.checks[{check_index}]")
            for check_index, raw_check in enumerate(checks_obj)
        ]
        levels.append(LevelResult(level=ValidationLevel(level_int), checks=checks))

    core_version_obj = root.get("core_version", "")
    core_version = _require_str(core_version_obj, "artifact.core_version")

    core_commit_obj = root.get("core_commit")
    core_commit = (
        None
        if core_commit_obj is None
        else _require_str(core_commit_obj, "artifact.core_commit")
    )

    logs_path_obj = root.get("logs_path")
    logs_path = (
        None
        if logs_path_obj is None
        else _require_str(logs_path_obj, "artifact.logs_path")
    )

    mode_obj = root.get("mode", "dry-run")
    mode = _require_str(mode_obj, "artifact.mode")

    metadata_obj = root.get("metadata", {})
    if not isinstance(metadata_obj, dict):
        raise SchemaValidationError("artifact.metadata must be an object")

    generated_at_obj = root.get("generated_at")
    generated_at = (
        None
        if generated_at_obj is None
        else _require_str(generated_at_obj, "artifact.generated_at")
    )

    return ValidationArtifact(
        radio=radio,
        transport=transport,
        safety=safety,
        levels=levels,
        core_version=core_version,
        core_commit=core_commit,
        logs_path=logs_path,
        mode=mode,
        tool=tool,
        metadata=dict(metadata_obj),
        generated_at=generated_at,
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
]
