"""Tests for lifecycle diagnostics — WARN when Radio/WebServer/RigctldServer are GC'd with active tasks.

See GitHub issue 207: help diagnose 'forgot to disconnect/stop' by logging at GC time.
"""

from __future__ import annotations

import asyncio
import gc
import logging

import pytest

from icom_lan.runtime._connection_state import RadioConnectionState
from icom_lan.backends.icom7610.drivers.serial_stub import SerialMockRadio
from icom_lan.radio import IcomRadio
from icom_lan.rigctld.contract import RigctldConfig
from icom_lan.rigctld.server import RigctldServer
from icom_lan.web.server import WebConfig, WebServer


# ---------------------------------------------------------------------------
# Radio: GC with active connection
# ---------------------------------------------------------------------------


@pytest.mark.filterwarnings("ignore:coroutine .* was never awaited:RuntimeWarning")
def test_radio_gc_with_active_connection_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When a Radio is collected while still 'connected', a WARN is emitted."""
    with caplog.at_level(logging.WARNING, logger="icom_lan.radio"):
        radio = IcomRadio("192.168.1.1")
        # Simulate still connected (e.g. user forgot disconnect() or async with exit)
        radio._conn_state = RadioConnectionState.CONNECTED
        del radio
        gc.collect()

    assert any(
        "active" in r.message.lower() and "disconnect" in r.message.lower()
        for r in caplog.records
    ), (
        f"Expected WARN about active connection; got: {[r.message for r in caplog.records]}"
    )
    assert any(r.levelno == logging.WARNING for r in caplog.records)


def test_radio_gc_when_disconnected_does_not_log_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When a Radio is collected after disconnect, no WARN is emitted."""
    with caplog.at_level(logging.WARNING, logger="icom_lan.radio"):
        radio = IcomRadio("192.168.1.1")
        assert radio._conn_state == RadioConnectionState.DISCONNECTED
        del radio
        gc.collect()

    assert not any(
        "active" in r.message.lower() and "disconnect" in r.message.lower()
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# WebServer: GC while running
# ---------------------------------------------------------------------------

# Background tasks (zombie reaper, scope health) hold bound-method references
# to `self`, keeping refcount > 0 even after `del`.  The `_accept_client`
# closure captured by `asyncio.start_server` adds another reference.  To make
# `__del__` fire deterministically within the caplog scope we must cancel all
# background tasks *and* clear the `_server` attribute (which holds the
# closure) while keeping `_server_was_running` so `__del__` can still detect
# the "forgotten stop()" scenario.


async def _force_gc_ready(server: WebServer) -> None:
    """Cancel every background task that prevents immediate GC.

    After cancelling, we must yield to the event loop so coroutine frames
    holding ``self`` are torn down before ``del server``.
    """
    for attr in (
        "_zombie_reaper_task",
        "_scope_health_task",
        "_scope_reenable_task",
        "_dx_client_task",
    ):
        task = getattr(server, attr, None)
        if task is not None and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
            setattr(server, attr, None)
    # Yield so event loop finalises cancelled coroutine frames
    await asyncio.sleep(0)


async def test_web_server_gc_while_running_logs_warning() -> None:
    """When WebServer is collected while _server is still set (forgot stop()), a WARN is emitted."""
    server = WebServer(config=WebConfig(port=0, discovery=False))
    await server.start()
    assert server._server is not None
    server._server.close()
    await server._server.wait_closed()
    await _force_gc_ready(server)
    # Stash a sentinel so __del__ sees "still running", then clear _server
    # to release the closure reference that keeps refcount > 0.
    server._server_was_running = True  # type: ignore[attr-defined]
    server._server = None

    # __del__ logging bypasses caplog in some Python/pytest combos, so we
    # attach a handler directly to the logger to capture warnings reliably.
    caught: list[logging.LogRecord] = []
    handler = logging.Handler()
    handler.emit = lambda record: caught.append(record)  # type: ignore[assignment]
    handler.setLevel(logging.WARNING)
    target_logger = logging.getLogger("icom_lan.web.server")
    target_logger.addHandler(handler)
    try:
        del server
        gc.collect()
    finally:
        target_logger.removeHandler(handler)

    assert any(
        "running" in r.message.lower() or "stop" in r.message.lower() for r in caught
    ), f"Expected WARN about running server; got: {[r.message for r in caught]}"
    assert any(r.levelno == logging.WARNING for r in caught)


async def test_web_server_gc_after_stop_does_not_log_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When WebServer is stopped before GC, no WARN is emitted."""
    with caplog.at_level(logging.WARNING, logger="icom_lan.web.server"):
        server = WebServer(config=WebConfig(port=0, discovery=False))
        await server.start()
        await server.stop()
        del server
        gc.collect()

    assert not any(
        "running" in r.message.lower() or "stop" in r.message.lower()
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# RigctldServer: GC while running
# ---------------------------------------------------------------------------


async def test_rigctld_server_gc_while_running_logs_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When RigctldServer is collected while TCP server is still active, a WARN is emitted."""
    radio = SerialMockRadio()
    await radio.connect()  # required by assert_radio_startup_ready
    with caplog.at_level(logging.WARNING, logger="icom_lan.rigctld.server"):
        server = RigctldServer(radio, RigctldConfig(port=0))
        await server.start()
        assert server._server is not None
        server._server.close()
        await server._server.wait_closed()
        del server
        gc.collect()

    assert any(
        "running" in r.message.lower() or "stop" in r.message.lower()
        for r in caplog.records
    ), f"Expected WARN about active rigctld; got: {[r.message for r in caplog.records]}"
    assert any(r.levelno == logging.WARNING for r in caplog.records)


async def test_rigctld_server_gc_after_stop_does_not_log_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """When RigctldServer is stopped before GC, no WARN is emitted."""
    radio = SerialMockRadio()
    await radio.connect()  # required by assert_radio_startup_ready
    with caplog.at_level(logging.WARNING, logger="icom_lan.rigctld.server"):
        server = RigctldServer(radio, RigctldConfig(port=0))
        await server.start()
        await server.stop()
        del server
        gc.collect()

    assert not any(
        "running" in r.message.lower() or "stop" in r.message.lower()
        for r in caplog.records
    )
