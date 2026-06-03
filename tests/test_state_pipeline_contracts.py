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
    Observation,
    SourceMetadata,
)
from rigplane.radio_state import RadioState
from rigplane.web.radio_poller import RadioPoller
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


def test_web_poller_public_revision_delivery_api_remains_absent() -> None:
    # Narrow API-surface guard. Delivery behavior is covered by the semantic
    # WebServer tests below, so this only rejects reintroducing the old poller
    # revision methods as public delivery API.
    assert not hasattr(RadioPoller, "bump_revision")
    assert not hasattr(RadioPoller, "revision")


@pytest.mark.asyncio
async def test_web_delivery_payloads_use_snapshot_not_legacy_state_or_revision() -> None:
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
