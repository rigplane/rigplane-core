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
import re
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from rigplane.audio import AudioPacket
from rigplane.audio.backend import (
    AudioDeviceId,
    AudioDeviceInfo,
    FakeAudioBackend,
    FakeRxStream,
)
from rigplane.audio.session import AudioSession
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
    def _head(self) -> str:
        return self.buffer.split(b"\r\n\r\n", 1)[0].decode("ascii")

    def header(self, name: str) -> str | None:
        for line in self._head.split("\r\n")[1:]:
            key, _, value = line.partition(":")
            if key.strip().lower() == name.lower():
                return value.strip()
        return None

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


_AUDIO_DEVICE = AudioDeviceInfo(
    id=AudioDeviceId(7), name="Fake RX", input_channels=1, output_channels=0
)


class _FactsRadio:
    """Narrow fake carrying exactly the identity/session facts the server reads."""

    backend_id = "rigplane"
    model = "FAKE-FACTS"
    capabilities: set[str] = {"audio"}
    has_usb_audio = True

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
        self.audio_backend = FakeAudioBackend([_AUDIO_DEVICE])
        self._rx_stream: FakeRxStream | None = None
        self.audio_session = AudioSession(self)

    async def start_rx(self, callback: Callable[[AudioPacket], None]) -> None:
        self._rx_stream = self.audio_backend.open_rx(_AUDIO_DEVICE.id)
        await self._rx_stream.start(lambda _frame: None)

    async def stop_rx(self) -> None:
        if self._rx_stream is not None:
            await self._rx_stream.stop()
            self._rx_stream = None

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


async def test_cancellation_during_connect_snapshot_unregisters_the_queue() -> None:
    """A cancel inside the connect-time snapshot await cleans registration.

    The queue, leaf-delta set and session mapping registered by
    ``run()`` must not outlive a handler cancelled while the
    managed-transmit document getter is still awaiting.
    """
    import contextlib

    from rigplane.web.handlers.control import ControlHandler

    server = _server(radio=_FactsRadio())
    blocked = asyncio.Event()
    release = asyncio.Event()

    async def blocking_document() -> dict[str, object]:
        blocked.set()
        await release.wait()
        raise AssertionError("cancelled before the getter finished")

    server.current_managed_tx_document = blocking_document  # type: ignore[method-assign]

    class _CaptureWs:
        async def send_text(self, _text: str) -> None:
            return None

        async def recv(self) -> tuple[int, bytes]:
            raise EOFError()

    handler = ControlHandler(
        _CaptureWs(),  # type: ignore[arg-type]
        None,
        "test-version",
        "FAKE-FACTS",
        server=server,
        session_id="cancel-probe",
    )
    task = asyncio.get_running_loop().create_task(handler.run())
    await blocked.wait()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert server._control_event_queues == set()  # noqa: SLF001
    assert server._leaf_delta_queues == set()  # noqa: SLF001
    assert server._session_queues.get("cancel-probe") is None  # noqa: SLF001


async def test_state_etag_is_a_valid_opaque_tag_and_conditional_get_304s() -> None:
    """The fact-record delivery-key components keep the ETag well-formed.

    The validator hashes every component, so populated fact records and an
    SDR error text carrying an apostrophe (whose tuple repr embeds double
    quotes) cannot break RFC 9110 opaque-tag syntax.
    """
    from types import SimpleNamespace

    server = _server(radio=_FactsRadio())
    server._sdr_runtime = SimpleNamespace(  # noqa: SLF001
        state="error",
        span_hz=2_000_000,
        tx_frozen=False,
        overflow_count=1,
        last_error="can't open device",
    )
    writer = _Writer()
    await server._handle_http(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        "GET",
        "/api/v1/state",
    )
    assert writer.status == 200
    etag = writer.header("etag")
    assert etag is not None
    assert re.fullmatch(r'"[\x21\x23-\x7e]+"', etag), etag

    conditional = _Writer()
    await server._handle_http(  # noqa: SLF001
        conditional,  # type: ignore[arg-type]
        "GET",
        "/api/v1/state",
        headers={"if-none-match": etag},
    )
    assert conditional.status == 304


