import asyncio
from itertools import count
from unittest.mock import patch

import pytest

from rigplane.core import tx_safety as tx
from rigplane.runtime.managed_radio_runtime import _ManagedTxEffectHost
from rigplane.runtime.managed_tx_effect_service import managed_tx_effect_service


@pytest.mark.asyncio
async def test_exact_claim_drains_serial_write_then_fresh_read_once() -> None:
    ids = (f"id-{value}" for value in count())
    supervisor = tx.TxSafetySupervisor(clock=lambda: 10.0, id_factory=lambda: next(ids))
    supervisor.replace_provider(3, ready=True)
    supervisor.observe_ptt(tx.ProviderPttObservation(tx.RadioTx.OFF, 3, 1, 10.0))
    calls: list[tuple[str, int]] = []

    async def write(generation: int, on: bool) -> None:
        calls.append(("on" if on else "off", generation))

    async def read(generation: int) -> None:
        calls.append(("read", generation))
        supervisor.observe_ptt(
            tx.ProviderPttObservation(tx.RadioTx.ON, generation, 2, 10.0)
        )

    async def retire(_: int) -> None:
        raise AssertionError("smoke path must not retire")

    service = managed_tx_effect_service(
        _ManagedTxEffectHost(lambda: 10.0, write, read, retire)
    )
    transition = supervisor.request_on(tx.TxOwner(tx.TxSource.SDK, "client"))
    with patch.object(
        supervisor, "settle_attempt", wraps=supervisor.settle_attempt
    ) as settle:
        await asyncio.gather(
            service(supervisor, transition), service(supervisor, transition)
        )
    assert calls == [("on", 3), ("read", 3)]
    assert [call.args[:2] for call in settle.call_args_list] == [
        ("id-1", 3),
        ("id-2", 3),
    ]
    assert supervisor.snapshot.phase is tx.TxPhase.KEYED
