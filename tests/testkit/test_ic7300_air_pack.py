"""``air_pack.py``: an air capture becomes a ``rigplane-air/1`` directory."""

from __future__ import annotations

import importlib.util
import json
import sys
import wave
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_7610 = REPO_ROOT / "tests" / "fixtures" / "ic7610" / "civ_pairs.jsonl"


def _load():
    path = REPO_ROOT / "scripts" / "ic7300_capture" / "air_pack.py"
    spec = importlib.util.spec_from_file_location("ic7300_air_pack", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


air_pack = _load()


def _wave_template() -> bytes:
    """A real single-packet 27 00 frame (IC-7610 recording), re-addressed to 0x94."""
    for line in FIXTURE_7610.read_text().splitlines():
        row = json.loads(line)
        frame = bytes.fromhex(row["frame"])
        if (
            row["dir"] == "r2c"
            and frame[4:6] == b"\x27\x00"
            and frame[7:9] == b"\x01\x01"
        ):
            return frame[:3] + b"\x94" + frame[4:]
    raise AssertionError("no standalone wave frame in the IC-7610 fixture")


def test_pack_puts_scope_frames_on_the_audio_sample_clock(tmp_path) -> None:
    rate, seconds = 8000, 4
    wav_path = tmp_path / "ic7300-ft8-X.wav"
    with wave.open(str(wav_path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * 2 * rate * seconds)

    frame = _wave_template().hex()
    civ = tmp_path / "ic7300-ft8-X.civ.jsonl"
    civ.write_text(
        "".join(
            json.dumps({"t": t, "dir": "r2c", "port": "serial", "hex": frame}) + "\n"
            for t in (0.5, 1.5, 2.5, 99.0)  # the last falls outside the audio
        )
    )
    manifest = {
        "clip": "ft8",
        "radio": {"model": "IC-7300", "radio_addr": 0x94},
        "started_utc": "2026-10-21T13:00:00+00:00",
        "seconds": seconds,
        "civ_t0_wall_utc": "2026-10-21T13:00:00+00:00",
        "audio": {
            "sample_rate_hz": rate,
            "channels": 2,
            "frames": rate * seconds,
            "start_wall_utc": "2026-10-21T13:00:00.250000+00:00",
        },
        "state_tuned": {"freq_hz": 14_074_000, "mode": "USB", "filter": 1},
    }
    manifest_path = tmp_path / "ic7300-ft8-X.manifest.json"
    manifest_path.write_text(json.dumps(manifest))

    out = air_pack.pack(manifest_path, tmp_path / "air", kind="hf_master")
    doc = json.loads(out.read_text())
    rows = [
        json.loads(line)
        for line in (tmp_path / "air" / "scope.jsonl").read_text().splitlines()
    ]

    # civ t=0.5 is wall 13:00:00.5, audio started at .25 -> 0.25 s -> sample 2000.
    assert [r["t_samples"] for r in rows] == [2000, 10000, 18000]
    assert doc["schema"] == "rigplane-air/1" and doc["align"] == "wall_clock_ms"
    assert doc["duration_s"] == seconds
    assert doc["scope"]["frames"] == 3 and doc["scope"]["rate_hz"] == 1.0
    assert doc["scope"]["bins"] > 0 and doc["scope"]["span_hz"] > 0
    assert doc["audio"]["sample_rate_hz"] == rate
    assert doc["state"]["frequency_hz"] == 14_074_000
    assert (tmp_path / "air" / "truth.json").exists()


def test_ft8_truth_refuses_a_clip_whose_audio_is_invalid(tmp_path) -> None:
    import pytest

    path = REPO_ROOT / "scripts" / "ic7300_capture" / "ft8_truth.py"
    spec = importlib.util.spec_from_file_location("ic7300_ft8_truth", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    manifest = tmp_path / "x.manifest.json"
    manifest.write_text(
        json.dumps({"files": {"x.wav": {}}, "audio": {"valid": False, "frames": 1}})
    )
    with pytest.raises(SystemExit, match="invalid"):
        mod.main([str(manifest)])
