"""Conformance gate: real public payloads validate against ServerStatePublic.

This keeps the dict producers (``runtime_helpers.build_public_state_payload`` /
``build_public_state_payload_from_snapshot``) in lockstep with the canonical
pydantic schema (``state_schema.ServerStatePublic``) that drives TypeScript
codegen (MOR-881). The producers are NOT changed to return the model — this
test is the tie-in. If a producer emits a field the schema does not allow (or
omits a required field), ``model_validate`` fails here.

``pydantic`` is a dev/optional dependency; skip cleanly when absent so the
core runtime install (which excludes pydantic) is unaffected.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("pydantic")

from rigplane.core.radio_state import RadioState  # noqa: E402
from rigplane.core.state_pipeline_contracts import (  # noqa: E402
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessClock, StateStore  # noqa: E402
from rigplane.web.runtime_helpers import (  # noqa: E402
    build_public_state_payload,
    build_public_state_payload_from_snapshot,
)
from rigplane.web.state_schema import ServerStatePublic  # noqa: E402


def _source() -> SourceMetadata:
    return SourceMetadata(
        source="poll_response",
        provider="test",
        transport="fake",
        native_id="test",
    )


def _observation(path: FieldPath, value: Any, *, at: float) -> Observation:
    return Observation(
        path=path,
        value=value,
        source=_source(),
        timestamp_monotonic=at,
        max_age=None,
        quality=("confirmed",),
    )


@pytest.mark.parametrize("receiver_count", [1, 2])
def test_dataclass_path_conforms(receiver_count: int) -> None:
    """``build_public_state_payload`` (RadioState/to_dict path) validates."""
    payload = build_public_state_payload(
        RadioState(),
        radio=None,
        revision=7,
        receiver_count=receiver_count,
    )
    # Single-receiver payloads drop ``sub`` (runtime_helpers L940).
    assert ("sub" in payload) == (receiver_count >= 2)
    ServerStatePublic.model_validate(payload)


@pytest.mark.parametrize("receiver_count", [1, 2])
def test_snapshot_path_conforms(receiver_count: int) -> None:
    """``build_public_state_payload_from_snapshot`` validates.

    Exercises the path-specific fields (``fieldStatus``, ``dcd`` + its
    deprecated ``sMeterSqlOpen`` alias, observed ``quality``) that only the
    snapshot path emits.
    """
    clock = FreshnessClock()
    store = StateStore(freshness_clock=clock)
    store.apply(
        _observation(
            FieldPath.active("0", "freq_mode", "freq_hz"),
            14_074_000,
            at=clock.now(),
        )
    )
    store.apply(
        _observation(
            FieldPath.receiver("0", "operator_toggles", "dcd"),
            True,
            at=clock.now(),
        )
    )
    store.apply(
        _observation(
            FieldPath.receiver("0", "meters", "s_meter"),
            120,
            at=clock.now(),
        )
    )
    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=receiver_count,
    )

    # Snapshot-path-only fields really emitted.
    assert "fieldStatus" in payload
    assert payload["main"]["dcd"] is True
    assert payload["main"]["sMeterSqlOpen"] is True
    # The observed entry carries the ``quality`` string[] absent from old TS.
    observed = next(v for v in payload["fieldStatus"].values() if v.get("observed"))
    assert isinstance(observed["quality"], list)

    ServerStatePublic.model_validate(payload)
