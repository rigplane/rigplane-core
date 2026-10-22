"""Split descriptors reach CoreRadio through real HTTP and WebSocket sockets."""

from __future__ import annotations

import asyncio
import json
import time
from contextlib import asynccontextmanager
from dataclasses import replace

import pytest

from rigplane.commands.command_map import CommandMap
from rigplane.profiles import get_radio_profile
from rigplane.types import Mode
from rigplane.web.radio_poller import RadioPoller
from rigplane.web.server import WebConfig, WebServer
from test_rigctld_split import _pause_mode_preservation
from test_rigctld_wsjtx_split_mk2 import MODELS, _exchange, _peer
from test_web_server import (
    _read_http_response,
    _ws_connect,
    _ws_recv_frame,
    _ws_send_text,
)

CASES = [
    (
        "set_split_freq",
        {"freq_hz": 14_080_100},
        (14_080_100, Mode.LSB, 3, 1),
        (
            "set_selected_freq",
            "get_selected_freq",
            "set_unselected_freq",
            "get_unselected_freq",
        ),
    ),
    (
        "set_split_mode",
        {"mode": int(Mode.RTTY), "data": False, "filter_width": 2},
        (7_076_000, Mode.RTTY, 2, 0),
        ("set_unselected_mode", "get_unselected_mode", "get_unselected_freq"),
    ),
]


@asynccontextmanager
async def _web_peer(model, *, profile=None):
    async with _peer(model, profile=profile) as (radio, peer, _server, reader, writer):
        radio._civ_stream_ready = True
        radio._last_civ_data_received = time.monotonic()
        web = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
        poller = RadioPoller(
            radio, web.command_queue, state_store=web.command_state_store
        )
        web._server = await asyncio.start_server(web._accept_client, "127.0.0.1", 0)
        executed = asyncio.Event()
        entries = []

        async def consume():
            await web.command_queue.wait(2)
            entry = web.command_queue.take_entry()
            assert entry is not None
            entries.append(entry)
            await poller._execute_queued_entry(entry)
            executed.set()

        consumer = asyncio.create_task(consume())
        try:
            yield web, peer, entries, executed, (radio, reader, writer)
        finally:
            web._server.close()
            await web._server.wait_closed()
            web._server = None
            tasks = (consumer, *tuple(web._client_tasks))
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


async def _request(web, protocol, name, params):
    payload = {"id": "split-write", "name": name, "params": params}
    if protocol == "http":
        reader, writer = await asyncio.open_connection("127.0.0.1", web.port)
        try:
            body = json.dumps(payload).encode()
            writer.write(
                (
                    f"POST /api/v1/commands HTTP/1.1\r\n"
                    f"Host: 127.0.0.1:{web.port}\r\n"
                    "Connection: close\r\nContent-Type: application/json\r\n"
                    f"Content-Length: {len(body)}\r\n\r\n"
                ).encode()
                + body
            )
            await writer.drain()
            status, _headers, response = await asyncio.wait_for(
                _read_http_response(reader), 2
            )
            return status, json.loads(response)
        finally:
            writer.close()
            await writer.wait_closed()
    reader, writer, _ = await _ws_connect("127.0.0.1", web.port, "/api/v1/ws")
    try:
        await _ws_send_text(writer, json.dumps({"type": "cmd", **payload}))
        while True:
            _opcode, response = await _ws_recv_frame(reader, timeout=2)
            result = json.loads(response)
            if result.get("type") == "response" and result.get("id") == payload["id"]:
                return None, result
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("protocol", ["http", "ws"])
@pytest.mark.parametrize("name,params,expected,required", CASES)
async def test_web_split_descriptor_writes_physical_tuple(
    model, protocol, name, params, expected, required
):
    async with _web_peer(model) as (web, peer, entries, executed, _rigctld):
        before = dict(peer.slots)
        status, result = await _request(web, protocol, name, params)
        assert status == (200 if protocol == "http" else None)
        assert result["ok"] is True
        await asyncio.wait_for(executed.wait(), 2)
        assert len(entries) == 1
        assert entries[0].command.name == name
        assert peer.slots["B"] == expected
        assert peer.slots["A"] == before["A"]
        assert peer.selected == "A"


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("protocol", ["http", "ws"])
@pytest.mark.parametrize(
    "name,params,missing",
    [(name, params, key) for name, params, _expected, keys in CASES for key in keys],
)
async def test_web_split_missing_profile_command_refuses_before_io(
    model, protocol, name, params, missing
):
    profile = get_radio_profile(model)
    commands = profile.command_map
    assert commands is not None
    profile = replace(
        profile,
        command_names=profile.command_names - {missing},
        command_map=CommandMap(
            {key: commands.get(key) for key in commands if key != missing}
        ),
    )
    async with _web_peer(model, profile=profile) as (
        web,
        peer,
        entries,
        _executed,
        _rigctld,
    ):
        before = dict(peer.slots)
        status, result = await _request(web, protocol, name, params)
        if protocol == "http":
            assert status == 409
        assert result["ok"] is False
        assert result["error"] == "unsupported_command"
        assert not entries
        assert not peer.requests
        assert peer.slots == before


@pytest.mark.asyncio
@pytest.mark.parametrize("model", MODELS)
@pytest.mark.parametrize("protocol", ["http", "ws"])
async def test_web_select_after_rigctld_preservation_never_sets(
    monkeypatch, model, protocol
):
    async with _web_peer(model) as (web, peer, entries, executed, rigctld):
        radio, reader, writer = rigctld
        before = dict(peer.slots)
        read_done, release = _pause_mode_preservation(monkeypatch, radio)
        reply = asyncio.create_task(_exchange(writer, reader, b"X USB\n"))
        try:
            await asyncio.wait_for(read_done.wait(), 1)
            status, result = await _request(
                web, protocol, "select_vfo", {"vfo": "B"}
            )
            assert status == (200 if protocol == "http" else None)
            assert result["ok"] is True
            await asyncio.wait_for(executed.wait(), 2)
            assert len(entries) == 1
            assert peer.selected == "B"
            release.set()
            assert await reply == b"RPRT -9\n"
            assert not any(
                cmd == 0x26 and len(data) > 1 for cmd, _sub, data in peer.requests
            )
            assert peer.slots == before
        finally:
            release.set()
            if not reply.done():
                reply.cancel()
            await asyncio.gather(reply, return_exceptions=True)
