import asyncio
from collections.abc import Callable

import pytest

from rigplane.core.radio_protocol import ManagedTxApi
from rigplane.core.tx_safety import TxOwner, TxSource
from rigplane.runtime.managed_tx_effect_lane import ManagedTxActuator
from rigplane.runtime.managed_tx_state import (
    AbortOperation,
    ActuationOperation,
    ActuationResult,
    EffectToken,
)
from rigplane.runtime.radio import (
    ManagedTxComposition,
    ManagedTxCompositionPort,
    install_managed_tx_composition,
)


class FakeActuator:
    async def actuate(
        self,
        token: EffectToken,
        operation: ActuationOperation | AbortOperation,
        *,
        is_current: Callable[[], bool],
    ) -> ActuationResult:
        return ActuationResult.ACCEPTED


@pytest.mark.asyncio
async def test_builds_one_authority_fence_lane_and_tot_store(tmp_path) -> None:
    actuator = FakeActuator()
    composition = ManagedTxComposition(
        actuator,
        config_path=tmp_path / "managed-tx.json",
    )
    assert isinstance(actuator, ManagedTxActuator)
    assert isinstance(composition, ManagedTxCompositionPort)
    assert composition.authority._lane is composition._lane
    assert composition.authority._abort_fence is composition.abort_fence
    assert composition.authority._config_store is composition._config_store
    assert composition._lane._actuator is actuator

    await composition.shutdown(asyncio.Event())


class LegacyRadio:
    def __init__(self) -> None:
        self.raw_writes: list[bool] = []

    async def set_ptt(self, on: bool) -> None:
        self.raw_writes.append(on)


@pytest.mark.asyncio
async def test_install_blocks_legacy_raw_ptt_fallback(tmp_path) -> None:
    radio = LegacyRadio()
    composition = ManagedTxComposition(
        FakeActuator(), config_path=tmp_path / "managed-tx.json"
    )
    install_managed_tx_composition(radio, composition)

    api = ManagedTxApi.bind(radio, TxOwner(TxSource.SDK, "cutover"))
    assert api is not None
    with pytest.raises(RuntimeError, match="legacy PTT ingress is blocked"):
        await api.set_ptt(True)
    assert radio.raw_writes == []
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_duplicate_install_is_rejected(tmp_path) -> None:
    radio = LegacyRadio()
    composition = ManagedTxComposition(
        FakeActuator(), config_path=tmp_path / "managed-tx.json"
    )
    install_managed_tx_composition(radio, composition)
    with pytest.raises(RuntimeError, match="already installed"):
        install_managed_tx_composition(radio, composition)
    await composition.shutdown(asyncio.Event())
