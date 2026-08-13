from __future__ import annotations

import tomllib
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from rigplane.core.state_pipeline_contracts import (
    DEFAULT_FIELD_REGISTRY,
    FieldFamily,
    FieldPath,
    FieldScope,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.web.runtime_helpers import build_public_state_payload_from_snapshot
from rigplane.web.state_schema import StateUpdateEnvelope

_CONTRACT_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs/internals/ui-radio-control-contract.toml"
)


def _manifest_paths() -> set[str]:
    with _CONTRACT_PATH.open("rb") as stream:
        contract = tomllib.load(stream)
    return {path for family in contract["families"] for path in family["field_paths"]}


def _connection_path(name: str) -> FieldPath:
    return FieldPath(
        scope=FieldScope.CONNECTION,
        family=FieldFamily.CONNECTION,
        name=name,
    )


def _health_path(name: str) -> FieldPath:
    return FieldPath(
        scope=FieldScope.HEALTH,
        family=FieldFamily.HEALTH,
        name=name,
    )


def _source() -> SourceMetadata:
    return SourceMetadata(
        source="poll_response",
        provider="mor1408_test",
        transport="fake",
        native_id="registry-projection",
    )


def _apply(store: StateStore, path: FieldPath, value: Any) -> None:
    store.apply(
        Observation(
            path=path,
            value=value,
            source=_source(),
            timestamp_monotonic=10.0,
            quality=("confirmed",),
        )
    )


def _public_value(payload: dict[str, Any], public_path: str) -> Any:
    value: Any = payload
    for part in public_path.split("."):
        value = value[part]
    return value


_MOR1406_CONTROL_ROWS: tuple[tuple[FieldPath, str, Any], ...] = tuple(
    row
    for receiver in ("main", "sub")
    for row in (
        (
            FieldPath.vfo_slot(receiver, "A", "freq_mode", "filter_num"),
            f"{receiver}.vfoA.filterNum",
            1,
        ),
        (
            FieldPath.vfo_slot(receiver, "A", "freq_mode", "data_mode"),
            f"{receiver}.vfoA.dataMode",
            1,
        ),
        (
            FieldPath.vfo_slot(receiver, "B", "freq_mode", "filter_num"),
            f"{receiver}.vfoB.filterNum",
            2,
        ),
        (
            FieldPath.vfo_slot(receiver, "B", "freq_mode", "data_mode"),
            f"{receiver}.vfoB.dataMode",
            2,
        ),
        (
            FieldPath.receiver(receiver, "operator_toggles", "af_mute"),
            f"{receiver}.afMute",
            True,
        ),
        (
            FieldPath.receiver(receiver, "operator_controls", "digisel_shift"),
            f"{receiver}.digiselShift",
            11,
        ),
        (
            FieldPath.receiver(receiver, "operator_toggles", "apf_on"),
            f"{receiver}.apfOn",
            True,
        ),
        (
            FieldPath.receiver(receiver, "operator_controls", "apf_freq"),
            f"{receiver}.apfFreq",
            22,
        ),
        (
            FieldPath.receiver(receiver, "slow_state", "contour"),
            f"{receiver}.contour",
            3,
        ),
        (
            FieldPath.receiver(receiver, "operator_controls", "manual_notch_width"),
            f"{receiver}.manualNotchWidth",
            4,
        ),
        (
            FieldPath.receiver(receiver, "operator_controls", "filter_shape"),
            f"{receiver}.filterShape",
            2,
        ),
        # notch_filter (MOR-1548): reclassified receiver-scoped, matching the
        # ic7610.toml cmd29 route's own per-receiver rationale.
        (
            FieldPath.receiver(receiver, "operator_controls", "notch_filter"),
            f"{receiver}.notchFilter",
            3,
        ),
    )
) + (
    (
        FieldPath.global_("tx_state", "main_sub_tracking"),
        "mainSubTracking",
        True,
    ),
    (FieldPath.global_("operator_controls", "nb_depth"), "nbDepth", 4),
    (FieldPath.global_("operator_controls", "nb_width"), "nbWidth", 5),
    (FieldPath.global_("operator_controls", "drive_gain"), "driveGain", 6),
    (FieldPath.global_("operator_controls", "dash_ratio"), "dashRatio", 7),
    (FieldPath.global_("slow_state", "scanning"), "scanning", True),
    (FieldPath.global_("slow_state", "scan_type"), "scanType", 2),
    (
        FieldPath.global_("slow_state", "scan_resume_mode"),
        "scanResumeMode",
        1,
    ),
    (
        FieldPath.global_("slow_state", "data_off_mod_input"),
        "dataOffModInput",
        1,
    ),
    (
        FieldPath.global_("slow_state", "data1_mod_input"),
        "data1ModInput",
        2,
    ),
    (
        FieldPath.global_("slow_state", "data2_mod_input"),
        "data2ModInput",
        3,
    ),
    (
        FieldPath.global_("slow_state", "data3_mod_input"),
        "data3ModInput",
        4,
    ),
    (
        FieldPath.global_("operator_controls", "ref_adjust"),
        "refAdjust",
        8,
    ),
    (
        FieldPath.global_("operator_controls", "ssb_tx_bandwidth"),
        "ssbTxBandwidth",
        2,
    ),
    (
        FieldPath.global_("slow_state", "tx_band_edges"),
        "txBandEdges",
        [{"low": 14_000_000, "high": 14_350_000}],
    ),
)

