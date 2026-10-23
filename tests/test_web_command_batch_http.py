"""HTTP structured command and ordered batch endpoints."""

from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call

import pytest

from rigplane.core.exceptions import CommandRejectedError
from rigplane.core.state_pipeline_contracts import (
    CommandIntent,
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.profiles import resolve_radio_profile
from rigplane.web import server as web_server
from rigplane.web.radio_poller import (
    CommandQueue,
    CommandQueueEntry,
    SendCiv,
    SetFreq,
    SetMode,
    SetPower,
)
from rigplane.web.server import WebConfig, WebServer


class _FakeWriter:
    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None

    def get_extra_info(self, name: str, default=None):
        if name == "peername":
            return ("127.0.0.1", 55555)
        return default

    @property
    def response_status(self) -> int:
        line = self.buffer.split(b"\r\n", 1)[0]
        return int(line.split(b" ")[1])

    @property
    def response_body(self) -> dict:
        body = self.buffer.split(b"\r\n\r\n", 1)[1]
        return json.loads(body) if body else {}


def _reader_for(payload: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(payload)
    reader.feed_eof()
    return reader


def _radio() -> MagicMock:
    profile = resolve_radio_profile(model="IC-9700")
    radio = MagicMock()
    radio.profile = profile
    radio.model = profile.model
    radio.capabilities = set(profile.capabilities)
    radio.send_civ = AsyncMock()
    return radio


def _radio_without_civ() -> SimpleNamespace:
    profile = resolve_radio_profile(model="FTX-1")
    return SimpleNamespace(
        profile=profile,
        model=profile.model,
        capabilities=set(profile.capabilities),
    )


async def _post_json(
    srv: WebServer,
    path: str,
    payload: dict,
    *,
    headers: dict[str, str] | None = None,
) -> _FakeWriter:
    body = json.dumps(payload).encode()
    writer = _FakeWriter()
    request_headers = {"content-length": str(len(body))}
    if headers:
        request_headers.update(headers)
    await srv._handle_http(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        "POST",
        path,
        headers=request_headers,
        reader=_reader_for(body),
    )
    return writer


async def _complete_ordered_commands(
    queue: CommandQueue,
    count: int,
    captured: list[object],
) -> None:
    while len(captured) < count:
        await queue.wait(timeout=1.0)
        for entry in queue.drain_entries():
            captured.append(entry.command)
            if entry.future is not None and not entry.future.done():
                entry.future.set_result(None)


async def _complete_ordered_entries(
    queue: CommandQueue,
    count: int,
    captured: list[CommandQueueEntry],
) -> None:
    while len(captured) < count:
        await queue.wait(timeout=1.0)
        for entry in queue.drain_entries():
            captured.append(entry)
            if entry.future is not None and not entry.future.done():
                entry.future.set_result(None)


async def _fail_ordered_commands(
    queue: CommandQueue,
    count: int,
    captured: list[object],
    error: BaseException,
) -> None:
    """Drain ordered entries the way the poller settles a failed command.

    Fails the entry's command lifecycle, then settles its future with the
    same error — the two signals the real ``RadioPoller`` produces after
    executing the command against the radio.
    """
    while len(captured) < count:
        await queue.wait(timeout=1.0)
        for entry in queue.drain_entries():
            captured.append(entry.command)
            if entry.command_service is not None and entry.command_id is not None:
                entry.command_service.fail_command(
                    entry.command_id,
                    message=str(error),
                    source=entry.source,
                    session_id=entry.session_id,
                )
            if entry.future is not None and not entry.future.done():
                entry.future.set_exception(error)


@pytest.mark.asyncio
async def test_http_command_enqueues_single_structured_command() -> None:
    srv = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0))
    captured: list[object] = []
    consumer = asyncio.create_task(
        _complete_ordered_commands(srv.command_queue, 1, captured)
    )
    try:
        writer = await _post_json(
            srv,
            "/api/v1/commands",
            {"id": "single-1", "name": "set_freq", "params": {"freq": 144_030_000}},
        )
    finally:
        await asyncio.wait_for(consumer, timeout=5.0)

    assert writer.response_status == 200
    assert writer.response_body == {
        "id": "single-1",
        "ok": True,
        "name": "set_freq",
        "result": {"freq": 144_030_000, "receiver": 0},
    }
    assert captured == [SetFreq(144_030_000, receiver=0)]


@pytest.mark.asyncio
async def test_http_single_set_freq_nak_reports_409_radio_nak() -> None:
    """POST /api/v1/commands answers a refused write with the execution outcome.

    The stand-in drains the ordered entry the way the poller does — failing
    the command lifecycle, then settling the future with the NAK — so the
    reply below observes the execution outcome, not the enqueue ack.
    """
    srv = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0))
    captured: list[object] = []
    consumer = asyncio.create_task(
        _fail_ordered_commands(
            srv.command_queue,
            1,
            captured,
            CommandRejectedError("radio returned NAK"),
        )
    )
    try:
        writer = await _post_json(
            srv,
            "/api/v1/commands",
            {"id": "single-nak", "name": "set_freq", "params": {"freq": 7_074_000}},
        )
    finally:
        await asyncio.wait_for(consumer, timeout=5.0)

    assert writer.response_status == 409
    assert writer.response_body["ok"] is False
    assert writer.response_body["error"] == "radio_nak"
    assert captured == [SetFreq(7_074_000, receiver=0)]
    failed = [
        event
        for event in srv.command_service.lifecycle_events()
        if event.state == "failed"
    ]
    assert failed and failed[-1].source == "http"


