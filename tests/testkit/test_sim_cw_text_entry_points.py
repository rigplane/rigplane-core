"""MOR-3678 (#4317 gate review): CW text through every user entry point.

Each case starts from user input at a real entry point and ends at what the
user sees plus what reached the radio:

* HTTP: a request over a real loopback socket to a started ``WebServer``.
* WebSocket: a real upgrade on that server, a ``cmd`` frame, its response.
* CLI: real argv through ``_build_parser`` and ``_run``, which builds the
  production ``YaesuCatRadio`` through ``create_radio``.

The profile comes from a TOML file through ``load_rig`` (the CLI reads it
from the rigs directory it is pointed at), and the radio is the simulated
FTX-1 on ``socket://127.0.0.1`` (``serve --radio ftx1``). Only the radio is
simulated; no serial port, no real radio. The sim model records KM writes
(``keying_memory``) and KY plays (``keyer_play_requests``).
"""

from __future__ import annotations

import asyncio
import json
import re
import struct
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.cli import _build_parser, _finalize_ptt_args, _run
from rigplane.rig_loader import load_rig
from rigplane.web.server import WebConfig, WebServer

from rigplane_testkit.sim.__main__ import build_parser
from rigplane_testkit.sim.serve import build_stack
from test_serial_backend_smoke import _ws_connect, _ws_recv_frame
from testkit.test_sim_ftx1_send_cw_text import _request, _settle

ROOT = Path(__file__).resolve().parents[2]
_CW_TEXT_SECTION = re.compile(r"^\[cw_text\]\n(?:(?!\[).*\n)*", re.MULTILINE)

ENABLED = "[cw_text]\nsupported = true\nmax_length = 50\nmemory_channel = 5"
DISABLED = "[cw_text]\nsupported = false\nmax_length = 50\nmemory_channel = 5"
TOO_LONG = "CW text too long for FTX-1: got 51 characters, limit is 50"
UNSUPPORTED = "FTX-1 does not support sending CW text yet"


def _rigs_dir(tmp_path: Path, cw_text: str, *, cw_tag: bool) -> Path:
    """A rigs directory whose ftx1.toml carries *cw_text* (and maybe no cw tag)."""
    text = (ROOT / "rigs" / "ftx1.toml").read_text(encoding="utf-8")
    assert _CW_TEXT_SECTION.search(text)
    text = _CW_TEXT_SECTION.sub(cw_text + "\n", text)
    if not cw_tag:
        assert text.count('\n    "cw",\n') == 1
        text = text.replace('\n    "cw",\n', "\n", 1)
    rigs = tmp_path / "rigs"
    rigs.mkdir()
    for shared in (ROOT / "rigs").glob("_*.toml"):
        (rigs / shared.name).write_bytes(shared.read_bytes())
    (rigs / "ftx1.toml").write_text(text, encoding="utf-8")
    return rigs


@asynccontextmanager
async def _sim() -> AsyncIterator[SimpleNamespace]:
    stack = build_stack(build_parser().parse_args(["serve", "--radio", "ftx1"]))
    await stack.backend.start()
    try:
        device = stack.backend.startup_fields()["device"]
        assert device.startswith("socket://127.0.0.1:"), device
        yield SimpleNamespace(
            device=device, model=stack.backend.radio.model, sim=stack.backend.radio
        )
    finally:
        await stack.backend.stop()
        stack.trace.close()


@asynccontextmanager
async def _web(rigs: Path) -> AsyncIterator[SimpleNamespace]:
    async with _sim() as sim:
        radio = YaesuCatRadio(sim.device, profile=load_rig(rigs / "ftx1.toml"))
        await asyncio.wait_for(radio.connect(), 20)
        server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
        await server.start()
        try:
            yield SimpleNamespace(port=server.port, model=sim.model, sim=sim.sim)
        finally:
            await server.stop()
            await radio.disconnect()


