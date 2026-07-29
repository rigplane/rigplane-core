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
from test_radio import MockTransport, _ptt_response


class ProviderLifecycle:
    def __init__(self) -> None:
        self.bindings: list[tuple[int, Callable[[ProviderPttObservation], None]]] = []
        self.current: tuple[int, Callable[[ProviderPttObservation], None]] | None = None
        self.bind_error = self.unbind_error = False
        self.unbinds = 0
        self.reads: list[tuple[int, Callable[[ProviderPttObservation], None]]] = []
        self.read_started, self.read_release = asyncio.Event(), asyncio.Event()
        self.read_blocked, self.emit_read_observation = False, True
        self.read_error: BaseException | None = None
        self.captures, self.capture_result = 0, True
        self.writes: list[tuple[int, bool]] = []
        self.retirements: list[int] = []
        self.retire_started, self.retire_release = asyncio.Event(), asyncio.Event()
        self.retire_error: BaseException | None = None
        self.retire_release.set()

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
        assert (
            self.current
            and self.current[0] == provider_generation
            and self.current[1] is observer
        )
        self.reads.append((provider_generation, observer))
        self.read_started.set()
        if self.read_blocked:
            await self.read_release.wait()
        if self.read_error is not None:
            raise self.read_error
        if self.emit_read_observation:
            observer(ProviderPttObservation(RadioTx.OFF, provider_generation, 2, 10.0))

    def _capture_managed_tx_port(
        self,
        provider_generation: int,
        observer: Callable[[ProviderPttObservation], None],
    ) -> bool:
        self.captures += 1
        self._bind_authoritative_ptt_observer(
            provider_generation=provider_generation, observer=observer
        )
        if not self.capture_result:
            self._unbind_authoritative_ptt_observer()
        return self.capture_result

    async def _write_managed_ptt(self, provider_generation: int, on: bool) -> None:
        self.writes.append((provider_generation, on))

    async def _retire_managed_tx_port(self, provider_generation: int) -> None:
        self.retirements.append(provider_generation)
        self.retire_started.set()
        await self.retire_release.wait()
        if self.retire_error is not None:
            raise self.retire_error
        self._unbind_authoritative_ptt_observer()


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
        service_factory=lambda _host: service,
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
    assert repeated.outcome is TxOutcome.IDEMPOTENT and rt.tx_snapshot.lease_id == lease


@pytest.mark.asyncio
async def test_shutdown_fences_readiness_and_terminal_reason() -> None:
    provider, started, finish = ProviderLifecycle(), asyncio.Event(), asyncio.Event()
    armed = False

    async def service(_supervisor: TxSafetySupervisor, transition: object) -> None:
        if not armed or not getattr(transition, "effects"):
            return
        await rt._effect_host.write(rt.tx_snapshot.provider_generation, False)
        started.set()
        await finish.wait()

    rt = runtime(service=service, lifecycle=provider)
    owner, _ = await acquire(rt)
    lease = rt.tx_snapshot.lease_id or ""
    await rt.request_off(owner, lease)
    await rt.replace_provider(ready=False)
    armed = True
    readiness = asyncio.create_task(rt.set_provider_ready(ready=True))
    await asyncio.wait_for(started.wait(), 0.2)
    shutdown = asyncio.create_task(
        rt.shutdown(release_provider=lambda: asyncio.sleep(0))
    )
    await asyncio.sleep(0)
    with pytest.raises(ConnectionError, match="stale"):
        await rt._effect_host.write(2, True)
    late = await asyncio.gather(
        rt.request_off(owner, lease),
        rt.release_owner(owner, reason=TxReleaseReason.SOURCE_DETACHED),
        rt.set_provider_ready(ready=False),
        rt.set_provider_ready(ready=True),
    )
    assert not readiness.done() and provider.writes
    assert all(not on for _generation, on in provider.writes)
    assert [item.outcome for item in late[2:]] == [TxOutcome.NOT_READY] * 2
    assert all(
        item.snapshot.terminal_release_reason is TxReleaseReason.SERVER_SHUTDOWN
        for item in late
    )
    finish.set()
    assert (await readiness).outcome is TxOutcome.NOT_READY
    assert (await shutdown).snapshot.phase is TxPhase.RELEASE_REQUIRED


