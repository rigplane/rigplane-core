"""Convert a captured ``.rdscope`` stream into Core's live-air fixture pair.

MOR-4388: the acceptance-matrix performance cell replays the recorded
IC-705 bench stream through the production renderers. The recorded
container (``tmp/lane/rdscope-README.md``) is ``b"RDSCOPE1\\n"`` followed by
records of ``u8 kind (1 text, 2 binary)``, ``f64 seconds since socket
open``, ``u32 payload length``, ``payload``. The output pair matches the
existing live-air fixture contract (``tests/fixtures/scope-wire/*.frames``
consumed by ``frontend/tests/e2e/renderers/waterfall-parity.spec.ts``):

* ``<out>.frames`` — ``[u32 LE length][wire-v2 frame]`` for every binary
  record, binary frames only;
* ``<out>.sidecar.json`` — the typed metadata the replay harness needs:
  the first ``granted`` text, frame count, native width, recorded view,
  duration and per-frame relative timestamps.

Usage (on the Mac mini; the .rdscope never lands on this laptop outside
``tmp/``):

    uv run python tests/e2e/panorama_matrix_fixture.py convert \
        /path/to/ic705_bench_scope_antenna_20s.rdscope \
        --out ~/validation/mor-4388/rc8/ic705_bench_antenna_20s
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

MAGIC = b"RDSCOPE1\n"


@dataclass(frozen=True)
class RdscopeRecord:
    kind: int  # 1 text, 2 binary
    at_s: float
    payload: bytes


@dataclass(frozen=True)
class FixtureSidecar:
    """Typed sidecar contract for the replay harness (mirror in TS)."""

    granted_text: str
    frames: int
    native_width: int
    view_start_hz: int
    view_end_hz: int
    duration_s: float
    frame_offsets_s: list[float]
    source: str

    def to_json(self) -> str:
        return json.dumps(asdict_shallow(self), indent=2)


def asdict_shallow(sidecar: FixtureSidecar) -> dict[str, object]:
    return {
        "grantedText": sidecar.granted_text,
        "frames": sidecar.frames,
        "nativeWidth": sidecar.native_width,
        "viewStartHz": sidecar.view_start_hz,
        "viewEndHz": sidecar.view_end_hz,
        "durationS": sidecar.duration_s,
        "frameOffsetsS": sidecar.frame_offsets_s,
        "source": sidecar.source,
    }


def read_rdscope(path: Path) -> list[RdscopeRecord]:
    raw = path.read_bytes()
    if not raw.startswith(MAGIC):
        raise ValueError(f"{path}: not an RDSCOPE1 container")
    offset = len(MAGIC)
    records: list[RdscopeRecord] = []
    while offset < len(raw):
        if offset + 13 > len(raw):
            raise ValueError(f"{path}: truncated record header at {offset}")
        kind = raw[offset]
        at_s, length = struct.unpack_from("<dI", raw, offset + 1)
        offset += 13
        if offset + length > len(raw):
            raise ValueError(f"{path}: truncated record payload at {offset}")
        records.append(RdscopeRecord(kind, at_s, raw[offset : offset + length]))
        offset += length
    return records


def native_width_of(payload: bytes) -> int:
    # Wire v2: u16 LE width follows the fixed header (scope-wire fixtures;
    # the authoritative decode lives in the TS parser the replay uses).
    (width,) = struct.unpack_from("<H", payload, 4)
    return width


def convert(source: Path, out_base: Path) -> FixtureSidecar:
    records = read_rdscope(source)
    granted = next((r for r in records if r.kind == 1), None)
    binaries = [r for r in records if r.kind == 2]
    if granted is None or not binaries:
        raise ValueError(f"{source}: no granted text or no binary frames")
    frames_blob = bytearray()
    offsets: list[float] = []
    width = 0
    for record in binaries:
        frames_blob += struct.pack("<I", len(record.payload))
        frames_blob += record.payload
        offsets.append(round(record.at_s, 4))
        width = native_width_of(record.payload) or width
    out_base.parent.mkdir(parents=True, exist_ok=True)
    (out_base.with_suffix(".frames")).write_bytes(bytes(frames_blob))
    granted_json = json.loads(granted.payload.decode("utf-8"))
    view = granted_json.get("view") or {}
    sidecar = FixtureSidecar(
        granted_text=granted.payload.decode("utf-8"),
        frames=len(binaries),
        native_width=width,
        view_start_hz=int(view.get("startHz") or 0),
        view_end_hz=int(view.get("endHz") or 0),
        duration_s=round(binaries[-1].at_s, 3),
        frame_offsets_s=offsets,
        source=str(source),
    )
    (out_base.with_suffix(".sidecar.json")).write_text(sidecar.to_json())
    return sidecar


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    convert_parser = sub.add_parser(
        "convert", help="rdscope -> .frames + .sidecar.json"
    )
    convert_parser.add_argument("source", type=Path)
    convert_parser.add_argument(
        "--out", type=Path, required=True, help="output base path without suffix"
    )
    info_parser = sub.add_parser("info", help="print a rdscope container's summary")
    info_parser.add_argument("source", type=Path)
    args = parser.parse_args(argv)
    if args.command == "info":
        records = read_rdscope(args.source)
        texts = sum(1 for r in records if r.kind == 1)
        binaries = [r for r in records if r.kind == 2]
        print(
            json.dumps(
                {
                    "text": texts,
                    "binary": len(binaries),
                    "durationS": round(binaries[-1].at_s, 3) if binaries else 0,
                    "bytes": sum(len(r.payload) for r in binaries),
                }
            )
        )
        return 0
    sidecar = convert(args.source, args.out)
    print(json.dumps(asdict_shallow(sidecar)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
