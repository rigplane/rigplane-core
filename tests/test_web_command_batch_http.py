"""HTTP structured command and ordered batch endpoints."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from unittest.mock import MagicMock

import pytest

from rigplane.profiles import resolve_radio_profile
from rigplane.web import server as web_server
from rigplane.web.radio_poller import CommandQueue, SendCiv, SetFreq, SetMode
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


@pytest.mark.asyncio
async def test_http_command_enqueues_single_structured_command() -> None:
    srv = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands",
        {"id": "single-1", "name": "set_freq", "params": {"freq": 144_030_000}},
    )

    assert writer.response_status == 200
    assert writer.response_body == {
        "id": "single-1",
        "ok": True,
        "name": "set_freq",
        "result": {"freq": 144_030_000, "receiver": 0},
    }
    assert srv.command_queue.drain() == [SetFreq(144_030_000, receiver=0)]


@pytest.mark.asyncio
async def test_http_command_enqueues_raw_civ_command() -> None:
    srv = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands",
        {
            "id": "display-type-b",
            "name": "send_civ",
            "params": {"command": 0x1A, "sub": 0x05, "data": "015301"},
        },
    )

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
    assert srv.command_queue.drain() == [
        SendCiv(command=0x1A, sub=0x05, data=b"\x01\x53\x01")
    ]


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
async def test_http_raw_civ_transaction_requires_auth_when_configured() -> None:
    radio = _radio()
    radio.send_civ_transaction = AsyncMock()
    srv = WebServer(
        radio,
        WebConfig(host="127.0.0.1", port=0, auth_token="secret"),
    )

    writer = await _post_json(
        srv,
        "/api/v1/civ/transaction",
        {"command": 0x1A, "data": "015301", "expect": "ack"},
    )

    assert writer.response_status == 401
    assert writer.response_body["error"] == "unauthorized"
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


@pytest.mark.asyncio
async def test_http_raw_civ_single_commands_preserve_repeated_steps() -> None:
    srv = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0))

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

    assert writer1.response_status == 200
    assert writer2.response_status == 200
    assert srv.command_queue.drain() == [
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
async def test_http_command_requires_auth_when_configured() -> None:
    srv = WebServer(
        _radio(),
        WebConfig(host="127.0.0.1", port=0, auth_token="secret"),
    )

    writer = await _post_json(
        srv,
        "/api/v1/commands",
        {"name": "set_freq", "params": {"freq": 144_030_000}},
    )

    assert writer.response_status == 401
    assert writer.response_body["error"] == "unauthorized"


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
    srv = WebServer(_radio(), WebConfig(host="127.0.0.1", port=0))

    writer = await _post_json(
        srv,
        "/api/v1/commands/batch",
        {"steps": [{"name": "set_freq", "params": {"freq": 144_030_000}}]},
    )

    assert writer.response_status == 200
    assert writer.response_body["ok"] is False
    assert writer.response_body["results"][0]["status"] == "timed_out"
    assert writer.response_body["results"][0]["error"] == "command_timeout"

    [entry] = srv.command_queue.drain_entries()
    assert entry.command == SetFreq(144_030_000, receiver=0)
    assert entry.future is not None
    assert entry.future.cancelled()


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
