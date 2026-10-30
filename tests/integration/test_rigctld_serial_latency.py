"""Two polling rigctld clients on an IC-7300 PTY, with explicit F/f verification."""

from __future__ import annotations

import asyncio
import json
import math
import time

import pytest

from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.commands.commander import Priority
from rigplane.core.acquisition_scheduler import (
    AcquisitionPriority,
    AcquisitionQuery,
)
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.server import RigctldServer
from rigplane_testkit.serial.endpoints import PtyEndpoint, pty_supported
from testkit.ic7300_fakes import ic7300_profile_fake

pytestmark = pytest.mark.mock_integration


async def _finish_poll_tasks(tasks, cleanups):
    """Cancel polling tasks, run every cleanup, then re-raise a poll failure.

    A polling client that died during measurement must fail the test, but
    only after each cleanup has run so no server, radio, or PTY resource
    is left active after the error.
    """
    for task in tasks:
        task.cancel()
    results = await asyncio.gather(*tasks, return_exceptions=True)
    failure = None
    for result in results:
        # Suppress only the cancellation requested above.
        if isinstance(result, asyncio.CancelledError):
            continue
        if isinstance(result, BaseException) and failure is None:
            failure = result
    for cleanup in cleanups:
        await cleanup()
    if failure is not None:
        raise failure


@pytest.mark.skipif(not pty_supported(), reason="requires a PTY")
async def test_acquisition_polls_yield_to_writes(monkeypatch) -> None:
    """MOR-3888: rigctld-seat background polls share no lane with writes.

    The seat's acquisition sender used to drop the scheduler priority, so
    background cadence polls went out at NORMAL — queuing ahead of
    interactive writes — and bypassed the commander's drop-newest cap for
    BACKGROUND fire-and-forget sends.
    """
    fake = ic7300_profile_fake()
    endpoint = PtyEndpoint(fake)
    await endpoint.start()
    radio = Ic7300SerialRadio(
        device=endpoint.device, _enumerate_serial_ports_fn=lambda: []
    )
    server = RigctldServer(radio, RigctldConfig(host="127.0.0.1", port=0))
    try:
        await asyncio.wait_for(radio.connect(), 30)
        seen: list[object] = []
        real_send_civ = radio.send_civ

        async def spy(command, sub=None, data=None, **kwargs):
            seen.append(kwargs.get("priority"))
            return await real_send_civ(command, sub=sub, data=data, **kwargs)

        monkeypatch.setattr(radio, "send_civ", spy)
        query = AcquisitionQuery(command=0x03)
        await server._send_one_state_query(query, AcquisitionPriority.BACKGROUND)
        await server._send_one_state_query(query, AcquisitionPriority.USER)
        assert seen == [Priority.BACKGROUND, Priority.NORMAL]
    finally:
        await radio.disconnect()
        await endpoint.stop()


@pytest.mark.skipif(not pty_supported(), reason="requires a PTY")
@pytest.mark.parametrize("silent", [False, True])
async def test_two_clients_frequency_readback_latency(silent: bool) -> None:
    fake = ic7300_profile_fake()
    endpoint = PtyEndpoint(fake)
    await endpoint.start()
    radio = Ic7300SerialRadio(
        device=endpoint.device, _enumerate_serial_ports_fn=lambda: []
    )
    server = RigctldServer(radio, RigctldConfig(host="127.0.0.1", port=0))
    writers = []
    tasks = []
    samples = []
    transcript = []
    stop = asyncio.Event()
    poll_cycles = 0

    async def exchange(client, reader, writer, command):
        start = time.monotonic()
        wall = time.time()
        writer.write((command + "\n").encode())
        await writer.drain()
        first = await asyncio.wait_for(reader.readline(), 5)
        assert first, "rigctld closed the client"
        lines = [first.decode().strip()]
        if command in ("m", "s") and not first.startswith(b"RPRT"):
            lines.append(
                (await asyncio.wait_for(reader.readline(), 5)).decode().strip()
            )
        transcript.append(
            dict(
                wall=wall,
                client=client,
                command=command,
                reply=lines,
                duration=time.monotonic() - start,
            )
        )
        return lines

    async def poll(reader, writer):
        nonlocal poll_cycles
        while not stop.is_set():
            started = time.monotonic()
            for cmd in ("f", "m", "t", "s", "\\get_powerstat"):
                await exchange(1, reader, writer, cmd)
            poll_cycles += 1
            try:
                await asyncio.wait_for(
                    stop.wait(), max(0.001, 1 - (time.monotonic() - started))
                )
            except TimeoutError:
                pass

    def _raise_if_poll_client_lost():
        for task in tasks:
            if task.done():
                # An unexpected polling-client failure (timeout, disconnect)
                # must fail the measurement, not be discarded by cleanup.
                task.result()

    try:
        await asyncio.wait_for(radio.connect(), 30)
        await server.start()
        port = server._server.sockets[0].getsockname()[1]
        reader1, writer1 = await asyncio.open_connection("127.0.0.1", port)
        reader2, writer2 = await asyncio.open_connection("127.0.0.1", port)
        writers.extend((writer1, writer2))
        tasks.append(asyncio.create_task(poll(reader1, writer1)))
        for index in range(20):
            _raise_if_poll_client_lost()
            await exchange(2, reader2, writer2, "f")
            await exchange(2, reader2, writer2, "m")
            if silent and index % 5 == 0:
                fake.silence(0.12)
                await asyncio.sleep(0.15)
            frequency = 14_074_000 + index * 100
            start = time.monotonic()
            reply = await exchange(2, reader2, writer2, f"F {frequency}")
            write_duration = time.monotonic() - start
            assert reply == ["RPRT 0"], reply
            while time.monotonic() - start < 15:
                observed = await exchange(2, reader2, writer2, "f")
                if observed == [str(frequency)]:
                    break
                await asyncio.sleep(0.05)
            duration = time.monotonic() - start
            samples.append(dict(write=write_duration, readback=duration))
            assert observed == [str(frequency)], observed
            assert fake.frequency_hz == frequency
            await asyncio.sleep(max(0, 1.25 - duration))
        assert not fake.ptt
        _raise_if_poll_client_lost()
        assert poll_cycles > 0, "client 1 produced no polling samples"
        values = sorted(sample["readback"] for sample in samples)
        assert values[math.ceil(0.95 * len(values)) - 1] <= 2
        assert max(values) <= 5
    finally:
        print(
            "LATENCY",
            json.dumps(dict(silent=silent, samples=samples, transcript=transcript)),
        )
        stop.set()

        async def _close_writers():
            for writer in writers:
                writer.close()
                await writer.wait_closed()

        await _finish_poll_tasks(
            tasks,
            [_close_writers, server.stop, radio.disconnect, endpoint.stop],
        )


async def test_failing_poll_still_runs_cleanup() -> None:
    """MOR-3888 round 2: a polling failure must fail and still clean up."""

    async def _boom():
        raise RuntimeError("poll boom")

    task = asyncio.create_task(_boom())
    await asyncio.sleep(0)  # let the poll fail before cleanup cancels it
    assert task.done()
    calls: list[str] = []

    async def _cleaner(name: str):
        calls.append(name)

    with pytest.raises(RuntimeError, match="poll boom"):
        await _finish_poll_tasks(
            [task],
            [
                lambda: _cleaner("writers"),
                lambda: _cleaner("server"),
                lambda: _cleaner("radio"),
                lambda: _cleaner("endpoint"),
            ],
        )
    assert calls == ["writers", "server", "radio", "endpoint"]
