import asyncio
from dataclasses import replace
from itertools import count
from unittest.mock import patch

import pytest

from rigplane.core import tx_safety as tx
from rigplane.runtime.managed_radio_runtime import _ManagedTxEffectHost as Host
from rigplane.runtime.managed_tx_effect_service import (
    managed_tx_effect_service as make_service,
)

_OWNER = tx.TxOwner(tx.TxSource.SDK, "client")


def _acquire(
    generation: int, now: list[float], *, attempt_timeout: float = 0.2
) -> tuple[tx.TxSafetySupervisor, tx.TxTransition]:
    ids = count()
    supervisor = tx.TxSafetySupervisor(
        clock=lambda: now[0],
        id_factory=lambda: f"{generation}-{next(ids)}",
        write_timeout_seconds=attempt_timeout,
        read_timeout_seconds=attempt_timeout,
        cancel_timeout_seconds=0.02,
        retry_schedule_seconds=(0.01,),
    )
    supervisor.replace_provider(generation, ready=True)
    supervisor.observe_ptt(
        tx.ProviderPttObservation(tx.RadioTx.OFF, generation, 1, now[0])
    )
    return supervisor, supervisor.request_on(_OWNER)


def _release(supervisor: tx.TxSafetySupervisor) -> tx.TxTransition:
    return supervisor.request_off(_OWNER, supervisor.snapshot.lease_id or "")


@pytest.mark.asyncio
async def test_iterative_exact_claims_and_retention_stay_bounded() -> None:
    now, calls = [10.0], []

    async def provider(generation: int, on: bool | None = None) -> None:
        calls.append(("read" if on is None else "on" if on else "off", generation))
        if on is None:
            current.observe_ptt(
                tx.ProviderPttObservation(tx.RadioTx.ON, generation, 2, now[0])
            )

    service = make_service(Host(lambda: now[0], provider, provider, provider))
    for generation in range(1, 41):
        current, transition = _acquire(generation, now)
        if generation == 1:
            first = current, transition
            with patch.object(
                type(service), "__call__", autospec=True, wraps=type(service).__call__
            ) as facade:
                await service(current, transition)
            assert facade.await_count == 1
        else:
            await asyncio.gather(
                service(current, transition), service(current, transition)
            )
        await service(current, transition)
        assert not getattr(service, "_claims")
    await service(*first)
    assert calls == [
        (kind, generation) for generation in range(1, 41) for kind in ("on", "read")
    ]
    assert getattr(service, "_barrier") == (40, None)


@pytest.mark.parametrize("admitted", [False, True])
@pytest.mark.asyncio
async def test_cancel_settles_xor_retires_and_drains_release(admitted: bool) -> None:
    now = [10.0]
    supervisor, pending = _acquire(7, now)
    started, blocked = asyncio.Event(), asyncio.Event()
    calls: list[tuple[str, int]] = []
    retirements: list[int] = []

    async def write(generation: int, on: bool) -> None:
        calls.append(("on" if on else "off", generation))
        if on:
            started.set()
            await blocked.wait()

    async def read(generation: int) -> None:
        calls.append(("read", generation))
        supervisor.observe_ptt(
            tx.ProviderPttObservation(tx.RadioTx.OFF, generation, 2, now[0])
        )

    async def retire(generation: int) -> None:
        retirements.append(generation)

    service = make_service(Host(lambda: now[0], write, read, retire))
    barriers, real_settle = [], supervisor.settle_attempt
    running = asyncio.create_task(service(supervisor, pending)) if admitted else None
    if running:
        await asyncio.wait_for(started.wait(), 0.2)
    cancel = _release(supervisor)

    def record_settle(attempt_id: str, generation: int, **kwargs):
        barriers.append(getattr(service, "_barrier"))
        return real_settle(attempt_id, generation, **kwargs)

    with patch.object(
        supervisor, "settle_attempt", side_effect=record_settle
    ) as settle:
        await service(supervisor, cancel)
        if not admitted:
            assert barriers[0] == (7, "7-1")
        if running:
            await running
        await asyncio.gather(service(supervisor, pending), service(supervisor, cancel))
    expected = ([("on", 7)] if admitted else []) + [("off", 7), ("read", 7)]
    assert calls == expected and not retirements
    settled = [call.args[:2] for call in settle.call_args_list]
    assert settled == [("7-1", 7), ("7-2", 7), ("7-3", 7)]
    assert not getattr(service, "_claims")


