"""Simulated IC-705 PTT: real HTTP, WebSocket, rigctld and CI-V transport."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.tx_target import KnownTxTarget, UnknownTxTarget
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.radio import IcomRadio
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import (
    attach_managed_tx_composition,
    prepare_managed_tx_observation_generation,
)
from rigplane_testkit.icom import MockIcomRadio

from _perf_helpers import fast_connect
from test_refused_write_polygon import _http_json, _ws_read
from test_web_server import _ws_connect, _ws_send_text
from testkit.ic705_fakes import ic705_lan_fake

pytestmark = pytest.mark.mock_integration


@dataclass
class _Stack:
    sim: MockIcomRadio
    web: WebServer
    rigctld: RigctldServer


@pytest.fixture
async def stack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[_Stack]:
    sim = ic705_lan_fake(stateful_ptt=True, capture_civ_frames=True)
    # The recorded personality has a fixed split reply; extend only the
    # simulator's missing stateful 0F read/write, never the permission code.
    native = sim._dispatch_civ_native
    split = False

    def with_split(command: int, payload: bytes, sender: int) -> bytes | None:
        nonlocal split
        if command == 0x0F:
            if not payload:
                return sim._civ_frame(sender, 0xA4, command, data=bytes([split]))
            if payload in (b"\x00", b"\x01"):
                split = bool(payload[0])
                return sim._civ_ack(sender, 0xA4)
        return native(command, payload, sender)

    monkeypatch.setattr(sim, "_dispatch_civ_native", with_split)
    await sim.start()
    radio = IcomRadio(
        host="127.0.0.1",
        port=sim.control_port,
        username="simulated",
        password="simulated",
        timeout=2.0,
        model="IC-705",
    )
    composition = ManagedTxComposition(radio, config_path=tmp_path / "tx.json")
    install_managed_tx_composition(radio, composition)
    web: WebServer | None = None
    rigctld: RigctldServer | None = None
    try:
        with fast_connect():
            await radio.connect()
        web = WebServer(radio, WebConfig(host="127.0.0.1", port=0, discovery=False))
        prepare_managed_tx_observation_generation(web)
        await composition.bind_state_store(web.command_state_store)
        attach_managed_tx_composition(web, composition)
        rigctld = RigctldServer(
            radio,
            RigctldConfig(host="127.0.0.1", port=0, command_timeout=10.0),
            allow_not_ready=True,
            managed_tx_authority=composition.authority,
            command_queue=web.command_queue,
            command_service=web.command_service,
        )
        await rigctld.start()
        await asyncio.wait_for(web.start(), 25.0)
        yield _Stack(sim, web, rigctld)
    finally:
        if rigctld is not None:
            await rigctld.stop()
        if web is not None:
            await web.stop()
        with contextlib.suppress(Exception):
            await radio.disconnect()
        await composition.shutdown(termination=asyncio.Event())
        await sim.stop()


async def _command(stack: _Stack, name: str, params: dict[str, Any]) -> None:
    status, body = await _http_json(
        stack.web.port, "POST", "/api/v1/commands", {"name": name, "params": params}
    )
    assert status == 200, body


async def _wait_target(stack: _Stack, frequency: int) -> None:
    async with asyncio.timeout(15.0):
        while True:
            status, body = await _http_json(stack.web.port, "GET", "/api/v1/state")
            assert status == 200
            target = body.get("txTarget", {})
            if (
                target.get("status") == "known"
                and target.get("frequencyHz") == frequency
            ):
                return
            await asyncio.sleep(0.05)


@contextlib.asynccontextmanager
async def _client(stack: _Stack, lane: str) -> AsyncIterator[Any]:
    if lane == "http":

        async def ptt(on: bool) -> dict[str, Any]:
            status, body = await _http_json(
                stack.web.port,
                "POST",
                "/api/v1/managed-transmit/command",
                {"operation": "transmit_on" if on else "force_off"},
            )
            assert status in (202, 409), body
            return body

        yield ptt
        return
    if lane == "ws":
        reader, writer, _ = await _ws_connect("127.0.0.1", stack.web.port, "/api/v1/ws")

        async def ptt(on: bool) -> dict[str, Any]:
            command_id = f"permit-{time.monotonic_ns()}"
            await _ws_send_text(
                writer,
                json.dumps(
                    {
                        "type": "cmd",
                        "id": command_id,
                        "name": "ptt_on" if on else "ptt_off",
                        "params": {},
                    }
                ),
            )
            return await _ws_read(
                reader, lambda message: message.get("id") == command_id
            )
    else:
        assert stack.rigctld._server is not None
        port = stack.rigctld._server.sockets[0].getsockname()[1]
        reader, writer = await asyncio.open_connection("127.0.0.1", port)

        async def ptt(on: bool) -> dict[str, Any]:
            writer.write(f"T {int(on)}\n".encode())
            await writer.drain()
            reply = await asyncio.wait_for(reader.readline(), 10.0)
            return {"ok": reply == b"RPRT 0\n", "reply": reply.decode().strip()}

    try:
        yield ptt
    finally:
        writer.close()
        await writer.wait_closed()


def _assert_refused(reply: dict[str, Any], lane: str, reason: str) -> None:
    assert reply["ok"] is False, reply
    if lane == "rigctld":
        assert reply["reply"] == "RPRT -9"
    else:
        assert reply["error"] == reason


@pytest.mark.parametrize("lane", ["http", "ws", "rigctld"])
@pytest.mark.parametrize(
    "frequency,allowed",
    [
        (100_000_000, False),
        (121_500_000, False),
        (162_000_000, False),
        (146_520_000, True),
        (446_000_000, True),
        (14_074_000, True),
    ],
)
async def test_simulated_profile_frequency_permit(
    stack: _Stack,
    lane: str,
    frequency: int,
    allowed: bool,
) -> None:
    await _command(stack, "set_freq", {"freq": frequency})
    await _wait_target(stack, frequency)
    async with _client(stack, lane) as ptt:
        before = len(stack.sim.ptt_set_writes)
        reply = await ptt(True)
        if allowed:
            assert reply["ok"] is True, reply
            assert stack.sim.ptt_on
            assert any(on for _, on in stack.sim.ptt_set_writes[before:])
        else:
            _assert_refused(reply, lane, "tx_frequency_not_allowed")
            assert not stack.sim.ptt_on
            assert not any(on for _, on in stack.sim.ptt_set_writes[before:])
        assert (await ptt(False))["ok"] is True
        assert not stack.sim.ptt_on


@pytest.mark.parametrize("lane", ["http", "ws", "rigctld"])
@pytest.mark.parametrize(
    "rx,tx,allowed",
    [
        (100_000_000, 146_520_000, True),
        (146_520_000, 100_000_000, False),
    ],
)
async def test_simulated_split_uses_transmit_vfo(
    stack: _Stack,
    lane: str,
    rx: int,
    tx: int,
    allowed: bool,
) -> None:
    await _command(stack, "set_freq", {"freq": rx})
    # CI-V 25/01 writes the simulator's unselected VFO through production IO.
    bcd = bytes.fromhex(f"{tx:010d}")[::-1]
    await _command(stack, "send_civ", {"command": 0x25, "data": (b"\x01" + bcd).hex()})
    await _command(stack, "set_split", {"on": True})
    await _wait_target(stack, tx)
    async with _client(stack, lane) as ptt:
        reply = await ptt(True)
        if allowed:
            assert reply["ok"] is True, reply
            assert stack.sim.ptt_on
        else:
            _assert_refused(reply, lane, "tx_frequency_not_allowed")
            assert not stack.sim.ptt_on
        assert (await ptt(False))["ok"] is True
        assert not stack.sim.ptt_on


@pytest.mark.parametrize("lane", ["http", "ws", "rigctld"])
@pytest.mark.parametrize("fault", ["unknown", "stale"])
async def test_simulated_unknown_or_stale_target_refuses_key_allows_unkey(
    stack: _Stack,
    lane: str,
    fault: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _command(stack, "set_freq", {"freq": 146_520_000})
    await _wait_target(stack, 146_520_000)
    # Suspend this producer to model a missing/stalled TX-target observation;
    # PTT observations, the command queue and real simulator IO remain live.
    assert stack.web._radio_poller is not None
    monkeypatch.setattr(stack.web._radio_poller, "_publish_tx_target", lambda: None)
    stack.web.command_state_store.apply_current(
        Observation(
            path=FieldPath.global_("tx_state", "tx_target"),
            value=UnknownTxTarget("not-observed")
            if fault == "unknown"
            else KnownTxTarget("MAIN", "A", 146_520_000),
            source=SourceMetadata(source="local_reconcile", provider="simulated_fault"),
            timestamp_monotonic=time.monotonic(),
            max_age=10.0 if fault == "unknown" else 0.0,
        )
    )
    async with _client(stack, lane) as ptt:
        before = len(stack.sim.ptt_set_writes)
        _assert_refused(await ptt(True), lane, "tx_target_unknown")
        assert not stack.sim.ptt_on
        assert not any(on for _, on in stack.sim.ptt_set_writes[before:])
        assert (await ptt(False))["ok"] is True


async def test_simulated_http_has_no_ownerless_ptt_bypass(stack: _Stack) -> None:
    status, _ = await _http_json(
        stack.web.port,
        "POST",
        "/api/v1/commands",
        {
            "name": "ptt_on",
            "params": {},
        },
    )
    assert status >= 400
    assert not stack.sim.ptt_on
    assert not any(on for _, on in stack.sim.ptt_set_writes)