@pytest.mark.asyncio
async def test_http_single_set_freq_timeout_reports_504(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST /api/v1/commands answers 504 when the poller never settles the command.

    No consumer drains the queue: the step still runs through put_ordered
    plus the bounded wait, with only the bound shortened, so the 504 below
    comes from the real timeout path, not a mocked wait.
    """
    monkeypatch.setattr(web_server, "_COMMAND_BATCH_STEP_TIMEOUT", 0.05)
    srv = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands",
        {"id": "single-timeout", "name": "set_freq", "params": {"freq": 7_074_000}},
    )

    assert writer.response_status == 504
    assert writer.response_body["ok"] is False
    assert writer.response_body["error"] == "command_timeout"
    # The bounded wait cancels the ordered future on timeout, and the
    # queue's cancellation hook drops the entry — the timed-out command
    # does not linger for a later drain, exactly as in the batch path.
    assert srv.command_queue.drain() == []
    timed_out = [
        event
        for event in srv.command_service.lifecycle_events()
        if event.state == "timed_out"
    ]
    assert timed_out and timed_out[-1].source == "http"


def _expected_enqueue_path_body(
    radio: object, name: str, params: dict[str, object], command_id: str
) -> dict[str, object]:
    """Derive main's enqueue-path body from the existing enrichment helpers.

    The oracle for the single-command reply: what
    ``ControlHandler._enqueue_command`` returned on main for the same request
    (executor details with the ``admitted_level`` enrichment), computed here
    from ``descriptor.result`` plus ``_with_admitted_level`` — never by
    copying the new output.
    """
    from rigplane.core.command_dispatch import (
        command_descriptor,
        prepare_command_intent,
    )
    from rigplane.web.handlers.control import (
        _consume_normalized_level_unit,
        _with_admitted_level,
    )

    normalized = _consume_normalized_level_unit(name, dict(params))
    intent = prepare_command_intent(
        radio,  # type: ignore[arg-type]
        name,
        normalized,
        source="http",
        command_id=command_id,
    )
    descriptor = command_descriptor(name)
    assert descriptor is not None
    return _with_admitted_level(intent, descriptor.result(intent))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "params"),
    [
        ("set_af_level", {"level": 0.5, "receiver": 0}),
        ("set_rf_gain", {"level": 128, "receiver": 0}),
        ("set_squelch", {"level": 128, "receiver": 0}),
    ],
)
async def test_http_single_descriptor_body_matches_enqueue_path(
    name: str, params: dict[str, object]
) -> None:
    """POST /api/v1/commands answers a descriptor command with main's body.

    The single path must return the executor's details (the
    ``admitted_level`` enrichment included), not the bare descriptor ack
    result the batch keeps. The set_af_level row additionally pins main's
    literal IC-9700 body.
    """
    radio = _radio()
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    expected = _expected_enqueue_path_body(radio, name, params, "single-descriptor")
    if name == "set_af_level":
        assert expected == {
            "level": 128,
            "receiver": 0,
            "admitted_level": 0.5019607843137255,
        }

    captured: list[object] = []
    consumer = asyncio.create_task(
        _complete_ordered_commands(srv.command_queue, 1, captured)
    )
    try:
        writer = await _post_json(
            srv,
            "/api/v1/commands",
            {"id": "single-descriptor", "name": name, "params": params},
        )
    finally:
        await asyncio.wait_for(consumer, timeout=5.0)

    assert writer.response_status == 200
    assert writer.response_body == {
        "id": "single-descriptor",
        "ok": True,
        "name": name,
        "result": expected,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("model", ["IC-7300", "IC-7300MK2", "IC-705"])
async def test_http_single_set_vfo_freq_reports_readback_body(model: str) -> None:
    """POST /api/v1/commands set_vfo_freq waits for the write and readback.

    The command already awaits its own completion on the enqueue path, so the
    single command keeps that path instead of the batch proxy collector
    (which cannot capture the connection generation): bound generation, fresh
    active slot A, write slot B answers 200 with main's readback body and
    executes SetVfoFreq.
    """
    from rigplane.commands import parse_civ_frame
    from rigplane.core.state_pipeline_contracts import (
        FieldPath,
        Observation,
        SourceMetadata,
    )
    from rigplane.runtime._dual_rx_runtime import DualRxRuntimeMixin
    from rigplane.web.radio_poller import RadioPoller, SetVfoFreq

    profile = resolve_radio_profile(model=model)
    reply_prefix = bytes((0xFE, 0xFE, 0xE0, profile.civ_addr))
    radio = SimpleNamespace(
        profile=profile,
        _profile=profile,
        model=profile.model,
        capabilities=set(),
        _civ_epoch=1,
        send_civ=AsyncMock(
            side_effect=[
                parse_civ_frame(reply_prefix + bytes.fromhex("FB FD")),
                parse_civ_frame(
                    reply_prefix + bytes.fromhex("25 01 00 40 07 14 00 FD")
                ),
            ]
        ),
        set_freq=AsyncMock(),
        set_vfo=AsyncMock(),
        set_vfo_slot=AsyncMock(),
        select_receiver=AsyncMock(),
        swap_vfo_ab=AsyncMock(),
    )
    radio.write_relative_vfo_frequency = (
        DualRxRuntimeMixin.write_relative_vfo_frequency.__get__(radio)
    )
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))  # type: ignore[arg-type]
    store = srv.command_state_store
    store.apply(
        Observation(
            path=FieldPath.active_slot("0"),
            value="A",
            source=SourceMetadata(source="command_response", provider="vfo_binding"),
            timestamp_monotonic=time.monotonic(),
            max_age=5,
            provider_generation=store.provider_generation,
        )
    )
    generation = store.provider_generation
    poller = RadioPoller(radio, srv.command_queue, state_store=store)  # type: ignore[arg-type]
    for path, value in (
        (FieldPath.active("0", "freq_mode", "freq_hz"), 7_074_000),
        (FieldPath.active("0", "freq_mode", "mode"), "LSB"),
        (FieldPath.unselected("0", "freq_mode", "mode"), "USB"),
        (FieldPath.unselected("0", "freq_mode", "filter_num"), 1),
        (FieldPath.unselected("0", "freq_mode", "data_mode"), 0),
    ):
        store.apply(
            Observation(
                path=path,
                value=value,
                source=SourceMetadata(source="poll_response", provider="icom_civ"),
                timestamp_monotonic=time.monotonic(),
                provider_generation=generation,
            )
        )
    params = {
        "freq": 14_074_000,
        "receiver": 0,
        "slot": "B",
        "expected_active_slot": "A",
        "provider_generation": generation,
    }
    request = asyncio.create_task(
        _post_json(
            srv,
            "/api/v1/commands",
            {"id": "single-vfo", "name": "set_vfo_freq", "params": params},
        )
    )
    await srv.command_queue.wait(timeout=5.0)
    entries = srv.command_queue.drain_entries()
    assert len(entries) == 1
    assert isinstance(entries[0].command, SetVfoFreq)
    await poller._execute_queued_entry(entries[0])  # noqa: SLF001
    writer = await asyncio.wait_for(request, timeout=10.0)

    assert radio.send_civ.await_args_list == [
        call(0x25, None, data=bytes.fromhex("01 00 40 07 14 00"), wait_response=True),
        call(0x25, None, data=b"\x01", wait_response=True),
    ]
    radio.set_freq.assert_not_awaited()
    radio.set_vfo.assert_not_awaited()
    radio.set_vfo_slot.assert_not_awaited()
    radio.select_receiver.assert_not_awaited()
    radio.swap_vfo_ab.assert_not_awaited()
    assert writer.response_status == 200
    assert writer.response_body == {
        "id": "single-vfo",
        "ok": True,
        "name": "set_vfo_freq",
        "result": {
            "freq": 14_074_000,
            "receiver": 0,
            "slot": "B",
            "expected_active_slot": "A",
            "provider_generation": generation,
        },
    }

    state_writer = _FakeWriter()
    await srv._handle_http(  # noqa: SLF001
        state_writer,  # type: ignore[arg-type]
        "GET",
        "/api/v1/state",
        headers={},
    )
    assert state_writer.response_status == 200
    state = state_writer.response_body
    assert state["main"]["unselectedVfo"]["freqHz"] == 14_074_000
    assert state["main"]["activeSlot"] == "A"
    assert state["main"]["freqHz"] == 7_074_000


@pytest.mark.asyncio
async def test_http_single_unexpected_poller_error_keeps_envelope() -> None:
    """A poller failure that is neither CommandError nor a timeout keeps the envelope.

    The poller settles the ordered future the way a lost radio link does
    during an identity hold (ConnectionError) — the single command answers
    500 command_failed with {ok, error, message}, not the generic routing 500.
    """
    srv = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0))
    captured: list[object] = []
    consumer = asyncio.create_task(
        _fail_ordered_commands(
            srv.command_queue, 1, captured, ConnectionError("radio link lost")
        )
    )
    try:
        writer = await _post_json(
            srv,
            "/api/v1/commands",
            {"id": "single-lost", "name": "set_freq", "params": {"freq": 7_074_000}},
        )
    finally:
        await asyncio.wait_for(consumer, timeout=5.0)

    assert writer.response_status == 500
    assert writer.response_body["ok"] is False
    assert writer.response_body["error"] == "command_failed"
    assert writer.response_body["message"] == "radio link lost"


@pytest.mark.asyncio
async def test_http_command_enqueues_raw_civ_command() -> None:
    srv = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0))
    captured: list[object] = []
    consumer = asyncio.create_task(
        _complete_ordered_commands(srv.command_queue, 1, captured)
    )
    try:
        writer = await _post_json(
            srv,
            "/api/v1/commands",
            {
                "id": "display-type-b",
                "name": "send_civ",
                "params": {"command": 0x1A, "sub": 0x05, "data": "015301"},
            },
        )
    finally:
        await asyncio.wait_for(consumer, timeout=5.0)

    assert writer.response_status == 200
    assert writer.response_body == {
        "id": "display-type-b",
        "ok": True,
        "name": "send_civ",
        "result": {
            "command": 0x1A,
            "sub": 0x05,
            "data": "015301",
            "wait_response": False,
        },
    }
    assert captured == [SendCiv(command=0x1A, sub=0x05, data=b"\x01\x53\x01")]


@pytest.mark.asyncio
async def test_http_command_rejects_invalid_raw_civ_hex() -> None:
    srv = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands",
        {
            "name": "send_civ",
            "params": {"command": 0x1A, "sub": 0x05, "data": "01530"},
        },
    )

    assert writer.response_status == 400
    assert writer.response_body["error"] == "invalid_request"
    assert "data must be an even-length hex string" in writer.response_body["message"]
    assert srv.command_queue.drain() == []


@pytest.mark.asyncio
async def test_http_command_rejects_raw_civ_hex_with_spaces() -> None:
    srv = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands",
        {
            "name": "send_civ",
            "params": {"command": 0x1A, "sub": 0x05, "data": "01 53 01"},
        },
    )

    assert writer.response_status == 400
    assert writer.response_body["error"] == "invalid_request"
    assert "data must be a compact hex string" in writer.response_body["message"]
    assert srv.command_queue.drain() == []


@pytest.mark.asyncio
async def test_http_command_rejects_raw_civ_wait_response() -> None:
    srv = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands",
        {
            "name": "send_civ",
            "params": {
                "command": 0x1A,
                "sub": 0x05,
                "data": "015301",
                "wait_response": True,
            },
        },
    )

    assert writer.response_status == 400
    assert writer.response_body["error"] == "invalid_request"
    assert "wait_response is not supported" in writer.response_body["message"]
    assert srv.command_queue.drain() == []


@pytest.mark.asyncio
async def test_http_raw_civ_transaction_ack_success() -> None:
    radio = _radio()
    frame = SimpleNamespace(
        command=0xFB,
        sub=None,
        data=b"",
    )
    radio.send_civ_transaction = AsyncMock(
        return_value=SimpleNamespace(
            status="ack",
            frame=frame,
            frame_bytes=bytes.fromhex("fefee0a2fbfd"),
        )
    )
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/civ/transaction",
        {
            "id": "display-type-b",
            "command": 0x1A,
            "sub": 0x05,
            "data": "015301",
            "expect": "ack",
            "timeout_ms": 1000,
        },
    )

    assert writer.response_status == 200
    assert writer.response_body == {
        "id": "display-type-b",
        "ok": True,
        "status": "ack",
        "result": {
            "frame": "FEFEE0A2FBFD",
            "command": 0xFB,
            "sub": None,
            "data": "",
        },
    }
    radio.send_civ_transaction.assert_awaited_once_with(
        0x1A,
        sub=0x05,
        data=b"\x01\x53\x01",
        expect="ack",
        timeout=1.0,
    )


@pytest.mark.asyncio
async def test_http_raw_civ_transaction_nak_returns_ok_false() -> None:
    radio = _radio()
    frame = SimpleNamespace(command=0xFA, sub=None, data=b"")
    radio.send_civ_transaction = AsyncMock(
        return_value=SimpleNamespace(
            status="nak",
            frame=frame,
            frame_bytes=bytes.fromhex("fefee0a2fafd"),
        )
    )
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/civ/transaction",
        {"command": 0x1A, "data": "015301", "expect": "ack"},
    )

    assert writer.response_status == 200
    assert writer.response_body["ok"] is False
    assert writer.response_body["status"] == "nak"
    assert writer.response_body["error"] == "radio_nak"


@pytest.mark.asyncio
async def test_http_raw_civ_transaction_data_nak_returns_ok_false() -> None:
    radio = _radio()
    frame = SimpleNamespace(command=0xFA, sub=None, data=b"")
    radio.send_civ_transaction = AsyncMock(
        return_value=SimpleNamespace(
            status="nak",
            frame=frame,
            frame_bytes=bytes.fromhex("fefee0a2fafd"),
        )
    )
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/civ/transaction",
        {"command": 0x03, "expect": "data"},
    )

    assert writer.response_status == 200
    assert writer.response_body["ok"] is False
    assert writer.response_body["status"] == "nak"
    assert writer.response_body["error"] == "radio_nak"
    radio.send_civ_transaction.assert_awaited_once_with(
        0x03,
        sub=None,
        data=b"",
        expect="data",
        timeout=None,
    )


@pytest.mark.asyncio
async def test_http_raw_civ_transaction_expect_none_returns_sent() -> None:
    radio = _radio()
    radio.send_civ_transaction = AsyncMock(
        return_value=SimpleNamespace(
            status="sent",
            frame=None,
            frame_bytes=None,
        )
    )
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/civ/transaction",
        {"command": 0x1A, "sub": 0x05, "data": "015301", "expect": "none"},
    )

    assert writer.response_status == 200
    assert writer.response_body == {
        "ok": True,
        "status": "sent",
        "result": {"frame": None, "command": None, "sub": None, "data": None},
    }
    radio.send_civ_transaction.assert_awaited_once_with(
        0x1A,
        sub=0x05,
        data=b"\x01\x53\x01",
        expect="none",
        timeout=None,
    )


@pytest.mark.asyncio
async def test_http_raw_civ_transaction_rejects_invalid_expect() -> None:
    radio = _radio()
    radio.send_civ_transaction = AsyncMock()
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/civ/transaction",
        {"command": 0x1A, "data": "015301", "expect": "auto"},
    )

    assert writer.response_status == 400
    assert writer.response_body["error"] == "invalid_request"
    assert "expect must be one of" in writer.response_body["message"]
    radio.send_civ_transaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_raw_civ_transaction_rejects_nonpositive_timeout() -> None:
    radio = _radio()
    radio.send_civ_transaction = AsyncMock()
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/civ/transaction",
        {"command": 0x1A, "data": "015301", "expect": "ack", "timeout_ms": 0},
    )

    assert writer.response_status == 400
    assert writer.response_body["error"] == "invalid_request"
    assert "timeout_ms must be positive" in writer.response_body["message"]
    radio.send_civ_transaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_raw_civ_transaction_rejects_read_only() -> None:
    radio = _radio()
    radio.send_civ_transaction = AsyncMock()
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0, read_only=True))

    writer = await _post_json(
        srv,
        "/api/v1/civ/transaction",
        {"command": 0x1A, "data": "015301", "expect": "ack"},
    )

    assert writer.response_status == 403
    assert writer.response_body["error"] == "read_only"
    radio.send_civ_transaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_raw_civ_transaction_without_auth_keeps_read_only_guard() -> None:
    radio = _radio()
    radio.send_civ_transaction = AsyncMock()
    srv = WebServer(
        radio,
        WebConfig(host="127.0.0.1", port=0, read_only=True),
    )

    writer = await _post_json(
        srv,
        "/api/v1/civ/transaction",
        {"command": 0x1A, "data": "015301", "expect": "ack"},
    )

    assert writer.response_status == 403
    assert writer.response_body["error"] == "read_only"
    radio.send_civ_transaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_raw_civ_transaction_rejects_missing_radio() -> None:
    srv = WebServer(None, WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/civ/transaction",
        {"command": 0x1A, "data": "015301", "expect": "ack"},
    )

    assert writer.response_status == 503
    assert writer.response_body == {
        "error": "no_radio",
        "message": "No radio configured",
    }


@pytest.mark.asyncio
async def test_http_raw_civ_transaction_rejects_unsupported_backend() -> None:
    srv = WebServer(_radio_without_civ(), WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/civ/transaction",
        {"command": 0x1A, "data": "015301", "expect": "ack"},
    )

    assert writer.response_status == 409
    assert writer.response_body["error"] == "unsupported_command"


@pytest.mark.asyncio
async def test_http_raw_civ_transaction_owner_conflict_maps_to_409() -> None:
    radio = _radio()
    radio.send_civ_transaction = AsyncMock(
        side_effect=RuntimeError("CI-V stream is already owned by external")
    )
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/civ/transaction",
        {"command": 0x03, "expect": "data", "timeout_ms": 10},
    )

    assert writer.response_status == 409
    assert writer.response_body == {
        "error": "civ_owner_conflict",
        "message": "CI-V stream is already owned by external",
    }
    radio.send_civ_transaction.assert_awaited_once_with(
        0x03,
        sub=None,
        data=b"",
        expect="data",
        timeout=0.01,
    )


@pytest.mark.asyncio
async def test_http_raw_civ_transaction_timeout_maps_to_504() -> None:
    radio = _radio()
    radio.send_civ_transaction = AsyncMock(side_effect=TimeoutError("timed out"))
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/civ/transaction",
        {"command": 0x03, "expect": "data", "timeout_ms": 10},
    )

    assert writer.response_status == 504
    assert writer.response_body["error"] == "transaction_timeout"


@pytest.mark.asyncio
async def test_http_command_rejects_raw_civ_when_backend_does_not_support_it() -> None:
    srv = WebServer(_radio_without_civ(), WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands",
        {"name": "send_civ", "params": {"command": 0x1A, "data": "015301"}},
    )

    assert writer.response_status == 409
    assert writer.response_body["error"] == "unsupported_command"
    assert "does not support send_civ" in writer.response_body["message"]
    assert srv.command_queue.drain() == []


class _AuthorityTouchProbe:
    """Managed TX authority fake that records every attribute reached for."""

    def __init__(self) -> None:
        self.touches: list[str] = []

    def __bool__(self) -> bool:
        raise AssertionError("managed references must be tested with is None")

    def __getattr__(self, name: str) -> object:
        if name.startswith("__"):
            raise AttributeError(name)
        self.touches.append(name)
        raise AssertionError(f"managed TX authority was consulted: {name}")


def _managed_server(radio: MagicMock) -> tuple[WebServer, _AuthorityTouchProbe]:
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    probe = _AuthorityTouchProbe()
    srv._production_managed_tx_port = SimpleNamespace(authority=probe)  # noqa: SLF001
    return srv, probe


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"command": 0x1C, "sub": 0x00, "data": "01", "expect": "none"},
        {"command": 0x1C, "sub": 0x00, "data": "00", "expect": "none"},
        {"command": 0x1C, "sub": 0x01, "data": "01", "expect": "none"},
        {"command": 0x1C, "sub": 0x01, "data": "02", "expect": "none"},
        {"command": 0x29, "data": "001C0001", "expect": "none"},
        {"command": 0x29, "data": "00", "expect": "none"},
        {"command": 0x29, "sub": 0x00, "data": "1C0001", "expect": "none"},
        {"command": 0x28, "sub": 0x00, "data": "01", "expect": "none"},
        {"command": 0x28, "sub": 0x00, "data": "00", "expect": "none"},
        {"command": 0x29, "sub": 0x00, "data": "280001", "expect": "none"},
        {"command": 0x03, "data": "FDFEFE94E01C0001", "expect": "none"},
        {"command": 0x03, "data": "FCFEFE94E01C0001", "expect": "none"},
        {"command": 0x03, "data": "FE1C0001", "expect": "none"},
        {"command": 0x29, "expect": "none"},
    ],
    ids=[
        "ptt-on",
        "ptt-off",
        "tuner-engage",
        "tuner-tune",
        "cmd29-ptt-on",
        "cmd29-truncated",
        "cmd29-sub-form-ptt-on",
        "voice-memory-tx",
        "voice-memory-stop",
        "cmd29-voice-memory-tx",
        "smuggled-frame-in-data",
        "fc-separator-smuggled-frame",
        "leading-extra-fe-in-data",
        "cmd29-empty",
    ],
)
async def test_http_raw_civ_transaction_managed_refuses_tx_family(
    payload: dict,
) -> None:
    """Managed mode refuses TX-family raw CI-V transactions by payload,
    before anything reaches the radio or the managed TX authority.  The
    verdict is computed on the exact frame the transaction would send, so a
    cmd29 wrapper split as (sub, data), a frame smuggled inside the data
    field past an FD, FC, or stray FE delimiter, and a cmd29 wrapper shorter
    than receiver plus inner command are refused exactly as their wire form
    is."""
    radio = _radio()
    radio.send_civ_transaction = AsyncMock()
    srv, probe = _managed_server(radio)

    writer = await _post_json(srv, "/api/v1/civ/transaction", payload)

    assert writer.response_status == 403
    assert writer.response_body["error"] == "managed_tx_refused"
    radio.send_civ_transaction.assert_not_awaited()
    assert probe.touches == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "expected_command", "expected_sub"),
    [
        ({"command": 0x03, "expect": "none"}, 0x03, None),
        ({"command": 0x1C, "sub": 0x00, "expect": "none"}, 0x1C, 0x00),
    ],
    ids=["freq-read", "ptt-read"],
)
async def test_http_raw_civ_transaction_managed_passes_reads(
    payload: dict, expected_command: int, expected_sub: int | None
) -> None:
    """Read-only raw transactions (no data payload) pass in managed mode."""
    radio = _radio()
    radio.send_civ_transaction = AsyncMock(
        return_value=SimpleNamespace(status="sent", frame=None, frame_bytes=None)
    )
    srv, probe = _managed_server(radio)

    writer = await _post_json(srv, "/api/v1/civ/transaction", payload)

    assert writer.response_status == 200
    radio.send_civ_transaction.assert_awaited_once_with(
        expected_command,
        sub=expected_sub,
        data=b"",
        expect="none",
        timeout=None,
    )
    assert probe.touches == []


@pytest.mark.asyncio
async def test_http_raw_civ_transaction_managed_passes_non_tx_write() -> None:
    """A non-TX raw write (set frequency) passes in managed mode and reaches
    the radio's transaction entry point unchanged."""
    radio = _radio()
    radio.send_civ_transaction = AsyncMock(
        return_value=SimpleNamespace(status="sent", frame=None, frame_bytes=None)
    )
    srv, probe = _managed_server(radio)

    writer = await _post_json(
        srv,
        "/api/v1/civ/transaction",
        {"command": 0x05, "data": "0000101400", "expect": "none"},
    )

    assert writer.response_status == 200
    radio.send_civ_transaction.assert_awaited_once_with(
        0x05,
        sub=None,
        data=bytes.fromhex("0000101400"),
        expect="none",
        timeout=None,
    )
    assert probe.touches == []


