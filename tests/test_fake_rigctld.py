"""Tests for the reusable fake external ``rigctld`` simulator."""

from __future__ import annotations

import asyncio

import pytest

from fake_rigctld import FakeRigctldBehavior, FakeRigctldServer


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


async def _read_lines(
    reader: asyncio.StreamReader,
    count: int,
) -> list[str]:
    lines: list[str] = []
    for _ in range(count):
        line = await asyncio.wait_for(reader.readline(), timeout=1.0)
        assert line != b""
        lines.append(line.decode("ascii").rstrip("\n"))
    return lines


async def test_rigctld_owned_frequency_mode_ptt_and_vfo_happy_paths() -> None:
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


async def test_rigctld_owned_unsupported_command_returns_enimpl() -> None:
    async with FakeRigctldServer() as server:
        reader, writer = await _connect(server)
        try:
            await _send_line(writer, r"\dump_state")
            assert await _read_lines(reader, 1) == ["RPRT -4"]
        finally:
            await _close(writer)


async def test_rigctld_owned_disconnect_closes_socket_without_response() -> None:
    behavior = FakeRigctldBehavior(disconnect_commands={"f"})

    async with FakeRigctldServer(behavior=behavior) as server:
        reader, writer = await _connect(server)
        try:
            await _send_line(writer, "f")
            data = await asyncio.wait_for(reader.read(4096), timeout=1.0)
            assert data == b""
        finally:
            await _close(writer)


async def test_rigctld_owned_malformed_response_can_be_scripted() -> None:
    behavior = FakeRigctldBehavior(malformed_responses={"f": b"not-a-frequency\n"})

    async with FakeRigctldServer(behavior=behavior) as server:
        reader, writer = await _connect(server)
        try:
            await _send_line(writer, "f")
            assert await _read_lines(reader, 1) == ["not-a-frequency"]
        finally:
            await _close(writer)


async def test_rigctld_owned_delayed_response_can_be_scripted() -> None:
    behavior = FakeRigctldBehavior(command_delays={"f": 0.02})

    async with FakeRigctldServer(behavior=behavior) as server:
        reader, writer = await _connect(server)
        try:
            await _send_line(writer, "f")
            assert await _read_lines(reader, 1) == ["14074000"]
        finally:
            await _close(writer)


async def test_rigctld_owned_stop_cancels_active_delayed_response() -> None:
    behavior = FakeRigctldBehavior(command_delays={"f": 10.0})
    server = FakeRigctldServer(behavior=behavior)
    await server.start()
    reader, writer = await _connect(server)
    try:
        await _send_line(writer, "f")
        await asyncio.wait_for(_wait_for_command(server, "f"), timeout=1.0)

        loop = asyncio.get_running_loop()
        started_at = loop.time()
        await asyncio.wait_for(server.stop(), timeout=0.5)

        assert loop.time() - started_at < 0.5
        assert server._tasks == set()
        assert server._writers == set()
        assert await asyncio.wait_for(reader.read(4096), timeout=0.5) == b""
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


async def _wait_for_command(server: FakeRigctldServer, command: str) -> None:
    while command not in server.commands_seen:
        await asyncio.sleep(0)
