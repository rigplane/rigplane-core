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


# ---------------------------------------------------------------------------
# Sprint 0B: revision counter + updatedAt (#158)
# ---------------------------------------------------------------------------


def test_radio_poller_revision_starts_at_zero() -> None:
    """RadioPoller.revision is 0 before any state changes."""
    from rigplane.web.radio_poller import CommandQueue, RadioPoller
    from rigplane.rigctld.state_cache import StateCache

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

    poller = RadioPoller(radio, StateCache(), CommandQueue())
    assert poller.revision == 0


def test_radio_poller_revision_increments() -> None:
    """bump_revision() monotonically increments revision."""
    from rigplane.web.radio_poller import CommandQueue, RadioPoller
    from rigplane.rigctld.state_cache import StateCache

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

    poller = RadioPoller(radio, StateCache(), CommandQueue())
    assert poller.revision == 0
    poller.bump_revision()
    assert poller.revision == 1
    poller.bump_revision()
    assert poller.revision == 2


def test_radio_poller_revision_never_decreases() -> None:
    """After many bump_revision calls, revision is always >= previous value."""
    from rigplane.web.radio_poller import CommandQueue, RadioPoller
    from rigplane.rigctld.state_cache import StateCache

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

    poller = RadioPoller(radio, StateCache(), CommandQueue())
    prev = poller.revision
    for _ in range(10):
        poller.bump_revision()
        assert poller.revision >= prev
        prev = poller.revision


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
    """revision is 0 when no RadioPoller is attached."""
    import json as _json

    srv = WebServer(None)
    assert srv._radio_poller is None  # noqa: SLF001
    writer = _FakeWriter()
    await srv._serve_state(writer)  # noqa: SLF001
    text = writer.buffer.decode("ascii", errors="replace")
    body_start = text.index("\r\n\r\n") + 4
    data = _json.loads(text[body_start:])
    assert data["revision"] == 0


@pytest.mark.asyncio
async def test_on_radio_state_change_bumps_revision() -> None:
    """_on_radio_state_change increments poller.revision."""
    from rigplane.web.radio_poller import CommandQueue, RadioPoller
    from rigplane.rigctld.state_cache import StateCache

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

    srv = WebServer(None)
    poller = RadioPoller(radio, StateCache(), CommandQueue())
    srv._radio_poller = poller  # noqa: SLF001
    assert poller.revision == 0

    srv._on_radio_state_change("freq_changed", {"freq": 14074000})  # noqa: SLF001
    assert poller.revision == 1

    srv._on_radio_state_change("mode_changed", {"mode": "USB"})  # noqa: SLF001
    assert poller.revision == 2


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