@pytest.mark.asyncio
async def test_http_command_batch_raw_civ_transaction_managed_refuses_tx_family() -> (
    None
):
    """The batch executor shares the managed refusal with the single endpoint."""
    radio = _radio()
    radio.send_civ_transaction = AsyncMock()
    srv, probe = _managed_server(radio)

    writer = await _post_json(
        srv,
        "/api/v1/commands/batch",
        {
            "steps": [
                {
                    "type": "raw_civ_transaction",
                    "command": 0x1C,
                    "sub": 0x00,
                    "data": "01",
                    "expect": "none",
                }
            ]
        },
    )

    assert writer.response_status == 200
    assert writer.response_body["ok"] is False
    [step] = writer.response_body["results"]
    assert step["ok"] is False
    assert step["status"] == "refused"
    assert step["error"] == "managed_tx_refused"
    radio.send_civ_transaction.assert_not_awaited()
    assert probe.touches == []


@pytest.mark.asyncio
async def test_http_command_managed_refuses_tx_family_send_civ() -> None:
    """Managed mode answers a TX-family named send_civ with 403 and its
    reason code, mirroring the raw_civ_transaction refusal surface, and
    nothing reaches the radio, the command queue, or the managed TX
    authority."""
    radio = _radio()
    srv, probe = _managed_server(radio)

    writer = await _post_json(
        srv,
        "/api/v1/commands",
        {"name": "send_civ", "params": {"command": 0x1C, "sub": 0x00, "data": "01"}},
    )

    assert writer.response_status == 403
    assert writer.response_body["error"] == "managed_tx_refused"
    assert "managed TX authority" in writer.response_body["message"]
    radio.send_civ.assert_not_awaited()
    assert srv.command_queue.drain() == []
    assert probe.touches == []


