"""Read-only memory listing: radio method and ``GET /api/v1/memories`` (MOR-3627).

The radio answers from the real-shaped IC-705 golden record (MOR-3650); the
requests the server sends are checked on the wire.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import parse_qs

import pytest

from rigplane.commands.commander import Priority
from rigplane.core.exceptions import CommandError
from rigplane.core.radio_protocol import MemoryReadCapable
from rigplane.radio import IcomRadio
from rigplane.types import CivFrame
from rigplane.web.server import WebConfig, WebServer
from test_icom_memory_entry import _IC705_GOLDEN
from test_mor2899_api_info_rigctld import _MemoryWriter, _status_and_json
from test_radio import MockTransport


def _radio(model: str = "IC-705", answers: dict[tuple[int, int], bytes] | None = None):
    """A connected radio whose CI-V reads are answered from ``answers``."""
    radio = IcomRadio("192.168.1.100", model=model)
    radio._civ_transport = MockTransport()
    radio._ctrl_transport = radio._civ_transport
    radio._connected = True
    sent: list[tuple[bytes, Priority]] = []
    answers = {(0, 4): _IC705_GOLDEN} if answers is None else answers

    async def fake_send(frame: bytes, *, priority: Priority, **_: Any) -> CivFrame:
        sent.append((frame, priority))
        address = frame[6:-1]
        slot = (int(address[:2].hex()), int(address[2:4].hex()))
        data = answers.get(slot, address + b"\xff")
        return CivFrame(0xE0, 0xA4, 0x1A, 0x00, data, 0x00)

    radio._send_civ_raw = fake_send  # type: ignore[method-assign]
    radio.sent = sent  # type: ignore[attr-defined]
    return radio


@pytest.mark.asyncio
async def test_read_memory_entries_returns_slots_in_order_at_background_priority() -> (
    None
):
    radio = _radio()
    assert isinstance(radio, MemoryReadCapable)
    entries = await radio.read_memory_entries(0, [3, 4, 5])
    assert [(e.channel, e.empty) for e in entries] == [(3, True), (4, False), (5, True)]
    assert entries[1].name == "TEST RPT"
    assert [frame for frame, _ in radio.sent] == [
        bytes.fromhex(f"fefea4e01a000000{channel:04d}fd") for channel in (3, 4, 5)
    ]
    assert {priority for _, priority in radio.sent} == {Priority.BACKGROUND}


@pytest.mark.asyncio
async def test_a_reply_for_another_slot_or_a_corrupt_one_is_an_error() -> None:
    other = _radio(answers={(0, 4): _IC705_GOLDEN[:3] + b"\x05" + _IC705_GOLDEN[4:]})
    with pytest.raises(CommandError, match="answered memory slot 0/5"):
        await other.read_memory_entries(0, [4])
    corrupt = _radio(answers={(0, 4): _IC705_GOLDEN[:-1]})
    with pytest.raises(CommandError, match="unreadable memory slot 0/4"):
        await corrupt.read_memory_entries(0, [4])


@pytest.mark.asyncio
async def test_a_profile_without_memory_channels_refuses() -> None:
    with pytest.raises(CommandError, match="declares no memory channels"):
        await _radio("IC-7300").read_memory_entries(0, [0])


async def _get(radio: Any, target: str) -> tuple[int, dict]:
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    writer = _MemoryWriter()
    path, _, qs = target.partition("?")
    await server._handle_http(
        writer, "GET", path, {"host": "localhost:8470"}, query=parse_qs(qs)
    )
    return _status_and_json(writer)


@pytest.mark.asyncio
async def test_memories_endpoint_serves_a_page_with_the_profile_bounds() -> None:
    status, body = await _get(_radio(), "/api/v1/memories?group=0&start=3&count=3")
    assert status == 200
    assert body["addressing"] == "group_channel"
    assert body["groups"] == {"first": 0, "last": 99}
    assert body["channels"] == {"first": 0, "last": 99}
    empty, entry, _ = body["entries"]
    assert empty == {"group": 0, "channel": 3, "empty": True}
    assert entry == {
        "group": 0,
        "channel": 4,
        "empty": False,
        "name": "TEST RPT",
        "frequencyHz": 146_940_000,
        "mode": "FM",
        "dataMode": False,
        "duplex": "plus",
        "offsetHz": 600_000,
        "toneMode": "tone",
        "toneCentiHz": 13_650,
        "tsqlCentiHz": 13_650,
        "dtcsCode": 23,
    }


@pytest.mark.asyncio
async def test_memories_endpoint_defaults_and_clips_the_page() -> None:
    status, body = await _get(_radio(), "/api/v1/memories")
    assert status == 200
    assert [e["channel"] for e in body["entries"]] == list(range(25))
    status, body = await _get(_radio(), "/api/v1/memories?start=98&count=50")
    assert [e["channel"] for e in body["entries"]] == [98, 99]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "target",
    [
        "/api/v1/memories?group=100",
        "/api/v1/memories?start=100",
        "/api/v1/memories?count=0",
        "/api/v1/memories?count=51",
        "/api/v1/memories?group=x",
    ],
)
async def test_memories_endpoint_rejects_a_bad_query(target: str) -> None:
    radio = _radio()
    status, body = await _get(radio, target)
    assert (status, body["error"]) == (400, "bad_request")
    assert radio.sent == []  # nothing was sent to the radio


@pytest.mark.asyncio
async def test_memories_endpoint_unsupported_radio_and_missing_radio() -> None:
    status, body = await _get(_radio("IC-7300"), "/api/v1/memories")
    assert (status, body["error"]) == (404, "unsupported")
    status, body = await _get(None, "/api/v1/memories")
    assert (status, body["error"]) == (503, "no_radio")


@pytest.mark.asyncio
async def test_memories_endpoint_maps_radio_failures() -> None:
    radio = _radio(answers={(0, 0): _IC705_GOLDEN[:-1]})
    status, body = await _get(radio, "/api/v1/memories?count=1")
    assert (status, body["error"]) == (502, "memory_read_failed")
    radio._connected = False
    status, body = await _get(radio, "/api/v1/memories?count=1")
    assert (status, body["error"]) == (503, "not_connected")
    json.dumps(body)
