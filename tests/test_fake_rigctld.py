"""Tests for the reusable external ``rigctld`` simulator (MOR-4075).

Clock-dependent waits run on the shared virtual clock
(``virtual_clock`` fixture); every socket byte is a real event awaited with
:func:`await_until`, never with a wall-clock ``asyncio.sleep`` or a raised
``asyncio`` timeout.
"""

from __future__ import annotations

import asyncio

import pytest

from fake_rigctld import FakeRigctldBehavior, FakeRigctldServer
from rigplane.testing.timing import VirtualClock, await_until


async def _connect(
    server: FakeRigctldServer,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(*server.address)


async def _close(writer: asyncio.StreamWriter) -> None:
    writer.close()
    await writer.wait_closed()


async def _send_line(
    writer: asyncio.StreamWriter,
    command: str,
) -> None:
    writer.write(f"{command}\n".encode("ascii"))
    await writer.drain()


async def _read_line(reader: asyncio.StreamReader) -> str:
    """Wait for one reply line as a real socket event."""
    read = asyncio.ensure_future(reader.readline())
    try:
        await await_until(
            lambda: read.done(),
            timeout=10.0,
            description="the rigctld reply line",
        )
    except BaseException:
        if not read.done():
            read.cancel()
        raise
    line = read.result()
    assert line != b""
    return line.decode("ascii").rstrip("\n")


async def _read_lines(
    reader: asyncio.StreamReader,
    count: int,
) -> list[str]:
    lines: list[str] = []
    for _ in range(count):
        lines.append(await _read_line(reader))
    return lines


async def _read_eof(reader: asyncio.StreamReader, description: str) -> bytes:
    """Wait for the real EOF bytes as a real socket event."""
    read = asyncio.ensure_future(reader.read(4096))
    try:
        await await_until(
            lambda: read.done(),
            timeout=10.0,
            description=description,
        )
    except BaseException:
        if not read.done():
            read.cancel()
        raise
    return read.result()


@pytest.mark.timeout(30)
async def test_rigctld_owned_frequency_mode_ptt_and_vfo_happy_paths(
    virtual_clock: VirtualClock,
) -> None:
    async with FakeRigctldServer() as server:
        reader, writer = await _connect(server)
        try:
            await _send_line(writer, "f")
            assert await _read_lines(reader, 1) == ["14074000"]

            await _send_line(writer, "F 7050000")
            assert await _read_lines(reader, 1) == ["RPRT 0"]
            await _send_line(writer, "f")
            assert await _read_lines(reader, 1) == ["7050000"]

            await _send_line(writer, "m")
            assert await _read_lines(reader, 2) == ["USB", "2400"]
            await _send_line(writer, "M LSB 1800")
            assert await _read_lines(reader, 1) == ["RPRT 0"]
            await _send_line(writer, "m")
            assert await _read_lines(reader, 2) == ["LSB", "1800"]

            await _send_line(writer, "t")
            assert await _read_lines(reader, 1) == ["0"]
            await _send_line(writer, "T 1")
            assert await _read_lines(reader, 1) == ["RPRT 0"]
            await _send_line(writer, "t")
            assert await _read_lines(reader, 1) == ["1"]

            await _send_line(writer, "v")
            assert await _read_lines(reader, 1) == ["VFOA"]
            await _send_line(writer, "V VFOB")
            assert await _read_lines(reader, 1) == ["RPRT 0"]
            await _send_line(writer, "v")
            assert await _read_lines(reader, 1) == ["VFOB"]
        finally:
            await _close(writer)

    assert server.commands_seen == [
        "f",
        "F 7050000",
        "f",
        "m",
        "M LSB 1800",
        "m",
        "t",
        "T 1",
        "t",
        "v",
        "V VFOB",
        "v",
    ]


@pytest.mark.timeout(30)
async def test_rigctld_owned_unsupported_command_returns_enimpl(
    virtual_clock: VirtualClock,
) -> None:
    async with FakeRigctldServer() as server:
        reader, writer = await _connect(server)
        try:
            await _send_line(writer, r"\dump_state")
            assert await _read_lines(reader, 1) == ["RPRT -4"]
        finally:
            await _close(writer)


@pytest.mark.timeout(30)
async def test_rigctld_owned_disconnect_closes_socket_without_response(
    virtual_clock: VirtualClock,
) -> None:
    behavior = FakeRigctldBehavior(disconnect_commands={"f"})

    async with FakeRigctldServer(behavior=behavior) as server:
        reader, writer = await _connect(server)
        try:
            await _send_line(writer, "f")
            assert await _read_eof(reader, "the disconnect EOF") == b""
        finally:
            await _close(writer)


@pytest.mark.timeout(30)
async def test_rigctld_owned_malformed_response_can_be_scripted(
    virtual_clock: VirtualClock,
) -> None:
    behavior = FakeRigctldBehavior(malformed_responses={"f": b"not-a-frequency\n"})

    async with FakeRigctldServer(behavior=behavior) as server:
        reader, writer = await _connect(server)
        try:
            await _send_line(writer, "f")
            assert await _read_lines(reader, 1) == ["not-a-frequency"]
        finally:
            await _close(writer)


@pytest.mark.timeout(30)
async def test_rigctld_owned_delayed_response_can_be_scripted(
    virtual_clock: VirtualClock,
) -> None:
    behavior = FakeRigctldBehavior(command_delays={"f": 0.02})

    async with FakeRigctldServer(behavior=behavior) as server:
        reader, writer = await _connect(server)
        try:
            await _send_line(writer, "f")
            # Drain only queues the bytes: wait until the server has really
            # received 'f' (its handler then sits on the scripted delay),
            # because elapse() settles ready callbacks, not bytes still in
            # the kernel. Only then move the clock past the virtual timer
            # and wait for the real socket bytes it releases.
            await await_until(
                lambda: "f" in server.commands_seen,
                timeout=10.0,
                description="the server receiving 'f'",
            )
            await virtual_clock.elapse(0.1)
            assert await _read_lines(reader, 1) == ["14074000"]
        finally:
            await _close(writer)


@pytest.mark.timeout(30)
async def test_rigctld_owned_stop_cancels_active_delayed_response(
    virtual_clock: VirtualClock,
) -> None:
    behavior = FakeRigctldBehavior(command_delays={"f": 10.0})
    server = FakeRigctldServer(behavior=behavior)
    await server.start()
    reader, writer = await _connect(server)
    try:
        await _send_line(writer, "f")
        await await_until(
            lambda: "f" in server.commands_seen,
            timeout=10.0,
            description="the server receiving 'f'",
        )

        # Stop must finish without waiting out the 10 s scripted delay:
        # drive it as a task under a coordinated virtual deadline (same
        # 0.5 s bound the old wall-clock version enforced) and fail if it
        # is still pending once the clock passes the bound. Each elapse
        # step also settles the loop, so the real socket teardown inside
        # stop() is delivered while virtual time moves.
        stop_task = asyncio.ensure_future(server.stop())
        deadline = virtual_clock() + 0.5
        while not stop_task.done() and virtual_clock() < deadline:
            await virtual_clock.elapse(0.05)
        assert stop_task.done(), "stop() did not finish within 0.5 s of virtual time"
        stop_task.result()
        assert server._tasks == set()
        assert server._writers == set()
        assert await _read_eof(reader, "the EOF after stop") == b""
    finally:
        await server.stop()
        if not writer.is_closing():
            await _close(writer)


async def test_rigctld_owned_busy_port_rejects_second_simulator() -> None:
    async with FakeRigctldServer() as first:
        second = FakeRigctldServer(port=first.port)
        with pytest.raises(OSError):
            await second.start()
        await second.stop()
