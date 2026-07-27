import asyncio
from collections.abc import Callable, Iterator
from dataclasses import FrozenInstanceError

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
from rigplane.runtime.managed_radio_runtime import (
    ManagedRadioRuntime,
    ProviderTxLifecycle,
    TxService,
)
from rigplane.runtime.radio import CoreRadio


class ProviderLifecycle:
    def __init__(self) -> None:
        self.bindings: list[tuple[int, Callable[[ProviderPttObservation], None]]] = []
        self.current: tuple[int, Callable[[ProviderPttObservation], None]] | None = None
        self.bind_error = self.unbind_error = False

    def _bind_authoritative_ptt_observer(
        self,
        *,
        provider_generation: int,
        observer: Callable[[ProviderPttObservation], None],
    ) -> None:
        self.current = (provider_generation, observer)
        self.bindings.append(self.current)
        if self.bind_error:
            raise RuntimeError("bind failed")

    def _unbind_authoritative_ptt_observer(self) -> None:
        if self.unbind_error:
            raise RuntimeError("unbind failed")
        self.current = None

    async def _request_authoritative_ptt_read(
        self,
        *,
        provider_generation: int,
        observer: Callable[[ProviderPttObservation], None],
    ) -> None:
        assert self.current == (provider_generation, observer)
        observer(ProviderPttObservation(RadioTx.OFF, provider_generation, 1, 10.0))


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
    lifecycle: ProviderTxLifecycle | None = None,
) -> ManagedRadioRuntime:
    generated = ids()
    return ManagedRadioRuntime(
        target,
        service=service,
        provider_lifecycle=lifecycle or ProviderLifecycle(),
        clock=lambda: 10.0,
        id_factory=lambda: next(generated),
        shutdown_timeout_seconds=timeout,
    )


async def acquire(rt: ManagedRadioRuntime) -> tuple[TxOwner, ProviderAttempt]:
    await rt.replace_provider(ready=True)
    rt._tx_safety.observe_ptt(ProviderPttObservation(RadioTx.OFF, 1, 1, 10.0))
    owner = TxOwner(TxSource.WEBSOCKET, "web-1")
    attempt = (await rt.request_on(owner)).effects[0]
    assert isinstance(attempt, ProviderAttempt)
    return owner, attempt


@pytest.mark.parametrize("timeout", [0, float("nan"), float("inf")])
def test_shutdown_timeout_must_be_bounded(timeout: float) -> None:
    with pytest.raises(ValueError):
        runtime(timeout=timeout)


@pytest.mark.asyncio
async def test_runtime_does_not_expose_mutable_supervisor() -> None:
    rt = runtime()
    await rt.replace_provider(ready=True)
    snapshot = rt.tx_snapshot

    assert not hasattr(rt, "tx_safety")
    assert not hasattr(snapshot, "request_on")
    assert not hasattr(snapshot, "replace_provider")
    with pytest.raises(FrozenInstanceError):
        setattr(snapshot, "provider_generation", 999)
    assert rt.tx_snapshot.provider_generation == 1


@pytest.mark.asyncio
async def test_target_owns_one_supervisor_across_consumers_and_generations() -> None:
    first, second = runtime("a"), runtime("b")
    supervisor = first._tx_safety
    await acquire(first)
    lease = first.tx_snapshot.lease_id

    changed = await first.replace_provider(ready=False)

    assert first._tx_safety is supervisor is not second._tx_safety
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
    lease = rt.tx_snapshot.lease_id or ""
    await rt.request_off(owner, lease)

    await rt.replace_provider(ready=False)
    await rt.set_provider_ready(ready=True)
    order.append("ordinary")

    assert order[-2:] == [ProviderAttemptKind.WRITE_OFF, "ordinary"]
    assert rt.tx_snapshot.active_attempt != attempt


@pytest.mark.asyncio
async def test_managed_requests_service_once_and_return_owner_lease_identity() -> None:
    serviced: list[TxTransition] = []

    async def service(supervisor: TxSafetySupervisor, transition: TxTransition) -> None:
        assert supervisor is rt._tx_safety
        serviced.append(transition)

    rt = runtime(service=service)
    await rt.replace_provider(ready=True)
    rt._tx_safety.observe_ptt(ProviderPttObservation(RadioTx.OFF, 1, 1, 10.0))
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
    lease = rt.tx_snapshot.lease_id or ""
    fail = True

    with pytest.raises(RuntimeError, match="provider service failed"):
        await rt.request_off(owner, lease)

    assert rt.tx_snapshot.phase is TxPhase.RELEASE_REQUIRED
    repeated = await rt.request_off(owner, lease)
    assert repeated.outcome is TxOutcome.IDEMPOTENT
    assert rt.tx_snapshot.lease_id == lease


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


@pytest.mark.asyncio
async def test_real_core_radio_keyword_port_binds_effectless_generation() -> None:
    radio = CoreRadio("127.0.0.1")
    rt = runtime(lifecycle=radio)

    changed = await rt.replace_provider(ready=False)

    assert changed.snapshot.provider_generation == 1
    assert radio._civ_runtime._ptt_observer_provider_generation == 1
    await rt.invalidate_provider(1)


@pytest.mark.asyncio
async def test_unbound_bind_failure_and_stale_callback_fail_closed() -> None:
    provider = ProviderLifecycle()
    rt = runtime(lifecycle=provider)
    owner = TxOwner(TxSource.SDK, "sdk")
    assert (await rt.request_on(owner)).outcome is TxOutcome.NOT_READY

    provider.bind_error = True
    with pytest.raises(RuntimeError, match="bind failed"):
        await rt.replace_provider(ready=True)
    generation, observer = provider.bindings[-1]
    assert rt.tx_snapshot.provider_generation == generation == 1
    assert rt.tx_snapshot.provider_ready is False
    assert callable(observer)
    observer(ProviderPttObservation(RadioTx.ON, generation, 1, 10.0))
    assert rt.tx_snapshot.radio_tx is RadioTx.UNKNOWN
    assert (await rt.request_on(owner)).outcome is TxOutcome.NOT_READY
    assert (await rt.set_provider_ready(ready=True)).outcome is TxOutcome.NOT_READY


@pytest.mark.asyncio
async def test_unbind_failure_still_invalidates_host_and_old_callback() -> None:
    provider = ProviderLifecycle()
    rt = runtime(lifecycle=provider)
    await rt.replace_provider(ready=True)
    generation, observer = provider.bindings[-1]
    provider.unbind_error = True

    with pytest.raises(RuntimeError, match="unbind failed"):
        await rt.invalidate_provider(generation)

    assert rt.tx_snapshot.provider_generation == 2
    assert rt.tx_snapshot.provider_ready is False
    assert callable(observer)
    observer(ProviderPttObservation(RadioTx.ON, generation, 1, 10.0))
    assert rt.tx_snapshot.radio_tx is RadioTx.UNKNOWN
    assert (await rt.set_provider_ready(ready=True)).outcome is TxOutcome.NOT_READY


@pytest.mark.asyncio
async def test_replacement_rejects_old_generation_callback() -> None:
    provider = ProviderLifecycle()
    rt = runtime(lifecycle=provider)
    await rt.replace_provider(ready=True)
    generation, observer = provider.bindings[-1]

    await rt.replace_provider(ready=True)
    observer(ProviderPttObservation(RadioTx.ON, generation, 1, 10.0))

    assert rt.tx_snapshot.provider_generation == 2
    assert rt.tx_snapshot.radio_tx is RadioTx.UNKNOWN
