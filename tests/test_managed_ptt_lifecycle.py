from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import pytest

from rigplane.core.tx_safety import ProviderPttObservation, RadioTx, TxOwner, TxSource
from rigplane.runtime.managed_radio_runtime import ManagedRadioRuntime
from rigplane.runtime.managed_tx_effect_service import managed_tx_effect_service


@dataclass
class _Port:
    current: object | None


def _lifecycle(
    port: _Port,
    *,
    write: Callable[[bool], Awaitable[None]],
    read: Callable[[], Awaitable[Any]],
    drain_timeout: float = 0.02,
):
    from rigplane.runtime.managed_ptt_lifecycle import ProviderTxLifecycle

    return ProviderTxLifecycle(
        port_token=lambda: port.current,
        write_ptt=write,
        read_ptt=read,
        clock=lambda: 12.5,
        drain_timeout_seconds=drain_timeout,
    )


async def test_write_ack_alone_never_publishes_authoritative_ptt() -> None:
    writes: list[bool] = []
    observations: list[ProviderPttObservation] = []
    port = _Port(object())

    async def write(on: bool) -> None:
        writes.append(on)

    async def read() -> bool:
        return True

    lifecycle = _lifecycle(port, write=write, read=read)
    assert lifecycle._capture_managed_tx_port(1, observations.append)

    await lifecycle._write_managed_ptt(1, True)

    assert writes == [True]
    assert observations == []


async def test_stale_generation_cannot_write_or_publish() -> None:
    writes: list[bool] = []
    observations: list[ProviderPttObservation] = []
    observer = observations.append
    port = _Port(object())

    async def write(on: bool) -> None:
        writes.append(on)

    async def read() -> bool:
        return True

    lifecycle = _lifecycle(port, write=write, read=read)
    assert lifecycle._capture_managed_tx_port(1, observer)
    await lifecycle._retire_managed_tx_port(1)
    port.current = object()
    assert lifecycle._capture_managed_tx_port(2, observer)

    with pytest.raises(ConnectionError, match="stale"):
        await lifecycle._write_managed_ptt(1, True)
    with pytest.raises(ConnectionError, match="stale"):
        await lifecycle._request_authoritative_ptt_read(
            provider_generation=1, observer=observer
        )

    assert writes == []
    assert observations == []


async def test_late_read_after_retirement_is_inert() -> None:
    started, release = asyncio.Event(), asyncio.Event()
    observations: list[ProviderPttObservation] = []
    observer = observations.append
    port = _Port(object())

    async def write(_on: bool) -> None:
        return None

    async def read() -> bool:
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            await release.wait()
        return True

    lifecycle = _lifecycle(port, write=write, read=read)
    assert lifecycle._capture_managed_tx_port(1, observer)
    pending = asyncio.create_task(
        lifecycle._request_authoritative_ptt_read(
            provider_generation=1, observer=observer
        )
    )
    await started.wait()
    retirement = asyncio.create_task(lifecycle._retire_managed_tx_port(1))
    await asyncio.sleep(0)
    release.set()
    await retirement

    with pytest.raises(ConnectionError, match="stale"):
        await pending
    assert observations == []


async def test_retirement_prevents_stale_on_after_replacement() -> None:
    started, release = asyncio.Event(), asyncio.Event()
    writes: list[bool] = []
    port = _Port(object())

    async def write(on: bool) -> None:
        if not writes:
            started.set()
            await release.wait()
        writes.append(on)

    async def read() -> bool:
        return False

    lifecycle = _lifecycle(port, write=write, read=read)
    assert lifecycle._capture_managed_tx_port(1, lambda _observation: None)
    stale_on = asyncio.create_task(lifecycle._write_managed_ptt(1, True))
    await started.wait()

    await lifecycle._retire_managed_tx_port(1)
    port.current = object()
    assert lifecycle._capture_managed_tx_port(2, lambda _observation: None)

    with pytest.raises(asyncio.CancelledError):
        await stale_on
    release.set()
    await lifecycle._write_managed_ptt(2, False)
    assert writes == [False]


async def test_replacement_waits_for_stubborn_old_write_to_be_terminal() -> None:
    started, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    events: list[str] = []
    port = _Port(object())

    async def stubborn_write(on: bool) -> None:
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        events.append(f"wire:{on}")

    async def read() -> bool:
        return False

    lifecycle = _lifecycle(port, write=stubborn_write, read=read, drain_timeout=0.01)
    assert lifecycle._capture_managed_tx_port(1, lambda _observation: None)
    stale_on = asyncio.create_task(lifecycle._write_managed_ptt(1, True))
    await started.wait()

    before = time.monotonic()
    await lifecycle._retire_managed_tx_port(1)
    elapsed = time.monotonic() - before
    assert cancelled.is_set()
    assert elapsed < 0.2
    assert not stale_on.done()

    port.current = object()
    try:
        with pytest.raises(ConnectionError, match="write.*pending"):
            lifecycle._capture_managed_tx_port(2, lambda _observation: None)
    finally:
        release.set()
        with pytest.raises(ConnectionError, match="stale"):
            await stale_on

    assert events == ["wire:True"]
    assert lifecycle._capture_managed_tx_port(2, lambda _observation: None)
    events.append("replacement:valid")
    assert events == ["wire:True", "replacement:valid"]


