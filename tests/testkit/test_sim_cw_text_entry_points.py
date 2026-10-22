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
(``keying_memory``) and KY plays (``keyer_play_requests``). The pending-
send cases put ``_HoldingWire`` (fake serial line, part of the faked
hardware) between the radio object and the simulator to hold one reply.
"""

from __future__ import annotations

import asyncio
import json
import re
import struct
from collections.abc import AsyncIterator, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import rigplane.cli as rigplane_cli
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.cli import (
    _build_parser,
    _finalize_ptt_args,
    _ManagedTxRadioSession,
    _run,
)
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.rig_loader import load_rig
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import (
    attach_managed_tx_composition,
    prepare_managed_tx_observation_generation,
)

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
STOPPED = "CW text send to FTX-1 was cancelled by a CW stop"
ABORTED = "CW text send to FTX-1 was cancelled by a transmit abort"


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


async def _cli(device: str, *argv: str, model: tuple[str, ...] = ()) -> int:
    parser = _build_parser()
    args = parser.parse_args(
        ["--backend", "yaesu-cat", *model, "--serial-port", device, *argv]
    )
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
@pytest.mark.parametrize(
    "model", [(), ("--model", "FTX-1")], ids=["backend-default", "named-model"]
)
async def test_cli_refuses_a_yaesu_enable_without_a_text_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    model: tuple[str, ...],
) -> None:
    # Before #4317's gate review this profile loaded with the 512 default,
    # and 51 characters went out as an over-long KM the radio refused.
    rigs = _rigs_dir(
        tmp_path, "[cw_text]\nsupported = true\nmemory_channel = 5", cw_tag=True
    )
    monkeypatch.setattr("rigplane.backends.yaesu_cat.radio._RIGS_DIR", rigs)
    # --model resolves through discover_available_rigs(): the source-checkout
    # layout (no packaged rigs, the rigs/ directory as fallback).
    monkeypatch.setattr("rigplane.cli._rigs_dir", lambda: rigs)
    monkeypatch.setattr(
        "rigplane.profiles.rig_loader._discover_resource_rigs", lambda _root: {}
    )
    async with _sim() as sim:
        rc = await _cli(sim.device, "cw", "A" * 51, model=model)
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


# ── Force-off through the production managed-TX composition ──


class _HoldingWire:
    """The serial line between the radio object and the simulated FTX-1.

    Fake hardware only: bytes pass both ways unchanged until :attr:`armed`;
    then the first query line (``SM0;``, ``FA;`` ...) the radio sends makes
    the wire sit on the radio's replies until :attr:`release` is set. The
    transport keeps its exchange gate while it waits for that reply (up to
    its 1 s query timeout), so CW text sends submitted meanwhile stay
    pending: the first holds the send lock waiting to write ``KM``, the next
    waits for the lock.
    """

    _QUERY = re.compile(rb"^[A-Z]{2}[0-9]?;$")

    def __init__(self, device: str) -> None:
        host, port = device.removeprefix("socket://").rsplit(":", 1)
        self._upstream = (host, int(port))
        self.armed = False
        self.held = asyncio.Event()
        self.release = asyncio.Event()
        self._tasks: set[asyncio.Task[None]] = set()

    async def __aenter__(self) -> _HoldingWire:
        self._server = await asyncio.start_server(self._serve, "127.0.0.1", 0)
        port = self._server.sockets[0].getsockname()[1]
        self.device = f"socket://127.0.0.1:{port}"
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self.release.set()
        self._server.close()
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _serve(
        self, radio_reader: asyncio.StreamReader, radio_writer: asyncio.StreamWriter
    ) -> None:
        sim_reader, sim_writer = await asyncio.open_connection(*self._upstream)

        async def to_sim() -> None:
            pending = b""
            while data := await radio_reader.read(4096):
                sim_writer.write(data)
                await sim_writer.drain()
                pending += data
                while b";" in pending:
                    line, pending = pending.split(b";", 1)
                    if self.armed and self._QUERY.match(line + b";"):
                        self.held.set()

        async def to_radio() -> None:
            while data := await sim_reader.read(4096):
                if self.held.is_set():
                    await self.release.wait()
                radio_writer.write(data)
                await radio_writer.drain()

        for coro in (to_sim(), to_radio()):
            task = asyncio.create_task(coro)
            self._tasks.add(task)
        try:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        finally:
            sim_writer.close()
            radio_writer.close()


class _RejectingKmWire(_HoldingWire):
    """A serial line whose radio answers ``?;`` to a ``KM`` write.

    Fake hardware only. Once :attr:`armed`, a ``KM`` line from the radio
    object is not passed on (the simulated radio stores nothing); the wire
    first calls :attr:`on_km` (if set), lets the loop run once, then
    answers ``?;``, which the transport reads while it drains after the
    write (CAT general rules, printed p.4: ``?;`` is the radio's refusal).
    """

    def __init__(self, device: str) -> None:
        super().__init__(device)
        self.on_km: Callable[[], object] | None = None

    async def _serve(
        self, radio_reader: asyncio.StreamReader, radio_writer: asyncio.StreamWriter
    ) -> None:
        sim_reader, sim_writer = await asyncio.open_connection(*self._upstream)

        async def to_sim() -> None:
            pending = b""
            while data := await radio_reader.read(4096):
                pending += data
                while b";" in pending:
                    line, pending = pending.split(b";", 1)
                    if self.armed and line.startswith(b"KM"):
                        if self.on_km is not None:
                            self.on_km()
                        await asyncio.sleep(0)
                        radio_writer.write(b"?;")
                        await radio_writer.drain()
                        continue
                    sim_writer.write(line + b";")
                    await sim_writer.drain()

        async def to_radio() -> None:
            while data := await sim_reader.read(4096):
                radio_writer.write(data)
                await radio_writer.drain()

        for coro in (to_sim(), to_radio()):
            task = asyncio.create_task(coro)
            self._tasks.add(task)
        try:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        finally:
            sim_writer.close()
            radio_writer.close()


@asynccontextmanager
async def _managed_web(
    rigs: Path,
    tmp_path: Path,
    *,
    wire: bool = False,
    line_type: type[_HoldingWire] = _HoldingWire,
) -> AsyncIterator[SimpleNamespace]:
    """The composition order of ``rigplane web`` (cli/__init__.py: _run, _cmd_web)."""
    async with _sim() as sim, AsyncExitStack() as stack:
        line = await stack.enter_async_context(line_type(sim.device))
        radio = YaesuCatRadio(
            line.device if wire else sim.device, profile=load_rig(rigs / "ftx1.toml")
        )
        composition = ManagedTxComposition(
            radio, config_path=tmp_path / "managed-tx.json"
        )
        install_managed_tx_composition(radio, composition)
        async with _ManagedTxRadioSession(radio, composition, None):
            server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
            prepare_managed_tx_observation_generation(server)
            await composition.bind_state_store(server.command_state_store)
            attach_managed_tx_composition(server, composition)
            await server.start()
            try:
                yield SimpleNamespace(
                    port=server.port,
                    model=sim.model,
                    sim=sim.sim,
                    radio=radio,
                    wire=line,
                )
            finally:
                await server.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("memory_type", "stop"), [("text", (0, 0)), ("message", (1, 0))]
)
async def test_force_off_stops_the_keyer_memory_send_cw_text_plays(
    tmp_path: Path, memory_type: str, stop: tuple[int, int]
) -> None:
    # The managed abort is KY{P1}0; for the memory kind the profile plays
    # (CAT 2508-C printed p.18); it used to be the invalid "KY ;".
    rigs = _rigs_dir(
        tmp_path, ENABLED + f'\nmemory_type = "{memory_type}"', cw_tag=True
    )
    async with _managed_web(rigs, tmp_path) as s:
        status, body = await _request(
            s.port, "POST", "/api/v1/radio/cw/send", {"text": "CQ"}
        )
        assert (status, body) == (200, {"text": "CQ"})
        status, body = await _request(
            s.port,
            "POST",
            "/api/v1/managed-transmit/command",
            {"operation": "force_off"},
        )
        assert status == 202, body
        await _settle()
        assert s.model.keying_memory == {5: "CQ"}
        assert s.model.keyer_play_requests == ((stop[0], 5), stop)
        assert s.sim.ptt is False


# ── An unloadable profile on the audio-probe path is a readable error ──


@pytest.mark.asyncio
async def test_cli_audio_probe_reports_an_unloadable_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # `audio probe` resolves the profile registry before create_radio(); a
    # rigs directory with an incomplete Yaesu [cw_text] enable must answer
    # like every other CLI path: "Error: <file>: <key> ...", exit 1, and no
    # probe (dry run: nothing would connect anyway).
    rigs = _rigs_dir(
        tmp_path, "[cw_text]\nsupported = true\nmemory_channel = 5", cw_tag=True
    )
    monkeypatch.delenv("ICOM_SERIAL_DEVICE", raising=False)
    monkeypatch.setattr("rigplane.profiles._RIG_DIRS", [rigs])
    for name in ("_profiles", "_by_normalized", "_by_id", "_by_civ_addr"):
        monkeypatch.setattr(f"rigplane.profiles.{name}", None)
    output = tmp_path / "probe.json"
    parser = _build_parser()
    args = parser.parse_args(
        [
            "--host",
            "127.0.0.1",
            "--radio-addr",
            "0x94",
            "audio",
            "probe",
            "--dry-run",
            "--output",
            str(output),
        ]
    )
    _finalize_ptt_args(parser, args)
    rc = await _run(args)
    assert rc == 1
    captured = capsys.readouterr()
    assert re.search(
        r"^Error: \S*ftx1\.toml: \[cw_text\]\.max_length is required",
        captured.err,
        re.MULTILINE,
    ), captured.err
    assert not output.exists()


# ── A stop ends sends that are still pending (not yet on the wire) ──

_SEND = "/api/v1/radio/cw/send"
_COMMANDS = "/api/v1/commands"


async def _send_outcome(
    port: int, text: str, *, path: str = _SEND
) -> tuple[int | None, dict]:
    """POST a CW text send; ``(None, {})`` when the server closes unanswered.

    *path* ``/api/v1/commands`` sends it as the structured
    ``send_cw_text`` command instead of ``/api/v1/radio/cw/send``.
    """
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        body: dict[str, object] = {"text": text}
        if path == _COMMANDS:
            body = {"name": "send_cw_text", "params": body}
        data = json.dumps(body).encode()
        writer.write(
            f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
            f"Content-Type: application/json\r\nContent-Length: {len(data)}\r\n"
            "Connection: close\r\n\r\n".encode()
            + data
        )
        await writer.drain()
        raw = await asyncio.wait_for(reader.read(), 10)
    finally:
        writer.close()
    if not raw:
        return None, {}
    head, _, body = raw.partition(b"\r\n\r\n")
    return int(head.split(b" ", 2)[1]), json.loads(body or b"{}")


async def _ws_send_outcome(port: int, text: str) -> dict | None:
    """A WebSocket ``send_cw_text``; ``None`` when no response ever comes."""
    try:
        return await asyncio.wait_for(
            _ws_command(port, "send_cw_text", {"text": text}), 10
        )
    except (TimeoutError, asyncio.IncompleteReadError, ConnectionError, AssertionError):
        return None


async def _commands_send_outcome(port: int, text: str) -> tuple[int | None, dict]:
    """``send_cw_text`` through the structured ``POST /api/v1/commands``."""
    return await _send_outcome(port, text, path=_COMMANDS)


_OUTCOME = {
    "http": _send_outcome,
    "commands": _commands_send_outcome,
    "websocket": _ws_send_outcome,
}


async def _pending_sends(
    s: SimpleNamespace, entry: str = "http"
) -> tuple[asyncio.Task, asyncio.Task]:
    """Two sends (HTTP or WebSocket) left pending behind a held radio reply."""
    outcome = _OUTCOME[entry]
    s.wire.armed = True
    await asyncio.wait_for(s.wire.held.wait(), 5)
    first = asyncio.create_task(outcome(s.port, "AAA"))
    await asyncio.sleep(0.05)
    second = asyncio.create_task(outcome(s.port, "BBB"))
    await asyncio.sleep(0.05)
    # Observation only: the first send holds the send lock, KM not yet sent.
    assert s.radio._cw_text_lock.locked()  # noqa: SLF001
    assert s.model.keying_memory == {}
    assert not first.done() and not second.done()
    return first, second


def _ended_unsent(entry: str, how: str, message: str | None = None) -> object:
    """What the user sees for a send a stop or force-off ended (MOR-3700).

    A defined refusal on every entry point: HTTP 409 ``command_cancelled``
    (``/api/v1/radio/cw/send`` and the structured ``/api/v1/commands``,
    each in its own envelope), WebSocket ``ok: false`` with the same code
    and text. It used to be a 500
    ("Managed Yaesu CAT write is no longer current") for a stop and, for a
    force-off, a connection closed with no response at all.
    """
    if message is None:
        message = STOPPED if how == "stop" else ABORTED
    if entry == "http":
        return (409, {"error": "command_cancelled", "message": message})
    if entry == "commands":
        return (
            409,
            {"ok": False, "error": "command_cancelled", "message": message},
        )
    return {
        "type": "response",
        "id": "cw-1",
        "ok": False,
        "error": "command_cancelled",
        "message": message,
    }


async def _end_pending_sends(s: SimpleNamespace, how: str) -> tuple[int, dict]:
    """A CW stop or a managed force-off over HTTP while the sends wait."""
    if how == "stop":
        stop = asyncio.create_task(
            _request(s.port, "POST", "/api/v1/radio/cw/stop", {})
        )
    else:
        stop = asyncio.create_task(
            _request(
                s.port,
                "POST",
                "/api/v1/managed-transmit/command",
                {"operation": "force_off"},
            )
        )
    await asyncio.sleep(0.05)
    s.wire.release.set()
    return await asyncio.wait_for(stop, 10)


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["http", "commands", "websocket"])
@pytest.mark.parametrize("how", ["stop", "force_off"])
async def test_stop_ends_pending_sends_before_they_play(
    tmp_path: Path, how: str, entry: str
) -> None:
    rigs = _rigs_dir(tmp_path, ENABLED, cw_tag=True)
    async with _managed_web(rigs, tmp_path, wire=True) as s:
        first, second = await _pending_sends(s, entry)
        stopped = await _end_pending_sends(s, how)
        expected = _ended_unsent(entry, how)
        assert await asyncio.wait_for(first, 15) == expected
        assert await asyncio.wait_for(second, 15) == expected
        assert stopped[0] in (200, 202), stopped
        await _settle()
        # Neither earlier send wrote its memory or played; only the stop.
        assert s.model.keying_memory == {}
        assert s.model.keyer_play_requests == ((0, 0),)
        assert s.sim.ptt is False
        # The radio still sends after the stop.
        status, body = await _request(s.port, "POST", _SEND, {"text": "CQ"})
        assert (status, body) == (200, {"text": "CQ"})


def _km_rejected(entry: str) -> object:
    """What a send whose KM the radio refuses with ``?;`` answers (unchanged)."""
    message = "Radio rejected command 'KM5AAA;' (returned '?;')"
    if entry == "http":
        return (500, {"error": message})
    if entry == "commands":
        return (409, {"ok": False, "error": "radio_nak", "message": message})
    return {
        "type": "response",
        "id": "cw-1",
        "ok": False,
        "error": "radio_nak",
        "message": message,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("entry", ["http", "commands", "websocket"])
@pytest.mark.parametrize("stopped", [True, False], ids=["stopped", "current"])
async def test_a_km_refused_after_a_stop_is_the_stop_refusal(
    tmp_path: Path, entry: str, stopped: bool
) -> None:
    # MOR-3700 (pre-review round 2): KM passed its currency check and went
    # out; a CW stop ends the send while the transport still drains after
    # that write; then the radio answers ?;. The caller is told the stop
    # ended the send. Without the stop, the refusal keeps its old answer.
    rigs = _rigs_dir(tmp_path, ENABLED, cw_tag=True)
    async with _managed_web(rigs, tmp_path, wire=True, line_type=_RejectingKmWire) as s:
        assert isinstance(s.wire, _RejectingKmWire)
        stops: list[asyncio.Task[None]] = []
        if stopped:
            # The production radio's public stop, started the moment KM
            # reaches the line (an HTTP stop cannot be timed into the
            # drain window from outside).
            s.wire.on_km = lambda: stops.append(
                asyncio.create_task(s.radio.stop_cw_text())
            )
        s.wire.armed = True
        result = await asyncio.wait_for(_OUTCOME[entry](s.port, "AAA"), 15)
        s.wire.armed = False
        await asyncio.gather(*stops)
        assert len(stops) == int(stopped)
        if stopped:
            assert result == _ended_unsent(entry, "stop")
        else:
            assert result == _km_rejected(entry)
        await _settle()
        assert s.model.keying_memory == {}
        assert (0, 5) not in s.model.keyer_play_requests
        assert s.sim.ptt is False


@pytest.mark.asyncio
async def test_cli_send_a_stop_ends_exits_1_with_the_reason(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    # MOR-3700: the CLI's half of the cancelled-send refusal. Real argv
    # through _run and create_radio; the send waits behind a held radio
    # reply (_HoldingWire, fake serial line) when a CW stop ends it. The CLI
    # has no stop or managed force-off of its own while ``cw`` runs, so the
    # stop is the production radio's public stop_cw_text, called on the
    # radio the CLI built (captured, not replaced). It used to print the
    # transport's "Managed Yaesu CAT write is no longer current."
    rigs = _rigs_dir(tmp_path, ENABLED, cw_tag=True)
    monkeypatch.setattr("rigplane.backends.yaesu_cat.radio._RIGS_DIR", rigs)
    reached_send = asyncio.Event()
    go = asyncio.Event()
    built: list[YaesuCatRadio] = []
    real_create_radio = rigplane_cli.create_radio

    def capture_radio(config: object) -> object:
        radio = real_create_radio(config)
        assert isinstance(radio, YaesuCatRadio)
        built.append(radio)
        real_send = radio.send_cw_text

        async def send_when_told(text: str) -> None:
            # Synchronization only: the real send runs unchanged.
            reached_send.set()
            await go.wait()
            await real_send(text)

        radio.send_cw_text = send_when_told  # type: ignore[method-assign]
        return radio

    monkeypatch.setattr("rigplane.cli.create_radio", capture_radio)
    async with _sim() as sim, _HoldingWire(sim.device) as wire:
        cli = asyncio.create_task(_cli(wire.device, "cw", "AAA"))
        await asyncio.wait_for(reached_send.wait(), 20)
        (radio,) = built
        wire.armed = True
        query = asyncio.create_task(radio.get_freq())
        await asyncio.wait_for(wire.held.wait(), 5)
        go.set()
        await asyncio.sleep(0.05)
        assert not cli.done()
        stop = asyncio.create_task(radio.stop_cw_text())
        await asyncio.sleep(0.05)
        wire.release.set()
        assert await asyncio.wait_for(cli, 15) == 1
        # The stop's own KY queues behind the send and may find the CLI
        # already disconnected; only the send's outcome matters here.
        await asyncio.gather(stop, query, return_exceptions=True)
        await _settle()
        assert sim.model.keying_memory == {}
        assert (0, 5) not in sim.model.keyer_play_requests
    err = capsys.readouterr().err
    assert f"Error: {STOPPED}\n" in err, err


# ── Text a CAT parameter cannot carry: refused alike, nothing written ──

_EMPTY = "CW text is empty"
_BAD_CHAR = "CW text cannot contain ';' or control characters"
_INVALID_TEXT = [
    pytest.param("", _EMPTY, id="empty"),
    pytest.param("CQ;TEST", _BAD_CHAR, id="semicolon"),
    pytest.param("CQ\x07", _BAD_CHAR, id="control-BEL"),
    pytest.param("CQ\tDE", _BAD_CHAR, id="control-TAB"),
    pytest.param("CQ\x7f", _BAD_CHAR, id="DEL"),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("text", "message"), _INVALID_TEXT)
async def test_http_refuses_text_a_cat_parameter_cannot_carry(
    tmp_path: Path, text: str, message: str
) -> None:
    async with _web(_rigs_dir(tmp_path, ENABLED, cw_tag=True)) as s:
        status, body = await _request(s.port, "POST", _SEND, {"text": text})
        assert (status, body) == (
            400,
            {"error": "invalid_request", "message": message},
        )
        await _settle()
        assert s.model.keying_memory == {}
        assert s.model.keyer_play_requests == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(("text", "message"), _INVALID_TEXT)
async def test_websocket_refuses_text_a_cat_parameter_cannot_carry(
    tmp_path: Path, text: str, message: str
) -> None:
    async with _web(_rigs_dir(tmp_path, ENABLED, cw_tag=True)) as s:
        response = await _ws_command(s.port, "send_cw_text", {"text": text})
        assert (response["ok"], response["error"], response["message"]) == (
            False,
            "command_failed",
            message,
        )
        await _settle()
        assert s.model.keying_memory == {}
        assert s.model.keyer_play_requests == ()


@pytest.mark.asyncio
@pytest.mark.parametrize(("text", "message"), _INVALID_TEXT)
async def test_cli_refuses_text_a_cat_parameter_cannot_carry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    text: str,
    message: str,
) -> None:
    rigs = _rigs_dir(tmp_path, ENABLED, cw_tag=True)
    monkeypatch.setattr("rigplane.backends.yaesu_cat.radio._RIGS_DIR", rigs)
    async with _sim() as sim:
        assert await _cli(sim.device, "cw", text) == 1
        await _settle()
        assert sim.model.keying_memory == {}
        assert sim.model.keyer_play_requests == ()
    captured = capsys.readouterr()
    assert f"Error: {message}" in captured.err
    assert "CW:" not in captured.out
