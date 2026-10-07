from __future__ import annotations

import asyncio
import json

import pytest
from test_web_audio_session_rx import _SessionLanRadio, _make_ws
from test_web_auth_compare_digest import _MemoryWriter

from rigplane.web.server import WebConfig, WebServer


class RefusingRadio(_SessionLanRadio):
    def __init__(self, *, fails=True):
        super().__init__()
        self.fails = fails
        self.entered = asyncio.Event()
        self.gate = None

    async def start_rx(self, callback, *, jitter_depth=None):
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.fails:
            raise OSError("private device /private/example could not open")
        await super().start_rx(callback, jitter_depth=jitter_depth)


@pytest.fixture
async def station():
    radio = RefusingRadio()
    server = WebServer(radio, WebConfig(radio_model="IC-7610"))
    yield server, radio, server._audio_broadcaster
    await server._audio_broadcaster._stop_relay()


async def info(server):
    writer = _MemoryWriter()
    await server._handle_http(writer, "GET", "/api/v1/info", {})
    assert writer.buffer.startswith(b"HTTP/1.1 200 ")
    return json.loads(writer.buffer.split(b"\r\n\r\n", 1)[1])["audioReceive"]


FAILED = {
    "schemaVersion": 1,
    "failure": {"stage": "rx_start", "code": "core_rx_start_failed"},
}
CLEAR = {"schemaVersion": 1, "failure": None}


@pytest.mark.parametrize("with_ws", [True, False])
async def test_actual_browser_start_refusal_is_retained_after_client_close(
    station, with_ws
):
    server, radio, broadcaster = station
    assert await info(server) == CLEAR
    ws = _make_ws() if with_ws else None
    queue = await broadcaster.subscribe(ws=ws)
    assert radio.entered.is_set()
    assert radio.audio_session.rx_demand == 0
    assert await info(server) == FAILED
    if ws is not None:
        ws.send_text.assert_awaited_once()
    await broadcaster.unsubscribe(queue)
    assert await info(server) == FAILED
    radio.fails = False
    await broadcaster.ensure_relay()
    assert radio.audio_session.rx_demand == 1
    assert await info(server) == CLEAR


async def test_tap_start_refusal_is_visible_without_any_ws(station):
    server, _, broadcaster = station
    await broadcaster.ensure_relay()
    assert not broadcaster._client_ws
    assert await info(server) == FAILED


async def test_provider_retirement_clears_fact_then_new_refusal_sets_it(station):
    server, _, broadcaster = station
    await broadcaster.ensure_relay()
    assert await info(server) == FAILED
    server._on_provider_generation(9)
    assert await info(server) == CLEAR
    await broadcaster.ensure_relay()
    assert await info(server) == FAILED


async def test_source_replacement_does_not_expose_retired_failure(station):
    server, _, broadcaster = station
    await broadcaster.ensure_relay()
    assert await info(server) == FAILED
    replacement = RefusingRadio(fails=False)
    broadcaster._radio = replacement
    server._radio = replacement
    assert await info(server) == CLEAR
    await broadcaster.ensure_relay()
    assert await info(server) == CLEAR


@pytest.mark.parametrize("old_fails", [True, False])
async def test_late_retired_attempt_cannot_overwrite_current_source_fact(
    station, old_fails
):
    server, old, broadcaster = station
    old.fails = old_fails
    old.gate = asyncio.Event()
    pending = asyncio.create_task(broadcaster.ensure_relay())
    await asyncio.wait_for(old.entered.wait(), timeout=1)
    try:
        server._on_provider_generation(10)
        current = RefusingRadio(fails=not old_fails)
        broadcaster._radio = current
        server._radio = current
        await broadcaster._start_relay()
        expected = CLEAR if old_fails else FAILED
        assert await info(server) == expected
        current_subscription = broadcaster._subscription
    finally:
        old.gate.set()
        await pending
    assert await info(server) == expected
    assert broadcaster._subscription is current_subscription
    assert old.audio_session.rx_demand == 0
