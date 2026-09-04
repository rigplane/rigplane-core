import asyncio
import json
from collections.abc import Callable
from typing import Any

import pytest

from rigplane.backends.rigctld_client.radio import RigctldClientRadio
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.profiles import resolve_radio_profile
from rigplane.core.state_store import StateStore
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.radio import CoreRadio
from rigplane.runtime.managed_tx_state import ManagedTxIntentKind
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import attach_managed_tx_composition


class _DelayedWire:
    def __init__(self) -> None:
        self.on_started = asyncio.Event()
        self.release_on = asyncio.Event()
        self.wire: list[bool] = []
        self.wire_changed = asyncio.Event()
        self.raw_ptt: list[bool] = []

    async def finish(self, on: bool, is_current: Callable[[], bool]) -> None:
        if on:
            self.on_started.set()
            try:
                await self.release_on.wait()
            except asyncio.CancelledError:
                await self.release_on.wait()
        if is_current():
            self.wire.append(on)
            self.wire_changed.set()

    async def wait_for_wire(self, expected: list[bool]) -> None:
        async def wait() -> None:
            while self.wire != expected:
                self.wire_changed.clear()
                if self.wire != expected:
                    await self.wire_changed.wait()

        await asyncio.wait_for(wait(), 1)


class _IcomActuator(_DelayedWire):
    actuate = CoreRadio.actuate

    def __init__(self) -> None:
        super().__init__()
        self._commands = type(
            "Commands",
            (),
            {
                "ptt_on": staticmethod(lambda **_kw: b"ON"),
                "ptt_off": staticmethod(lambda **_kw: b"OFF"),
                "stop_cw": staticmethod(lambda **_kw: b"CW-OFF"),
                "set_tuner_status": staticmethod(lambda *_a, **_kw: b"TUNE-OFF"),
            },
        )()
        self._radio_addr = 0x94

    async def _send_civ_raw(
        self, frame: bytes, *, is_current: Callable[[], bool], **_kw: Any
    ) -> None:
        if frame in (b"ON", b"OFF"):
            await self.finish(frame == b"ON", is_current)


class _YaesuActuator(_DelayedWire):
    actuate = YaesuCatRadio.actuate

    async def _write(
        self,
        command: str,
        *,
        is_current: Callable[[], bool],
        **params: Any,
    ) -> None:
        if command == "set_ptt":
            await self.finish(params["state"] == "1", is_current)


class _RigctldActuator(_DelayedWire):
    actuate = RigctldClientRadio.actuate

    async def _set_ptt(
        self,
        on: bool,
        *,
        is_current: Callable[[], bool],
        urgent: bool,
    ) -> None:
        del urgent
        await self.finish(on, is_current)


class _HttpWriter:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    @property
    def status(self) -> int:
        return int(self.buffer.split(b" ", 2)[1])


class _EofWebSocket:
    async def send_text(self, _text: str) -> None:
        return None

    async def recv(self) -> tuple[int, bytes]:
        raise EOFError


async def _http_tx_command(server: WebServer, operation: str) -> None:
    body = json.dumps({"operation": operation}).encode()
    reader = asyncio.StreamReader()
    reader.feed_data(body)
    reader.feed_eof()
    writer = _HttpWriter()
    await server._handle_http(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        "POST",
        "/api/v1/managed-transmit/command",
        headers={"content-length": str(len(body))},
        reader=reader,
    )
    assert writer.status == 202


async def _wait_for_settlement(composition: ManagedTxComposition) -> None:
    async def wait() -> None:
        while (await composition.authority.snapshot()).state.pending_effect is not None:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait(), 1)


def _control_handler(
    server: WebServer,
    actuator: _DelayedWire,
    composition: ManagedTxComposition,
    session_id: str,
) -> ControlHandler:
    return ControlHandler(
        ws=_EofWebSocket(),  # type: ignore[arg-type]
        radio=actuator,  # type: ignore[arg-type]
        server_version="test",
        radio_model="IC-9700",
        server=server,
        session_id=session_id,
        managed_tx_authority=composition.authority,
    )


