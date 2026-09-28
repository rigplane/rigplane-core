"""``GET /api/v1/info`` against the external rigctld backend (MOR-2899).

Pins two contracts:

- a rigctld-backend radio — whose ``model`` default ``"External rigctld"``
  matches no radio profile — still gets a 200 with the expected fields:
  profile-derived fields are reported as unavailable, never raised; and
- an exception escaping any HTTP handler is answered with a 500 and logged
  with its route, instead of dropping the connection silently.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from rigplane.backends.rigctld_client import RigctldClientRadio
from rigplane.web.server import WebConfig, WebServer


class _MemoryWriter:
    """Minimal asyncio.StreamWriter stand-in that captures written bytes."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:  # pragma: no cover - trivial
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:  # pragma: no cover - trivial
        return None

    def is_closing(self) -> bool:
        return False

    def get_extra_info(self, *_args: Any, **_kwargs: Any) -> Any:
        return ("127.0.0.1", 0)


def _status_and_json(writer: _MemoryWriter) -> tuple[int, dict]:
    text = bytes(writer.buffer).decode("ascii", errors="replace")
    status = int(text.split(" ", 2)[1])
    body = text.split("\r\n\r\n", 1)[1]
    return status, json.loads(body or "{}")


@pytest.mark.asyncio
async def test_api_info_serves_200_with_neutral_defaults_for_rigctld_radio() -> None:
    """The rigctld client's default model resolves to no profile, so the
    endpoint must keep serving: 200, backend caps present, profile-derived
    fields simply absent (MOR-2899)."""
    radio = RigctldClientRadio(host="127.0.0.1", port=0)
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    writer = _MemoryWriter()
    await server._handle_http(  # noqa: SLF001
        writer, "GET", "/api/v1/info", {"host": "localhost:8470"}
    )
    status, payload = _status_and_json(writer)
    assert status == 200
    assert payload["server"] == "rigplane"
    assert payload["model"] == "External rigctld"
    capabilities = payload["capabilities"]
    assert capabilities["hasTx"] is True
    assert "tx" in capabilities["tags"]
    assert capabilities["maxReceivers"] == 1
    assert "modes" not in capabilities
    assert "filters" not in capabilities
    connection = payload["connection"]
    assert connection["rigConnected"] is False
    assert "radioReady" in connection


@pytest.mark.asyncio
async def test_unhandled_handler_exception_answers_500_and_logs_route(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A handler that raises must produce a 500 response and an ERROR log
    naming the route — not a silently dropped connection (MOR-2899)."""

    async def _boom(
        writer: Any, headers: dict[str, str] | None = None
    ) -> None:  # pragma: no cover - raises
        raise RuntimeError("boom from handler")

    server = WebServer(None, WebConfig(host="127.0.0.1", port=0))
    monkeypatch.setattr(server, "_serve_info", _boom)
    writer = _MemoryWriter()
    with caplog.at_level(logging.ERROR, logger="rigplane.web.web_routing"):
        await server._handle_http(  # noqa: SLF001
            writer, "GET", "/api/v1/info", {"host": "localhost:8470"}
        )
    status, payload = _status_and_json(writer)
    assert status == 500
    assert payload == {"error": "internal server error"}
    assert any(
        "/api/v1/info" in record.getMessage() and record.exc_info
        for record in caplog.records
    )
