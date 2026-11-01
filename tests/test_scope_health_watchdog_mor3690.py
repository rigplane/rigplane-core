"""MOR-3690: scope-health watchdog must not fight the radio front panel.

When the operator opens a front-panel menu (MENU/SET/KEYER...) an Icom radio
keeps sending scope frames, but they are all zero. That is a valid radio
state (the scope is hidden on the radio), not a transport fault: the
watchdog must never write scope-enable for it. Re-enable writes are only
for a real transport stall (no scope frames at all while the link is up),
bounded per session with backoff, and the attempt counter never resets
just because an attempt was made.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.web.radio_poller import EnableScope
from rigplane.web.server import WebServer

SCOPE_HIDDEN_PATH = FieldPath.parse("health.health.scope_hidden")

_ZERO_PIXELS = bytes(479)
_LIVE_PIXELS = b"\x00" * 200 + b"\x05\x20" + b"\x00" * 277


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


def _make_server(monkeypatch: pytest.MonkeyPatch, *, interval: float) -> WebServer:
    srv = WebServer(_scope_radio())
    srv._scope_handlers.add(MagicMock())
    srv._scope_enabled = True
    srv._scope_health_interval = interval
    queued: list[EnableScope] = []
    put_ordered = srv.command_queue.put_ordered

    def observe_and_complete(command: object, **kwargs: Any) -> Any:
        entry = put_ordered(command, **kwargs)
        if isinstance(command, EnableScope):
            queued.append(command)
        future = kwargs.get("future")
        if isinstance(future, asyncio.Future) and not future.done():
            future.set_result(None)
        return entry

    monkeypatch.setattr(srv.command_queue, "put_ordered", observe_and_complete)
    srv._test_queued_enables = queued  # type: ignore[attr-defined]
    return srv


def _zero_frame() -> SimpleNamespace:
    return SimpleNamespace(pixels=_ZERO_PIXELS)


def _live_frame() -> SimpleNamespace:
    return SimpleNamespace(pixels=_LIVE_PIXELS)


async def _run_monitor(srv: WebServer, coro: Any) -> None:
    task = asyncio.create_task(srv._scope_health_monitor())  # noqa: SLF001
    try:
        await coro
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


def _queued(srv: WebServer) -> list[EnableScope]:
    return srv._test_queued_enables  # type: ignore[attr-defined]


def _scope_hidden(srv: WebServer) -> Any:
    snapshot = srv.command_state_store.snapshot()
    for field in snapshot.fields:
        if field.path == SCOPE_HIDDEN_PATH:
            return field.value
    return None


def _scope_hidden_source(srv: WebServer) -> Any:
    snapshot = srv.command_state_store.snapshot()
    for field in snapshot.fields:
        if field.path == SCOPE_HIDDEN_PATH:
            return field.source
    return None


async def test_all_zero_frames_flowing_writes_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MOR-3690 (a): arriving-but-zero frames = menu on the radio, never write."""
    srv = _make_server(monkeypatch, interval=0.05)

    async def feed_zero_frames() -> None:
        # ~40 monitor windows ≈ 400 s of production coverage (10 s cadence),
        # well past the reported ~20 s re-enable loop and the 2-minute case.
        for _ in range(200):
            srv._scope_health_check(_zero_frame())  # noqa: SLF001
            await asyncio.sleep(0.01)

    await _run_monitor(srv, feed_zero_frames())

    assert _queued(srv) == []
    assert _scope_hidden(srv) is True


