"""Regenerate the language-neutral fixtures with the production encoder."""

import json
from dataclasses import asdict, replace
from pathlib import Path

from rigplane.scope.models import ScopeWireFrame
from rigplane.web.protocol import encode_scope_frame_v2


def fixtures() -> dict[str, ScopeWireFrame]:
    base = ScopeWireFrame(
        "radio",
        "radio:main",
        0,
        14000000,
        14100000,
        14000000,
        14100000,
        4,
        bytes([0, 127, 128, 255]),
        False,
        7,
        42,
        1700000000123,
    )
    return {
        "radio": base,
        "audio_fft": replace(
            base,
            source="audio_fft",
            source_id="audio:rx",
            receiver=None,
            view_start_hz=14070000,
            view_end_hz=14073000,
            coverage_start_hz=14070000,
            coverage_end_hz=14073000,
        ),
        "iq_fft": replace(
            base, source="iq_fft", source_id="iq:main", receiver=None, sent_at_ms=None
        ),
        "out_of_range": replace(
            base,
            out_of_range=True,
            pixels=None,
            coverage_start_hz=14000000,
            coverage_end_hz=14000000,
        ),
        "pixels_null": replace(base, pixels=None),
        "partial_coverage": replace(
            base, coverage_start_hz=14025000, coverage_end_hz=14075000
        ),
        "rigdeck_1024": replace(base, width=1024, pixels=bytes(range(256)) * 4),
        "desktop_2800": replace(
            base, width=2800, pixels=(bytes(range(256)) * 11)[:2800]
        ),
    }


def generate(destination: Path) -> None:
    for name, frame in fixtures().items():
        destination.joinpath(name + ".bin").write_bytes(encode_scope_frame_v2(frame))
        fields = asdict(frame)
        fields["pixels"] = list(frame.pixels) if frame.pixels is not None else None
        # One line per sidecar: pixels stay a JSON array (same contract),
        # just without the one-number-per-line pretty printing.
        destination.joinpath(name + ".json").write_text(
            json.dumps(fields, separators=(",", ":")) + "\n"
        )


if __name__ == "__main__":
    generate(Path(__file__).parent)
