"""Additional coverage tests for rigplane.web.server without real sockets."""

from __future__ import annotations

import asyncio
import io
import json
import pathlib
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rigplane._bounded_queue import BoundedQueue
from rigplane.core.command_service import (
    command_intent_from_request,
    command_response_observation,
)
from rigplane.core.acquisition_scheduler import (
    AcquisitionScheduler,
    RadioStateModelService,
    StateFreshnessService,
)
from rigplane.core.state_pipeline_contracts import FieldPath, Observation, SourceMetadata
from rigplane.core.state_store import FreshnessClock, StateStore
from rigplane.profiles import resolve_radio_profile
from rigplane.radio_state import RadioState
from rigplane.web import server as server_module
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.radio_poller import EnableScope
from rigplane.web.server import WebConfig, WebServer, _send_response, run_web_server


class _FakeSocket:
    def __init__(self, host: str = "127.0.0.1", port: int = 4242) -> None:
        self._host = host
        self._port = port

    def getsockname(self):
        return (self._host, self._port)


class _FakeAsyncServer:
    def __init__(self) -> None:
        self.sockets = [_FakeSocket()]
        self.closed = False

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class _FakeWriter:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False
        self.wait_closed_called = False

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        self.wait_closed_called = True

    def get_extra_info(self, name: str, default=None):
        if name == "peername":
            return ("127.0.0.1", 55555)
        return default


def _reader_with(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


def _response_json(writer: _FakeWriter) -> tuple[int, dict]:
    text = writer.buffer.decode("ascii", errors="replace")
    status = int(text.split(" ", 2)[1])
    body_start = text.index("\r\n\r\n") + 4
    return status, json.loads(text[body_start:] or "{}")


def _state_store_source() -> SourceMetadata:
    return SourceMetadata(
        source="poll_response",
        provider="test",
        transport="fake",
        native_id="test",
    )


def _store_observation(
    path: FieldPath,
    value: object,
    *,
    at: float,
    max_age: float | None = None,
) -> Observation:
    return Observation(
        path=path,
        value=value,
        source=_state_store_source(),
        timestamp_monotonic=at,
        max_age=max_age,
    )


def _scope_radio(*, ready: bool = True, connected: bool = True) -> MagicMock:
    radio = MagicMock()
    radio.radio_ready = ready
    radio.connected = connected
    radio.capabilities = {"scope"}

    # ScopeCapable protocol attrs (Python 3.12+ runtime_checkable)
    radio.on_scope_data = MagicMock()
    radio.scope_stream = MagicMock()
    radio.enable_scope = AsyncMock()
    radio.disable_scope = AsyncMock()
    radio.capture_scope_frame = AsyncMock()
    radio.capture_scope_frames = AsyncMock()
    radio.get_scope_during_tx = AsyncMock(return_value=False)
    radio.set_scope_during_tx = AsyncMock()
    radio.get_scope_center_type = AsyncMock(return_value=0)
    radio.set_scope_center_type = AsyncMock()
    radio.get_scope_fixed_edge = AsyncMock()
    radio.set_scope_fixed_edge = AsyncMock()
    radio.get_scope_edge = AsyncMock(return_value=1)
    radio.set_scope_edge = AsyncMock()
    radio.get_scope_rbw = AsyncMock(return_value=0)
    radio.set_scope_rbw = AsyncMock()
    radio.get_scope_vbw = AsyncMock(return_value=False)
    radio.set_scope_vbw = AsyncMock()

    # Scope control settings (0x27 sub-commands)
    radio.get_scope_receiver = AsyncMock(return_value=0)
    radio.set_scope_receiver = AsyncMock()
    radio.get_scope_dual = AsyncMock(return_value=False)
    radio.set_scope_dual = AsyncMock()
    radio.get_scope_mode = AsyncMock(return_value=0)
    radio.set_scope_mode = AsyncMock()
    radio.get_scope_span = AsyncMock(return_value=0)
    radio.set_scope_span = AsyncMock()
    radio.get_scope_speed = AsyncMock(return_value=0)
    radio.set_scope_speed = AsyncMock()
    radio.get_scope_ref = AsyncMock(return_value=0.0)
    radio.set_scope_ref = AsyncMock()
    radio.get_scope_hold = AsyncMock(return_value=False)
    radio.set_scope_hold = AsyncMock()

    return radio


class _StateNotifyRadio(MagicMock):
    """Minimal mock that satisfies StateNotifyCapable so server registers callbacks."""

    def set_state_change_callback(self, callback: object) -> None:
        self._state_change_callback = callback

    def set_reconnect_callback(self, callback: object) -> None:
        self._reconnect_callback = callback


class _ProfiledStateNotifyRadio(_StateNotifyRadio):
    @property
    def state_store(self) -> StateStore:
        return self._state_store


@pytest.mark.asyncio
async def test_start_and_stop_with_radio_sets_callbacks() -> None:
    radio = _StateNotifyRadio()
    radio.state_cache = MagicMock()
    radio.disconnect = AsyncMock()
    radio.connected = True
    radio.radio_ready = True
    radio.control_connected = True
    fake_server = _FakeAsyncServer()
    fake_poller = MagicMock()

    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    with (
        patch(
            "rigplane.web.web_startup.asyncio.start_server",
            new=AsyncMock(return_value=fake_server),
        ),
        patch("rigplane.web.web_startup.RadioPoller", return_value=fake_poller),
    ):
        await srv.start()
        assert srv.port == 4242
        assert radio._state_change_callback is not None
        assert radio._state_change_callback == srv._on_radio_state_change
        assert radio._reconnect_callback is not None
        assert radio._reconnect_callback == srv._on_radio_reconnect
        fake_poller.start.assert_called_once()
        await srv.stop()

    fake_poller.stop.assert_called_once()
    # radio.disconnect is NOT called by WebServer.stop() — it's the caller's
    # responsibility via the context manager (async with radio:).
    radio.disconnect.assert_not_awaited()
    assert fake_server.closed is True


@pytest.mark.asyncio
async def test_start_attaches_shared_state_model_service_for_acquisition_profile() -> None:
    radio = _ProfiledStateNotifyRadio()
    radio.profile = resolve_radio_profile(model="IC-7610")
    radio.model = radio.profile.model
    radio.capabilities = set(radio.profile.capabilities)
    radio._state_store = StateStore()
    radio.state_cache = MagicMock()
    radio.disconnect = AsyncMock()
    radio.connected = True
    radio.radio_ready = True
    radio.control_connected = True
    fake_server = _FakeAsyncServer()
    fake_poller = MagicMock()

    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    with (
        patch(
            "rigplane.web.web_startup.asyncio.start_server",
            new=AsyncMock(return_value=fake_server),
        ),
        patch("rigplane.web.web_startup.RadioPoller", return_value=fake_poller) as poller_cls,
    ):
        await srv.start()
        await srv.stop()

    assert srv.command_state_store is radio.state_store
    assert isinstance(radio._acquisition_scheduler, AcquisitionScheduler)
    assert isinstance(radio._state_model_service, RadioStateModelService)
    assert isinstance(radio._state_freshness_service, StateFreshnessService)
    assert srv._state_freshness_service is radio._state_freshness_service
    assert radio._meter_observation_coalescer is not None
    assert poller_cls.call_args.kwargs["state_store"] is radio.state_store


def test_state_store_s_meter_change_broadcasts_without_legacy_revision_event() -> None:
    radio = MagicMock()
    radio.profile = resolve_radio_profile(model="IC-7610")
    radio.model = radio.profile.model
    radio.capabilities = set(radio.profile.capabilities)
    srv = WebServer(radio, WebConfig(state_diagnostics=True))
    queue = BoundedQueue[dict[str, object]](maxsize=4)
    srv.register_control_event_queue(queue)
    path = FieldPath.receiver("0", "meters", "s_meter")
    srv.command_state_store.apply(_store_observation(path, 73, at=time.monotonic()))

    srv._on_radio_state_change("state_store_changed", {"paths": [str(path)]})

    event = queue.get_nowait()
    state_update = queue.get_nowait()
    assert event == {
        "type": "event",
        "name": "state_store_changed",
        "data": {"paths": [str(path)]},
    }
    assert state_update["type"] == "state_update"
    payload = srv.build_public_state()
    assert payload["main"]["sMeter"] == 73
    assert not any(
        item.kind == "revision_producing_event"
        for item in srv.state_diagnostics.events()
    )


def test_state_store_freshness_refresh_broadcasts_without_semantic_change() -> None:
    radio = MagicMock()
    radio.profile = resolve_radio_profile(model="IC-7610")
    radio.model = radio.profile.model
    radio.capabilities = set(radio.profile.capabilities)
    srv = WebServer(radio, WebConfig(state_diagnostics=True))
    queue = BoundedQueue[dict[str, object]](maxsize=4)
    srv.register_control_event_queue(queue)
    path = FieldPath.receiver("0", "meters", "s_meter")
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    srv.command_state_store = store
    store.apply(_store_observation(path, 73, at=clock.now(), max_age=0.5))
    clock.advance(0.6)
    store.mark_stale_due()
    refreshed = store.apply(_store_observation(path, 73, at=clock.now(), max_age=0.5))

    assert refreshed.changes == ()
    srv._on_radio_state_change("state_store_changed", {"paths": [str(path)]})

    event = queue.get_nowait()
    state_update = queue.get_nowait()
    assert event == {
        "type": "event",
        "name": "state_store_changed",
        "data": {"paths": [str(path)]},
    }
    assert state_update["type"] == "state_update"
    assert any(
        item.kind == "web_delivery_trigger"
        and item.details["freshness_revision"] == refreshed.freshness_revision
        for item in srv.state_diagnostics.events()
    )
    assert not any(
        item.kind == "revision_producing_event"
        for item in srv.state_diagnostics.events()
    )


@pytest.mark.asyncio
async def test_start_disables_reuse_port_on_windows() -> None:
    srv = WebServer(None, WebConfig(host="127.0.0.1", port=0, discovery=False))
    fake_server = _FakeAsyncServer()

    with (
        patch("rigplane.web.web_startup.sys.platform", "win32"),
        patch(
            "rigplane.web.web_startup.asyncio.start_server",
            new=AsyncMock(return_value=fake_server),
        ) as start_server,
    ):
        await srv.start()
        await srv.stop()

    assert start_server.await_args.kwargs["reuse_port"] is False


def test_shutdown_signal_handler_falls_back_when_loop_does_not_support_signals(
    monkeypatch,
) -> None:
    calls: list[tuple[int, object]] = []
    triggered = 0

    class FakeLoop:
        def add_signal_handler(self, *_args: object) -> None:
            raise NotImplementedError

    def fake_signal(sig: int, handler: object) -> None:
        calls.append((sig, handler))

    def on_signal() -> None:
        nonlocal triggered
        triggered += 1

    monkeypatch.setattr(server_module._signal, "signal", fake_signal)

    server_module._install_shutdown_signal_handlers(FakeLoop(), on_signal)

    assert [sig for sig, _handler in calls] == [
        server_module._signal.SIGTERM,
        server_module._signal.SIGINT,
    ]
    calls[0][1](server_module._signal.SIGTERM, None)  # type: ignore[operator]
    assert triggered == 1


@pytest.mark.asyncio
async def test_stop_handles_disconnect_failure_and_cancels_client_tasks() -> None:
    radio = MagicMock()
    radio.disconnect = AsyncMock(side_effect=RuntimeError("disconnect failed"))
    srv = WebServer(radio)
    srv._server = _FakeAsyncServer()
    srv._radio_poller = MagicMock()

    blocker = asyncio.Event()

    async def slow_client() -> None:
        await blocker.wait()

    task = asyncio.create_task(slow_client())
    srv._client_tasks.add(task)
    await srv.stop()
    assert task.cancelled() or task.done()


@pytest.mark.asyncio
async def test_start_aborts_before_listening_when_radio_not_ready() -> None:
    radio = MagicMock()
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))

    with (
        patch(
            "rigplane.web.web_startup.assert_radio_startup_ready",
            side_effect=RuntimeError("web startup aborted"),
        ),
        patch(
            "rigplane.web.web_startup.asyncio.start_server", new=AsyncMock()
        ) as start_server,
    ):
        with pytest.raises(RuntimeError, match="web startup aborted"):
            await srv.start()
    start_server.assert_not_awaited()


