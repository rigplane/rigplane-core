"""MOR-4279: typed fact records pushed on the control WebSocket.

Three fact groups the Pro companion polled over HTTP become typed
control-WS records pushed on change plus included in the connect-time
snapshot, serialized by the SAME producers the HTTP endpoints use:

- the managed-transmit document (``GET /api/v1/managed-transmit``) as the
  ``managed_transmit_changed`` event payload;
- connection identity/session facts (``GET /api/v1/info``) as
  ``connection.identity`` / ``connection.session`` leaves of the public
  state payload;
- ``audioReceive`` (``GET /api/v1/info``) as a public state leaf.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from rigplane.core._bounded_queue import BoundedQueue
from rigplane.core.radio_protocol import RadioIdentity, RadioIdentityStatus
from rigplane.core.state_pipeline_contracts import Observation, SourceMetadata
from rigplane.core.tx_observation import OBSERVED_PTT_PATH, ObservedPtt
from rigplane.radio_state import RadioState
from rigplane.runtime.managed_tx_authority import ManagedTxProjection
from rigplane.runtime.managed_tx_state import (
    ManagedTxIntent,
    ManagedTxIntentKind,
    ManagedTxState,
    ReleasePlan,
)
from rigplane.web.api_contract import (
    WEB_API_CONTRACT_VERSION,
)
from rigplane.web.server import WebConfig, WebServer


def _sdk_records() -> tuple[type, type, type, type]:
    """The SDK record types (MOR-4279; absent before the change)."""
    from rigplane.web.api_contract import (
        AudioReceiveRecord,
        ConnectionIdentityRecord,
        ConnectionSessionRecord,
        ManagedTransmitDocument,
    )

    return (
        ConnectionIdentityRecord,
        ConnectionSessionRecord,
        AudioReceiveRecord,
        ManagedTransmitDocument,
    )


def _document_schema() -> type:
    """The pinned wire schema of the managed-transmit document record."""
    from rigplane.web.state_schema import ManagedTransmitDocumentPublic

    return ManagedTransmitDocumentPublic


_SAMPLED_AT_TEXT = "2026-11-08T12:00:00.000Z"


class _Writer:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    @property
    def status(self) -> int:
        return int(self.buffer.split(b" ", 2)[1])

    @property
    def payload(self) -> dict[str, object]:
        body = self.buffer.split(b"\r\n\r\n", 1)[1]
        return json.loads(body or b"{}")


class _Authority:
    """Minimal managed-transmit authority double (the HTTP-route shape)."""

    def __init__(self) -> None:
        self.configured_tot_seconds: float | None = 180.0

    async def snapshot(self) -> ManagedTxProjection:
        return ManagedTxProjection(
            ManagedTxState(
                intent=ManagedTxIntent(ManagedTxIntentKind.TRANSMIT),
                release_plan=ReleasePlan.PTT_RELEASE,
                tx_started_at_monotonic=10.0,
                tot_deadline_monotonic=20.0,
            ),
            self.configured_tot_seconds,
            5.25,
            provider_generation=7,
        )


class _AudioSessionSource:
    """Object identity holder for a staged RX-start failure."""


class _FactsRadio:
    """Narrow fake carrying exactly the identity/session facts the server reads."""

    backend_id = "rigplane"
    model = "FAKE-FACTS"
    capabilities: set[str] = {"audio"}

    def __init__(self) -> None:
        from rigplane.profiles import resolve_radio_profile

        self.profile = resolve_radio_profile(model="IC-705")
        self.connected = True
        self.control_connected = True
        self.radio_ready = False
        self.radio_state = RadioState()
        self.connection_identity = RadioIdentity(
            status=RadioIdentityStatus.VERIFIED,
            expected_model="FAKE-FACTS",
            answered_model="FAKE-FACTS",
            answered_id="94",
        )
        self.startup_busy_hold_event = asyncio.Event()
        self.startup_busy_hold_event.set()
        self.startup_busy_hold_retry_in_seconds = 12.5
        self.audio_session = _AudioSessionSource()

    def supports_command(self, _command: str) -> bool:
        return False


def _server(*, radio: Any = None, authority: _Authority | None = None) -> WebServer:
    server = WebServer(
        radio,
        WebConfig(host="127.0.0.1", port=0),
    )
    if authority is not None:
        server._production_managed_tx_port = type(  # noqa: SLF001
            "_Port", (), {"authority": authority}
        )()
    return server


def _register(server: WebServer) -> BoundedQueue[dict[str, Any]]:
    queue: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=100)
    server._control_event_queues.add(queue)  # noqa: SLF001
    return queue


def _drain(queue: BoundedQueue[dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


def _managed_tx_events(queue: BoundedQueue[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        event
        for event in _drain(queue)
        if event.get("type") == "event"
        and event.get("name") == "managed_transmit_changed"
    ]


def _observe_ptt(server: WebServer, value: ObservedPtt) -> None:
    # The store's own snapshot stamp keeps the observation fresh without the
    # test reading the real clock (MOR-4075 realtime guard).
    now = server.command_state_store.snapshot().generated_at_monotonic
    server.command_state_store.apply_current(
        Observation(
            path=OBSERVED_PTT_PATH,
            value=value,
            source=SourceMetadata(source="poll_response", provider="test"),
            timestamp_monotonic=now,
            max_age=30.0,
        )
    )


async def _await_managed_tx_publication(server: WebServer) -> None:
    tasks = [task for task in server._bg_tasks if not task.done()]  # noqa: SLF001
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)
    # Let any queued done-callbacks and queue puts settle.
    for _ in range(4):
        await asyncio.sleep(0)


async def test_managed_tx_change_pushes_the_document_with_observed_ptt() -> None:
    """(a) one change reaches a control client as the full document record."""
    server = _server(authority=_Authority())
    _observe_ptt(server, ObservedPtt.ON)
    queue = _register(server)

    server._on_managed_tx_changed()  # noqa: SLF001
    await _await_managed_tx_publication(server)

    events = _managed_tx_events(queue)
    assert len(events) == 1, events
    document = events[0]["data"]
    assert document["schemaVersion"] == 1
    assert document["txObservation"] == {"observedPtt": "on"}
    assert document["managedTransmit"]["status"] == "available"
    assert document["managedTransmit"]["intent"] == {"kind": "transmit"}


async def test_managed_tx_event_matches_the_http_document_record() -> None:
    """(c) the WS event and the HTTP endpoint serialize the SAME record."""
    server = _server(authority=_Authority())
    _observe_ptt(server, ObservedPtt.ON)
    queue = _register(server)

    server._on_managed_tx_changed()  # noqa: SLF001
    await _await_managed_tx_publication(server)
    events = _managed_tx_events(queue)
    assert len(events) == 1, events

    writer = _Writer()
    await server._handle_http(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        "GET",
        "/api/v1/managed-transmit",
    )
    assert writer.status == 200

    event_document = dict(events[0]["data"])
    http_document = dict(writer.payload)
    # Both carry the same build-time stamps; everything else must be equal.
    for document in (event_document, http_document):
        assert isinstance(document["sampledAt"], str)
        document.pop("sampledAt")
        managed = document["managedTransmit"]
        assert isinstance(managed, dict)
        managed.pop("lastActuation")
        tot = managed["tot"]
        assert isinstance(tot, dict)
        tot.pop("expiresAt")
    assert event_document == http_document
    assert event_document["txObservation"] == {"observedPtt": "on"}


async def test_managed_tx_document_conforms_to_the_wire_schema() -> None:
    """(c) the pushed record validates against the pinned pydantic schema."""
    server = _server(authority=_Authority())
    _observe_ptt(server, ObservedPtt.ON)
    queue = _register(server)

    server._on_managed_tx_changed()  # noqa: SLF001
    await _await_managed_tx_publication(server)
    events = _managed_tx_events(queue)
    assert len(events) == 1, events

    document_schema = _document_schema()
    parsed = document_schema.model_validate(events[0]["data"])
    assert parsed.txObservation.observedPtt == "on"
    assert parsed.managedTransmit is not None
    assert parsed.managedTransmit.status == "available"


def test_sdk_record_types_pin_the_wire_shapes() -> None:
    """(c) the SDK record types exist with the pinned wire fields."""
    import typing

    (
        identity_record,
        session_record,
        receive_record,
        document_record,
    ) = _sdk_records()
    identity_hints = typing.get_type_hints(identity_record)
    assert set(identity_hints) == {
        "status",
        "expectedModel",
        "answeredModel",
        "answeredId",
    }
    session_hints = typing.get_type_hints(session_record)
    assert set(session_hints) == {"busy", "retryInSeconds"}
    receive_hints = typing.get_type_hints(receive_record)
    assert set(receive_hints) == {"schemaVersion", "failure"}
    document_hints = typing.get_type_hints(document_record)
    assert set(document_hints) == {
        "schemaVersion",
        "sampledAt",
        "managedTransmit",
        "txObservation",
    }
    assert WEB_API_CONTRACT_VERSION == 1


def test_sdk_records_do_not_drift_from_the_wire_schema() -> None:
    """(c) the pydantic-free SDK records stay equal to the wire schema."""
    import typing

    from rigplane.web import state_schema

    (
        identity_record,
        session_record,
        receive_record,
        document_record,
    ) = _sdk_records()
    pairs = (
        (identity_record, state_schema.ConnectionIdentityPublic),
        (session_record, state_schema.ConnectionSessionPublic),
        (receive_record, state_schema.AudioReceivePublic),
        (document_record, state_schema.ManagedTransmitDocumentPublic),
    )
    for record, model in pairs:
        assert set(typing.get_type_hints(record)) == set(model.model_fields), (
            record.__name__,
            model.__name__,
        )


def _baseline_payload(server: WebServer) -> dict[str, Any]:
    """Register one control client and return its connect-time full frame."""
    queue: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=100)
    baseline = server.register_control_event_queue(queue)
    payload = baseline["data"]
    assert isinstance(payload, dict)
    return payload


async def test_connect_baseline_carries_identity_session_and_audio_receive() -> None:
    """(b) the connect-time state snapshot contains all three fact groups."""
    radio = _FactsRadio()
    server = _server(radio=radio)

    payload = _baseline_payload(server)
    connection = payload["connection"]
    assert isinstance(connection, dict)
    assert connection["identity"] == {
        "status": "verified",
        "expectedModel": "FAKE-FACTS",
        "answeredModel": "FAKE-FACTS",
        "answeredId": "94",
    }
    assert connection["session"] == {"busy": True, "retryInSeconds": 12.5}
    assert payload["audioReceive"] == {"schemaVersion": 1, "failure": None}


async def test_state_payload_with_fact_records_conforms_to_state_schema() -> None:
    """(c) a real server state payload with the new leaves validates."""
    from rigplane.web.state_schema import ServerStatePublic

    radio = _FactsRadio()
    server = _server(radio=radio)

    parsed = ServerStatePublic.model_validate(_baseline_payload(server))
    assert parsed.connection is not None
    assert parsed.connection.identity is not None
    assert parsed.connection.identity.status == "verified"
    assert parsed.connection.session is not None
    assert parsed.connection.session.busy is True
    assert parsed.audioReceive is not None
    assert parsed.audioReceive.schemaVersion == 1


async def test_control_handler_sends_current_managed_tx_document_on_connect() -> None:
    """(b) the connect-time snapshot delivers the current managed-TX document."""
    from rigplane.web.handlers.control import ControlHandler

    authority = _Authority()
    server = _server(authority=authority)
    _observe_ptt(server, ObservedPtt.OFF)

    handler = ControlHandler.__new__(ControlHandler)
    sent: list[str] = []

    class _CaptureWs:
        async def send_text(self, text: str) -> None:
            sent.append(text)

    handler._ws = _CaptureWs()  # type: ignore[attr-assign]
    handler._server = server  # type: ignore[attr-assign]

    await handler._send_current_managed_tx_document()  # type: ignore[attr-defined]

    assert len(sent) == 1
    message = json.loads(sent[0])
    assert message["type"] == "event"
    assert message["name"] == "managed_transmit_changed"
    document = message["data"]
    assert document["schemaVersion"] == 1
    assert document["txObservation"]["observedPtt"] == "off"
    assert document["managedTransmit"]["status"] == "available"


async def test_identity_change_pushes_one_delta_and_unchanged_facts_push_nothing() -> (
    None
):
    """(d) fact pushes are change-driven: nothing changes, nothing is pushed."""
    radio = _FactsRadio()
    server = _server(radio=radio)
    queue: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=100)

    server.register_control_event_queue(queue)
    assert _drain(queue) == []

    # An interval of broadcast cycles with no fact change: no pushes at all.
    for _ in range(3):
        server._last_state_broadcast = 0.0  # noqa: SLF001
        server._broadcast_state_update()  # noqa: SLF001
    assert _drain(queue) == []

    # One identity change: exactly one delta carrying the new identity record.
    radio.connection_identity = RadioIdentity(
        status=RadioIdentityStatus.IDENTITY_MISMATCH,
        expected_model="FAKE-FACTS",
        answered_model="IC-7300",
        answered_id="94",
    )
    server._last_state_broadcast = 0.0  # noqa: SLF001
    server._broadcast_state_update()  # noqa: SLF001

    state_updates = [
        event for event in _drain(queue) if event.get("type") == "state_update"
    ]
    assert len(state_updates) == 1, state_updates
    delta = state_updates[0]["data"]
    assert delta["type"] == "delta"
    changed = delta["changed"]
    assert isinstance(changed, dict)
    connection = changed["connection"]
    assert isinstance(connection, dict)
    assert connection["identity"] == {
        "status": "identity_mismatch",
        "expectedModel": "FAKE-FACTS",
        "answeredModel": "IC-7300",
        "answeredId": "94",
    }
    # The managed-transmit event never fires for an identity-only change.
    assert _managed_tx_events(queue) == []


async def test_managed_tx_pushes_coalesce_to_the_latest_document() -> None:
    """(d) two rapid changes deliver one event, not a periodic stream."""
    server = _server(authority=_Authority())
    _observe_ptt(server, ObservedPtt.ON)
    queue = _register(server)

    server._on_managed_tx_changed()  # noqa: SLF001
    server._on_managed_tx_changed()  # noqa: SLF001
    await _await_managed_tx_publication(server)

    events = _managed_tx_events(queue)
    assert len(events) == 1, events
    assert events[0]["data"]["txObservation"] == {"observedPtt": "on"}