async def _joined_web_runtime(
    tmp_path: Any, actuator_type: type[_DelayedWire]
) -> tuple[_DelayedWire, ManagedTxComposition, WebServer]:
    actuator = actuator_type()
    actuator.release_on.set()
    profile = resolve_radio_profile(model="IC-9700")
    actuator.profile = profile  # type: ignore[attr-defined]
    actuator.model = profile.model  # type: ignore[attr-defined]
    actuator.capabilities = set(profile.capabilities)  # type: ignore[attr-defined]
    actuator.connected = True  # type: ignore[attr-defined]
    actuator.radio_ready = True  # type: ignore[attr-defined]

    async def raw_set_ptt(on: bool) -> None:
        actuator.raw_ptt.append(on)

    actuator.set_ptt = raw_set_ptt  # type: ignore[attr-defined]
    composition = ManagedTxComposition(
        actuator, config_path=tmp_path / f"{actuator_type.__name__}-web.json"
    )
    install_managed_tx_composition(actuator, composition)
    actuator.set_ptt = raw_set_ptt  # type: ignore[attr-defined]
    server = WebServer(actuator, WebConfig(host="127.0.0.1", port=0))  # type: ignore[arg-type]
    await composition.transport_ready(actuator)
    await composition.bind_state_store(server.command_state_store)
    attach_managed_tx_composition(server, composition)
    return actuator, composition, server


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actuator_type", [_IcomActuator, _YaesuActuator, _RigctldActuator]
)
async def test_real_provider_actuator_suppresses_stale_on_at_final_wire(
    tmp_path, actuator_type: type[_DelayedWire]
) -> None:
    actuator = actuator_type()
    composition = ManagedTxComposition(
        actuator, config_path=tmp_path / f"{actuator_type.__name__}.json"
    )
    store = StateStore()
    store.begin_provider_generation()
    await composition.transport_ready(actuator)
    await composition.bind_state_store(store)

    submission = await composition.authority.submit_ptt(True, "provider-owner")
    await asyncio.wait_for(actuator.on_started.wait(), 0.2)
    store.begin_provider_generation()
    actuator.release_on.set()
    await submission.wait_settlement()
    await composition.transport_ready(object())

    assert True not in actuator.wire
    assert actuator.wire[-1:] == [False]
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actuator_type", [_IcomActuator, _YaesuActuator, _RigctldActuator]
)
async def test_web_sessions_and_latched_commands_share_one_authority_to_final_wire(
    tmp_path, actuator_type: type[_DelayedWire]
) -> None:
    actuator, composition, server = await _joined_web_runtime(tmp_path, actuator_type)
    session_a = _control_handler(server, actuator, composition, "session-a")
    session_b = _control_handler(server, actuator, composition, "session-b")
    try:
        assert session_a._managed_tx_authority is composition.authority  # noqa: SLF001
        assert session_b._managed_tx_authority is composition.authority  # noqa: SLF001
        assert server._managed_tx_authority() is composition.authority  # noqa: SLF001

        await session_a._enqueue_command("ptt_on", {})  # noqa: SLF001
        await actuator.wait_for_wire([True])
        await _wait_for_settlement(composition)

        await session_b.run()
        assert actuator.wire == [True]
        assert (
            await composition.authority.snapshot()
        ).state.intent.owner == "session-a"

        await _http_tx_command(server, "force_off")
        await actuator.wait_for_wire([True, False])
        await _wait_for_settlement(composition)
        await _http_tx_command(server, "transmit_on")
        await actuator.wait_for_wire([True, False, True])
        await _wait_for_settlement(composition)

        await session_a.run()
        projection = await composition.authority.snapshot()
        assert projection.state.intent.kind is ManagedTxIntentKind.TRANSMIT
        assert actuator.wire == [True, False, True]

        await _http_tx_command(server, "force_off")
        await actuator.wait_for_wire([True, False, True, False])
        await _wait_for_settlement(composition)
        assert not server.command_queue.drain()
        assert actuator.raw_ptt == []
    finally:
        await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "actuator_type", [_IcomActuator, _YaesuActuator, _RigctldActuator]
)
async def test_replacement_and_shutdown_drain_off_debt_at_final_wire_without_on_replay(
    tmp_path, actuator_type: type[_DelayedWire]
) -> None:
    actuator = actuator_type()
    actuator.release_on.set()
    retired: list[tuple[int, tuple[bool, ...]]] = []

    async def retire(event: Any) -> None:
        retired.append((event.provider_generation, tuple(actuator.wire)))

    composition = ManagedTxComposition(
        actuator,
        config_path=tmp_path / f"{actuator_type.__name__}-lifecycle.json",
        retire_provider=retire,
    )
    authority = composition.authority
    fence = composition._abort_fence  # noqa: SLF001
    lane = composition._effect_lane  # noqa: SLF001
    store = StateStore()
    store.begin_provider_generation()
    first, replacement = object(), object()
    await composition.transport_ready(first)
    await composition.bind_state_store(store)

    first_on = await authority.submit_transmit_on()
    await first_on.wait_settlement()
    await actuator.wait_for_wire([True])

    store.begin_provider_generation()
    await composition.transport_ready(replacement)
    await actuator.wait_for_wire([True, False])
    assert [entry[0] for entry in retired] == [1]
    assert composition.authority is authority
    assert composition._abort_fence is fence  # noqa: SLF001
    assert composition._effect_lane is lane  # noqa: SLF001
    assert (await authority.snapshot()).provider_generation == 2

    replacement_on = await authority.submit_transmit_on()
    await replacement_on.wait_settlement()
    await actuator.wait_for_wire([True, False, True])

    await composition.shutdown(asyncio.Event())

    assert actuator.wire == [True, False, True, False]
    assert retired == [(1, (True,)), (2, (True, False, True, False))]
    assert composition.authority is authority
    assert composition._abort_fence is fence  # noqa: SLF001
    assert composition._effect_lane is lane  # noqa: SLF001
