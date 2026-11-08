"""Generate scope-wire.json with rigplane.web.protocol.encode_scope_frame."""

import json
from pathlib import Path

from rigplane.scope import ScopeFrame
from rigplane.web.protocol import encode_scope_frame

cases = {
    "radio": (ScopeFrame(0, 0, 14_000_000, 14_100_000, bytes([0, 64, 128, 255]), False), 65535),
    "audio": (ScopeFrame(1, 1, 7_100_000, 7_103_000, bytes([1, 127, 254]), False), 17),
    "outOfRange": (ScopeFrame(1, 1, 144_000_000, 148_000_000, b"", out_of_range=True), 0),
}
Path(__file__).with_name("scope-wire.json").write_text(
    json.dumps({name: list(encode_scope_frame(frame, seq)) for name, (frame, seq) in cases.items()}, indent=2) + "\n"
)