@pytest.mark.asyncio
async def test_accept_client_max_clients_and_normal_path() -> None:
    srv = WebServer(None, WebConfig(max_clients=0))
    writer = _FakeWriter()
    srv._accept_client(_reader_with(b""), writer)
    assert writer.closed is True

    srv2 = WebServer(None, WebConfig(max_clients=10))
    with patch.object(srv2, "_handle_connection", new=AsyncMock()) as handle_conn:
        writer2 = _FakeWriter()
        srv2._accept_client(_reader_with(b""), writer2)
        await asyncio.sleep(0)
    handle_conn.assert_awaited()


@pytest.mark.asyncio
async def test_read_request_parses_and_handles_invalid_cases() -> None:
    srv = WebServer(None)
    reader = _reader_with(
        b"GET /x%20y?q=1 HTTP/1.1\r\nHost: localhost\r\nX-Test: abc\r\n\r\n"
    )
    method, path, headers, query = await srv._read_request(reader)  # noqa: SLF001
    assert method == "GET"
    assert path == "/x y"
    assert headers["host"] == "localhost"
    assert headers["x-test"] == "abc"
    assert query == {"q": ["1"]}

    bad = _reader_with(b"BROKEN\r\n\r\n")
    assert await srv._read_request(bad) is None  # noqa: SLF001

    async def timeout_wait_for(coro, timeout):
        del timeout
        if hasattr(coro, "close"):
            coro.close()
        raise asyncio.TimeoutError

    with patch("rigplane.web.server.asyncio.wait_for", side_effect=timeout_wait_for):
        assert await srv._read_request(_reader_with(b"GET / HTTP/1.1\r\n")) is None  # noqa: SLF001


@pytest.mark.asyncio
async def test_handle_http_routes_and_405_404() -> None:
    srv = WebServer(None)
    writer = _FakeWriter()
    with patch("rigplane.web.server._send_response", new=AsyncMock()) as send_resp:
        await srv._handle_http(writer, "POST", "/")  # noqa: SLF001
    send_resp.assert_awaited_once()

    srv2 = WebServer(None)
    writer2 = _FakeWriter()
    srv2._serve_info = AsyncMock()
    srv2._serve_state = AsyncMock()
    srv2._serve_capabilities = AsyncMock()
    srv2._serve_station_status = AsyncMock()
    srv2._serve_static = AsyncMock()
    with patch("rigplane.web.server._send_response", new=AsyncMock()) as send_resp2:
        await srv2._handle_http(writer2, "GET", "/api/v1/info")  # noqa: SLF001
        await srv2._handle_http(writer2, "GET", "/api/v1/state")  # noqa: SLF001
        await srv2._handle_http(writer2, "GET", "/api/v1/capabilities")  # noqa: SLF001
        await srv2._handle_http(writer2, "GET", "/api/v1/station")  # noqa: SLF001
        await srv2._handle_http(writer2, "GET", "/")  # noqa: SLF001
        await srv2._handle_http(writer2, "GET", "/file.js")  # noqa: SLF001
        await srv2._handle_http(writer2, "GET", "relative-path")  # noqa: SLF001
    assert srv2._serve_info.await_count == 1
    assert srv2._serve_state.await_count == 1
    assert srv2._serve_capabilities.await_count == 1
    assert srv2._serve_station_status.await_count == 1
    assert srv2._serve_static.await_count == 2
    send_resp2.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_single_command_uses_http_command_service_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, object] = {}

    async def fake_enqueue(
        self: ControlHandler,
        name: str,
        params: dict[str, object],
        *,
        command_id: str | None = None,
        source: str = "websocket",
    ) -> dict[str, object]:
        del self
        seen.update(
            {
                "name": name,
                "params": params,
                "command_id": command_id,
                "source": source,
            }
        )
        return {"freq": params["freq"], "receiver": params["receiver"]}

    monkeypatch.setattr(ControlHandler, "_enqueue_command", fake_enqueue)
    srv = WebServer(SimpleNamespace(connected=True, capabilities=set()), WebConfig())
    writer = _FakeWriter()

    await srv._handle_http_single_command(  # noqa: SLF001
        writer,
        {
            "id": "http-set-freq",
            "name": "set_freq",
            "params": {"freq": 14_074_000, "receiver": 0},
        },
    )

    status, body = _response_json(writer)
    assert status == 200
    assert body["ok"] is True
    assert seen == {
        "name": "set_freq",
        "params": {"freq": 14_074_000, "receiver": 0},
        "command_id": "http-set-freq",
        "source": "http",
    }


