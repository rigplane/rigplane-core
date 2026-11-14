"""Reusable real-HTTP + CAT-simulator harness for the FT-991A profile.

One function starts a production-shaped station — the committed
``rigs/ft991a.toml`` profile (unmodified, with its own acquisition
policy), a real ``YaesuCatRadio``, the real CAT simulator, and a real
``WebServer`` with its production handler, poller, store and projection —
then the same module posts commands and reads the served state over a
real socket. Every later FT-991A PR (tuner, memory, squelch, ...) reuses
this instead of reinventing the wiring; the seam assertions (served
``/api/v1/state`` values, not adapter return values) stay identical.

The waits are real-time by construction (live socket + live poller), so
consuming tests carry ``@pytest.mark.realtime`` with a reason; the
deadline uses ``asyncio.timeout`` and the poll interval is the only
sleep, both confined to :func:`wait_for_state_value`.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.profiles.rig_loader import load_rig
from rigplane.web.server import WebConfig, WebServer
from rigplane_testkit.sim.adapters.yaesu_cat import YaesuCatSerialFake

ROOT = Path(__file__).resolve().parents[2]
RIG = ROOT / "rigs/ft991a.toml"
SIM_PROFILE = ROOT / "rigplane_testkit/sim/profiles/ft991a.toml"


@dataclass
class Ft991aHttpStation:
    """A served FT-991A station: real radio, sim wire log, HTTP address."""

    radio: YaesuCatRadio
    wire: list[str] = field(default_factory=list)
    host: str = "127.0.0.1"
    port: int = 0
    server: WebServer | None = None

    async def aclose(self) -> None:
        """Stop the server and close the transport (call from ``finally``)."""
        if self.server is not None:
            await self.server.stop()
            self.server = None
        await self.radio._transport.close()


async def serve_ft991a_over_http(monkeypatch: Any) -> Ft991aHttpStation:
    """Serve the committed FT-991A profile over HTTP on the CAT simulator.

    Returns a started :class:`Ft991aHttpStation`; the caller owns
    :meth:`Ft991aHttpStation.aclose`. No patched profile copy is involved:
    the radio and the fake both load ``rigs/ft991a.toml`` unmodified.
    """
    radio = YaesuCatRadio("/dev/fixture", profile=load_rig(RIG))
    fake = YaesuCatSerialFake.from_profile(ROOT, SIM_PROFILE, rig_path=RIG)
    station = Ft991aHttpStation(radio=radio)
    wire = station.wire
    handle = fake.model.handle_with_outcome

    def logged(line: str) -> Any:
        wire.append(line)
        return handle(line)

    monkeypatch.setattr(fake.model, "handle_with_outcome", logged)

    async def open_port(*args: Any, **kwargs: Any) -> Any:
        return await fake.open_serial_connection()

    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.transport.open_serial_port", open_port
    )
    await radio._transport.connect()
    server = WebServer(
        radio,
        WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0),
    )
    await server.start()
    station.server = server
    station.port = server.port
    return station


async def _read_http_response(reader: asyncio.StreamReader) -> tuple[int, dict, str]:
    """Read one HTTP/1.x response: ``(status, headers, body)``."""
    head = await reader.readuntil(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split(" ", 2)[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" in line:
            name, _, value = line.partition(":")
            headers[name.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0"))
    body = b""
    while len(body) < length:
        chunk = await reader.read(length - len(body))
        if not chunk:
            break
        body += chunk
    return status, headers, body.decode("utf-8")


async def post_command(
    host: str, port: int, name: str, params: dict[str, Any]
) -> dict[str, Any]:
    """POST one command to ``/api/v1/commands`` over a real socket."""
    reader, writer = await asyncio.open_connection(host, port)
    try:
        payload = json.dumps({"id": "ft991a-harness", "name": name, "params": params})
        writer.write(
            (
                "POST /api/v1/commands HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                "Connection: close\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(payload.encode())}\r\n\r\n"
            ).encode()
            + payload.encode()
        )
        await writer.drain()
        status, _, body = await asyncio.wait_for(_read_http_response(reader), 10)
    finally:
        writer.close()
        await writer.wait_closed()
    assert status == 200
    response = json.loads(body)
    assert response["ok"] is True
    return response


async def read_info(host: str, port: int) -> dict[str, Any]:
    """GET the public ``/api/v1/info`` over a real socket."""
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(
            (
                "GET /api/v1/info HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                "Connection: close\r\n\r\n"
            ).encode()
        )
        await writer.drain()
        status, _, body = await asyncio.wait_for(_read_http_response(reader), 10)
    finally:
        writer.close()
        await writer.wait_closed()
    assert status == 200
    return json.loads(body)


async def read_capabilities(host: str, port: int) -> dict[str, Any]:
    """GET the public ``/api/v1/capabilities`` over a real socket."""
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(
            (
                "GET /api/v1/capabilities HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                "Connection: close\r\n\r\n"
            ).encode()
        )
        await writer.drain()
        status, _, body = await asyncio.wait_for(_read_http_response(reader), 10)
    finally:
        writer.close()
        await writer.wait_closed()
    assert status == 200
    return json.loads(body)


async def read_state(host: str, port: int) -> dict[str, Any]:
    """GET the user-visible ``/api/v1/state`` over a real socket."""
    reader, writer = await asyncio.open_connection(host, port)
    try:
        writer.write(
            (
                "GET /api/v1/state HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                "Connection: close\r\n\r\n"
            ).encode()
        )
        await writer.drain()
        status, _, body = await asyncio.wait_for(_read_http_response(reader), 10)
    finally:
        writer.close()
        await writer.wait_closed()
    assert status == 200
    return json.loads(body)


async def wait_for_state_value(
    host: str,
    port: int,
    key: str,
    want: Any,
    *,
    timeout: float = 10.0,
) -> Any:
    """Wait until top-level ``state[key] == want``; return the observed value.

    The deadline is an ``asyncio.timeout`` bound for a stuck run, not the
    expected wait; the only sleep is the poll interval.
    """
    async with asyncio.timeout(timeout):
        while True:
            value = (await read_state(host, port)).get(key)
            if value == want:
                return value
            await asyncio.sleep(0.05)