async def test_real_control_ws_receives_the_fact_records_end_to_end() -> None:
    """A real control-WS client gets every fact group through the socket.

    Covers the production entrypoint: hello, the connect-time state
    baseline with identity/session/audioReceive, the connect-time
    managed-transmit document, and the change-driven document push.
    """
    tests_root = Path(__file__).resolve().parents[1]
    if str(tests_root) not in sys.path:
        # The shared WS handshake helpers live in the unpackaged tests root;
        # make them importable when this packaged test module runs alone.
        sys.path.insert(0, str(tests_root))
    from test_web_server import _ws_connect, _ws_recv_frame  # noqa: E402

    radio = _FactsRadio()
    radio.radio_ready = True
    from rigplane.runtime.managed_tx_authority import ManagedTxAuthority
    from rigplane.runtime.managed_tx_config import ManagedTxTotConfig
    from rigplane.runtime.managed_tx_effect_lane import ManagedTxEffectLane
    from rigplane.runtime.managed_tx_fence import TxAbortFence
    from rigplane.runtime.managed_tx_state import ActuationResult

    class _AcceptingActuator:
        async def actuate(self, *_args: object, **_kwargs: object) -> object:
            return ActuationResult.ACCEPTED

    class _MemoryConfig:
        def __init__(self) -> None:
            self._config = ManagedTxTotConfig(10.0)

        @property
        def config(self) -> ManagedTxTotConfig:
            return self._config

        def set_timeout_seconds(self, value: object) -> ManagedTxTotConfig:
            del value
            return self._config

    authority = ManagedTxAuthority(
        ManagedTxEffectLane(_AcceptingActuator()),
        _MemoryConfig(),  # type: ignore[arg-type]
        TxAbortFence(),
        provider_generation=7,
    )
    # The REAL authority is both the change listener and the document
    # producer: `_production_managed_tx_port` feeds `_managed_tx_document`.
    server = _server(radio=radio)
    server._production_managed_tx_port = type(  # noqa: SLF001
        "_Port", (), {"authority": authority}
    )()
    server._bind_managed_tx_hint(authority)  # noqa: SLF001
    _observe_ptt(server, ObservedPtt.OFF)
    server._server = await asyncio.start_server(  # noqa: SLF001
        server._accept_client,
        "127.0.0.1",
        0,  # noqa: SLF001
    )
    try:
        reader, writer, _ = await _ws_connect("127.0.0.1", server.port, "/api/v1/ws")
        try:
            hello_seen = baseline_seen = document_seen = False
            for _ in range(6):
                _, payload = await _ws_recv_frame(reader, timeout=5.0)
                message = json.loads(payload)
                if message["type"] == "hello":
                    hello_seen = True
                elif message["type"] == "state_update":
                    baseline_seen = True
                    connection = message["data"]["data"]["connection"]
                    assert connection["identity"]["status"] == "verified"
                    assert connection["session"]["busy"] is True
                    assert message["data"]["data"]["audioReceive"] == {
                        "schemaVersion": 1,
                        "failure": None,
                    }
                elif (
                    message["type"] == "event"
                    and message["name"] == "managed_transmit_changed"
                ):
                    document_seen = True
                    assert message["data"]["txObservation"] == {"observedPtt": "off"}
                    assert message["data"]["managedTransmit"]["intent"] == {
                        "kind": "rx"
                    }
                if hello_seen and baseline_seen and document_seen:
                    break
            assert hello_seen and baseline_seen and document_seen

            # A managed-TX key through the real authority listener (the
            # production wiring: subscribe_changes -> _on_managed_tx_changed)
            # reaches the connected client within one event, with the exact
            # resulting intent and owner from the authority itself.
            _observe_ptt(server, ObservedPtt.ON)
            await authority.ptt_down("e2e-owner")
            document = None
            for _ in range(6):
                _, payload = await _ws_recv_frame(reader, timeout=5.0)
                message = json.loads(payload)
                if (
                    message.get("type") == "event"
                    and message.get("name") == "managed_transmit_changed"
                ):
                    document = message["data"]
                    break
            assert document is not None
            assert document["txObservation"] == {"observedPtt": "on"}
            assert document["managedTransmit"]["status"] == "available"
            assert document["managedTransmit"]["intent"] == {
                "kind": "ptt",
                "owner": "e2e-owner",
            }
            await authority.ptt_up("e2e-owner")

            # RX failure and recovery through the production tap-only relay
            # entrypoint reach the control socket as audioReceive deltas.
            radio.audio_backend.remove_devices()
            await server._audio_broadcaster.ensure_relay()  # noqa: SLF001
            for _ in range(6):
                _, payload = await _ws_recv_frame(reader, timeout=5.0)
                message = json.loads(payload)
                if message.get("type") == "state_update":
                    audio_receive = message["data"]["changed"]["audioReceive"]
                    assert audio_receive["failure"]["reason"] == "other"
                    break
            else:
                pytest.fail("rx failure delta never arrived")

            radio.audio_backend.add_device(_AUDIO_DEVICE)
            await server._audio_broadcaster.ensure_relay()  # noqa: SLF001
            for _ in range(6):
                _, payload = await _ws_recv_frame(reader, timeout=5.0)
                message = json.loads(payload)
                if message.get("type") == "state_update":
                    audio_receive = message["data"]["changed"]["audioReceive"]
                    assert audio_receive["failure"] is None
                    break
            else:
                pytest.fail("rx recovery delta never arrived")

            from rigplane.audio.session import RxSubscription

            assert isinstance(server._audio_broadcaster._subscription, RxSubscription)
            assert radio.audio_session.rx_demand == 1
            await server._audio_broadcaster._stop_relay()
            assert radio.audio_session.rx_demand == 0
            assert all(not stream.running for stream in radio.audio_backend.rx_streams)
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        server._server.close()  # noqa: SLF001
        await server._server.wait_closed()  # noqa: SLF001
        await server.stop()
        await authority.close()