@pytest.mark.asyncio
async def test_http_raw_civ_transaction_enters_command_lifecycle() -> None:
    frame = SimpleNamespace(command=0x03, sub=None, data=b"\x01")
    radio = SimpleNamespace(
        connected=True,
        capabilities=set(),
        send_civ_transaction=AsyncMock(
            return_value=SimpleNamespace(
                status="response",
                frame=frame,
                frame_bytes=b"\xfe\xfe\xe0\x98\x03\x01\xfd",
            )
        ),
    )
    srv = WebServer(radio, WebConfig())
    writer = _FakeWriter()
    payload = json.dumps(
        {"id": "raw-1", "command": 0x03, "data": "", "expect": "data"}
    ).encode()

    await srv._handle_http_civ_transaction(  # noqa: SLF001
        writer,
        headers={"content-length": str(len(payload))},
        reader=_reader_with(payload),
    )

    status, body = _response_json(writer)
    assert status == 200
    assert body["id"] == "raw-1"
    radio.send_civ_transaction.assert_awaited_once_with(
        0x03,
        sub=None,
        data=b"",
        expect="data",
        timeout=None,
    )
    events = srv._http_command_service.lifecycle_events()  # noqa: SLF001
    assert [event.state for event in events[:4]] == [
        "accepted",
        "queued",
        "sent",
        "acknowledged",
    ]
    assert events[0].command_id == "raw-1"
    assert events[0].source == "http"


@pytest.mark.asyncio
async def test_http_power_enters_command_service_and_keeps_delivery_mirror() -> None:
    radio = SimpleNamespace(
        connected=True,
        control_connected=True,
        capabilities={"power_control"},
        set_powerstat=AsyncMock(),
    )
    srv = WebServer(radio, WebConfig())
    writer = _FakeWriter()
    payload = json.dumps({"state": "off"}).encode()

    await srv._handle_http(  # noqa: SLF001
        writer,
        "POST",
        "/api/v1/radio/power",
        headers={"content-length": str(len(payload))},
        reader=_reader_with(payload),
    )

    status, body = _response_json(writer)
    assert status == 200
    assert body == {"status": "ok", "power": "off"}
    radio.set_powerstat.assert_awaited_once_with(False)
    assert srv._radio_state.power_on is False  # noqa: SLF001
    assert srv.command_state_store.snapshot().field("global.tx_state.power_on").value is False


@pytest.mark.asyncio
async def test_http_command_batch_preparation_uses_shared_command_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio = SimpleNamespace(connected=True, capabilities={"tuner"})
    srv = WebServer(radio, WebConfig())
    writer = _FakeWriter()
    seen: dict[str, object] = {}

    def fake_put_ordered(
        command: object,
        *,
        future: asyncio.Future[None] | None = None,
        command_id: str | None = None,
        source: str | None = None,
        command_service=None,
    ) -> None:
        del command
        seen.update(
            {
                "command_id": command_id,
                "source": source,
                "command_service": command_service,
                "overlays": command_service.pending_overlays(
                    source="http",
                    session_id=None,
                    command_id=command_id,
                ),
            }
        )
        assert command_id is not None
        intent = command_intent_from_request(
            "set_freq",
            {"freq": 14_074_000, "receiver": 0},
            source="http",
            command_id=command_id,
        )
        command_service.apply_observation(
            command_response_observation(
                intent,
                timestamp_monotonic=123.0,
                provider="test",
            )
        )
        assert future is not None
        future.set_result(None)

    monkeypatch.setattr(srv.command_queue, "put_ordered", fake_put_ordered)
    payload = json.dumps(
        {
            "steps": [
                {
                    "id": "http-batch-freq",
                    "name": "set_freq",
                    "params": {"freq": 14_074_000, "receiver": 0},
                }
            ]
        }
    ).encode()

    await srv._handle_http_commands(  # noqa: SLF001
        "/api/v1/commands/batch",
        writer,
        headers={"content-length": str(len(payload))},
        reader=_reader_with(payload),
    )

    status, body = _response_json(writer)
    assert status == 200
    assert body["ok"] is True
    assert body["results"][0]["ok"] is True
    assert isinstance(seen["command_id"], str)
    assert seen["source"] == "http"
    assert seen["command_service"] is srv.command_service
    assert seen["command_service"]._state_store is srv.command_state_store  # noqa: SLF001
    assert seen["overlays"] != ()
    assert (
        srv.command_state_store.snapshot().field("receiver.0.freq_mode.freq_hz").value
        == 14_074_000
    )
    assert (
        seen["command_service"].pending_overlays(
            source="http",
            session_id=None,
            command_id=seen["command_id"],
        )
        == ()
    )


@pytest.mark.asyncio
async def test_websocket_reused_command_ids_are_scoped_per_connection() -> None:
    radio = SimpleNamespace(connected=True, capabilities=set())
    srv = WebServer(radio, WebConfig())
    handler_a = ControlHandler(
        SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock()),
        radio,
        "9.9.9",
        "IC-7610",
        server=srv,
        session_id="ws-a",
    )
    handler_b = ControlHandler(
        SimpleNamespace(send_text=AsyncMock(), recv=AsyncMock()),
        radio,
        "9.9.9",
        "IC-7610",
        server=srv,
        session_id="ws-b",
    )

    await handler_a._enqueue_command(  # noqa: SLF001
        "set_freq",
        {"freq": 14_074_000, "receiver": 0},
        command_id="ws-shared",
        source="websocket",
    )
    await handler_b._enqueue_command(  # noqa: SLF001
        "set_freq",
        {"freq": 14_075_000, "receiver": 0},
        command_id="ws-shared",
        source="websocket",
    )

    overlays_a = srv.command_service.pending_overlays(
        source="websocket",
        session_id="ws-a",
        command_id="ws-shared",
    )
    overlays_b = srv.command_service.pending_overlays(
        source="websocket",
        session_id="ws-b",
        command_id="ws-shared",
    )

    assert len(overlays_a) == 1
    assert overlays_a[0].value == 14_074_000
    assert len(overlays_b) == 1
    assert overlays_b[0].value == 14_075_000
    accepted = [
        event
        for event in srv.command_service.lifecycle_events()
        if event.command_id == "ws-shared" and event.state == "accepted"
    ]
    assert [event.details["session_id"] for event in accepted] == ["ws-a", "ws-b"]


@pytest.mark.asyncio
async def test_http_command_batch_timeout_marks_command_timed_out_and_expires_overlay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio = SimpleNamespace(connected=True, capabilities=set())
    srv = WebServer(radio, WebConfig())
    writer = _FakeWriter()
    seen: dict[str, object] = {}
    real_wait_for = asyncio.wait_for

    def fake_put_ordered(
        command: object,
        *,
        future: asyncio.Future[None] | None = None,
        command_id: str | None = None,
        source: str | None = None,
        command_service=None,
    ) -> None:
        del command
        assert future is not None
        seen.update(
            {
                "future": future,
                "command_id": command_id,
                "source": source,
                "command_service": command_service,
                "overlays": command_service.pending_overlays(
                    source="http",
                    session_id=None,
                    command_id=command_id,
                ),
            }
        )

    async def fake_wait_for(awaitable, timeout):
        if awaitable is seen.get("future"):
            awaitable.cancel()
            raise TimeoutError("batch step timed out")
        return await real_wait_for(awaitable, timeout)

    monkeypatch.setattr(srv.command_queue, "put_ordered", fake_put_ordered)
    monkeypatch.setattr("rigplane.web.server.asyncio.wait_for", fake_wait_for)
    payload = json.dumps(
        {
            "steps": [
                {
                    "id": "http-timeout",
                    "name": "set_freq",
                    "params": {"freq": 14_074_000, "receiver": 0},
                }
            ]
        }
    ).encode()

    await srv._handle_http_commands(  # noqa: SLF001
        "/api/v1/commands/batch",
        writer,
        headers={"content-length": str(len(payload))},
        reader=_reader_with(payload),
    )

    status, body = _response_json(writer)
    assert status == 200
    assert body["ok"] is False
    assert body["results"][0]["status"] == "timed_out"
    assert seen["overlays"] != ()
    assert (
        srv.command_service.pending_overlays(
            source="http",
            session_id=None,
            command_id=seen["command_id"],
        )
        == ()
    )
    timed_out = [
        event
        for event in srv.command_service.lifecycle_events()
        if event.command_id == seen["command_id"] and event.state == "timed_out"
    ]
    assert len(timed_out) == 1
    assert timed_out[0].source == "http"
    assert timed_out[0].message == "batch step timed out"