@pytest.mark.asyncio
async def test_http_command_managed_passes_set_frequency_send_civ() -> None:
    """A managed set-frequency raw frame still flows through the named
    HTTP command route and queues its SendCiv unchanged."""
    radio = _radio()
    srv, probe = _managed_server(radio)
    captured: list[object] = []
    consumer = asyncio.create_task(
        _complete_ordered_commands(srv.command_queue, 1, captured)
    )
    try:
        writer = await _post_json(
            srv,
            "/api/v1/commands",
            {"name": "send_civ", "params": {"command": 0x05, "data": "0000101400"}},
        )
    finally:
        await asyncio.wait_for(consumer, timeout=5.0)

    assert writer.response_status == 200
    assert writer.response_body["ok"] is True
    assert captured == [
        SendCiv(command=0x05, sub=None, data=bytes.fromhex("0000101400"))
    ]
    radio.send_civ.assert_not_awaited()
    assert probe.touches == []


@pytest.mark.asyncio
async def test_http_command_batch_managed_refuses_tx_family_send_civ() -> None:
    """The named-command batch reports a refused step carrying the reason
    code instead of failing the whole request with an internal error."""
    radio = _radio()
    srv, probe = _managed_server(radio)

    writer = await _post_json(
        srv,
        "/api/v1/commands/batch",
        {
            "steps": [
                {
                    "name": "send_civ",
                    "params": {"command": 0x1C, "sub": 0x00, "data": "01"},
                }
            ]
        },
    )

    assert writer.response_status == 200
    assert writer.response_body["ok"] is False
    [step] = writer.response_body["results"]
    assert step["ok"] is False
    assert step["status"] == "refused"
    assert step["error"] == "managed_tx_refused"
    radio.send_civ.assert_not_awaited()
    assert srv.command_queue.drain() == []
    assert probe.touches == []


def _unmanaged_server(radio: MagicMock) -> WebServer:
    return WebServer(radio, WebConfig(host="127.0.0.1", port=0))


_PTT_PATH = FieldPath.global_("tx_state", "ptt")
_TEST_SOURCE = SourceMetadata(source="test", provider="tests")


def _observe_ptt(srv: WebServer, value: bool, *, age_seconds: float = 0.0) -> None:
    srv.command_state_store.apply_current(
        Observation(
            path=_PTT_PATH,
            value=value,
            source=_TEST_SOURCE,
            timestamp_monotonic=time.monotonic() - age_seconds,
            max_age=60.0,
        )
    )


@pytest.mark.asyncio
async def test_http_raw_civ_transaction_unmanaged_refuses_ptt_on() -> None:
    """The unmanaged HTTP raw path refuses PTT-on when RF state
    is UNKNOWN (no PTT observation at all) instead of keying the rig with
    no interlock at all."""
    radio = _radio()
    radio.send_civ_transaction = AsyncMock(
        return_value=SimpleNamespace(status="sent", frame=None, frame_bytes=None)
    )
    srv = _unmanaged_server(radio)

    writer = await _post_json(
        srv,
        "/api/v1/civ/transaction",
        {"command": 0x1C, "sub": 0x00, "data": "01", "expect": "none"},
    )

    assert writer.response_status == 403, (
        "MOR-3364: unmanaged HTTP raw PTT-on still reaches the radio"
    )
    assert writer.response_body["error"] == "rf_state_unknown"
    radio.send_civ_transaction.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "ptt_value", "age_seconds", "expected_error"),
    [
        (
            {"command": 0x1C, "sub": 0x00, "data": "01", "expect": "none"},
            True,
            0.0,
            "radio_transmitting",
        ),
        (
            {"command": 0x1C, "sub": 0x00, "data": "01", "expect": "none"},
            False,
            120.0,
            "rf_state_unknown",
        ),
        (
            {"command": 0x29, "sub": 0x00, "data": "1C0001", "expect": "none"},
            None,
            0.0,
            "rf_state_unknown",
        ),
        (
            {"command": 0x1C, "sub": 0x01, "data": "01", "expect": "none"},
            None,
            0.0,
            "rf_state_unknown",
        ),
        (
            {"command": 0x28, "sub": 0x00, "data": "01", "expect": "none"},
            True,
            0.0,
            "radio_transmitting",
        ),
        (
            {"command": 0x29, "sub": 0x00, "data": "280001", "expect": "none"},
            True,
            0.0,
            "radio_transmitting",
        ),
    ],
    ids=[
        "ptt-on-observed-tx",
        "ptt-on-stale-rx",
        "cmd29-ptt-on-unknown",
        "tuner-engage-unknown",
        "voice-memory-tx-observed-tx",
        "cmd29-voice-memory-tx-observed-tx",
    ],
)
async def test_http_raw_civ_transaction_unmanaged_refuses_tx_family_without_fresh_rx(
    payload: dict,
    ptt_value: bool | None,
    age_seconds: float,
    expected_error: str,
) -> None:
    """A TX-family raw frame that is not an exact de-key is refused
    unless the observed PTT resolves to fresh-known RX, matching what
    unmanaged rigctld raw ``w`` does to TX-family frames
    (``_RigctldCommandExecutor.execute``): observed TX refuses, and so
    does a stale or absent observation."""
    radio = _radio()
    radio.send_civ_transaction = AsyncMock(
        return_value=SimpleNamespace(status="sent", frame=None, frame_bytes=None)
    )
    srv = _unmanaged_server(radio)
    if ptt_value is not None:
        _observe_ptt(srv, ptt_value, age_seconds=age_seconds)

    writer = await _post_json(srv, "/api/v1/civ/transaction", payload)

    assert writer.response_status == 403
    assert writer.response_body["error"] == expected_error
    radio.send_civ_transaction.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "ptt_value"),
    [
        (
            {"command": 0x1C, "sub": 0x00, "data": "00", "expect": "none"},
            True,
        ),
        (
            {"command": 0x1C, "sub": 0x00, "data": "00", "expect": "none"},
            None,
        ),
        (
            {"command": 0x29, "sub": 0x00, "data": "1C0000", "expect": "none"},
            True,
        ),
        (
            {"command": 0x1C, "sub": 0x01, "data": "00", "expect": "none"},
            True,
        ),
        (
            {"command": 0x28, "sub": 0x00, "data": "00", "expect": "none"},
            True,
        ),
        (
            {"command": 0x29, "sub": 0x00, "data": "280000", "expect": "none"},
            True,
        ),
    ],
    ids=[
        "ptt-off-observed-tx",
        "ptt-off-unknown",
        "cmd29-ptt-off-observed-tx",
        "tuner-off-observed-tx",
        "voice-memory-stop-observed-tx",
        "cmd29-voice-memory-stop-observed-tx",
    ],
)
async def test_http_raw_civ_transaction_unmanaged_passes_exact_dekey(
    payload: dict,
    ptt_value: bool | None,
) -> None:
    """An exact raw de-key frame — PTT off ``1C 00 00`` or tuner off
    ``1C 01 00``, bare or inside a cmd29 prefix — always reaches the radio
    on the unmanaged HTTP raw seat, observed TX or unknown RF state
    included, exactly as the typed PTT-off command always passes: a de-key
    must never get harder to execute."""
    radio = _radio()
    radio.send_civ_transaction = AsyncMock(
        return_value=SimpleNamespace(status="sent", frame=None, frame_bytes=None)
    )
    srv = _unmanaged_server(radio)
    if ptt_value is not None:
        _observe_ptt(srv, ptt_value)

    writer = await _post_json(srv, "/api/v1/civ/transaction", payload)

    assert writer.response_status == 200, (
        "MOR-3364: a raw de-key must not be refused by the RF-state check"
    )
    radio.send_civ_transaction.assert_awaited_once_with(
        payload["command"],
        sub=payload["sub"],
        data=bytes.fromhex(payload["data"]),
        expect="none",
        timeout=None,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {"command": 0x03, "data": "FDFEFE94E01C0001", "expect": "none"},
        {"command": 0x03, "data": "FCFEFE94E01C0001", "expect": "none"},
    ],
    ids=["smuggled-frame-in-data", "fc-separator-smuggled-frame"],
)
async def test_http_raw_civ_transaction_unmanaged_refuses_smuggled_while_tx(
    payload: dict,
) -> None:
    """A payload that cannot be parsed as one whole CI-V frame — a PTT-on
    smuggled in the data field past an FD, or an FC collision byte standing
    in for the separator — is refused while TX is observed, exactly as a
    parsed TX-family frame is: the refusal covers the cannot-parse verdict,
    not only an explicit True."""
    radio = _radio()
    radio.send_civ_transaction = AsyncMock(
        return_value=SimpleNamespace(status="sent", frame=None, frame_bytes=None)
    )
    srv = _unmanaged_server(radio)
    _observe_ptt(srv, True)

    writer = await _post_json(srv, "/api/v1/civ/transaction", payload)

    assert writer.response_status == 403
    assert writer.response_body["error"] == "radio_transmitting"
    radio.send_civ_transaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_raw_civ_transaction_unmanaged_passes_ptt_on_with_fresh_rx() -> None:
    """With a fresh-known RX observation the unmanaged HTTP raw
    seat passes a TX-family frame to the radio, exactly as unmanaged
    rigctld raw ``w`` and the typed ``T 1`` do."""
    radio = _radio()
    radio.send_civ_transaction = AsyncMock(
        return_value=SimpleNamespace(status="sent", frame=None, frame_bytes=None)
    )
    srv = _unmanaged_server(radio)
    _observe_ptt(srv, False)

    writer = await _post_json(
        srv,
        "/api/v1/civ/transaction",
        {"command": 0x1C, "sub": 0x00, "data": "01", "expect": "none"},
    )

    assert writer.response_status == 200
    radio.send_civ_transaction.assert_awaited_once_with(
        0x1C,
        sub=0x00,
        data=b"\x01",
        expect="none",
        timeout=None,
    )


