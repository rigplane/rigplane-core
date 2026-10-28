"""The memory write admission gate (MOR-3644).

``set_memory_contents`` and ``memory_clear`` pass one admission check
(``profiles.admit_memory_write``) at every ingress: the profile must
declare a structurally valid ``[memory].write_layout`` field table. The
manual-verified layouts themselves -- per-radio fixture bytes, negative
pins and the write/read round trips -- live in
``tests/test_memory_write_layouts.py``; this file pins the GATE:

* radios without a layout (X6100, X6200 -- commands declared absent, no
  ``[memory]`` section) refuse at the backend, at the dispatch arm and at
  the HTTP/WS seam, with the stable error and nothing on the wire;
* an SDK caller splicing a junk layout into a profile refuses the same
  way;
* the opted-in profiles are unchanged: the IC-7610 complete-frame
  goldens, the HTTP seam write and the quiet-RX deferral;
* a write-side ``[memory]`` section does not turn on the read listing
  (IC-7300, IC-7610: ``memory_read`` stays undeclared).

All radios here are in-process mocks/simulators; no hardware is touched.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.profiles import MemoryWriteUnverifiedError
from rigplane.radio import IcomRadio
from rigplane.types import MemoryChannel
from rigplane.web.radio_poller import RadioPoller
from rigplane.web.server import WebConfig, WebServer
from _helpers import CIV_HEADER_SIZE
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
@pytest.mark.parametrize("model", ["X6100", "X6200"])
async def test_unverified_profiles_refuse_set_memory_contents(model: str) -> None:
    transport = MockTransport()
    radio = _radio(model, transport)
    try:
        with pytest.raises(MemoryWriteUnverifiedError) as excinfo:
            await radio.set_memory_contents(_mem())
    finally:
        radio._connected = False
    assert excinfo.value.reason_code == "memory_write_unverified"
    # The wire shows no bytes: no record is built for an unverified radio.
    assert transport.sent_packets == []


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["X6100", "X6200"])
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
async def test_ic7300_write_side_memory_section_does_not_enable_the_read() -> None:
    """IC-7300 declares ``[memory]`` for the WRITE layout only (MOR-3644
    part 2): the 1A 00 read reply is unverified, so the radio method
    refuses and the listing endpoint stays 404 (no ``memory_read``
    capability was added)."""
    from rigplane.core.exceptions import CommandError

    transport = MockTransport()
    radio = _radio("IC-7300", transport)
    try:
        with pytest.raises(CommandError, match="no verified CI-V memory read layout"):
            await radio.read_memory_entries(0, [1])
        web = await _serve(radio)
        try:
            host, port = _addr(web)
            status, _headers, _body = await _http_get(
                host, port, "/api/v1/memories?group=0&start=1&count=3"
            )
        finally:
            await _shutdown(web, radio)
    finally:
        radio._connected = False
    assert status == 404
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
    # Complete-frame golden (MOR-3644): the exact 35-byte IC-7610 record.
    # sent_packets carry the UDP envelope; the CI-V frame starts at
    # CIV_HEADER_SIZE.
    assert transport.sent_packets[0][CIV_HEADER_SIZE:] == bytes.fromhex(
        "fefe98e01a00"  # envelope + 1A 00
        "0001"  # channel 1, 2-byte BCD (MSB first)
        "00"  # scan
        "0040071400"  # 14_074_000, LE BCD (core.types.bcd_encode docstring)
        "01"  # mode USB
        "01"  # filter 1
        "00"  # datamode<<4 | tonemode
        "000000"  # tone frequency
        "000000"  # tsql frequency
        "46543800000000000000"  # name "FT8", 10 bytes
        "00"  # record pad byte
        "fd"
    )


@pytest.mark.asyncio
async def test_ic7610_memory_clear_still_writes() -> None:
    transport = MockTransport()
    radio = _radio("IC-7610", transport)
    try:
        await radio.memory_clear(10)
    finally:
        radio._connected = False
    assert len(transport.sent_packets) == 1
    # Complete-frame golden; the 2-byte channel payload itself is the
    # open MOR-2055 question, unchanged by this fix on purpose.
    assert transport.sent_packets[0][CIV_HEADER_SIZE:] == bytes.fromhex(
        "fefe98e00b0010fd"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("layout", ["civ_47", "", "CIV_26", [], {}])
async def test_sdk_supplied_profile_with_an_unlisted_layout_refuses(
    layout: object,
) -> None:
    """The admission gate checks the shared vocabulary, not mere presence:
    a hand-built profile (no TOML loader in play) carrying an unlisted
    layout value refuses exactly like one that declares none — including
    unhashable values, which must refuse rather than crash."""
    from dataclasses import replace

    transport = MockTransport()
    radio = _radio("IC-705", transport)
    radio._profile = replace(
        radio._profile,
        memory=replace(radio._profile.memory, write_layout=layout),  # type: ignore[arg-type]
    )
    try:
        with pytest.raises(MemoryWriteUnverifiedError):
            await radio.set_memory_contents(_mem())
        with pytest.raises(MemoryWriteUnverifiedError):
            await radio.memory_clear(5)
    finally:
        radio._connected = False
    assert transport.sent_packets == []


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
    @pytest.mark.parametrize("model", ["X6100", "X6200"])
    async def test_memory_clear_refused_and_not_enqueued(self, model: str) -> None:
        handler, queue = self._handler(model)
        with pytest.raises(MemoryWriteUnverifiedError):
            await handler._enqueue_command("memory_clear", {"channel": 5})
        assert queue.drain() == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize("model", ["X6100", "X6200"])
    async def test_set_memory_contents_refused_and_not_enqueued(
        self, model: str
    ) -> None:
        handler, queue = self._handler(model)
        with pytest.raises(MemoryWriteUnverifiedError):
            await handler._enqueue_command("set_memory_contents", _mem().__dict__)
        assert queue.drain() == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("model", "group"), [("IC-705", 3), ("IC-9700", 2), ("IC-7300", 0)]
    )
    async def test_the_opted_in_radios_enqueue_the_write(
        self, model: str, group: int
    ) -> None:
        """MOR-3644 part 2: the manual-verified layouts pass the gate and
        the address reaches the queue (group/band for the addressed
        radios)."""
        from dataclasses import replace

        from rigplane.web.radio_poller import MemoryClear, SetMemoryContents

        handler, queue = self._handler(model)
        mem = replace(_mem(), group=group)
        result = await handler._enqueue_command(
            "memory_clear", {"channel": 5, "group": group}
        )
        assert result == {"channel": 5}
        assert queue.drain() == [MemoryClear(5, group)]
        result = await handler._enqueue_command("set_memory_contents", mem.__dict__)
        assert result == {"channel": mem.channel}
        assert queue.drain() == [SetMemoryContents(mem)]

    @pytest.mark.asyncio
    async def test_ic7610_memory_clear_still_enqueues(self) -> None:
        handler, queue = self._handler("IC-7610")
        result = await handler._enqueue_command("memory_clear", {"channel": 10})
        assert result == {"channel": 10}
        from rigplane.web.radio_poller import MemoryClear

        assert queue.drain() == [MemoryClear(10)]

    @pytest.mark.asyncio
    async def test_ws_response_carries_the_stable_error_code(self) -> None:
        handler, _queue = self._handler("X6100")
        await handler._dispatch_command(7, "memory_clear", {"channel": 5})
        reply = json.loads(handler._ws.send_text.await_args_list[-1].args[0])
        assert reply["id"] == 7
        assert reply["ok"] is False
        assert reply["error"] == "memory_write_unverified"
        assert "write layout" in reply["message"]

    @pytest.mark.asyncio
    async def test_ftx1_refuses_at_the_dispatch_arm(self) -> None:
        """FTX-1 pinned explicitly (MOR-3644): the Yaesu backend has no
        write path at all (``NotImplementedError``, confirmed in
        tests/test_memory_write_layouts.py), so the shared arm refuses it
        with the stable code before anything is enqueued."""
        from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
        from rigplane.profiles import get_radio_profile
        from rigplane.web.handlers import ControlHandler
        from rigplane.web.radio_poller import CommandQueue

        radio = YaesuCatRadio("/dev/null", profile=get_radio_profile("FTX-1"))
        ws = MagicMock()
        ws.send_text = AsyncMock()
        server = MagicMock()
        server.command_queue = CommandQueue()
        handler = ControlHandler(
            ws=ws,
            radio=radio,
            server_version="test",
            radio_model="FTX-1",
            server=server,
        )
        with pytest.raises(MemoryWriteUnverifiedError):
            await handler._enqueue_command("memory_clear", {"channel": 5})
        with pytest.raises(MemoryWriteUnverifiedError):
            await handler._enqueue_command("set_memory_contents", _mem().__dict__)
        assert server.command_queue.drain() == []


# ---------------------------------------------------------------------------
# HTTP seam: POST /api/v1/commands answers the stable code and sends nothing
# ---------------------------------------------------------------------------


async def _serve(radio: IcomRadio) -> WebServer:
    web = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    web._server = await asyncio.start_server(web._accept_client, "127.0.0.1", 0)
    return web


async def _post_batch(web: WebServer, payload: dict[str, Any]) -> tuple[int, Any]:
    """POST /api/v1/commands/batch and return ``(status, json body)``."""
    return await _post_json(web, "/api/v1/commands/batch", payload, timeout=2)


async def _post_json(
    web: WebServer, path: str, payload: dict[str, Any], *, timeout: float
) -> tuple[int, Any]:
    """POST a JSON body to *path* and return ``(status, json body)``."""
    from test_web_server import _read_http_response

    reader, writer = await asyncio.open_connection("127.0.0.1", web.port)
    try:
        body = json.dumps(payload).encode()
        writer.write(
            (
                f"POST {path} HTTP/1.1\r\n"
                f"Host: 127.0.0.1:{web.port}\r\n"
                "Connection: close\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\n\r\n"
            ).encode()
            + body
        )
        await writer.drain()
        status, _headers, response = await asyncio.wait_for(
            _read_http_response(reader), timeout
        )
        return status, json.loads(response)
    finally:
        writer.close()
        await writer.wait_closed()


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
    radio = _radio("X6100", transport)
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
    assert transport.sent_packets[0][CIV_HEADER_SIZE:][4:6] == b"\x1a\x00"


@pytest.mark.asyncio
async def test_http_batch_refuses_unverified_memory_writes_and_skips_the_rest() -> None:
    """The batch endpoint answers the refusal per step and marks the
    remaining steps skipped; nothing is executed or sent."""
    transport = MockTransport()
    radio = _radio("X6100", transport)
    web = await _serve(radio)
    try:
        status, body = await _post_batch(
            web,
            {
                "steps": [
                    {"name": "memory_clear", "params": {"channel": 5}},
                    {"name": "set_memory_contents", "params": _mem().__dict__},
                ]
            },
        )
    finally:
        await _shutdown(web, radio)
    assert status == 200
    assert body["ok"] is False
    assert len(body["results"]) == 2
    refused, skipped = body["results"]
    assert refused["name"] == "memory_clear"
    assert refused["ok"] is False
    assert refused["status"] == "refused"
    assert refused["error"] == "memory_write_unverified"
    assert skipped["status"] == "skipped"
    assert skipped["error"] == "skipped_after_failure"
    assert transport.sent_packets == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "params", "wire"),
    [
        ("memory_clear", {"channel": 5}, b"\x0b"),
        ("set_memory_contents", _mem().__dict__, b"\x1a\x00"),
    ],
    ids=["memory_clear", "set_memory_contents"],
)
async def test_memory_writes_from_the_http_api_defer_until_quiet_rx(
    name: str, params: dict[str, Any], wire: bytes
) -> None:
    """API to drain while keyed, through the production poller loop: the
    real request enqueues, the loop's own staging holds it (no wire bytes,
    request unanswered), and the loop's own quiet-RX release lets it
    complete with exactly one frame on the wire."""
    from rigplane.core.state_pipeline_contracts import (
        FieldPath,
        Observation,
        SourceMetadata,
    )

    transport = MockTransport()
    radio = _radio("IC-7610", transport)
    web = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    poller = RadioPoller(radio, web.command_queue, state_store=web.command_state_store)
    # Keep the loop off the radio's poll surfaces: this test's wire
    # assertions must see only the memory command under test.
    poller._send_query = AsyncMock()  # noqa: SLF001
    poller._fetch_nb_controls = AsyncMock()  # noqa: SLF001
    poller._fetch_mod_inputs = AsyncMock()  # noqa: SLF001
    loop = asyncio.get_running_loop()

    def _observe(ptt: bool) -> None:
        web.command_state_store.apply_current(
            Observation(
                path=FieldPath.global_("tx_state", "ptt"),
                value=ptt,
                source=SourceMetadata(source="test", provider="tests"),
                timestamp_monotonic=loop.time(),
                max_age=60,
            )
        )

    async def _until(predicate: Callable[[], bool], timeout: float = 5.0) -> None:
        deadline = loop.time() + timeout
        while not predicate():
            if loop.time() > deadline:
                pytest.fail("condition not reached before the deadline")
            await asyncio.sleep(0.02)

    _observe(True)  # keyed: the request must be held, not sent
    web._server = await asyncio.start_server(web._accept_client, "127.0.0.1", 0)
    drain = asyncio.create_task(poller._run())  # noqa: SLF001
    try:
        request = asyncio.create_task(
            _post_json(
                web,
                "/api/v1/commands",
                {"id": "deferred", "name": name, "params": params},
                timeout=8,
            )
        )
        # The production loop staged and held the entry (MOR-3644 deferral).
        await _until(lambda: poller._deferred_tx_lane.pending is not None)  # noqa: SLF001
        # Bounded observation window: were the deferral missing, the loop
        # would have executed the entry and answered the request by now.
        await asyncio.sleep(0.2)
        assert transport.sent_packets == []
        assert not request.done()

        # Quiet known RX: the loop itself releases and executes the entry;
        # the request completing is the bounded release condition.
        _observe(False)
        status, body = await asyncio.wait_for(request, timeout=8)
    finally:
        drain.cancel()
        await asyncio.gather(drain, return_exceptions=True)
        await _shutdown(web, radio)
    assert status == 200
    assert body["ok"] is True
    frames = [packet[CIV_HEADER_SIZE:] for packet in transport.sent_packets]
    assert len(frames) == 1
    assert frames[0][4 : 4 + len(wire)] == wire


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
