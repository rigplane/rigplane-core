"""Web UI binary frame protocol for scope and audio data.

Binary Scope Frame (RFC):
    Offset  Size  Field           Description
    0       1     msg_type        0x01 = scope_frame
    1       1     receiver        0=Main, 1=Sub
    2       1     mode            0=center, 1=fixed, 2=scroll-C, 3=scroll-F
    3       4     start_freq      uint32 LE, Hz
    7       4     end_freq        uint32 LE, Hz
    11      2     sequence        uint16 LE
    13      1     flags           bit 0: out_of_range
    14      2     pixel_count     uint16 LE
    16      N     pixels          uint8[], amplitude 0-160
"""

from __future__ import annotations

import json
import struct
from typing import Any, Protocol

__all__ = [
    "MSG_TYPE_SCOPE",
    "MSG_TYPE_AUDIO_RX",
    "MSG_TYPE_AUDIO_TX",
    "SCOPE_HEADER_SIZE",
    "encode_scope_frame",
    "encode_audio_frame",
    "AUDIO_HEADER_SIZE",
    "AUDIO_CODEC_OPUS",
    "AUDIO_CODEC_PCM16",
    "encode_json",
    "decode_json",
]

# Message type constants
MSG_TYPE_SCOPE: int = 0x01
MSG_TYPE_AUDIO_RX: int = 0x10
MSG_TYPE_AUDIO_TX: int = 0x11

# Header sizes
SCOPE_HEADER_SIZE: int = 16


class ScopeFrameLike(Protocol):
    """Structural scope frame required by the web binary encoder."""

    receiver: int
    mode: int
    start_freq_hz: int
    end_freq_hz: int
    out_of_range: bool
    pixels: bytes


def encode_scope_frame(frame: ScopeFrameLike, sequence: int) -> bytes:
    """Serialize a scope frame to binary wire format (RFC).

    Args:
        frame: Complete scope frame from the radio.
        sequence: Wrapping sequence counter (uint16).

    Returns:
        16-byte header + pixel bytes.
    """
    pixel_count = len(frame.pixels)
    flags = 0x01 if frame.out_of_range else 0x00
    seq_u16 = sequence & 0xFFFF

    header = (
        bytes([MSG_TYPE_SCOPE, frame.receiver, frame.mode])
        + struct.pack("<I", frame.start_freq_hz)
        + struct.pack("<I", frame.end_freq_hz)
        + struct.pack("<H", seq_u16)
        + bytes([flags])
        + struct.pack("<H", pixel_count)
    )
    # header is exactly 3 + 4 + 4 + 2 + 1 + 2 = 16 bytes
    return header + frame.pixels


def encode_audio_frame(
    msg_type: int,
    codec: int,
    sequence: int,
    sample_rate: int,
    channels: int,
    frame_ms: int,
    payload: bytes,
) -> bytes:
    """Serialize an audio frame to binary wire format (RFC).

    Args:
        msg_type: 0x10 for RX, 0x11 for TX.
        codec: 0x01 = Opus, 0x02 = PCM16.
        sequence: Wrapping sequence counter (uint16).
        sample_rate: Sample rate / 100 as uint16 (e.g. 480 for 48000).
        channels: 1 = mono, 2 = stereo.
        frame_ms: Frame duration in ms. **Advisory** — consumers MUST
            compute buffer sizes from ``len(payload) / (sample_rate *
            channels * bytes_per_sample)`` directly and not trust this
            label for allocator decisions. The broadcaster derives the
            value from the actual payload on emit (see epic #764 / #765);
            packed as uint8 (1-255).
        payload: Codec-specific audio data.

    Returns:
        8-byte header + payload bytes.
    """
    header = struct.pack(
        "<BBHHBB",
        msg_type,
        codec,
        sequence & 0xFFFF,
        sample_rate,
        channels,
        frame_ms,
    )
    return header + payload


AUDIO_HEADER_SIZE: int = 8
AUDIO_CODEC_OPUS: int = 0x01
AUDIO_CODEC_PCM16: int = 0x02


def encode_json(msg: dict[str, Any]) -> str:
    """Serialize a JSON message dict to a string.

    Args:
        msg: Message dict (must include 'type' field).

    Returns:
        JSON string.
    """
    return json.dumps(msg, separators=(",", ":"))


def decode_json(text: str) -> dict[str, Any]:
    """Deserialize a JSON message string to a dict.

    Args:
        text: JSON text received from client.

    Returns:
        Parsed dict.

    Raises:
        ValueError: If text is not valid JSON or not a dict.
    """
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError("expected a JSON object")
    return obj