@pytest.mark.asyncio
async def test_http_raw_civ_transaction_unmanaged_passes_voice_tx_with_fresh_rx() -> (
    None
):
    """MOR-3374: with a fresh-known RX observation the unmanaged HTTP raw
    seat passes a voice TX memory transmit frame to the radio, exactly as
    it passes a raw PTT-on in the same state."""
    radio = _radio()
    radio.send_civ_transaction = AsyncMock(
        return_value=SimpleNamespace(status="sent", frame=None, frame_bytes=None)
    )
    srv = _unmanaged_server(radio)
    _observe_ptt(srv, False)

    writer = await _post_json(
        srv,
        "/api/v1/civ/transaction",
        {"command": 0x28, "sub": 0x00, "data": "01", "expect": "none"},
    )

    assert writer.response_status == 200
    radio.send_civ_transaction.assert_awaited_once_with(
        0x28,
        sub=0x00,
        data=b"\x01",
        expect="none",
        timeout=None,
    )


@pytest.mark.asyncio
async def test_http_raw_civ_transaction_unmanaged_raw_cw_text_still_passes_while_tx() -> (
    None
):
    """MOR-3374 parity pin: raw CW text ``0x17`` is not TX-family, so it
    keeps passing this seat while TX is observed, exactly as before the
    voice-memory frames joined the family — the typed ``send_cw_text``
    path is not interlocked, so by parity the raw form stays unchanged.
    A later decision to add 0x17 to the family makes this row fail."""
    radio = _radio()
    radio.send_civ_transaction = AsyncMock(
        return_value=SimpleNamespace(status="sent", frame=None, frame_bytes=None)
    )
    srv = _unmanaged_server(radio)
    _observe_ptt(srv, True)

    writer = await _post_json(
        srv,
        "/api/v1/civ/transaction",
        {"command": 0x17, "data": "41", "expect": "none"},
    )

    assert writer.response_status == 200
    radio.send_civ_transaction.assert_awaited_once_with(
        0x17,
        sub=None,
        data=b"\x41",
        expect="none",
        timeout=None,
    )


@pytest.mark.asyncio
async def test_http_raw_civ_transaction_unmanaged_passes_non_tx_while_observed_tx() -> (
    None
):
    """A non-TX frame (set frequency) passes regardless of the observed RF
    state, observed TX included: it is not a TX actuation."""
    radio = _radio()
    radio.send_civ_transaction = AsyncMock(
        return_value=SimpleNamespace(status="sent", frame=None, frame_bytes=None)
    )
    srv = _unmanaged_server(radio)
    _observe_ptt(srv, True)

    writer = await _post_json(
        srv,
        "/api/v1/civ/transaction",
        {"command": 0x05, "data": "0000101400", "expect": "none"},
    )

    assert writer.response_status == 200
    radio.send_civ_transaction.assert_awaited_once_with(
        0x05,
        sub=None,
        data=bytes.fromhex("0000101400"),
        expect="none",
        timeout=None,
    )


@pytest.mark.asyncio
async def test_http_raw_civ_single_commands_preserve_repeated_steps() -> None:
    srv = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0))
    captured: list[object] = []
    consumer = asyncio.create_task(
        _complete_ordered_commands(srv.command_queue, 2, captured)
    )
    try:
        writer1 = await _post_json(
            srv,
            "/api/v1/commands",
            {"name": "send_civ", "params": {"command": 0x1A, "data": "0001"}},
        )
        writer2 = await _post_json(
            srv,
            "/api/v1/commands",
            {"name": "send_civ", "params": {"command": 0x1A, "data": "0002"}},
        )
    finally:
        await asyncio.wait_for(consumer, timeout=5.0)

    assert writer1.response_status == 200
    assert writer2.response_status == 200
    assert captured == [
        SendCiv(command=0x1A, data=b"\x00\x01"),
        SendCiv(command=0x1A, data=b"\x00\x02"),
    ]


@pytest.mark.asyncio
async def test_http_raw_civ_rejected_in_read_only_mode() -> None:
    srv = WebServer(
        _radio(),
        WebConfig(host="127.0.0.1", port=0, read_only=True),
    )

    writer = await _post_json(
        srv,
        "/api/v1/commands",
        {"name": "send_civ", "params": {"command": 0x1A, "data": "015301"}},
    )

    assert writer.response_status == 403
    assert writer.response_body["error"] == "read_only"
    assert srv.command_queue.drain() == []


@pytest.mark.asyncio
async def test_http_command_without_auth_keeps_read_only_guard() -> None:
    srv = WebServer(
        _radio(),
        WebConfig(host="127.0.0.1", port=0, read_only=True),
    )

    writer = await _post_json(
        srv,
        "/api/v1/commands",
        {"name": "ptt", "params": {"state": True}},
    )

    assert writer.response_status == 403
    assert writer.response_body["error"] == "read_only"

    assert srv.command_queue.drain() == []


@pytest.mark.asyncio
async def test_http_command_batch_preserves_exact_order_and_repeated_commands() -> None:
    srv = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0))
    captured: list[object] = []
    consumer = asyncio.create_task(
        _complete_ordered_commands(srv.command_queue, 3, captured)
    )
    try:
        writer = await _post_json(
            srv,
            "/api/v1/commands/batch",
            {
                "id": "profile-vara-fm",
                "steps": [
                    {"name": "set_freq", "params": {"freq": 144_030_000}},
                    {"name": "set_mode", "params": {"mode": "FM"}},
                    {"name": "set_freq", "params": {"freq": 144_031_000}},
                ],
            },
        )
    finally:
        await asyncio.wait_for(consumer, timeout=1.0)

    assert writer.response_status == 200
    assert writer.response_body["id"] == "profile-vara-fm"
    assert writer.response_body["ok"] is True
    assert [r["status"] for r in writer.response_body["results"]] == [
        "executed",
        "executed",
        "executed",
    ]
    assert captured == [
        SetFreq(144_030_000, receiver=0),
        SetMode("FM", receiver=0),
        SetFreq(144_031_000, receiver=0),
    ]


@pytest.mark.asyncio
async def test_http_command_batch_preserves_raw_civ_step_order() -> None:
    srv = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0))
    captured: list[object] = []
    consumer = asyncio.create_task(
        _complete_ordered_commands(srv.command_queue, 3, captured)
    )
    try:
        writer = await _post_json(
            srv,
            "/api/v1/commands/batch",
            {
                "id": "profile-display",
                "steps": [
                    {"name": "set_freq", "params": {"freq": 144_030_000}},
                    {
                        "name": "send_civ",
                        "params": {"command": 0x1A, "sub": 0x05, "data": "015301"},
                    },
                    {"name": "set_mode", "params": {"mode": "FM"}},
                ],
            },
        )
    finally:
        await asyncio.wait_for(consumer, timeout=1.0)

    assert writer.response_status == 200
    assert writer.response_body["id"] == "profile-display"
    assert writer.response_body["ok"] is True
    assert [r["status"] for r in writer.response_body["results"]] == [
        "executed",
        "executed",
        "executed",
    ]
    assert captured == [
        SetFreq(144_030_000, receiver=0),
        SendCiv(command=0x1A, sub=0x05, data=b"\x01\x53\x01"),
        SetMode("FM", receiver=0),
    ]


@pytest.mark.asyncio
async def test_http_command_batch_mixes_command_transaction_command_in_order() -> None:
    radio = _radio()
    frame = SimpleNamespace(
        command=0x1A,
        sub=0x05,
        data=b"\x01\x53",
    )
    events: list[object] = []

    async def transaction(
        command: int,
        *,
        sub: int | None = None,
        data: bytes | None = None,
        expect: str = "data",
        timeout: float | None = None,
    ) -> SimpleNamespace:
        events.append(("transaction", command, sub, data, expect, timeout))
        return SimpleNamespace(
            status="response",
            frame=frame,
            frame_bytes=bytes.fromhex("fefee0981a050153fd"),
        )

    radio.send_civ_transaction = AsyncMock(side_effect=transaction)
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))

    async def complete_commands() -> None:
        while sum(1 for event in events if event[0] == "command") < 2:
            await srv.command_queue.wait(timeout=1.0)
            for entry in srv.command_queue.drain_entries():
                events.append(("command", entry.command))
                if entry.future is not None and not entry.future.done():
                    entry.future.set_result(None)

    consumer = asyncio.create_task(complete_commands())
    try:
        writer = await _post_json(
            srv,
            "/api/v1/commands/batch",
            {
                "id": "mixed-profile",
                "steps": [
                    {"name": "set_freq", "params": {"freq": 144_030_000}},
                    {
                        "type": "raw_civ_transaction",
                        "id": "display-type-query",
                        "command": 0x1A,
                        "sub": 0x05,
                        "data": "0153",
                        "expect": "data",
                        "timeout_ms": 250,
                    },
                    {"name": "set_mode", "params": {"mode": "FM"}},
                ],
            },
        )
    finally:
        await asyncio.wait_for(consumer, timeout=1.0)

    assert writer.response_status == 200
    assert writer.response_body["id"] == "mixed-profile"
    assert writer.response_body["ok"] is True
    assert writer.response_body["results"] == [
        {
            "index": 0,
            "name": "set_freq",
            "ok": True,
            "status": "executed",
            "result": {"freq": 144_030_000, "receiver": 0},
        },
        {
            "index": 1,
            "type": "raw_civ_transaction",
            "id": "display-type-query",
            "ok": True,
            "status": "response",
            "result": {
                "frame": "FEFEE0981A050153FD",
                "command": 0x1A,
                "sub": 0x05,
                "data": "0153",
            },
        },
        {
            "index": 2,
            "name": "set_mode",
            "ok": True,
            "status": "executed",
            "result": {"mode": "FM", "receiver": 0},
        },
    ]
    assert events == [
        ("command", SetFreq(144_030_000, receiver=0)),
        ("transaction", 0x1A, 0x05, b"\x01\x53", "data", 0.25),
        ("command", SetMode("FM", receiver=0)),
    ]


