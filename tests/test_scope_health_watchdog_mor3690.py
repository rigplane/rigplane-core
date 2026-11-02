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
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from rigplane_testkit.timing import await_until
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.web.radio_poller import EnableScope
from rigplane.web.server import WebServer
from test_web_server_coverage import _scope_radio

SCOPE_HIDDEN_PATH = FieldPath.parse("health.health.scope_hidden")

_ZERO_PIXELS = bytes(479)
_LIVE_PIXELS = b"\x00" * 200 + b"\x05\x20" + b"\x00" * 277


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

    async def feed_until_hidden() -> None:
        stop = asyncio.Event()

        async def feed() -> None:
            while not stop.is_set():
                srv._scope_health_check(_zero_frame())  # noqa: SLF001
                await asyncio.sleep(0)

        feeder = asyncio.create_task(feed())
        try:
            # The verdict arrives when the monitor sees flowing zero frames;
            # no fixed feed count or wall-timing schedule is asserted.
            await await_until(
                lambda: _scope_hidden(srv) is True,
                timeout=10.0,
                description="the scope-hidden verdict",
            )
            # Several more monitor windows pass while frames keep flowing.
            await asyncio.sleep(0.5)
            assert _queued(srv) == []
        finally:
            stop.set()
            await asyncio.gather(feeder, return_exceptions=True)

    await _run_monitor(srv, feed_until_hidden())

    assert _queued(srv) == []
    assert _scope_hidden(srv) is True


async def test_transport_stall_reenables_bounded_without_counter_reset(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """MOR-3690 (b): no frames at all -> bounded re-enables, then silence."""
    caplog.set_level("WARNING", logger="rigplane.web.server")
    srv = _make_server(monkeypatch, interval=0.05)

    async def wait_for_give_up() -> None:
        await await_until(
            lambda: "giving up" in caplog.text,
            timeout=10.0,
            description="the watchdog give-up",
        )
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
        await await_until(
            lambda: "giving up" in caplog.text,
            timeout=10.0,
            description="the watchdog give-up",
        )
        assert len(_queued(srv)) == srv._scope_health_max_retries

        # A single non-zero frame (blip) must NOT restore the budget.
        srv._scope_health_check(_live_frame())  # noqa: SLF001
        await asyncio.sleep(0.3)
        assert len(_queued(srv)) == srv._scope_health_max_retries

        # Sustained healthy flow restores it: feed until the visible
        # verdict flips, let several healthy windows pass, then stop.
        stop = asyncio.Event()

        async def feed_live() -> None:
            while not stop.is_set():
                srv._scope_health_check(_live_frame())  # noqa: SLF001
                await asyncio.sleep(0)

        feeder = asyncio.create_task(feed_live())
        try:
            await await_until(
                lambda: _scope_hidden(srv) is False,
                timeout=10.0,
                description="the scope-visible verdict",
            )
            await asyncio.sleep(0.5)
        finally:
            stop.set()
            await asyncio.gather(feeder, return_exceptions=True)
        baseline = len(_queued(srv))
        await await_until(
            lambda: len(_queued(srv)) > baseline,
            timeout=10.0,
            description="the restored-budget re-enable",
        )

    await _run_monitor(srv, scenario())

    assert len(_queued(srv)) > srv._scope_health_max_retries


async def test_new_viewing_session_restores_budget(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """MOR-3690: a fresh viewer session gets a fresh attempt budget."""
    caplog.set_level("WARNING", logger="rigplane.web.server")
    srv = _make_server(monkeypatch, interval=0.05)

    async def scenario() -> None:
        await await_until(
            lambda: "giving up" in caplog.text,
            timeout=10.0,
            description="the watchdog give-up",
        )
        baseline = len(_queued(srv))
        await asyncio.sleep(0.3)
        assert len(_queued(srv)) == baseline

        for handler in list(srv._scope_handlers):
            srv.unregister_scope_handler(handler)
        assert not srv._scope_handlers
        handler = MagicMock()
        await srv.ensure_scope_enabled(handler)
        # The monitor must add more.
        baseline = len(_queued(srv))
        await await_until(
            lambda: len(_queued(srv)) > baseline,
            timeout=10.0,
            description="the new-session re-enable",
        )

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

    async def feed_until_hidden() -> None:
        stop = asyncio.Event()

        async def feed() -> None:
            while not stop.is_set():
                srv._scope_health_check(_zero_frame())  # noqa: SLF001
                await asyncio.sleep(0)

        feeder = asyncio.create_task(feed())
        try:
            await await_until(
                lambda: _scope_hidden(srv) is True,
                timeout=10.0,
                description="the scope-hidden verdict",
            )
        finally:
            stop.set()
            await asyncio.gather(feeder, return_exceptions=True)

    await _run_monitor(srv, feed_until_hidden())

    assert _scope_hidden(srv) is True
    source = _scope_hidden_source(srv)
    assert source is not None
    assert source.source == "local_reconcile"
    assert source.provider == "scope_health"