@pytest.mark.parametrize(
    "error", [RuntimeError("dekey failed"), asyncio.CancelledError("retire-cancelled")]
)
@pytest.mark.asyncio
async def test_shutdown_error_is_shared(error: BaseException) -> None:
    provider = ProviderLifecycle()
    provider.retire_error = error
    provider.retire_release.clear()
    rt = runtime(lifecycle=provider)
    await acquire(rt)

    async def capture() -> BaseException:
        try:
            await rt.shutdown(release_provider=lambda: asyncio.sleep(0))
        except BaseException as raised:
            return raised
        raise AssertionError("shutdown did not propagate retirement failure")

    waiter = asyncio.create_task(rt.shutdown(release_provider=lambda: asyncio.sleep(0)))
    await provider.retire_started.wait()
    assert waiter.cancel()
    await asyncio.gather(waiter, return_exceptions=True)
    errors = asyncio.gather(capture(), capture())
    provider.retire_release.set()
    caught = await errors
    assert caught[0] is caught[1] is error and provider.retirements == [1]


@pytest.mark.asyncio
async def test_real_core_radio_keyword_port_binds_effectless_generation() -> None:
    radio = CoreRadio("127.0.0.1")
    rt = runtime(lifecycle=radio)
    changed = await rt.replace_provider(ready=False)
    assert changed.snapshot.provider_generation == 1
    assert radio._civ_runtime._ptt_observer_provider_generation == 1
    await rt.invalidate_provider(1)


@pytest.mark.asyncio
async def test_real_core_radio_fresh_read_preserves_healthy_generation() -> None:
    transport = MockTransport()
    radio = CoreRadio("127.0.0.1", timeout=0.05)
    radio._civ_transport = transport
    radio._connected = True
    rt = runtime(lifecycle=radio)
    await rt.replace_provider(ready=True)
    transport.queue_response(_ptt_response(False))
    result = await rt.request_fresh_ptt()

    assert result.outcome is TxOutcome.APPLIED and len(transport.sent_packets) == 1
    assert rt.tx_snapshot.provider_generation == 1 and rt.tx_snapshot.provider_ready
    assert rt.tx_snapshot.radio_tx is RadioTx.OFF
    radio._connected = False


@pytest.mark.asyncio
async def test_unbound_bind_failure_and_stale_callback_fail_closed() -> None:
    provider = ProviderLifecycle()
    rt = runtime(lifecycle=provider)
    owner = TxOwner(TxSource.SDK, "sdk")
    assert (await rt.request_on(owner)).outcome is TxOutcome.NOT_READY

    provider.capture_result = False
    assert (await rt.replace_provider(ready=True)).snapshot.provider_ready is False
    provider.capture_result = True
    provider.bind_error = True
    with pytest.raises(RuntimeError, match="bind failed"):
        await rt.replace_provider(ready=True)
    generation, observer = provider.bindings[-1]
    assert rt.tx_snapshot.provider_generation == generation == 2
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
    observer(ProviderPttObservation(RadioTx.ON, generation, 1, 10.0))
    assert rt.tx_snapshot.radio_tx is RadioTx.UNKNOWN
    assert (await rt.set_provider_ready(ready=True)).outcome is TxOutcome.NOT_READY


