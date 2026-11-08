"""Real PCM render fixtures retain native RF samples and observed mapping facts."""

import json
import runpy
from pathlib import Path

import numpy as np

from rigplane.web.protocol import decode_scope_frame_v2


ROOT = Path(__file__).parents[1] / "fixtures"
RENDER = ROOT / "audio-scope-render"


async def test_audio_render_generator_is_reproducible_and_reference_decodes(tmp_path):
    generator = runpy.run_path(str(RENDER / "generate.py"))["generate"]
    await generator(tmp_path)
    generated = sorted(path.name for path in tmp_path.iterdir())
    assert generated == sorted(
        path.name for path in RENDER.iterdir() if path.suffix in (".json", ".bin")
    )
    reference_decode = runpy.run_path(str(ROOT / "scope-wire/reference_decoder.py"))[
        "decode"
    ]
    for name in generated:
        assert (
            tmp_path.joinpath(name).read_bytes() == RENDER.joinpath(name).read_bytes()
        )
        if not name.endswith(".bin"):
            continue
        wire = tmp_path.joinpath(name).read_bytes()
        metadata = json.loads(
            tmp_path.joinpath(name.replace(".bin", ".json")).read_text()
        )
        assert reference_decode(wire) == metadata["frame"]
        frame = decode_scope_frame_v2(wire)
        facts, inputs = metadata["audioPassband"], metadata["input"]
        assert frame.source == frame.source_id == "audio_fft" and frame.receiver is None
        assert frame.seq == 1 and frame.epoch == 0 and frame.sent_at_ms is None
        assert frame.pixels is not None
        assert frame.width == len(frame.pixels) < 1024
        peak = int(np.argmax(np.frombuffer(frame.pixels, dtype=np.uint8)))
        rf_peak = (
            frame.view_start_hz
            + (peak + 0.5) * (frame.view_end_hz - frame.view_start_hz) / frame.width
        )
        sign = -1 if facts["sideband"] == "lower" else 1
        af_peak = sign * (rf_peak - facts["tunedHz"] - facts["carrierOffsetHz"])
        assert (
            abs(af_peak - inputs["toneHz"])
            <= 2 * inputs["sampleRateHz"] / inputs["fftSize"]
        )