async def test_connect_document_seeds_the_observed_ptt_baseline() -> None:
    """The connect document's observation counts as the announced baseline.

    Without the seed, a client that connects on ``off`` misses the first
    physical ``on``: the first broadcast would silently seed the marker
    instead of announcing the changed document.
    """
    server = _server(radio=_FactsRadio(), authority=_Authority())
    _observe_ptt(server, ObservedPtt.OFF)
    queue = _register(server)

    await server.current_managed_tx_document()
    assert server._last_announced_observed_ptt is ObservedPtt.OFF  # noqa: SLF001

    _observe_ptt(server, ObservedPtt.ON)
    server._last_state_broadcast = 0.0  # noqa: SLF001
    server._broadcast_state_update()  # noqa: SLF001
    await _await_managed_tx_publication(server)

    events = _managed_tx_events(queue)
    assert len(events) == 1
    assert events[0]["data"]["txObservation"] == {"observedPtt": "on"}


async def test_rx_start_failure_change_is_pushed_without_audio_clients() -> None:
    """Tap-only RX-start failure and recovery re-deliver the state leaf.

    Driven through the production ``ensure_relay()`` transitions (audio
    scope / CW auto-tune path): no audio client came or went, so the
    client-count hook stays silent — the status change itself delivers.
    """
    radio = _FactsRadio()
    server = _server(radio=radio)
    queue: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=100)
    server.register_control_event_queue(queue)
    assert _drain(queue) == []

    try:
        radio.audio_backend.remove_devices()
        await server._audio_broadcaster.ensure_relay()  # noqa: SLF001

        updates = [
            event for event in _drain(queue) if event.get("type") == "state_update"
        ]
        assert len(updates) == 1, updates
        audio_receive = updates[0]["data"]["changed"]["audioReceive"]
        assert audio_receive["failure"]["reason"] == "other"

        # A retry producing the SAME failure record stays silent: the
        # delivery key suppresses unchanged facts (change-driven contract).
        await server._audio_broadcaster.ensure_relay()  # noqa: SLF001
        updates = [
            event for event in _drain(queue) if event.get("type") == "state_update"
        ]
        assert updates == []

        # A retry that succeeds drops the retained failure and delivers the
        # recovered record (the relay keeps running on the fake source).
        radio.audio_backend.add_device(_AUDIO_DEVICE)
        await server._audio_broadcaster.ensure_relay()  # noqa: SLF001
        updates = [
            event for event in _drain(queue) if event.get("type") == "state_update"
        ]
        assert len(updates) == 1, updates
        audio_receive = updates[0]["data"]["changed"]["audioReceive"]
        assert audio_receive["failure"] is None
        from rigplane.audio.session import RxSubscription

        assert isinstance(server._audio_broadcaster._subscription, RxSubscription)
        assert radio.audio_session.rx_demand == 1
    finally:
        await server._audio_broadcaster._stop_relay()
        assert radio.audio_session.rx_demand == 0
        assert all(not stream.running for stream in radio.audio_backend.rx_streams)


