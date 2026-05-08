"""Guarded profile patch proposals from audio probe artifacts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


class AudioProfileProposalError(ValueError):
    """Raised when an artifact cannot produce a profile patch."""


@dataclass(frozen=True, slots=True)
class AudioProfileProposal:
    """A proposed TOML patch plus non-fatal evidence warnings."""

    toml: str
    warnings: list[str]


def propose_audio_profile_patch(
    artifact: Mapping[str, Any],
) -> AudioProfileProposal:
    """Build a deterministic ``[audio]`` TOML patch from passed evidence only."""

    passed = [
        result
        for result in artifact.get("results", [])
        if isinstance(result, Mapping) and result.get("status") == "pass"
    ]
    if not passed:
        raise AudioProfileProposalError("No passed probe results in artifact.")

    codec_preference: list[str] = []
    sample_rate_by_codec: dict[str, int] = {}
    tx_codec: str | None = None
    for result in passed:
        candidate = result.get("candidate")
        if not isinstance(candidate, Mapping):
            continue
        rx_codec = str(candidate.get("rx_codec") or "")
        if not rx_codec:
            continue
        sample_rate_hz = int(candidate["sample_rate_hz"])
        if rx_codec not in codec_preference:
            codec_preference.append(rx_codec)
        sample_rate_by_codec.setdefault(rx_codec, sample_rate_hz)
        tx_codec = tx_codec or str(candidate.get("tx_codec") or "")

    if not codec_preference:
        raise AudioProfileProposalError("No passed probe results with candidate data.")

    lines = [
        "[audio]",
        "codec_preference = ["
        + ", ".join(f'"{codec}"' for codec in codec_preference)
        + "]",
    ]
    if tx_codec:
        lines.append(f'tx_codec = "{tx_codec}"')
    lines.append(
        f"default_sample_rate_hz = {sample_rate_by_codec[codec_preference[0]]}"
    )
    by_codec = ", ".join(
        f"{codec} = {sample_rate_by_codec[codec]}" for codec in codec_preference
    )
    lines.append(f"sample_rate_by_codec = {{ {by_codec} }}")
    lines.append("")

    return AudioProfileProposal(
        toml="\n".join(lines),
        warnings=_artifact_warnings(artifact),
    )


def _artifact_warnings(artifact: Mapping[str, Any]) -> list[str]:
    warnings: list[str] = []
    model = str(artifact.get("model") or "").strip().lower()
    profile_id = str(artifact.get("profile_id") or "").strip().lower()
    if model in {"", "unknown"} or profile_id in {"", "unknown"}:
        warnings.append("artifact model/profile_id metadata is missing or unknown")
    metadata = artifact.get("metadata")
    firmware = None
    if isinstance(metadata, Mapping):
        firmware = (
            metadata.get("firmware_version")
            or metadata.get("firmware")
            or metadata.get("version")
        )
    if firmware in (None, ""):
        warnings.append("artifact firmware/version metadata is missing")
    return warnings


__all__ = [
    "AudioProfileProposal",
    "AudioProfileProposalError",
    "propose_audio_profile_patch",
]
