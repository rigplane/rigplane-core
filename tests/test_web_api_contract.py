"""Stable Web API contract tests for managed supervisors and Pro clients."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rigplane.web.api_contract import (
    RESPONSE_FIELD_CONTRACTS,
    STABLE_HTTP_ENDPOINTS,
    STABLE_WEBSOCKET_ROUTES,
    WEB_API_CONTRACT_VERSION,
)
from rigplane.web.server import WebConfig, WebServer


class _Writer:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None


def _json_response(writer: _Writer) -> tuple[int, dict]:
    text = writer.buffer.decode("ascii", errors="replace")
    status = int(text.split(" ", 2)[1])
    body_start = text.index("\r\n\r\n") + 4
    return status, json.loads(text[body_start:] or "{}")


def test_pro_web_api_contract_lists_stable_surface() -> None:
    assert WEB_API_CONTRACT_VERSION == 1

    http = {(route["method"], route["path"]) for route in STABLE_HTTP_ENDPOINTS}
    assert ("GET", "/healthz") in http
    assert ("GET", "/readyz") in http
    assert ("GET", "/api/v1/runtime") in http
    assert ("GET", "/api/v1/station") in http
    assert ("GET", "/api/v1/info") in http
    assert ("GET", "/api/v1/state") in http
    assert ("GET", "/api/v1/capabilities") in http
    assert ("GET", "/api/v1/audio/analysis") in http
    assert ("GET", "/api/v1/bridge") in http
    assert ("POST", "/api/v1/bridge") in http
    assert ("DELETE", "/api/v1/bridge") in http
    assert ("POST", "/api/v1/commands") in http
    assert ("POST", "/api/v1/commands/batch") in http

    ws = {route["path"] for route in STABLE_WEBSOCKET_ROUTES}
    assert "/api/v1/ws" in ws
    assert "/api/v1/scope" in ws
    assert "/api/v1/audio" in ws
    assert "/api/v1/audio-scope" in ws

    assert RESPONSE_FIELD_CONTRACTS["/api/v1/info"]["required"] == (
        "server",
        "version",
        "proto",
        "radio",
        "model",
        "capabilities",
        "connection",
    )
    assert RESPONSE_FIELD_CONTRACTS["/api/v1/commands"]["required"] == (
        "ok",
        "name",
        "result",
    )
    assert RESPONSE_FIELD_CONTRACTS["/api/v1/commands/batch"]["required"] == (
        "ok",
        "results",
    )


def test_command_batch_docs_use_numeric_data_mode_contract() -> None:
    root = Path(__file__).resolve().parents[1]
    docs = [
        root / "docs/api/web.md",
        root / "docs/guide/web-ui.md",
    ]

    for path in docs:
        text = path.read_text(encoding="utf-8")
        assert '"name": "set_data_mode"' in text or '"name":"set_data_mode"' in text
        assert '"set_data_mode", "params": { "enabled"' not in text
        assert '"set_data_mode","params":{"enabled"' not in text
        assert '"set_data_mode", "params": {"enabled"' not in text
        assert '"set_data_mode","params": {"enabled"' not in text


@pytest.mark.asyncio
async def test_stable_http_payloads_satisfy_required_field_contract() -> None:
    srv = WebServer(None, WebConfig(host="127.0.0.1", port=0, auth_token="token"))
    srv._server = type(  # noqa: SLF001
        "_Server",
        (),
        {
            "sockets": [
                type("_Socket", (), {"getsockname": lambda self: ("127.0.0.1", 0)})()
            ]
        },
    )()
    headers = {"authorization": "Bearer token"}

    for path, expected_status in (
        ("/healthz", 200),
        ("/readyz", 503),
        ("/api/v1/runtime", 200),
        ("/api/v1/station", 200),
        ("/api/v1/info", 200),
        ("/api/v1/state", 200),
        ("/api/v1/capabilities", 200),
    ):
        writer = _Writer()
        await srv._handle_http(writer, "GET", path, headers=headers)  # noqa: SLF001
        status, payload = _json_response(writer)
        assert status == expected_status
        required = RESPONSE_FIELD_CONTRACTS[path]["required"]
        assert set(required).issubset(payload)