@pytest.mark.asyncio
async def test_healthz_reports_process_liveness() -> None:
    srv = WebServer(None, WebConfig(host="127.0.0.1", port=0))
    writer = _FakeWriter()

    await srv._handle_http(writer, "GET", "/healthz")  # noqa: SLF001

    status, data = _response_json(writer)
    assert status == 200
    assert data["status"] == "ok"
    assert data["version"]
    assert isinstance(data["pid"], int)


@pytest.mark.asyncio
async def test_readyz_reflects_station_readiness() -> None:
    ready_radio = SimpleNamespace(radio_ready=True, connected=True, capabilities=set())
    ready_srv = WebServer(ready_radio, WebConfig(host="127.0.0.1", port=0))
    ready_writer = _FakeWriter()

    await ready_srv._handle_http(ready_writer, "GET", "/readyz")  # noqa: SLF001

    status, data = _response_json(ready_writer)
    assert status == 200
    assert data == {"status": "ready", "radioReady": True}

    not_ready_srv = WebServer(None, WebConfig(host="127.0.0.1", port=0))
    not_ready_writer = _FakeWriter()

    await not_ready_srv._handle_http(not_ready_writer, "GET", "/readyz")  # noqa: SLF001

    status, data = _response_json(not_ready_writer)
    assert status == 503
    assert data == {"status": "not_ready", "radioReady": False}


@pytest.mark.asyncio
async def test_runtime_endpoint_reports_process_bind_radio_and_bridge_status() -> None:
    radio = SimpleNamespace(
        model="IC-7610",
        backend_id="rigplane",
        connected=True,
        control_connected=True,
        radio_ready=True,
        capabilities=set(),
    )
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0, auth_token="token"))
    srv._server = _FakeAsyncServer()  # noqa: SLF001
    srv._audio_bridge = SimpleNamespace(  # noqa: SLF001
        running=True,
        stats={"rx_frames": 3, "tx_frames": 4},
    )
    srv._runtime_log_path = "/tmp/rigplane.log"  # noqa: SLF001
    srv._runtime_rigctld_addr = "127.0.0.1:4532"  # noqa: SLF001
    writer = _FakeWriter()

    await srv._handle_http(  # noqa: SLF001
        writer,
        "GET",
        "/api/v1/runtime",
        headers={"authorization": "Bearer token"},
    )

    status, data = _response_json(writer)
    assert status == 200
    assert data["pid"] > 0
    assert data["uptimeSeconds"] >= 0
    assert data["version"]
    assert data["bind"] == {"host": "127.0.0.1", "port": 4242}
    assert data["logPath"] == "/tmp/rigplane.log"
    assert data["authRequired"] is True
    assert data["backend"] == "rigplane"
    assert data["radio"] == {
        "model": "IC-7610",
        "connected": True,
        "controlConnected": True,
        "radioReady": True,
    }
    assert data["station"]["readiness"] == "ready_with_radio"
    assert data["station"]["radioAvailable"] is True
    assert data["station"]["backend"] == "rigplane"
    assert data["rigctld"] == {"enabled": True, "address": "127.0.0.1:4532"}
    assert data["bridge"]["running"] is True
    assert data["bridge"]["stats"] == {"rx_frames": 3, "tx_frames": 4}
    assert data["lastError"] is None
    srv._server = None  # noqa: SLF001


@pytest.mark.asyncio
async def test_station_endpoint_reports_selection_metadata_and_guidance() -> None:
    serial_radio = SimpleNamespace(
        model="IC-7300",
        backend_id="icom_serial",
        connected=False,
        control_connected=False,
        radio_ready=False,
        capabilities=set(),
    )
    srv = WebServer(serial_radio, WebConfig(host="127.0.0.1", port=0))
    srv._server = _FakeAsyncServer()  # noqa: SLF001
    writer = _FakeWriter()

    await srv._handle_http(writer, "GET", "/api/v1/station")  # noqa: SLF001

    status, data = _response_json(writer)
    assert status == 200
    assert data["schema"] == "rigplane.station.status.v1"
    assert data["displayName"] == "IC-7300"
    assert data["baseUrl"] == "http://127.0.0.1:4242"
    assert data["healthUrl"] == "http://127.0.0.1:4242/healthz"
    assert data["runtimeUrl"] == "http://127.0.0.1:4242/api/v1/runtime"
    assert data["station"]["readiness"] == "no_usb_radio_connected"
    assert data["station"]["radioAvailable"] is False
    assert "Connect the radio by USB" in data["station"]["message"]
    assert data["radio"] == {
        "model": "IC-7300",
        "connected": False,
        "controlConnected": False,
        "radioReady": False,
    }
    srv._server = None  # noqa: SLF001


def test_startup_event_reports_actual_runtime_urls_and_log_path() -> None:
    srv = WebServer(
        None,
        WebConfig(
            host="127.0.0.1",
            port=0,
            emit_startup_event=True,
        ),
    )
    srv._server = _FakeAsyncServer()  # noqa: SLF001
    srv._runtime_log_path = "/tmp/rigplane-managed.log"  # noqa: SLF001
    out = io.StringIO()

    srv.emit_startup_event(out)

    payload = json.loads(out.getvalue())
    assert payload["type"] == "rigplane.runtime.started"
    assert payload["pid"] > 0
    assert payload["baseUrl"] == "http://127.0.0.1:4242"
    assert payload["healthUrl"] == "http://127.0.0.1:4242/healthz"
    assert payload["runtimeUrl"] == "http://127.0.0.1:4242/api/v1/runtime"
    assert payload["logPath"] == "/tmp/rigplane-managed.log"


@pytest.mark.asyncio
async def test_serve_static_forbidden_missing_read_error_and_success(tmp_path) -> None:
    static_dir = tmp_path / "static"
    static_dir.mkdir()
    index = static_dir / "index.html"
    index.write_text("<html>ok</html>", encoding="utf-8")

    srv = WebServer(None, WebConfig(static_dir=static_dir))
    writer = _FakeWriter()

    await srv._serve_static(writer, "../secret")  # noqa: SLF001
    assert b"403 Forbidden" in writer.buffer

    writer = _FakeWriter()
    await srv._serve_static(writer, "missing.txt")  # noqa: SLF001
    assert b"404 Not Found" in writer.buffer

    writer = _FakeWriter()
    with patch.object(pathlib.Path, "read_bytes", side_effect=OSError("read fail")):
        await srv._serve_static(writer, "index.html")  # noqa: SLF001
    assert b"500 Internal Server Error" in writer.buffer

    writer = _FakeWriter()
    await srv._serve_static(writer, "index.html")  # noqa: SLF001
    text = writer.buffer.decode("ascii", errors="replace")
    assert "200 OK" in text
    assert "Cache-Control: no-cache, no-store, must-revalidate" in text


@pytest.mark.asyncio
async def test_handle_websocket_missing_key_unknown_channel_and_control_handler() -> (
    None
):
    srv = WebServer(None)
    writer = _FakeWriter()
    with patch("rigplane.web.server._send_response", new=AsyncMock()) as send_resp:
        await srv._handle_websocket(_reader_with(b""), writer, "/api/v1/ws", {})  # noqa: SLF001
    send_resp.assert_awaited_once()

    ws_unknown = MagicMock()
    ws_unknown.close = AsyncMock()
    ws_unknown.keepalive_loop = AsyncMock()
    with patch("rigplane.web.server.WebSocketConnection", return_value=ws_unknown):
        await srv._handle_websocket(  # noqa: SLF001
            _reader_with(b""),
            _FakeWriter(),
            "/api/v1/unknown",
            {"sec-websocket-key": "abc"},
        )
    ws_unknown.close.assert_awaited_once_with(1008, "unknown channel")

    ws_ok = MagicMock()

    async def keepalive_loop(_interval: float) -> None:
        await asyncio.sleep(3600)

    ws_ok.keepalive_loop = keepalive_loop
    ws_ok.close = AsyncMock()
    handler = MagicMock()
    handler.run = AsyncMock(side_effect=RuntimeError("handler failed"))
    writer_ok = _FakeWriter()
    with (
        patch("rigplane.web.server.WebSocketConnection", return_value=ws_ok),
        patch("rigplane.web.server.ControlHandler", return_value=handler),
    ):
        await srv._handle_websocket(  # noqa: SLF001
            _reader_with(b""),
            writer_ok,
            "/api/v1/ws",
            {"sec-websocket-key": "abc"},
        )
    assert b"101 Switching Protocols" in writer_ok.buffer
    handler.run.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_connection_none_http_ws_and_exception() -> None:
    srv = WebServer(None)
    writer = _FakeWriter()

    srv._read_request = AsyncMock(return_value=None)
    await srv._handle_connection(_reader_with(b""), writer)  # noqa: SLF001
    assert writer.closed is True

    writer2 = _FakeWriter()
    srv._read_request = AsyncMock(return_value=("GET", "/api/v1/info", {}, {}))
    srv._handle_http = AsyncMock()
    await srv._handle_connection(_reader_with(b""), writer2)  # noqa: SLF001
    srv._handle_http.assert_awaited_once()

    writer3 = _FakeWriter()
    srv._read_request = AsyncMock(
        return_value=(
            "GET",
            "/api/v1/ws",
            {"upgrade": "websocket", "connection": "Upgrade"},
            {},
        )
    )
    srv._handle_websocket = AsyncMock()
    await srv._handle_connection(_reader_with(b""), writer3)  # noqa: SLF001
    srv._handle_websocket.assert_awaited_once()

    writer4 = _FakeWriter()
    srv._read_request = AsyncMock(side_effect=RuntimeError("boom"))
    await srv._handle_connection(_reader_with(b""), writer4)  # noqa: SLF001
    assert writer4.closed is True


