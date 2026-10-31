"""Tests for POST body size cap (issue #946).

Verifies _read_capped_body helper and that all four POST handlers
return 413 when Content-Length exceeds _MAX_POST_BODY.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from rigplane.web.server import _MAX_POST_BODY, _read_capped_body


# ---------------------------------------------------------------------------
# _read_capped_body unit tests
# ---------------------------------------------------------------------------


def _make_reader(data: bytes) -> asyncio.StreamReader:
    reader = asyncio.StreamReader()
    reader.feed_data(data)
    reader.feed_eof()
    return reader


@pytest.mark.asyncio
async def test_read_capped_body_within_limit() -> None:
    """Body at or below the cap is returned intact."""
    payload = b'{"key": "value"}'
    reader = _make_reader(payload)
    result = await _read_capped_body(reader, len(payload))
    assert result == payload


@pytest.mark.asyncio
async def test_read_capped_body_exactly_at_limit() -> None:
    """Body exactly at _MAX_POST_BODY is accepted."""
    payload = b"x" * _MAX_POST_BODY
    reader = _make_reader(payload)
    result = await _read_capped_body(reader, _MAX_POST_BODY)
    assert result == payload


@pytest.mark.asyncio
async def test_read_capped_body_exceeds_limit_returns_none() -> None:
    """Content-Length one byte over the cap returns None without reading."""
    oversized_cl = _MAX_POST_BODY + 1
    # Reader has no data — if _read_capped_body tried to read it would hang/fail.
    reader = asyncio.StreamReader()
    result = await _read_capped_body(reader, oversized_cl)
    assert result is None


@pytest.mark.asyncio
async def test_read_capped_body_huge_cl_does_not_allocate() -> None:
    """Malicious huge Content-Length (1 GiB) is rejected immediately."""
    one_gib = 1024 * 1024 * 1024
    reader = asyncio.StreamReader()  # empty — no data fed
    result = await _read_capped_body(reader, one_gib)
    assert result is None


# ---------------------------------------------------------------------------
# Helper: fake writer that captures output
# ---------------------------------------------------------------------------


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
        """Parse HTTP status code from the buffered response."""
        line = self.buffer.split(b"\r\n", 1)[0]
        return int(line.split(b" ")[1])

    @property
    def response_body(self) -> dict:
        """Parse JSON body from the buffered response."""
        body = self.buffer.split(b"\r\n\r\n", 1)[1]
        return json.loads(body)


# ---------------------------------------------------------------------------
# Integration-style tests: handlers send 413 on oversized Content-Length
# ---------------------------------------------------------------------------


def _oversized_headers() -> dict[str, str]:
    return {"content-length": str(_MAX_POST_BODY + 1)}


def _small_headers(payload: bytes) -> dict[str, str]:
    return {"content-length": str(len(payload))}


@pytest.mark.asyncio
async def test_band_plan_config_rejects_oversized_body() -> None:
    """_handle_band_plan_config returns 413 for oversized Content-Length."""
    from unittest.mock import MagicMock

    from rigplane.web.server import WebConfig, WebServer

    srv = WebServer(MagicMock(), WebConfig(host="127.0.0.1", port=0))
    writer = _FakeWriter()
    reader = asyncio.StreamReader()  # empty — should not be read

    await srv._handle_band_plan_config(
        writer,  # type: ignore[arg-type]
        headers=_oversized_headers(),
        reader=reader,  # type: ignore[arg-type]
    )

    assert writer.response_status == 413
    assert writer.response_body.get("error") == "request_too_large"
    assert writer.closed


@pytest.mark.asyncio
async def test_eibi_fetch_rejects_oversized_body() -> None:
    """_handle_eibi_fetch returns 413 for oversized Content-Length."""
    from unittest.mock import AsyncMock, MagicMock

    from rigplane.web.server import WebConfig, WebServer

    srv = WebServer(MagicMock(), WebConfig(host="127.0.0.1", port=0))
    srv._eibi = MagicMock()
    srv._eibi.fetch = AsyncMock(return_value={"status": "ok"})
    writer = _FakeWriter()
    reader = asyncio.StreamReader()

    await srv._handle_eibi_fetch(
        writer,  # type: ignore[arg-type]
        headers=_oversized_headers(),
        reader=reader,  # type: ignore[arg-type]
    )

    assert writer.response_status == 413
    assert writer.response_body.get("error") == "request_too_large"
    assert writer.closed


@pytest.mark.asyncio
async def test_band_plan_config_accepts_valid_body(tmp_path: Path) -> None:
    """_handle_band_plan_config succeeds with a small valid body.

    The handler persists ``_config.toml`` into the server's band-plan
    directory, so it gets a temporary one (never the shared checkout).
    """
    from unittest.mock import MagicMock

    from rigplane.web.server import WebConfig, WebServer

    srv = WebServer(
        MagicMock(), WebConfig(host="127.0.0.1", port=0, band_plans_dir=tmp_path)
    )
    srv._band_plan_manager = MagicMock()
    srv._band_plan_manager.set_region = MagicMock(return_value=None)
    srv._band_plan_manager.get_all_bands = MagicMock(return_value=[])

    payload = json.dumps({"region": "US"}).encode()
    reader = _make_reader(payload)
    writer = _FakeWriter()

    await srv._handle_band_plan_config(
        writer,  # type: ignore[arg-type]
        headers=_small_headers(payload),
        reader=reader,  # type: ignore[arg-type]
    )

    assert writer.response_status == 200
    assert not writer.closed
