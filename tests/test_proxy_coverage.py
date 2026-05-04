"""Extra coverage tests for proxy.py.

Covers:
- datagram_received() when transport is None (line 42)
- datagram_received() client address change log (line 53)
- error_received() (line 68)
- connection_lost() (line 71)
- _session_watchdog() timeout + cancel (lines 79-87)
"""

from __future__ import annotations

import asyncio
import time

import pytest

from icom_lan.proxy import _RelayProtocol, _session_watchdog


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[bytes, tuple[str, int]]] = []

    def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
        self.sent.append((data, addr))

    def get_extra_info(self, key: str) -> tuple[str, int]:
        return ("0.0.0.0", 50001)


def _make_relay(radio_host: str = "192.168.1.100", port: int = 50001) -> _RelayProtocol:
    return _RelayProtocol(radio_host, port, "control")


# ---------------------------------------------------------------------------
# datagram_received — transport is None (line 42)
# ---------------------------------------------------------------------------


def test_datagram_received_before_connection_made_is_noop() -> None:
    """Should silently drop packets when transport has not been set yet."""
    relay = _make_relay()
    # transport is None → must not raise
    relay.datagram_received(b"hello", ("10.0.0.5", 12345))
    assert relay.client_addr is None


# ---------------------------------------------------------------------------
# datagram_received — client address change (line 53)
# ---------------------------------------------------------------------------


def test_datagram_received_logs_client_address_change(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A second client coming from a new address should log 'client changed'."""
    import logging

    relay = _make_relay()
    t = _FakeTransport()
    relay.connection_made(t)  # type: ignore[arg-type]

    with caplog.at_level(logging.INFO, logger="icom_lan.runtime.proxy"):
        # First client registers
        relay.datagram_received(b"first", ("10.0.0.1", 11111))
        # Second client from different address
        relay.datagram_received(b"second", ("10.0.0.2", 22222))

    assert relay.client_addr == ("10.0.0.2", 22222)
    assert any("client changed" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# error_received() — line 68
# ---------------------------------------------------------------------------


def test_error_received_logs_warning(caplog: pytest.LogCaptureFixture) -> None:
    """error_received() should log a warning with the exception."""
    import logging

    relay = _make_relay()
    exc = OSError("network unreachable")

    with caplog.at_level(logging.WARNING, logger="icom_lan.runtime.proxy"):
        relay.error_received(exc)

    assert any("UDP error" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# connection_lost() — line 71
# ---------------------------------------------------------------------------


def test_connection_lost_logs_info(caplog: pytest.LogCaptureFixture) -> None:
    """connection_lost() should log an info message."""
    import logging

    relay = _make_relay()

    with caplog.at_level(logging.INFO, logger="icom_lan.runtime.proxy"):
        relay.connection_lost(None)

    assert any("connection lost" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _session_watchdog() — lines 79-87
# ---------------------------------------------------------------------------


async def test_session_watchdog_clears_stale_client() -> None:
    """Watchdog resets client_addr when session timed out."""
    relay = _make_relay()
    t = _FakeTransport()
    relay.connection_made(t)  # type: ignore[arg-type]
    relay.datagram_received(b"hello", ("10.0.0.1", 11111))
    assert relay.client_addr is not None

    # Simulate long inactivity
    relay.last_activity = time.monotonic() - 200.0

    # Run watchdog briefly: SESSION_TIMEOUT=60, sleep=30; we'll run one cycle
    # by patching sleep so it doesn't actually wait but still yields to event loop
    _real_sleep = asyncio.sleep

    async def instant_sleep(_: float) -> None:
        await _real_sleep(0)  # yield to event loop without actually sleeping

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(asyncio, "sleep", instant_sleep)
        task = asyncio.create_task(_session_watchdog([relay]))
        # Let one iteration of the while loop run
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert relay.client_addr is None


async def test_session_watchdog_does_not_clear_active_client() -> None:
    """Watchdog leaves client_addr intact when the session is still active."""
    relay = _make_relay()
    t = _FakeTransport()
    relay.connection_made(t)  # type: ignore[arg-type]
    relay.datagram_received(b"hello", ("10.0.0.1", 11111))
    # Recent activity
    relay.last_activity = time.monotonic()

    async def instant_sleep(_: float) -> None:
        pass

    with pytest.MonkeyPatch().context() as mp:
        mp.setattr(asyncio, "sleep", instant_sleep)
        task = asyncio.create_task(_session_watchdog([relay]))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    assert relay.client_addr == ("10.0.0.1", 11111)


async def test_session_watchdog_cancellable() -> None:
    """_session_watchdog() exits cleanly on CancelledError."""
    relay = _make_relay()
    task = asyncio.create_task(_session_watchdog([relay]))
    await asyncio.sleep(0)
    task.cancel()
    # Should not raise
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert task.done()