_LIFECYCLE_ROWS: tuple[FieldPath, ...] = (
    _connection_path("connected"),
    _connection_path("radio_ready"),
    _connection_path("control_connected"),
    _connection_path("status"),
    _health_path("server_reachable"),
    _health_path("radio_link"),
    _health_path("readiness"),
    _health_path("likely_cause"),
    _health_path("since_ms"),
    _health_path("last_error"),
)


def test_every_mor1406_live_control_row_has_one_canonical_registered_fieldpath() -> (
    None
):
    paths = tuple(path for path, _, _ in _MOR1406_CONTROL_ROWS) + _LIFECYCLE_ROWS
    for path in paths:
        matches = tuple(
            spec for spec in DEFAULT_FIELD_REGISTRY.fields if spec.path == path
        )
        assert len(matches) == 1, str(path)
        assert matches[0].path.name == matches[0].path.name.lower()
        assert "_" in matches[0].path.name or matches[0].path.name.islower()
    assert _manifest_paths() == {
        str(spec.path) for spec in DEFAULT_FIELD_REGISTRY.fields
    }


def test_registered_control_observation_projects_to_existing_public_leaf() -> None:
    for path, public_path, value in _MOR1406_CONTROL_ROWS:
        DEFAULT_FIELD_REGISTRY.require(path)
        store = StateStore()
        _apply(store, path, value)
        payload = build_public_state_payload_from_snapshot(
            store.snapshot(), radio=None, receiver_count=2
        )
        assert _public_value(payload, public_path) == value, str(path)
        status = payload["fieldStatus"][public_path]
        assert status["storePath"] == str(path)
        assert status["availability"] == "available"


def test_vfo_select_is_derived_from_canonical_active_identity() -> None:
    store = StateStore()
    _apply(store, FieldPath.global_("slow_state", "active"), "SUB")
    _apply(store, FieldPath.global_("slow_state", "vfo_select"), 0)

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(), radio=None, receiver_count=2
    )

    assert payload["active"] == "SUB"
    assert payload["vfoSelect"] == 1
    assert payload["fieldStatus"]["vfoSelect"]["storePath"] == (
        "global.slow_state.active"
    )


def test_yaesu_ui_capabilities_overflow_and_transport_metadata_are_not_registered() -> (
    None
):
    forbidden_names = {
        "vfo_select",
        "yaesu",
        "ui_capabilities",
        "overflow",
        "ws_clients",
        "public_state_seq",
        "transport_seq",
        "radio_detail",
    }
    registered_names = {spec.path.name for spec in DEFAULT_FIELD_REGISTRY.fields}
    assert forbidden_names.isdisjoint(registered_names)
    assert not any(
        path.rsplit(".", 1)[-1] in forbidden_names for path in _manifest_paths()
    )


def test_static_capability_metadata_is_not_mutable_store_state() -> None:
    registered_names = {spec.path.name for spec in DEFAULT_FIELD_REGISTRY.fields}
    assert {"capabilities", "ui_capabilities", "profile", "model"}.isdisjoint(
        registered_names
    )

    fields = StateUpdateEnvelope.model_fields
    assert fields["stateContractVersion"].is_required() is False
    assert fields["providerGeneration"].is_required() is False


def test_connection_health_projection_prefers_store_and_has_pr_a_fallback() -> None:
    radio = SimpleNamespace(
        connected=True,
        radio_ready=True,
        control_connected=False,
        conn_state=None,
    )
    fallback_health = {
        "serverReachable": True,
        "radioLink": "connected",
        "readiness": "ready",
        "likelyCause": "unknown",
        "sinceMs": 0,
        "lastError": None,
    }
    fallback = build_public_state_payload_from_snapshot(
        StateStore().snapshot(),
        radio=radio,
        receiver_count=1,
        radio_health=fallback_health,
    )
    assert fallback["connection"] == {
        "rigConnected": True,
        "radioReady": True,
        "controlConnected": False,
    }
    assert fallback["radioDetail"] == {"status": "connected"}
    assert fallback["radioHealth"] == fallback_health

    canonical = {
        _connection_path("connected"): False,
        _connection_path("radio_ready"): False,
        _connection_path("control_connected"): True,
        _connection_path("status"): "reconnecting",
        _health_path("server_reachable"): True,
        _health_path("radio_link"): "reconnecting",
        _health_path("readiness"): "recovering",
        _health_path("likely_cause"): "radio_network_lost",
        _health_path("since_ms"): 1250,
        _health_path("last_error"): "link reset",
    }
    store = StateStore()
    for path, value in canonical.items():
        _apply(store, path, value)

    projected = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=radio,
        receiver_count=1,
        radio_health=fallback_health,
    )
    assert projected["connection"] == {
        "rigConnected": False,
        "radioReady": False,
        "controlConnected": True,
    }
    assert projected["radioDetail"] == {"status": "reconnecting"}
    assert projected["radioHealth"] == {
        "serverReachable": True,
        "radioLink": "reconnecting",
        "readiness": "recovering",
        "likelyCause": "radio_network_lost",
        "sinceMs": 1250,
        "lastError": "link reset",
    }
    for path in canonical:
        DEFAULT_FIELD_REGISTRY.require(path)
