import asyncio
from collections.abc import Iterator

import pytest

from rigplane.core.tx_safety import (
    ProviderAttempt,
    ProviderAttemptKind,
    ProviderPttObservation,
    RadioTx,
    TxOwner,
    TxPhase,
    TxSafetySupervisor,
    TxSource,
)
from rigplane.runtime.managed_radio_runtime import ManagedRadioRuntime


def ids() -> Iterator[str]:
    index = 0
    while True:
        index += 1
        yield f"id-{index}"


def runtime(target: str = "radio-a", *, timeout: float = 0.01) -> ManagedRadioRuntime:
    generated = ids()
    return ManagedRadioRuntime(
        target,
        clock=lambda: 10.0,
        id_factory=lambda: next(generated),
        shutdown_timeout_seconds=timeout,
    )


async def no_effects(_supervisor: TxSafetySupervisor, transition: object) -> None:
    assert not getattr(transition, "effects")


async def acquire(rt: ManagedRadioRuntime) -> tuple[TxOwner, ProviderAttempt]:
    await rt.replace_provider(ready=True, service=no_effects)
    rt.tx_safety.observe_ptt(ProviderPttObservation(RadioTx.OFF, 1, 1, 10.0))
    owner = TxOwner(TxSource.WEBSOCKET, "web-1")
    attempt = rt.tx_safety.request_on(owner).effects[0]
    assert isinstance(attempt, ProviderAttempt)
    return owner, attempt


@pytest.mark.parametrize("timeout", [0, float("nan"), float("inf")])
def test_shutdown_timeout_must_be_bounded(timeout: float) -> None:
    with pytest.raises(ValueError):
        runtime(timeout=timeout)


@pytest.mark.asyncio
async def test_target_owns_one_supervisor_across_consumers_and_generations() -> None:
    first, second = runtime("a"), runtime("b")
    supervisor = first.tx_safety
    await acquire(first)
    lease = first.tx_safety.snapshot.lease_id

    changed = await first.replace_provider(ready=False, service=no_effects)

    assert first.tx_safety is supervisor is not second.tx_safety
    assert changed.snapshot.lease_id == lease
    assert changed.snapshot.phase is TxPhase.RELEASE_REQUIRED


@pytest.mark.asyncio
async def test_ready_provider_services_pending_off_before_return() -> None:
    rt = runtime()
    owner, attempt = await acquire(rt)
    lease = rt.tx_safety.snapshot.lease_id or ""
    rt.tx_safety.request_off(owner, lease)
    order: list[str] = []

    async def service(_supervisor: TxSafetySupervisor, transition: object) -> None:
        effect = getattr(transition, "effects")[0]
        assert effect.kind is ProviderAttemptKind.WRITE_OFF
        order.append("off")

    await rt.replace_provider(ready=False, service=no_effects)
    await rt.set_provider_ready(ready=True, service=service)
    order.append("ordinary")

    assert order == ["off", "ordinary"]
    assert rt.tx_safety.snapshot.active_attempt != attempt


@pytest.mark.asyncio
async def test_shutdown_bounds_dekey_before_provider_release() -> None:
    rt = runtime()
    await acquire(rt)
    order: list[str] = []

    async def dekey(_supervisor: TxSafetySupervisor, _transition: object) -> None:
        order.append("dekey")
        await asyncio.Event().wait()

    async def release() -> None:
        order.append("release")

    result = await rt.shutdown(dekey=dekey, release_provider=release)

    assert order == ["dekey", "release"]
    assert result.snapshot.phase is TxPhase.RELEASE_REQUIRED


@pytest.mark.asyncio
async def test_shutdown_error_still_releases_provider() -> None:
    rt = runtime()
    await acquire(rt)
    released = False

    async def fail(_supervisor: TxSafetySupervisor, _transition: object) -> None:
        raise RuntimeError("dekey failed")

    async def release() -> None:
        nonlocal released
        released = True

    with pytest.raises(RuntimeError, match="dekey failed"):
        await rt.shutdown(dekey=fail, release_provider=release)
    assert released
