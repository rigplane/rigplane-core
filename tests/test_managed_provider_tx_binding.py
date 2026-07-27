import asyncio
from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.core.tx_safety import ProviderPttObservation, RadioTx, TxOutcome
from rigplane.runtime.managed_radio_runtime import (
    ManagedRadioRuntime,
    ProviderTxLifecycle,
)
from rigplane.runtime.radio import CoreRadio


class ProviderLifecycle:
    def __init__(self) -> None:
        self.bindings: list[tuple[int, Callable[[ProviderPttObservation], None]]] = []
        self.unbinds = 0
        self.read_started = asyncio.Event()
        self.read_release = asyncio.Event()
        self.block_read = False

    def bind(
        self,
        generation: int,
        observer: Callable[[ProviderPttObservation], None],
    ) -> None:
        self.bindings.append((generation, observer))

    def unbind(self) -> None:
        self.unbinds += 1

    async def read_ptt(self) -> None:
        generation, observer = self.bindings[-1]
        self.read_started.set()
        if self.block_read:
            await self.read_release.wait()
        observer(ProviderPttObservation(RadioTx.OFF, generation, 1, 10.0))

    def contract(self) -> ProviderTxLifecycle:
        return ProviderTxLifecycle(self.bind, self.unbind, self.read_ptt)


def runtime(provider: ProviderLifecycle) -> ManagedRadioRuntime:
    async def no_effects(*_args: object) -> None:
        pass

    return ManagedRadioRuntime(
        "radio-a",
        service=no_effects,
        provider_lifecycle=provider.contract(),
        clock=lambda: 10.0,
    )


@pytest.mark.asyncio
async def test_effectless_replacement_binds_host_generation_before_ready() -> None:
    provider = ProviderLifecycle()
    managed = runtime(provider)

    changed = await managed.replace_provider(ready=True)

    assert changed.snapshot.provider_generation == 1
    assert changed.snapshot.provider_ready is True
    assert [generation for generation, _ in provider.bindings] == [1]


@pytest.mark.asyncio
async def test_fresh_read_uses_bound_observer_and_rejects_stale_callback() -> None:
    provider = ProviderLifecycle()
    managed = runtime(provider)
    await managed.replace_provider(ready=True)
    old_generation, old_observer = provider.bindings[-1]

    read = await managed.request_fresh_ptt(old_generation)
    assert read.outcome is TxOutcome.APPLIED
    assert read.snapshot.radio_tx is RadioTx.OFF

    await managed.replace_provider(ready=False)
    old_observer(ProviderPttObservation(RadioTx.ON, old_generation, 2, 11.0))
    assert managed.tx_snapshot.radio_tx is RadioTx.UNKNOWN


@pytest.mark.asyncio
async def test_read_completion_is_stale_after_provider_replacement() -> None:
    provider = ProviderLifecycle()
    provider.block_read = True
    managed = runtime(provider)
    await managed.replace_provider(ready=True)

    pending = asyncio.create_task(managed.request_fresh_ptt(1))
    await provider.read_started.wait()
    await managed.replace_provider(ready=False)
    provider.read_release.set()

    assert (await pending).outcome is TxOutcome.STALE
    assert managed.tx_snapshot.provider_generation == 2


@pytest.mark.asyncio
async def test_invalidation_advances_host_and_supervisor_together() -> None:
    provider = ProviderLifecycle()
    managed = runtime(provider)
    await managed.replace_provider(ready=True)

    assert (await managed.invalidate_provider(0)).outcome is TxOutcome.STALE
    invalidated = await managed.invalidate_provider(1)

    assert invalidated.snapshot.provider_generation == 2
    assert invalidated.snapshot.provider_ready is False
    assert provider.unbinds == 1
    assert [generation for generation, _ in provider.bindings] == [1]
    replaced = await managed.replace_provider(ready=True)
    assert replaced.snapshot.provider_generation == 3
    assert [generation for generation, _ in provider.bindings] == [1, 3]


@pytest.mark.asyncio
async def test_icom_fresh_read_sends_real_ptt_query() -> None:
    radio = SimpleNamespace(
        _radio_addr=0x98,
        _check_connected=Mock(),
        _send_civ_expect=AsyncMock(),
    )

    await CoreRadio._request_authoritative_ptt_read(radio)  # type: ignore[arg-type]

    radio._check_connected.assert_called_once()
    radio._send_civ_expect.assert_awaited_once_with(
        build_civ_frame(0x98, CONTROLLER_ADDR, 0x1C, sub=0x00),
        label="get_ptt",
    )