@pytest.mark.asyncio
async def test_factory_clock_capture_and_stale_host_are_generation_safe() -> None:
    provider = ProviderLifecycle()
    hosts: list[object] = []

    def clock() -> float:
        return 23.0

    rt = ManagedRadioRuntime(
        "radio",
        service_factory=lambda host: hosts.append(host) or no_effects,
        provider_lifecycle=provider,
        clock=clock,
    )
    await rt.replace_provider(ready=True)
    generation, observer = provider.bindings[-1]
    observer(ProviderPttObservation(RadioTx.OFF, generation, 1, 1.0))
    assert rt._tx_safety._observation.observed_at_monotonic == 23.0
    host = rt._effect_host
    await rt.replace_provider(ready=True)
    observer(ProviderPttObservation(RadioTx.ON, generation, 1, 10.0))
    for call in (
        host.write(generation, True),
        host.read(generation),
        host.retire(generation),
    ):
        with pytest.raises(ConnectionError, match="stale"):
            await call
    assert len(hosts) == 1 and provider.captures == 2
    assert host._clock is rt._tx_safety._clock is clock
    assert rt._tx_safety._observation is None
    assert (provider.writes, provider.reads, provider.retirements) == ([], [], [1])
    assert not any(
        hasattr(host, name) for name in ("runtime", "provider", "transport", "set_ptt")
    )


@pytest.mark.parametrize(
    "retire_error", [RuntimeError("retire failed"), asyncio.CancelledError("cancelled")]
)
@pytest.mark.asyncio
async def test_failed_retirement_retry_observes_latched_outcome(
    retire_error: BaseException,
) -> None:
    provider = ProviderLifecycle()
    rt = runtime(lifecycle=provider)
    await rt.replace_provider(ready=True)
    provider.retire_error = retire_error
    tasks, errors = [], []
    for _attempt in range(2):
        with pytest.raises(type(retire_error), match=str(retire_error)) as raised:
            await rt._effect_host.retire(1)
        tasks.append(rt._retirement_task)
        errors.append(raised.value)

    assert tasks[0] is tasks[1] and tasks[0] is not None and tasks[0].done()
    assert errors[0] is errors[1] is retire_error
    assert provider.retirements == [1] and rt._provider_state == "invalidating"
    assert not rt._lifecycle_lock.locked()
    assert rt.tx_snapshot.provider_generation == 2 and not rt.tx_snapshot.provider_ready
    for stale in (0, 2):
        with pytest.raises(ConnectionError, match="stale"):
            await rt._effect_host.retire(stale)
    assert provider.retirements == [1]


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
async def test_shutdown_is_terminal_and_cleans_up_exactly_once() -> None:
    provider = ProviderLifecycle()
    serviced: list[TxTransition] = []
    releases = 0
    sequence = 1

    async def service(supervisor: TxSafetySupervisor, transition: TxTransition) -> None:
        nonlocal sequence
        serviced.append(transition)
        effect = transition.effects[0]
        assert isinstance(effect, ProviderAttempt)
        assert provider.current is not None
        value = (
            RadioTx.ON if effect.kind is ProviderAttemptKind.WRITE_ON else RadioTx.OFF
        )
        followup = supervisor.settle_attempt(
            effect.id, effect.provider_generation, succeeded=True
        )
        read = followup.effects[0]
        assert isinstance(read, ProviderAttempt)
        sequence += 1
        provider.current[1](
            ProviderPttObservation(value, effect.provider_generation, sequence, 10.0)
        )
        supervisor.settle_attempt(read.id, read.provider_generation, succeeded=True)

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
    assert rt.tx_snapshot.provider_generation == 2 and not rt.tx_snapshot.provider_ready
    assert rt.tx_snapshot.radio_tx is RadioTx.UNKNOWN
    assert rt.tx_snapshot.lease_id == first.snapshot.lease_id
    assert rt.tx_snapshot.terminal_release_reason is TxReleaseReason.SERVER_SHUTDOWN
    assert (await rt.replace_provider(ready=True)).outcome is TxOutcome.NOT_READY
    assert (await rt.request_fresh_ptt()).outcome is TxOutcome.NOT_READY
    assert (await rt.set_provider_ready(ready=True)).outcome is TxOutcome.NOT_READY
    assert (await rt.request_on(owner)).outcome is TxOutcome.NOT_READY
