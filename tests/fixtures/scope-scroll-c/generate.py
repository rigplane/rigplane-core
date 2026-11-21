"""Regenerate the trimmed SCROLL-C journey goldens with the production encoder.

MOR-4452: what the real ``/api/v1/scope`` journey delivers for a SCROLL-C
wave whose reported window is the trimmed ±49.25 kHz sweep extent at the
±50 kHz preset — the source expands it to the preset's 100 kHz window, so
the wire document names the preset. The per-delivery sequence counter is
pinned to 0 (arrival-timing dependent); the source emits epoch 0 and no
sent time. The journey
(``tests/test_scroll_c_span_preset_wire_mor4452.py``) asserts the server
produces exactly these bytes per profile; the paired frontend component
test replays them. The journey's ``RIGPLANE_UPDATE_SCROLL_C_SCOPE_WIRE=1``
writes the same documents from a live run.
"""

import json
from dataclasses import asdict
from pathlib import Path

from rigplane.scope.models import ScopeWireFrame
from rigplane.web.protocol import encode_scope_frame_v2


def frames() -> dict[str, ScopeWireFrame]:
    frame = ScopeWireFrame(
        "radio",
        "radio",
        0,
        14_000_000,
        14_100_000,
        14_000_000,
        14_100_000,
        475,
        bytes(475),
        False,
        0,
        0,
        None,
    )
    return {"ic705": frame, "ic7300": frame}


def generate(destination: Path) -> None:
    for name, frame in frames().items():
        destination.joinpath(name + ".bin").write_bytes(encode_scope_frame_v2(frame))
        fields = asdict(frame)
        fields["pixels"] = list(frame.pixels) if frame.pixels is not None else None
        # One line per sidecar: pixels stay a JSON array (same contract as
        # the scope-wire goldens), without one-number-per-line printing.
        destination.joinpath(name + ".json").write_text(
            json.dumps(fields, separators=(",", ":")) + "\n"
        )


if __name__ == "__main__":
    generate(Path(__file__).parent)
