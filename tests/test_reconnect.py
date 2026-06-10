"""Tests for watchdog and auto-reconnect."""

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from rigplane.core._bounded_queue import BoundedQueue
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.exceptions import AuthenticationError
from rigplane.radio import IcomRadio
from rigplane.web.server import WebConfig, WebServer

from test_audio_session_health import _FakeWriter, _response_json
from test_radio import MockTransport


@pytest.fixture
def radio() -> IcomRadio:
    r = IcomRadio(
        "192.168.1.100",
        auto_reconnect=True,
        reconnect_delay=0.1,
        reconnect_max_delay=0.5,
        watchdog_timeout=0.3,
    )
    mt = MockTransport()
    r._ctrl_transport = mt
    r._connected = True
    r._token = 0x1234
    return r


class TestWatchdog:
    @pytest.mark.asyncio
    async def test_start_stop(self, radio: IcomRadio) -> None:
        radio._control_phase._start_watchdog()
        assert radio._watchdog_task is not None
        radio._control_phase._stop_watchdog()
        await asyncio.sleep(0.05)
        assert radio._watchdog_task is None

    @pytest.mark.asyncio
    async def test_stop_when_not_started(self) -> None:
        r = IcomRadio("192.168.1.100")
        r._control_phase._stop_watchdog()  # should not raise

    @pytest.mark.asyncio
    async def test_watchdog_triggers_on_timeout(self, radio: IcomRadio) -> None:
        """Watchdog should set connected=False after timeout with no activity."""
        radio._control_phase._start_watchdog()
        # Wait longer than watchdog_timeout
        await asyncio.sleep(0.6)
        assert not radio._connected
        # Reconnect task should have been started
        assert radio._reconnect_task is not None
        # Clean up
        radio._intentional_disconnect = True
        radio._control_phase._stop_reconnect()
        radio._control_phase._stop_watchdog()

    @pytest.mark.asyncio
    async def test_watchdog_does_not_trigger_with_activity(
        self, radio: IcomRadio
    ) -> None:
        """Watchdog should not trigger if there's queue activity."""
        radio._control_phase._start_watchdog()
        # Simulate activity by putting something in the queue
        for _ in range(3):
            radio._ctrl_transport._packet_queue.put_nowait(b"\x00" * 16)
            await asyncio.sleep(0.15)
        assert radio._connected
        radio._control_phase._stop_watchdog()
        # Drain queue
        while not radio._ctrl_transport._packet_queue.empty():
            radio._ctrl_transport._packet_queue.get_nowait()


class TestReconnect:
    @pytest.mark.asyncio
    async def test_stop_reconnect_when_not_started(self) -> None:
        r = IcomRadio("192.168.1.100")
        r._control_phase._stop_reconnect()  # should not raise

    @pytest.mark.asyncio
    async def test_intentional_disconnect_stops_reconnect(
        self, radio: IcomRadio
    ) -> None:
        """Setting intentional_disconnect should prevent reconnect."""
        radio._intentional_disconnect = True
        radio._reconnect_task = asyncio.create_task(radio._reconnect_loop())
        await asyncio.sleep(0.1)
        # Should exit immediately
        assert radio._reconnect_task.done()

    @pytest.mark.asyncio
    async def test_reconnect_increments_delay(self, radio: IcomRadio) -> None:
        """Reconnect should use exponential backoff."""
        # The reconnect will fail (no real radio), but we verify it tries
        radio._connected = False
        radio._intentional_disconnect = False
        radio._reconnect_task = asyncio.create_task(radio._reconnect_loop())
        await asyncio.sleep(0.5)
        # Should still be trying (not connected, not intentional)
        radio._intentional_disconnect = True
        radio._control_phase._stop_reconnect()
        await asyncio.sleep(0.05)