@pytest.mark.asyncio
async def test_scope_health_and_radio_state_event_paths() -> None:
    radio = _scope_radio()
    srv = WebServer(radio)

    frame = SimpleNamespace(pixels=b"\x00\x01")
    before = srv._scope_last_nonzero
    srv._scope_health_check(frame)  # noqa: SLF001
    assert srv._scope_last_nonzero >= before

    bad = SimpleNamespace(pixels=123)
    srv._scope_health_check(bad)  # noqa: SLF001

    # Meter state change still triggers broadcast_state_update
    srv._on_radio_state_change("meter", {"type": "power", "raw": 77})  # noqa: SLF001

    scope_handler = MagicMock()
    srv._scope_handlers.add(scope_handler)
    radio._fetch_initial_state = AsyncMock()
    srv._on_radio_reconnect()  # noqa: SLF001
    await asyncio.sleep(0.05)  # let the refetch task complete
    cmds = srv.command_queue.drain
    assert any(isinstance(c, EnableScope) for c in cmds())


@pytest.mark.asyncio
async def test_http_snapshot_matches_initial_ws_full_state_for_same_store_revision() -> None:
    srv = WebServer(None)
    srv.command_state_store.apply(
        _store_observation(
            FieldPath.active("0", "freq_mode", "freq_hz"),
            14_074_000,
            at=1.0,
            max_age=10.0,
        )
    )
    srv.command_state_store.apply(
        _store_observation(
            FieldPath.receiver("0", "meters", "s_meter"),
            42,
            at=1.1,
            max_age=0.5,
        )
    )

    writer = _FakeWriter()
    await srv._serve_state(writer)  # noqa: SLF001
    _, http_body = _response_json(writer)

    ws = MagicMock()
    sent: list[dict[str, object]] = []

    async def _send_text(payload: str) -> None:
        sent.append(json.loads(payload))

    ws.send_text = _send_text

    handler = ControlHandler(ws, None, "0.0.0-test", "IC-TEST", server=srv)
    await handler._send_state_snapshot()  # noqa: SLF001

    assert sent[0]["type"] == "state_update"
    ws_body = sent[0]["data"]["data"]  # type: ignore[index]
    assert ws_body == http_body


def test_meter_only_state_store_change_emits_web_delta_without_legacy_revision() -> None:
    srv = WebServer(None)
    q = asyncio.Queue()
    srv.register_control_event_queue(q)
    srv.command_state_store.apply(
        _store_observation(
            FieldPath.active("0", "freq_mode", "freq_hz"),
            14_074_000,
            at=1.0,
            max_age=10.0,
        )
    )
    srv._broadcast_state_update()  # noqa: SLF001
    q.get_nowait()

    srv.command_state_store.apply(
        _store_observation(
            FieldPath.receiver("0", "meters", "s_meter"),
            55,
            at=1.1,
            max_age=0.5,
        )
    )
    srv._last_state_broadcast = 0.0  # noqa: SLF001
    srv._broadcast_state_update()  # noqa: SLF001
    event = q.get_nowait()

    assert event["type"] == "state_update"
    assert event["data"]["type"] == "delta"
    assert event["data"]["changed"]["main"]["sMeter"] == 55
    assert event["data"]["stateRevision"] == 2
    assert event["data"]["revision"] == 2


def test_freshness_only_state_store_change_emits_web_delta() -> None:
    clock = FreshnessClock(start=5.0)
    store = StateStore(freshness_clock=clock)
    srv = WebServer(None)
    srv.command_state_store = store
    srv.command_service._state_store = store  # noqa: SLF001
    srv._http_command_service._state_store = store  # noqa: SLF001
    q = asyncio.Queue()
    srv.register_control_event_queue(q)

    store.apply(
        _store_observation(
            FieldPath.receiver("0", "meters", "s_meter"),
            12,
            at=clock.now(),
            max_age=0.5,
        )
    )
    srv._broadcast_state_update()  # noqa: SLF001
    q.get_nowait()

    clock.advance(0.6)
    delta = store.mark_stale_due()
    assert delta.freshness

    srv._last_state_broadcast = 0.0  # noqa: SLF001
    srv._broadcast_state_update()  # noqa: SLF001
    event = q.get_nowait()

    assert event["type"] == "state_update"
    assert event["data"]["type"] == "delta"
    assert event["data"]["changed"]["freshnessRevision"] == 2
    assert event["data"]["stateRevision"] == 1


def test_initial_full_state_envelope_does_not_consume_broadcast_delta() -> None:
    srv = WebServer(None)
    q = asyncio.Queue()
    srv.register_control_event_queue(q)
    srv.command_state_store.apply(
        _store_observation(
            FieldPath.active("0", "freq_mode", "freq_hz"),
            14_074_000,
            at=1.0,
        )
    )
    srv._broadcast_state_update()  # noqa: SLF001
    q.get_nowait()

    srv.command_state_store.apply(
        _store_observation(
            FieldPath.global_("tx_state", "ptt"),
            True,
            at=1.1,
        )
    )
    initial = srv.build_state_update_envelope(force_full=True)
    assert initial["type"] == "full"
    assert initial["data"]["ptt"] is True

    srv._last_state_broadcast = 0.0  # noqa: SLF001
    srv._broadcast_state_update()  # noqa: SLF001
    event = q.get_nowait()

    assert event["data"]["type"] == "delta"
    assert event["data"]["changed"]["ptt"] is True
    assert event["data"]["stateRevision"] == 2


@pytest.mark.asyncio
async def test_initial_full_state_envelope_revisions_match_legacy_seeded_http_state() -> None:
    srv = WebServer(None)
    legacy = RadioState()
    legacy.main.freq = 14_250_000
    legacy.main.mode = "USB"
    legacy.ptt = True
    srv._radio_state = legacy  # noqa: SLF001

    envelope = srv.build_state_update_envelope(force_full=True)
    assert envelope["type"] == "full"
    ws_state = envelope["data"]

    assert envelope["revision"] == ws_state["stateRevision"]
    assert envelope["stateRevision"] == ws_state["stateRevision"]
    assert envelope["freshnessRevision"] == ws_state["freshnessRevision"]

    writer = _FakeWriter()
    await srv._serve_state(writer)  # noqa: SLF001
    status, http_state = _response_json(writer)

    assert status == 200
    assert http_state == ws_state
    assert http_state["revision"] == http_state["stateRevision"]
    assert http_state["freshnessRevision"] == ws_state["freshnessRevision"]


@pytest.mark.asyncio
async def test_state_response_refreshes_live_connection_payload_without_revisions() -> None:
    class _LiveConnectionRadio:
        connected = True
        control_connected = False
        radio_ready = True
        capabilities: set[str] = set()

    radio = _LiveConnectionRadio()
    srv = WebServer(radio)

    writer = _FakeWriter()
    await srv._serve_state(writer)  # noqa: SLF001
    status, initial = _response_json(writer)

    assert status == 200
    assert initial["connection"]["rigConnected"] is True
    assert initial["connection"]["controlConnected"] is False
    assert initial["connection"]["radioReady"] is True

    radio.control_connected = True

    writer2 = _FakeWriter()
    await srv._serve_state(writer2)  # noqa: SLF001
    status2, control_changed = _response_json(writer2)

    assert status2 == 200
    assert control_changed["stateRevision"] == initial["stateRevision"]
    assert control_changed["freshnessRevision"] == initial["freshnessRevision"]
    assert control_changed["healthRevision"] == initial["healthRevision"]
    assert control_changed["connection"]["controlConnected"] is True

    radio.connected = False

    writer3 = _FakeWriter()
    await srv._serve_state(writer3)  # noqa: SLF001
    status3, connected_changed = _response_json(writer3)

    assert status3 == 200
    assert connected_changed["stateRevision"] == initial["stateRevision"]
    assert connected_changed["freshnessRevision"] == initial["freshnessRevision"]
    assert connected_changed["healthRevision"] == initial["healthRevision"]
    assert connected_changed["connection"]["rigConnected"] is False
    assert connected_changed["connection"]["radioReady"] is True


