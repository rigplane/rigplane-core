"""Producer half of host-api-v3.component.test.ts's shared wire documents."""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.runtime.controller_authority import ControllerAuthority
from rigplane.web.radio_poller import RadioPoller
from rigplane.web.server import WebConfig, WebServer
from test_handlers_coverage import _capable_radio, _control_handler
from test_web_server_coverage import _FakeWriter, _reader_with, _response_json

WIRE = json.loads(
    (Path(__file__).parent / "fixtures/host-api-v3-wire.json").read_text()
)


@pytest.mark.asyncio
async def test_controller_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    authority = ControllerAuthority(
        ready=lambda: True, cleanup=AsyncMock(), clock=lambda: 1.0
    )
    monkeypatch.setattr(
        "rigplane.runtime.controller_authority.secrets.token_hex",
        lambda size: "0" * (size * 2),
    )
    try:
        assert authority.status() == WIRE["controllerStatus"]
        grant = authority.acquire("browser")
        assert grant == WIRE["controllerGrant"]
        ticket = authority.attach(grant["controller_key"], "primary", "host-wire")
        authority.register_control(ticket)
        assert authority.heartbeat(ticket) == WIRE["controllerHeartbeat"]
    finally:
        authority.revoke()
        await authority.settle()


def _observe(server: WebServer, path: FieldPath, value: object) -> None:
    # Fake provider readback enters the real observation store and serializers.
    server.command_state_store.apply_current(
        Observation(
            path=path,
            value=value,
            source=SourceMetadata(source="poll_response", provider="fake_radio"),
            timestamp_monotonic=1.0,
        )
    )


@pytest.mark.asyncio
async def test_power_handler_and_observed_publication() -> None:
    radio = SimpleNamespace(
        connected=True,
        control_connected=True,
        capabilities={"power_control"},
        set_powerstat=AsyncMock(),
    )
    server = WebServer(radio, WebConfig(radio_model="IC-7610"))
    path = FieldPath.global_("tx_state", "power_on")
    _observe(server, path, True)
    writer = _FakeWriter()
    body = json.dumps(WIRE["power"]["request"]).encode()
    await server._handle_http(
        writer,
        "POST",
        "/api/v1/radio/power",
        headers={"content-length": str(len(body))},
        reader=_reader_with(body),
    )
    assert _response_json(writer) == (200, WIRE["power"]["response"])
    radio.set_powerstat.assert_awaited_once_with(False)
    assert server.build_public_state()["powerOn"] is True
    _observe(server, path, WIRE["power"]["observed"])
    assert server.build_public_state()["powerOn"] is False
    assert (
        server.build_state_update_envelope(force_full=True)["data"]["powerOn"] is False
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("receiver", [0, 1])
async def test_af_handler_dispatch_and_observed_publication(receiver: int) -> None:
    radio = _capable_radio()
    server = WebServer(radio, WebConfig(radio_model="IC-7610"))
    for path, value in (
        (FieldPath.receiver("0", "operator_controls", "af_level"), 0.4),
        (FieldPath.receiver("1", "operator_controls", "af_level"), 0.6),
        (FieldPath.global_("slow_state", "active"), "MAIN"),
    ):
        _observe(server, path, value)
    fields = server.build_public_state()["fieldStatus"]
    assert {key: fields[key] for key in WIRE["fieldStatus"]} == WIRE["fieldStatus"]
    socket = SimpleNamespace(send_text=AsyncMock())
    handler = _control_handler(
        ws=socket, radio=radio, server=server, session_id="host-wire"
    )
    request = deepcopy(WIRE["af"]["request"])
    request["params"]["receiver"] = receiver
    await handler._handle_text(json.dumps(request))
    assert json.loads(socket.send_text.await_args.args[0])["ok"] is True
    entry = server.command_queue.take_entry()
    assert entry is not None
    poller = RadioPoller(
        radio, server.command_queue, state_store=server.command_state_store
    )
    await poller._execute_queued_entry(entry)
    radio.set_af_level.assert_awaited_once_with(
        level=WIRE["af"]["native"], receiver=receiver
    )
    _observe(
        server,
        FieldPath.receiver(str(receiver), "operator_controls", "af_level"),
        WIRE["af"]["native"],
    )
    name = "main" if receiver == 0 else "sub"
    assert server.build_public_state()[name]["afLevel"] == WIRE["af"]["observed"]
    assert (
        server.build_state_update_envelope(force_full=True)["data"][name]["afLevel"]
        == WIRE["af"]["observed"]
    )
