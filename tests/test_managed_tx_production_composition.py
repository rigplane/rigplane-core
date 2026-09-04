import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from rigplane.core.radio_protocol import ManagedTxApi
from rigplane.core.state_store import StateStore
from rigplane.core.tx_safety import TxOutcome, TxOwner, TxSource
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.runtime.managed_tx_effect_lane import ManagedTxActuator
from rigplane.runtime.managed_tx_state import (
    AbortOperation,
    ActuationOperation,
    ActuationResult,
    EffectToken,
)
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    ManagedTxCompositionPort,
    install_managed_tx_composition,
)
from rigplane.web.web_startup import (
    attach_managed_tx_composition,
    start_web_server,
)


class RecordingActuator:
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


class LegacyRadio:
    def __init__(self) -> None:
        self.raw_writes: list[bool] = []

    async def set_ptt(self, on: bool) -> None:
        self.raw_writes.append(on)


@pytest.mark.asyncio
async def test_one_identity_graph_is_installed_and_shared(tmp_path) -> None:
    actuator = RecordingActuator()
    composition = ManagedTxComposition(
        actuator, config_path=tmp_path / "managed-tx.json"
    )

    assert isinstance(actuator, ManagedTxActuator)
    assert isinstance(composition, ManagedTxCompositionPort)
    assert composition.authority._lane is composition._effect_lane
    assert composition.authority._abort_fence is composition._abort_fence
    assert composition.authority._config_store is composition._tot_config_store
    assert composition._effect_lane._actuator is actuator
    assert composition.local_tx_work_runner._abort_fence is composition._abort_fence

    radio = LegacyRadio()
    install_managed_tx_composition(radio, composition)
    assert radio._managed_tx_composition is composition
    assert radio.managed_tx is composition.legacy_supervisor

    store = StateStore()
    store.begin_provider_generation()
    await composition.transport_ready(radio)
    await composition.bind_state_store(store)
    server = SimpleNamespace(
        _radio=radio,
        _production_managed_tx_port=None,
        command_state_store=store,
    )
    attach_managed_tx_composition(server, composition)
    assert server._production_managed_tx_port is composition
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_yaesu_install_shares_runner_and_poison_suppresses_queued_cw_chunks(
    tmp_path,
) -> None:
    radio = object.__new__(YaesuCatRadio)
    radio._local_tx_work = None

    async def raw_set_ptt(_on: bool) -> None:
        raise AssertionError("raw PTT must be replaced")

    radio.set_ptt = raw_set_ptt
    queued = asyncio.Event()
    release = asyncio.Event()
    pending: list[tuple[str, Callable[[], bool] | None]] = []
    wire: list[str] = []

    async def delayed_write(
        _command: str,
        *,
        is_current: Callable[[], bool] | None = None,
        **params: str,
    ) -> None:
        pending.append((params["mem"], is_current))
        queued.set()
        await release.wait()
        if is_current is None or is_current():
            wire.append(params["mem"])

    radio._write = delayed_write
    composition = ManagedTxComposition(radio, config_path=tmp_path / "managed-tx.json")
    install_managed_tx_composition(radio, composition)

    assert radio._local_tx_work is composition.local_tx_work_runner
    assert radio._local_tx_work._abort_fence is composition._abort_fence
    with pytest.raises(RuntimeError, match="already installed"):
        install_managed_tx_composition(radio, composition)

    mismatched = object.__new__(YaesuCatRadio)
    mismatched._local_tx_work = object()
    mismatched.set_ptt = raw_set_ptt
    with pytest.raises(RuntimeError, match="local TX work runner"):
        install_managed_tx_composition(mismatched, composition)
    assert mismatched._local_tx_work is not composition.local_tx_work_runner

    store = StateStore()
    store.begin_provider_generation()
    await composition.transport_ready(radio)
    await composition.bind_state_store(store)
    sending = asyncio.create_task(radio.send_cw_text("A" * 48))
    await queued.wait()
    await composition.transport_unavailable(radio)
    release.set()

    with pytest.raises(asyncio.CancelledError):
        await sending
    assert len(pending) == 1
    assert pending[0][0] == "A" * 24
    assert pending[0][1] is not None
    assert wire == []
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_legacy_on_fails_before_wire_but_off_uses_canonical_force_receive(
    tmp_path,
) -> None:
    actuator = RecordingActuator()
    radio = LegacyRadio()
    composition = ManagedTxComposition(
        actuator, config_path=tmp_path / "managed-tx.json"
    )
    install_managed_tx_composition(radio, composition)
    store = StateStore()
    store.begin_provider_generation()
    await composition.transport_ready(radio)
    await composition.bind_state_store(store)
    api = ManagedTxApi.bind(radio, TxOwner(TxSource.SDK, "legacy-cutover"))
    assert api is not None

    with pytest.raises(RuntimeError, match="legacy PTT ON is blocked"):
        await api.set_ptt(True)
    with pytest.raises(RuntimeError, match="raw PTT ON is blocked"):
        await radio.set_ptt(True)
    first = await api.set_ptt(False)
    await radio.set_ptt(False)
    second = await api.set_ptt(False)

    assert first.outcome is TxOutcome.IDEMPOTENT
    assert second.outcome is TxOutcome.IDEMPOTENT
    assert radio.raw_writes == []
    assert actuator.operations.count(ActuationOperation.FORCE_RECEIVE) == 3
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_installed_radio_requires_exact_port_and_active_event_before_listener(
    tmp_path,
) -> None:
    composition = ManagedTxComposition(
        RecordingActuator(), config_path=tmp_path / "managed-tx.json"
    )
    radio = LegacyRadio()
    install_managed_tx_composition(radio, composition)
    store = StateStore()
    store.begin_provider_generation()
    server = SimpleNamespace(
        _radio=radio,
        _production_managed_tx_port=None,
        command_state_store=store,
    )

    with patch("asyncio.start_server", new_callable=AsyncMock) as listener:
        with pytest.raises(RuntimeError, match="not attached"):
            await start_web_server(server)
    listener.assert_not_awaited()

    await composition.bind_state_store(store)
    attach_managed_tx_composition(server, composition)
    with patch("asyncio.start_server", new_callable=AsyncMock) as listener:
        with pytest.raises(RuntimeError, match="not current"):
            await start_web_server(server)
    listener.assert_not_awaited()
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_unmanaged_web_ignores_dynamic_mock_attrs_but_rejects_attachments() -> (
    None
):
    class SlotOnlyRadio:
        __slots__ = ()

    with patch(
        "rigplane.web.web_startup._start_web_server", new_callable=AsyncMock
    ) as start:
        for radio in (MagicMock(), SlotOnlyRadio(), None):
            unmanaged = SimpleNamespace(_radio=radio)
            await start_web_server(unmanaged)
    assert start.await_count == 3

    attached_without_marker = SimpleNamespace(
        _radio=LegacyRadio(),
        _production_managed_tx_port=object(),
    )
    with pytest.raises(RuntimeError, match="not installed"):
        await start_web_server(attached_without_marker)


