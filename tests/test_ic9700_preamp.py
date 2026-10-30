"""IC-9700 preamp domain: CI-V 0x16 0x02 combines the internal and external preamp.

IC-9700 CI-V Reference Guide p.5, command 16 02: 00 = both OFF, 01 = P.AMP ON,
02 = EXT-P.AMP ON, 03 = both ON.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from _helpers import wrap_civ_in_udp
from rigplane.profiles import resolve_radio_profile
from rigplane.radio import IcomRadio
from rigplane.web.radio_poller import RadioPoller
from rigplane.web.server import WebConfig, WebServer
from test_radio import MockTransport
from test_web_command_batch_http import _FakeWriter
from test_web_server import _ws_connect, _ws_recv_frame, _ws_send_text
from test_web_split_writes import _request
from _helpers import observe_selected_receiver


@pytest.fixture
def mock_transport() -> MockTransport:
    return MockTransport()


@pytest.fixture
def radio(mock_transport: MockTransport):
    r = IcomRadio("192.168.1.103", timeout=2.0, model="IC-9700")
    r._civ_transport = mock_transport
    r._ctrl_transport = mock_transport
    r._connected = True
    observe_selected_receiver(r, 0)
    yield r
    r._connected = False


def test_profile_declares_internal_and_external_preamp_combinations() -> None:
    profile = resolve_radio_profile(model="IC-9700")
    assert profile.pre_values == (0, 1, 2, 3)
    assert profile.pre_labels == {"0": "OFF", "1": "INT", "2": "EXT", "3": "INT+EXT"}


async def test_set_preamp_sends_both_on(
    radio: IcomRadio, mock_transport: MockTransport
) -> None:
    await radio.set_preamp(3)
    assert any(b"\x16\x02\x03\xfd" in packet for packet in mock_transport.sent_packets)


async def _request_preamp(web: WebServer, protocol: str) -> tuple[int | None, dict]:
    if protocol == "http":
        return await _request(web, protocol, "set_preamp", {"level": 3})

    reader, writer, _ = await _ws_connect("127.0.0.1", web.port, "/api/v1/ws")
    try:
        # This fixture has no ready CI-V stream: the server waits up to 2 s
        # between hello and its initial state, before receiving commands.
        for expected in ("hello", "state_update"):
            _, payload = await _ws_recv_frame(reader, timeout=5.0)
            assert json.loads(payload)["type"] == expected
        command_id = "preamp-combination"
        await _ws_send_text(
            writer,
            json.dumps(
                {
                    "type": "cmd",
                    "id": command_id,
                    "name": "set_preamp",
                    "params": {"level": 3},
                }
            ),
        )
        while True:
            _, payload = await _ws_recv_frame(reader, timeout=5.0)
            result = json.loads(payload)
            if result.get("type") == "response" and result.get("id") == command_id:
                return None, result
    finally:
        writer.close()
        await writer.wait_closed()


@pytest.mark.parametrize("protocol", ["http", "ws"])
async def test_web_preamp_combination_reaches_wire_and_public_readback(
    radio: IcomRadio, mock_transport: MockTransport, protocol: str
) -> None:
    web = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    poller = RadioPoller(radio, web.command_queue, state_store=web.command_state_store)
    web._server = await asyncio.start_server(web._accept_client, "127.0.0.1", 0)

    async def get_payload(path: str) -> dict:
        writer = _FakeWriter()
        await web._handle_http(writer, "GET", path, headers={})
        assert writer.response_status == 200
        return writer.response_body

    async def execute_queued_command() -> None:
        await web.command_queue.wait(timeout=5.0)
        entry = web.command_queue.take_entry()
        assert entry is not None
        await poller._execute_queued_entry(entry)

    consumer = None
    try:
        capabilities = await get_payload("/api/v1/capabilities")
        assert capabilities["preValues"] == [0, 1, 2, 3]
        assert capabilities["preLabels"] == {
            "0": "OFF",
            "1": "INT",
            "2": "EXT",
            "3": "INT+EXT",
        }
        assert (await get_payload("/api/v1/state"))["main"]["preamp"] is None

        consumer = asyncio.create_task(execute_queued_command())
        status, result = await asyncio.wait_for(
            _request_preamp(web, protocol), timeout=10.0
        )
        await asyncio.wait_for(consumer, timeout=5.0)
        assert status == (200 if protocol == "http" else None)
        assert result["ok"] is True
        address = bytes([radio.profile.civ_addr])
        command = b"\xfe\xfe" + address + b"\xe0\x16\x02\x03\xfd"
        assert any(packet.endswith(command) for packet in mock_transport.sent_packets)
        assert (await get_payload("/api/v1/state"))["main"]["preamp"] is None

        radio._civ_runtime.start_pump()
        mock_transport.queue_response(
            wrap_civ_in_udp(b"\xfe\xfe\xe0" + address + b"\x16\x02\x03\xfd")
        )
        async with asyncio.timeout(5.0):
            while (await get_payload("/api/v1/state"))["main"]["preamp"] != 3:
                await asyncio.sleep(0)
        assert (await get_payload("/api/v1/state"))["main"]["preamp"] == 3
    finally:
        web._server.close()
        await web._server.wait_closed()
        web._server = None
        tasks = list(web._client_tasks)
        if consumer is not None:
            tasks.append(consumer)
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await radio._force_cleanup_civ()