@pytest.mark.asyncio
async def test_http_command_batch_raw_civ_transaction_ack_success() -> None:
    radio = _radio()
    frame = SimpleNamespace(command=0xFB, sub=None, data=b"")
    radio.send_civ_transaction = AsyncMock(
        return_value=SimpleNamespace(
            status="ack",
            frame=frame,
            frame_bytes=bytes.fromhex("fefee0a2fbfd"),
        )
    )
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands/batch",
        {
            "steps": [
                {
                    "type": "raw_civ_transaction",
                    "command": 0x1A,
                    "sub": 0x05,
                    "data": "015301",
                    "expect": "ack",
                }
            ]
        },
    )

    assert writer.response_status == 200
    assert writer.response_body["ok"] is True
    assert writer.response_body["results"] == [
        {
            "index": 0,
            "type": "raw_civ_transaction",
            "ok": True,
            "status": "ack",
            "result": {
                "frame": "FEFEE0A2FBFD",
                "command": 0xFB,
                "sub": None,
                "data": "",
            },
        }
    ]
    radio.send_civ_transaction.assert_awaited_once_with(
        0x1A,
        sub=0x05,
        data=b"\x01\x53\x01",
        expect="ack",
        timeout=10.0,
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("command", True),
        ("command", "26"),
        ("command", 26.9),
        ("sub", True),
        ("sub", "5"),
        ("sub", 5.1),
        ("timeout_ms", "1000"),
        ("timeout_ms", True),
    ],
)
@pytest.mark.asyncio
async def test_http_command_batch_raw_civ_transaction_rejects_non_strict_scalars(
    field: str,
    value: object,
) -> None:
    radio = _radio()
    radio.send_civ_transaction = AsyncMock()
    step: dict[str, object] = {
        "type": "raw_civ_transaction",
        "command": 0x1A,
        "sub": 0x05,
        "expect": "ack",
    }
    step[field] = value
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands/batch",
        {"steps": [step, {"name": "set_freq", "params": {"freq": 144_030_000}}]},
    )

    assert writer.response_status == 200
    assert writer.response_body["ok"] is False
    assert writer.response_body["results"][0]["status"] == "failed_validation"
    assert writer.response_body["results"][0]["error"] == "invalid_request"
    assert writer.response_body["results"][1]["status"] == "skipped"
    radio.send_civ_transaction.assert_not_awaited()
    assert srv.command_queue.drain() == []


@pytest.mark.asyncio
async def test_http_command_batch_reports_unknown_typed_step_type() -> None:
    radio = _radio()
    radio.send_civ_transaction = AsyncMock()
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands/batch",
        {
            "steps": [
                {"type": "bogus", "command": 0x1A, "expect": "ack"},
                {"name": "set_freq", "params": {"freq": 144_030_000}},
            ]
        },
    )

    assert writer.response_status == 200
    assert writer.response_body["ok"] is False
    assert writer.response_body["results"][0]["status"] == "failed_validation"
    assert writer.response_body["results"][0]["error"] == "unknown_step_type"
    assert writer.response_body["results"][1]["status"] == "skipped"
    radio.send_civ_transaction.assert_not_awaited()
    assert srv.command_queue.drain() == []


@pytest.mark.asyncio
async def test_http_command_batch_legacy_command_tolerates_extra_type_field() -> None:
    srv = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0))
    captured: list[object] = []
    consumer = asyncio.create_task(
        _complete_ordered_commands(srv.command_queue, 1, captured)
    )
    try:
        writer = await _post_json(
            srv,
            "/api/v1/commands/batch",
            {
                "steps": [
                    {
                        "type": "ignored_legacy_extra",
                        "name": "set_freq",
                        "params": {"freq": 144_030_000},
                    }
                ]
            },
        )
    finally:
        await asyncio.wait_for(consumer, timeout=1.0)

    assert writer.response_status == 200
    assert writer.response_body["ok"] is True
    assert writer.response_body["results"][0]["status"] == "executed"
    assert captured == [SetFreq(144_030_000, receiver=0)]


@pytest.mark.asyncio
async def test_http_command_batch_raw_civ_transaction_nak_stops_and_skips_later() -> (
    None
):
    radio = _radio()
    frame = SimpleNamespace(command=0xFA, sub=None, data=b"")
    radio.send_civ_transaction = AsyncMock(
        return_value=SimpleNamespace(
            status="nak",
            frame=frame,
            frame_bytes=bytes.fromhex("fefee0a2fafd"),
        )
    )
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands/batch",
        {
            "steps": [
                {
                    "type": "raw_civ_transaction",
                    "command": 0x1A,
                    "expect": "ack",
                },
                {"name": "set_freq", "params": {"freq": 144_030_000}},
            ]
        },
    )

    assert writer.response_status == 200
    assert writer.response_body["ok"] is False
    assert writer.response_body["results"] == [
        {
            "index": 0,
            "type": "raw_civ_transaction",
            "ok": False,
            "status": "nak",
            "error": "radio_nak",
            "message": "radio returned CI-V NAK",
            "result": {
                "frame": "FEFEE0A2FAFD",
                "command": 0xFA,
                "sub": None,
                "data": "",
            },
        },
        {
            "index": 1,
            "name": "set_freq",
            "ok": False,
            "status": "skipped",
            "error": "skipped_after_failure",
            "message": "skipped after earlier batch failure",
        },
    ]
    assert srv.command_queue.drain() == []


@pytest.mark.asyncio
async def test_http_command_batch_transaction_timeout_can_continue() -> None:
    radio = _radio()
    radio.send_civ_transaction = AsyncMock(side_effect=TimeoutError("timed out"))
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    captured: list[object] = []
    consumer = asyncio.create_task(
        _complete_ordered_commands(srv.command_queue, 1, captured)
    )
    try:
        writer = await _post_json(
            srv,
            "/api/v1/commands/batch",
            {
                "continue_on_error": True,
                "steps": [
                    {
                        "type": "raw_civ_transaction",
                        "command": 0x03,
                        "expect": "data",
                        "timeout_ms": 10,
                    },
                    {"name": "set_mode", "params": {"mode": "FM"}},
                ],
            },
        )
    finally:
        await asyncio.wait_for(consumer, timeout=1.0)

    assert writer.response_status == 200
    assert writer.response_body["ok"] is False
    assert [result["status"] for result in writer.response_body["results"]] == [
        "timed_out",
        "executed",
    ]
    assert writer.response_body["results"][0] == {
        "index": 0,
        "type": "raw_civ_transaction",
        "ok": False,
        "status": "timed_out",
        "error": "transaction_timeout",
        "message": "raw CI-V transaction timed out",
    }
    assert captured == [SetMode("FM", receiver=0)]
    radio.send_civ_transaction.assert_awaited_once_with(
        0x03,
        sub=None,
        data=b"",
        expect="data",
        timeout=0.01,
    )


@pytest.mark.asyncio
async def test_http_command_batch_transaction_timeout_stops_and_skips_later() -> None:
    radio = _radio()
    radio.send_civ_transaction = AsyncMock(side_effect=TimeoutError("timed out"))
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands/batch",
        {
            "continue_on_error": False,
            "steps": [
                {
                    "type": "raw_civ_transaction",
                    "command": 0x03,
                    "expect": "data",
                    "timeout_ms": 10,
                },
                {"name": "set_mode", "params": {"mode": "FM"}},
            ],
        },
    )

    assert writer.response_status == 200
    assert writer.response_body["ok"] is False
    assert [result["status"] for result in writer.response_body["results"]] == [
        "timed_out",
        "skipped",
    ]
    assert writer.response_body["results"][0]["error"] == "transaction_timeout"
    assert writer.response_body["results"][1] == {
        "index": 1,
        "name": "set_mode",
        "ok": False,
        "status": "skipped",
        "error": "skipped_after_failure",
        "message": "skipped after earlier batch failure",
    }
    assert srv.command_queue.drain() == []
    radio.send_civ_transaction.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_command_batch_transaction_owner_conflict_stops_batch() -> None:
    radio = _radio()
    radio.send_civ_transaction = AsyncMock(
        side_effect=RuntimeError("CI-V stream is already owned by external")
    )
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands/batch",
        {
            "steps": [
                {"type": "raw_civ_transaction", "command": 0x03, "expect": "data"},
                {"name": "set_freq", "params": {"freq": 144_030_000}},
            ]
        },
    )

    assert writer.response_status == 200
    assert writer.response_body["ok"] is False
    assert writer.response_body["results"][0] == {
        "index": 0,
        "type": "raw_civ_transaction",
        "ok": False,
        "status": "owner_conflict",
        "error": "civ_owner_conflict",
        "message": "CI-V stream is already owned by external",
    }
    assert writer.response_body["results"][1]["status"] == "skipped"
    assert srv.command_queue.drain() == []


@pytest.mark.asyncio
async def test_http_command_batch_transaction_reports_unsupported_backend() -> None:
    srv = WebServer(_radio_without_civ(), WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands/batch",
        {
            "steps": [
                {
                    "type": "raw_civ_transaction",
                    "command": 0x1A,
                    "expect": "ack",
                }
            ]
        },
    )

    assert writer.response_status == 200
    assert writer.response_body["ok"] is False
    assert writer.response_body["results"] == [
        {
            "index": 0,
            "type": "raw_civ_transaction",
            "ok": False,
            "status": "unsupported",
            "error": "unsupported_command",
            "message": "active backend does not support raw CI-V transactions",
        }
    ]


