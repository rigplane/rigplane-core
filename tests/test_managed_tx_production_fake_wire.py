import asyncio
from collections.abc import Callable

import pytest

from rigplane.runtime.managed_tx_state import (
    AbortOperation,
    ActuationOperation,
    ActuationResult,
    EffectToken,
)
from rigplane.runtime.radio import ManagedTxComposition, ManagedTxProviderEvent


class DelayedFinalWire:
    def __init__(self) -> None:
        self.on_started = asyncio.Event()
        self.release_on = asyncio.Event()
        self.wire: list[str] = []

    async def actuate(
        self,
        token: EffectToken,
        operation: ActuationOperation | AbortOperation,
        *,
        is_current: Callable[[], bool],
    ) -> ActuationResult:
        if operation in (
            ActuationOperation.PTT_ON,
            ActuationOperation.TRANSMIT_ON,
        ):
            self.on_started.set()
            try:
                await self.release_on.wait()
            except asyncio.CancelledError:
                await self.release_on.wait()
        if not is_current():
            return ActuationResult.REJECTED
        self.wire.append(operation.value)
        return ActuationResult.ACCEPTED


def install_invalidation_seam(composition: ManagedTxComposition) -> None:
    def start() -> asyncio.Task[None]:
        composition.abort_fence.force_off()
        return asyncio.create_task(composition.authority.provider_unavailable())

    setattr(composition.authority, "start_provider_unavailable", start)


@pytest.mark.asyncio
@pytest.mark.parametrize("provider", ["icom", "yaesu", "rigctld-client"])
async def test_stale_on_is_rejected_at_each_fake_final_wire(
    tmp_path, provider: str
) -> None:
    actuator = DelayedFinalWire()
    composition = ManagedTxComposition(
        actuator, config_path=tmp_path / f"{provider}.json"
    )
    install_invalidation_seam(composition)
    event = ManagedTxProviderEvent(1, 1)
    await composition.activate_provider(event)

    submission = await composition.authority.submit_ptt(True, f"{provider}:owner")
    await asyncio.wait_for(actuator.on_started.wait(), 0.2)
    invalidation = composition.start_provider_unavailable(event)
    actuator.release_on.set()
    await submission.wait_settlement()
    await invalidation

    assert "ptt_on" not in actuator.wire
    assert actuator.wire[-1:] == ["force_receive"]
    await composition.shutdown(asyncio.Event())
