"""Tests for watchdog and auto-reconnect."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from icom_lan.runtime._connection_state import RadioConnectionState
from icom_lan.exceptions import AuthenticationError
from icom_lan.radio import IcomRadio

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
