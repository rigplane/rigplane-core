"""Recall of a listed memory channel (MOR-3627): ``set_memory_mode`` with a group.

IC-705: ``08 A0 gggg`` selects the memory group, ``08 cccc`` the channel; both
are answered with an ACK. The operation selects memory; it writes no contents
and sends no transmit-start command.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from _helpers import CIV_HEADER_SIZE, wrap_civ_in_udp
from rigplane.commands import CONTROLLER_ADDR, _CMD_ACK
from rigplane.commands._frame import build_civ_frame
from rigplane.core.exceptions import CommandError
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.profiles import get_radio_profile
from rigplane.radio import IcomRadio
from rigplane.types import CivFrame
from rigplane.web.radio_poller import RadioPoller, SetMemoryMode
from rigplane.web.server import WebConfig, WebServer
from test_memory_commands import TestHandlerMemoryDispatch
from test_radio import MockTransport
from test_web_split_writes import _request

_NAK = 0xFA


def _answer(command: int, addr: int = 0xA4) -> bytes:
    return wrap_civ_in_udp(build_civ_frame(CONTROLLER_ADDR, addr, command))


def _radio(model: str, transport: MockTransport) -> IcomRadio:
    radio = IcomRadio("192.168.1.100", timeout=2.0, model=model)
    radio._civ_transport = radio._ctrl_transport = transport
    radio._connected = True
    return radio


@pytest.mark.asyncio
async def test_ic705_recall_selects_the_group_then_the_channel() -> None:
    transport = MockTransport()
    transport.queue_response_on_send(1, _answer(_CMD_ACK))
    transport.queue_response_on_send(2, _answer(_CMD_ACK))
    radio = _radio("IC-705", transport)
    try:
        await radio.set_memory_mode(4, group=12)
    finally:
        radio._connected = False
    expected = ["fefea4e008a00012fd", "fefea4e0080004fd"]
    assert len(transport.sent_packets) == 2
    for packet, frame in zip(transport.sent_packets, expected, strict=True):
        assert packet.endswith(bytes.fromhex(frame))


@pytest.mark.asyncio
async def test_ic705_recall_reports_a_rejection() -> None:
    transport = MockTransport()
    transport.queue_response_on_send(1, _answer(_NAK))
    radio = _radio("IC-705", transport)
    try:
        with pytest.raises(CommandError, match="rejected memory recall 12/4"):
            await radio.set_memory_mode(4, group=12)
    finally:
        radio._connected = False
    assert len(transport.sent_packets) == 1  # the channel is not selected after a NAK


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-9700", "IC-7610"])
async def test_recall_needs_a_profile_that_declares_it(model: str) -> None:
    transport = MockTransport()
    radio = _radio(model, transport)
    try:
        with pytest.raises(CommandError, match="does not support memory recall"):
            await radio.set_memory_mode(1, group=1)
    finally:
        radio._connected = False
    assert transport.sent_packets == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("group", "channel"), [(100, 0), (0, 100), (-1, 0)])
async def test_recall_refuses_a_slot_outside_the_profile(
    group: int, channel: int
) -> None:
    transport = MockTransport()
    radio = _radio("IC-705", transport)
    try:
        with pytest.raises(ValueError):
            await radio.set_memory_mode(channel, group=group)
    finally:
        radio._connected = False
    assert transport.sent_packets == []


@pytest.mark.asyncio
async def test_two_recalls_never_interleave_their_group_and_channel_selects() -> None:
    radio = _radio("IC-705", MockTransport())
    sent: list[bytes] = []

    async def fake_expect(civ: bytes, **_: object):
        sent.append(civ)
        await asyncio.sleep(0)  # let the other recall run if nothing serializes them
        return CivFrame(CONTROLLER_ADDR, 0xA4, _CMD_ACK, None, b"", 0x00)

    radio._send_civ_expect = fake_expect  # type: ignore[method-assign]
    try:
        await asyncio.gather(
            radio.set_memory_mode(4, group=1), radio.set_memory_mode(7, group=2)
        )
    finally:
        radio._connected = False
    assert [frame[4:-1].hex() for frame in sent] == [
        "08a00001",
        "080004",
        "08a00002",
        "080007",
    ]


@pytest.fixture
async def recall_web():
    transport = MockTransport()
    radio = _radio("IC-705", transport)
    radio._civ_stream_ready = True
    radio._last_civ_data_received = time.monotonic()
    web = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    poller = RadioPoller(radio, web.command_queue, state_store=web.command_state_store)
    # Match _web_peer -> _peer: publish observed receive state before consuming.
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
    entries = []

    async def consume():
        await web.command_queue.wait(2)
        entry = web.command_queue.take_entry()
        assert entry is not None
        entries.append(entry)
        await poller._execute_queued_entry(entry)

    consumer = asyncio.create_task(consume())
    try:
        yield web, transport, entries, consumer
    finally:
        web._server.close()
        await web._server.wait_closed()
        web._server = None
        tasks = (consumer, *tuple(web._client_tasks))
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        radio._connected = False
        await radio._force_cleanup_civ()


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["http", "ws"])
@pytest.mark.parametrize(
    ("channel", "channel_frame"),
    [(0, "fefea4e0080000fd"), (4, "fefea4e0080004fd")],
)
async def test_ic705_web_recall_executes_group_then_channel(
    recall_web, protocol: str, channel: int, channel_frame: str
) -> None:
    web, transport, entries, consumer = recall_web
    transport.queue_response_on_send(1, _answer(_CMD_ACK))
    transport.queue_response_on_send(2, _answer(_CMD_ACK))

    status, result = await _request(
        web, protocol, "set_memory_mode", {"group": 12, "channel": channel}
    )
    await asyncio.wait_for(consumer, 2)

    assert status == (200 if protocol == "http" else None)
    assert result["ok"] is True
    assert result["result"] == {"group": 12, "channel": channel}
    assert [entry.command for entry in entries] == [SetMemoryMode(channel, 12)]
    assert [packet[CIV_HEADER_SIZE:] for packet in transport.sent_packets] == [
        bytes.fromhex("fefea4e008a00012fd"),
        bytes.fromhex(channel_frame),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["http", "ws"])
@pytest.mark.parametrize("group", [-1, 100])
async def test_ic705_web_recall_rejects_group_without_channel_select(
    recall_web, protocol: str, group: int
) -> None:
    web, transport, entries, _consumer = recall_web

    status, result = await _request(
        web, protocol, "set_memory_mode", {"group": group, "channel": 4}
    )

    assert status == (400 if protocol == "http" else None)
    if protocol == "ws":
        assert result["ok"] is False
    assert result["error"] == (
        "invalid_request" if protocol == "http" else "command_failed"
    )
    assert result["message"] == f"group must be 0-99, got {group}"
    assert entries == []
    assert web.command_queue.drain_entries() == []
    assert transport.sent_packets == []


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["http", "ws"])
@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"group": -0.5, "channel": 4}, "group"),
        ({"group": 0.5, "channel": 4}, "group"),
        ({"group": True, "channel": 4}, "group"),
        ({"group": False, "channel": 4}, "group"),
        ({"group": 0, "channel": 4.5}, "channel"),
        ({"group": 0, "channel": True}, "channel"),
        ({"group": 0, "channel": False}, "channel"),
        ({"group": "0.5", "channel": 4}, "group"),
        ({"group": 0, "channel": "4.5"}, "channel"),
    ],
)
async def test_ic705_web_recall_rejects_non_integer_addresses(
    recall_web, protocol: str, params: dict[str, object], field: str
) -> None:
    web, transport, entries, _consumer = recall_web

    status, result = await _request(web, protocol, "set_memory_mode", params)

    assert status == (400 if protocol == "http" else None)
    if protocol == "ws":
        assert result["ok"] is False
    assert result["error"] == (
        "invalid_request" if protocol == "http" else "command_failed"
    )
    assert result["message"] == f"{field} must be an integer, got {params[field]!r}"
    assert entries == []
    assert web.command_queue.drain_entries() == []
    assert transport.sent_packets == []


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["http", "ws"])
@pytest.mark.parametrize(
    ("group", "channel"), [(12.0, 4), (12, 4.0), ("12", 4), (12, "4")]
)
async def test_ic705_web_recall_keeps_integer_coercion(
    recall_web, protocol: str, group: int | float | str, channel: int | float | str
) -> None:
    web, transport, entries, consumer = recall_web
    transport.queue_response_on_send(1, _answer(_CMD_ACK))
    transport.queue_response_on_send(2, _answer(_CMD_ACK))

    status, result = await _request(
        web, protocol, "set_memory_mode", {"group": group, "channel": channel}
    )
    await asyncio.wait_for(consumer, 2)

    assert status == (200 if protocol == "http" else None)
    assert result["ok"] is True
    assert result["result"] == {"group": 12, "channel": 4}
    assert [entry.command for entry in entries] == [SetMemoryMode(4, 12)]
    assert [packet[CIV_HEADER_SIZE:] for packet in transport.sent_packets] == [
        bytes.fromhex("fefea4e008a00012fd"),
        bytes.fromhex("fefea4e0080004fd"),
    ]


@pytest.mark.asyncio
async def test_ic705_http_recall_stops_after_group_nak(recall_web) -> None:
    web, transport, entries, consumer = recall_web
    transport.queue_response_on_send(1, _answer(_NAK))
    transport.queue_response_on_send(2, _answer(_CMD_ACK))

    status, result = await _request(
        web, "http", "set_memory_mode", {"group": 12, "channel": 4}
    )
    with pytest.raises(CommandError, match="rejected memory recall 12/4"):
        await asyncio.wait_for(consumer, 2)

    assert status == 500
    assert result["ok"] is False
    assert result["error"] == "command_failed"
    assert result["message"] == "Radio rejected memory recall 12/4"
    assert [entry.command for entry in entries] == [SetMemoryMode(4, 12)]
    assert [packet[CIV_HEADER_SIZE:] for packet in transport.sent_packets] == [
        bytes.fromhex("fefea4e008a00012fd")
    ]


class TestRecallThroughTheControlHandler:
    def _handler(self, model: str):
        handler, queue = TestHandlerMemoryDispatch()._make_handler()
        handler._radio.profile = get_radio_profile(model)
        return handler, queue

    @pytest.mark.asyncio
    async def test_ic705_recall_is_queued_with_its_group(self) -> None:
        handler, queue = self._handler("IC-705")
        result = await handler._enqueue_command(
            "set_memory_mode", {"group": 3, "channel": 0}
        )
        assert result == {"group": 3, "channel": 0}
        assert queue.drain() == [SetMemoryMode(0, 3)]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "params",
        [
            {"group": 100, "channel": 1},
            {"group": 0, "channel": 100},
        ],
    )
    async def test_ic705_recall_rejects_a_bad_slot(self, params: dict) -> None:
        handler, queue = self._handler("IC-705")
        with pytest.raises(ValueError):
            await handler._enqueue_command("set_memory_mode", params)
        assert queue.drain() == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("model", ["IC-705", "IC-9700", "IC-7610"])
    async def test_the_channel_only_form_is_unchanged(self, model: str) -> None:
        """The store flow sends ``set_memory_mode(channel)`` then ``memory_write``."""
        handler, queue = self._handler(model)
        assert await handler._enqueue_command("set_memory_mode", {"channel": 5}) == {
            "channel": 5
        }
        assert queue.drain() == [SetMemoryMode(5)]

    @pytest.mark.asyncio
    async def test_a_profile_without_recall_keeps_the_ic7610_form(self) -> None:
        handler, queue = self._handler("IC-7610")
        assert await handler._enqueue_command("set_memory_mode", {"channel": 5}) == {
            "channel": 5
        }
        assert queue.drain() == [SetMemoryMode(5)]
        with pytest.raises(ValueError, match="no groups"):
            await handler._enqueue_command(
                "set_memory_mode", {"group": 0, "channel": 5}
            )
        handler, _ = self._handler("IC-9700")
        with pytest.raises(ValueError, match="does not support recall"):
            await handler._enqueue_command(
                "set_memory_mode", {"group": 1, "channel": 5}
            )