class TestAutoReconnectConfig:
    def test_default_off(self) -> None:
        r = IcomRadio("192.168.1.100")
        assert r._auto_reconnect is False

    def test_enabled(self) -> None:
        r = IcomRadio("192.168.1.100", auto_reconnect=True)
        assert r._auto_reconnect is True
        assert r._reconnect_delay == 2.0
        assert r._reconnect_max_delay == 60.0
        assert r._watchdog_timeout == 30.0

    def test_custom_delays(self) -> None:
        r = IcomRadio(
            "192.168.1.100",
            auto_reconnect=True,
            reconnect_delay=5.0,
            reconnect_max_delay=120.0,
            watchdog_timeout=15.0,
        )
        assert r._reconnect_delay == 5.0
        assert r._reconnect_max_delay == 120.0
        assert r._watchdog_timeout == 15.0


class TestReconnectPermanentErrors:
    """Reconnect should abort immediately on permanent errors (#472)."""

    @pytest.mark.asyncio
    async def test_auth_error_aborts_reconnect(self, radio: IcomRadio) -> None:
        """AuthenticationError should stop reconnect immediately."""
        radio._connected = False
        radio._intentional_disconnect = False
        with patch.object(radio, "connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = AuthenticationError("wrong password")
            radio._reconnect_task = asyncio.create_task(radio._reconnect_loop())
            await asyncio.sleep(0.15)
            assert radio._reconnect_task.done()
            assert radio._conn_state == RadioConnectionState.DISCONNECTED
            # Should have tried only once
            assert mock_connect.call_count == 1

    @pytest.mark.asyncio
    async def test_value_error_aborts_reconnect(self, radio: IcomRadio) -> None:
        """ValueError should stop reconnect immediately."""
        radio._connected = False
        radio._intentional_disconnect = False
        with patch.object(radio, "connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = ValueError("invalid config")
            radio._reconnect_task = asyncio.create_task(radio._reconnect_loop())
            await asyncio.sleep(0.15)
            assert radio._reconnect_task.done()
            assert radio._conn_state == RadioConnectionState.DISCONNECTED
            assert mock_connect.call_count == 1

    @pytest.mark.asyncio
    async def test_type_error_aborts_reconnect(self, radio: IcomRadio) -> None:
        """TypeError should stop reconnect immediately."""
        radio._connected = False
        radio._intentional_disconnect = False
        with patch.object(radio, "connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = TypeError("bad arg")
            radio._reconnect_task = asyncio.create_task(radio._reconnect_loop())
            await asyncio.sleep(0.15)
            assert radio._reconnect_task.done()
            assert radio._conn_state == RadioConnectionState.DISCONNECTED
            assert mock_connect.call_count == 1

    @pytest.mark.asyncio
    async def test_transient_error_retries(self, radio: IcomRadio) -> None:
        """Transient errors (OSError) should still retry."""
        radio._connected = False
        radio._intentional_disconnect = False
        with patch.object(radio, "connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = OSError("network unreachable")
            radio._reconnect_task = asyncio.create_task(radio._reconnect_loop())
            # Let it retry a few times (delay starts at 0.1)
            await asyncio.sleep(0.35)
            assert mock_connect.call_count >= 2
            assert radio._conn_state == RadioConnectionState.RECONNECTING
            # Clean up
            radio._intentional_disconnect = True
            radio._control_phase._stop_reconnect()
            await asyncio.sleep(0.05)


class TestExternalCatSessionResetOnConnect:
    """Regression for #1702: a leaked external-CAT session must not survive a
    (re)connect, or the cooperating pollers stay paused forever and rigctld/web
    serve a frozen ``radio_state`` (e.g. the FT8 default freq)."""

    @pytest.mark.asyncio
    async def test_connect_resets_leaked_external_cat_session(
        self, radio: IcomRadio
    ) -> None:
        """A begin_external_cat_session() that was never matched by an end()
        (managed runtime crash/restart, dropped Hamlib bridge) leaves the flag
        set. connect() must clear it so pollers resume after reconnect."""
        # Simulate a leaked external-CAT session (no matching end()).
        radio.begin_external_cat_session()
        assert radio.external_cat_session_active is True

        # Drive connect() without hardware: stub the control-phase handshake and
        # the initial-state fetch so only the reset behaviour under test runs.
        with (
            patch.object(radio._control_phase, "connect", new_callable=AsyncMock),
            patch.object(radio, "_fetch_initial_state", new_callable=AsyncMock),
        ):
            await radio.connect()

        assert radio.external_cat_session_active is False
        assert radio._external_cat_session_owner is None

    @pytest.mark.asyncio
    async def test_connect_keeps_session_clear_when_not_leaked(
        self, radio: IcomRadio
    ) -> None:
        """The reset is a no-op on a clean connect (no false-positive churn)."""
        assert radio.external_cat_session_active is False
        with (
            patch.object(radio._control_phase, "connect", new_callable=AsyncMock),
            patch.object(radio, "_fetch_initial_state", new_callable=AsyncMock),
        ):
            await radio.connect()
        assert radio.external_cat_session_active is False


class TestReconnectStatusCallback:
    """MOR-594: reconnect status surfaced via callback at each meaningful edge."""

    @pytest.mark.asyncio
    async def test_reports_each_attempt_then_connected(self, radio: IcomRadio) -> None:
        """Each attempt emits ``reconnecting`` with attempt + backoff; success
        emits ``connected``."""
        statuses: list[dict[str, Any]] = []
        radio.set_reconnect_status_callback(statuses.append)
        radio._connected = False
        radio._intentional_disconnect = False
        with patch.object(radio, "connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = [OSError("down"), OSError("down"), None]
            radio._reconnect_task = asyncio.create_task(radio._reconnect_loop())
            await asyncio.wait_for(radio._reconnect_task, timeout=5.0)
        reconnecting = [s for s in statuses if s["state"] == "reconnecting"]
        assert [s["attempt"] for s in reconnecting] == [1, 2, 3]
        # Backoff: reconnect_delay=0.1 doubling toward reconnect_max_delay=0.5.
        assert [s["next_retry_seconds"] for s in reconnecting] == [0.1, 0.2, 0.4]
        assert statuses[-1] == {
            "state": "connected",
            "attempt": 3,
            "next_retry_seconds": None,
        }

    @pytest.mark.asyncio
    async def test_raising_callback_does_not_break_reconnect(
        self, radio: IcomRadio
    ) -> None:
        """A raising status callback must never break the reconnect loop."""

        def _boom(_status: dict[str, Any]) -> None:
            raise RuntimeError("boom")

        radio.set_reconnect_status_callback(_boom)
        radio._connected = False
        radio._intentional_disconnect = False
        with patch.object(radio, "connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = [OSError("down"), None]
            radio._reconnect_task = asyncio.create_task(radio._reconnect_loop())
            await asyncio.wait_for(radio._reconnect_task, timeout=5.0)
        assert mock_connect.call_count == 2

    @pytest.mark.asyncio
    async def test_permanent_error_reports_disconnected(self, radio: IcomRadio) -> None:
        """A permanent-error abort must not leave the status stuck at
        ``reconnecting`` (no silent problems)."""
        statuses: list[dict[str, Any]] = []
        radio.set_reconnect_status_callback(statuses.append)
        radio._connected = False
        radio._intentional_disconnect = False
        with patch.object(radio, "connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = AuthenticationError("wrong password")
            radio._reconnect_task = asyncio.create_task(radio._reconnect_loop())
            await asyncio.wait_for(radio._reconnect_task, timeout=5.0)
        assert statuses[-1] == {
            "state": "disconnected",
            "attempt": 1,
            "next_retry_seconds": None,
        }

    @pytest.mark.asyncio
    async def test_watchdog_trigger_reports_first_attempt(
        self, radio: IcomRadio
    ) -> None:
        """The watchdog trigger point surfaces ``reconnecting`` immediately."""
        statuses: list[dict[str, Any]] = []
        radio.set_reconnect_status_callback(statuses.append)
        radio._control_phase._start_watchdog()
        await asyncio.sleep(0.6)  # > watchdog_timeout (0.3 s), no activity
        assert statuses, "watchdog trigger must surface a reconnecting status"
        assert statuses[0]["state"] == "reconnecting"
        assert statuses[0]["attempt"] == 1
        assert statuses[0]["next_retry_seconds"] == 0.1
        # Clean up
        radio._intentional_disconnect = True
        radio._control_phase._stop_reconnect()
        radio._control_phase._stop_watchdog()
        await asyncio.sleep(0.05)


def _web_radio_stub() -> SimpleNamespace:
    """Minimal radio surface for WebServer runtime-payload tests (MOR-581 shape)."""
    return SimpleNamespace(
        model="IC-7610",
        backend_id="rigplane",
        connected=True,
        control_connected=True,
        radio_ready=True,
        capabilities=set(),
    )


class TestWebReconnectStatusSurface:
    """MOR-594: runtime payload ``connection`` block + ``connection_status``
    WS event — local-only surfacing, no telemetry."""

    @pytest.mark.asyncio
    async def test_runtime_payload_defaults_to_radio_connected(self) -> None:
        srv = WebServer(_web_radio_stub(), WebConfig(host="127.0.0.1", port=0))
        writer = _FakeWriter()
        await srv._handle_http(writer, "GET", "/api/v1/runtime")
        status, data = _response_json(writer)
        assert status == 200
        assert data["connection"] == {
            "state": "connected",
            "attempt": 0,
            "next_retry_seconds": None,
        }

    @pytest.mark.asyncio
    async def test_runtime_payload_reflects_latest_status(self) -> None:
        srv = WebServer(_web_radio_stub(), WebConfig(host="127.0.0.1", port=0))
        srv._on_reconnect_status(
            {"state": "reconnecting", "attempt": 3, "next_retry_seconds": 4.0}
        )
        writer = _FakeWriter()
        await srv._handle_http(writer, "GET", "/api/v1/runtime")
        status, data = _response_json(writer)
        assert status == 200
        assert data["connection"] == {
            "state": "reconnecting",
            "attempt": 3,
            "next_retry_seconds": 4.0,
        }

    @pytest.mark.asyncio
    async def test_status_events_broadcast_on_control_ws_queues(
        self, radio: IcomRadio
    ) -> None:
        """Reconnect statuses are forwarded as ``connection_status`` events on
        the EXISTING control-WS event surface."""
        srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
        srv._attach_reconnect_status_listener()  # start() wiring
        q: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=16)
        srv.register_control_event_queue(q)
        radio._connected = False
        radio._intentional_disconnect = False
        with patch.object(radio, "connect", new_callable=AsyncMock) as mock_connect:
            mock_connect.side_effect = [OSError("down"), None]
            radio._reconnect_task = asyncio.create_task(radio._reconnect_loop())
            await asyncio.wait_for(radio._reconnect_task, timeout=5.0)
        events: list[dict[str, Any]] = []
        while not q.empty():
            events.append(q.get_nowait())
        conn = [e for e in events if e.get("name") == "connection_status"]
        assert [e["data"]["state"] for e in conn] == [
            "reconnecting",
            "reconnecting",
            "connected",
        ]
        assert [e["data"]["attempt"] for e in conn] == [1, 2, 2]
        assert conn[0]["data"]["next_retry_seconds"] == 0.1
        assert conn[-1]["data"]["next_retry_seconds"] is None
        srv.unregister_control_event_queue(q)
        # stop() detaches — the radio must not keep a stale server reference.
        srv._detach_reconnect_status_listener()
        assert radio._on_reconnect_status is None
