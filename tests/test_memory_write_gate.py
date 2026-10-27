"""Memory writes/clears refused without a verified write layout (MOR-3644).

``set_memory_contents`` builds one fixed 26-byte ``1A 00`` record
(``commands/memory.py: build_memory_contents_set``). The IC-705 and IC-9700
``1A 00`` record is a different layout (group/band code, select/split byte,
two 47-byte channel blocks, a 16-char name), so a client call could corrupt a
real stored channel. Only a profile whose ``[memory]`` section declares a
``write_layout`` may write or clear; every profile that merely declares the
commands (IC-705, IC-9700, IC-7300, ...) and every profile with no
``[memory]`` section is refused before any byte reaches the wire.

All radios here are in-process mocks/simulators; no hardware is touched.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.profiles import MemoryWriteUnverifiedError
from rigplane.radio import IcomRadio
from rigplane.types import MemoryChannel
from rigplane.web.radio_poller import RadioPoller
from rigplane.web.server import WebConfig, WebServer
from test_radio import MockTransport
from test_web_server import _addr, _http_get
from test_web_split_writes import _request


def _radio(model: str, transport: MockTransport) -> IcomRadio:
    radio = IcomRadio("192.168.1.100", timeout=2.0, model=model)
    radio._civ_transport = radio._ctrl_transport = transport
    radio._connected = True
    # The fields the real fire-and-forget send path checks (same as
    # test_memory_slot_lock's harness) so an accepted write reaches the wire.
    radio._civ_stream_ready = True
    radio._last_civ_data_received = time.monotonic()
    return radio


def _mem(channel: int = 1) -> MemoryChannel:
    return MemoryChannel(
        channel=channel,
        frequency_hz=14_074_000,
        mode=1,
        filter=1,
        scan=0,
        datamode=0,
        tonemode=0,
        name="FT8",
    )


# ---------------------------------------------------------------------------
# Backend: profiles without a verified write layout send nothing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-705", "IC-9700"])
async def test_unverified_profiles_refuse_set_memory_contents(model: str) -> None:
    transport = MockTransport()
    radio = _radio(model, transport)
    try:
        with pytest.raises(MemoryWriteUnverifiedError) as excinfo:
            await radio.set_memory_contents(_mem())
    finally:
        radio._connected = False
    assert excinfo.value.reason_code == "memory_write_unverified"
    # The wire shows no bytes: the 26-byte IC-7610-shaped record is never sent.
    assert transport.sent_packets == []


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-705", "IC-9700"])
async def test_unverified_profiles_refuse_memory_clear(model: str) -> None:
    transport = MockTransport()
    radio = _radio(model, transport)
    try:
        with pytest.raises(MemoryWriteUnverifiedError) as excinfo:
            await radio.memory_clear(5)
    finally:
        radio._connected = False
    assert excinfo.value.reason_code == "memory_write_unverified"
    assert transport.sent_packets == []


@pytest.mark.asyncio
async def test_profile_without_memory_section_refuses_both() -> None:
    """IC-7300 has the commands but no ``[memory]`` section at all."""
    transport = MockTransport()
    radio = _radio("IC-7300", transport)
    try:
        with pytest.raises(MemoryWriteUnverifiedError, match="IC-7300"):
            await radio.set_memory_contents(_mem())
        with pytest.raises(MemoryWriteUnverifiedError, match="IC-7300"):
            await radio.memory_clear(5)
    finally:
        radio._connected = False
    assert transport.sent_packets == []


# ---------------------------------------------------------------------------
# Backend: the opted-in profile (IC-7610) is unchanged
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ic7610_set_memory_contents_still_writes_the_record() -> None:
    transport = MockTransport()
    radio = _radio("IC-7610", transport)
    try:
        await radio.set_memory_contents(_mem())
    finally:
        radio._connected = False
    assert len(transport.sent_packets) == 1
    frame = transport.sent_packets[0]
    # fe fe 98 e0 1a 00 <channel BCD> <26-byte record> fd
    assert frame[4:6] == b"\x1a\x00"
    assert frame[-1] == 0xFD
    assert frame.endswith(b"FT8" + b"\x00" * 8 + b"\xfd")


@pytest.mark.asyncio
async def test_ic7610_memory_clear_still_writes() -> None:
    transport = MockTransport()
    radio = _radio("IC-7610", transport)
    try:
        await radio.memory_clear(10)
    finally:
        radio._connected = False
    assert len(transport.sent_packets) == 1
    assert transport.sent_packets[0][4] == 0x0B


# ---------------------------------------------------------------------------
# Dispatch: the shared WS/HTTP handler arm refuses before enqueueing
# ---------------------------------------------------------------------------


class TestHandlerWriteGate:
    def _handler(self, model: str) -> tuple[Any, Any]:
        from rigplane.web.handlers import ControlHandler
        from rigplane.web.radio_poller import CommandQueue

        ws = MagicMock()
        ws.send_text = AsyncMock()
        radio = _radio(model, MockTransport())
        radio._connected = False  # refused before enqueue; never touched
        server = MagicMock()
        server.command_queue = CommandQueue()
        handler = ControlHandler(
            ws=ws,
            radio=radio,
            server_version="test",
            radio_model=model,
            server=server,
        )
        return handler, server.command_queue

    @pytest.mark.asyncio
    @pytest.mark.parametrize("model", ["IC-705", "IC-9700", "IC-7300"])
    async def test_memory_clear_refused_and_not_enqueued(self, model: str) -> None:
        handler, queue = self._handler(model)
        with pytest.raises(MemoryWriteUnverifiedError):
            await handler._enqueue_command("memory_clear", {"channel": 5})
        assert queue.drain() == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("model", ["IC-705", "IC-9700", "IC-7300"])
    async def test_set_memory_contents_refused_and_not_enqueued(
        self, model: str
    ) -> None:
        handler, queue = self._handler(model)
        with pytest.raises(MemoryWriteUnverifiedError):
            await handler._enqueue_command("set_memory_contents", _mem().__dict__)
        assert queue.drain() == []

    @pytest.mark.asyncio
    async def test_ic7610_memory_clear_still_enqueues(self) -> None:
        handler, queue = self._handler("IC-7610")
        result = await handler._enqueue_command("memory_clear", {"channel": 10})
        assert result == {"channel": 10}
        from rigplane.web.radio_poller import MemoryClear

        assert queue.drain() == [MemoryClear(10)]

    @pytest.mark.asyncio
    async def test_ws_response_carries_the_stable_error_code(self) -> None:
        handler, _queue = self._handler("IC-705")
        await handler._dispatch_command(7, "memory_clear", {"channel": 5})
        reply = json.loads(handler._ws.send_text.await_args_list[-1].args[0])
        assert reply["id"] == 7
        assert reply["ok"] is False
        assert reply["error"] == "memory_write_unverified"
        assert "write layout" in reply["message"]


# ---------------------------------------------------------------------------
# HTTP seam: POST /api/v1/commands answers the stable code and sends nothing
# ---------------------------------------------------------------------------


async def _serve(radio: IcomRadio) -> WebServer:
    web = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    web._server = await asyncio.start_server(web._accept_client, "127.0.0.1", 0)
    return web


async def _shutdown(web: WebServer, radio: IcomRadio) -> None:
    web._server.close()
    await web._server.wait_closed()
    web._server = None
    tasks = tuple(web._client_tasks)
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    radio._connected = False


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("memory_clear", {"channel": 5}),
        (
            "set_memory_contents",
            {
                "channel": 1,
                "frequency_hz": 14_074_000,
                "mode": 1,
                "filter": 1,
                "scan": 0,
                "datamode": 0,
                "tonemode": 0,
                "name": "FT8",
            },
        ),
    ],
)
async def test_http_seam_refuses_unverified_memory_writes(
    name: str, params: dict[str, Any]
) -> None:
    transport = MockTransport()
    radio = _radio("IC-705", transport)
    web = await _serve(radio)
    try:
        status, body = await _request(web, "http", name, params)
    finally:
        await _shutdown(web, radio)
    assert status == 409
    assert body["ok"] is False
    assert body["error"] == "memory_write_unverified"
    assert transport.sent_packets == []


@pytest.mark.asyncio
async def test_http_seam_still_writes_for_the_opted_in_profile() -> None:
    """IC-7610 (simulated): the write crosses the HTTP seam unchanged."""
    from rigplane.core.state_pipeline_contracts import (
        FieldPath,
        Observation,
        SourceMetadata,
    )

    transport = MockTransport()
    radio = _radio("IC-7610", transport)
    web = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    poller = RadioPoller(radio, web.command_queue, state_store=web.command_state_store)
    web.command_state_store.apply_current(
        Observation(
            path=FieldPath.global_("tx_state", "ptt"),
            value=False,
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=asyncio.get_running_loop().time(),
            max_age=60,
        )
    )
    web._server = await asyncio.start_server(web._accept_client, "127.0.0.1", 0)
    consumed = asyncio.Event()

    async def consume() -> None:
        await web.command_queue.wait(2)
        entry = web.command_queue.take_entry()
        consumed.set()
        assert entry is not None
        await poller._execute_queued_entry(entry)

    consumer = asyncio.create_task(consume())
    try:
        status, body = await _request(
            web,
            "http",
            "set_memory_contents",
            {
                "channel": 1,
                "frequency_hz": 14_074_000,
                "mode": 1,
                "filter": 1,
                "scan": 0,
                "datamode": 0,
                "tonemode": 0,
                "name": "FT8",
            },
        )
        await asyncio.wait_for(consumed.wait(), timeout=5)
        await asyncio.wait_for(consumer, timeout=5)
    finally:
        await _shutdown(web, radio)
    assert status == 200
    assert body["ok"] is True
    assert body["result"] == {"channel": 1}
    assert len(transport.sent_packets) == 1
    assert transport.sent_packets[0][4:6] == b"\x1a\x00"


@pytest.mark.asyncio
async def test_memories_listing_stays_unavailable_for_ic7610() -> None:
    """Adding ``[memory]`` to ic7610.toml must not turn on the read listing:
    the ``memory_read`` capability stays undeclared (MOR-3627 boundary)."""
    transport = MockTransport()
    radio = _radio("IC-7610", transport)
    web = await _serve(radio)
    try:
        host, port = _addr(web)
        status, _headers, _body = await _http_get(
            host, port, "/api/v1/memories?group=0&start=1&count=3"
        )
    finally:
        await _shutdown(web, radio)
    assert status == 404
    assert transport.sent_packets == []
