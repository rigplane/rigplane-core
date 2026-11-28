"""MOR-4885: new local CW intent during a real serial identity recovery."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from rigplane.backends import _icom_serial_base
from rigplane.backends.icom7610.drivers import serial_civ_link
from rigplane.cli import _ManagedTxRadioSession
from rigplane.core import civ as civ_module
from rigplane.core.exceptions import StaleBeforeWireError
from rigplane.runtime import _civ_rx
from rigplane.runtime import radio as radio_module
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.testing.timing import VirtualClock, await_until
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import (
    attach_managed_tx_composition,
    prepare_managed_tx_observation_generation,
)

from rigplane_testkit.serial import CivSerialFake, civ_serial
from test_serial_backend_smoke import _ws_connect, _ws_recv_frame
from testkit.test_serial_usb_silence_recovery import _poll_loop, _radio_for
from testkit.test_sim_cw_text_civ_long_text import _AckSendCw
from testkit.test_sim_cw_text_entry_points import _ws_send_text
from testkit.test_sim_ftx1_send_cw_text import _request

_OUTAGE_TEXT = "MOR4885 OUTAGE"
_BEFORE_TEXT = "MOR4885 BEFORE"
_AFTER_TEXT = "MOR4885 AFTER"
_UNAVAILABLE = "CI-V transport unavailable before dispatch"


async def _cancel_task(task: asyncio.Task) -> None:
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


@asynccontextmanager
async def _managed_serial_web(tmp_path: Path) -> AsyncIterator[SimpleNamespace]:
    table = _AckSendCw(civ_addr=0x94)
    fake = CivSerialFake(recorded_answers=table)
    radio = _radio_for("IC-7300", fake)
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
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
                radio=radio,
                composition=composition,
                table=table,
                fake=fake,
            )
        finally:
            await server.stop()


@asynccontextmanager
async def _local_client(
    port: int, entry: str
) -> AsyncIterator[Callable[[str], Awaitable[tuple[bool, dict]]]]:
    async with AsyncExitStack() as stack:
        if entry in ("commands", "http"):

            async def send(text: str) -> tuple[bool, dict]:
                path = "/api/v1/radio/cw/send"
                payload: dict[str, Any] = {"text": text}
                if entry == "commands":
                    path = "/api/v1/commands"
                    payload = {"name": "send_cw_text", "params": payload}
                status, body = await _request(port, "POST", path, payload)
                return status == 200, body

        else:
            # Both sockets stay live across the outage; neither acquires TX ownership.
            for _ in range(2 if entry == "second_websocket" else 1):
                reader, writer = await _ws_connect("127.0.0.1", port, "/api/v1/ws")
                stack.push_async_callback(writer.wait_closed)
                stack.callback(writer.close)

            async def send(text: str) -> tuple[bool, dict]:
                await _ws_send_text(
                    writer,
                    json.dumps(
                        {
                            "type": "cmd",
                            "id": text,
                            "name": "send_cw_text",
                            "params": {"text": text},
                        }
                    ),
                )
                while True:
                    _, payload = await _ws_recv_frame(reader)
                    body = json.loads(payload)
                    if body.get("type") == "response" and body.get("id") == text:
                        return body["ok"], body

        yield send


@pytest.mark.asyncio
@pytest.mark.timeout(60)
@pytest.mark.parametrize("entry", ["commands", "http", "websocket", "second_websocket"])
async def test_local_cw_issued_during_usb_identity_recovery_never_plays_after_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entry: str
) -> None:
    monkeypatch.delenv("ICOM_CIV_RECOVERY_WAIT_TIMEOUT_S", raising=False)
    async with (
        _managed_serial_web(tmp_path) as s,
        _local_client(s.port, entry) as send,
        AsyncExitStack() as tasks,
    ):
        await await_until(
            lambda: (
                s.composition.authority.snapshot_nowait().provider_generation
                is not None
            ),
            timeout=10.0,
            description="managed serial provider never became available before the fault",
        )
        assert s.radio._civ_recovery_wait_timeout == 12.0
        stop_payload = s.radio._commands.stop_cw(to_addr=s.radio._radio_addr)[5:-1]
        ok, body = await send(_BEFORE_TEXT)
        assert ok, body
        assert [p for p in s.table.payloads if p != stop_payload] == [
            _BEFORE_TEXT.encode()
        ]
        initial_opens = s.fake.open_count
        owner_epoch = s.radio._owner_stop_epoch
        initial_civ_epoch = s.radio._civ_epoch

        async with VirtualClock.on_running_loop() as clock:
            with monkeypatch.context() as scoped:
                # All these modules keep their real asyncio API: loop timers are
                # driven by elapse(), while their monotonic reads share that clock.
                for module in (
                    _icom_serial_base,
                    serial_civ_link,
                    _civ_rx,
                    radio_module,
                    civ_module,
                    civ_serial,
                ):
                    clock.scope_to(scoped, module)
                    scoped.setattr(module, "asyncio", asyncio)

                poller = asyncio.create_task(_poll_loop(s.radio, asyncio.Event()))
                tasks.push_async_callback(_cancel_task, poller)
                s.fake.silence(30.0)
                await clock.elapse(8.0)
                radio = s.radio
                assert not radio.connected, "silent USB link never went down"
                assert s.fake.open_count > initial_opens
                assert radio._civ_epoch > initial_civ_epoch
                assert radio._civ_link_down_ever_declared
                assert radio._civ_recovering
                assert radio._ctrl_transport._udp_transport is not None
                assert radio._civ_transport is not None
                assert radio._identity_phase_owns_open_link()
                assert (
                    s.composition.authority.snapshot_nowait().provider_generation
                    is None
                )
                assert radio._owner_stop_epoch == owner_epoch

                admitted = asyncio.Event()
                fresh_admitted = asyncio.Event()
                refused_at: list[float] = []
                original_send = radio._send_civ_raw

                async def observe_send(frame: bytes, **kwargs: Any) -> Any:
                    target = frame[4] == 0x17 and frame[5:-1] == _OUTAGE_TEXT.encode()
                    if frame[4] == 0x17 and frame[5:-1] == _AFTER_TEXT.encode():
                        fresh_admitted.set()
                    if target:
                        assert not radio.connected
                        assert radio._identity_phase_owns_open_link()
                        admitted.set()
                    try:
                        return await original_send(frame, **kwargs)
                    except StaleBeforeWireError as exc:
                        if target:
                            assert str(exc) == _UNAVAILABLE
                            refused_at.append(clock.now)
                        raise

                scoped.setattr(radio, "_send_civ_raw", observe_send)
                bookmark = len(s.fake.commands)
                payload_bookmark = len(s.table.payloads)
                sending = asyncio.create_task(send(_OUTAGE_TEXT))
                tasks.push_async_callback(_cancel_task, sending)
                try:
                    await await_until(
                        admitted.is_set,
                        timeout=5.0,
                        description=f"{entry} CW never reached the real raw-send seam",
                    )
                    issued_at = clock.now
                    await clock.elapse(0.5)
                    if refused_at:
                        await await_until(
                            sending.done,
                            timeout=5.0,
                            description="guard refusal never reached the live client",
                        )
                    replied_before_restore = sending.done()
                    restored_at = clock.now
                    # Restore independently of the HTTP/WS result: main can still
                    # have a request waiting in the real 12-second recovery loop.
                    s.fake.silence(0.0)
                    await clock.elapse(3.0)
                    await await_until(
                        lambda: radio.connected,
                        timeout=5.0,
                        description="identity-owned serial link never recovered",
                    )
                    recovered_at = clock.now
                    await await_until(
                        sending.done,
                        timeout=5.0,
                        description="outage-time CW request never answered",
                    )
                    outage_ok, outage_body = await sending
                    await clock.elapse(1.0)
                    fresh = asyncio.create_task(send(_AFTER_TEXT))
                    tasks.push_async_callback(_cancel_task, fresh)
                    await await_until(
                        fresh_admitted.is_set,
                        timeout=5.0,
                        description="fresh post-recovery CW never reached the backend",
                    )
                    await clock.elapse(0.5)
                    await await_until(
                        fresh.done,
                        timeout=5.0,
                        description="fresh post-recovery CW never answered",
                    )
                    ok, body = await fresh
                    assert ok, body
                    await clock.elapse(1.0)

                    late_cw = [
                        frame.data
                        for frame in s.fake.commands[bookmark:]
                        if frame.command == 0x17
                    ]
                    assert _AFTER_TEXT.encode() in late_cw
                    assert _OUTAGE_TEXT.encode() not in late_cw, (
                        "outage-time CW reached the fake after identity recovery: "
                        f"{late_cw!r}; response={outage_body!r}"
                    )
                    assert [
                        p
                        for p in s.table.payloads[payload_bookmark:]
                        if p != stop_payload
                    ] == [_AFTER_TEXT.encode()]
                    assert refused_at and issued_at <= refused_at[0] < restored_at
                    assert replied_before_restore
                    assert not outage_ok, outage_body
                    assert _UNAVAILABLE in str(outage_body)
                    assert recovered_at - issued_at < 12.0
                    assert radio._owner_stop_epoch == owner_epoch
                    assert s.fake.ptt is False
                finally:
                    s.fake.silence(0.0)
                    if not sending.done():
                        sending.cancel()
                    await asyncio.gather(sending, return_exceptions=True)
