"""MOR-3678 (#4317 gate review): a backend with no ``send_cw_text`` refuses alike.

Removing the ``cw`` tag gate must not turn "this backend cannot send CW
text" into a programmer error (HTTP 500). The external-rigctld client has
no ``send_cw_text``; HTTP, WebSocket and the CLI all refuse it as an
unsupported command with one text, and nothing reaches rigctld.

Real entry points: a started ``WebServer`` over loopback (HTTP request,
WebSocket ``cmd``) and real argv through ``_build_parser`` / ``_run``. The
radio is the production ``RigctldClientRadio``; only rigctld itself is
faked (``FakeRigctldServer``, a loopback TCP server).
"""

from __future__ import annotations

import asyncio

import pytest

from fake_rigctld import FakeRigctldServer
from rigplane.backends.rigctld_client import RigctldClientRadio
from rigplane.cli import _build_parser, _finalize_ptt_args, _run
from rigplane.web.server import WebConfig, WebServer
from testkit.test_sim_cw_text_entry_points import _ws_command
from testkit.test_sim_ftx1_send_cw_text import _request

UNSUPPORTED = "radio does not support this command"
_SEND_COMMANDS = {"\\send_morse", "b"}


def _sent_cw(server: FakeRigctldServer) -> list[str]:
    return [c for c in server.commands_seen if c.split(" ", 1)[0] in _SEND_COMMANDS]


@pytest.mark.asyncio
async def test_http_and_websocket_refuse_cw_text_on_a_backend_without_it() -> None:
    async with FakeRigctldServer() as rigctld:
        radio = RigctldClientRadio(host=rigctld.host, port=rigctld.port)
        await asyncio.wait_for(radio.connect(), 10)
        server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
        await server.start()
        try:
            status, body = await _request(
                server.port, "POST", "/api/v1/radio/cw/send", {"text": "CQ"}
            )
            response = await _ws_command(server.port, "send_cw_text", {"text": "CQ"})
        finally:
            await server.stop()
            await radio.disconnect()
        assert status == 409, body
        assert body["error"] == "unsupported_command"
        assert body["message"] == UNSUPPORTED
        assert response["ok"] is False
        assert response["error"] == "unsupported_command"
        assert response["message"] == UNSUPPORTED
        assert _sent_cw(rigctld) == []


@pytest.mark.asyncio
async def test_cli_refuses_cw_text_on_a_backend_without_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    async with FakeRigctldServer() as rigctld:
        parser = _build_parser()
        args = parser.parse_args(
            [
                "--backend",
                "rigctld",
                "--host",
                rigctld.host,
                "--port",
                str(rigctld.port),
                "cw",
                "CQ",
            ]
        )
        _finalize_ptt_args(parser, args)
        rc = await _run(args)
        assert _sent_cw(rigctld) == []
    assert rc == 1
    captured = capsys.readouterr()
    assert captured.err.strip().splitlines()[-1] == f"Error: {UNSUPPORTED}"
    assert "CW:" not in captured.out


@pytest.mark.asyncio
async def test_published_cw_text_matches_what_a_rigctld_backend_can_run() -> None:
    # #4317 gate review: --backend rigctld --model IC-7300 resolves the
    # IC-7300 profile (cw_text supported by default), but the rigctld client
    # cannot send CW text. Both capability documents say so, with the
    # profile's limits kept, and agree with the refusal over HTTP and WS.
    async with FakeRigctldServer() as rigctld:
        radio = RigctldClientRadio(
            host=rigctld.host, port=rigctld.port, model="IC-7300"
        )
        await asyncio.wait_for(radio.connect(), 10)
        server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
        await server.start()
        try:
            _, info = await _request(server.port, "GET", "/api/v1/info")
            _, caps = await _request(server.port, "GET", "/api/v1/capabilities")
            status, body = await _request(
                server.port, "POST", "/api/v1/radio/cw/send", {"text": "CQ"}
            )
            response = await _ws_command(server.port, "send_cw_text", {"text": "CQ"})
        finally:
            await server.stop()
            await radio.disconnect()
        published = {"supported": False, "maxLength": 512, "memoryChannel": None}
        assert info["model"] == "IC-7300"
        assert info["capabilities"]["cwText"] == published
        assert caps["cwText"] == published
        assert (status, body["error"], body["message"]) == (
            409,
            "unsupported_command",
            UNSUPPORTED,
        )
        assert (response["ok"], response["error"], response["message"]) == (
            False,
            "unsupported_command",
            UNSUPPORTED,
        )
        assert _sent_cw(rigctld) == []