def test_broadcast_state_update_refreshes_live_connection_payload_without_revisions() -> (
    None
):
    class _LiveConnectionRadio:
        connected = True
        control_connected = False
        radio_ready = True
        capabilities: set[str] = set()

    radio = _LiveConnectionRadio()
    srv = WebServer(radio)
    q = asyncio.Queue()
    srv.register_control_event_queue(q)

    srv._broadcast_state_update()  # noqa: SLF001
    first = q.get_nowait()
    initial = first["data"]["data"]

    radio.control_connected = True

    srv._last_state_broadcast = 0.0  # noqa: SLF001
    srv._broadcast_state_update()  # noqa: SLF001
    event = q.get_nowait()

    assert event["type"] == "state_update"
    assert event["data"]["type"] == "delta"
    assert event["data"]["stateRevision"] == initial["stateRevision"]
    assert event["data"]["freshnessRevision"] == initial["freshnessRevision"]
    assert event["data"]["changed"]["connection"]["controlConnected"] is True


def test_legacy_state_store_sync_can_clear_default_boolean_values() -> None:
    srv = WebServer(None)
    transmitting = RadioState()
    transmitting.ptt = True
    srv.sync_state_store_from_radio_state(transmitting)
    assert srv.build_public_state()["ptt"] is True

    idle = RadioState()
    idle.ptt = False
    srv.sync_state_store_from_radio_state(idle)

    assert srv.build_public_state()["ptt"] is False


def test_legacy_state_store_sync_preserves_receiver_fields() -> None:
    srv = WebServer(None)
    legacy = RadioState()
    legacy.main.data_mode = 2
    legacy.main.filter_width = 1_800
    legacy.main.nr_level = 42

    srv.sync_state_store_from_radio_state(legacy)

    public_state = srv.build_public_state()
    assert public_state["main"]["dataMode"] == 2
    assert public_state["main"]["filterWidth"] == 1_800
    assert public_state["main"]["nrLevel"] == 42


def test_legacy_state_store_sync_can_clear_default_receiver_values() -> None:
    srv = WebServer(None)
    legacy = RadioState()
    legacy.main.data_mode = 2
    srv.sync_state_store_from_radio_state(legacy)
    assert srv.build_public_state()["main"]["dataMode"] == 2

    cleared = RadioState()
    cleared.main.data_mode = 0
    srv.sync_state_store_from_radio_state(cleared)

    assert srv.build_public_state()["main"]["dataMode"] == 0


def test_legacy_state_store_sync_preserves_global_rit_on() -> None:
    srv = WebServer(None)
    legacy = RadioState()
    legacy.rit_on = True

    srv.sync_state_store_from_radio_state(legacy)

    assert srv.build_public_state()["ritOn"] is True


def test_public_state_syncs_legacy_active_after_state_store_observations() -> None:
    srv = WebServer(None)
    srv.command_state_store.apply(
        _store_observation(
            FieldPath.active("0", "freq_mode", "freq_hz"),
            14_074_000,
            at=1.0,
        )
    )
    srv._radio_state.active = "SUB"  # noqa: SLF001

    public_state = srv.build_public_state()

    assert public_state["main"]["freqHz"] == 14_074_000
    assert public_state["active"] == "SUB"


def test_public_state_syncs_legacy_global_toggle_after_state_store_observations() -> None:
    srv = WebServer(None)
    srv.command_state_store.apply(
        _store_observation(
            FieldPath.active("0", "freq_mode", "freq_hz"),
            14_074_000,
            at=1.0,
        )
    )
    srv._radio_state.dual_watch = True  # noqa: SLF001

    public_state = srv.build_public_state()

    assert public_state["dualWatch"] is True


def test_public_state_sync_can_clear_legacy_global_toggle_default() -> None:
    srv = WebServer(None)
    srv.command_state_store.apply(
        _store_observation(
            FieldPath.active("0", "freq_mode", "freq_hz"),
            14_074_000,
            at=1.0,
        )
    )
    srv._radio_state.split = True  # noqa: SLF001
    assert srv.build_public_state()["split"] is True

    srv._radio_state.split = False  # noqa: SLF001

    assert srv.build_public_state()["split"] is False


@pytest.mark.asyncio
async def test_http_and_ws_full_state_share_post_sync_legacy_snapshot() -> None:
    srv = WebServer(None)
    srv.command_state_store.apply(
        _store_observation(
            FieldPath.active("0", "freq_mode", "freq_hz"),
            14_074_000,
            at=1.0,
        )
    )
    srv._radio_state.active = "SUB"  # noqa: SLF001
    srv._radio_state.dual_watch = True  # noqa: SLF001

    envelope = srv.build_state_update_envelope(force_full=True)
    assert envelope["type"] == "full"
    ws_state = envelope["data"]

    writer = _FakeWriter()
    await srv._serve_state(writer)  # noqa: SLF001
    status, http_state = _response_json(writer)

    assert status == 200
    assert http_state == ws_state
    assert http_state["active"] == "SUB"
    assert http_state["dualWatch"] is True
    assert envelope["revision"] == ws_state["stateRevision"]
    assert envelope["stateRevision"] == ws_state["stateRevision"]
    assert envelope["freshnessRevision"] == ws_state["freshnessRevision"]


@pytest.mark.asyncio
async def test_on_radio_reconnect_enables_scope_without_waiting_for_broadcast() -> None:
    """Reconnect must queue EnableScope even while ``radio_ready`` is False.

    Deadlock background: ``radio_ready`` waits for CI-V broadcast to resume,
    but in the "deaf" firmware state observed on IC-7610 the broadcast may
    not resume until a scope-enable CI-V command is sent.  Gating scope
    re-enable behind ``radio_ready`` turned the two into a mutual wait
    that timed out with "scope: radio not ready after 30s" every minute.

    The reconnect path now trusts that ``soft_reconnect`` already brought
    the session up (UDP + auth + discovery), and queues EnableScope
    immediately.  If the radio is genuinely dead the command will fail
    on its own — strictly better than a silent 30-second wait every cycle.
    """
    radio = _scope_radio(ready=False)
    radio._fetch_initial_state = AsyncMock()
    srv = WebServer(radio)
    srv._scope_handlers.add(MagicMock())

    srv._on_radio_reconnect()  # noqa: SLF001
    await asyncio.sleep(0.05)  # let the refetch task complete

    assert any(isinstance(c, EnableScope) for c in srv.command_queue.drain())
    assert radio.radio_ready is False  # gate was bypassed, not flipped


@pytest.mark.asyncio
async def test_ensure_scope_enabled_defers_enable_when_radio_not_ready() -> None:
    radio = _scope_radio(ready=False)
    srv = WebServer(radio)
    srv._scope_reenable_poll_interval = 0.01  # noqa: SLF001
    srv._scope_reenable_timeout = 0.2  # noqa: SLF001

    await srv.ensure_scope_enabled(MagicMock())
    assert not any(isinstance(c, EnableScope) for c in srv.command_queue.drain())

    radio.radio_ready = True
    await asyncio.sleep(0.03)
    assert any(isinstance(c, EnableScope) for c in srv.command_queue.drain())


@pytest.mark.asyncio
async def test_ensure_scope_enabled_skips_when_scope_capability_absent() -> None:
    radio = MagicMock()
    radio.connected = True
    radio.radio_ready = True
    radio.capabilities = set()
    srv = WebServer(radio)

    await srv.ensure_scope_enabled(MagicMock())

    assert not any(isinstance(c, EnableScope) for c in srv.command_queue.drain())
    assert srv._scope_enabled is False


