"""Contracts for backend-neutral radio state pipeline values."""

from __future__ import annotations

import json

import pytest

from rigplane.core.state_pipeline_contracts import (
    CapabilityMetadata,
    DEFAULT_FIELD_REGISTRY,
    ChangeSet,
    CommandIntent,
    CommandLifecycleEvent,
    FieldChange,
    FieldFamily,
    FieldPath,
    FieldRegistry,
    Observation,
    SourceMetadata,
)


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("path", "expected"),
    [
        (
            "receiver.main.slot.A.freq_mode.freq_hz",
            FieldPath.vfo_slot("main", "A", "freq_mode", "freq_hz"),
        ),
        (
            "receiver.sub.active.freq_mode.mode",
            FieldPath.active("sub", "freq_mode", "mode"),
        ),
        ("receiver.main.vfo.active_slot", FieldPath.active_slot("main")),
        ("global.tx_state.ptt", FieldPath.global_("tx_state", "ptt")),
        (
            "receiver.main.meters.s_meter",
            FieldPath.receiver("main", "meters", "s_meter"),
        ),
        (
            "scope_controls.receiver.main.display.span",
            FieldPath.scope_control("display", "span", receiver_id="main"),
        ),
        (
            "scope_controls.global.display.speed",
            FieldPath.scope_control("display", "speed"),
        ),
    ],
)
def test_field_path_parse_and_format_round_trip(
    path: str,
    expected: FieldPath,
) -> None:
    parsed = FieldPath.parse(path)

    assert parsed == expected
    assert str(parsed) == path
    assert parsed.to_dict()["path"] == path
    assert FieldPath.from_dict(parsed.to_dict()) == expected


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "path",
    [
        "",
        "receiver.main.freq_hz",
        "receiver.main.slot.C.freq_mode.freq_hz",
        "receiver.main.active_slot",
        "global.ptt",
        "scope_controls.main.display.span",
        "scope_controls.global.meters.s_meter",
        "scope_controls.receiver.main.meters.s_meter",
        "receiver.MAIN.meters.s_meter",
        "receiver.main.meters.s-meter",
    ],
)
def test_invalid_field_paths_are_rejected(path: str) -> None:
    with pytest.raises(ValueError):
        FieldPath.parse(path)


def test_registry_rejects_duplicate_and_ambiguous_paths() -> None:
    first = FieldPath.global_("tx_state", "ptt")
    duplicate = FieldPath.parse("global.tx_state.ptt")
    ambiguous = FieldPath.global_("slow_state", "ptt")

    with pytest.raises(ValueError, match="duplicate field path"):
        FieldRegistry.from_paths([first, duplicate])

    with pytest.raises(ValueError, match="ambiguous field name"):
        FieldRegistry.from_paths([first, ambiguous])


def test_default_registry_contains_representative_state_families() -> None:
    examples = {
        "frequency": FieldPath.active("main", "freq_mode", "freq_hz"),
        "mode": FieldPath.active("main", "freq_mode", "mode"),
        "s-meter": FieldPath.receiver("main", "meters", "s_meter"),
        "alc": FieldPath.global_("meters", "alc"),
        "power": FieldPath.global_("meters", "power"),
        "nr": FieldPath.receiver("main", "operator_toggles", "nr"),
        "nb": FieldPath.receiver("main", "operator_toggles", "nb"),
        "volume": FieldPath.receiver("main", "operator_controls", "af_level"),
        "rf gain": FieldPath.receiver("main", "operator_controls", "rf_gain"),
        "pbt": FieldPath.receiver("main", "operator_controls", "pbt_inner"),
    }

    for path in examples.values():
        assert DEFAULT_FIELD_REGISTRY.require(path).path == path

    assert (
        DEFAULT_FIELD_REGISTRY.require(examples["s-meter"]).family is FieldFamily.METERS
    )
    assert DEFAULT_FIELD_REGISTRY.require(examples["alc"]).family is FieldFamily.METERS
    assert (
        DEFAULT_FIELD_REGISTRY.require(examples["frequency"]).family
        is FieldFamily.FREQ_MODE
    )
    assert (
        DEFAULT_FIELD_REGISTRY.require(examples["volume"]).family
        is FieldFamily.OPERATOR_CONTROLS
    )


