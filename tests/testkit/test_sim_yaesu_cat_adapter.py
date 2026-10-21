"""Tests for the Yaesu CAT serial adapter (MOR-3602).

The wire tests drive a real ``TcpEndpoint`` through pyserial's
``socket://`` URL — the same open the production link performs — or raw
TCP clients; the PTY test opens the slave path with ``os.open`` and
termios raw. Nothing here touches a real serial device.
"""

from __future__ import annotations

import ast
import asyncio
import os
import select
import time
from pathlib import Path

import pytest
import serial

from rigplane_testkit.serial.endpoints import PtyEndpoint, TcpEndpoint, pty_supported
from rigplane_testkit.sim.adapters import yaesu_cat as adapter
from rigplane_testkit.sim.adapters.yaesu_cat import (
    YaesuCatSerialFake,
    fidelity_for_banner,
)

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "tests" / "fixtures" / "ftx1" / "model_conformance_ledger.json"
ADAPTER_PATH = ROOT / "rigplane_testkit" / "sim" / "adapters" / "yaesu_cat.py"


def _fake(seed: int = 0) -> YaesuCatSerialFake:
    return YaesuCatSerialFake.from_profile(ROOT, seed=seed)


def _connect(url: str) -> serial.SerialBase:
    """The production open: pyserial's ``socket://`` URL, 3 s timeout."""
    return serial.serial_for_url(url, baudrate=38400, timeout=3)


async def _write(port, data: bytes) -> None:
    await asyncio.to_thread(port.write, data)


async def _read_line(port, timeout: float = 3.0) -> bytes:
    return bytes(
        await asyncio.wait_for(asyncio.to_thread(port.read_until, b";"), timeout)
    )


async def _until(condition, timeout: float = 5.0) -> None:
    async def _poll() -> None:
        while not condition():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), timeout)


async def test_tcp_wire_identity_chunking_set_and_refusal() -> None:
    fake = _fake()
    endpoint = TcpEndpoint(fake)
    await endpoint.start()
    try:
        port = await asyncio.to_thread(_connect, endpoint.device_url)
        try:
            await _write(port, b"ID;")
            assert await _read_line(port) == b"ID0840;"

            # Two lines in one chunk.
            await _write(port, b"FA;MD0;")
            assert await _read_line(port) == b"FA147225000;"
            assert await _read_line(port) == b"MD04;"  # FM

            # A line split over chunks.
            for chunk in (b"I", b"D", b";"):
                await _write(port, chunk)
            assert await _read_line(port) == b"ID0840;"

            # A served SET gets no reply but changes state: the one reply
            # behind it is the read's, with the new value. (FB: the seeded
            # MEMORY mode refuses FA SETs, as recorded on the MAIN side.)
            await _write(port, b"FB007074000;FB;")
            assert await _read_line(port) == b"FB007074000;"
            assert fake.model.engine.state.receivers[1].frequency_hz == 7_074_000

            # An unserved SET answers '?;'.
            await _write(port, b"ZZ1;")
            assert await _read_line(port) == b"?;"
        finally:
            await asyncio.to_thread(port.close)
    finally:
        await endpoint.stop()


async def test_tcp_oversize_run_answers_refusal_once_and_recovers() -> None:
    fake = _fake()
    endpoint = TcpEndpoint(fake)
    await endpoint.start()
    try:
        port = await asyncio.to_thread(_connect, endpoint.device_url)
        try:
            # 65 bytes without a ';' cross the 64-byte run limit: one '?;'.
            await _write(port, b"X" * 65)
            assert await _read_line(port) == b"?;"
            # The tail up to the next ';' is discarded silently; the line
            # after it is served again.
            await _write(port, b"YYY;ID;")
            assert await _read_line(port) == b"ID0840;"
        finally:
            await asyncio.to_thread(port.close)
    finally:
        await endpoint.stop()


async def test_silence_consumes_lines_without_answering_or_applying() -> None:
    fake = _fake()
    endpoint = TcpEndpoint(fake)
    await endpoint.start()
    try:
        port = await asyncio.to_thread(_connect, endpoint.device_url)
        try:
            fake.silence(1.0)
            await _write(port, b"FB007074000;ID;")
            # Nothing answers inside the window (bounded negative read).
            port.timeout = 0.3
            assert await _read_line(port) == b""
            await _until(lambda: time.monotonic() >= fake._silent_until)
            # The silenced SET never applied (FB stays as seeded); reads
            # answer again.
            port.timeout = 3
            await _write(port, b"FB;ID;")
            assert await _read_line(port) == b"FB052900000;"
            assert await _read_line(port) == b"ID0840;"
        finally:
            await asyncio.to_thread(port.close)
    finally:
        await endpoint.stop()