@pytest.mark.asyncio
async def test_scope_health_monitor_disconnected_and_reenable() -> None:
    radio = _scope_radio(connected=False)
    srv = WebServer(radio)
    srv._scope_handlers.add(MagicMock())
    srv._scope_health_interval = 0.01
    srv._scope_last_nonzero = 0.0

    task = asyncio.create_task(srv._scope_health_monitor())  # noqa: SLF001
    await asyncio.sleep(0.03)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert srv._scope_last_nonzero > 0

    radio.connected = True
    srv._scope_last_nonzero = time.monotonic() - 1.0
    task = asyncio.create_task(srv._scope_health_monitor())  # noqa: SLF001
    await asyncio.sleep(0.03)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    cmds = srv.command_queue.drain()
    assert any(isinstance(c, EnableScope) for c in cmds)


@pytest.mark.asyncio
async def test_send_response_and_run_web_server() -> None:
    writer = _FakeWriter()
    await _send_response(writer, 200, "OK", b"abc", {"Content-Type": "text/plain"})
    assert b"HTTP/1.1 200 OK" in writer.buffer
    assert b"Content-Length: 3" in writer.buffer

    fake_server = MagicMock()
    fake_server.serve_forever = AsyncMock()
    with patch("rigplane.web.server.WebServer", return_value=fake_server):
        await run_web_server(None, host="127.0.0.1", port=8000)
    fake_server.serve_forever.assert_awaited_once()


@pytest.mark.asyncio
async def test_broadcast_notification_puts_to_all_queues() -> None:
    """broadcast_notification pushes notification dict to all registered queues."""
    srv = WebServer()
    q1: asyncio.Queue[dict] = asyncio.Queue()
    q2: asyncio.Queue[dict] = asyncio.Queue()
    srv.register_control_event_queue(q1)
    srv.register_control_event_queue(q2)

    srv.broadcast_notification("success", "Radio connected", "connection")

    assert not q1.empty()
    assert not q2.empty()
    n1 = q1.get_nowait()
    n2 = q2.get_nowait()
    assert n1["type"] == "notification"
    assert n1["level"] == "success"
    assert n1["message"] == "Radio connected"
    assert n1["category"] == "connection"
    assert n1 == n2


@pytest.mark.asyncio
async def test_broadcast_notification_default_category() -> None:
    """broadcast_notification uses 'system' as default category."""
    srv = WebServer()
    q: asyncio.Queue[dict] = asyncio.Queue()
    srv.register_control_event_queue(q)

    srv.broadcast_notification("info", "Hello")

    n = q.get_nowait()
    assert n["category"] == "system"


@pytest.mark.asyncio
async def test_broadcast_notification_full_queue_no_crash() -> None:
    """broadcast_notification silently skips full queues (dead clients)."""
    srv = WebServer()
    q: asyncio.Queue[dict] = asyncio.Queue(maxsize=1)
    q.put_nowait({"type": "other"})  # fill the queue
    srv.register_control_event_queue(q)

    # Should not raise even though queue is full
    srv.broadcast_notification("warning", "Test")
    assert q.qsize() == 1  # queue still has the old item, notification was dropped


@pytest.mark.asyncio
async def test_broadcast_notification_includes_reason_code_when_set() -> None:
    """broadcast_notification emits the optional `code` field for i18n resolution.

    Wire-schema decision (RP-ML-005): the payload is additive — `code` and
    `params` are present only when the caller supplies them, so legacy
    consumers that only read `message` keep working.
    """
    srv = WebServer()
    q: asyncio.Queue[dict] = asyncio.Queue()
    srv.register_control_event_queue(q)

    srv.broadcast_notification(
        "success",
        "Radio connected",
        "connection",
        code="radioConnected",
    )

    n = q.get_nowait()
    assert n["type"] == "notification"
    assert n["level"] == "success"
    assert n["message"] == "Radio connected"
    assert n["category"] == "connection"
    assert n["code"] == "radioConnected"
    assert "params" not in n


@pytest.mark.asyncio
async def test_broadcast_notification_threads_params_through() -> None:
    """broadcast_notification copies `params` into the payload when provided."""
    srv = WebServer()
    q: asyncio.Queue[dict] = asyncio.Queue()
    srv.register_control_event_queue(q)

    srv.broadcast_notification(
        "info",
        "An update is available: 2.1.0.",
        "system",
        code="updateAvailable",
        params={"version": "2.1.0"},
    )

    n = q.get_nowait()
    assert n["code"] == "updateAvailable"
    assert n["params"] == {"version": "2.1.0"}


@pytest.mark.asyncio
async def test_broadcast_notification_omits_code_for_legacy_path() -> None:
    """Calls without an explicit `code` keep the legacy English-only shape."""
    srv = WebServer()
    q: asyncio.Queue[dict] = asyncio.Queue()
    srv.register_control_event_queue(q)

    srv.broadcast_notification("info", "Legacy notification")

    n = q.get_nowait()
    assert "code" not in n
    assert "params" not in n
    assert n["message"] == "Legacy notification"


# ---------------------------------------------------------------------------
# Sprint 0B compatibility: canonical state revision + updatedAt (#158)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_state_response_includes_revision_and_updated_at() -> None:
    """_serve_state adds revision (int) and updatedAt (ISO 8601) to JSON."""
    import datetime as _dt
    import json as _json

    srv = WebServer(None)
    writer = _FakeWriter()
    await srv._serve_state(writer)  # noqa: SLF001

    text = writer.buffer.decode("ascii", errors="replace")
    assert "200 OK" in text
    # Extract JSON body (after blank line)
    body_start = text.index("\r\n\r\n") + 4
    data = _json.loads(text[body_start:])
    assert "revision" in data
    assert isinstance(data["revision"], int)
    assert "updatedAt" in data
    # Must parse as valid ISO 8601 UTC datetime
    ts = _dt.datetime.fromisoformat(data["updatedAt"])
    assert ts.tzinfo is not None


@pytest.mark.asyncio
async def test_state_response_revision_zero_without_poller() -> None:
    """The legacy revision alias comes from the StateStore, not RadioPoller."""
    import json as _json

    srv = WebServer(None)
    assert srv._radio_poller is None  # noqa: SLF001
    writer = _FakeWriter()
    await srv._serve_state(writer)  # noqa: SLF001
    text = writer.buffer.decode("ascii", errors="replace")
    body_start = text.index("\r\n\r\n") + 4
    data = _json.loads(text[body_start:])
    assert data["revision"] == 0
    assert data["stateRevision"] == 0


@pytest.mark.asyncio
async def test_on_radio_state_change_broadcasts_canonical_state_payload() -> None:
    """State callbacks broadcast StateStore-backed payloads without poller revision."""
    radio = MagicMock()
    radio.capabilities = set()
    radio.profile = MagicMock()
    radio.profile.receiver_count = 1
    radio.profile.model = "IC-7300"
    radio.profile.supports_receiver = MagicMock(return_value=True)
    radio.profile.supports_cmd29 = MagicMock(return_value=False)
    radio.profile.vfo_sub_code = None
    radio.profile.vfo_main_code = None
    radio.profile.vfo_swap_code = None

    srv = WebServer(radio)
    queue = BoundedQueue[dict[str, object]](maxsize=4)
    srv.register_control_event_queue(queue)
    srv.command_state_store.apply(
        _store_observation(
            FieldPath.active("0", "freq_mode", "freq_hz"),
            14_074_000,
            at=time.monotonic(),
        )
    )

    srv._on_radio_state_change("freq_changed", {"freq": 14074000})  # noqa: SLF001

    event = queue.get_nowait()
    state_update = queue.get_nowait()
    assert event["type"] == "event"
    assert state_update["type"] == "state_update"
    assert state_update["data"]["stateRevision"] == 1
    assert state_update["data"]["revision"] == 1


@pytest.mark.asyncio
async def test_info_endpoint_returns_structured_capabilities() -> None:
    """/api/v1/info returns model, capabilities, and connection objects."""
    import json as _json

    from rigplane.radio_protocol import AudioCapable, DualReceiverCapable, ScopeCapable

    class _FakeRadio(ScopeCapable, AudioCapable, DualReceiverCapable):
        def __init__(self) -> None:
            self.model = "IC-7610"
            self.connected = True
            self.control_connected = False
            self.radio_ready = True
            self.capabilities = {"scope", "audio", "dual_rx"}

    radio = _FakeRadio()

    srv = WebServer(radio)
    writer = _FakeWriter()
    await srv._serve_info(writer)  # noqa: SLF001

    text = writer.buffer.decode("ascii", errors="replace")
    assert "200 OK" in text
    body_start = text.index("\r\n\r\n") + 4
    data = _json.loads(text[body_start:])

    # Legacy fields still present (backward compat)
    assert data["server"] == "rigplane"
    assert data["proto"] == 1
    assert data["radio"] == "IC-7610"

    # New structured fields
    assert data["model"] == "IC-7610"
    caps = data["capabilities"]
    assert caps["hasSpectrum"] is True
    assert caps["hasAudio"] is True
    assert caps["hasDualReceiver"] is True
    assert caps["maxReceivers"] == 2
    assert isinstance(caps["tags"], list)
    assert isinstance(caps["modes"], list)
    assert isinstance(caps["filters"], list)

    conn = data["connection"]
    assert conn["rigConnected"] is True
    assert conn["controlConnected"] is False
    assert isinstance(conn["wsClients"], int)


