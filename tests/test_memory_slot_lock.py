"""Memory-slot operations share one lock across backends (MOR-3768)."""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from _helpers import CIV_HEADER_SIZE
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.backends.yaesu_cat.transport import CatCommandRejected, YaesuCatTransport
from rigplane.commands import CONTROLLER_ADDR
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.types import CivFrame
from rigplane.radio import IcomRadio
from rigplane.rig_loader import load_rig
from rigplane.web.radio_poller import RadioPoller
from rigplane.web.server import WebConfig, WebServer
from test_memory_list_api import _reply
from test_memory_recall import _radio as _icom_recall_radio
from test_radio import MockTransport
from test_web_server import _addr, _http_get
from test_web_split_writes import _request

_RIGS_DIR = Path(__file__).parents[1] / "rigs"


def _icom_radio(model: str = "IC-705") -> IcomRadio:
    return _icom_recall_radio(model, MockTransport())


@pytest.mark.asyncio
async def test_grouped_recall_never_interleaves_memory_write() -> None:
    radio = _icom_radio()
    order: list[str] = []

    async def fake_expect(civ: bytes, **_: object) -> CivFrame:
        payload = civ[4:-1]
        if len(payload) >= 2 and payload[0] == 0x08 and payload[1] == 0xA0:
            order.append("group")
            await asyncio.sleep(0)
        elif payload and payload[0] == 0x08:
            order.append("channel")
        return CivFrame(CONTROLLER_ADDR, 0xA4, 0x00, None, b"", 0x00)

    async def fake_fire_and_forget(_civ: bytes) -> None:
        order.append("memory_write")

    radio._send_civ_expect = fake_expect  # type: ignore[method-assign]
    radio._send_fire_and_forget = fake_fire_and_forget  # type: ignore[method-assign]
    try:
        await asyncio.gather(
            radio.set_memory_mode(4, group=12),
            radio.memory_write(),
        )
    finally:
        radio._connected = False
    assert order == ["group", "channel", "memory_write"]


@pytest.mark.asyncio
async def test_grouped_recall_never_interleaves_channel_only_select() -> None:
    radio = _icom_radio()
    order: list[str] = []

    async def fake_expect(civ: bytes, **_: object) -> CivFrame:
        payload = civ[4:-1]
        if len(payload) >= 2 and payload[0] == 0x08 and payload[1] == 0xA0:
            order.append("group")
            await asyncio.sleep(0)
        elif payload and payload[0] == 0x08:
            order.append("channel")
        return CivFrame(CONTROLLER_ADDR, 0xA4, 0x00, None, b"", 0x00)

    async def fake_fire_and_forget(_civ: bytes) -> None:
        order.append("channel_select")

    radio._send_civ_expect = fake_expect  # type: ignore[method-assign]
    radio._send_fire_and_forget = fake_fire_and_forget  # type: ignore[method-assign]
    try:
        await asyncio.gather(
            radio.set_memory_mode(4, group=12),
            radio.set_memory_mode(7),
        )
    finally:
        radio._connected = False
    assert order == ["group", "channel", "channel_select"]


@pytest.mark.asyncio
async def test_ftx1_memory_reads_do_not_interleave_across_callers() -> None:
    from test_ftx1_memory_read import _MR_25, _MT_25

    radio = YaesuCatRadio("/dev/null", profile=load_rig(_RIGS_DIR / "ftx1.toml"))
    radio._transport._connected = True
    order: list[str] = []
    mr_steps = 0

    async def query(read_cmd: str, **_: object) -> str:
        nonlocal mr_steps
        order.append(read_cmd)
        if read_cmd.startswith("MR"):
            mr_steps += 1
            if mr_steps == 1:
                await asyncio.sleep(0)
        if read_cmd == "MR00025;":
            return _MR_25
        if read_cmd == "MT00025;":
            return _MT_25
        raise CatCommandRejected("?;", command=read_cmd)

    radio._transport.query = AsyncMock(spec=YaesuCatTransport.query, side_effect=query)
    try:
        await asyncio.gather(
            radio.read_memory_entries(0, [25]),
            radio.read_memory_entries(0, [26]),
        )
    finally:
        radio._transport._connected = False
    assert order == ["MR00025;", "MT00025;", "MR00026;"]


