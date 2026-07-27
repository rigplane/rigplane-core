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
        self.unbinds = 0
        self.reads: list[tuple[int, Callable[[ProviderPttObservation], None]]] = []
        self.read_started = asyncio.Event()
        self.read_release = asyncio.Event()
        self.read_blocked = False
        self.read_error: BaseException | None = None

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
        self.unbinds += 1
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
        self.reads.append((provider_generation, observer))
        self.read_started.set()
        if self.read_blocked:
            await self.read_release.wait()
        if self.read_error is not None:
            raise self.read_error
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


async def no_release() -> None:
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


@pytest.mark.asyncio
async def test_fresh_read_serializes_replacement_and_rejects_a_b_a_callback() -> None:
    provider = ProviderLifecycle()
    provider.read_blocked = True
    rt = runtime(lifecycle=provider)
    await rt.replace_provider(ready=True)
    generation, old_observer = provider.bindings[-1]

    read = asyncio.create_task(rt.request_fresh_ptt())
    await provider.read_started.wait()
    replaced = asyncio.create_task(rt.replace_provider(ready=True))
    await asyncio.sleep(0)
    assert not replaced.done()

    provider.read_release.set()
    assert (await read).outcome is TxOutcome.APPLIED
    assert (await replaced).snapshot.provider_generation == 2
    await rt.replace_provider(ready=True)
    old_observer(ProviderPttObservation(RadioTx.ON, generation, 2, 10.0))

    assert provider.reads[0] == (generation, old_observer)
    assert rt.tx_snapshot.provider_generation == 3
    assert rt.tx_snapshot.radio_tx is RadioTx.UNKNOWN


@pytest.mark.asyncio
async def test_failed_and_cancelled_fresh_reads_invalidate_and_release_lane() -> None:
    provider = ProviderLifecycle()
    rt = runtime(lifecycle=provider)
    await rt.replace_provider(ready=True)
    provider.read_error = RuntimeError("source epoch changed")

    with pytest.raises(RuntimeError, match="source epoch changed"):
        await rt.request_fresh_ptt()
    assert rt.tx_snapshot.provider_generation == 2
    assert rt.tx_snapshot.provider_ready is False
    assert (await rt.request_fresh_ptt()).outcome is TxOutcome.NOT_READY

    provider.read_error = None
    provider.read_blocked = True
    provider.read_started.clear()
    await rt.replace_provider(ready=True)
    pending = asyncio.create_task(rt.request_fresh_ptt())
    await provider.read_started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    assert rt.tx_snapshot.provider_generation == 4
    assert rt.tx_snapshot.provider_ready is False
    assert (await rt.replace_provider(ready=False)).snapshot.provider_generation == 5


@pytest.mark.asyncio
async def test_fresh_read_serializes_terminal_shutdown() -> None:
    provider = ProviderLifecycle()
    provider.read_blocked = True
    rt = runtime(lifecycle=provider)
    await rt.replace_provider(ready=True)
    read = asyncio.create_task(rt.request_fresh_ptt())
    await provider.read_started.wait()
    shutdown = asyncio.create_task(rt.shutdown(release_provider=no_release))
    await asyncio.sleep(0)
    assert not shutdown.done()

    provider.read_release.set()
    assert (await read).outcome is TxOutcome.APPLIED
    await shutdown

    assert provider.unbinds == 1
    assert rt.tx_snapshot.provider_generation == 2
    assert rt.tx_snapshot.provider_ready is False


@pytest.mark.asyncio
async def test_shutdown_is_terminal_and_cleans_up_exactly_once() -> None:
    provider = ProviderLifecycle()
    serviced: list[TxTransition] = []
    releases = 0

    async def service(
        _supervisor: TxSafetySupervisor, transition: TxTransition
    ) -> None:
        serviced.append(transition)

    async def release() -> None:
        nonlocal releases
        releases += 1

    rt = runtime(service=service, lifecycle=provider)
    owner, _ = await acquire(rt)
    generation, observer = provider.bindings[-1]
    serviced.clear()

    first = await rt.shutdown(release_provider=release)
    second = await rt.shutdown(release_provider=release)
    observer(ProviderPttObservation(RadioTx.ON, generation, 2, 10.0))

    assert first is second
    assert len(serviced) == provider.unbinds == releases == 1
    assert rt.tx_snapshot.provider_generation == 2
    assert rt.tx_snapshot.provider_ready is False
    assert rt.tx_snapshot.radio_tx is RadioTx.UNKNOWN
    assert (await rt.replace_provider(ready=True)).outcome is TxOutcome.NOT_READY
    assert (await rt.request_fresh_ptt()).outcome is TxOutcome.NOT_READY
    assert (await rt.set_provider_ready(ready=True)).outcome is TxOutcome.NOT_READY
    assert (await rt.request_on(owner)).outcome is TxOutcome.NOT_READY


@pytest.mark.asyncio
async def test_shutdown_unbind_error_still_releases_and_stays_terminal() -> None:
    provider = ProviderLifecycle()
    rt = runtime(lifecycle=provider)
    await rt.replace_provider(ready=True)
    provider.unbind_error = True
    released = False

    async def release() -> None:
        nonlocal released
        released = True

    with pytest.raises(RuntimeError, match="unbind failed"):
        await rt.shutdown(release_provider=release)

    assert released
    assert rt.tx_snapshot.provider_generation == 2
    assert rt.tx_snapshot.provider_ready is False
    assert (await rt.replace_provider(ready=True)).outcome is TxOutcome.NOT_READY
