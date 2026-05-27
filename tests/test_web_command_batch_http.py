"""HTTP structured command and ordered batch endpoints."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import MagicMock

import pytest

from rigplane.profiles import resolve_radio_profile
from rigplane.web import server as web_server
from rigplane.web.radio_poller import CommandQueue, SetFreq, SetMode
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
    return radio


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