async def test_transport_stall_reenables_bounded_without_counter_reset(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """MOR-3690 (b): no frames at all -> bounded re-enables, then silence."""
    caplog.set_level("WARNING", logger="rigplane.web.server")
    srv = _make_server(monkeypatch, interval=0.05)

    async def wait_for_give_up() -> None:
        deadline = time.monotonic() + 10.0
        while "giving up" not in caplog.text:
            if time.monotonic() > deadline:
                raise AssertionError("watchdog never gave up")
            await asyncio.sleep(0.02)
        count_after_give_up = len(_queued(srv))
        await asyncio.sleep(0.5)
        assert len(_queued(srv)) == count_after_give_up

    await _run_monitor(srv, wait_for_give_up())

    assert len(_queued(srv)) == srv._scope_health_max_retries
    assert "attempt 1/3" in caplog.text


async def test_brief_blip_does_not_reset_budget_but_sustained_flow_does(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """MOR-3690 (c): budget resets only after healthy flow for a while."""
    caplog.set_level("WARNING", logger="rigplane.web.server")
    srv = _make_server(monkeypatch, interval=0.05)

    async def scenario() -> None:
        deadline = time.monotonic() + 10.0
        while "giving up" not in caplog.text:
            if time.monotonic() > deadline:
                raise AssertionError("watchdog never gave up")
            await asyncio.sleep(0.02)
        assert len(_queued(srv)) == srv._scope_health_max_retries

        # A single non-zero frame (blip) must NOT restore the budget.
        srv._scope_health_check(_live_frame())  # noqa: SLF001
        await asyncio.sleep(0.3)
        assert len(_queued(srv)) == srv._scope_health_max_retries

        # Sustained healthy flow (several monitor windows) restores it.
        for _ in range(30):
            srv._scope_health_check(_live_frame())  # noqa: SLF001
            await asyncio.sleep(0.01)
        assert _scope_hidden(srv) is False
        baseline = len(_queued(srv))
        deadline = time.monotonic() + 10.0
        while len(_queued(srv)) == baseline:
            if time.monotonic() > deadline:
                raise AssertionError("budget was not restored by healthy flow")
            await asyncio.sleep(0.02)

    await _run_monitor(srv, scenario())

    assert len(_queued(srv)) > srv._scope_health_max_retries


async def test_new_viewing_session_restores_budget(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """MOR-3690: a fresh viewer session gets a fresh attempt budget."""
    caplog.set_level("WARNING", logger="rigplane.web.server")
    srv = _make_server(monkeypatch, interval=0.05)

    async def scenario() -> None:
        deadline = time.monotonic() + 10.0
        while "giving up" not in caplog.text:
            if time.monotonic() > deadline:
                raise AssertionError("watchdog never gave up")
            await asyncio.sleep(0.02)
        baseline = len(_queued(srv))
        await asyncio.sleep(0.3)
        assert len(_queued(srv)) == baseline

        for handler in list(srv._scope_handlers):
            srv.unregister_scope_handler(handler)
        assert not srv._scope_handlers
        handler = MagicMock()
        await srv.ensure_scope_enabled(handler)
        # The fresh enable itself queues one write; the monitor must add more.
        baseline = len(_queued(srv))
        deadline = time.monotonic() + 10.0
        while len(_queued(srv)) == baseline:
            if time.monotonic() > deadline:
                raise AssertionError("new session did not restore the budget")
            await asyncio.sleep(0.02)

    await _run_monitor(srv, scenario())

    assert len(_queued(srv)) > srv._scope_health_max_retries


async def test_tx_pause_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    """MOR-3690 (d) / MOR-2859: known transmit -> no scope-health writes."""
    srv = _make_server(monkeypatch, interval=0.02)
    srv._managed_tx_keyed = True  # noqa: SLF001

    async def idle_under_tx() -> None:
        await asyncio.sleep(0.4)

    await _run_monitor(srv, idle_under_tx())

    assert _queued(srv) == []


async def test_hidden_fact_is_source_qualified(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MOR-3690: the hidden verdict lives in the existing state facts."""
    srv = _make_server(monkeypatch, interval=0.05)

    async def feed_zero_frames() -> None:
        for _ in range(30):
            srv._scope_health_check(_zero_frame())  # noqa: SLF001
            await asyncio.sleep(0.01)

    await _run_monitor(srv, feed_zero_frames())

    assert _scope_hidden(srv) is True
    source = _scope_hidden_source(srv)
    assert source is not None
    assert source.source == "local_reconcile"
    assert source.provider == "scope_health"