async def test_tcp_vanish_drops_clients_and_closes_new_opens_until_reappear() -> None:
    fake = _fake()
    endpoint = TcpEndpoint(fake)
    await endpoint.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", endpoint.port)
        fake.usb_vanish()  # at=0: fires on the next loop tick
        # The live connection drops: EOF, never data, never a hang.
        assert await asyncio.wait_for(reader.read(), 5.0) == b""
        writer.close()

        # While vanished, a new client is accepted then closed at once.
        gone_reader, gone_writer = await asyncio.open_connection(
            "127.0.0.1", endpoint.port
        )
        assert await asyncio.wait_for(gone_reader.read(), 5.0) == b""
        gone_writer.close()

        fake.usb_reappear(after=0.05)
        await _until(lambda: not fake._vanished)
        reader2, writer2 = await asyncio.open_connection("127.0.0.1", endpoint.port)
        writer2.write(b"ID;")
        await writer2.drain()
        assert await asyncio.wait_for(reader2.readuntil(b";"), 5.0) == b"ID0840;"
        writer2.close()
    finally:
        await endpoint.stop()


def _read_fd_until(fd: int, marker: bytes, timeout: float) -> bytes:
    deadline = time.monotonic() + timeout
    buf = bytearray()
    while marker not in buf:
        if time.monotonic() > deadline:
            raise TimeoutError(f"no {marker!r} within {timeout}s: {bytes(buf)!r}")
        ready, _, _ = select.select([fd], [], [], 0.05)
        if ready:
            chunk = os.read(fd, 256)
            if not chunk:
                raise OSError(f"pty returned EOF: {bytes(buf)!r}")
            buf += chunk
    return bytes(buf)


@pytest.mark.skipif(not pty_supported(), reason="PTY endpoints need macOS/Linux")
async def test_pty_round_trip_fa_through_slave_path() -> None:
    import tty

    fake = _fake()
    endpoint = PtyEndpoint(fake)
    await endpoint.start()
    try:
        fd = os.open(endpoint.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            tty.setraw(fd)
            os.write(fd, b"FA;")
            data = await asyncio.to_thread(_read_fd_until, fd, b";", 5.0)
            assert data == b"FA147225000;"
        finally:
            os.close(fd)
    finally:
        await endpoint.stop()


async def test_frequency_setter_bypasses_the_memory_mode_wire_refusal() -> None:
    fake = _fake()
    fake.model.engine.state.extra["main_memory_mode"] = True
    reader, writer = await fake.open_serial_connection()
    # The wire FA SET is refused in MEMORY mode (recorded MAIN behaviour)...
    writer.write(b"FA014074000;")
    assert await asyncio.wait_for(reader.readuntil(b";"), 3.0) == b"?;"
    assert fake.frequency_hz == 147_225_000  # unchanged
    # ...while the control-event setter applies straight on the engine.
    fake.frequency_hz = 7_074_000
    writer.write(b"FA;")
    assert await asyncio.wait_for(reader.readuntil(b";"), 3.0) == b"FA007074000;"
    # An engine refusal (here: above the FTX-1's 470 MHz top) raises.
    with pytest.raises(ValueError):
        fake.frequency_hz = 999_999_999
    assert fake.frequency_hz == 7_074_000
    writer.close()


async def test_unserved_unrecorded_request_is_counted_and_observed() -> None:
    seen: list[str] = []
    fake = YaesuCatSerialFake.from_profile(ROOT, on_not_modelled=seen.append)
    reader, writer = await fake.open_serial_connection()
    try:
        # QQ; is neither a native handler verb nor in the recording: the
        # not-modelled coverage gap answers '?;' and notifies the observer.
        writer.write(b"QQ;")
        assert await asyncio.wait_for(reader.readuntil(b";"), 3.0) == b"?;"
        # A malformed run is refused too, but is not a coverage gap.
        writer.write(b"1;")
        assert await asyncio.wait_for(reader.readuntil(b";"), 3.0) == b"?;"
    finally:
        writer.close()
    assert fake.model.not_modelled == {"QQ;": 1}
    assert seen == ["QQ;"]


def test_banner_fidelity_is_A_for_the_committed_ledger() -> None:
    assert fidelity_for_banner(_fake(), LEDGER) == "A"


def test_banner_fidelity_drops_to_B_on_an_unledgered_divergence(monkeypatch) -> None:
    fake = _fake()
    real_fresh = adapter._fresh_seeded_model

    def _mutating(fake: YaesuCatSerialFake, table) -> object:
        model = real_fresh(fake, table)
        original = model.handle

        def handle(line: str):
            if line == "ID;":
                return "ID9999;"
            return original(line)

        model.handle = handle
        return model

    monkeypatch.setattr(adapter, "_fresh_seeded_model", _mutating)
    assert fidelity_for_banner(fake, LEDGER) == "B"


def test_adapter_imports_only_the_allowed_modules() -> None:
    tree = ast.parse(ADAPTER_PATH.read_text())
    modules: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            names.update(alias.name for alias in node.names)
    # The production parser/formatter must not generate the replies.
    assert "CatCommandParser" not in names
    assert "format_command" not in names
    for module in modules:
        assert not module.startswith("rigplane.backends"), module
        assert not module.startswith("rigplane.commands"), module
        assert module.split(".")[0] != "tests", module
        if module == "rigplane" or module.startswith("rigplane."):
            assert module.startswith("rigplane.profiles"), module
