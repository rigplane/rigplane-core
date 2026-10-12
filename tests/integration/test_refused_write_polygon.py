"""Refused-write polygon: one injected CI-V NAK, every caller surface.

A set-frequency write (CI-V ``0x05``) the radio refuses with a bare NAK
(``FE FE E0 <addr> FA FD``) is driven end to end through the production
stack — ``WebServer`` / ``ControlHandler`` / ``RadioPoller`` /
``RigctldServer`` / ``IcomRadio`` over the ``MockIcomRadio`` LAN simulator
(the composition ``scripts/soak/soak_driver.py: _start_simulator_stack``
serves, without its unsolicited CI-V stream so the radio's frequency
stays deterministic) — and each surface the caller reads is pinned.

Today's observed behaviour (the run recorded in MOR-3263): a MAIN-receiver
``0x05`` write is dispatched blind (``runtime/_dual_rx_runtime.py:
_set_frequency_main`` → ``wait_response=False``), so the NAK settles an
ACK sink and never reaches the caller — the command lifecycle stops at
``acknowledged``, ``F`` answers ``RPRT 0``, and the web replies ``ok``
immediately. Every assertion of the correct refusal reporting below is
therefore ``xfail(strict=True)`` with ticket MOR-3004 (beta known
limitation: "A write the radio refuses can still answer ``ok`` to the
caller. The refusal shows only in the server log."). What holds today and
is pinned plainly: the browser-visible state snapshot keeps the radio's
frequency, and the optimistic overlay outlives the NAK only up to its
bounded pending TTL, after which the rigctld read-back answers the
radio's frequency again.

The NAK itself comes from the simulator's opt-in refusal hook
(``rigplane_testkit/icom/simulator.py: MockIcomRadio
.refused_civ_commands``), pinned by the unit tests at the top of this
module: default off, refuses the whole command byte, and scopes to a
sub-command when one is registered.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane.runtime.radio import IcomRadio
from rigplane.web.server import WebConfig, WebServer
from rigplane_testkit.icom import MockIcomRadio

from _ptt_reread_fixtures import wait_for_known_rf_state
from test_rigctld_wsjtx import RigctldClient
from test_web_server import (
    _read_http_response,
    _ws_connect,
    _ws_recv_frame,
    _ws_send_text,
)

pytestmark = [pytest.mark.integration, pytest.mark.mock_integration]

RADIO_FREQ = 14_074_000
REFUSED_WEB_FREQ = 7_074_000
REFUSED_RIGCTLD_FREQ = 21_074_000

_ACK_FRAME = bytes.fromhex("fe fee098fbfd")
_NAK_FRAME = bytes.fromhex("fe fee098fafd")

_PENDING_TTL_S = 2.0


# ---------------------------------------------------------------------------
# Refusal hook unit pins (no sockets: the simulator's CI-V dispatcher)
# ---------------------------------------------------------------------------


def test_refusal_hook_default_off_still_acks_set_frequency() -> None:
    sim = MockIcomRadio()
    payload = bytes.fromhex("0040070700")

    reply = sim._dispatch_civ(0x05, payload, 0xE0)  # noqa: SLF001

    assert reply == _ACK_FRAME
    assert sim._frequency == 7_074_000  # noqa: SLF001


def test_refusal_hook_answers_nak_and_keeps_radio_state() -> None:
    sim = MockIcomRadio()
    sim.refused_civ_commands.add((0x05, None))
    payload = bytes.fromhex("0040070700")

    reply = sim._dispatch_civ(0x05, payload, 0xE0)  # noqa: SLF001

    assert reply == _NAK_FRAME
    assert sim._frequency == 14_074_000  # noqa: SLF001


def test_refusal_hook_scopes_to_the_subcommand() -> None:
    sim = MockIcomRadio()
    sim.refused_civ_commands.add((0x15, 0x13))

    refused = sim._dispatch_civ(0x15, b"\x13", 0xE0)  # noqa: SLF001
    served = sim._dispatch_civ(0x15, b"\x02", 0xE0)  # noqa: SLF001

    assert refused == _NAK_FRAME
    assert served is not None and served != _NAK_FRAME


# ---------------------------------------------------------------------------
# Production stack over the simulator
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Stack:
    sim: MockIcomRadio
    radio: IcomRadio
    web: WebServer
    rigctld: RigctldServer

    @property
    def rigctld_port(self) -> int:
        return int(self.rigctld._server.sockets[0].getsockname()[1])  # noqa: SLF001


@pytest.fixture
async def stack() -> AsyncIterator[_Stack]:
    sim = MockIcomRadio(single_owner=True)
    await sim.start()
    radio = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="polygon",
        password="polygon",
        timeout=1.0,
        model="IC-7610",
    )
    await radio.connect()
    web = WebServer(radio, WebConfig(host="127.0.0.1", port=0, discovery=False))
    await web.start()
    rigctld = RigctldServer(
        radio, RigctldConfig(host="127.0.0.1", port=0, command_timeout=10.0)
    )
    await rigctld.start()
    try:
        yield _Stack(sim=sim, radio=radio, web=web, rigctld=rigctld)
    finally:
        await rigctld.stop()
        await web.stop()
        with contextlib.suppress(Exception):
            await radio.disconnect()
        await sim.stop()


@contextlib.contextmanager
def _refusing_set_frequency(sim: MockIcomRadio) -> Iterator[None]:
    sim.refused_civ_commands.add((0x05, None))
    try:
        yield
    finally:
        sim.refused_civ_commands.discard((0x05, None))


async def _http_json(
    port: int, method: str, path: str, body: dict[str, Any] | None = None
) -> tuple[int, Any]:
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        payload = b"" if body is None else json.dumps(body).encode()
        writer.write(
            (
                f"{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n"
            ).encode()
            + payload
        )
        await writer.drain()
        status, _, raw = await asyncio.wait_for(_read_http_response(reader), 5.0)
        return status, json.loads(raw)
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


async def _ws_read(
    reader: asyncio.StreamReader,
    match: Callable[[dict[str, Any]], bool],
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        assert remaining > 0.0, "timed out waiting for the matched WebSocket frame"
        try:
            _, payload = await _ws_recv_frame(reader, timeout=remaining)
        except TimeoutError:
            raise AssertionError(
                "timed out waiting for the matched WebSocket frame"
            ) from None
        message = json.loads(payload)
        if match(message):
            return message


async def _wait_lifecycle_failed(web: WebServer, command_id: str) -> None:
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        events = [
            event
            for event in web.command_service.lifecycle_events()
            if event.command_id == command_id
        ]
        if events and events[-1].state == "failed":
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"command {command_id!r} never reached lifecycle state failed")


async def _ws_set_freq_refused(stack: _Stack, command_id: str) -> None:
    """Send one WebSocket set_freq the radio will NAK, over a live session."""
    reader, writer, _ = await _ws_connect("127.0.0.1", stack.web.port, "/api/v1/ws")
    try:
        with _refusing_set_frequency(stack.sim):
            await _ws_send_text(
                writer,
                json.dumps(
                    {
                        "type": "cmd",
                        "id": command_id,
                        "name": "set_freq",
                        "params": {"freq": REFUSED_WEB_FREQ, "receiver": 0},
                    }
                ),
            )
            yield reader
    finally:
        writer.close()
        with contextlib.suppress(Exception):
            await writer.wait_closed()


# ---------------------------------------------------------------------------
# Web surface
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "MOR-3004: the MAIN-receiver 0x05 write is dispatched blind, the NAK "
        "settles an ACK sink, and the lifecycle stops at acknowledged — the "
        "issuer never receives a command_lifecycle failed frame"
    ),
)
async def test_ws_refused_write_ends_lifecycle_failed(stack: _Stack) -> None:
    command_id = "polygon-ws-nak"
    async for reader in _ws_set_freq_refused(stack, command_id):
        await _ws_read(
            reader,
            lambda message: (
                message.get("type") == "command_lifecycle"
                and message.get("commandId") == command_id
                and message.get("state") == "failed"
            ),
        )
        await _wait_lifecycle_failed(stack.web, command_id)

    events = [
        event
        for event in stack.web.command_service.lifecycle_events()
        if event.command_id == command_id
    ]
    assert [event.state for event in events] == [
        "accepted",
        "queued",
        "sent",
        "acknowledged",
        "failed",
    ]
    assert events[-1].source == "websocket"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="MOR-3004: the WebSocket response frame answers ok: true immediately",
)
async def test_ws_refused_write_reply_reports_failure(stack: _Stack) -> None:
    async for reader in _ws_set_freq_refused(stack, "polygon-ws-nak-reply"):
        response = await _ws_read(
            reader,
            lambda message: (
                message.get("type") == "response"
                and message.get("id") == "polygon-ws-nak-reply"
            ),
        )
    assert response["ok"] is False
    assert response["error"] == "radio_nak"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "MOR-3004: the blind 0x05 write is acknowledged at enqueue and the "
        "lifecycle never reaches failed for the HTTP-sourced command either"
    ),
)
async def test_http_refused_write_ends_lifecycle_failed(stack: _Stack) -> None:
    with _refusing_set_frequency(stack.sim):
        await _http_json(
            stack.web.port,
            "POST",
            "/api/v1/commands",
            {
                "name": "set_freq",
                "params": {"freq": REFUSED_WEB_FREQ, "receiver": 0},
                "id": "polygon-http-nak",
            },
        )
        await _wait_lifecycle_failed(stack.web, "polygon-http-nak")

    events = [
        event
        for event in stack.web.command_service.lifecycle_events()
        if event.command_id == "polygon-http-nak"
    ]
    assert events[-1].state == "failed"
    assert events[-1].source == "http"


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="MOR-3004: the HTTP reply is 200 with ok: true immediately",
)
async def test_http_refused_write_reply_reports_failure(stack: _Stack) -> None:
    with _refusing_set_frequency(stack.sim):
        status, body = await _http_json(
            stack.web.port,
            "POST",
            "/api/v1/commands",
            {
                "name": "set_freq",
                "params": {"freq": REFUSED_WEB_FREQ, "receiver": 0},
                "id": "polygon-http-nak-reply",
            },
        )
    assert status == 409
    assert body["ok"] is False
    assert body["error"] == "radio_nak"


async def test_web_state_keeps_radio_value_after_refused_write(
    stack: _Stack,
) -> None:
    deadline = time.monotonic() + 5.0
    while True:
        status, state = await _http_json(stack.web.port, "GET", "/api/v1/state")
        assert status == 200
        if state["main"]["freqHz"] == RADIO_FREQ:
            break
        assert time.monotonic() < deadline, "state snapshot never showed the radio freq"

    async for _reader in _ws_set_freq_refused(stack, "polygon-ws-state"):
        await asyncio.sleep(0.2)

    status, state = await _http_json(stack.web.port, "GET", "/api/v1/state")
    assert status == 200
    assert state["main"]["freqHz"] == RADIO_FREQ


# ---------------------------------------------------------------------------
# rigctld surface and the optimistic overlay
# ---------------------------------------------------------------------------


async def _open_rigctld_client(stack: _Stack) -> RigctldClient:
    reader, writer = await asyncio.open_connection("127.0.0.1", stack.rigctld_port)
    return RigctldClient(reader, writer)


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="MOR-3004: the blind 0x05 write is reported as sent-uncontested, RPRT 0",
)
async def test_rigctld_refused_set_freq_answers_negative_rprt(
    stack: _Stack,
) -> None:
    client = await _open_rigctld_client(stack)
    try:
        await wait_for_known_rf_state(stack.rigctld)
        with _refusing_set_frequency(stack.sim):
            reply = await client.send(f"F {REFUSED_RIGCTLD_FREQ}", read_timeout=5.0)
        assert reply == "RPRT -9"
    finally:
        await client.close()


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "MOR-3004: nothing fails the command on the NAK, so the pending "
        "overlay keeps projecting the refused target (observed: f answers "
        "the refused 21074000) until its TTL lapses"
    ),
)
async def test_rigctld_refused_write_overlay_expires_on_nak(stack: _Stack) -> None:
    client = await _open_rigctld_client(stack)
    try:
        await wait_for_known_rf_state(stack.rigctld)
        baseline = await client.send("f", read_timeout=5.0)
        assert baseline == str(RADIO_FREQ)

        with _refusing_set_frequency(stack.sim):
            reply = await client.send(f"F {REFUSED_RIGCTLD_FREQ}", read_timeout=5.0)
        assert reply.startswith("RPRT")

        readback = await client.send("f", read_timeout=5.0)
        assert readback == str(RADIO_FREQ)
    finally:
        await client.close()


async def test_rigctld_overlay_expires_within_bounded_ttl(stack: _Stack) -> None:
    client = await _open_rigctld_client(stack)
    try:
        await wait_for_known_rf_state(stack.rigctld)
        baseline = await client.send("f", read_timeout=5.0)
        assert baseline == str(RADIO_FREQ)

        with _refusing_set_frequency(stack.sim):
            await client.send(f"F {REFUSED_RIGCTLD_FREQ}", read_timeout=5.0)

        deadline = time.monotonic() + _PENDING_TTL_S + 4.0
        while True:
            readback = await client.send("f", read_timeout=5.0)
            if readback == str(RADIO_FREQ):
                break
            assert time.monotonic() < deadline, (
                f"read-back still {readback!r} past the pending overlay TTL"
            )
            await asyncio.sleep(0.2)
    finally:
        await client.close()
