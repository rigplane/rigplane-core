"""C24 regression, portable to v3.3.1 and v3.4.0rc1."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from socket import socket
from typing import TypeVar

import aiohttp
import pytest

from rigplane.core.transport import IcomTransport
from rigplane.radio import IcomRadio
from rigplane.testing.timing import VirtualClock
from rigplane.web.server import WebConfig, WebServer
from rigplane_testkit.icom import MockIcomRadio

_T = TypeVar("_T")


async def advance_to(
    clock: VirtualClock, condition: Callable[[], bool], *, budget: float
) -> None:
    """Drive timers in small steps while the loopback peers exchange packets."""
    deadline = clock.now + budget
    while not condition() and clock.now < deadline:
        await clock.elapse(min(0.05, deadline - clock.now))


async def complete_on_clock(
    clock: VirtualClock, operation: Awaitable[_T], *, budget: float
) -> _T:
    task = asyncio.ensure_future(operation)
    try:
        await advance_to(clock, task.done, budget=budget)
        assert task.done(), f"{operation!r} exceeded {budget}s of virtual time"
        return task.result()
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


@contextmanager
def hold_control_close(
    monkeypatch: pytest.MonkeyPatch, sim: MockIcomRadio
) -> Iterator[tuple[asyncio.Event, asyncio.Event]]:
    """Keep the old control socket live while a concurrent repair can run.

    Reopening CI-V under the retired login blocks at discovery. A fresh
    login can always open CI-V. Only the first control close is gated.
    """
    entered = asyncio.Event()
    close = asyncio.Event()
    stale_discovery = asyncio.Event()
    login_count = sim.login_count
    disconnect = IcomTransport.disconnect
    connect = IcomTransport.connect

    async def disconnect_at_boundary(transport: IcomTransport) -> None:
        if type(transport) is IcomTransport and not entered.is_set():
            entered.set()
            await close.wait()
        await disconnect(transport)

    async def connect_at_boundary(
        transport: IcomTransport,
        host: str,
        port: int,
        *,
        local_host: str | None = None,
        local_port: int = 0,
        sock: socket | None = None,
    ) -> None:
        if port == sim.civ_port and sim.login_count == login_count:
            await stale_discovery.wait()
        await connect(
            transport, host, port, local_host=local_host, local_port=local_port, sock=sock
        )

    with monkeypatch.context() as patch:
        patch.setattr(IcomTransport, "disconnect", disconnect_at_boundary)
        patch.setattr(IcomTransport, "connect", connect_at_boundary)
        try:
            yield entered, close
        finally:
            close.set()
            stale_discovery.set()


@asynccontextmanager
async def concurrent_soft_recovery(
    clock: VirtualClock, radio: IcomRadio
) -> AsyncIterator[None]:
    # This is the public entry point the fast CI-V path also calls.
    soft = asyncio.create_task(radio.soft_reconnect())
    try:
        await clock.elapse(0)
        yield
    finally:
        if not soft.done():
            soft.cancel()
        await asyncio.gather(soft, return_exceptions=True)


@pytest.mark.mock_integration
@pytest.mark.timeout(30)
async def test_session_free_before_control_loss_restores_readyz(
    virtual_clock: VirtualClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = virtual_clock
    sim = MockIcomRadio(single_owner=True, enable_audio=True, clock=clock)
    await sim.start()
    radio = IcomRadio(
        "127.0.0.1",
        port=sim.control_port,
        username="testuser",
        password="testpass",
        model="IC-7610",
        timeout=2.0,
        auto_reconnect=True,
    )
    web = WebServer(radio, WebConfig(host="127.0.0.1", port=0, discovery=False))
    try:
        await complete_on_clock(clock, radio.connect(), budget=30.0)
        assert radio.radio_ready
        await complete_on_clock(clock, web.start(), budget=10.0)
        with hold_control_close(monkeypatch, sim) as (notice_seen, close):
            sim.drop_session(at=0, notice=True)
            await advance_to(clock, notice_seen.is_set, budget=1.0)
            assert notice_seen.is_set() and radio.control_connected
            assert not radio.radio_ready
            new_freq = 14_076_000
            sim.set_frequency(new_freq)
            async with concurrent_soft_recovery(clock, radio):
                close.set()
                await advance_to(clock, lambda: radio.radio_ready, budget=4.0)
                async with (
                    aiohttp.ClientSession() as client,
                    client.get(f"http://127.0.0.1:{web.port}/readyz") as response,
                ):
                    assert response.status == 200
                    assert (await response.json())["radioReady"] is True
                assert await complete_on_clock(
                    clock, radio.get_freq(bypass_cache=True), budget=1.0
                ) == new_freq
    finally:
        try:
            await complete_on_clock(clock, web.stop(), budget=10.0)
        finally:
            try:
                await complete_on_clock(clock, radio.disconnect(), budget=10.0)
            finally:
                await sim.stop()