def test_observation_and_changeset_serialization_round_trip() -> None:
    source = SourceMetadata(
        source="civ_unsolicited",
        provider="icom",
        transport="lan",
        native_id="0x15/0x02",
    )
    observation = Observation(
        path=FieldPath.receiver("main", "meters", "s_meter"),
        value=82,
        source=source,
        timestamp_monotonic=42.5,
        quality=("confirmed",),
        correlation_id="rx-1",
        max_age=0.5,
    )
    changeset = ChangeSet(
        revision=7,
        freshness_revision=3,
        observation_seq=12,
        changes=(
            FieldChange(
                path=observation.path,
                previous=80,
                current=82,
            ),
        ),
        timestamp_monotonic=42.5,
        sources=(source,),
        coalesced=False,
    )

    payload = json.loads(json.dumps(changeset.to_dict()))

    assert (
        Observation.from_dict(json.loads(json.dumps(observation.to_dict())))
        == observation
    )
    assert ChangeSet.from_dict(payload) == changeset


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    "payload",
    [
        {"path": "receiver.main.meters.s_meter", "source": {}, "timestampMonotonic": 1.0},
        {"path": "receiver.main.meters.s_meter", "previous": 1},
        {"path": "receiver.main.meters.s_meter", "current": 2},
    ],
)
def test_value_bearing_contracts_reject_missing_required_values(
    payload: dict[str, object],
) -> None:
    if "source" in payload:
        with pytest.raises(KeyError):
            Observation.from_dict(payload)
    else:
        with pytest.raises(KeyError):
            FieldChange.from_dict(payload)


def test_contract_bool_fields_reject_non_bool_payloads() -> None:
    capability_payload = CapabilityMetadata(
        path=FieldPath.active("main", "freq_mode", "freq_hz"),
        sources=("civ_unsolicited",),
    ).to_dict()
    capability_payload["readable"] = "false"

    changeset_payload = ChangeSet(
        revision=1,
        freshness_revision=1,
        observation_seq=1,
        changes=(),
        timestamp_monotonic=1.0,
        sources=(),
        coalesced=False,
    ).to_dict()
    changeset_payload["coalesced"] = "false"

    with pytest.raises(TypeError):
        CapabilityMetadata.from_dict(capability_payload)
    with pytest.raises(TypeError):
        ChangeSet.from_dict(changeset_payload)


def test_command_intent_and_lifecycle_event_serialization_round_trip() -> None:
    intent = CommandIntent(
        id="cmd-123",
        name="set_freq",
        params={"freq_hz": 14_074_000},
        source="rigctld",
        target=FieldPath.active("main", "freq_mode", "freq_hz"),
        priority="user",
        timeout=2.0,
        pending_policy="scoped",
        expected_observations=(FieldPath.active("main", "freq_mode", "freq_hz"),),
    )
    event = CommandLifecycleEvent(
        command_id=intent.id,
        state="confirmed",
        timestamp_monotonic=45.0,
        source="rigctld",
        target=intent.target,
        message="confirmed by matching observation",
        details={"revision": 8},
    )

    assert CommandIntent.from_dict(json.loads(json.dumps(intent.to_dict()))) == intent
    assert (
        CommandLifecycleEvent.from_dict(json.loads(json.dumps(event.to_dict())))
        == event
    )


def test_capability_metadata_serialization_round_trip() -> None:
    capability = CapabilityMetadata(
        path=FieldPath.active("main", "freq_mode", "freq_hz"),
        sources=("civ_unsolicited", "poll_response"),
        readable=True,
        writable=True,
        unsolicited=True,
        max_age=2.5,
    )

    assert (
        CapabilityMetadata.from_dict(json.loads(json.dumps(capability.to_dict())))
        == capability
    )
