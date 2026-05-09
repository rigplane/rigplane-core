"""Audio capability probe models and evidence helpers."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterable

from rigplane.types import AudioCodec

_DEFAULT_STOCK_RADIO_LAN_RX_CODECS: tuple[AudioCodec, ...] = (
    AudioCodec.PCM_2CH_16BIT,
    AudioCodec.PCM_1CH_16BIT,
    AudioCodec.ULAW_2CH,
    AudioCodec.ULAW_1CH,
)
_DEFAULT_STOCK_RADIO_LAN_SAMPLE_RATES_HZ: tuple[int, ...] = (
    48_000,
    24_000,
    16_000,
    8_000,
)
_DIRECT_RADIO_OPUS_CODECS: frozenset[AudioCodec] = frozenset(
    {AudioCodec.OPUS_1CH, AudioCodec.OPUS_2CH}
)


class AudioProbeStatus(StrEnum):
    """Normalized result status for one probe candidate."""

    PASS = "pass"
    REJECTED = "rejected"
    FAILED = "failed"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class AudioProbeCandidate:
    """One radio-native stream combination to test."""

    rx_codec: AudioCodec
    tx_codec: AudioCodec
    sample_rate_hz: int
    rx_channels: int
    tx_channels: int
    frame_ms: int = 20
    mode: str = "rx-only"

    def to_dict(self) -> dict[str, object]:
        return {
            "rx_codec": self.rx_codec.name,
            "tx_codec": self.tx_codec.name,
            "sample_rate_hz": self.sample_rate_hz,
            "rx_channels": self.rx_channels,
            "tx_channels": self.tx_channels,
            "frame_ms": self.frame_ms,
            "mode": self.mode,
        }


@dataclass(frozen=True, slots=True)
class AudioProbeResult:
    """Observed result for a single probe candidate."""

    candidate: AudioProbeCandidate
    status: AudioProbeStatus
    phase: str
    reason: str
    rx_payload_bytes: int | None = None
    expected_rx_payload_bytes: int | None = None
    observed_packets: int | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "candidate": self.candidate.to_dict(),
            "status": self.status.value,
            "phase": self.phase,
            "reason": self.reason,
        }
        if self.rx_payload_bytes is not None:
            payload["rx_payload_bytes"] = self.rx_payload_bytes
        if self.expected_rx_payload_bytes is not None:
            payload["expected_rx_payload_bytes"] = self.expected_rx_payload_bytes
        if self.observed_packets is not None:
            payload["observed_packets"] = self.observed_packets
        if self.error:
            payload["error"] = self.error
        return payload


@dataclass(frozen=True, slots=True)
class AudioProbeArtifact:
    """Machine-readable evidence artifact emitted by audio probes."""

    model: str
    profile_id: str
    transport: str
    results: list[AudioProbeResult]
    schema_version: int = 1
    tool: str = "rigplane-audio-probe"
    metadata: dict[str, object] = field(default_factory=dict)

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "tool": self.tool,
            "model": self.model,
            "profile_id": self.profile_id,
            "transport": self.transport,
            "metadata": dict(self.metadata),
            "results": [result.to_dict() for result in self.results],
        }


def _channels_for_codec(codec: AudioCodec) -> int:
    return 2 if "_2CH" in codec.name else 1


def expected_pcm16_rx_payload_bytes(candidate: AudioProbeCandidate) -> int | None:
    """Return expected RX payload bytes for PCM16 candidates."""

    if candidate.rx_codec not in {
        AudioCodec.PCM_1CH_16BIT,
        AudioCodec.PCM_2CH_16BIT,
    }:
        return None
    return (
        candidate.sample_rate_hz
        * candidate.frame_ms
        * candidate.rx_channels
        * 2
        // 1000
    )


def build_stock_radio_lan_probe_matrix(
    *,
    rx_codecs: Iterable[AudioCodec] = _DEFAULT_STOCK_RADIO_LAN_RX_CODECS,
    sample_rates_hz: Iterable[int] = _DEFAULT_STOCK_RADIO_LAN_SAMPLE_RATES_HZ,
    tx_codec: AudioCodec = AudioCodec.PCM_1CH_16BIT,
    frame_ms: int = 20,
) -> list[AudioProbeCandidate]:
    """Build a conservative stock-Icom LAN probe matrix.

    Opus is intentionally excluded for direct stock-radio LAN paths. wfview
    server support is a separate transport concern, not a radio-native default.
    """

    candidates: list[AudioProbeCandidate] = []
    if _channels_for_codec(tx_codec) != 1 or tx_codec in _DIRECT_RADIO_OPUS_CODECS:
        return candidates
    for rx_codec in rx_codecs:
        if rx_codec in _DIRECT_RADIO_OPUS_CODECS:
            continue
        for sample_rate_hz in sample_rates_hz:
            candidates.append(
                AudioProbeCandidate(
                    rx_codec=rx_codec,
                    tx_codec=tx_codec,
                    sample_rate_hz=int(sample_rate_hz),
                    rx_channels=_channels_for_codec(rx_codec),
                    tx_channels=1,
                    frame_ms=frame_ms,
                )
            )
    return candidates


def classify_stock_radio_lan_probe_error(
    exc: Exception,
) -> tuple[AudioProbeStatus, str]:
    """Classify common Icom LAN session failures into probe statuses."""

    text = str(exc).lower()
    if "0xffffffff" in text or "conninfo" in text or "rejected" in text:
        return AudioProbeStatus.REJECTED, "conninfo-rejected"
    return AudioProbeStatus.FAILED, "runtime-error"


def profile_policy_from_probe_results(
    results: Iterable[AudioProbeResult],
) -> dict[str, object]:
    """Build a profile-policy candidate from passed probe evidence only."""

    passed = [result for result in results if result.status is AudioProbeStatus.PASS]
    codec_preference: list[str] = []
    sample_rate_by_codec: dict[str, int] = {}
    tx_codec: str | None = None
    for result in passed:
        candidate = result.candidate
        rx_name = candidate.rx_codec.name
        if rx_name not in codec_preference:
            codec_preference.append(rx_name)
        sample_rate_by_codec.setdefault(rx_name, candidate.sample_rate_hz)
        tx_codec = tx_codec or candidate.tx_codec.name

    policy: dict[str, object] = {}
    if codec_preference:
        policy["codec_preference"] = codec_preference
    if tx_codec is not None:
        policy["tx_codec"] = tx_codec
    if sample_rate_by_codec:
        policy["sample_rate_by_codec"] = sample_rate_by_codec
    return policy


__all__ = [
    "AudioProbeArtifact",
    "AudioProbeCandidate",
    "AudioProbeResult",
    "AudioProbeStatus",
    "build_stock_radio_lan_probe_matrix",
    "classify_stock_radio_lan_probe_error",
    "expected_pcm16_rx_payload_bytes",
    "profile_policy_from_probe_results",
]
