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
        while not stop.is_set():
            started = time.monotonic()
            for cmd in ("f", "m", "t", "s", "\\get_powerstat"):
                await exchange(1, reader, writer, cmd)
            try:
                await asyncio.wait_for(
                    stop.wait(), max(0.001, 1 - (time.monotonic() - started))
                )
            except TimeoutError:
                pass

    try:
        await asyncio.wait_for(radio.connect(), 30)
        await server.start()
        port = server._server.sockets[0].getsockname()[1]
        reader1, writer1 = await asyncio.open_connection("127.0.0.1", port)
        reader2, writer2 = await asyncio.open_connection("127.0.0.1", port)
        writers.extend((writer1, writer2))
        tasks.append(asyncio.create_task(poll(reader1, writer1)))
        for index in range(20):
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
        values = sorted(sample["readback"] for sample in samples)
        assert values[math.ceil(0.95 * len(values)) - 1] <= 2
        assert max(values) <= 5
    finally:
        print(
            "LATENCY",
            json.dumps(dict(silent=silent, samples=samples, transcript=transcript)),
        )
        stop.set()
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        for writer in writers:
            writer.close()
            await writer.wait_closed()
        await server.stop()
        await radio.disconnect()
        await endpoint.stop()
