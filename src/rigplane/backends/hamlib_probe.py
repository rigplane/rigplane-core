"""Safe read-only probing and ranking for external Hamlib ``rigctld`` targets."""

from __future__ import annotations

import asyncio
import hashlib
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any, Literal

from rigplane.exceptions import CommandError
from rigplane.exceptions import ConnectionError as RadioConnectionError
from rigplane.exceptions import TimeoutError as RadioTimeoutError

from .hamlib_models import HamlibModelCatalog, HamlibModelMetadata
from .rigctld_client import RigctldTransport

__all__ = [
    "DiscoveryCandidate",
    "DiscoveryEvidence",
    "HamlibProbeTarget",
    "ProbeAuditRecord",
    "ProbeOptions",
    "ProbeResult",
    "probe_hamlib_rigctld_targets",
    "rank_hamlib_probe",
]

Confidence = Literal["high", "medium", "low"]
AuditStatus = Literal[
    "ok",
    "unsupported",
    "timeout",
    "malformed",
    "error",
    "cancelled",
]

_READ_INFO = r"\get_info"
_READ_FREQUENCY = "f"
_READ_MODE = "m"
_TARGET_LOCKS: dict[str, asyncio.Lock] = {}


@dataclass(frozen=True, slots=True)
class DiscoveryEvidence:
    """Privacy-safe evidence explaining a discovery candidate ranking."""

    source: str
    kind: str
    status: str
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class DiscoveryCandidate:
    """Ranked setup candidate produced from safe discovery evidence."""

    transport: str
    address: str
    observed_identity: dict[str, object]
    suggested_backend: str
    suggested_model: str | None
    confidence: Confidence
    evidence: list[DiscoveryEvidence]
    safe_next_action: str


@dataclass(frozen=True, slots=True)
class HamlibProbeTarget:
    """External ``rigctld`` endpoint to probe with read-only commands."""

    host: str
    port: int = 4532
    model_id: int | None = None


@dataclass(frozen=True, slots=True)
class ProbeOptions:
    """Safety controls for Hamlib probing.

    Probing is disabled by default. When disabled, callers must receive an empty
    result without constructing transports or touching any target.
    """

    enabled: bool = False
    max_concurrency: int = 2
    command_timeout: float = 0.25
    target_timeout: float = 1.0