class _GatedIc705Transport(MockTransport):
    """Answers each ``1A 00`` slot read only after its test-owned gate opens.

    A read is recorded when its frame is dispatched; the reply is queued only
    when the gate for that channel opens, so the test holds each slot read in
    flight. A ``1A 00`` + address + ``FF`` clear frame (the IC-705's
    declared form, MOR-3644) is recorded and passed through.
    """

    def __init__(self, channels: list[int]) -> None:
        super().__init__()
        self.events: list[str] = []
        self._gates = {channel: asyncio.Event() for channel in channels}
        self._in_flight = {channel: asyncio.Event() for channel in channels}

    def release(self, channel: int) -> None:
        self._gates[channel].set()

    async def wait_in_flight(self, channel: int) -> None:
        await asyncio.wait_for(self._in_flight[channel].wait(), timeout=5)

    async def send_tracked(self, data: bytes) -> None:
        civ = data[CIV_HEADER_SIZE:]
        if civ[4:6] == b"\x1a\x00":
            payload = civ[6:-1]
            if payload.endswith(b"\xff"):  # the declared clear form
                self.events.append("clear")
                await super().send_tracked(data)
                return
            address = payload
            channel = int(address[2:4].hex())
            self.events.append(f"read {channel}")
            self._in_flight[channel].set()
            await self._gates[channel].wait()
            await super().send_tracked(data)
            self.queue_response(_reply(0xA4, address + b"\xff"))
            return
        await super().send_tracked(data)


@pytest.mark.asyncio
async def test_web_memory_clear_runs_between_the_listed_slot_reads() -> None:
    transport = _GatedIc705Transport([0, 1, 2])
    radio = _icom_recall_radio("IC-705", transport)
    # The slot-lock ordering pinned here is profile-agnostic; the shipped
    # IC-705 profile now declares its verified write layout (MOR-3644 part
    # 2), so the clear reaches the wire in the declared 1A 00 + FF form.
    radio._civ_stream_ready = True
    radio._last_civ_data_received = time.monotonic()
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
    entry_taken = asyncio.Event()

    async def consume() -> None:
        await web.command_queue.wait(2)
        entry = web.command_queue.take_entry()
        assert entry is not None
        entry_taken.set()
        await poller._execute_queued_entry(entry)

    consumer = asyncio.create_task(consume())
    try:
        host, port = _addr(web)
        get = asyncio.create_task(
            _http_get(host, port, "/api/v1/memories?group=0&start=0&count=3")
        )
        await transport.wait_in_flight(0)
        # The IC-705 is group-addressed, so the clear names its group
        # (MOR-3644: a grouped radio refuses an unnamed group).
        post = asyncio.create_task(
            _request(web, "http", "memory_clear", {"channel": 5, "group": 0})
        )
        await asyncio.wait_for(entry_taken.wait(), timeout=5)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        transport.release(0)
        await transport.wait_in_flight(1)
        transport.release(1)
        await transport.wait_in_flight(2)
        transport.release(2)

        status, _headers, body = await asyncio.wait_for(get, timeout=5)
        post_status, post_result = await asyncio.wait_for(post, timeout=5)
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

    assert status == 200
    page = json.loads(body)
    assert [entry["channel"] for entry in page["entries"]] == [0, 1, 2]
    assert all(entry["empty"] for entry in page["entries"])
    assert post_status == 200
    assert post_result["ok"] is True
    assert [event for event in transport.events if event != "clear"] == [
        "read 0",
        "read 1",
        "read 2",
    ]
    assert 0 < transport.events.index("clear") < len(transport.events) - 1
