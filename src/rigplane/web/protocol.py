"""Web UI binary scope and audio codecs; scope wire format is in docs/api/scope.md."""

from __future__ import annotations

import json
import struct
from typing import Any, TypedDict

from rigplane.scope.models import (
    Coverage,
    FollowView,
    GrantedSubscription,
    ScopeTarget,
    ScopeWireFrame,
    SourceKind,
    Subscription,
    View,
)

__all__ = [
    "ScopeSourceCapabilities",
    "MSG_TYPE_AUDIO_RX",
    "MSG_TYPE_AUDIO_TX",
    "encode_audio_frame",
    "AUDIO_HEADER_SIZE",
    "AUDIO_CODEC_OPUS",
    "AUDIO_CODEC_PCM16",
    "encode_json",
    "decode_json",
    "MSG_TYPE_SCOPE_V2",
    "SCOPE_V2_HEADER_SIZE",
    "encode_scope_frame_v2",
    "decode_scope_frame_v2",
    "encode_scope_subscription",
    "decode_scope_subscription",
    "decode_scope_target",
    "decode_scope_unsubscribe",
    "encode_scope_granted",
    "decode_scope_granted",
]


class ScopeSourceCapabilities(TypedDict):
    scopeSources: list[SourceKind]
    scopeDefaultSourceId: SourceKind | None
    span_choices_hz: list[int]


# Message type constants
MSG_TYPE_AUDIO_RX: int = 0x10
MSG_TYPE_AUDIO_TX: int = 0x11


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


MSG_TYPE_SCOPE_V2 = 0x02
SCOPE_V2_HEADER = struct.Struct("<BBBBHHIIqdddd")
SCOPE_V2_HEADER_SIZE = SCOPE_V2_HEADER.size
_SOURCE_KINDS: tuple[SourceKind, ...] = ("radio", "audio_fft", "iq_fft")


def encode_scope_frame_v2(frame: ScopeWireFrame) -> bytes:
    """Encode the module header, length-prefixed identity, and optional pixels.

    Identity travels with each message, requiring no external source registry.
    """
    source_id: bytes = frame.source_id.encode("ascii")
    pixels: bytes = frame.pixels if frame.pixels is not None else b""
    return (
        SCOPE_V2_HEADER.pack(
            MSG_TYPE_SCOPE_V2,
            _SOURCE_KINDS.index(frame.source),
            int(frame.out_of_range) | (2 if frame.pixels is not None else 0),
            len(source_id),
            65535 if frame.receiver is None else frame.receiver,
            frame.width,
            frame.epoch,
            frame.seq,
            -1 if frame.sent_at_ms is None else frame.sent_at_ms,
            frame.view_start_hz,
            frame.view_end_hz,
            frame.coverage_start_hz,
            frame.coverage_end_hz,
        )
        + source_id
        + pixels
    )


def decode_scope_frame_v2(data: bytes) -> ScopeWireFrame:
    """Decode one complete v2 message; malformed messages raise ValueError."""
    if not isinstance(data, bytes) or len(data) < SCOPE_V2_HEADER_SIZE:
        raise ValueError("short v2 header")
    (
        msg,
        source,
        flags,
        length,
        receiver,
        width,
        epoch,
        seq,
        stamp,
        start,
        end,
        low,
        high,
    ) = SCOPE_V2_HEADER.unpack_from(data)
    if msg != MSG_TYPE_SCOPE_V2 or source >= len(_SOURCE_KINDS) or flags & ~3:
        raise ValueError("invalid v2 message type, source or flags")
    offset = SCOPE_V2_HEADER_SIZE + length
    if len(data) != offset + (width if flags & 2 else 0):
        raise ValueError("invalid v2 message length")
    try:
        source_id = data[SCOPE_V2_HEADER_SIZE:offset].decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("invalid source ID") from exc
    return ScopeWireFrame(
        _SOURCE_KINDS[source],
        source_id,
        None if receiver == 65535 else receiver,
        start,
        end,
        low,
        high,
        width,
        data[offset:] if flags & 2 else None,
        bool(flags & 1),
        epoch,
        seq,
        None if stamp == -1 else stamp,
    )


def _scope_view_json(view: View | FollowView) -> dict[str, Any]:
    if isinstance(view, FollowView):
        return {"follow": view.follow, "spanHz": view.span_hz}
    return {"startHz": view.start_hz, "endHz": view.end_hz}


def _parse_scope_view(value: Any) -> View | FollowView:
    if not isinstance(value, dict):
        raise ValueError("view must be an object")
    if set(value) == {"follow", "spanHz"}:
        return FollowView(value["follow"], value["spanHz"])
    if set(value) == {"startHz", "endHz"}:
        return View(value["startHz"], value["endHz"])
    raise ValueError("invalid view fields")


def encode_scope_subscription(request: Subscription) -> str:
    return encode_json(
        {
            "type": "subscribe",
            "sourceId": request.source_id,
            "receiver": request.receiver,
            "view": _scope_view_json(request.view),
            "width": request.width,
            "maxFps": request.max_fps,
        }
    )


def decode_scope_subscription(text: str) -> Subscription:
    obj = decode_json(text)
    if (
        set(obj) != {"type", "sourceId", "receiver", "view", "width", "maxFps"}
        or obj["type"] != "subscribe"
    ):
        raise ValueError("invalid subscription fields")
    return Subscription(
        obj["sourceId"],
        obj["receiver"],
        _parse_scope_view(obj["view"]),
        obj["width"],
        obj["maxFps"],
    )


def decode_scope_target(text: str) -> ScopeTarget:
    obj = decode_json(text)
    if "sourceId" not in obj or "receiver" not in obj:
        raise ValueError("missing subscription target")
    return ScopeTarget(obj["sourceId"], obj["receiver"])


def decode_scope_unsubscribe(text: str) -> ScopeTarget | None:
    obj = decode_json(text)
    if obj == {"type": "unsubscribe"}:
        return None
    if set(obj) != {"type", "sourceId", "receiver"} or obj["type"] != "unsubscribe":
        raise ValueError("invalid unsubscribe fields")
    return ScopeTarget(obj["sourceId"], obj["receiver"])


def encode_scope_granted(granted: GrantedSubscription) -> str:
    return encode_json(
        {
            "type": "granted",
            "sourceId": granted.source_id,
            "receiver": granted.receiver,
            "view": _scope_view_json(granted.view),
            "coverage": None
            if granted.coverage is None
            else _scope_view_json(granted.coverage),
            "width": granted.width,
            "fps": granted.max_fps,
        }
    )


def decode_scope_granted(text: str) -> GrantedSubscription:
    obj = decode_json(text)
    if (
        set(obj) != {"type", "sourceId", "receiver", "view", "coverage", "width", "fps"}
        or obj["type"] != "granted"
    ):
        raise ValueError("invalid granted fields")
    view = _parse_scope_view(obj["view"])
    coverage = None if obj["coverage"] is None else _parse_scope_view(obj["coverage"])
    if not isinstance(view, View) or (
        coverage is not None and not isinstance(coverage, View)
    ):
        raise ValueError("granted geometry must have fixed edges")
    return GrantedSubscription(
        obj["sourceId"],
        obj["receiver"],
        view,
        None if coverage is None else Coverage(coverage.start_hz, coverage.end_hz),
        obj["width"],
        obj["fps"],
    )
