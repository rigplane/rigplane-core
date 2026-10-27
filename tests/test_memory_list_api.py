"""Read-only memory listing: radio method and ``GET /api/v1/memories`` (MOR-3627).

The radio answers from the real-shaped IC-705 golden record (MOR-3650); the
requests the server sends are checked on the wire.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import pytest

from rigplane.backends.rigctld_client import RigctldClientRadio
from rigplane.commands import CONTROLLER_ADDR
from rigplane.commands._frame import build_civ_frame
from rigplane.commands.commander import Priority
from rigplane.core.exceptions import CommandError, TimeoutError
from rigplane.core.radio_protocol import MemoryReadCapable
from rigplane.radio import IcomRadio
from rigplane.types import CivFrame
from rigplane.web.server import WebConfig, WebServer
from _helpers import wrap_civ_in_udp
from test_icom_memory_entry import _IC705_GOLDEN, _block, _ic9700_reply
from test_mor2899_api_info_rigctld import _MemoryWriter, _status_and_json
from test_radio import MockTransport


_RADIOS: list[IcomRadio] = []


@pytest.fixture(autouse=True)
def _disconnect_radios():
    yield
    for radio in _RADIOS:
        radio._connected = False  # keeps the "collected while connected" warning quiet
    _RADIOS.clear()


def _radio(model: str = "IC-705", answers: dict[tuple[int, int], bytes] | None = None):
    """A connected radio whose CI-V reads are answered from ``answers``."""
    radio = IcomRadio("192.168.1.100", model=model)
    radio._civ_transport = MockTransport()
    radio._ctrl_transport = radio._civ_transport
    radio._connected = True
    _RADIOS.append(radio)
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
    status, body = await _get(_radio(), "/api/v1/memories?group=0&start=0&count=25")
    assert status == 200
    golden = Path(__file__).parent / "fixtures" / "ic705-memory-page.json"
    assert body == json.loads(golden.read_text(encoding="utf-8"))
    assert body["addressing"] == "group_channel"
    assert body["groups"] == {"first": 0, "last": 99}
    assert body["channels"] == {"first": 0, "last": 99}
    empty, entry = body["entries"][3:5]
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


def _reply(radio_addr: int, data: bytes) -> bytes:
    civ = build_civ_frame(CONTROLLER_ADDR, radio_addr, 0x1A, sub=0x00, data=data)
    return wrap_civ_in_udp(civ)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "addr", "group", "replies"),
    [
        ("IC-705", 0xA4, 0, [bytes.fromhex("00000003ff"), _IC705_GOLDEN]),
        (
            "IC-9700",
            0xA2,
            2,
            [
                bytes.fromhex("020001ff"),
                _ic9700_reply(
                    _block(freq="0439000000"), band=2, channel=2, name=b"UHF TEST"
                ),
            ],
        ),
    ],
)
async def test_reads_settle_through_the_real_request_tracker(
    model: str, addr: int, group: int, replies: list[bytes]
) -> None:
    """A 1A 00 read waits for the data reply, not for an ACK (no timeout)."""
    transport = MockTransport()
    radio = IcomRadio("192.168.1.100", timeout=2.0, model=model)
    radio._civ_transport = radio._ctrl_transport = transport
    radio._connected = True
    first = 1 if model == "IC-9700" else 3
    for send, reply in enumerate(replies, start=1):
        transport.queue_response_on_send(send, _reply(addr, reply))
    try:
        entries = await radio.read_memory_entries(group, [first, first + 1])
    finally:
        radio._connected = False
    assert [e.empty for e in entries] == [True, False]
    assert entries[1].name in ("TEST RPT", "UHF TEST")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "addr", "group", "first", "late", "valid", "name"),
    [
        ("IC-705", 0xA4, 0, 3, bytes.fromhex("00000003ff"), _IC705_GOLDEN, "TEST RPT"),
        (
            "IC-9700",
            0xA2,
            2,
            1,
            bytes.fromhex("020001ff"),
            _ic9700_reply(
                _block(freq="0439000000"), band=2, channel=2, name=b"UHF TEST"
            ),
            "UHF TEST",
        ),
    ],
)
async def test_late_memory_reply_does_not_settle_the_next_slot(
    model: str, addr: int, group: int, first: int, late: bytes, valid: bytes, name: str
) -> None:
    transport = MockTransport()
    radio = IcomRadio("radio.invalid", timeout=0.1, model=model)
    radio._civ_transport = radio._ctrl_transport = transport
    radio._connected = True
    try:
        with pytest.raises(TimeoutError, match="CI-V response timed out"):
            await radio.read_memory_entries(group, [first])
        assert len(transport.sent_packets) == 1
        transport.queue_response_on_send(2, _reply(addr, late))
        transport.queue_response_on_send(2, _reply(addr, valid))
        entries = await radio.read_memory_entries(group, [first + 1])
        assert len(transport.sent_packets) == 2
        assert [(e.group, e.channel, e.empty, e.name) for e in entries] == [
            (group, first + 1, False, name)
        ]
    finally:
        radio._connected = False
        await radio._civ_runtime.stop_pump()


@pytest.mark.asyncio
async def test_memories_endpoint_refuses_a_backend_without_a_profile() -> None:
    radio = RigctldClientRadio(host="127.0.0.1", port=0)
    status, body = await _get(radio, "/api/v1/memories")
    assert (status, body["error"]) == (404, "unsupported")


@pytest.mark.asyncio
async def test_memories_endpoint_maps_a_serial_io_failure_to_503() -> None:
    radio = _radio()

    async def broken(frame: bytes, **_: Any) -> CivFrame:
        raise OSError(5, "Input/output error")

    radio._send_civ_raw = broken  # type: ignore[method-assign]
    status, body = await _get(radio, "/api/v1/memories?count=1")
    assert (status, body["error"]) == (503, "not_connected")
