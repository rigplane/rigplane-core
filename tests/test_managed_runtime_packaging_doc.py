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
        "RIGPLANE_OS_AUDIO_TX_DEVICE",
        "RIGPLANE_OS_AUDIO_RX_DEVICE",
        "USB Audio CODEC: Audio (hw:2,0)",
        "Microphone (USB Audio CODEC)",
        "Follow-up issues",
    ]
    for term in required_terms:
        assert term in text

    assert "rigplane-pro#" in text
    assert "rigplane-core#" in text
