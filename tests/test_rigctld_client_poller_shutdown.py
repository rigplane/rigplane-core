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


async def test_web_shutdown_during_real_tcp_reconnect(monkeypatch, caplog):
    from aiohttp import ClientSession
    from fake_rigctld import FakeRigctldServer

    from rigplane.web.server import WebConfig, WebServer

    async with FakeRigctldServer() as fake:
        radio = RigctldClientRadio(host=fake.host, port=fake.port)
        await radio.connect()
        server = WebServer(radio, WebConfig(host="127.0.0.1", port=0, discovery=False))
        loop = asyncio.get_running_loop()
        stopping = loop.create_future()
        stop_entered = asyncio.Event()
        connection_complete = asyncio.Event()
        real_open = asyncio.open_connection
        poll_tasks = ()
        connection_tasks = []
        stop_task = None
        armed = False
        try:
            await asyncio.wait_for(server.start(), timeout=10)
            listener = server._server
            poller = server._state_poller
            assert isinstance(poller, RigctldClientObservationPoller)
            poll_tasks = tuple(poller._tasks)
            real_stop = poller.stop
            async with ClientSession() as client:
                async with client.get(
                    f"http://127.0.0.1:{server.port}/api/v1/info"
                ) as response:
                    assert response.status == 200
                    assert (await response.json())["model"] == "External rigctld"
            assert fake.commands_seen

            async def stop_at_completion():
                stop_entered.set()
                await connection_complete.wait()
                await real_stop()

            async def connect_at_shutdown(*args, **kwargs):
                reader, writer = await real_open(*args, **kwargs)
                await stop_entered.wait()
                connection_complete.set()
                return reader, writer

            def open_connection(*args, **kwargs):
                nonlocal armed
                if armed and args[:2] == (fake.host, fake.port):
                    armed = False
                    stopping.set_result(loop.create_task(server.stop()))
                    task = loop.create_task(connect_at_shutdown(*args, **kwargs))
                    connection_tasks.append(task)
                    return task
                return real_open(*args, **kwargs)

            monkeypatch.setattr(poller, "stop", stop_at_completion)
            monkeypatch.setattr(asyncio, "open_connection", open_connection)
            armed = True
            # Retire the real transport without the operator-disconnect flag:
            # the production poll loop must reopen it after its next read fails.
            await radio._transport.close()
            stop_task = await asyncio.wait_for(stopping, timeout=5)
            done, _ = await asyncio.wait({stop_task}, timeout=1)
            assert stop_task in done
            await stop_task
            assert connection_complete.is_set()
            assert len(connection_tasks) == 1
            assert all(task.done() for task in connection_tasks + list(poll_tasks))
            assert poller._tasks == []
            assert server._state_poller is None
            assert listener is not None and not listener.is_serving()
            assert not any(
                "state poller stop timed out" in r.message for r in caplog.records
            )
            assert "T 1" not in fake.commands_seen
            with pytest.raises(RuntimeError, match="not bound"):
                server.command_queue.capture_connection_generation()
            await radio.disconnect()
            assert not radio.connected
        finally:
            armed = False
            stop_entered.set()
            connection_complete.set()
            for task in poll_tasks:
                task.cancel()
            await asyncio.gather(*poll_tasks, return_exceptions=True)
            for task in connection_tasks:
                task.cancel()
            await asyncio.gather(*connection_tasks, return_exceptions=True)
            if stop_task is None and stopping.done() and not stopping.cancelled():
                stop_task = stopping.result()
            if stop_task is not None:
                await stop_task
            await server.stop()
            await radio.disconnect()
            for task in connection_tasks:
                if not task.cancelled() and task.exception() is None:
                    _, writer = task.result()
                    writer.close()
                    await asyncio.gather(writer.wait_closed(), return_exceptions=True)
