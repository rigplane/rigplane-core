"""PcmFrame — codec-neutral s16le carrier for the PCM audio spine.

ADR §3.5 (``docs/plans/2026-06-09-target-audio-architecture.md``, tenet
T1): transport adapters decode the radio-negotiated wire codec ONCE at
ingress and publish PCM s16le frames, so the spine (bus / DSP / taps /
bridge) never has to know a compressed codec exists. During the
migration the ingress dual-publishes: the legacy
:class:`~rigplane.audio.lan_stream.AudioPacket` (Pro-pinned export)
keeps flowing unchanged alongside this carrier (MOR-591).
"""

from __future__ import annotations

from dataclasses import dataclass

__all__ = ["PcmFrame"]


@dataclass(frozen=True, slots=True)
class PcmFrame:
    """One decoded PCM audio frame on the spine (MOR-591, ADR §3.5).

    Attributes:
        sample_rate: Sample rate in Hz of the decoded payload.
        channels: Interleaved channel count (1 or 2).
        payload: Interleaved signed 16-bit little-endian PCM bytes —
            ALWAYS s16le, regardless of the radio's negotiated wire
            codec. For PCM16-native radios this is the wire payload
            itself (zero-copy passthrough, no decode).
        seq: Locally counted monotonic frame sequence for the current
            RX session (starts at 0 per session; never wraps — unlike
            the uint16 ``AudioPacket.send_seq``).
    """

    sample_rate: int
    channels: int
    payload: bytes
    seq: int
