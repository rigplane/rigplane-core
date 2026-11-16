"""MOR-4383: convert_rdscope decodes through the production v2 contract.

The converter used to slice the wire with raw ``struct`` offsets; it now
decodes every payload with ``decode_scope_frame_v2`` (typed
``ScopeWireFrame``) and only keeps the original bytes for the envelope.
These tests build their inputs through the production *encoder*, so the
converter is pinned to the producer contract instead of a parallel
struct layout — including rejection of anything that is not v2.
"""

import json
import runpy
import struct
from pathlib import Path

import pytest

from rigplane.scope.models import ScopeWireFrame
from rigplane.web.protocol import decode_scope_frame_v2, encode_scope_frame_v2

ROOT = Path(__file__).parents[1] / "fixtures" / "scope-wire"
CONVERTER = runpy.run_path(str(ROOT / "convert_rdscope.py"))
read_rdscope = CONVERTER["read_rdscope"]
build_fixture = CONVERTER["build_fixture"]
MAGIC = CONVERTER["MAGIC"]


def _frame(seq: int, pixels: bytes, **overrides) -> ScopeWireFrame:
    base = {
        "source": "radio",
        "source_id": "radio",
        "receiver": 0,
        "view_start_hz": 14_000_000.0,
        "view_end_hz": 14_100_000.0,
        "coverage_start_hz": 14_000_000.0,
        "coverage_end_hz": 14_100_000.0,
        "width": len(pixels),
        "pixels": pixels,
        "out_of_range": False,
        "epoch": 1,
        "seq": seq,
        "sent_at_ms": None,
    }
    base.update(overrides)
    return ScopeWireFrame(**base)  # type: ignore[arg-type]


def _recording(frames: list[bytes]) -> bytes:
    granted = json.dumps({"type": "granted"}).encode()
    out = bytearray(MAGIC)
    out += b"\x01" + struct.pack("<d", 0.0) + struct.pack("<I", len(granted)) + granted
    for index, frame in enumerate(frames):
        out += b"\x02" + struct.pack("<d", float(index)) + struct.pack("<I", len(frame)) + frame
    return bytes(out)


def test_build_fixture_keeps_producer_bytes_and_contract_fields() -> None:
    wire = [
        encode_scope_frame_v2(_frame(7, bytes([10, 20, 30, 40]))),
        encode_scope_frame_v2(_frame(8, bytes([50, 60, 70, 80]))),
    ]
    envelope, sidecar, stats = build_fixture(wire)
    assert envelope == b"".join(struct.pack("<I", len(frame)) + frame for frame in wire)
    assert sidecar["granted_width"] == 4
    assert sidecar["frame_count"] == 2
    assert sidecar["frequency_hz"] == 14_050_000
    assert sidecar["span_hz"] == 100_000
    assert stats == {
        "frames": 2,
        "width": 4,
        "bytes": len(envelope),
        "pixels": 8,
        "zeros": 0,
        "max": 80,
        "seq_first": 7,
        "seq_last": 8,
    }
    first = decode_scope_frame_v2(wire[0])
    assert (first.view_start_hz, first.view_end_hz) == (14_000_000.0, 14_100_000.0)
    assert first.pixels == bytes([10, 20, 30, 40])


def test_read_rdscope_round_trip_through_encoder(tmp_path: Path) -> None:
    wire = [encode_scope_frame_v2(_frame(seq, bytes([seq, seq + 1]))) for seq in (3, 4, 5)]
    recording = tmp_path / "bench.rdscope"
    recording.write_bytes(_recording(wire))
    _granted, frames = read_rdscope(recording)
    assert frames == wire
    envelope, _sidecar, stats = build_fixture(frames)
    assert stats["seq_first"] == 3 and stats["seq_last"] == 5
    assert envelope == b"".join(struct.pack("<I", len(frame)) + frame for frame in wire)


def test_rejects_non_v2_payload() -> None:
    good = encode_scope_frame_v2(_frame(1, bytes([9, 9])))
    wrong_type = b"\x01" + good[1:]
    with pytest.raises(ValueError):
        build_fixture([wrong_type])
    with pytest.raises(ValueError):
        build_fixture([good[:40]])
    with pytest.raises(ValueError):
        build_fixture([good + b"\x00"])


def test_committed_bench_frames_rebuild_byte_identical() -> None:
    """The committed .frames fixtures survive the new code path untouched."""
    for stem in ("ic705_bench_antenna_20s", "ic705_air_20m_ref-10", "ic705_air_20m_ref+10"):
        raw = (ROOT / f"{stem}.frames").read_bytes()
        frames: list[bytes] = []
        offset = 0
        while offset < len(raw):
            (length,) = struct.unpack_from("<I", raw, offset)
            offset += 4
            frames.append(raw[offset : offset + length])
            offset += length
        assert offset == len(raw) and frames
        envelope, _sidecar, stats = build_fixture(frames)
        assert envelope == raw
        assert stats["frames"] == len(frames)
