"""Managed runtime packaging requirement documentation contract."""

from __future__ import annotations

from pathlib import Path


def test_managed_runtime_packaging_requirements_doc_tracks_v1_gates() -> None:
    doc = Path("docs/operations/managed-runtime-packaging.md")
    text = doc.read_text(encoding="utf-8")

    required_terms = [
        "libopus",
        "PortAudio",
        "pyserial",
        "pyserial-asyncio",
        "sounddevice",
        "macOS",
        "Windows",
        "Linux",
        "CoreAudio",
        "VB-Cable",
        "PipeWire",
        "PulseAudio",
        "minimum viable paid-v1 support",
        "Follow-up issues",
    ]
    for term in required_terms:
        assert term in text

    assert "rigplane-pro#" in text
    assert "rigplane-core#" in text