async def _ws_send_text(writer: asyncio.StreamWriter, text: str) -> None:
    """One masked client text frame (RFC 6455 5.2), any length up to 64 KiB."""
    payload = text.encode("utf-8")
    mask = b"\x11\x22\x33\x44"
    masked = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    if len(payload) < 126:
        header = bytes([0x81, 0x80 | len(payload)])
    else:
        header = bytes([0x81, 0x80 | 126]) + struct.pack("!H", len(payload))
    writer.write(header + mask + masked)
    await writer.drain()


async def _ws_command(port: int, name: str, params: dict) -> dict:
    reader, writer = await _ws_connect("127.0.0.1", port, "/api/v1/ws")
    try:
        await _ws_send_text(
            writer,
            json.dumps({"type": "cmd", "id": "cw-1", "name": name, "params": params}),
        )
        for _ in range(50):
            _op, payload = await _ws_recv_frame(reader)
            message = json.loads(payload.decode("utf-8"))
            if message.get("type") == "response" and message.get("id") == "cw-1":
                return message
        raise AssertionError("no response to the cw-1 command")
    finally:
        writer.close()


async def _cli(device: str, *argv: str) -> int:
    parser = _build_parser()
    args = parser.parse_args(["--backend", "yaesu-cat", "--serial-port", device, *argv])
    _finalize_ptt_args(parser, args)
    return await _run(args)


# ── Enabled profile without the cw tag: all three entry points send ──


@pytest.mark.asyncio
async def test_http_sends_without_cw_tag(tmp_path: Path) -> None:
    async with _web(_rigs_dir(tmp_path, ENABLED, cw_tag=False)) as s:
        status, caps = await _request(s.port, "GET", "/api/v1/capabilities")
        assert "cw" not in caps["capabilities"]
        assert caps["cwText"]["supported"] is True
        status, body = await _request(
            s.port, "POST", "/api/v1/radio/cw/send", {"text": "CQ"}
        )
        assert (status, body) == (200, {"text": "CQ"})
        await _settle()
        assert s.model.keying_memory == {5: "CQ"}
        assert s.model.keyer_play_requests == ((0, 5),)


@pytest.mark.asyncio
async def test_websocket_sends_without_cw_tag(tmp_path: Path) -> None:
    async with _web(_rigs_dir(tmp_path, ENABLED, cw_tag=False)) as s:
        response = await _ws_command(s.port, "send_cw_text", {"text": "CQ"})
        assert response["ok"] is True, response
        await _settle()
        assert s.model.keying_memory == {5: "CQ"}
        assert s.model.keyer_play_requests == ((0, 5),)


