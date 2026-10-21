"""Ground truth for an FT8 air clip: slice into 15 s slots and decode with jt9.

Reads the clip's manifest (``slot_start_wall_utc``, audio format) and WAV,
cuts the recording into UTC-aligned 15 s slots at 12 kHz mono 16-bit (the
file name carries the slot start, ``YYMMDD_HHMMSS.wav``, as jt9 expects),
runs ``jt9 -8`` over every complete slot and writes ``decodes.json`` next to
the manifest: per slot the decoded lines (jt9's own text) and their count.

The decode list contains third-party callsigns: it belongs with the private
recording, never in the repository.

    python scripts/ic7300_capture/ft8_truth.py MANIFEST.json \\
        [--jt9 ~/Downloads/wsjtx.app/Contents/MacOS/jt9] [--channel 0]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SLOT_S = 15
DEFAULT_JT9 = Path.home() / "Downloads/wsjtx.app/Contents/MacOS/jt9"


def slot_names(start_wall_utc: str, total_seconds: float) -> list[tuple[int, str]]:
    """``[(offset_s, 'YYMMDD_HHMMSS')]`` for every complete slot in the clip."""
    start = datetime.fromisoformat(start_wall_utc).astimezone(timezone.utc)
    count = int(total_seconds // SLOT_S)
    return [
        (
            i * SLOT_S,
            datetime.fromtimestamp(
                start.timestamp() + i * SLOT_S, timezone.utc
            ).strftime("%y%m%d_%H%M%S"),
        )
        for i in range(count)
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("manifest")
    parser.add_argument("--jt9", default=str(DEFAULT_JT9))
    parser.add_argument("--channel", type=int, default=0, help="0 = left, 1 = right")
    args = parser.parse_args(argv)

    manifest_path = Path(args.manifest)
    manifest = json.loads(manifest_path.read_text())
    wav = manifest_path.with_name(
        next(n for n in manifest["files"] if n.endswith(".wav"))
    )
    frames = manifest["audio"]["frames"]
    rate = manifest["audio"]["sample_rate_hz"]
    slots = slot_names(manifest["slot_start_wall_utc"], frames / rate)

    results = []
    with tempfile.TemporaryDirectory(prefix="ft8truth-") as tmp:
        tmpdir = Path(tmp)
        for offset, name in slots:
            slot_wav = tmpdir / f"{name}.wav"
            subprocess.run(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-y",
                    "-ss",
                    str(offset),
                    "-t",
                    str(SLOT_S),
                    "-i",
                    str(wav),
                    "-af",
                    f"pan=mono|c0=c{args.channel}",
                    "-ar",
                    "12000",
                    "-c:a",
                    "pcm_s16le",
                    str(slot_wav),
                ],
                check=True,
            )
            out = subprocess.run(
                [
                    args.jt9,
                    "-8",
                    "-p",
                    str(SLOT_S),
                    "-L",
                    "200",
                    "-H",
                    "4000",
                    "-d",
                    "3",
                    "-a",
                    tmp,
                    "-t",
                    tmp,
                    str(slot_wav),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            if out.returncode != 0:
                raise SystemExit(
                    f"jt9 failed on slot {name} (exit {out.returncode}): "
                    f"{(out.stderr or out.stdout)[-300:]!r}"
                )
            lines = [
                ln.strip()
                for ln in out.stdout.splitlines()
                if ln.strip() and "<DecodeFinished>" not in ln
            ]
            results.append(
                {
                    "slot": name,
                    "offset_s": offset,
                    "decodes": lines,
                    "count": len(lines),
                }
            )
    doc = {
        "jt9": args.jt9,
        "slots": results,
        "total_decodes": sum(r["count"] for r in results),
    }
    out_path = manifest_path.with_name(
        manifest_path.name.replace(".manifest.json", ".decodes.json")
    )
    out_path.write_text(json.dumps(doc, indent=2) + "\n")
    print(
        json.dumps(
            {
                "slots": len(results),
                "total_decodes": doc["total_decodes"],
                "out": out_path.name,
            }
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