@dataclass(frozen=True, slots=True)
class ProbeAuditRecord:
    """Privacy-safe audit record for one semantic read operation."""

    target_ref: str
    operation: str
    status: AuditStatus
    duration_ms: int
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Read-only probe outcome for one redacted target."""

    target_ref: str
    candidates: list[DiscoveryCandidate]
    audit: list[ProbeAuditRecord] = field(default_factory=list)


async def probe_hamlib_rigctld_targets(
    targets: Iterable[HamlibProbeTarget],
    *,
    options: ProbeOptions | None = None,
    catalog: HamlibModelCatalog | None = None,
    _transport_factory: Any = RigctldTransport,
) -> list[ProbeResult]:
    """Probe external ``rigctld`` targets using only safe read commands.

    The runner sends only ``\\get_info``, ``f``, and ``m``. It does not call the
    higher-level radio backend because backend connection setup may perform
    additional reads outside this discovery safety envelope.
    """
    probe_options = options or ProbeOptions()
    if not probe_options.enabled:
        return []

    target_list = list(targets)
    if not target_list:
        return []

    model_catalog = catalog or HamlibModelCatalog(models={})
    semaphore = asyncio.Semaphore(max(1, probe_options.max_concurrency))
    tasks = [
        asyncio.create_task(
            _probe_one_with_limits(
                target,
                options=probe_options,
                catalog=model_catalog,
                semaphore=semaphore,
                transport_factory=_transport_factory,
            )
        )
        for target in target_list
    ]
    try:
        return await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise


async def _probe_one_with_limits(
    target: HamlibProbeTarget,
    *,
    options: ProbeOptions,
    catalog: HamlibModelCatalog,
    semaphore: asyncio.Semaphore,
    transport_factory: Any,
) -> ProbeResult:
    target_ref = _target_ref(target)
    async with semaphore:
        lock = _lock_for(target)
        async with lock:
            try:
                return await asyncio.wait_for(
                    _probe_one(
                        target,
                        options=options,
                        catalog=catalog,
                        target_ref=target_ref,
                        transport_factory=transport_factory,
                    ),
                    timeout=options.target_timeout,
                )
            except TimeoutError:
                audit = [
                    ProbeAuditRecord(
                        target_ref=target_ref,
                        operation="probe_target",
                        status="timeout",
                        duration_ms=int(options.target_timeout * 1000),
                    )
                ]
                candidates = rank_hamlib_probe(target=target, catalog=catalog)
                return ProbeResult(
                    target_ref=target_ref,
                    candidates=candidates,
                    audit=audit,
                )


async def _probe_one(
    target: HamlibProbeTarget,
    *,
    options: ProbeOptions,
    catalog: HamlibModelCatalog,
    target_ref: str,
    transport_factory: Any,
) -> ProbeResult:
    audit: list[ProbeAuditRecord] = []
    transport = transport_factory(
        host=target.host,
        port=target.port,
        timeout=options.command_timeout,
    )

    info_text: str | None = None
    frequency_readable = False
    mode_readable = False

    try:
        await asyncio.wait_for(transport.connect(), timeout=options.command_timeout)
        info_text = await _query_info(transport, options, target_ref, audit)
        frequency_readable = await _query_frequency(
            transport,
            options,
            target_ref,
            audit,
        )
        mode_readable = await _query_mode(transport, options, target_ref, audit)
    except asyncio.CancelledError:
        audit.append(
            ProbeAuditRecord(
                target_ref=target_ref,
                operation="probe_target",
                status="cancelled",
                duration_ms=0,
            )
        )
        raise
    except (RadioTimeoutError, TimeoutError):
        audit.append(
            ProbeAuditRecord(
                target_ref=target_ref,
                operation="connect",
                status="timeout",
                duration_ms=0,
                detail="timeout",
            )
        )
    except (CommandError, RadioConnectionError, OSError, RuntimeError):
        audit.append(
            ProbeAuditRecord(
                target_ref=target_ref,
                operation="connect",
                status="error",
                duration_ms=0,
            )
        )
    finally:
        await transport.close()

    candidates = rank_hamlib_probe(
        target=target,
        catalog=catalog,
        info_text=info_text,
        frequency_readable=frequency_readable,
        mode_readable=mode_readable,
    )
    return ProbeResult(target_ref=target_ref, candidates=candidates, audit=audit)


async def _query_info(
    transport: RigctldTransport,
    options: ProbeOptions,
    target_ref: str,
    audit: list[ProbeAuditRecord],
) -> str | None:
    lines, status, duration_ms = await _query_read(
        transport,
        command=_READ_INFO,
        response_lines=1,
        timeout=options.command_timeout,
    )
    audit.append(
        ProbeAuditRecord(
            target_ref=target_ref,
            operation="read_info",
            status=status,
            duration_ms=duration_ms,
            detail=_status_detail(status),
        )
    )
    if status != "ok" or not lines:
        return None
    return lines[0]


async def _query_frequency(
    transport: RigctldTransport,
    options: ProbeOptions,
    target_ref: str,
    audit: list[ProbeAuditRecord],
) -> bool:
    lines, status, duration_ms = await _query_read(
        transport,
        command=_READ_FREQUENCY,
        response_lines=1,
        timeout=options.command_timeout,
    )
    if status == "ok" and (not lines or not _is_sane_frequency(lines[0])):
        status = "malformed"
    audit.append(
        ProbeAuditRecord(
            target_ref=target_ref,
            operation="read_frequency",
            status=status,
            duration_ms=duration_ms,
            detail=_status_detail(status),
        )
    )
    return status == "ok"


async def _query_mode(
    transport: RigctldTransport,
    options: ProbeOptions,
    target_ref: str,
    audit: list[ProbeAuditRecord],
) -> bool:
    lines, status, duration_ms = await _query_read(
        transport,
        command=_READ_MODE,
        response_lines=2,
        timeout=options.command_timeout,
    )
    if status == "ok" and not _is_sane_mode(lines):
        status = "malformed"
    audit.append(
        ProbeAuditRecord(
            target_ref=target_ref,
            operation="read_mode",
            status=status,
            duration_ms=duration_ms,
            detail=_status_detail(status),
        )
    )
    return status == "ok"


async def _query_read(
    transport: RigctldTransport,
    *,
    command: str,
    response_lines: int,
    timeout: float,
) -> tuple[list[str], AuditStatus, int]:
    start = time.monotonic()
    try:
        lines = await asyncio.wait_for(
            transport.query(command, response_lines=response_lines),
            timeout=timeout,
        )
    except asyncio.CancelledError:
        raise
    except (RadioTimeoutError, TimeoutError):
        return [], "timeout", _duration_ms(start)
    except CommandError as exc:
        if "unsupported" in str(exc).lower():
            return [], "unsupported", _duration_ms(start)
        return [], "error", _duration_ms(start)
    except (OSError, RuntimeError):
        return [], "error", _duration_ms(start)
    return lines, "ok", _duration_ms(start)


def rank_hamlib_probe(
    *,
    target: HamlibProbeTarget,
    catalog: HamlibModelCatalog,
    info_text: str | None = None,
    frequency_readable: bool = False,
    mode_readable: bool = False,
) -> list[DiscoveryCandidate]:
    """Rank safe Hamlib observations into one or more candidates."""
    matches = _matched_models(target, catalog, info_text)
    evidence = _ranking_evidence(
        target=target,
        catalog=catalog,
        info_text=info_text,
        frequency_readable=frequency_readable,
        mode_readable=mode_readable,
    )

    if len(matches) > 1:
        return [
            _candidate(
                target=target,
                model=model,
                confidence="medium",
                evidence=evidence,
                safe_next_action="confirm_model",
            )
            for model in matches
        ]

    if len(matches) == 1:
        confidence: Confidence = (
            "high" if info_text and frequency_readable and mode_readable else "medium"
        )
        return [
            _candidate(
                target=target,
                model=matches[0],
                confidence=confidence,
                evidence=evidence,
                safe_next_action=(
                    "read_only_probe_confirmed"
                    if confidence == "high"
                    else "confirm_model"
                ),
            )
        ]

    confidence = _fallback_confidence(
        target=target,
        catalog=catalog,
        info_text=info_text,
        frequency_readable=frequency_readable,
        mode_readable=mode_readable,
    )
    return [
        _candidate(
            target=target,
            model=None,
            confidence=confidence,
            evidence=evidence,
            safe_next_action=(
                "confirm_model"
                if confidence == "medium"
                else "manual_configuration_required"
            ),
        )
    ]


def _candidate(
    *,
    target: HamlibProbeTarget,
    model: HamlibModelMetadata | None,
    confidence: Confidence,
    evidence: list[DiscoveryEvidence],
    safe_next_action: str,
) -> DiscoveryCandidate:
    observed_identity: dict[str, object] = {
        "rigctld_endpoint": "redacted",
    }
    if target.model_id is not None:
        observed_identity["target_model_id"] = target.model_id
    if any(
        item.kind == "rigctld_info" and item.status == "available" for item in evidence
    ):
        observed_identity["rigctld_info"] = "available"
    if any(item.kind == "frequency" and item.status == "readable" for item in evidence):
        observed_identity["frequency"] = "readable"
    if any(item.kind == "mode" and item.status == "readable" for item in evidence):
        observed_identity["mode"] = "readable"

    return DiscoveryCandidate(
        transport="rigctld",
        address=_target_ref(target),
        observed_identity=observed_identity,
        suggested_backend="hamlib",
        suggested_model=_model_label(model),
        confidence=confidence,
        evidence=evidence,
        safe_next_action=safe_next_action,
    )


def _matched_models(
    target: HamlibProbeTarget,
    catalog: HamlibModelCatalog,
    info_text: str | None,
) -> list[HamlibModelMetadata]:
    if target.model_id is not None and target.model_id in catalog.models:
        return [catalog.models[target.model_id]]
    if not info_text or not catalog.models:
        return []

    info = info_text.lower()
    ids = {int(value) for value in re.findall(r"\b\d{2,6}\b", info)}
    id_matches = [
        model for model_id, model in catalog.models.items() if model_id in ids
    ]
    if id_matches:
        return sorted(id_matches, key=lambda item: item.model_id)

    full_name_matches = [
        model for model in catalog.models.values() if model.name.lower() in info
    ]
    if full_name_matches:
        return sorted(full_name_matches, key=lambda item: item.model_id)

    info_tokens = {
        token
        for token in re.split(r"[^a-z0-9]+", info)
        if len(token) >= 4 and not token.isdigit()
    }
    if not info_tokens:
        return []
    token_matches = [
        model
        for model in catalog.models.values()
        if info_tokens & set(re.split(r"[^a-z0-9]+", model.name.lower()))
    ]
    return sorted(token_matches, key=lambda item: item.model_id)


def _ranking_evidence(
    *,
    target: HamlibProbeTarget,
    catalog: HamlibModelCatalog,
    info_text: str | None,
    frequency_readable: bool,
    mode_readable: bool,
) -> list[DiscoveryEvidence]:
    evidence: list[DiscoveryEvidence] = []
    if target.model_id is not None:
        evidence.append(
            DiscoveryEvidence(
                source="inventory",
                kind="hamlib_model_id",
                status=(
                    "catalog_match"
                    if target.model_id in catalog.models
                    else "catalog_unknown"
                ),
            )
        )
    if info_text:
        evidence.append(
            DiscoveryEvidence(
                source="rigctld_probe",
                kind="rigctld_info",
                status="available",
            )
        )
    if frequency_readable:
        evidence.append(
            DiscoveryEvidence(
                source="rigctld_probe",
                kind="frequency",
                status="readable",
            )
        )
    if mode_readable:
        evidence.append(
            DiscoveryEvidence(
                source="rigctld_probe",
                kind="mode",
                status="readable",
            )
        )
    if catalog.degraded_reason is not None:
        evidence.append(
            DiscoveryEvidence(
                source="hamlib_catalog",
                kind="catalog",
                status="degraded",
                detail=catalog.degraded_reason,
            )
        )
    if not evidence:
        evidence.append(
            DiscoveryEvidence(
                source="rigctld_probe",
                kind="probe",
                status="unconfirmed",
            )
        )
    return evidence


def _fallback_confidence(
    *,
    target: HamlibProbeTarget,
    catalog: HamlibModelCatalog,
    info_text: str | None,
    frequency_readable: bool,
    mode_readable: bool,
) -> Confidence:
    if catalog.degraded_reason is not None:
        return "low"
    if target.model_id is not None and target.model_id in catalog.models:
        return "medium"
    if info_text and frequency_readable and mode_readable and catalog.models:
        return "medium"
    return "low"


def _is_sane_frequency(value: str) -> bool:
    try:
        frequency = int(value)
    except ValueError:
        return False
    return 1_000 <= frequency <= 3_000_000_000


def _is_sane_mode(lines: list[str]) -> bool:
    if len(lines) != 2:
        return False
    mode = lines[0].strip()
    if not mode or not mode.replace("-", "").isalnum():
        return False
    try:
        passband = int(lines[1])
    except ValueError:
        return False
    return passband >= 0


def _target_ref(target: HamlibProbeTarget) -> str:
    digest = hashlib.sha256(f"{target.host}:{target.port}".encode("utf-8")).hexdigest()
    return f"rigctld:{digest[:12]}"


def _target_key(target: HamlibProbeTarget) -> str:
    return f"{target.host}:{target.port}"


def _lock_for(target: HamlibProbeTarget) -> asyncio.Lock:
    key = _target_key(target)
    lock = _TARGET_LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _TARGET_LOCKS[key] = lock
    return lock


def _duration_ms(start: float) -> int:
    return max(0, int((time.monotonic() - start) * 1000))


def _status_detail(status: AuditStatus) -> str | None:
    if status == "ok":
        return "readable"
    if status in {"unsupported", "timeout", "malformed", "cancelled"}:
        return status
    return None


def _model_label(model: HamlibModelMetadata | None) -> str | None:
    if model is None:
        return None
    return f"{model.model_id} {model.name}"
