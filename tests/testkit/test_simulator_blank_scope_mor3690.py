"""MOR-3690: blank simulator waves decode to all-zero frames; watchdog stays quiet.

The testkit Icom simulator replays recorded ``27 00`` scope waves. With
``scope_blank_waves`` it zeroes the pixel payload while the stream keeps
flowing — the menu-open radio from the MOR-3690 report. The waves travel
the real UDP path, decode through the production CI-V frame parser and
``ScopeAssembler``, and land in the web scope-health watchdog, which must
not write scope-enable for them.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from rigplane.commands import parse_civ_frame
from rigplane.scope import ScopeAssembler
from rigplane.web.radio_poller import EnableScope
from rigplane.web.server import WebServer
from rigplane_testkit.icom import MockIcomRadio
from rigplane_testkit.sim.engine.core import ManualClock
from test_web_server_coverage import _scope_radio
from testkit.test_simulator_scope_stream import _ACK, _client, _open, _waves


def _set(sim: MockIcomRadio, payload: bytes) -> None:
    assert sim._dispatch_civ(0x27, payload, 0xE0) == _ACK  # noqa: SLF001


def _decode(frame: bytes) -> SimpleNamespace:
    """Decode one simulator wave through the production scope path."""
    parsed = parse_civ_frame(frame)
    assert parsed.sub == 0x00
    receiver = parsed.data[0]
    assembled = ScopeAssembler().feed(parsed.data[1:], receiver)
    assert assembled is not None
    assert len(assembled.pixels) > 0
    return SimpleNamespace(pixels=bytes(assembled.pixels))


async def _collect_blank_waves() -> list[SimpleNamespace]:
    clock = ManualClock()
    sim = MockIcomRadio(scope_stream=True, scope_blank_waves=True, clock=clock)
    await sim.start()
    client = await _client()
    try:
        await _open(client, sim)
        _set(sim, b"\x11\x01")
        decoded = []
        for _ in range(10):
            clock.advance(0.13)
            assert sim.pump_scope_stream() >= 0
            for wave in await _waves(client):
                decoded.append(_decode(wave))
            if len(decoded) >= 2:
                break
        return decoded
    finally:
        client.close()
        await sim.stop()


async def test_blank_waves_decode_to_all_zero_frames() -> None:
    """Simulator blank waves keep framing but carry no pixel data."""
    decoded = await _collect_blank_waves()

    assert len(decoded) >= 2
    for frame in decoded:
        assert all(b == 0 for b in frame.pixels)


def _watchdog_server(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[WebServer, list[EnableScope]]:
    srv = WebServer(_scope_radio())
    srv._scope_handlers.add(MagicMock())  # noqa: SLF001
    srv._scope_enabled = True  # noqa: SLF001
    srv._scope_health_interval = 0.05  # noqa: SLF001
    queued: list[EnableScope] = []
    put_ordered = srv.command_queue.put_ordered  # noqa: SLF001

    def observe_and_complete(command: object, **kwargs: Any) -> Any:
        entry = put_ordered(command, **kwargs)
        if isinstance(command, EnableScope):
            queued.append(command)
        future = kwargs.get("future")
        if isinstance(future, asyncio.Future) and not future.done():
            future.set_result(None)
        return entry

    monkeypatch.setattr(srv.command_queue, "put_ordered", observe_and_complete)
    return srv, queued


async def test_watchdog_ignores_simulator_blank_stream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MOR-3690: decoded blank simulator frames never trigger a write."""
    from rigplane.core.state_pipeline_contracts import FieldPath

    frames = await _collect_blank_waves()
    assert frames, "simulator emitted no waves"
    srv, queued = _watchdog_server(monkeypatch)

    monitor = asyncio.create_task(srv._scope_health_monitor())  # noqa: SLF001
    try:
        for i in range(100):
            srv._scope_health_check(frames[i % len(frames)])  # noqa: SLF001
            await asyncio.sleep(0.01)
    finally:
        monitor.cancel()
        await asyncio.gather(monitor, return_exceptions=True)

    assert queued == []
    hidden_path = FieldPath.parse("health.health.scope_hidden")
    values = [
        field.value
        for field in srv.command_state_store.snapshot().fields
        if field.path == hidden_path
    ]
    assert values == [True]
