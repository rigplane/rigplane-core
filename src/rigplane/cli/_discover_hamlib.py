"""Hamlib-assisted discovery payload and human-output helpers."""

from __future__ import annotations

from typing import Any


def _camelize_discovery_key(key: str) -> str:
    parts = key.split("_")
    return parts[0] + "".join(part[:1].upper() + part[1:] for part in parts[1:])


def _camelize_discovery_value(value: object) -> object:
    if isinstance(value, dict):
        return {
            _camelize_discovery_key(str(key)): _camelize_discovery_value(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [_camelize_discovery_value(item) for item in value]
    return value


def _hamlib_catalog_payload(catalog: Any) -> dict[str, object]:
    return {
        "available": catalog.degraded_reason is None and bool(catalog.models),
        "sourceTool": catalog.source_tool,
        "modelCount": len(catalog.models),
        "degradedReason": catalog.degraded_reason,
    }


def _hamlib_evidence_payload(evidence: object) -> dict[str, object]:
    payload = {
        "source": getattr(evidence, "source"),
        "kind": getattr(evidence, "kind"),
        "status": getattr(evidence, "status"),
        "detail": getattr(evidence, "detail"),
    }
    return {key: value for key, value in payload.items() if value is not None}


def _hamlib_candidate_payload(
    candidate: object,
    *,
    candidate_id: str,
    auto_selectable: bool,
) -> dict[str, object]:
    return {
        "id": candidate_id,
        "transport": getattr(candidate, "transport"),
        "address": getattr(candidate, "address"),
        "observedIdentity": _camelize_discovery_value(
            getattr(candidate, "observed_identity")
        ),
        "suggestedBackend": getattr(candidate, "suggested_backend"),
        "suggestedModel": getattr(candidate, "suggested_model"),
        "confidence": getattr(candidate, "confidence"),
        "evidence": [
            _hamlib_evidence_payload(item) for item in getattr(candidate, "evidence")
        ],
        "safeNextAction": getattr(candidate, "safe_next_action"),
        "autoSelectable": auto_selectable,
    }


def _match_hamlib_catalog_model(serial: dict[str, object], catalog: Any) -> Any | None:
    if catalog.degraded_reason is not None:
        return None
    serial_model = str(serial.get("model") or "").lower()
    if not serial_model:
        return None
    serial_tokens = {
        token
        for token in serial_model.replace("-", " ").replace("_", " ").split()
        if token
    }
    for model in sorted(catalog.models.values(), key=lambda item: item.model_id):
        catalog_name = model.name.lower()
        catalog_tokens = {
            token
            for token in catalog_name.replace("-", " ").replace("_", " ").split()
            if token
        }
        if serial_model in catalog_name or serial_tokens <= catalog_tokens:
            return model
    return None


def _build_hamlib_serial_candidate(
    serial: dict[str, object],
    *,
    catalog: Any,
    candidate_id: str,
) -> dict[str, object]:
    from rigplane.discovery import DiscoveryCandidate, DiscoveryEvidence

    catalog_match = _match_hamlib_catalog_model(serial, catalog)
    evidence = [
        DiscoveryEvidence(
            source="serial_discovery",
            kind="identity",
            status="detected",
            detail=str(serial.get("model") or serial.get("protocol") or "serial"),
        )
    ]
    if catalog.degraded_reason is not None:
        evidence.append(
            DiscoveryEvidence(
                source="hamlib_catalog",
                kind="catalog",
                status="degraded",
                detail=catalog.degraded_reason,
            )
        )
    elif catalog_match is not None:
        evidence.append(
            DiscoveryEvidence(
                source="hamlib_catalog",
                kind="model",
                status="catalog_match",
                detail=f"{catalog_match.model_id} {catalog_match.name}",
            )
        )
    else:
        evidence.append(
            DiscoveryEvidence(
                source="hamlib_catalog",
                kind="model",
                status="no_match",
            )
        )

    confidence = "medium" if catalog_match is not None else "low"
    if catalog_match is not None:
        safe_next_action = "run_read_only_validation"
        suggested_model = f"{catalog_match.model_id} {catalog_match.name}"
    elif catalog.degraded_reason is not None:
        safe_next_action = "install_hamlib_then_validate"
        suggested_model = None
    else:
        safe_next_action = "manual_configuration_required"
        suggested_model = None

    candidate = DiscoveryCandidate(
        transport="serial",
        address=str(serial.get("port") or ""),
        observed_identity={
            "model": serial.get("model"),
            "protocol": serial.get("protocol"),
            "profile_id": serial.get("profile_id"),
            "baudrate": serial.get("baudrate", serial.get("baud")),
            "radio_address": serial.get("address"),
            "description": serial.get("description"),
            "hwid": serial.get("hwid"),
            "vid": serial.get("vid"),
            "pid": serial.get("pid"),
            "manufacturer": serial.get("manufacturer"),
            "product": serial.get("product"),
        },
        suggested_backend="hamlib",
        suggested_model=suggested_model,
        confidence=confidence,
        evidence=evidence,
        safe_next_action=safe_next_action,
    )
    return _hamlib_candidate_payload(
        candidate,
        candidate_id=candidate_id,
        auto_selectable=False,
    )


def _hamlib_audit_payload(audit: object) -> dict[str, object]:
    payload = {
        "targetRef": getattr(audit, "target_ref"),
        "operation": getattr(audit, "operation"),
        "status": getattr(audit, "status"),
        "durationMs": getattr(audit, "duration_ms"),
        "detail": getattr(audit, "detail"),
    }
    return {key: value for key, value in payload.items() if value is not None}


def _hamlib_validation_payload(results: list[object]) -> dict[str, object]:
    audit_records = [
        record for result in results for record in getattr(result, "audit", [])
    ]
    frequency_readable = any(
        getattr(record, "operation") == "read_frequency"
        and getattr(record, "status") == "ok"
        for record in audit_records
    )
    mode_readable = any(
        getattr(record, "operation") == "read_mode"
        and getattr(record, "status") == "ok"
        for record in audit_records
    )
    identity_readable = any(
        getattr(record, "operation") == "read_info"
        and getattr(record, "status") == "ok"
        for record in audit_records
    )
    high_confidence = any(
        getattr(candidate, "confidence") == "high"
        for result in results
        for candidate in getattr(result, "candidates", [])
    )
    if high_confidence and frequency_readable and mode_readable:
        status = "confirmed"
    elif frequency_readable or mode_readable or identity_readable:
        status = "partial"
    else:
        status = "unconfirmed"

    return {
        "status": status,
        "readOnly": True,
        "safeOperations": ["read_info", "read_frequency", "read_mode"],
        "targetRefs": [getattr(result, "target_ref") for result in results],
        "frequencyReadable": frequency_readable,
        "modeReadable": mode_readable,
        "identityEvidence": "available" if identity_readable else "unconfirmed",
        "audit": [_hamlib_audit_payload(record) for record in audit_records],
    }


def build_hamlib_discovery_payload(
    *,
    catalog: Any,
    serial_radios: list[dict[str, object]],
    serial_scan_enabled: bool,
    candidates_requested: bool,
    validation_results: list[object],
) -> dict[str, object]:
    messages: list[dict[str, object]] = []
    candidates: list[dict[str, object]] = []

    if catalog.degraded_reason is not None:
        messages.append(
            {
                "severity": "warning",
                "code": "hamlibCatalogUnavailable",
                "message": catalog.degraded_reason,
            }
        )

    if candidates_requested:
        if serial_scan_enabled and not serial_radios:
            messages.append(
                {
                    "severity": "info",
                    "code": "noSerialCandidates",
                    "message": "No serial radio candidates were found.",
                }
            )
        elif not serial_scan_enabled:
            messages.append(
                {
                    "severity": "info",
                    "code": "serialScanSkipped",
                    "message": "Serial scanning was skipped by --lan-only.",
                }
            )
        for index, serial in enumerate(serial_radios, start=1):
            candidates.append(
                _build_hamlib_serial_candidate(
                    serial,
                    catalog=catalog,
                    candidate_id=f"hamlib-serial-{index}",
                )
            )

    for result_index, result in enumerate(validation_results, start=1):
        for candidate_index, candidate in enumerate(
            getattr(result, "candidates", []),
            start=1,
        ):
            candidates.append(
                _hamlib_candidate_payload(
                    candidate,
                    candidate_id=f"hamlib-validation-{result_index}-{candidate_index}",
                    auto_selectable=False,
                )
            )

    high_candidate_ids = [
        str(candidate["id"])
        for candidate in candidates
        if candidate.get("confidence") == "high"
    ]
    if len(high_candidate_ids) == 1:
        for candidate in candidates:
            candidate["autoSelectable"] = candidate["id"] == high_candidate_ids[0]

    payload: dict[str, object] = {
        "schema": "rigplane.discovery.hamlib.v1",
        "catalog": _hamlib_catalog_payload(catalog),
        "candidates": candidates,
        "messages": messages,
        "summary": {
            "candidateCount": len(candidates),
            "highConfidenceCount": len(high_candidate_ids),
            "autoSelectableCount": 1 if len(high_candidate_ids) == 1 else 0,
        },
    }
    if validation_results:
        payload["validation"] = _hamlib_validation_payload(validation_results)
    return payload


def print_hamlib_human(payload: dict[str, object]) -> None:
    catalog = payload["catalog"]
    assert isinstance(catalog, dict)
    candidates = payload["candidates"]
    assert isinstance(candidates, list)
    messages = payload["messages"]
    assert isinstance(messages, list)

    print("\nHamlib assisted discovery:")
    if catalog.get("available"):
        source = catalog.get("sourceTool") or "hamlib"
        print(
            f"  Catalog: available from {source} ({catalog.get('modelCount')} models)"
        )
    else:
        reason = catalog.get("degradedReason") or "no Hamlib models available"
        print(f"  Catalog: unavailable ({reason})")

    for message in messages:
        print(f"  {message.get('code')}: {message.get('message')}")

    if not candidates:
        print("  Candidates: none")
    for candidate in candidates:
        print(
            "  "
            f"{candidate['id']}: {candidate['confidence']} confidence, "
            f"{candidate['transport']} {candidate['address']}"
        )
        suggested = candidate.get("suggestedModel") or "manual model selection"
        print(f"    Suggested: {candidate['suggestedBackend']} / {suggested}")
        evidence = candidate.get("evidence")
        assert isinstance(evidence, list)
        for item in evidence:
            assert isinstance(item, dict)
            detail = f" ({item['detail']})" if item.get("detail") else ""
            print(
                f"    Evidence: {item['source']} {item['kind']}="
                f"{item['status']}{detail}"
            )
        print(f"    Next action: {candidate['safeNextAction']}")
        if candidate.get("autoSelectable"):
            print("    Auto-selectable: yes")

    validation = payload.get("validation")
    if isinstance(validation, dict):
        print("  Read-only validation:")
        print(f"    Status: {validation['status']}")
        print(
            "    Frequency/mode: "
            f"{'readable' if validation['frequencyReadable'] else 'not readable'} / "
            f"{'readable' if validation['modeReadable'] else 'not readable'}"
        )
        print(f"    Identity evidence: {validation['identityEvidence']}")
        print("    Safety: read-only; no writes, PTT, raw CI-V, or transmit commands")
