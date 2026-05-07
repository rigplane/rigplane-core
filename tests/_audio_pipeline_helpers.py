"""Reusable helpers for CI-safe audio pipeline harness tests."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from rigplane.audio.lan_stream import AudioPacket, parse_audio_packet


def sine_pcm16_mono(
    frequency_hz: float,
    *,
    samples: int,
    sample_rate: int = 48_000,
    amplitude: int = 12_000,
) -> bytes:
    """Generate deterministic mono s16le sine PCM."""

    pcm = bytearray()
    for index in range(samples):
        phase = 2.0 * math.pi * frequency_hz * (index / sample_rate)
        value = int(amplitude * math.sin(phase))
        pcm += value.to_bytes(2, "little", signed=True)
    return bytes(pcm)


def pcm_rms(pcm: bytes) -> float:
    """Return RMS for mono s16le PCM bytes."""

    if not pcm:
        return 0.0
    if len(pcm) % 2:
        raise ValueError("PCM byte length must be even for s16le samples.")
    count = len(pcm) // 2
    total = 0.0
    for offset in range(0, len(pcm), 2):
        sample = int.from_bytes(pcm[offset : offset + 2], "little", signed=True)
        total += float(sample) * float(sample)
    return math.sqrt(total / count)


@dataclass(frozen=True)
class PcmDiagnostics:
    """Compact PCM diagnostics emitted by pipeline harness tests."""

    byte_count: int
    frame_count: int
    peak: int
    rms: float

    @classmethod
    def from_pcm(cls, pcm: bytes, *, frame_bytes: int = 1920) -> "PcmDiagnostics":
        peak = 0
        for offset in range(0, len(pcm), 2):
            sample = int.from_bytes(pcm[offset : offset + 2], "little", signed=True)
            peak = max(peak, abs(sample))
        return cls(
            byte_count=len(pcm),
            frame_count=len(pcm) // frame_bytes,
            peak=peak,
            rms=pcm_rms(pcm),
        )


def collect_tx_audio_packets(await_args_list: list[Any]) -> list[AudioPacket]:
    """Parse raw packets recorded by a mocked audio transport."""

    packets: list[AudioPacket] = []
    for call in await_args_list:
        raw = call.args[0]
        packet = parse_audio_packet(raw)
        if packet is not None:
            packets.append(packet)
    return packets


def assert_contiguous_sequences(packets: list[AudioPacket]) -> None:
    """Assert audio-level send_seq values increment by one with wraparound."""

    sequences = [packet.send_seq for packet in packets]
    expected = list(range(len(packets)))
    assert sequences == expected
