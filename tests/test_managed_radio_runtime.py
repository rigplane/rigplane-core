import asyncio
from collections.abc import Iterator

import pytest

from rigplane.core.tx_safety import (
    ProviderAttempt,
    ProviderAttemptKind,
    ProviderPttObservation,
    RadioTx,
    TxOwner,
    TxOutcome,
    TxPhase,
    TxReleaseReason,
    TxSafetySupervisor,
    TxSource,
    TxTransition,
)
from rigplane.runtime.managed_radio_runtime import ManagedRadioRuntime, TxService


def ids() -> Iterator[str]:
    index = 0
    while True:
        index += 1
        yield f"id-{index}"


async def no_effects(
    _supervisor: TxSafetySupervisor, _transition: TxTransition
) -> None:
    pass


def runtime(
    target: str = "radio-a",
    *,
    timeout: float = 0.01,
    service: TxService = no_effects,
) -> ManagedRadioRuntime:
    generated = ids()
    return ManagedRadioRuntime(
        target,
        service=service,
        clock=lambda: 10.0,
        id_factory=lambda: next(generated),
        shutdown_timeout_seconds=timeout,
    )


async def acquire(rt: ManagedRadioRuntime) -> tuple[TxOwner, ProviderAttempt]:
    await rt.replace_provider(ready=True)
    rt.tx_safety.observe_ptt(ProviderPttObservation(RadioTx.OFF, 1, 1, 10.0))
    owner = TxOwner(TxSource.WEBSOCKET, "web-1")
    attempt = (await rt.request_on(owner)).effects[0]
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

    changed = await first.replace_provider(ready=False)

    assert first.tx_safety is supervisor is not second.tx_safety
    assert changed.snapshot.lease_id == lease
    assert changed.snapshot.phase is TxPhase.RELEASE_REQUIRED


@pytest.mark.asyncio
async def test_ready_provider_services_pending_off_before_return() -> None:
    order: list[str] = []

    async def service(_supervisor: TxSafetySupervisor, transition: object) -> None:
        effect = getattr(transition, "effects")[0]
        order.append(getattr(effect, "kind", "cancel"))

    rt = runtime(service=service)
    owner, attempt = await acquire(rt)
    lease = rt.tx_safety.snapshot.lease_id or ""
    await rt.request_off(owner, lease)

    await rt.replace_provider(ready=False)
    await rt.set_provider_ready(ready=True)
    order.append("ordinary")

    assert order[-2:] == [ProviderAttemptKind.WRITE_OFF, "ordinary"]
    assert rt.tx_safety.snapshot.active_attempt != attempt


@pytest.mark.asyncio
async def test_managed_requests_service_once_and_return_owner_lease_identity() -> None:
    serviced: list[TxTransition] = []

    async def service(supervisor: TxSafetySupervisor, transition: TxTransition) -> None:
        assert supervisor is rt.tx_safety
        serviced.append(transition)

    rt = runtime(service=service)
    await rt.replace_provider(ready=True)
    rt.tx_safety.observe_ptt(ProviderPttObservation(RadioTx.OFF, 1, 1, 10.0))
    owner = TxOwner(TxSource.SDK, "sdk-1")

    keyed = await rt.request_on(owner)
    released = await rt.release_owner(owner, reason=TxReleaseReason.SOURCE_DETACHED)

    assert serviced == [keyed, released]
    assert keyed.snapshot.owner == owner
    assert keyed.snapshot.lease_id
    assert released.snapshot.lease_id == keyed.snapshot.lease_id
    assert released.snapshot.release_reason is TxReleaseReason.SOURCE_DETACHED


@pytest.mark.asyncio
async def test_request_off_service_error_propagates_and_retains_durable_off() -> None:
    fail = False

    async def service(
        _supervisor: TxSafetySupervisor, _transition: TxTransition
    ) -> None:
        if fail:
            raise RuntimeError("provider service failed")

    rt = runtime(service=service)
    owner, _ = await acquire(rt)
    lease = rt.tx_safety.snapshot.lease_id or ""
    fail = True

    with pytest.raises(RuntimeError, match="provider service failed"):
        await rt.request_off(owner, lease)

    assert rt.tx_safety.snapshot.phase is TxPhase.RELEASE_REQUIRED
    repeated = await rt.request_off(owner, lease)
    assert repeated.outcome is TxOutcome.IDEMPOTENT
    assert rt.tx_safety.snapshot.lease_id == lease


@pytest.mark.asyncio
async def test_shutdown_bounds_dekey_before_provider_release() -> None:
    order: list[str] = []
    armed = False

    async def service(_supervisor: TxSafetySupervisor, transition: object) -> None:
        if not armed or not getattr(transition, "effects"):
            return
        order.append("dekey")
        await asyncio.Event().wait()

    rt = runtime(service=service)
    await acquire(rt)
    armed = True

    async def release() -> None:
        order.append("release")

    result = await rt.shutdown(release_provider=release)

    assert order == ["dekey", "release"]
    assert result.snapshot.phase is TxPhase.RELEASE_REQUIRED


@pytest.mark.asyncio
async def test_shutdown_error_still_releases_provider() -> None:
    released = False

    armed = False

    async def service(_supervisor: TxSafetySupervisor, transition: object) -> None:
        if armed and getattr(transition, "effects"):
            raise RuntimeError("dekey failed")

    rt = runtime(service=service)
    await acquire(rt)
    armed = True

    async def release() -> None:
        nonlocal released
        released = True

    with pytest.raises(RuntimeError, match="dekey failed"):
        await rt.shutdown(release_provider=release)
    assert released