@pytest.mark.asyncio
async def test_http_command_batch_transaction_reports_read_only() -> None:
    radio = _radio()
    radio.send_civ_transaction = AsyncMock()
    srv = WebServer(
        radio,
        WebConfig(host="127.0.0.1", port=0, read_only=True),
    )

    writer = await _post_json(
        srv,
        "/api/v1/commands/batch",
        {
            "steps": [
                {
                    "type": "raw_civ_transaction",
                    "command": 0x1A,
                    "expect": "ack",
                }
            ]
        },
    )

    assert writer.response_status == 200
    assert writer.response_body["ok"] is False
    assert writer.response_body["results"] == [
        {
            "index": 0,
            "type": "raw_civ_transaction",
            "ok": False,
            "status": "read_only",
            "error": "read_only",
            "message": "raw CI-V transactions are disabled in read-only mode",
        }
    ]
    radio.send_civ_transaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_command_batch_transaction_reports_no_radio() -> None:
    srv = WebServer(None, WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands/batch",
        {
            "steps": [
                {
                    "type": "raw_civ_transaction",
                    "command": 0x1A,
                    "expect": "ack",
                }
            ]
        },
    )

    assert writer.response_status == 200
    assert writer.response_body["ok"] is False
    assert writer.response_body["results"] == [
        {
            "index": 0,
            "type": "raw_civ_transaction",
            "ok": False,
            "status": "no_radio",
            "error": "no_radio",
            "message": "No radio configured",
        }
    ]


@pytest.mark.asyncio
async def test_http_command_batch_legacy_no_radio_stays_service_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web_server, "_COMMAND_BATCH_STEP_TIMEOUT", 0.001)
    srv = WebServer(None, WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands/batch",
        {"steps": [{"name": "set_freq", "params": {"freq": 144_030_000}}]},
    )

    assert writer.response_status == 503
    assert writer.response_body == {
        "error": "no_radio",
        "message": "No radio configured",
    }


@pytest.mark.asyncio
async def test_http_command_batch_mixed_legacy_then_transaction_no_radio_stays_service_unavailable() -> (
    None
):
    srv = WebServer(None, WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands/batch",
        {
            "steps": [
                {"name": "set_freq", "params": {"freq": 144_030_000}},
                {
                    "type": "raw_civ_transaction",
                    "command": 0x1A,
                    "expect": "ack",
                },
            ]
        },
    )

    assert writer.response_status == 503
    assert writer.response_body == {
        "error": "no_radio",
        "message": "No radio configured",
    }


@pytest.mark.asyncio
async def test_http_command_batch_legacy_send_civ_stays_fire_and_forget() -> None:
    radio = _radio()
    radio.send_civ_transaction = AsyncMock()
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    captured: list[object] = []
    consumer = asyncio.create_task(
        _complete_ordered_commands(srv.command_queue, 1, captured)
    )
    try:
        writer = await _post_json(
            srv,
            "/api/v1/commands/batch",
            {
                "steps": [
                    {
                        "name": "send_civ",
                        "params": {
                            "command": 0x1A,
                            "sub": 0x05,
                            "data": "015301",
                        },
                    }
                ]
            },
        )
    finally:
        await asyncio.wait_for(consumer, timeout=1.0)

    assert writer.response_status == 200
    assert writer.response_body["ok"] is True
    assert writer.response_body["results"][0]["status"] == "executed"
    assert captured == [
        SendCiv(command=0x1A, sub=0x05, data=b"\x01\x53\x01"),
    ]
    radio.send_civ_transaction.assert_not_awaited()


@pytest.mark.asyncio
async def test_http_command_batch_legacy_send_civ_rejects_wait_response() -> None:
    radio = _radio()
    radio.send_civ_transaction = AsyncMock()
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands/batch",
        {
            "steps": [
                {
                    "name": "send_civ",
                    "params": {
                        "command": 0x1A,
                        "sub": 0x05,
                        "data": "015301",
                        "wait_response": True,
                    },
                },
                {"name": "set_freq", "params": {"freq": 144_030_000}},
            ]
        },
    )

    assert writer.response_status == 200
    assert writer.response_body["ok"] is False
    assert writer.response_body["results"][0]["status"] == "failed_validation"
    assert writer.response_body["results"][0]["error"] in {
        "invalid_request",
        "unsupported_command",
    }
    assert writer.response_body["results"][1]["status"] == "skipped"
    radio.send_civ_transaction.assert_not_awaited()
    assert srv.command_queue.drain() == []


@pytest.mark.asyncio
async def test_http_command_batch_rejects_raw_civ_when_backend_does_not_support_it() -> (
    None
):
    srv = WebServer(_radio_without_civ(), WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands/batch",
        {
            "steps": [
                {"name": "send_civ", "params": {"command": 0x1A, "data": "015301"}},
                {"name": "set_freq", "params": {"freq": 144_030_000}},
            ],
        },
    )

    assert writer.response_status == 200
    assert writer.response_body["ok"] is False
    assert writer.response_body["results"][0]["status"] == "failed_validation"
    assert writer.response_body["results"][0]["error"] == "unsupported_command"
    assert writer.response_body["results"][1]["status"] == "skipped"
    assert srv.command_queue.drain() == []


@pytest.mark.asyncio
async def test_http_command_batch_reports_validation_failure_and_skips_remaining() -> (
    None
):
    srv = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands/batch",
        {
            "steps": [
                {"name": "no_such_command", "params": {}},
                {"name": "set_freq", "params": {"freq": 144_030_000}},
            ],
        },
    )

    assert writer.response_status == 200
    assert writer.response_body["ok"] is False
    assert writer.response_body["results"][0]["status"] == "failed_validation"
    assert writer.response_body["results"][0]["error"] == "unknown_command"
    assert writer.response_body["results"][1]["status"] == "skipped"
    assert srv.command_queue.drain() == []


@pytest.mark.asyncio
async def test_http_command_batch_requires_boolean_continue_on_error() -> None:
    srv = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands/batch",
        {
            "continue_on_error": "false",
            "steps": [{"name": "set_freq", "params": {"freq": 144_030_000}}],
        },
    )

    assert writer.response_status == 400
    assert writer.response_body["error"] == "invalid_request"
    assert srv.command_queue.drain() == []


@pytest.mark.asyncio
async def test_http_command_batch_timeout_cancels_unconsumed_step(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(web_server, "_COMMAND_BATCH_STEP_TIMEOUT", 0.001)
    radio = _radio()
    radio.set_freq = AsyncMock()
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    captured = []
    put_ordered = srv.command_queue.put_ordered

    def capture_ordered(command, **kwargs):
        result = put_ordered(command, **kwargs)
        captured.append((command, kwargs["future"]))
        return result

    monkeypatch.setattr(srv.command_queue, "put_ordered", capture_ordered)

    writer = await _post_json(
        srv,
        "/api/v1/commands/batch",
        {"steps": [{"name": "set_freq", "params": {"freq": 144_030_000}}]},
    )

    assert writer.response_status == 200
    assert writer.response_body["ok"] is False
    assert writer.response_body["results"][0]["status"] == "timed_out"
    assert writer.response_body["results"][0]["error"] == "command_timeout"

    [(command, reply)] = captured
    assert command == SetFreq(144_030_000, receiver=0)
    assert isinstance(reply, asyncio.Future)
    assert reply.cancelled()
    await asyncio.sleep(0)
    radio.set_freq.assert_not_awaited()
    radio.send_civ.assert_not_awaited()
    assert srv.command_queue.drain_entries() == [], (
        "timed-out ordinary step must no longer be pending"
    )


@pytest.mark.asyncio
async def test_http_command_batch_reports_prior_executed_step_before_failure() -> None:
    srv = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0))
    captured: list[object] = []
    consumer = asyncio.create_task(
        _complete_ordered_commands(srv.command_queue, 1, captured)
    )
    try:
        writer = await _post_json(
            srv,
            "/api/v1/commands/batch",
            {
                "steps": [
                    {"name": "set_freq", "params": {"freq": 144_030_000}},
                    {"name": "no_such_command", "params": {}},
                    {"name": "set_mode", "params": {"mode": "FM"}},
                ],
            },
        )
    finally:
        await asyncio.wait_for(consumer, timeout=1.0)

    assert writer.response_status == 200
    assert writer.response_body["ok"] is False
    assert [r["status"] for r in writer.response_body["results"]] == [
        "executed",
        "failed_validation",
        "skipped",
    ]
    assert captured == [SetFreq(144_030_000, receiver=0)]


@pytest.mark.asyncio
async def test_http_command_batch_rejects_queue_bypass_commands() -> None:
    srv = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands/batch",
        {
            "steps": [
                {"name": "get_tuner_status", "params": {}},
                {"name": "set_freq", "params": {"freq": 144_030_000}},
            ],
        },
    )

    assert writer.response_status == 200
    assert writer.response_body["ok"] is False
    assert writer.response_body["results"][0]["status"] == "failed_validation"
    assert writer.response_body["results"][0]["error"] == "unsupported_in_batch"
    assert writer.response_body["results"][1]["status"] == "skipped"
    assert srv.command_queue.drain() == []


@pytest.mark.asyncio
async def test_http_command_batch_missing_required_param_returns_failed_validation() -> (
    None
):
    srv = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands/batch",
        {
            "steps": [
                {"name": "set_freq", "params": {}},
            ],
        },
    )

    assert writer.response_status == 200
    assert writer.response_body["ok"] is False
    assert writer.response_body["results"][0]["status"] == "failed_validation"
    assert writer.response_body["results"][0]["error"] == "invalid_request"
    assert srv.command_queue.drain() == []


@pytest.mark.asyncio
async def test_http_single_command_missing_required_param_returns_400() -> None:
    srv = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands",
        {"name": "set_freq", "params": {}},
    )

    assert writer.response_status == 400
    assert writer.response_body["error"] == "invalid_request"
    assert srv.command_queue.drain() == []


def _watts_radio() -> SimpleNamespace:
    profile = resolve_radio_profile(model="FTX-1")
    return SimpleNamespace(
        profile=profile,
        model=profile.model,
        capabilities=set(profile.capabilities),
        native_power_unit="watts",
        get_powerstat=AsyncMock(return_value=True),
        set_powerstat=AsyncMock(),
        get_rf_power=AsyncMock(return_value=0),
        set_rf_power=AsyncMock(),
    )


def _raw_level_radio() -> MagicMock:
    radio = _radio()
    radio.native_power_unit = "raw_255"
    radio.set_af_level = AsyncMock()
    radio.get_rf_power = AsyncMock(return_value=0)
    radio.set_rf_power = AsyncMock()
    radio.get_powerstat = AsyncMock(return_value=True)
    radio.set_powerstat = AsyncMock()
    return radio


