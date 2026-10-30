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

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from rigplane.types import AudioCodec

from ._codecs import decode_ulaw_to_pcm16

if TYPE_CHECKING:
    from .lan_stream import AudioPacket

__all__ = ["PcmFrame", "packet_to_pcm"]


#: Channel count implied by each s16le-mappable wire codec (MOR-3821 A1).
#: 8-bit PCM has no mapping (no shipping profile negotiates it) and Opus
#: needs an injected decoder, so neither appears here.
_CHANNELS_BY_CODEC: dict[AudioCodec, int] = {
    AudioCodec.PCM_1CH_16BIT: 1,
    AudioCodec.PCM_2CH_16BIT: 2,
    AudioCodec.ULAW_1CH: 1,
    AudioCodec.ULAW_2CH: 2,
    AudioCodec.OPUS_1CH: 1,
    AudioCodec.OPUS_2CH: 2,
}


def packet_to_pcm(
    packet: AudioPacket,
    *,
    codec: AudioCodec,
    sample_rate: int,
    seq: int,
    opus_decoder: Callable[[bytes, int], bytes | None] | None = None,
) -> PcmFrame | None:
    """Decode one RX :class:`AudioPacket` to a :class:`PcmFrame` (MOR-3821 A1).

    The one shared packet-to-PCM helper over the existing decoders in
    :mod:`rigplane.audio._codecs`: PCM16 wire payloads pass through
    zero-copy, uLaw decodes with :func:`decode_ulaw_to_pcm16`, Opus
    decodes through the injected ``opus_decoder``. Returns ``None``
    for wire codecs with no s16le mapping here (8-bit PCM) and for
    Opus when no decoder is available; the legacy ``AudioPacket``
    carrier keeps flowing either way.
    """
    channels = _CHANNELS_BY_CODEC.get(codec)
    if channels is None:
        return None
    if codec in (AudioCodec.PCM_1CH_16BIT, AudioCodec.PCM_2CH_16BIT):
        payload = bytes(packet.data)
    elif codec in (AudioCodec.ULAW_1CH, AudioCodec.ULAW_2CH):
        payload = decode_ulaw_to_pcm16(packet.data)
    elif opus_decoder is None:
        return None
    else:
        decoded = opus_decoder(bytes(packet.data), channels)
        if decoded is None:
            return None
        payload = decoded
    return PcmFrame(
        sample_rate=int(sample_rate),
        channels=channels,
        payload=payload,
        seq=int(seq),
    )


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