@pytest.mark.parametrize("model", ["IC-7300", "IC-7610"])
async def test_serial_identity_reread_pushes_no_response_to_control_ws(
    model: str,
) -> None:
    from rigplane.backends.ic7300 import Ic7300SerialRadio
    from rigplane.backends.icom7610 import Icom7610SerialRadio
    from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
    from rigplane_testkit.serial import CivSerialFake

    tests_root = Path(__file__).resolve().parents[1]
    if str(tests_root) not in sys.path:
        sys.path.insert(0, str(tests_root))
    from test_web_server import _ws_connect, _ws_recv_frame

    fake = CivSerialFake(model=model)
    fake.wrong_model_id()
    radio_type = Ic7300SerialRadio if model == "IC-7300" else Icom7610SerialRadio
    radio = radio_type(
        device="civ-serial-fake",
        civ_link=SerialCivLink(
            device="civ-serial-fake", open_serial_connection=fake.open_serial_connection
        ),
        _enumerate_serial_ports_fn=lambda: [],
    )
    radio._civ_get_timeout = 0.1
    radio._SERIAL_IDENTITY_REREAD_BACKOFF_S = (0.1,)
    radio._SERIAL_IDENTITY_REREAD_STEADY_S = 0.1
    await radio.connect()
    server = _server(radio=radio)
    radio.set_state_change_callback(server._on_radio_state_change)
    server._server = await asyncio.start_server(server._accept_client, "127.0.0.1", 0)
    try:
        reader, writer, _ = await _ws_connect("127.0.0.1", server.port, "/api/v1/ws")
        try:
            while True:
                _, payload = await _ws_recv_frame(reader, timeout=5.0)
                message = json.loads(payload)
                if message.get("type") == "state_update":
                    assert (
                        message["data"]["data"]["connection"]["identity"]["status"]
                        == "identity_mismatch"
                    )
                    break
            fake.answer_identity_after_reconnect_only()
            while True:
                _, payload = await _ws_recv_frame(reader, timeout=5.0)
                message = json.loads(payload)
                if message.get("type") == "state_update":
                    connection = message["data"]["changed"]["connection"]
                    if connection["identity"]["status"] == "no_response":
                        info = _Writer()
                        await server._handle_http(info, "GET", "/api/v1/info")
                        assert (
                            connection["identity"]
                            == info.payload["connection"]["identity"]
                        )
                        break
            await radio.disconnect()
            while True:
                _, payload = await _ws_recv_frame(reader, timeout=5.0)
                message = json.loads(payload)
                if message.get("type") == "state_update":
                    envelope = message["data"]
                    key = "data" if envelope["type"] == "full" else "changed"
                    connection = envelope[key]["connection"]
                    if connection["identity"] is None:
                        break
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        radio.set_state_change_callback(None)
        await radio.disconnect()
        await server.stop()


async def test_session_busy_lifecycle_pushes_hold_and_release_to_control_ws(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from rigplane.runtime.radio import CoreRadio
    from rigplane.runtime.session_lifecycle import AttemptOutcome, AttemptResult
    from rigplane.testing.timing import VirtualClock, await_until
    from rigplane.runtime import session_lifecycle as lifecycle_module
    from types import SimpleNamespace

    clock = VirtualClock()
    monkeypatch.setattr(lifecycle_module, "time", SimpleNamespace(monotonic=clock))

    tests_root = Path(__file__).resolve().parents[1]
    if str(tests_root) not in sys.path:
        sys.path.insert(0, str(tests_root))
    from test_web_server import _ws_connect, _ws_recv_frame

    radio = CoreRadio("127.0.0.1", model="IC-7300")
    lifecycle = radio._session_lifecycle

    async def busy_attempt() -> AttemptResult:
        return AttemptResult(AttemptOutcome.SESSION_BUSY_REJECT)

    async def release() -> None:
        return None

    lifecycle._mech.connect_attempt = busy_attempt
    lifecycle._mech.release = release
    server = _server(radio=radio)
    radio.set_state_change_callback(server._on_radio_state_change)
    server._server = await asyncio.start_server(server._accept_client, "127.0.0.1", 0)
    connect_task = None
    try:
        reader, writer, _ = await _ws_connect("127.0.0.1", server.port, "/api/v1/ws")
        try:
            while True:
                _, payload = await _ws_recv_frame(reader, timeout=5.0)
                message = json.loads(payload)
                if message.get("type") == "state_update":
                    assert (
                        message["data"]["data"]["connection"]["session"]["busy"]
                        is False
                    )
                    break
            connect_task = asyncio.create_task(lifecycle.connect())
            await await_until(lifecycle.startup_busy_hold_event.is_set, timeout=5.0)
            for busy in (True, False):
                while True:
                    _, payload = await _ws_recv_frame(reader, timeout=5.0)
                    message = json.loads(payload)
                    if message.get("type") == "state_update":
                        session = message["data"]["changed"]["connection"]["session"]
                        if session["busy"] is busy:
                            info = _Writer()
                            await server._handle_http(info, "GET", "/api/v1/info")
                            assert session == info.payload["connection"]["session"]
                            break
                if busy:
                    await lifecycle.disconnect()
        finally:
            writer.close()
            await writer.wait_closed()
    finally:
        await lifecycle.disconnect()
        if connect_task is not None:
            await asyncio.gather(connect_task, return_exceptions=True)
        radio.set_state_change_callback(None)
        await server.stop()
