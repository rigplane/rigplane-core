"""MOR-3678 end to end: WebServer -> YaesuCatRadio -> simulated FTX-1 socket.

The simulator (``serve --radio ftx1``) listens on ``socket://127.0.0.1``;
the production ``YaesuCatRadio`` connects to it, a real ``WebServer`` on
an ephemeral loopback port serves it, and requests go over real HTTP.
The device string is asserted to be loopback before anything connects:
no serial port, no real radio. The sim model records KM writes
(``keying_memory``) and KY play requests (``keyer_play_requests``).

FTX-1_CAT_OM_ENG_2508-C: KM printed p.17, KY printed p.18. The sim's KM
lexer rejects spaces, so the text here is ``CQ``.
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.rig_loader import load_rig
from rigplane.web.server import WebConfig, WebServer

from rigplane_testkit.sim.__main__ import build_parser
from rigplane_testkit.sim.serve import build_stack

ROOT = Path(__file__).resolve().parents[2]


@asynccontextmanager
async def _served(*, supported: bool) -> AsyncIterator[SimpleNamespace]:
    # The profile is loaded from a file, as production loads it: the shipped
    # ftx1.toml, or a copy whose [cw_text] says supported = true.
    text = (ROOT / "rigs" / "ftx1.toml").read_text(encoding="utf-8")
    if supported:
        assert text.count("\nsupported = false\n") == 1
        text = text.replace("\nsupported = false\n", "\nsupported = true\n", 1)
    with tempfile.TemporaryDirectory() as tmp:
        rigs = Path(tmp)
        for shared in (ROOT / "rigs").glob("_*.toml"):
            (rigs / shared.name).write_bytes(shared.read_bytes())
        (rigs / "ftx1.toml").write_text(text, encoding="utf-8")
        rig = load_rig(rigs / "ftx1.toml")
    assert rig.cw_text.supported is supported
    stack = build_stack(build_parser().parse_args(["serve", "--radio", "ftx1"]))
    await stack.backend.start()
    radio = None
    server = None
    try:
        device = stack.backend.startup_fields()["device"]
        assert device.startswith("socket://127.0.0.1:"), device
        radio = YaesuCatRadio(device, profile=rig)
        await asyncio.wait_for(radio.connect(), 20)
        server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
        await server.start()
        yield SimpleNamespace(
            port=server.port, model=stack.backend.radio.model, sim=stack.backend.radio
        )
    finally:
        if server is not None:
            await server.stop()
        if radio is not None:
            await radio.disconnect()
        await stack.backend.stop()
        stack.trace.close()


async def _request(
    port: int, method: str, path: str, body: dict | None = None
) -> tuple[int, dict]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        data = b"" if body is None else json.dumps(body).encode()
        head = (
            f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(data)}\r\n"
            "Connection: close\r\n\r\n"
        )
        writer.write(head.encode() + data)
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(), 10)
    finally:
        writer.close()
    status = int(raw.split(b" ", 2)[1])
    payload = raw.split(b"\r\n\r\n", 1)[1]
    return status, json.loads(payload) if payload else {}


async def _settle() -> None:
    await asyncio.sleep(0.3)


@pytest.mark.asyncio
async def test_shipped_ftx1_publishes_cw_text_and_refuses_to_send() -> None:
    async with _served(supported=False) as s:
        status, info = await _request(s.port, "GET", "/api/v1/info")
        assert status == 200
        assert info["capabilities"]["cwText"] == {
            "supported": False,
            "maxLength": 50,
            "memoryChannel": 5,
        }
        assert info["capabilities"]["hasCw"] is True
        assert "cw" in info["capabilities"]["tags"]

        status, caps = await _request(s.port, "GET", "/api/v1/capabilities")
        assert status == 200
        assert caps["cwText"] == {
            "supported": False,
            "maxLength": 50,
            "memoryChannel": 5,
        }
        assert "cw" in caps["capabilities"]

        status, body = await _request(
            s.port, "POST", "/api/v1/radio/cw/send", {"text": "CQ"}
        )
        assert (status, body) == (
            409,
            {
                "error": "unsupported_command",
                "message": "FTX-1 does not support sending CW text yet",
            },
        )
        await _settle()
        assert s.model.keying_memory == {}
        assert s.model.keyer_play_requests == ()


@pytest.mark.asyncio
async def test_enabled_ftx1_writes_keyer_memory_and_plays_it_without_ptt() -> None:
    async with _served(supported=True) as s:
        status, body = await _request(
            s.port, "POST", "/api/v1/radio/cw/send", {"text": "CQ"}
        )
        assert (status, body) == (200, {"text": "CQ"})
        await _settle()
        assert s.model.keying_memory == {5: "CQ"}
        assert s.model.keyer_play_requests == ((0, 5),)
        assert s.sim.ptt is False

        status, body = await _request(
            s.port, "POST", "/api/v1/radio/cw/send", {"text": "A" * 51}
        )
        assert (status, body) == (
            400,
            {
                "error": "invalid_request",
                "message": "CW text too long for FTX-1: got 51 characters, limit is 50",
            },
        )
        status, _ = await _request(s.port, "POST", "/api/v1/radio/cw/stop")
        assert status == 200
        await _settle()
        # The rejected text never reached the radio; stop is KY00.
        assert s.model.keying_memory == {5: "CQ"}
        assert s.model.keyer_play_requests == ((0, 5), (0, 0))
