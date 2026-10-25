"""Deterministic shutdown races at the rigctld transport await boundary."""

import asyncio
from types import SimpleNamespace

import pytest

from rigplane.backends.rigctld_client.radio import (
    RigctldClientObservationPoller,
    RigctldClientRadio,
)


@pytest.mark.parametrize("reconnecting", [False, True])
@pytest.mark.parametrize("connection_error", [False, True])
async def test_stop_when_connection_completes(
    reconnecting, connection_error, monkeypatch
):
    radio = RigctldClientRadio(host="fake.invalid")
    poller = RigctldClientObservationPoller(
        radio, lambda _: None, medium_interval=3600, slow_interval=3600
    )
    loop = asyncio.get_running_loop()
    stopping = loop.create_future()
    writer = SimpleNamespace(is_closing=lambda: False)
    connections = 0

    async def complete_connection():
        nonlocal connections
        connections += 1
        # Schedule stop before wait_for's completion callback. The inner
        # connection task is done when stop cancels the waiting poll task.
        if not stopping.done():
            stopping.set_result(loop.create_task(poller.stop()))
        if connection_error:
            raise OSError("fake connect failed")
        return asyncio.StreamReader(), writer

    def open_connection(*args, **kwargs):
        return loop.create_task(complete_connection())

    async def poll():
        if reconnecting:
            raise OSError("fake dropped connection")
        await radio._transport.connect()

    async def slow():
        await asyncio.Event().wait()

    monkeypatch.setattr(asyncio, "open_connection", open_connection)
    monkeypatch.setattr(poller, "_poll_medium", poll)
    monkeypatch.setattr(poller, "_poll_slow", slow)
    await poller.start()
    tasks = tuple(poller._tasks)
    stop_task = None
    try:
        stop_task = await asyncio.wait_for(stopping, timeout=1)
        done, pending = await asyncio.wait({stop_task}, timeout=1)
        assert stop_task in done, [
            (task.done(), task.cancelling(), task.get_stack()) for task in tasks
        ]
        assert not pending
        await stop_task
        assert poller._tasks == []
        assert all(task.done() for task in tasks)
        assert connections == 1
    finally:
        # A failing baseline must not leave its sleeping poller in the loop.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if stop_task is not None:
            await stop_task


@pytest.mark.parametrize("in_poll", [False, True])
async def test_stop_and_restart(in_poll, monkeypatch):
    radio = RigctldClientRadio(host="fake.invalid")
    poller = RigctldClientObservationPoller(
        radio, lambda _: None, medium_interval=3600, slow_interval=3600
    )
    entered = asyncio.Event()

    async def poll():
        entered.set()
        if in_poll:
            await asyncio.Event().wait()

    monkeypatch.setattr(poller, "_poll_medium", poll)
    monkeypatch.setattr(poller, "_poll_slow", poll)
    await poller.stop()
    for _ in range(2):
        entered.clear()
        await poller.start()
        tasks = tuple(poller._tasks)
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            await asyncio.wait_for(poller.stop(), timeout=1)
            assert all(task.done() for task in tasks)
            assert poller._tasks == []
            await poller.stop()
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
