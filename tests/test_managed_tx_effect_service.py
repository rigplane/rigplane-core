import asyncio
from unittest.mock import patch

import pytest

from rigplane.core import tx_safety as tx
from rigplane.runtime.managed_radio_runtime import _ManagedTxEffectHost
from rigplane.runtime.managed_tx_effect_service import managed_tx_effect_service


@pytest.mark.asyncio
async def test_exact_claim_drains_serial_write_then_fresh_read_once() -> None:
    calls: list[tuple[str, int]] = []
    observation = tx.ProviderPttObservation
    sup = tx.TxSafetySupervisor(clock=lambda: 10.0)

    def acquire(generation: int) -> tx.TxTransition:
        nonlocal sup
        sup = tx.TxSafetySupervisor(clock=lambda: 10.0)
        sup.replace_provider(generation, ready=True)
        sup.observe_ptt(observation(tx.RadioTx.OFF, generation, 1, 10.0))
        return sup.request_on(tx.TxOwner(tx.TxSource.SDK, "client"))

    async def provider(generation: int, on: bool | None = None) -> None:
        calls.append(("read" if on is None else "on" if on else "off", generation))
        if on is None:
            sup.observe_ptt(observation(tx.RadioTx.ON, generation, 2, 10.0))

    host = _ManagedTxEffectHost(lambda: 10.0, provider, provider, provider)
    service = managed_tx_effect_service(host)
    transition = acquire(3)
    with patch.object(sup, "settle_attempt", wraps=sup.settle_attempt) as settle:
        await asyncio.gather(service(sup, transition), service(sup, transition))
    assert settle.call_count == 2 and sup.snapshot.phase is tx.TxPhase.KEYED
    for generation in range(4, 24):
        current = acquire(generation)
        await asyncio.gather(service(sup, current), service(sup, current))
        await service(sup, current)
    pending = acquire(24)
    cancel = sup.set_provider_ready(24, ready=False)
    await asyncio.gather(service(sup, cancel), service(sup, cancel))
    await service(sup, pending)
    assert not getattr(service, "_claims") and sup.snapshot.active_attempt is None
    current = acquire(25)
    await service(sup, current)
    await service(sup, transition)
    assert calls == [(k, g) for g in (*range(3, 24), 25) for k in ("on", "read")]
