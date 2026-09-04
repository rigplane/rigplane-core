"""Standalone rigctld installs the one production managed-TX graph."""

import asyncio
import logging
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.managed_tx_state import (
    AbortOperation,
    ActuationOperation,
    ActuationResult,
    EffectToken,
)
from serial_stub import SerialMockRadio


class _Actuator:
    def __init__(self) -> None:
        self.operations: list[ActuationOperation | AbortOperation] = []

    async def actuate(
        self,
        _token: EffectToken,
        operation: ActuationOperation | AbortOperation,
        *,
        is_current: Callable[[], bool],
    ) -> ActuationResult:
        if not is_current():
            return ActuationResult.REJECTED
        self.operations.append(operation)
        return ActuationResult.ACCEPTED


async def _settle(predicate: Callable[[], bool]) -> None:
    for _ in range(100):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("managed effect did not settle")


@pytest.mark.asyncio
async def test_standalone_binds_exact_store_before_listener_and_uses_local_service(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    radio = SerialMockRadio()
    await radio.connect()
    composition = ManagedTxComposition(
        _Actuator(), config_path=tmp_path / "managed-tx.json"
    )
    install_managed_tx_composition(radio, composition)
    await composition.transport_ready(radio)
    server = RigctldServer(
        radio,
        RigctldConfig(host="127.0.0.1", port=0),
        managed_tx_composition=composition,
    )
    order: list[str] = []
    real_bind = composition.bind_state_store
    real_validate = composition.validate_state_store

    async def bind(store) -> None:
        order.append(f"bind:{store.provider_generation}")
        await real_bind(store)

    def validate(store) -> None:
        order.append("validate")
        real_validate(store)

    composition.bind_state_store = bind  # type: ignore[method-assign]
    composition.validate_state_store = validate  # type: ignore[method-assign]
    listener = SimpleNamespace(
        sockets=[SimpleNamespace(getsockname=lambda: ("127.0.0.1", 4532))],
        close=lambda: None,
        wait_closed=AsyncMock(),
    )

    async def start_listener(*_args, **_kwargs):
        order.append("listener")
        return listener

    monkeypatch.setattr(asyncio, "start_server", start_listener)
    try:
        await server.start()
        store = server._state_store  # noqa: SLF001
        assert store is not None and store.provider_generation == 1
        composition.validate_state_store(store)
        assert server._managed_tx_bound_store is store  # noqa: SLF001
        assert server._rig_handler._state_store is store  # noqa: SLF001
        assert server._rig_handler._command_service._state_store is store  # noqa: SLF001
        assert order == ["bind:1", "validate", "listener", "validate"]
    finally:
        await server.stop()
        await composition.shutdown(asyncio.Event())
        await radio.disconnect()


@pytest.mark.asyncio
async def test_real_socket_t1_t0_routes_only_through_composition(tmp_path) -> None:
    radio = SerialMockRadio()
    await radio.connect()
    actuator = _Actuator()
    composition = ManagedTxComposition(
        actuator, config_path=tmp_path / "managed-tx.json"
    )
    install_managed_tx_composition(radio, composition)
    await composition.transport_ready(radio)
    server = RigctldServer(
        radio,
        RigctldConfig(host="127.0.0.1", port=0),
        managed_tx_composition=composition,
    )
    await server.start()
    port = int(server._server.sockets[0].getsockname()[1])  # noqa: SLF001
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    try:
        writer.write(b"T 1\n")
        await writer.drain()
        assert await reader.readline() == b"RPRT 0\n"
        await _settle(lambda: actuator.operations == [ActuationOperation.PTT_ON])
        writer.write(b"T 0\n")
        await writer.drain()
        assert await reader.readline() == b"RPRT 0\n"
        await _settle(lambda: len(actuator.operations) == 2)
        assert actuator.operations == [
            ActuationOperation.PTT_ON,
            ActuationOperation.FORCE_RECEIVE,
        ]
        assert radio._ptt is False  # noqa: SLF001
    finally:
        writer.close()
        await writer.wait_closed()
        await server.stop()
        await composition.shutdown(asyncio.Event())
        await radio.disconnect()


@pytest.mark.asyncio
async def test_foreign_t0_is_wire_success_without_force_off(tmp_path) -> None:
    radio = SerialMockRadio()
    await radio.connect()
    actuator = _Actuator()
    composition = ManagedTxComposition(
        actuator, config_path=tmp_path / "managed-tx.json"
    )
    install_managed_tx_composition(radio, composition)
    await composition.transport_ready(radio)
    server = RigctldServer(
        radio,
        RigctldConfig(host="127.0.0.1", port=0),
        managed_tx_composition=composition,
    )
    await server.start()
    port = int(server._server.sockets[0].getsockname()[1])  # noqa: SLF001
    first_r, first_w = await asyncio.open_connection("127.0.0.1", port)
    second_r, second_w = await asyncio.open_connection("127.0.0.1", port)
    try:
        first_w.write(b"T 1\n")
        await first_w.drain()
        assert await first_r.readline() == b"RPRT 0\n"
        await _settle(lambda: actuator.operations == [ActuationOperation.PTT_ON])

        second_w.write(b"T 0\n")
        await second_w.drain()
        assert await second_r.readline() == b"RPRT 0\n"
        await asyncio.sleep(0)
        assert actuator.operations == [ActuationOperation.PTT_ON]
    finally:
        first_w.close()
        second_w.close()
        await asyncio.gather(first_w.wait_closed(), second_w.wait_closed())
        await server.stop()
        await composition.shutdown(asyncio.Event())
        await radio.disconnect()


@pytest.mark.asyncio
async def test_composition_identity_mismatch_fails_before_listener(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    radio = SerialMockRadio()
    await radio.connect()
    installed = ManagedTxComposition(
        _Actuator(), config_path=tmp_path / "installed.json"
    )
    other = ManagedTxComposition(_Actuator(), config_path=tmp_path / "other.json")
    install_managed_tx_composition(radio, installed)
    await other.transport_ready(radio)
    listener = AsyncMock()
    monkeypatch.setattr(asyncio, "start_server", listener)
    try:
        server = RigctldServer(radio, managed_tx_composition=other)
        with pytest.raises(RuntimeError, match="identity mismatch"):
            await server.start()
        listener.assert_not_awaited()
    finally:
        await installed.shutdown(asyncio.Event())
        await other.shutdown(asyncio.Event())
        await radio.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only,warned", [(False, True), (True, False)])
async def test_unmanaged_warning_boundary(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
    read_only: bool,
    warned: bool,
) -> None:
    radio = SerialMockRadio()
    await radio.connect()
    server = RigctldServer(
        radio,
        RigctldConfig(host="127.0.0.1", port=0, read_only=read_only),
    )
    listener = SimpleNamespace(
        sockets=[SimpleNamespace(getsockname=lambda: ("127.0.0.1", 4532))],
        close=lambda: None,
        wait_closed=AsyncMock(),
    )
    monkeypatch.setattr(asyncio, "start_server", AsyncMock(return_value=listener))
    try:
        with caplog.at_level(logging.WARNING):
            await server.start()
        messages = [record.getMessage() for record in caplog.records]
        assert any("Managed TX authority" in message for message in messages) is warned
    finally:
        await server.stop()
        await radio.disconnect()
