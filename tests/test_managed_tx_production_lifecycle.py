import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.core.state_store import StateStore
from rigplane.runtime.managed_tx_composition import ManagedTxComposition
from rigplane.runtime.managed_tx_authority import ShutdownResult
from rigplane.runtime.radio import CoreRadio, RadioConnectionState
from rigplane.runtime.managed_tx_state import (
    AbortOperation,
    ActuationOperation,
    ActuationResult,
    EffectToken,
)


class RecordingActuator:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def actuate(
        self,
        _token: EffectToken,
        operation: ActuationOperation | AbortOperation,
        *,
        is_current: Callable[[], bool],
    ) -> ActuationResult:
        if not is_current():
            return ActuationResult.REJECTED
        self.events.append(operation.value)
        return ActuationResult.ACCEPTED


class BlockingActuator(RecordingActuator):
    def __init__(self, events: list[str], generation: int) -> None:
        super().__init__(events)
        self.generation = generation
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def actuate(
        self,
        token: EffectToken,
        operation: ActuationOperation | AbortOperation,
        *,
        is_current: Callable[[], bool],
    ) -> ActuationResult:
        if (
            operation is ActuationOperation.FORCE_RECEIVE
            and token.provider_generation == self.generation
        ):
            self.started.set()
            await self.release.wait()
        return await super().actuate(token, operation, is_current=is_current)