def _level_params(name: str, level: object, *, tagged: bool) -> dict[str, object]:
    params: dict[str, object] = {"level": level}
    if name == "set_af_level":
        params["receiver"] = 0
    if tagged:
        params["level_unit"] = "normalized"
    return params


def _assert_native_levels(
    commands: list[object],
    *,
    name: str,
    expected: list[int],
    unit: str,
) -> None:
    assert len(commands) == len(expected)
    if name == "set_af_level":
        assert all(isinstance(command, CommandIntent) for command in commands)
        assert [
            command.name for command in commands if isinstance(command, CommandIntent)
        ] == ["set_af_level"] * len(expected)
        assert [
            command.params["af_level"]
            for command in commands
            if isinstance(command, CommandIntent)
        ] == expected
        assert all(
            "level_unit" not in command.params
            for command in commands
            if isinstance(command, CommandIntent)
        )
        return
    assert commands == [SetPower(level, unit=unit) for level in expected]


async def _post_level_series(
    srv: WebServer,
    path: str,
    *,
    name: str,
    levels: list[int | float],
    tagged: bool,
) -> tuple[list[_FakeWriter], list[object]]:
    steps = [
        {"name": name, "params": _level_params(name, level, tagged=tagged)}
        for level in levels
    ]
    if path.endswith("/batch"):
        captured: list[object] = []
        consumer = asyncio.create_task(
            _complete_ordered_commands(srv.command_queue, len(steps), captured)
        )
        try:
            writer = await _post_json(srv, path, {"steps": steps})
        finally:
            await asyncio.wait_for(consumer, timeout=1.0)
        assert [result["status"] for result in writer.response_body["results"]] == [
            "executed"
        ] * len(steps)
        return [writer], captured

    # The single command now answers after the poller settles it, so each
    # post needs the same poller-like completion the batch branch runs.
    writers: list[_FakeWriter] = []
    captured: list[object] = []
    consumer = asyncio.create_task(
        _complete_ordered_commands(srv.command_queue, len(steps), captured)
    )
    try:
        for step in steps:
            writer = await _post_json(srv, path, step)
            writers.append(writer)
    finally:
        await asyncio.wait_for(consumer, timeout=5.0)
    return writers, captured


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/commands", "/api/v1/commands/batch"])
@pytest.mark.parametrize(
    ("name", "unit", "expected"),
    [
        ("set_af_level", "raw_255", [0, 128, 255]),
        ("set_rf_power", "raw_255", [0, 128, 255]),
        ("set_power", "raw_255", [0, 128, 255]),
        # MOR-2998: the FTX-1 profile floor (5 W) clamps the bottom of
        # the range in admission — 0 rides up to 5.
        ("set_rf_power", "watts", [5, 50, 100]),
        ("set_power", "watts", [5, 50, 100]),
    ],
)
async def test_http_normalized_level_marker_reaches_native_effects(
    path: str,
    name: str,
    unit: str,
    expected: list[int],
) -> None:
    radio = _watts_radio() if unit == "watts" else _raw_level_radio()
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))

    writers, commands = await _post_level_series(
        srv,
        path,
        name=name,
        levels=[0, 0.5, 1],
        tagged=True,
    )

    assert all(writer.response_status == 200 for writer in writers)
    assert all(writer.response_body["ok"] is True for writer in writers)
    _assert_native_levels(commands, name=name, expected=expected, unit=unit)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/commands", "/api/v1/commands/batch"])
@pytest.mark.parametrize(
    ("name", "unit", "expected"),
    [
        ("set_af_level", "raw_255", [1, 255]),
        ("set_rf_power", "raw_255", [1, 255]),
        ("set_power", "raw_255", [1, 255]),
        # MOR-2998: an int below the FTX-1 floor rides up to 5 W in
        # admission; the JSON-type dispatch itself (int = watts,
        # float = normalized) is unchanged.
        ("set_rf_power", "watts", [5, 100]),
        ("set_power", "watts", [5, 100]),
    ],
)
async def test_http_untagged_level_json_type_compatibility(
    path: str,
    name: str,
    unit: str,
    expected: list[int],
) -> None:
    radio = _watts_radio() if unit == "watts" else _raw_level_radio()
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))

    writers, commands = await _post_level_series(
        srv,
        path,
        name=name,
        levels=[1, 1.0],
        tagged=False,
    )

    assert all(writer.response_status == 200 for writer in writers)
    assert all(writer.response_body["ok"] is True for writer in writers)
    _assert_native_levels(commands, name=name, expected=expected, unit=unit)


_MALFORMED_NORMALIZED_LEVELS = [
    {"level": False, "level_unit": "normalized"},
    {"level": "0.5", "level_unit": "normalized"},
    {"level": float("nan"), "level_unit": "normalized"},
    {"level": float("inf"), "level_unit": "normalized"},
    {"level": float("-inf"), "level_unit": "normalized"},
    {"level": -0.01, "level_unit": "normalized"},
    {"level": 1.01, "level_unit": "normalized"},
    {"level": 0.5, "level_unit": "raw_255"},
    {"level": 0.5, "level_unit": None},
    {"level_unit": "normalized"},
]


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["/api/v1/commands", "/api/v1/commands/batch"])
@pytest.mark.parametrize("name", ["set_af_level", "set_rf_power", "set_power"])
@pytest.mark.parametrize("params", _MALFORMED_NORMALIZED_LEVELS)
async def test_http_normalized_level_marker_rejects_malformed_without_effect(
    monkeypatch: pytest.MonkeyPatch,
    path: str,
    name: str,
    params: dict[str, object],
) -> None:
    monkeypatch.setattr(web_server, "_COMMAND_BATCH_STEP_TIMEOUT", 0.001)
    radio = _raw_level_radio()
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    attempted: list[object] = []
    original_put = srv.command_queue.put
    original_put_ordered = srv.command_queue.put_ordered

    def capture_put(command: object, **metadata: object) -> None:
        attempted.append(command)
        original_put(command, **metadata)

    def capture_put_ordered(command: object, **metadata: object) -> object:
        attempted.append(command)
        return original_put_ordered(command, **metadata)

    monkeypatch.setattr(srv.command_queue, "put", capture_put)
    monkeypatch.setattr(srv.command_queue, "put_ordered", capture_put_ordered)
    command_params = dict(params)
    if name == "set_af_level":
        command_params["receiver"] = 0
    payload = {"name": name, "params": command_params}
    if path.endswith("/batch"):
        payload = {"steps": [payload]}

    writer = await _post_json(srv, path, payload)

    if path.endswith("/batch"):
        assert writer.response_status == 200
        [result] = writer.response_body["results"]
        assert result["status"] == "failed_validation"
        assert result["error"] == "invalid_request"
    else:
        assert writer.response_status == 400
        assert writer.response_body["error"] == "invalid_request"
    assert attempted == []
    assert srv.command_queue.drain_entries() == []
    assert srv.command_service.lifecycle_events() == ()
    assert srv.command_service.pending_overlays(source="http", session_id=None) == ()
    radio.set_af_level.assert_not_awaited()
    radio.set_rf_power.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("continue_on_error", "expected_statuses", "expected_commands"),
    [
        (False, ["executed", "failed_validation", "skipped"], [SetFreq(144_030_000)]),
        (
            True,
            ["executed", "failed_validation", "executed"],
            [SetFreq(144_030_000), SetMode("FM")],
        ),
    ],
)
async def test_http_batch_malformed_normalized_step_preserves_ordered_policy(
    monkeypatch: pytest.MonkeyPatch,
    continue_on_error: bool,
    expected_statuses: list[str],
    expected_commands: list[object],
) -> None:
    monkeypatch.setattr(web_server, "_COMMAND_BATCH_STEP_TIMEOUT", 0.01)
    srv = WebServer(_raw_level_radio(), WebConfig(host="127.0.0.1", port=0))
    captured: list[object] = []
    consumer = asyncio.create_task(
        _complete_ordered_commands(srv.command_queue, len(expected_commands), captured)
    )
    try:
        writer = await _post_json(
            srv,
            "/api/v1/commands/batch",
            {
                "continue_on_error": continue_on_error,
                "steps": [
                    {"name": "set_freq", "params": {"freq": 144_030_000}},
                    {
                        "name": "set_rf_power",
                        "params": {"level": 0.5, "level_unit": "raw_255"},
                    },
                    {"name": "set_mode", "params": {"mode": "FM"}},
                ],
            },
        )
    finally:
        await asyncio.wait_for(consumer, timeout=1.0)

    assert writer.response_status == 200
    assert writer.response_body["ok"] is False
    assert [
        result["status"] for result in writer.response_body["results"]
    ] == expected_statuses
    assert captured == expected_commands


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "level", "expected_level", "expected_expectation"),
    [
        ("set_rf_power", 50, 50, 0.5),
        ("set_power", 50, 50, 0.5),
        ("set_rf_power", 0.4, 40, 102 / 255),
        ("set_power", 0.4, 40, 102 / 255),
    ],
)
async def test_http_batch_watts_power_expectation_uses_profile_max_watts(
    name: str,
    level: int | float,
    expected_level: int,
    expected_expectation: float,
) -> None:
    """HTTP batch power commands retain shared watts expectation semantics."""
    radio = _watts_radio()
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    captured: list[CommandQueueEntry] = []
    consumer = asyncio.create_task(
        _complete_ordered_entries(srv.command_queue, 1, captured)
    )

    try:
        writer = await _post_json(
            srv,
            "/api/v1/commands/batch",
            {"steps": [{"name": name, "params": {"level": level}}]},
        )
    finally:
        await asyncio.wait_for(consumer, timeout=1.0)

    assert writer.response_status == 200
    assert writer.response_body["results"][0]["status"] == "executed"
    [entry] = captured
    assert entry.command == SetPower(expected_level, unit="watts")
    assert entry.command_id is not None
    [expectation] = srv.command_service.readback_expectations(
        source="http",
        session_id=None,
        command_id=entry.command_id,
    )
    assert radio.profile.max_watts == 100
    assert expectation.value == pytest.approx(expected_expectation)
