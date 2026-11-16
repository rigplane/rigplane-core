"""Convert an ``.rdscope`` bench recording into the replayable fixture pair.

Input container (little endian): ``b"RDSCOPE1\\n"``, then records of
u8 kind (1 text, 2 binary), f64 seconds, u32 length, payload — the
unmodified WebSocket messages of one ``/api/v1/scope`` subscription.
Output: ``<stem>.frames`` holding the binary v2 frames in the
repeated ``u32 LE length + frame`` envelope used by the renderer
browser tests, plus a ``<stem>.json`` sidecar recording the capture
facts. The granted text record and the recording itself are supplied
by the coordinator; this script never touches a radio.

Every payload is decoded with the production
``rigplane.web.protocol.decode_scope_frame_v2`` contract
(``ScopeWireFrame``); geometry, sequence and pixels are read from the
decoded fields. The envelope keeps the original bytes untouched.
"""

import argparse
import json
import struct
from pathlib import Path

from rigplane.web.protocol import decode_scope_frame_v2

MAGIC = b"RDSCOPE1\n"


def read_rdscope(path: Path) -> tuple[dict, list[bytes]]:
    data = path.read_bytes()
    if not data.startswith(MAGIC):
        raise ValueError("not an RDSCOPE1 recording")
    offset = len(MAGIC)
    granted: dict | None = None
    frames: list[bytes] = []
    while offset < len(data):
        kind = data[offset]
        offset += 1
        (seconds,) = struct.unpack_from("<d", data, offset)
        offset += 8
        (length,) = struct.unpack_from("<I", data, offset)
        offset += 4
        payload = data[offset : offset + length]
        if len(payload) != length:
            raise ValueError("truncated record")
        offset += length
        if kind == 1:
            granted = json.loads(payload)
        elif kind == 2:
            frames.append(payload)
    if granted is None:
        raise ValueError("recording has no granted text record")
    if not frames:
        raise ValueError("recording has no binary frames")
    return granted, frames


def build_fixture(frames: list[bytes]) -> tuple[bytes, dict, dict]:
    """Decode ``frames`` through the production v2 contract.

    Returns the length-prefixed envelope (original bytes), the sidecar
    facts and the printed stats. Anything that is not a well-formed v2
    payload raises ``ValueError`` from the production decoder.
    """
    decoded = [decode_scope_frame_v2(frame) for frame in frames]
    views = {(round(item.view_start_hz), round(item.view_end_hz)) for item in decoded}
    coverages = {
        (round(item.coverage_start_hz), round(item.coverage_end_hz)) for item in decoded
    }
    widths = {item.width for item in decoded}
    sequences = [item.seq for item in decoded]
    if len(views) != 1 or len(coverages) != 1 or len(widths) != 1:
        raise ValueError("recording is not a single stable geometry")
    (width,) = widths
    pixel_bytes = []
    for item in decoded:
        if item.pixels is None:
            raise ValueError("recording has a frame without pixels")
        pixel_bytes.append(item.pixels)
    pixels = [value for chunk in pixel_bytes for value in chunk]
    if len(pixels) != len(frames) * width:
        raise ValueError("frame pixel lengths disagree with the width field")

    envelope = b"".join(struct.pack("<I", len(frame)) + frame for frame in frames)
    (view,) = views
    sidecar = {
        "radio": "",
        "frequency_hz": round(sum(view) / 2),
        "mode": "",
        "span_hz": round(decoded[0].view_end_hz - decoded[0].view_start_hz),
        "granted_width": width,
        "frame_count": len(frames),
        "duration_seconds": 0,
        "recorded_at": "",
        "core_version": "",
        "load": "",
        "retained_frame_range_1_based": [1, len(frames)],
        "byte_length": len(envelope),
        "format": "Repeated u32 little-endian byte length followed by a v2 binary scope frame",
    }
    stats = {
        "frames": len(frames),
        "width": width,
        "bytes": len(envelope),
        "pixels": len(pixels),
        "zeros": sum(1 for value in pixels if value == 0),
        "max": max(pixels),
        "seq_first": min(sequences),
        "seq_last": max(sequences),
    }
    return envelope, sidecar, stats


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("rdscope", type=Path)
    parser.add_argument("stem", type=Path, help="output path without suffix")
    parser.add_argument("--radio", default="IC-705")
    parser.add_argument("--mode", default="follow-VFO")
    parser.add_argument("--load", default="bench antenna")
    parser.add_argument("--core-version", default="3.2.0rc8")
    parser.add_argument("--recorded-at", default="2026-11-09")
    parser.add_argument("--duration-seconds", type=int, default=20)
    args = parser.parse_args()

    _granted, frames = read_rdscope(args.rdscope)
    envelope, sidecar, stats = build_fixture(frames)
    args.stem.with_suffix(".frames").write_bytes(envelope)
    sidecar.update(
        {
            "radio": args.radio,
            "mode": args.mode,
            "duration_seconds": args.duration_seconds,
            "recorded_at": args.recorded_at,
            "core_version": args.core_version,
            "load": args.load,
        }
    )
    args.stem.with_suffix(".json").write_text(json.dumps(sidecar, indent=2) + "\n")
    print(json.dumps(stats))


if __name__ == "__main__":
    main()