@pytest.mark.parametrize("result", [RuntimeError("read failed"), 1])
async def test_read_failure_or_malformed_value_never_becomes_truth(
    result: BaseException | int,
) -> None:
    observations: list[ProviderPttObservation] = []
    observer = observations.append
    port = _Port(object())

    async def write(_on: bool) -> None:
        return None

    async def read() -> Any:
        if isinstance(result, BaseException):
            raise result
        return result

    lifecycle = _lifecycle(port, write=write, read=read)
    assert lifecycle._capture_managed_tx_port(1, observer)

    with pytest.raises((RuntimeError, ValueError)):
        await lifecycle._request_authoritative_ptt_read(
            provider_generation=1, observer=observer
        )
    assert observations == []


async def test_authoritative_observation_sequence_is_monotonic() -> None:
    values = iter((False, True, False))
    observations: list[ProviderPttObservation] = []
    observer = observations.append
    port = _Port(object())

    async def write(_on: bool) -> None:
        return None

    async def read() -> bool:
        return next(values)

    lifecycle = _lifecycle(port, write=write, read=read)
    assert lifecycle._capture_managed_tx_port(7, observer)
    for _ in range(3):
        await lifecycle._request_authoritative_ptt_read(
            provider_generation=7, observer=observer
        )

    assert [item.ptt_observation_seq for item in observations] == [1, 2, 3]
    assert [item.value for item in observations] == [
        RadioTx.OFF,
        RadioTx.ON,
        RadioTx.OFF,
    ]
    assert {item.provider_generation for item in observations} == {7}
    assert {item.observed_at_monotonic for item in observations} == {12.5}


async def test_physical_port_token_mismatch_fails_closed() -> None:
    writes: list[bool] = []
    observations: list[ProviderPttObservation] = []
    observer = observations.append
    port = _Port(object())

    async def write(on: bool) -> None:
        writes.append(on)

    async def read() -> bool:
        return False

    lifecycle = _lifecycle(port, write=write, read=read)
    assert lifecycle._capture_managed_tx_port(1, observer)
    port.current = object()

    with pytest.raises(ConnectionError, match="stale"):
        await lifecycle._write_managed_ptt(1, True)
    with pytest.raises(ConnectionError, match="stale"):
        await lifecycle._request_authoritative_ptt_read(
            provider_generation=1, observer=observer
        )

    assert writes == []
    assert observations == []


async def test_retirement_drain_is_bounded_and_cancellation_safe() -> None:
    started, cancelled, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    port = _Port(object())

    async def write(_on: bool) -> None:
        return None

    async def stubborn_read() -> bool:
        started.set()
        try:
            await release.wait()
        except asyncio.CancelledError:
            cancelled.set()
            await release.wait()
        return False

    lifecycle = _lifecycle(port, write=write, read=stubborn_read, drain_timeout=0.01)

    def observer(_observation: ProviderPttObservation) -> None:
        return None

    assert lifecycle._capture_managed_tx_port(1, observer)
    pending = asyncio.create_task(
        lifecycle._request_authoritative_ptt_read(
            provider_generation=1, observer=observer
        )
    )
    await started.wait()

    before = time.monotonic()
    await lifecycle._retire_managed_tx_port(1)
    elapsed = time.monotonic() - before

    assert cancelled.is_set()
    assert elapsed < 0.2
    assert not pending.done()
    with pytest.raises(ConnectionError, match="stale"):
        await lifecycle._write_managed_ptt(1, True)

    release.set()
    with pytest.raises(ConnectionError, match="stale"):
        await pending

    started_2, cancelled_2, release_2 = (
        asyncio.Event(),
        asyncio.Event(),
        asyncio.Event(),
    )
    port_2 = _Port(object())

    async def stubborn_read_2() -> bool:
        started_2.set()
        try:
            await release_2.wait()
        except asyncio.CancelledError:
            cancelled_2.set()
            await release_2.wait()
        return True

    lifecycle_2 = _lifecycle(
        port_2, write=write, read=stubborn_read_2, drain_timeout=1.0
    )
    assert lifecycle_2._capture_managed_tx_port(2, observer)
    pending_2 = asyncio.create_task(
        lifecycle_2._request_authoritative_ptt_read(
            provider_generation=2, observer=observer
        )
    )
    await started_2.wait()
    retirement_2 = asyncio.create_task(lifecycle_2._retire_managed_tx_port(2))
    await cancelled_2.wait()
    retirement_2.cancel()
    with pytest.raises(asyncio.CancelledError):
        await retirement_2
    with pytest.raises(ConnectionError, match="stale"):
        await lifecycle_2._write_managed_ptt(2, True)
    release_2.set()
    with pytest.raises(ConnectionError, match="stale"):
        await pending_2


async def test_lifecycle_drives_the_existing_managed_runtime_contract() -> None:
    events: list[str] = []
    keyed = False
    port = _Port(object())

    async def write(on: bool) -> None:
        nonlocal keyed
        events.append(f"write:{on}")
        keyed = on

    async def read() -> bool:
        events.append("read")
        return keyed

    lifecycle = _lifecycle(port, write=write, read=read)
    runtime = ManagedRadioRuntime(
        "test",
        service_factory=managed_tx_effect_service,
        provider_lifecycle=lifecycle,
    )
    await runtime.replace_provider(ready=True)
    await runtime.request_fresh_ptt()

    owner = TxOwner(TxSource.WEBSOCKET, "ws-1")
    keyed_transition = await runtime.request_on(owner)
    assert runtime.tx_snapshot.radio_tx is RadioTx.ON
    assert keyed_transition.snapshot.lease_id is not None
    await runtime.request_off(owner, keyed_transition.snapshot.lease_id)

    assert runtime.tx_snapshot.radio_tx is RadioTx.OFF
    assert events == ["read", "write:True", "read", "write:False", "read"]
    await runtime.shutdown(release_provider=_noop_release)


async def _noop_release() -> None:
    return None