@pytest.mark.asyncio
async def test_cli_sends_without_cw_tag(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rigs = _rigs_dir(tmp_path, ENABLED, cw_tag=False)
    monkeypatch.setattr("rigplane.backends.yaesu_cat.radio._RIGS_DIR", rigs)
    async with _sim() as sim:
        assert await _cli(sim.device, "cw", "CQ") == 0
        await _settle()
        assert sim.model.keying_memory == {5: "CQ"}
        assert sim.model.keyer_play_requests == ((0, 5),)
    assert "CW: CQ" in capsys.readouterr().out


# ── 51 characters: refused before any KM/KY, same text everywhere ──


@pytest.mark.asyncio
async def test_http_refuses_text_over_the_km_limit(tmp_path: Path) -> None:
    async with _web(_rigs_dir(tmp_path, ENABLED, cw_tag=True)) as s:
        status, body = await _request(
            s.port, "POST", "/api/v1/radio/cw/send", {"text": "A" * 51}
        )
        assert (status, body) == (
            400,
            {"error": "invalid_request", "message": TOO_LONG},
        )
        await _settle()
        assert s.model.keying_memory == {}
        assert s.model.keyer_play_requests == ()


@pytest.mark.asyncio
async def test_websocket_refuses_text_over_the_km_limit(tmp_path: Path) -> None:
    async with _web(_rigs_dir(tmp_path, ENABLED, cw_tag=True)) as s:
        response = await _ws_command(s.port, "send_cw_text", {"text": "A" * 51})
        assert (response["ok"], response["error"], response["message"]) == (
            False,
            "command_failed",
            TOO_LONG,
        )
        await _settle()
        assert s.model.keying_memory == {}
        assert s.model.keyer_play_requests == ()


@pytest.mark.asyncio
async def test_cli_refuses_text_over_the_km_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rigs = _rigs_dir(tmp_path, ENABLED, cw_tag=True)
    monkeypatch.setattr("rigplane.backends.yaesu_cat.radio._RIGS_DIR", rigs)
    async with _sim() as sim:
        assert await _cli(sim.device, "cw", "A" * 51) == 1
        await _settle()
        assert sim.model.keying_memory == {}
        assert sim.model.keyer_play_requests == ()
    captured = capsys.readouterr()
    assert f"Error: {TOO_LONG}" in captured.err
    assert "CW:" not in captured.out


# ── Explicitly disabled (no cw tag either): same refusal everywhere ──


@pytest.mark.asyncio
async def test_http_refuses_disabled_profile(tmp_path: Path) -> None:
    async with _web(_rigs_dir(tmp_path, DISABLED, cw_tag=False)) as s:
        status, body = await _request(
            s.port, "POST", "/api/v1/radio/cw/send", {"text": "CQ"}
        )
        assert (status, body) == (
            409,
            {"error": "unsupported_command", "message": UNSUPPORTED},
        )
        await _settle()
        assert s.model.keying_memory == {}


@pytest.mark.asyncio
async def test_websocket_refuses_disabled_profile(tmp_path: Path) -> None:
    async with _web(_rigs_dir(tmp_path, DISABLED, cw_tag=False)) as s:
        response = await _ws_command(s.port, "send_cw_text", {"text": "CQ"})
        assert (response["ok"], response["error"], response["message"]) == (
            False,
            "unsupported_command",
            UNSUPPORTED,
        )
        await _settle()
        assert s.model.keying_memory == {}


@pytest.mark.asyncio
async def test_cli_refuses_disabled_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    rigs = _rigs_dir(tmp_path, DISABLED, cw_tag=False)
    monkeypatch.setattr("rigplane.backends.yaesu_cat.radio._RIGS_DIR", rigs)
    async with _sim() as sim:
        assert await _cli(sim.device, "cw", "CQ") == 1
        await _settle()
        assert sim.model.keying_memory == {}
    captured = capsys.readouterr()
    assert f"Error: {UNSUPPORTED}" in captured.err
    assert "CW:" not in captured.out


# ── An enable without the KM limit never reaches the radio ──


@pytest.mark.asyncio
async def test_cli_refuses_a_yaesu_enable_without_a_text_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # Before #4317's gate review this profile loaded with the 512 default,
    # and 51 characters went out as an over-long KM the radio refused.
    rigs = _rigs_dir(
        tmp_path, "[cw_text]\nsupported = true\nmemory_channel = 5", cw_tag=True
    )
    monkeypatch.setattr("rigplane.backends.yaesu_cat.radio._RIGS_DIR", rigs)
    async with _sim() as sim:
        rc = await _cli(sim.device, "cw", "A" * 51)
        await _settle()
        assert sim.model.keying_memory == {}
        assert sim.model.keyer_play_requests == ()
    assert rc == 1
    captured = capsys.readouterr()
    assert re.search(
        r"^Error: \S*ftx1\.toml: \[cw_text\]\.max_length is required",
        captured.err,
        re.MULTILINE,
    ), captured.err
    assert "CW:" not in captured.out


# ── Stop follows cwText too ──


@pytest.mark.asyncio
async def test_http_stop_works_without_cw_tag(tmp_path: Path) -> None:
    async with _web(_rigs_dir(tmp_path, ENABLED, cw_tag=False)) as s:
        status, body = await _request(s.port, "POST", "/api/v1/radio/cw/stop")
        assert status == 200, body
        await _settle()
        assert s.model.keyer_play_requests == ((0, 0),)
