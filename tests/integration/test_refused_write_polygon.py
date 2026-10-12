"""Refused-write polygon: one injected CI-V NAK, every caller surface.

A set-frequency write (CI-V ``0x05``) the radio refuses with a bare NAK
(``FE FE E0 <addr> FA FD``) is driven end to end through the production
stack — ``WebServer`` / ``ControlHandler`` / ``RadioPoller`` /
``RigctldServer`` / ``IcomRadio`` over the ``MockIcomRadio`` LAN simulator
(the composition ``scripts/soak/soak_driver.py: _start_simulator_stack``
serves, without its unsolicited CI-V stream so the radio's frequency
stays deterministic) — and each surface the caller reads is pinned.

The run recorded in MOR-3263 showed two separate causes. (a) The WS/HTTP
reply used to be produced at ENQUEUE: ``web/handlers/control.py:
ControlHandler._enqueue_rc_frequency`` handles ``set_freq`` with
``q.put(SetFreq(...))`` and returns before ``RadioPoller`` runs anything,
and ``core/command_service.py: CommandService.execute`` acknowledges at
that point — so the reply answered ``ok: true`` (HTTP 200) immediately
(MOR-3004 part 1). The HTTP reply now waits for poller execution through
the batch step path (``web/server.py: WebServer._execute_http_prepared_step``),
so the HTTP reply test below pins ``409`` ``radio_nak`` plainly. The WS
``response`` frame stays the enqueue acknowledgement — awaiting in the WS
read loop would block a later ``ptt_off`` from the same client and break
last-value-wins tuning — and the outcome arrives as the existing
``command_lifecycle`` frame correlated by ``commandId``, which the renamed
WS test pins plainly. (b) The MAIN-receiver
``0x05`` write itself used to be dispatched blind
(``runtime/_dual_rx_runtime.py: _set_frequency_main`` →
``_send_civ_raw(wait_response=False)``), so the NAK settled an ACK sink
and never failed the command; it now waits for the radio's answer and
raises ``CommandRejectedError`` on a NAK, which is why the lifecycle,
``RPRT -9`` and overlay-expiry tests below pass plainly: the command
lifecycle reaches ``failed`` and expires the pending overlay, and
``RigctldHandler.execute`` maps ``CommandRejectedError`` to ``RPRT -9``.
What also holds and is pinned plainly: the browser-visible state snapshot
keeps the radio's frequency, and the optimistic overlay outlives the NAK
only up to its bounded pending TTL, after which the rigctld read-back
answers the radio's frequency again.

The NAK itself comes from the simulator's opt-in refusal hook
(``rigplane_testkit/icom/simulator.py: MockIcomRadio
.refused_civ_commands``), pinned by the unit tests at the top of this
module: default off, refuses the whole command byte, scopes to a
sub-command when one is registered, and counts every refused frame in
``MockIcomRadio.refused_count``. Every refusal-dependent test below
first waits for that counter to move (``TimeoutError``, never a silent
pass) so a refusal that never reached the radio FAILS loudly instead of
xfailing.
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


def test_refusal_hook_counts_only_refused_frames() -> None:
    quiet = MockIcomRadio()
    quiet._dispatch_civ(0x05, bytes.fromhex("0040070700"), 0xE0)  # noqa: SLF001
    assert quiet.refused_count == 0

    sim = MockIcomRadio()
    sim.refused_civ_commands.add((0x05, None))
    sim._dispatch_civ(0x05, bytes.fromhex("0040070700"), 0xE0)  # noqa: SLF001
    sim._dispatch_civ(0x15, b"\x02", 0xE0)  # noqa: SLF001

    assert sim.refused_count == 1


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


async def _ws_connect_loud(
    host: str, port: int, path: str
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, str]:
    """Open a WebSocket session; a failed handshake is setup noise, not a pin."""
    try:
        return await _ws_connect(host, port, path)
    except AssertionError as exc:
        raise RuntimeError(f"websocket handshake failed: {exc}") from exc


async def _ws_read(
    reader: asyncio.StreamReader,
    match: Callable[[dict[str, Any]], bool],
    *,
    timeout: float = 5.0,
) -> dict[str, Any]:
    """Read WS frames until one matches; a missing frame is harness noise."""
    deadline = time.monotonic() + timeout
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0.0:
            raise TimeoutError("timed out waiting for the matched WebSocket frame")
        _, payload = await _ws_recv_frame(reader, timeout=remaining)
        message = json.loads(payload)
        if match(message):
            return message


async def _wait_known_rf_state(stack: _Stack) -> None:
    """Wait out the rigctld startup window; a stuck window is harness noise."""
    try:
        await wait_for_known_rf_state(stack.rigctld)
    except AssertionError as exc:
        raise TimeoutError(f"RF state never left UNKNOWN: {exc}") from exc


async def _require_refusal(sim: MockIcomRadio, before: int) -> None:
    """Wait until the simulator refused one more CI-V frame.

    Loud positive control: raises ``TimeoutError`` (never ``AssertionError``)
    when the refusal never reached the radio, so the caller FAILS instead of
    xfailing vacuously.
    """
    deadline = time.monotonic() + 5.0
    while sim.refused_count <= before:
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"no refused CI-V frame reached the simulator "
                f"(refused_count stayed {before})"
            )
        await asyncio.sleep(0.02)


async def _freq_observed_at(port: int) -> float:
    """Read the frequency field's observation time from the public snapshot.

    ``fieldStatus["main.freqHz"].lastObservedMonotonic`` moves only when a
    frequency observation applies (``core/state_store.py: StateStore``
    rewrites the field entry's time on every applied observation), so it
    names the frequency path itself rather than any applied observation.
    Transport / shape trouble is loud.
    """
    status, body = await _http_json(port, "GET", "/api/v1/state")
    if status != 200:
        raise RuntimeError(f"GET /api/v1/state answered HTTP {status}")
    try:
        return float(body["fieldStatus"]["main.freqHz"]["lastObservedMonotonic"])
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(
            f"/api/v1/state has no main.freqHz observation time: {exc}"
        ) from exc


async def _require_freq_observation_advance(port: int, after: float) -> None:
    """Wait for one frequency read to apply after the observed timestamp.

    A larger ``lastObservedMonotonic`` on ``main.freqHz`` proves the poller
    completed a frequency poll after ``after`` was read. Raises
    ``TimeoutError`` when the frequency path stalls.
    """
    deadline = time.monotonic() + 10.0
    while True:
        seen = await _freq_observed_at(port)
        if seen > after:
            return
        if time.monotonic() >= deadline:
            raise TimeoutError(
                f"no frequency observation applied after {after} within 10 s"
            )
        await asyncio.sleep(0.05)


@contextlib.asynccontextmanager
async def _ws_set_freq_refused(
    stack: _Stack, command_id: str, *, subscribe: bool = False
) -> AsyncIterator[asyncio.StreamReader]:
    """Send one WebSocket set_freq the radio will NAK, over a live session."""
    reader, writer, _ = await _ws_connect_loud(
        "127.0.0.1", stack.web.port, "/api/v1/ws"
    )
    try:
        with _refusing_set_frequency(stack.sim):
            if subscribe:
                # The sender loop forwards non-notification, non-state
                # payloads (including command_lifecycle) only to subscribed
                # streams — subscribe first, like the frontend does.
                await _ws_send_text(
                    writer,
                    json.dumps({"type": "subscribe", "streams": ["events"]}),
                )
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


async def test_ws_refused_write_ends_lifecycle_failed(stack: _Stack) -> None:
    command_id = "polygon-ws-nak"
    refused_before = stack.sim.refused_count
    async with _ws_set_freq_refused(stack, command_id):
        # The NAK was served, then a frequency read applied after it: the
        # command had every chance to fail, so the lifecycle below is settled.
        await _require_refusal(stack.sim, refused_before)
        await _require_freq_observation_advance(
            stack.web.port, await _freq_observed_at(stack.web.port)
        )

    events = [
        event
        for event in stack.web.command_service.lifecycle_events()
        if event.command_id == command_id
    ]
    assert events[-1].state == "failed"
    assert events[-1].source == "websocket"


async def test_ws_refused_write_outcome_frame_reports_failure(
    stack: _Stack,
) -> None:
    command_id = "polygon-ws-nak-outcome"
    refused_before = stack.sim.refused_count
    async with _ws_set_freq_refused(stack, command_id, subscribe=True) as reader:
        # The reply acknowledges enqueue only: the command has not executed
        # yet, so it still answers ok: true here by contract.
        response = await _ws_read(
            reader,
            lambda message: (
                message.get("type") == "response" and message.get("id") == command_id
            ),
        )
        assert response["ok"] is True
        await _require_refusal(stack.sim, refused_before)
        # The outcome arrives as the lifecycle frame correlated by
        # commandId. A missing frame raises TimeoutError (loud), never hangs.
        outcome = await _ws_read(
            reader,
            lambda message: (
                message.get("type") == "command_lifecycle"
                and message.get("commandId") == command_id
                and message.get("state") == "failed"
            ),
            timeout=10.0,
        )
    assert outcome["commandId"] == command_id
    assert outcome["state"] == "failed"


async def test_http_refused_write_ends_lifecycle_failed(stack: _Stack) -> None:
    refused_before = stack.sim.refused_count
    with _refusing_set_frequency(stack.sim):
        status, _ = await _http_json(
            stack.web.port,
            "POST",
            "/api/v1/commands",
            {
                "name": "set_freq",
                "params": {"freq": REFUSED_WEB_FREQ, "receiver": 0},
                "id": "polygon-http-nak",
            },
        )
        if status not in (200, 409):
            raise RuntimeError(f"POST /api/v1/commands answered HTTP {status}")
        await _require_refusal(stack.sim, refused_before)
        await _require_freq_observation_advance(
            stack.web.port, await _freq_observed_at(stack.web.port)
        )

    # The single command runs the batch step path, which tracks queued
    # (non-descriptor) commands under a generated lifecycle id — the
    # request id is only echoed in the reply. This test posts exactly one
    # HTTP command, so its outcome is the last http-source event.
    events = [
        event
        for event in stack.web.command_service.lifecycle_events()
        if event.source == "http"
    ]
    assert events[-1].state == "failed"


async def test_http_refused_write_reply_reports_failure(stack: _Stack) -> None:
    refused_before = stack.sim.refused_count
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
        await _require_refusal(stack.sim, refused_before)
        await _require_freq_observation_advance(
            stack.web.port, await _freq_observed_at(stack.web.port)
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
        if status != 200:
            raise RuntimeError(f"GET /api/v1/state answered HTTP {status}")
        if state["main"]["freqHz"] == RADIO_FREQ:
            break
        if time.monotonic() >= deadline:
            raise TimeoutError("state snapshot never showed the radio freq")
        await asyncio.sleep(0.05)

    refused_before = stack.sim.refused_count
    async with _ws_set_freq_refused(stack, "polygon-ws-state"):
        # Discriminate "refused" from "not polled yet": the NAK was served
        # and a frequency read applied after it, so the write had its
        # chance to take effect before the snapshot below is read.
        await _require_refusal(stack.sim, refused_before)
        await _require_freq_observation_advance(
            stack.web.port, await _freq_observed_at(stack.web.port)
        )

        status, state = await _http_json(stack.web.port, "GET", "/api/v1/state")
    if status != 200:
        raise RuntimeError(f"GET /api/v1/state answered HTTP {status}")
    assert state["main"]["freqHz"] == RADIO_FREQ


# ---------------------------------------------------------------------------
# rigctld surface and the optimistic overlay
# ---------------------------------------------------------------------------


async def _open_rigctld_client(stack: _Stack) -> RigctldClient:
    reader, writer = await asyncio.open_connection("127.0.0.1", stack.rigctld_port)
    return RigctldClient(reader, writer)


async def test_rigctld_refused_set_freq_answers_negative_rprt(
    stack: _Stack,
) -> None:
    client = await _open_rigctld_client(stack)
    try:
        await _wait_known_rf_state(stack)
        refused_before = stack.sim.refused_count
        with _refusing_set_frequency(stack.sim):
            reply = await client.send(f"F {REFUSED_RIGCTLD_FREQ}", read_timeout=5.0)
            await _require_refusal(stack.sim, refused_before)
        assert reply == "RPRT -9"
    finally:
        await client.close()


async def test_rigctld_refused_write_overlay_expires_on_nak(stack: _Stack) -> None:
    client = await _open_rigctld_client(stack)
    try:
        await _wait_known_rf_state(stack)
        baseline = await client.send("f", read_timeout=5.0)
        if baseline != str(RADIO_FREQ):
            raise RuntimeError(
                f"rigctld baseline read {baseline!r}, not the radio freq"
            )

        refused_before = stack.sim.refused_count
        with _refusing_set_frequency(stack.sim):
            reply = await client.send(f"F {REFUSED_RIGCTLD_FREQ}", read_timeout=5.0)
            await _require_refusal(stack.sim, refused_before)
        assert reply.startswith("RPRT")

        readback = await client.send("f", read_timeout=5.0)
        assert readback == str(RADIO_FREQ)
    finally:
        await client.close()


async def test_rigctld_overlay_expires_within_bounded_ttl(stack: _Stack) -> None:
    client = await _open_rigctld_client(stack)
    try:
        await _wait_known_rf_state(stack)
        baseline = await client.send("f", read_timeout=5.0)
        if baseline != str(RADIO_FREQ):
            raise RuntimeError(
                f"rigctld baseline read {baseline!r}, not the radio freq"
            )

        refused_before = stack.sim.refused_count
        with _refusing_set_frequency(stack.sim):
            await client.send(f"F {REFUSED_RIGCTLD_FREQ}", read_timeout=5.0)
            await _require_refusal(stack.sim, refused_before)

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
