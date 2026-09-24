import json

from rigplane.radio_state import RadioState
from rigplane.web.runtime_helpers import (  # noqa: TID251 - offline diagnostic fixture
    _default_snapshot_field_status,
    build_public_state_payload,
)

state = build_public_state_payload(
    RadioState(),
    radio=None,
    revision=1,
    receiver_count=2,
    updated_at="2026-01-01T00:00:00Z",
)
statuses = _default_snapshot_field_status(2, lambda _: "missing")
print(json.dumps({"state": state, "statuses": statuses}, separators=(",", ":")))
