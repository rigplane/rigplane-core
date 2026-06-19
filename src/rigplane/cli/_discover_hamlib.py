"""Hamlib-assisted discovery payload and human-output helpers.

The payload builder (``build_hamlib_discovery_payload``) has been promoted to
the public API as of MOR-911.  The canonical import path is now::

    from rigplane.backends.discovery import build_hamlib_discovery_payload
    # or equivalently via the top-level lazy map:
    from rigplane import build_hamlib_discovery_payload

This module re-exports the symbol for backward compatibility so that any
internal code that currently imports it from here continues to work without
modification.
"""

from __future__ import annotations

# Re-export from the canonical public location.  All helpers that were private
# to this module have been moved to rigplane.backends.discovery; they are not
# part of the public API and are not re-exported here.
from rigplane.backends.discovery import build_hamlib_discovery_payload as build_hamlib_discovery_payload  # noqa: PLC0414


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
