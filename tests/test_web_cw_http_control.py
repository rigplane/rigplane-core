"""HTTP CW control endpoints for local app integrations."""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.capabilities import CAP_CW
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


def _cw_radio() -> MagicMock:
    radio = MagicMock()
    radio.capabilities = {CAP_CW}
    radio.send_cw_text = AsyncMock()
    radio.stop_cw_text = AsyncMock()
    return radio


@pytest.mark.asyncio
async def test_http_cw_send_calls_radio_send_cw_text() -> None:
    radio = _cw_radio()
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    payload = json.dumps({"text": "CQ CQ DE KN4KYD"}).encode()
    writer = _FakeWriter()

    await srv._handle_http(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        "POST",
        "/api/v1/radio/cw/send",
        headers={"content-length": str(len(payload))},
        reader=_reader_for(payload),
    )

    assert writer.response_status == 200
    assert writer.response_body == {"text": "CQ CQ DE KN4KYD"}
    radio.send_cw_text.assert_awaited_once_with("CQ CQ DE KN4KYD")


@pytest.mark.asyncio
async def test_http_cw_stop_calls_radio_stop_cw_text() -> None:
    radio = _cw_radio()
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    writer = _FakeWriter()

    await srv._handle_http(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        "POST",
        "/api/v1/radio/cw/stop",
        headers={"content-length": "0"},
        reader=_reader_for(b""),
    )

    assert writer.response_status == 200
    assert writer.response_body == {}
    radio.stop_cw_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_http_cw_send_respects_read_only_mode() -> None:
    radio = _cw_radio()
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0, read_only=True))
    payload = json.dumps({"text": "CQ"}).encode()
    writer = _FakeWriter()

    await srv._handle_http(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        "POST",
        "/api/v1/radio/cw/send",
        headers={"content-length": str(len(payload))},
        reader=_reader_for(payload),
    )

    assert writer.response_status == 403
    assert writer.response_body["error"] == "read_only"
    radio.send_cw_text.assert_not_awaited()
