"""MOR-2508: reversed CI-V modes must reach the web wire in profile-label form.

The CI-V receive path stores ``Mode(...).name`` — the underscored enum token
(``"CW_R"``, ``"RTTY_R"``, ``"PSK_R"``) — into the state store
(runtime/_civ_rx.py). The public web payload is where those strings meet the
wire vocabulary shared with ``/api/v1/capabilities`` and the rig-TOML
``[modes]`` labels, which use the hyphen form (``"CW-R"``, ``"RTTY-R"``,
``"PSK-R"``). Both payload producers (StateStore snapshot and RadioState
dataclass) and every receiver view (active scalar and VFO slots) must emit
the hyphen form; the frontend mode panel matches buttons by strict string
equality against the capability labels.
"""

from __future__ import annotations

from typing import Any

import pytest

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.core.types import Mode
from rigplane.radio_state import RadioState
from rigplane.web.runtime_helpers import (
    build_public_state_payload,
    build_public_state_payload_from_snapshot,
)

_REVERSED_MODES = [
    (Mode.CW_R, "CW-R"),
    (Mode.RTTY_R, "RTTY-R"),
    (Mode.PSK_R, "PSK-R"),
]


def _observation(path: FieldPath, value: object, *, at: float) -> Observation:
    return Observation(
        path=path,
        value=value,
        source=SourceMetadata(
            source="poll_response",
            provider="mor2508_test",
            transport="fake",
            native_id="mor2508_test",
        ),
        timestamp_monotonic=at,
    )


def _snapshot_payload(mode_name: str) -> dict[str, Any]:
    store = StateStore()
    for receiver_id in ("0", "1"):
        store.apply(
            _observation(
                FieldPath.active(receiver_id, "freq_mode", "mode"),
                mode_name,
                at=1.0,
            )
        )
        store.apply(
            _observation(
                FieldPath.vfo_slot(receiver_id, "A", "freq_mode", "mode"),
                mode_name,
                at=1.1,
            )
        )
    return build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=2,
    )


@pytest.mark.parametrize(("mode", "wire_label"), _REVERSED_MODES)
def test_snapshot_payload_emits_profile_label_for_reversed_modes(
    mode: Mode, wire_label: str
) -> None:
    payload = _snapshot_payload(mode.name)
    assert payload["main"]["mode"] == wire_label
    assert payload["main"]["vfoA"]["mode"] == wire_label
    assert payload["sub"]["mode"] == wire_label
    assert payload["sub"]["vfoA"]["mode"] == wire_label


@pytest.mark.parametrize(("mode", "wire_label"), _REVERSED_MODES)
def test_dataclass_payload_emits_profile_label_for_reversed_modes(
    mode: Mode, wire_label: str
) -> None:
    state = RadioState()
    state.main.mode = mode.name
    payload = build_public_state_payload(
        state,
        radio=None,
        revision=1,
        receiver_count=1,
    )
    assert payload["main"]["mode"] == wire_label