@pytest.mark.asyncio
async def test_info_endpoint_no_radio() -> None:
    """/api/v1/info works without a radio (all capabilities false)."""
    import json as _json

    srv = WebServer(None)
    writer = _FakeWriter()
    await srv._serve_info(writer)  # noqa: SLF001

    text = writer.buffer.decode("ascii", errors="replace")
    body_start = text.index("\r\n\r\n") + 4
    data = _json.loads(text[body_start:])
    caps = data["capabilities"]
    assert caps["hasSpectrum"] is False
    assert caps["hasAudio"] is False
    assert caps["hasDualReceiver"] is False
    assert caps["maxReceivers"] == 1
    assert caps["tags"] == []
    conn = data["connection"]
    assert conn["rigConnected"] is False
    assert conn["radioReady"] is False


# ---------------------------------------------------------------------------
# Sprint 0 fixes: _camel_case_state transform (C2)
# ---------------------------------------------------------------------------


from rigplane.web.runtime_helpers import _camel_case_state  # noqa: E402


class TestCamelCaseState:
    """Unit tests for the _camel_case_state serialisation helper."""

    def _minimal_dict(self) -> dict:
        """Return the smallest valid state dict for testing."""
        return {
            "active": "MAIN",
            "dual_watch": False,
            "tuner_status": 0,
            "connected": False,
            "radio_ready": False,
            "control_connected": False,
            "revision": 1,
            "updatedAt": "2026-01-01T00:00:00+00:00",
            "main": {
                "freq": 14074000,
                "data_mode": False,
                "s_meter": 10,
                "af_level": 128,
                "rf_gain": 200,
            },
            "sub": {
                "freq": 7100000,
                "data_mode": True,
                "s_meter": 0,
                "af_level": 64,
                "rf_gain": 100,
            },
            "scope_controls": {"ref_db": -13.5, "vbw_narrow": False},
        }

    def test_snake_case_keys_become_camel_case(self) -> None:
        result = _camel_case_state(self._minimal_dict())
        assert "dualWatch" in result
        assert "dual_watch" not in result
        assert "tunerStatus" in result
        assert "tuner_status" not in result

    def test_freq_renamed_to_freq_hz_in_receiver_dicts(self) -> None:
        result = _camel_case_state(self._minimal_dict())
        assert "freqHz" in result["main"]
        assert "freq" not in result["main"]
        assert result["main"]["freqHz"] == 14074000
        assert "freqHz" in result["sub"]
        assert result["sub"]["freqHz"] == 7100000

    def test_receiver_snake_keys_become_camel_case(self) -> None:
        result = _camel_case_state(self._minimal_dict())
        assert "dataMode" in result["main"]
        assert "data_mode" not in result["main"]
        assert "sMeter" in result["main"]
        assert "afLevel" in result["main"]
        assert "rfGain" in result["main"]

    def test_connection_fields_wrapped_into_object(self) -> None:
        d = self._minimal_dict()
        d["connected"] = True
        d["radio_ready"] = True
        d["control_connected"] = False
        result = _camel_case_state(d)
        assert "connected" not in result
        assert "radio_ready" not in result
        assert "control_connected" not in result
        conn = result["connection"]
        assert conn["rigConnected"] is True
        assert conn["radioReady"] is True
        assert conn["controlConnected"] is False

    def test_revision_and_updated_at_preserved(self) -> None:
        result = _camel_case_state(self._minimal_dict())
        assert result["revision"] == 1
        assert result["updatedAt"] == "2026-01-01T00:00:00+00:00"

    def test_scope_controls_keys_converted(self) -> None:
        result = _camel_case_state(self._minimal_dict())
        sc = result["scopeControls"]
        assert "refDb" in sc
        assert "vbwNarrow" in sc
        assert "ref_db" not in sc

    @pytest.mark.asyncio
    async def test_state_response_is_camel_case(self) -> None:
        """Integration: _serve_state HTTP response body is camelCase."""
        import json as _json

        srv = WebServer(None)
        writer = _FakeWriter()
        await srv._serve_state(writer)  # noqa: SLF001
        text = writer.buffer.decode("ascii", errors="replace")
        body_start = text.index("\r\n\r\n") + 4
        data = _json.loads(text[body_start:])
        assert "dualWatch" in data
        assert "dual_watch" not in data
        assert "tunerStatus" in data
        assert "connection" in data
        assert "rigConnected" in data["connection"]


# ---------------------------------------------------------------------------
# ETag / 304 behaviour for /api/v1/state (#248)
# ---------------------------------------------------------------------------


class TestStateEtag:
    """Backend ETag/304 support for /api/v1/state."""

    @pytest.mark.asyncio
    async def test_state_response_includes_etag(self) -> None:
        """GET /api/v1/state returns an ETag header."""
        srv = WebServer(None)
        writer = _FakeWriter()
        await srv._serve_state(writer)
        text = writer.buffer.decode("ascii", errors="replace")
        header_block = text[: text.index("\r\n\r\n")]
        assert "ETag:" in header_block, f"No ETag in response headers:\n{header_block}"

    @pytest.mark.asyncio
    async def test_state_304_when_etag_matches(self) -> None:
        """GET /api/v1/state with matching If-None-Match returns 304 and empty body."""

        # First request — get the ETag
        srv = WebServer(None)
        writer = _FakeWriter()
        await srv._serve_state(writer)
        text = writer.buffer.decode("ascii", errors="replace")
        header_block = text[: text.index("\r\n\r\n")]
        etag_line = next(
            line for line in header_block.splitlines() if line.startswith("ETag:")
        )
        etag = etag_line.split(":", 1)[1].strip()

        # Second request — send If-None-Match with the same ETag
        writer2 = _FakeWriter()
        fake_headers = {"if-none-match": etag}
        await srv._serve_state(writer2, fake_headers)
        text2 = writer2.buffer.decode("ascii", errors="replace")
        status_line = text2.split("\r\n", 1)[0]
        assert "304" in status_line, f"Expected 304, got: {status_line}"
        body_start = text2.index("\r\n\r\n") + 4
        assert text2[body_start:] == "", "304 response must have empty body"

    @pytest.mark.asyncio
    async def test_state_etag_changes_when_health_changes_without_revision(
        self,
    ) -> None:
        """Health-only transitions must not be hidden behind a revision-only ETag."""

        class _HealthRadio:
            connected = True
            control_connected = True
            radio_ready = True
            capabilities: set[str] = set()

        radio = _HealthRadio()
        srv = WebServer(radio)
        writer = _FakeWriter()
        await srv._serve_state(writer)
        text = writer.buffer.decode("ascii", errors="replace")
        header_block = text[: text.index("\r\n\r\n")]
        etag = next(
            line.split(":", 1)[1].strip()
            for line in header_block.splitlines()
            if line.startswith("ETag:")
        )

        radio.radio_ready = False
        radio._last_civ_data_received = 0.0
        radio._civ_ready_idle_timeout = 1.0

        writer2 = _FakeWriter()
        await srv._serve_state(writer2, {"if-none-match": etag})
        text2 = writer2.buffer.decode("ascii", errors="replace")
        assert "200 OK" in text2.split("\r\n", 1)[0]
        body = json.loads(text2[text2.index("\r\n\r\n") + 4 :])
        assert body["revision"] == 0
        assert body["healthRevision"] == 2
        assert body["radioHealth"]["likelyCause"] == "radio_not_responding"

    @pytest.mark.asyncio
    async def test_state_200_when_etag_differs(self) -> None:
        """GET /api/v1/state with stale If-None-Match returns 200 with full body."""
        srv = WebServer(None)
        writer = _FakeWriter()
        fake_headers = {"if-none-match": '"stale-etag-999"'}
        await srv._serve_state(writer, fake_headers)
        text = writer.buffer.decode("ascii", errors="replace")
        status_line = text.split("\r\n", 1)[0]
        assert "200" in status_line, f"Expected 200, got: {status_line}"
        body_start = text.index("\r\n\r\n") + 4
        assert len(text[body_start:]) > 0, "200 response must have a body"