@pytest.mark.asyncio
async def test_web_only_validates_and_never_owns_composition_lifecycle() -> None:
    port = MagicMock()
    port.validate_state_store = MagicMock()
    port.shutdown = AsyncMock(side_effect=AssertionError("Web must not shut down TX"))
    radio = LegacyRadio()
    radio._managed_tx_composition = port
    store = StateStore()
    server = SimpleNamespace(
        _radio=radio,
        _production_managed_tx_port=None,
        command_state_store=store,
    )
    attach_managed_tx_composition(server, port)

    with (
        patch(
            "rigplane.web.web_startup._start_web_server", new_callable=AsyncMock
        ) as start,
        patch(
            "rigplane.web.web_startup._stop_web_server", new_callable=AsyncMock
        ) as stop,
    ):
        await start_web_server(server)
        from rigplane.web.web_startup import stop_web_server

        await stop_web_server(server)

    port.validate_state_store.assert_called_once_with(store)
    port.shutdown.assert_not_awaited()
    assert port.method_calls == [call.validate_state_store(store)]
    start.assert_awaited_once_with(server)
    stop.assert_awaited_once_with(server)


@pytest.mark.asyncio
async def test_observation_generation_drift_poisons_before_listener(tmp_path) -> None:
    composition = ManagedTxComposition(
        RecordingActuator(), config_path=tmp_path / "managed-tx.json"
    )
    radio = LegacyRadio()
    install_managed_tx_composition(radio, composition)
    store = StateStore()
    for _ in range(4):
        store.begin_provider_generation()
    await composition.transport_ready(radio)
    await composition.bind_state_store(store)
    server = SimpleNamespace(
        _radio=radio,
        _production_managed_tx_port=None,
        command_state_store=store,
    )
    attach_managed_tx_composition(server, composition)
    store.begin_provider_generation()

    with patch("asyncio.start_server", new_callable=AsyncMock) as listener:
        with pytest.raises(RuntimeError, match="provider is not current"):
            await start_web_server(server)

    listener.assert_not_awaited()
    assert composition._active_provider is None
    await composition.shutdown(asyncio.Event())
