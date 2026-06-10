"""Contracts for backend-neutral radio state pipeline values."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from rigplane.core.state_store import StateSnapshot, StateStore
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
    FieldSpec,
    Observation,
    SourceMetadata,
)
from rigplane.radio_state import RadioState
from rigplane.web.radio_poller import RadioPoller
from rigplane.web.runtime_helpers import build_public_state_payload_from_snapshot
from rigplane.web.server import WebConfig, WebServer

_LEGACY_POLLER_REVISION = 987_654


class _StateStoreRadio:
    capabilities: set[str] = set()
    connected = False
    control_connected = False
    radio_ready = False
    backend_id = "contract_test"

    def __init__(self, store: StateStore, legacy_state: RadioState) -> None:
        self._store = store
        self.radio_state = legacy_state

    @property
    def state_store(self) -> StateStore:
        return self._store


class _LegacyRevisionPoller:
    revision = _LEGACY_POLLER_REVISION

    def bump_revision(self) -> None:
        raise AssertionError("web delivery attempted to bump legacy poller revision")


class _FakeWriter:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None


def _response_json(writer: _FakeWriter) -> tuple[int, dict[str, Any]]:
    text = writer.buffer.decode("ascii", errors="replace")
    status = int(text.split(" ", 2)[1])
    body_start = text.index("\r\n\r\n") + 4
    return status, json.loads(text[body_start:] or "{}")


def _state_store_source() -> SourceMetadata:
    return SourceMetadata(
        source="poll_response",
        provider="contract_test",
        transport="fake",
        native_id="contract_test",
    )


def _store_observation(
    path: FieldPath,
    value: object,
    *,
    at: float,
) -> Observation:
    return Observation(
        path=path,
        value=value,
        source=_state_store_source(),
        timestamp_monotonic=at,
    )


def _server_with_conflicting_legacy_state() -> tuple[WebServer, StateSnapshot]:
    store = StateStore()
    store.apply(
        _store_observation(
            FieldPath.active("0", "freq_mode", "freq_hz"),
            14_074_000,
            at=1.0,
        )
    )
    store.apply(
        _store_observation(
            FieldPath.active("0", "freq_mode", "mode"),
            "USB",
            at=1.1,
        )
    )
    store.apply(
        _store_observation(
            FieldPath.global_("tx_state", "ptt"),
            False,
            at=1.2,
        )
    )
    snapshot = store.snapshot()
    legacy_state = RadioState()
    legacy_state.main.freq = 14_250_000
    legacy_state.main.mode = "LSB"
    legacy_state.ptt = True
    server = WebServer(
        _StateStoreRadio(store, legacy_state),
        WebConfig(state_diagnostics=True),
    )
    server._radio_state = legacy_state  # noqa: SLF001
    server._radio_poller = _LegacyRevisionPoller()  # noqa: SLF001
    return server, snapshot


def _assert_delivered_from_snapshot(
    payload: dict[str, Any],
    snapshot: StateSnapshot,
) -> None:
    assert payload["revision"] == snapshot.state_revision
    assert payload["stateRevision"] == snapshot.state_revision
    assert payload["freshnessRevision"] == snapshot.freshness_revision
    assert payload["revision"] != _LEGACY_POLLER_REVISION
    assert payload["main"]["freqHz"] == 14_074_000
    assert payload["main"]["mode"] == "USB"
    assert payload["ptt"] is False


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


def test_global_dial_lock_registered_as_tx_state_bool() -> None:
    """MOR-455: ``global.tx_state.dial_lock`` is a registered tx_state bool.

    The observation-backed FTX-1 dial-lock field (CAT ``LK``) needs a canonical
    FieldSpec so the acquisition profile and store accept it. It is a global
    tx_state bool, alongside the clarifier RIT/XIT flags.
    """
    path = FieldPath.global_("tx_state", "dial_lock")
    spec = DEFAULT_FIELD_REGISTRY.require(path)
    assert spec.path == path
    assert spec.family is FieldFamily.TX_STATE
    assert spec.value_type == "bool"
    assert spec.writable is True


def test_global_scope_control_display_leaves_registered() -> None:
    """MOR-557: the public scope-control leaves have canonical FieldSpecs.

    The 0x27 ingress decoder emits observations at
    ``scope_controls.global.display.<leaf>`` for every leaf the web layer
    publishes as ``scopeControls.*``; each needs a registered read-only spec.
    """
    expected = {
        "receiver": "int",
        "dual": "bool",
        "mode": "int",
        "span": "int",
        "edge": "int",
        "hold": "bool",
        "ref_db": "float",
        "speed": "int",
    }
    for name, value_type in expected.items():
        path = FieldPath.scope_control("display", name)
        spec = DEFAULT_FIELD_REGISTRY.require(path)
        assert spec.path == path
        assert spec.family is FieldFamily.DISPLAY
        assert spec.value_type == value_type
        assert spec.writable is False


def test_global_key_speed_registered_as_operator_control_int() -> None:
    """MOR-456: ``global.operator_controls.key_speed`` is a registered int.

    The observation-backed FTX-1 keyer-speed field (CAT ``KS``) needs a
    canonical FieldSpec so the acquisition profile and store accept it. It is a
    global operator-control int (WPM), alongside ``cw_pitch``.
    """
    path = FieldPath.global_("operator_controls", "key_speed")
    spec = DEFAULT_FIELD_REGISTRY.require(path)
    assert spec.path == path
    assert spec.family is FieldFamily.OPERATOR_CONTROLS
    assert spec.value_type == "int"
    assert spec.writable is True


def test_global_break_in_registered_as_operator_control_int() -> None:
    """MOR-456: ``global.operator_controls.break_in`` is a registered int.

    ``get_break_in`` returns a :class:`BreakInMode` ``IntEnum``, but the legacy
    poller and ``RadioState.break_in`` store it as a plain int
    (``1 if get_break_in() else 0``; ``0=off, 1=semi, 2=full``) and the web
    projection passes that int straight through with no ``.value``/``str()``.
    The canonical neutral type is therefore ``int``, not an enum/str.
    """
    path = FieldPath.global_("operator_controls", "break_in")
    spec = DEFAULT_FIELD_REGISTRY.require(path)
    assert spec.path == path
    assert spec.family is FieldFamily.OPERATOR_CONTROLS
    assert spec.value_type == "int"
    assert spec.writable is True


def test_global_break_in_delay_registered_as_operator_control_int() -> None:
    """MOR-456: ``global.operator_controls.break_in_delay`` is a registered int.

    The observation-backed FTX-1 QSK-delay field (CAT ``SD``) needs a canonical
    FieldSpec. It is a global operator-control int (milliseconds).
    """
    path = FieldPath.global_("operator_controls", "break_in_delay")
    spec = DEFAULT_FIELD_REGISTRY.require(path)
    assert spec.path == path
    assert spec.family is FieldFamily.OPERATOR_CONTROLS
    assert spec.value_type == "int"
    assert spec.writable is True


def test_global_vox_gain_registered_as_operator_control_int() -> None:
    """MOR-459: ``global.operator_controls.vox_gain`` is a registered int.

    The observation-backed VOX gain (Icom CI-V ``get_vox_gain`` 0x14 0x16) is
    promoted to a backend-neutral operator-control int on the interim device
    scale (cross-vendor calibration tracked in MOR-453).
    """
    path = FieldPath.global_("operator_controls", "vox_gain")
    spec = DEFAULT_FIELD_REGISTRY.require(path)
    assert spec.path == path
    assert spec.family is FieldFamily.OPERATOR_CONTROLS
    assert spec.value_type == "int"
    assert spec.writable is True


def test_global_anti_vox_gain_registered_as_operator_control_int() -> None:
    """MOR-459: ``global.operator_controls.anti_vox_gain`` is a registered int.

    The observation-backed anti-VOX gain (Icom CI-V ``get_anti_vox_gain``
    0x14 0x17) is a backend-neutral operator-control int on the interim device
    scale (cross-vendor calibration tracked in MOR-453).
    """
    path = FieldPath.global_("operator_controls", "anti_vox_gain")
    spec = DEFAULT_FIELD_REGISTRY.require(path)
    assert spec.path == path
    assert spec.family is FieldFamily.OPERATOR_CONTROLS
    assert spec.value_type == "int"
    assert spec.writable is True


def test_global_vox_delay_registered_as_operator_control_int() -> None:
    """MOR-459: ``global.operator_controls.vox_delay`` is a registered int.

    The observation-backed VOX hang delay (Icom CI-V ``get_vox_delay``
    0x1A 0x05 0x02 0x92) is a backend-neutral operator-control int on the
    interim device scale (cross-vendor calibration tracked in MOR-453).
    """
    path = FieldPath.global_("operator_controls", "vox_delay")
    spec = DEFAULT_FIELD_REGISTRY.require(path)
    assert spec.path == path
    assert spec.family is FieldFamily.OPERATOR_CONTROLS
    assert spec.value_type == "int"
    assert spec.writable is True


def test_global_comp_meter_registered_as_read_only_meter_int() -> None:
    """MOR-460: ``global.meters.comp`` is a registered read-only meter int.

    The PA compression meter is cross-vendor (Icom CI-V ``get_comp_meter``
    0x15 0x14 AND Yaesu FTX-1 ``get_comp_meter`` RM3) and is promoted to a
    backend-neutral meter int, matching the alc/power/swr meter form: read-only
    (NOT writable) on the interim device scale (calibration is MOR-453).
    """
    path = FieldPath.global_("meters", "comp")
    spec = DEFAULT_FIELD_REGISTRY.require(path)
    assert spec.path == path
    assert spec.family is FieldFamily.METERS
    assert spec.value_type == "int"
    assert spec.writable is False


def test_global_vd_meter_registered_as_read_only_meter_int() -> None:
    """MOR-460: ``global.meters.vd`` is a registered read-only meter int.

    The PA supply-voltage meter (Icom CI-V ``get_vd_meter`` 0x15 0x15; Xiegu
    X6200 shares the Icom ingress) is promoted to a backend-neutral meter int,
    matching the alc/power/swr meter form: read-only (NOT writable) on the
    interim device scale (calibration is MOR-453).
    """
    path = FieldPath.global_("meters", "vd")
    spec = DEFAULT_FIELD_REGISTRY.require(path)
    assert spec.path == path
    assert spec.family is FieldFamily.METERS
    assert spec.value_type == "int"
    assert spec.writable is False


def test_global_id_meter_registered_as_read_only_meter_int() -> None:
    """MOR-460: ``global.meters.id`` is a registered read-only meter int.

    The PA drain-current meter (Icom CI-V ``get_id_meter`` 0x15 0x16; Icom-only)
    is promoted to a backend-neutral meter int, matching the alc/power/swr meter
    form: read-only (NOT writable) on the interim device scale (calibration is
    MOR-453).
    """
    path = FieldPath.global_("meters", "id")
    spec = DEFAULT_FIELD_REGISTRY.require(path)
    assert spec.path == path
    assert spec.family is FieldFamily.METERS
    assert spec.value_type == "int"
    assert spec.writable is False


def test_receiver_dcd_registered_as_read_only_operator_toggle_bool() -> None:
    """MOR-466: ``receiver.<id>.operator_toggles.dcd`` is a read-only bool.

    The squelch-open / DCD (RX-busy) status (Icom CI-V 0x15 sub 0x01 and 0x05)
    is the RX counterpart of the first-class TX ``ptt`` and matches hamlib
    ``get_dcd``. It is a backend-neutral, observation-only receiver toggle:
    read-only (NOT writable) — there is no CAT command to set it.
    """
    path = FieldPath.receiver("main", "operator_toggles", "dcd")
    spec = DEFAULT_FIELD_REGISTRY.require(path)
    assert spec.path == path
    assert spec.family is FieldFamily.OPERATOR_TOGGLES
    assert spec.value_type == "bool"
    assert spec.writable is False


def test_receiver_digisel_registered_as_writable_operator_toggle_bool() -> None:
    """MOR-477: ``receiver.<id>.operator_toggles.digisel`` is a writable bool.

    DIGI-SEL (Icom CI-V 0x16 sub 0x4E) is a backend-neutral receiver toggle.
    Unlike ``dcd`` it has a CAT set command (``set_digisel``), so the spec is
    writable so the command-overlay path can observe + overlay it.
    """
    path = FieldPath.receiver("main", "operator_toggles", "digisel")
    spec = DEFAULT_FIELD_REGISTRY.require(path)
    assert spec.path == path
    assert spec.family is FieldFamily.OPERATOR_TOGGLES
    assert spec.value_type == "bool"
    assert spec.writable is True


def test_receiver_filter_num_registered_as_writable_freq_mode_int() -> None:
    """MOR-478: ``receiver.<id>.active.freq_mode.filter_num`` is a writable int.

    The FIL selector (FIL1/2/3) has a CAT set command (``set_filter``), so the
    spec is writable so the command-overlay path can observe + overlay it.
    """
    path = FieldPath.active("main", "freq_mode", "filter_num")
    spec = DEFAULT_FIELD_REGISTRY.require(path)
    assert spec.value_type == "int"
    assert spec.writable is True
    assert spec.family is FieldFamily.FREQ_MODE


def test_receiver_ipplus_registered_as_writable_operator_toggle_bool() -> None:
    """MOR-477: ``receiver.<id>.operator_toggles.ipplus`` is a writable bool.

    IP+ (Icom CI-V 0x16 sub 0x65) is a backend-neutral receiver toggle. Unlike
    ``dcd`` it has a CAT set command (``set_ip_plus``), so the spec is writable
    so the command-overlay path can observe + overlay it.
    """
    path = FieldPath.receiver("main", "operator_toggles", "ipplus")
    spec = DEFAULT_FIELD_REGISTRY.require(path)
    assert spec.path == path
    assert spec.family is FieldFamily.OPERATOR_TOGGLES
    assert spec.value_type == "bool"
    assert spec.writable is True


def test_global_cw_spot_registered_as_slow_state_bool() -> None:
    """MOR-456: ``global.slow_state.cw_spot`` is a registered slow_state bool.

    The observation-backed FTX-1 CW-spot field (CAT ``CS``) needs a canonical
    FieldSpec. It is a global slow_state bool, alongside ``slow_state.active``.
    """
    path = FieldPath.global_("slow_state", "cw_spot")
    spec = DEFAULT_FIELD_REGISTRY.require(path)
    assert spec.path == path
    assert spec.family is FieldFamily.SLOW_STATE
    assert spec.value_type == "bool"


def test_global_tuning_step_registered_as_writable_slow_state_int() -> None:
    """MOR-461: ``global.slow_state.tuning_step`` is a registered writable int.

    The observation-backed Icom tuning step (CI-V ``get_tuning_step`` 0x10) is
    promoted to a backend-neutral slow-state int, matching how it is already
    projected/consumed (``_GLOBAL_SLOW_STATE_FIELDS`` / server.py legacy bridge).
    The value is a device step *index* (0-8), NOT Hz, so the spec carries no
    ``unit``; any Hz mapping is deferred to MOR-453. Writable because
    ``set_tuning_step`` exists. Icom-only natively (FTX-1/Xiegu/Lab599 have no
    tuning-step command), so the field stays ``missing`` on those backends.
    """
    path = FieldPath.global_("slow_state", "tuning_step")
    spec = DEFAULT_FIELD_REGISTRY.require(path)
    assert spec.path == path
    assert spec.family is FieldFamily.SLOW_STATE
    assert spec.value_type == "int"
    assert spec.writable is True
    assert spec.unit is None


def test_global_tx_antenna_registered_as_writable_operator_control_int() -> None:
    """MOR-462: ``global.operator_controls.tx_antenna`` is a registered int.

    The observation-backed Icom antenna selection (CI-V ``get_antenna`` 0x12,
    sub 0x00 → ANT1 / 0x01 → ANT2) is promoted to a backend-neutral
    operator-control int (1 or 2), matching how it is already projected/consumed
    (``_GLOBAL_OPERATOR_CONTROL_FIELDS`` → ``txAntenna``). Writable because
    ``set_antenna_1``/``set_antenna_2`` exist. Icom-only natively
    (FTX-1/Xiegu/Lab599 have no antenna command), so the field stays ``missing``
    on those backends per the promotion-criterion ADR.
    """
    path = FieldPath.global_("operator_controls", "tx_antenna")
    spec = DEFAULT_FIELD_REGISTRY.require(path)
    assert spec.path == path
    assert spec.family is FieldFamily.OPERATOR_CONTROLS
    assert spec.value_type == "int"
    assert spec.writable is True
    assert spec.unit is None


def test_global_rx_antenna_1_registered_as_writable_slow_state_bool() -> None:
    """MOR-462: ``global.slow_state.rx_antenna_1`` is a registered writable bool.

    The per-connector RX-ANT toggle for ANT1 is decoded from the 0x12 0x00 data
    byte and promoted to a backend-neutral slow-state bool, matching how it is
    already projected/consumed (``_GLOBAL_SLOW_STATE_FIELDS`` → ``rxAntenna1``).
    Writable because ``set_rx_antenna_ant1`` exists. Only the IC-7610/IC-705 ship
    the RX-ANT path; backends without it leave the field ``missing``.
    """
    path = FieldPath.global_("slow_state", "rx_antenna_1")
    spec = DEFAULT_FIELD_REGISTRY.require(path)
    assert spec.path == path
    assert spec.family is FieldFamily.SLOW_STATE
    assert spec.value_type == "bool"
    assert spec.writable is True


def test_global_rx_antenna_2_registered_as_writable_slow_state_bool() -> None:
    """MOR-462: ``global.slow_state.rx_antenna_2`` is a registered writable bool.

    The per-connector RX-ANT toggle for ANT2 is decoded from the 0x12 0x01 data
    byte and promoted to a backend-neutral slow-state bool, matching how it is
    already projected/consumed (``_GLOBAL_SLOW_STATE_FIELDS`` → ``rxAntenna2``).
    Writable because ``set_rx_antenna_ant2`` exists. Only the IC-7610/IC-705 ship
    the RX-ANT path; backends without it leave the field ``missing``.
    """
    path = FieldPath.global_("slow_state", "rx_antenna_2")
    spec = DEFAULT_FIELD_REGISTRY.require(path)
    assert spec.path == path
    assert spec.family is FieldFamily.SLOW_STATE
    assert spec.value_type == "bool"
    assert spec.writable is True


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
        {
            "path": "receiver.main.meters.s_meter",
            "source": {},
            "timestampMonotonic": 1.0,
        },
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


def test_web_poller_public_revision_delivery_api_remains_absent() -> None:
    # Narrow API-surface guard. Delivery behavior is covered by the semantic
    # WebServer tests below, so this only rejects reintroducing the old poller
    # revision methods as public delivery API.
    assert not hasattr(RadioPoller, "bump_revision")
    assert not hasattr(RadioPoller, "revision")


def test_web_poller_command_response_no_op_remains_removed() -> None:
    # MOR-437 deleted the pure no-op ``_apply_command_response_observation`` and
    # the migrated per-family legacy mirror helpers. Setter success records
    # CommandService lifecycle/overlays only; it never confirms StateStore.
    import rigplane.web.radio_poller as radio_poller_module

    assert not hasattr(RadioPoller, "_apply_command_response_observation")
    assert not hasattr(radio_poller_module, "_apply_att_compatibility_mirror")
    assert not hasattr(radio_poller_module, "_apply_preamp_compatibility_mirror")
    # The real BSR readback observation emitter must stay (the BAND audit relies
    # on it) along with the generic deferred compatibility-mirror helper.
    assert hasattr(RadioPoller, "_apply_bsr_readback_observations")
    assert hasattr(RadioPoller, "_apply_compatibility_mirror")


@pytest.mark.asyncio
async def test_web_delivery_payloads_use_snapshot_not_legacy_state_or_revision() -> (
    None
):
    server, snapshot = _server_with_conflicting_legacy_state()

    public_payload = server.build_public_state()
    _assert_delivered_from_snapshot(public_payload, snapshot)

    envelope = server.build_state_update_envelope(force_full=True)
    assert envelope["type"] == "full"
    _assert_delivered_from_snapshot(envelope["data"], snapshot)

    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    server.register_control_event_queue(queue)
    server._last_state_broadcast = 0.0  # noqa: SLF001
    server._broadcast_state_update()  # noqa: SLF001
    broadcast = queue.get_nowait()
    assert broadcast["type"] == "state_update"
    assert broadcast["data"]["type"] == "full"
    _assert_delivered_from_snapshot(broadcast["data"]["data"], snapshot)

    writer = _FakeWriter()
    await server._serve_state(writer)  # noqa: SLF001
    status, http_payload = _response_json(writer)
    assert status == 200
    _assert_delivered_from_snapshot(http_payload, snapshot)

    after_delivery = server.command_state_store.snapshot()
    assert after_delivery.state_revision == snapshot.state_revision
    assert after_delivery.freshness_revision == snapshot.freshness_revision
    assert after_delivery.as_dict() == snapshot.as_dict()


def test_web_state_change_callback_broadcasts_snapshot_without_revision_path() -> None:
    server, snapshot = _server_with_conflicting_legacy_state()
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    server.register_control_event_queue(queue)

    server._on_radio_state_change(  # noqa: SLF001
        "legacy_radio_state_changed",
        {"revision": _LEGACY_POLLER_REVISION, "freq": 14_250_000},
    )

    forwarded = queue.get_nowait()
    broadcast = queue.get_nowait()
    assert forwarded == {
        "type": "event",
        "name": "legacy_radio_state_changed",
        "data": {"revision": _LEGACY_POLLER_REVISION, "freq": 14_250_000},
    }
    assert broadcast["type"] == "state_update"
    assert broadcast["data"]["type"] == "full"
    _assert_delivered_from_snapshot(broadcast["data"]["data"], snapshot)
    assert not any(
        item.kind == "revision_producing_event"
        for item in server.state_diagnostics.events()
    )


# Icom v2 field families that MOR-437 makes observation-backed. Each entry is
# ``(FieldPath, public field-status key, public dict location, sentinel value)``
# where ``location`` is the receiver public key (``"main"``) for receiver-scoped
# leaves or ``None`` for top-level global leaves.
_ICOM_V2_FIELD_FAMILIES: tuple[tuple[FieldPath, str, str | None, Any], ...] = (
    # receiver operator_controls
    (FieldPath.receiver("main", "operator_controls", "rf_gain"), "rfGain", "main", 7),
    (FieldPath.receiver("main", "operator_controls", "squelch"), "squelch", "main", 7),
    (FieldPath.receiver("main", "operator_controls", "att"), "att", "main", 6),
    (FieldPath.receiver("main", "operator_controls", "preamp"), "preamp", "main", 1),
    (FieldPath.receiver("main", "operator_controls", "agc"), "agc", "main", 2),
    (
        FieldPath.receiver("main", "operator_controls", "agc_time_constant"),
        "agcTimeConstant",
        "main",
        3,
    ),
    (
        FieldPath.receiver("main", "operator_controls", "nr_level"),
        "nrLevel",
        "main",
        9,
    ),
    (
        FieldPath.receiver("main", "operator_controls", "nb_level"),
        "nbLevel",
        "main",
        4,
    ),
    (
        FieldPath.receiver("main", "operator_controls", "pbt_inner"),
        "pbtInner",
        "main",
        50,
    ),
    (
        FieldPath.receiver("main", "operator_controls", "pbt_outer"),
        "pbtOuter",
        "main",
        60,
    ),
    # receiver operator_toggles
    (
        FieldPath.receiver("main", "operator_toggles", "auto_notch"),
        "autoNotch",
        "main",
        True,
    ),
    (
        FieldPath.receiver("main", "operator_toggles", "manual_notch"),
        "manualNotch",
        "main",
        True,
    ),
    # manual_notch_freq promoted as a neutral DSP control (MOR-444).
    (
        FieldPath.receiver("main", "operator_controls", "manual_notch_freq"),
        "manualNotchFreq",
        "main",
        128,
    ),
    # IF-shift / narrow promoted as neutral DSP controls (MOR-445).
    (
        FieldPath.receiver("main", "operator_controls", "if_shift"),
        "ifShift",
        "main",
        200,
    ),
    (
        FieldPath.receiver("main", "operator_toggles", "narrow"),
        "narrow",
        "main",
        True,
    ),
    # Squelch-open / DCD (RX-busy) promoted as a neutral read-only receiver
    # toggle — projects to the new ``dcd`` public key (MOR-466).
    (
        FieldPath.receiver("main", "operator_toggles", "dcd"),
        "dcd",
        "main",
        True,
    ),
    # receiver freq_mode (active slot)
    (FieldPath.active("main", "freq_mode", "data_mode"), "dataMode", "main", 1),
    (
        FieldPath.active("main", "freq_mode", "filter_width"),
        "filterWidth",
        "main",
        500,
    ),
    # global operator_controls
    (FieldPath.global_("operator_controls", "mic_gain"), "micGain", None, 5),
    (
        FieldPath.global_("operator_controls", "compressor_level"),
        "compressorLevel",
        None,
        5,
    ),
    (
        FieldPath.global_("operator_controls", "monitor_gain"),
        "monitorGain",
        None,
        5,
    ),
    (FieldPath.global_("operator_controls", "cw_pitch"), "cwPitch", None, 600),
    (
        FieldPath.global_("operator_controls", "tuner_status"),
        "tunerStatus",
        None,
        1,
    ),
    # VOX trio promoted as neutral operator-control ints (MOR-459).
    (FieldPath.global_("operator_controls", "vox_gain"), "voxGain", None, 50),
    (
        FieldPath.global_("operator_controls", "anti_vox_gain"),
        "antiVoxGain",
        None,
        30,
    ),
    (FieldPath.global_("operator_controls", "vox_delay"), "voxDelay", None, 12),
    # tx_antenna promoted as a neutral writable operator-control int (1/2) —
    # projects to the existing top-level ``txAntenna`` key (MOR-462).
    (FieldPath.global_("operator_controls", "tx_antenna"), "txAntenna", None, 2),
    # global slow_state
    # tuning_step promoted as a neutral writable slow-state int (device step
    # index, NOT Hz) — projects to the existing top-level ``tuningStep`` key
    # (MOR-461).
    (FieldPath.global_("slow_state", "tuning_step"), "tuningStep", None, 5),
    # RX-ANT per-connector toggles promoted as neutral writable slow-state bools
    # — project to the existing top-level ``rxAntenna1``/``rxAntenna2`` keys
    # (MOR-462).
    (FieldPath.global_("slow_state", "rx_antenna_1"), "rxAntenna1", None, True),
    (FieldPath.global_("slow_state", "rx_antenna_2"), "rxAntenna2", None, True),
    # global tx_state
    (FieldPath.global_("tx_state", "split"), "split", None, True),
    (FieldPath.global_("tx_state", "compressor_on"), "compressorOn", None, True),
    (FieldPath.global_("tx_state", "monitor_on"), "monitorOn", None, True),
    (FieldPath.global_("tx_state", "vox_on"), "voxOn", None, True),
    (FieldPath.global_("tx_state", "dual_watch"), "dualWatch", None, True),
)


def _public_status_path(public_key: str, location: str | None) -> str:
    return public_key if location is None else f"{location}.{public_key}"


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("path", "public_key", "location", "value"),
    _ICOM_V2_FIELD_FAMILIES,
    ids=[str(entry[0]) for entry in _ICOM_V2_FIELD_FAMILIES],
)
def test_icom_v2_field_family_registered(
    path: FieldPath,
    public_key: str,
    location: str | None,
    value: Any,
) -> None:
    spec = DEFAULT_FIELD_REGISTRY.require(path)
    assert spec.path == path
    assert spec.family is path.family


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("path", "public_key", "location", "value"),
    _ICOM_V2_FIELD_FAMILIES,
    ids=[str(entry[0]) for entry in _ICOM_V2_FIELD_FAMILIES],
)
def test_icom_v2_field_family_projects_observed(
    path: FieldPath,
    public_key: str,
    location: str | None,
    value: Any,
) -> None:
    store = StateStore()
    store.apply(_store_observation(path, value, at=1.0))
    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=2,
    )

    if location is None:
        assert payload[public_key] == value
    else:
        assert payload[location][public_key] == value

    status_path = _public_status_path(public_key, location)
    field_status = payload["fieldStatus"][status_path]
    assert field_status["observed"] is True
    assert field_status["availability"] == "available"


@pytest.mark.parametrize(  # type: ignore[untyped-decorator]
    ("path", "public_key", "location", "value"),
    _ICOM_V2_FIELD_FAMILIES,
    ids=[str(entry[0]) for entry in _ICOM_V2_FIELD_FAMILIES],
)
def test_icom_v2_field_family_seeds_missing_when_unobserved(
    path: FieldPath,
    public_key: str,
    location: str | None,
    value: Any,
) -> None:
    payload = build_public_state_payload_from_snapshot(
        StateStore().snapshot(),
        radio=None,
        receiver_count=2,
    )

    status_path = _public_status_path(public_key, location)
    field_status = payload["fieldStatus"][status_path]
    assert field_status["observed"] is False
    assert field_status["availability"] == "missing"


def test_dcd_projects_deprecated_smeter_sql_open_alias() -> None:
    """MOR-466: the ``dcd`` observation also projects the legacy alias.

    During the migration window the neutral ``dcd`` receiver toggle is projected
    under BOTH ``dcd`` and the deprecated ``sMeterSqlOpen`` public key, with the
    same value and the same ``available`` availability, so existing frontend
    consumers keep working.
    """
    path = FieldPath.receiver("main", "operator_toggles", "dcd")
    store = StateStore()
    store.apply(_store_observation(path, True, at=1.0))
    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=2,
    )

    assert payload["main"]["dcd"] is True
    assert payload["main"]["sMeterSqlOpen"] is True
    for status_path in ("main.dcd", "main.sMeterSqlOpen"):
        field_status = payload["fieldStatus"][status_path]
        assert field_status["observed"] is True
        assert field_status["availability"] == "available"


def test_dcd_deprecated_alias_seeds_missing_when_unobserved() -> None:
    """MOR-466: the deprecated ``sMeterSqlOpen`` alias seeds ``missing`` too.

    An absent ``dcd`` observation must leave the legacy alias ``missing`` (not
    resolve to ``available`` on its default), mirroring the primary ``dcd`` key.
    """
    payload = build_public_state_payload_from_snapshot(
        StateStore().snapshot(),
        radio=None,
        receiver_count=2,
    )

    for status_path in ("main.dcd", "main.sMeterSqlOpen"):
        field_status = payload["fieldStatus"][status_path]
        assert field_status["observed"] is False
        assert field_status["availability"] == "missing"


# --- MOR-464 Phase 1: calibrated-domain unit vocabulary ------------------


def _meters_path(name: str) -> FieldPath:
    return FieldPath.global_("meters", name)


def test_field_spec_rejects_unknown_unit() -> None:
    """An out-of-vocabulary unit token raises ``ValueError``."""
    with pytest.raises(ValueError, match="unknown field unit"):
        FieldSpec(
            path=FieldPath.global_("meters", "swr"),
            family=FieldFamily.METERS,
            value_type="int",
            unit="bogus",
        )


@pytest.mark.parametrize(
    "unit", ["hz", "centihz", "normalized", "db", "w", "ratio", "v", "a"]
)
def test_field_spec_accepts_declared_units(unit: str) -> None:
    """Every declared vocabulary token is accepted."""
    spec = FieldSpec(
        path=FieldPath.global_("meters", "swr"),
        family=FieldFamily.METERS,
        value_type="int",
        unit=unit,
    )
    assert spec.unit == unit


def test_field_spec_accepts_unit_none() -> None:
    """``unit=None`` (the default) remains valid."""
    spec = FieldSpec(
        path=FieldPath.global_("meters", "swr"),
        family=FieldFamily.METERS,
        value_type="int",
        unit=None,
    )
    assert spec.unit is None


@pytest.mark.parametrize("receiver_id", ["main", "sub"])
@pytest.mark.parametrize("field", ["tone_freq", "tsql_freq"])
def test_tone_and_tsql_freq_units_are_centihz(receiver_id: str, field: str) -> None:
    """CTCSS tone/TSQL frequencies declare ``centihz`` (value_type stays int)."""
    path = FieldPath.receiver(receiver_id, "operator_controls", field)
    spec = DEFAULT_FIELD_REGISTRY.require(path)
    assert spec.unit == "centihz"
    assert spec.value_type == "int"


def test_power_level_unit_is_normalized() -> None:
    """``power_level`` declares ``normalized`` and stays ``value_type='int'``."""
    spec = DEFAULT_FIELD_REGISTRY.require(
        FieldPath.global_("operator_controls", "power_level")
    )
    assert spec.unit == "normalized"
    assert spec.value_type == "int"


@pytest.mark.parametrize(
    "name,expected_unit",
    [
        ("power", "w"),
        ("swr", "ratio"),
        ("alc", "normalized"),
        ("comp", "db"),
        ("vd", "v"),
        ("id", "a"),
    ],
)
def test_global_meter_units(name: str, expected_unit: str) -> None:
    """Global meters carry their calibrated-domain units."""
    spec = DEFAULT_FIELD_REGISTRY.require(_meters_path(name))
    assert spec.unit == expected_unit


@pytest.mark.parametrize("receiver_id", ["main", "sub"])
def test_s_meter_unit_is_db(receiver_id: str) -> None:
    """The receiver-scoped S-meter declares ``db``."""
    spec = DEFAULT_FIELD_REGISTRY.require(
        FieldPath.receiver(receiver_id, "meters", "s_meter")
    )
    assert spec.unit == "db"
