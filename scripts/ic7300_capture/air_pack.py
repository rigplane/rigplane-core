"""Pack an air capture into the ``rigplane-air/1`` directory (ADR MOR-3592 s9).

Inputs: the clip manifest and WAV written by ``air_capture.py``, the raw CI-V
recording beside it (scope wave frames included) and, for FT8, the
``*.decodes.json`` from ``ft8_truth.py``. Output directory::

    manifest.json   schema, kind, duration, audio/scope descriptions, state at
                    start, align, source, provenance
    audio.wav       the radio's native-format RX audio (copied, sha256 kept)
    scope.jsonl     {"t_samples": N, "frame": "<hex>"} per RAW radio ``27 00``
                    frame, on the audio sample clock
    truth.json      FT8 reference decodes per slot (private: callsigns)

Alignment is ``wall_clock_ms``: audio and CI-V were stamped by two clocks in
one process (monotonic anchored to wall time at the start of each), good to a
few tens of milliseconds, which the manifest says; it is not proof of
scope-to-audio timing.

    python scripts/ic7300_capture/air_pack.py CLIP.manifest.json OUT_DIR \\
        --kind hf_master [--decodes CLIP.decodes.json] [--commit SHA]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

from rigplane.commands import parse_civ_frame
from rigplane.scope import ScopeAssembler
from rigplane_testkit.serial.recorder import civ_frames

_SCOPE_CMD, _WAVE_SUB = 0x27, 0x00


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _wall(iso: str) -> float:
    return datetime.fromisoformat(iso).timestamp()


def scope_rows(
    civ_path: Path,
    *,
    radio_addr: int,
    civ_t0_wall: float,
    audio_start_wall: float,
    sample_rate: int,
    total_samples: int,
) -> tuple[list[dict], dict]:
    """Raw wave frames on the audio sample clock, plus bins/span/centre."""
    rows: list[dict] = []
    assembler = ScopeAssembler()
    geometry: dict = {}
    for t, direction, frame in civ_frames(civ_path):
        if direction != "r2c" or len(frame) < 10:
            continue
        if frame[3] != radio_addr or frame[4] != _SCOPE_CMD or frame[5] != _WAVE_SUB:
            continue
        sample = round((civ_t0_wall + t - audio_start_wall) * sample_rate)
        if not 0 <= sample < total_samples:
            continue
        rows.append({"t_samples": sample, "frame": frame.hex()})
        if not geometry:
            parsed = parse_civ_frame(frame)
            done = assembler.feed(parsed.data[1:], parsed.data[0])
            if done is not None:
                geometry = {
                    "bins": len(done.pixels),
                    "span_hz": done.end_freq_hz - done.start_freq_hz,
                    "center_hz": (done.start_freq_hz + done.end_freq_hz) // 2,
                }
    return rows, geometry


def pack(
    manifest_path: Path,
    out_dir: Path,
    *,
    kind: str,
    decodes_path: Path | None = None,
    commit: str = "unknown",
    command: str = "",
) -> Path:
    manifest = json.loads(manifest_path.read_text())
    base = manifest_path.with_name(manifest_path.name.removesuffix(".manifest.json"))
    wav = base.with_suffix(".wav")
    civ = base.with_suffix(".civ.jsonl")
    audio = manifest["audio"]
    rate = audio["sample_rate_hz"]
    total = audio["frames"]
    audio_start = _wall(audio["start_wall_utc"])

    out_dir.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(wav, out_dir / "audio.wav")
    rows, geometry = scope_rows(
        civ,
        radio_addr=manifest["radio"]["radio_addr"],
        civ_t0_wall=_wall(manifest["civ_t0_wall_utc"]),
        audio_start_wall=audio_start,
        sample_rate=rate,
        total_samples=total,
    )
    scope_file = out_dir / "scope.jsonl"
    scope_file.write_text(
        "".join(json.dumps(r, separators=(",", ":")) + "\n" for r in rows)
    )
    sweeps_rate = None
    if len(rows) > 1:
        span_s = (rows[-1]["t_samples"] - rows[0]["t_samples"]) / rate
        sweeps_rate = round((len(rows) - 1) / span_s, 3) if span_s > 0 else None

    truth: dict = {}
    if decodes_path is not None:
        truth = {"ft8": json.loads(decodes_path.read_text())}
    (out_dir / "truth.json").write_text(json.dumps(truth, indent=1) + "\n")

    tuned = manifest.get("state_tuned") or manifest.get("state_before", {})
    doc = {
        "schema": "rigplane-air/1",
        "id": f"{manifest['clip']}-{manifest['started_utc'][:10]}",
        "kind": kind,
        "duration_s": round(total / rate, 3),
        "audio": {
            "sample_rate_hz": rate,
            "channels": audio["channels"],
            "sample_format": "s16le",
            "file": "audio.wav",
            "sha256": _sha256(out_dir / "audio.wav"),
        },
        "scope": {
            "frames_file": "scope.jsonl",
            "frames": len(rows),
            "rate_hz": sweeps_rate,
            "sha256": _sha256(scope_file),
            **geometry,
        },
        "state": {
            "frequency_hz": tuned.get("freq_hz"),
            "mode": tuned.get("mode"),
            "filter": tuned.get("filter"),
            "data_mode": tuned.get("data_mode"),
        },
        "align": "wall_clock_ms",
        "source": {
            "radio": manifest["radio"]["model"],
            "firmware": None,
            "path": "usb",
            "date": manifest["started_utc"][:10],
            "note": f"{manifest['clip']} clip, {manifest.get('seconds')} s",
        },
        "provenance": {
            "tool": "scripts/ic7300_capture/air_pack.py",
            "commit": commit,
            "command": command,
        },
    }
    (out_dir / "manifest.json").write_text(json.dumps(doc, indent=1) + "\n")
    return out_dir / "manifest.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("manifest")
    parser.add_argument("out_dir")
    parser.add_argument(
        "--kind",
        choices=("hf_master", "vhf_uhf_fm_master", "calibration"),
        required=True,
    )
    parser.add_argument("--decodes")
    parser.add_argument("--commit", default="unknown")
    args = parser.parse_args(argv)
    path = pack(
        Path(args.manifest),
        Path(args.out_dir),
        kind=args.kind,
        decodes_path=Path(args.decodes) if args.decodes else None,
        commit=args.commit,
        command=" ".join(sys.argv),
    )
    print(json.dumps({"manifest": str(path)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