@pytest.mark.asyncio
async def test_initial_transport_ready_is_latched_until_exact_store_bind(
    tmp_path,
) -> None:
    composition = ManagedTxComposition(
        RecordingActuator([]), config_path=tmp_path / "managed-tx.json"
    )
    transport = object()
    store = StateStore()
    store.begin_provider_generation()

    await composition.transport_ready(transport)
    assert composition._active_provider is None
    await composition.bind_state_store(store)

    assert composition._active_provider is not None
    assert composition._active_provider.transport_identity is transport
    assert composition._active_provider.provider_generation == 1
    assert composition._active_provider.observation_generation == 1
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_full_reconnect_retires_then_services_pending_off_debt(tmp_path) -> None:
    events: list[str] = []
    retired: list[int] = []

    async def retire(event) -> None:
        retired.append(event.provider_generation)

    composition = ManagedTxComposition(
        RecordingActuator(events),
        config_path=tmp_path / "managed-tx.json",
        retire_provider=retire,
    )
    store = StateStore()
    store.begin_provider_generation()
    first_transport, second_transport = object(), object()
    radio = object.__new__(CoreRadio)
    radio._managed_tx_composition = composition
    radio._managed_tx_arm_lock = asyncio.Lock()
    radio._civ_transport = first_transport
    radio._session_lifecycle = SimpleNamespace(connect=AsyncMock())
    radio._fetch_initial_state = AsyncMock()
    radio._reset_external_cat_session = lambda: None
    await radio.connect()
    await composition.bind_state_store(store)
    await composition.authority.transmit_on()

    store.begin_provider_generation()
    radio._civ_transport = second_transport
    await radio.connect()

    assert retired == [1]
    assert events[-1] == "force_receive"
    assert composition._active_provider.provider_generation == 2
    assert composition._active_provider.transport_identity is second_transport
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_two_replacements_use_monotonic_tx_generations_and_one_graph(
    tmp_path,
) -> None:
    composition = ManagedTxComposition(
        RecordingActuator([]), config_path=tmp_path / "managed-tx.json"
    )
    store = StateStore()
    store.begin_provider_generation()
    authority = composition.authority
    fence, lane = composition._abort_fence, composition._effect_lane
    transports = [object(), object(), object()]
    await composition.transport_ready(transports[0])
    await composition.bind_state_store(store)
    generations = [composition._active_provider.provider_generation]

    for transport in transports[1:]:
        store.begin_provider_generation()
        await composition.transport_ready(transport)
        generations.append(composition._active_provider.provider_generation)

    assert generations == [1, 2, 3]
    assert composition.authority is authority
    assert (composition._abort_fence, composition._effect_lane) == (fence, lane)
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_concurrent_duplicate_and_stale_transport_signals_are_idempotent(
    tmp_path,
) -> None:
    composition = ManagedTxComposition(
        RecordingActuator([]), config_path=tmp_path / "managed-tx.json"
    )
    store = StateStore()
    store.begin_provider_generation()
    first, second = object(), object()
    radio = object.__new__(CoreRadio)
    radio._managed_tx_composition = composition
    radio._managed_tx_arm_lock = asyncio.Lock()
    radio._civ_transport = first
    await radio._arm_managed_tx()
    await composition.bind_state_store(store)
    await radio._park_managed_tx()
    store.begin_provider_generation()
    radio._civ_transport = second

    await asyncio.gather(
        radio.rearm_managed_tx(),
        radio.rearm_managed_tx(),
        composition.transport_unavailable(first),
    )

    assert composition._active_provider.provider_generation == 2
    assert composition._active_provider.transport_identity is second
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_generation_drift_without_live_transport_poisons_without_activation(
    tmp_path,
) -> None:
    composition = ManagedTxComposition(
        RecordingActuator([]), config_path=tmp_path / "managed-tx.json"
    )
    store = StateStore()
    store.begin_provider_generation()
    transport = object()
    await composition.transport_ready(transport)
    await composition.bind_state_store(store)
    await composition.transport_unavailable(transport)

    store.begin_provider_generation()
    async with composition._transition_lock:
        await composition._join_transition_locked()

    assert composition._active_provider is None
    assert composition._observation_generation == 2
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_generation_drift_during_debt_replay_cannot_publish_stale_candidate(
    tmp_path,
) -> None:
    events: list[str] = []
    retired: list[int] = []
    actuator = BlockingActuator(events, generation=2)

    async def retire(event) -> None:
        retired.append(event.provider_generation)

    composition = ManagedTxComposition(
        actuator,
        config_path=tmp_path / "managed-tx.json",
        retire_provider=retire,
    )
    store = StateStore()
    store.begin_provider_generation()
    first, candidate = object(), object()
    await composition.transport_ready(first)
    await composition.bind_state_store(store)
    await composition.authority.transmit_on()
    store.begin_provider_generation()

    replacement = asyncio.create_task(composition.transport_ready(candidate))
    await actuator.started.wait()
    store.begin_provider_generation()
    actuator.release.set()
    await replacement

    assert composition._active_provider is None
    assert (await composition.authority.snapshot()).provider_generation is None
    assert retired == [1, 2]
    assert events.count("transmit_on") == 1
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_terminal_shutdown_then_actual_core_disconnect_is_idempotent(
    tmp_path,
) -> None:
    actuator = BlockingActuator([], generation=1)
    retired: list[int] = []

    async def retire(event) -> None:
        retired.append(event.provider_generation)

    composition = ManagedTxComposition(
        actuator,
        config_path=tmp_path / "managed-tx.json",
        retire_provider=retire,
    )
    store = StateStore()
    store.begin_provider_generation()
    transport = object()
    await composition.transport_ready(transport)
    await composition.bind_state_store(store)
    await composition.authority.transmit_on()
    termination = asyncio.Event()
    termination.set()

    result = await composition.shutdown(termination)
    await composition.transport_ready(object())

    radio = object.__new__(CoreRadio)
    radio._managed_tx_composition = composition
    radio._managed_tx_arm_lock = asyncio.Lock()
    radio._civ_transport = transport
    radio._session_lifecycle = SimpleNamespace(disconnect=AsyncMock())
    radio._conn_state = RadioConnectionState.DISCONNECTED
    await radio.disconnect()

    assert result is ShutdownResult.TERMINATED
    assert composition._active_provider is None
    assert retired == [1]
    radio._session_lifecycle.disconnect.assert_awaited_once()


@pytest.mark.asyncio
async def test_shutdown_is_ordered_joinable_and_retires_once(tmp_path) -> None:
    events: list[str] = []
    retired: list[int] = []

    async def retire(event) -> None:
        retired.append(event.provider_generation)

    composition = ManagedTxComposition(
        RecordingActuator(events),
        config_path=tmp_path / "managed-tx.json",
        retire_provider=retire,
    )
    store = StateStore()
    store.begin_provider_generation()
    await composition.transport_ready(object())
    await composition.bind_state_store(store)
    assert len(store._provider_generation_subscribers) == 1
    await composition.authority.transmit_on()

    first, second, third = await asyncio.gather(
        composition.shutdown(asyncio.Event()),
        composition.shutdown(asyncio.Event()),
        composition.shutdown(asyncio.Event()),
    )

    assert first is second is third
    assert events == [
        "transmit_on",
        "force_receive",
        "stop_cw",
        "stop_tune",
    ]
    assert retired == [1]
    assert store._provider_generation_subscribers == []
