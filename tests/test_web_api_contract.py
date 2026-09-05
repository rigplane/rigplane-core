"""Stable Web API contract tests for managed supervisors and Pro clients."""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from rigplane.web.api_contract import (
    RESPONSE_FIELD_CONTRACTS,
    STABLE_HTTP_ENDPOINTS,
    STABLE_WEBSOCKET_ROUTES,
    WEB_API_CONTRACT_VERSION,
)
from rigplane.web.server import WebConfig, WebServer
from rigplane.rig_loader import load_rig


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


def _golden_controls() -> dict[str, object]:
    fixture = Path(__file__).parent / "fixtures" / "control-domain-controls.json"
    return json.loads(fixture.read_text(encoding="utf-8"))


def _golden_profile(tmp_path: Path):
    huge = "1" + "0" * 399
    half_huge = "5" + "0" * 398
    toml = f"""\
[radio]
id = "golden_controls"
model = "Golden Controls"
receiver_count = 1
has_lan = false
has_wifi = false

[capabilities]
features = ["tx"]

[modes]
list = ["USB"]

[filters]
list = ["FIL1"]

[vfo]
scheme = "ab"

[commands]

[commands.overrides]

[antenna]
tx_count = 2
has_rx_ant = true

[controls.identity]
mapping = "identity"
raw_min = -2
raw_max = 2
raw_step = 2
raw_origin = -2
display_min = -2
display_max = 2
display_step = 2
display_origin = -2
display_unit = "steps"
quantization = "floor"
restoration = "exact"
style = "stepped"

[controls.linear]
mapping = "linear"
raw_min = 0
raw_max = 2
raw_step = 1
raw_origin = 0
display_min = 0
display_max = {huge}
display_step = {half_huge}
display_origin = 0
display_unit = "huge"
quantization = "nearest_ties_up"
restoration = "exact"

[controls.centered]
mapping = "centered"
raw_min = -2
raw_max = 2
raw_step = 2
raw_origin = -2
display_min = -0.00000000000000000001
display_max = 0.00000000000000000001
display_step = 0.00000000000000000001
display_origin = -0.00000000000000000001
display_unit = "tiny"
quantization = "nearest_ties_down"
restoration = "exact"
raw_center = 0
display_center = 0

[controls.lookup]
mapping = "lookup"
raw_min = 0
raw_max = 4
raw_step = 2
raw_origin = 0
display_min = -1
display_max = 1
display_step = 1
display_origin = -1
display_unit = "label"
quantization = "reject"
restoration = "exact"
lookup = [{{ raw = 0, display = -1 }}, {{ raw = 2, display = 0 }}, {{ raw = 4, display = 1 }}]
"""
    path = tmp_path / "golden-controls.toml"
    path.write_text(toml, encoding="utf-8")
    return load_rig(path).to_profile()


@pytest.mark.asyncio
async def test_control_domains_round_trip_through_both_capability_endpoints(
    tmp_path: Path,
) -> None:
    """The shared JSON is derived only by loading TOML into a RadioProfile."""
    profile = _golden_profile(tmp_path)
    assert profile.controls == _golden_controls()
    assert profile.antenna_tx_count == 2
    assert profile.antenna_has_rx_ant is True
    radio = SimpleNamespace(
        model=profile.model,
        profile=profile,
        capabilities=set(profile.capabilities),
        connected=False,
        control_connected=False,
        radio_ready=False,
    )
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    headers = {}
    for path in ("/api/v1/info", "/api/v1/capabilities"):
        writer = _Writer()
        await server._handle_http(writer, "GET", path, headers=headers)  # noqa: SLF001
        status, payload = _json_response(writer)
        assert status == 200
        controls = (
            payload["capabilities"]["controls"]
            if path == "/api/v1/info"
            else payload["controls"]
        )
        capability_payload = (
            payload["capabilities"] if path == "/api/v1/info" else payload
        )
        assert controls == _golden_controls()
        assert capability_payload["antennas"] == 2
        assert capability_payload["hasRxAntenna"] is True


@pytest.mark.asyncio
async def test_capability_endpoints_omit_controls_for_legacy_profiles(
    tmp_path: Path,
) -> None:
    profile = replace(_golden_profile(tmp_path), controls=None)
    radio = SimpleNamespace(
        model=profile.model,
        profile=profile,
        capabilities=set(profile.capabilities),
        connected=False,
        control_connected=False,
        radio_ready=False,
    )
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    for path in ("/api/v1/info", "/api/v1/capabilities"):
        writer = _Writer()
        await server._handle_http(writer, "GET", path)  # noqa: SLF001
        _status, payload = _json_response(writer)
        capability_payload = (
            payload["capabilities"] if path == "/api/v1/info" else payload
        )
        assert "controls" not in capability_payload


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
    assert ("GET", "/api/v1/managed-transmit") in http
    assert ("POST", "/api/v1/managed-transmit/command") in http
    assert ("PUT", "/api/v1/managed-transmit/tot") in http
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
    assert RESPONSE_FIELD_CONTRACTS["/api/v1/managed-transmit"]["required"] == (
        "schemaVersion",
        "sampledAt",
        "managedTransmit",
        "txObservation",
    )
    assert RESPONSE_FIELD_CONTRACTS["/api/v1/managed-transmit/command"]["required"] == (
        "ok",
        "operation",
        "result",
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
    srv = WebServer(
        None,
        WebConfig(host="127.0.0.1", port=0, radio_model="IC-7610"),
    )
    srv._server = type(  # noqa: SLF001
        "_Server",
        (),
        {
            "sockets": [
                type("_Socket", (), {"getsockname": lambda self: ("127.0.0.1", 0)})()
            ]
        },
    )()
    headers = {}

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