@pytest.mark.asyncio
async def test_poison_deadline_retirement_barrier_and_late_failure_are_inert() -> None:
    now = [10.0]
    old, pending = _acquire(9, now, attempt_timeout=0.08)
    started, cancelled, late_release, retire_started, retire_release = (
        asyncio.Event() for _ in range(5)
    )
    calls: list[tuple[str, int]] = []
    cancellations = retirements = 0
    newer: tx.TxSafetySupervisor | None = None

    async def write(generation: int, on: bool) -> None:
        nonlocal cancellations
        calls.append(("on" if on else "off", generation))
        if generation == 9:
            started.set()
            while not late_release.is_set():
                try:
                    await late_release.wait()
                except asyncio.CancelledError:
                    cancellations += 1
                    cancelled.set()
            raise RuntimeError("late provider failure")

    async def read(generation: int) -> None:
        calls.append(("read", generation))
        assert newer is not None
        newer.observe_ptt(
            tx.ProviderPttObservation(tx.RadioTx.ON, generation, 2, now[0])
        )

    async def retire(generation: int) -> None:
        nonlocal retirements
        retirements += 1
        assert generation == 9
        assert cancellations >= 2 and getattr(service, "_lane") is not old_lane
        retire_started.set()
        await retire_release.wait()

    service = make_service(Host(lambda: now[0], write, read, retire))
    old_lane = getattr(service, "_lane")
    old_call = asyncio.create_task(service(old, pending))
    await asyncio.wait_for(started.wait(), 0.2)
    cancel = _release(old)
    with patch.object(old, "settle_attempt", wraps=old.settle_attempt) as settle:
        cancel_call = asyncio.create_task(service(old, cancel))
        await asyncio.wait_for(cancelled.wait(), 0.2)
        now[0] = 11.0
        effect = cancel.effects[0]
        renewed = replace(effect, settlement_deadline_monotonic=99.0)
        duplicate = replace(cancel, effects=(renewed,))
        try:
            duplicate_call = asyncio.create_task(service(old, duplicate))
            await asyncio.wait_for(retire_started.wait(), 0.2)
            await asyncio.wait_for(
                asyncio.gather(cancel_call, duplicate_call), timeout=0.3
            )
            assert len(getattr(service, "_claims")) == 1
            newer, transition = _acquire(10, now, attempt_timeout=0.4)
            newer_call = asyncio.create_task(service(newer, transition))
            for _ in range(3):
                await asyncio.sleep(0)
            premature = calls != [("on", 9)]
            retire_release.set()
            await asyncio.wait_for(newer_call, 0.2)
            late_release.set()
            await asyncio.wait_for(old_call, 0.2)
        finally:
            late_release.set()
            retire_release.set()
    assert settle.call_count == 0 and cancellations >= 2 and retirements == 1
    assert not premature and calls == [("on", 9), ("on", 10), ("read", 10)]
    assert newer.snapshot.phase is tx.TxPhase.KEYED and not getattr(service, "_claims")


@pytest.mark.asyncio
async def test_failed_off_and_stale_readback_remain_durable_and_retryable() -> None:
    now = [10.0]
    supervisor, acquire = _acquire(11, now)
    calls: list[tuple[str, int]] = []
    off_attempts = reads = 0

    async def write(generation: int, on: bool) -> None:
        nonlocal off_attempts
        calls.append(("on" if on else "off", generation))
        if not on:
            off_attempts += 1
            if off_attempts == 1:
                raise RuntimeError("dekey failed")

    async def read(generation: int) -> None:
        nonlocal reads
        reads += 1
        calls.append(("read", generation))
        value = tx.RadioTx.ON if reads == 1 else tx.RadioTx.OFF
        observed_generation = 10 if reads == 2 else generation
        supervisor.observe_ptt(
            tx.ProviderPttObservation(value, observed_generation, reads + 1, now[0])
        )

    async def retire(_: int) -> None:
        raise AssertionError("settled provider operations must not retire")

    service = make_service(Host(lambda: now[0], write, read, retire))
    await service(supervisor, acquire)
    assert supervisor.snapshot.phase is tx.TxPhase.KEYED
    release = _release(supervisor)
    await service(supervisor, release)
    fault = supervisor.snapshot
    assert fault.phase is tx.TxPhase.FAULTED
    assert fault.release_last_error == "dekey failed" and fault.lease_id
    await service(supervisor, release)
    assert off_attempts == 1
    now[0] += 0.02
    await service(supervisor, supervisor.tick())
    stale = supervisor.snapshot
    assert stale.phase is tx.TxPhase.FAULTED
    assert stale.release_last_error == "read_ptt_unconfirmed"
    now[0] += 0.02
    await service(supervisor, supervisor.tick())
    assert supervisor.snapshot.phase is tx.TxPhase.IDLE
    retry = [("off", 11), ("read", 11)]
    assert calls == [("on", 11), ("read", 11), ("off", 11)] + retry * 2
