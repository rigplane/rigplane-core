import asyncio
from collections.abc import Callable

import pytest

from rigplane.runtime.managed_tx_state import (
    AbortOperation,
    ActuationOperation,
    ActuationResult,
    EffectToken,
    ManagedTxOutcome,
)
from rigplane.runtime.radio import ManagedTxComposition, ManagedTxProviderEvent


class RecordingActuator:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    async def actuate(
        self,
        token: EffectToken,
        operation: ActuationOperation | AbortOperation,
        *,
        is_current: Callable[[], bool],
    ) -> ActuationResult:
        if is_current():
            self.events.append(operation.value)
            return ActuationResult.ACCEPTED
        return ActuationResult.REJECTED


def install_invalidation_seam(composition: ManagedTxComposition) -> None:
    def start() -> asyncio.Task[None]:
        return asyncio.create_task(composition.authority.provider_unavailable())

    setattr(composition.authority, "start_provider_unavailable", start)


@pytest.mark.asyncio
async def test_one_event_drives_activation_and_idempotent_invalidation(
    tmp_path,
) -> None:
    events: list[str] = []
    retired = []

    async def retire(event: ManagedTxProviderEvent) -> None:
        retired.append(event)

    composition = ManagedTxComposition(
        RecordingActuator(events),
        config_path=tmp_path / "managed-tx.json",
        retire_provider=retire,
    )
    install_invalidation_seam(composition)
    event = ManagedTxProviderEvent(1, 17)
    await composition.activate_provider(event)
    assert composition.active_provider is event
    assert await composition.authority.ptt_down("web:1") is ManagedTxOutcome.ACCEPTED

    first = composition.start_provider_unavailable(event)
    second = composition.start_provider_unavailable(event)
    assert first is second
    assert composition.active_provider is None
    await first
    assert retired == [event]
    assert events == ["ptt_on", "force_receive"]

    replacement = ManagedTxProviderEvent(2, 18)
    await composition.activate_provider(replacement)
    assert events[-1] == "force_receive"
    assert events.count("ptt_on") == 1
    await composition.shutdown(asyncio.Event())


@pytest.mark.asyncio
async def test_shutdown_force_receive_precedes_retirement_and_is_joinable(
    tmp_path,
) -> None:
    events: list[str] = []

    async def retire(event: ManagedTxProviderEvent) -> None:
        events.append(f"retire:{event.provider_generation}")

    composition = ManagedTxComposition(
        RecordingActuator(events),
        config_path=tmp_path / "managed-tx.json",
        retire_provider=retire,
    )
    install_invalidation_seam(composition)
    await composition.activate_provider(ManagedTxProviderEvent(3, 9))
    assert await composition.authority.transmit_on() is ManagedTxOutcome.ACCEPTED

    first, second = await asyncio.gather(
        composition.shutdown(asyncio.Event()),
        composition.shutdown(asyncio.Event()),
    )
    assert first is second
    assert events == ["transmit_on", "force_receive", "retire:3"]
