"""Real TCP and PTY endpoints for ``CivSerialFake`` (MOR-3594).

The tests drive the endpoints the way an installed app would: raw TCP
clients, the production ``open_serial_port`` open over pyserial's
``socket://`` handler via an unmodified ``SerialCivLink``, and on
macOS/Linux a real pseudo-terminal slave, with vanish/reappear teardown
and the no-leak lifecycle pinned on the real streams.
"""

from __future__ import annotations

import asyncio
import errno
import os
import sys
import time

import pytest
import serial

from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.types import CivFrame

from rigplane_testkit.serial import CivSerialFake
from rigplane_testkit.serial.endpoints import PtyEndpoint, TcpEndpoint


async def _read_frame(reader: asyncio.StreamReader, timeout: float = 3.0) -> bytes:
    """One ``FE FE ... FD`` frame off a raw byte stream."""
    return bytes(await asyncio.wait_for(reader.readuntil(b"\xfd"), timeout))


async def _read_reply(
    reader: asyncio.StreamReader, frame: bytes, timeout: float = 3.0
) -> CivFrame:
    """Skip the USB CI-V echo of *frame*; return the parsed reply behind it."""
    assert await _read_frame(reader, timeout) == frame
    return parse_civ_frame(await _read_frame(reader, timeout))


def _ident(fake: CivSerialFake) -> bytes:
    return build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x19, sub=0x00)


async def _link_reply(link: SerialCivLink, timeout: float = 2.0) -> CivFrame | None:
    """Receive until the radio's reply arrives, skipping the USB echo."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = await link.receive(timeout=max(0.01, deadline - time.monotonic()))
        if raw is None:
            return None
        frame = parse_civ_frame(raw)
        if frame.to_addr == CONTROLLER_ADDR:
            return frame
    return None


async def _until(condition, timeout: float = 5.0) -> None:
    async def _poll() -> None:
        while not condition():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), timeout)


async def _pty_ident_reply(fake: CivSerialFake, device: str) -> None:
    port = await asyncio.to_thread(serial.Serial, device, 115200, timeout=3)
    try:
        await asyncio.to_thread(port.write, _ident(fake))
        for _ in range(2):  # the USB CI-V echo, then the radio's reply
            frame = await asyncio.wait_for(
                asyncio.to_thread(port.read_until, b"\xfd"), 5.0
            )
        assert parse_civ_frame(bytes(frame)).data == fake.model_id
    finally:
        await asyncio.to_thread(port.close)


async def test_tcp_echo_identity_and_frequency_over_raw_sockets() -> None:
    fake = CivSerialFake(frequency_hz=14_074_000)
    endpoint = TcpEndpoint(fake)
    await endpoint.start()
    try:
        assert endpoint.device_url == f"socket://127.0.0.1:{endpoint.port}"

        reader, writer = await asyncio.open_connection("127.0.0.1", endpoint.port)
        ident = _ident(fake)
        writer.write(ident)
        await writer.drain()
        reply = await _read_reply(reader, ident)
        assert (reply.command, reply.sub) == (0x19, 0x00)
        assert reply.from_addr == fake.civ_addr
        assert reply.data == fake.model_id

        set_freq = build_civ_frame(
            fake.civ_addr, CONTROLLER_ADDR, 0x05, data=b"\x00\x50\x13\x07\x00"
        )
        writer.write(set_freq)
        await writer.drain()
        assert (await _read_reply(reader, set_freq)).command == 0xFB
        assert fake.frequency_hz == 7_135_000

        get_freq = build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x03)
        writer.write(get_freq)
        await writer.drain()
        freq = await _read_reply(reader, get_freq)
        assert (freq.command, freq.data) == (0x03, b"\x00\x50\x13\x07\x00")
        writer.close()
    finally:
        port = endpoint.port
        await endpoint.stop()

    # The stopped listener accepts nothing, and stop() is idempotent.
    with pytest.raises(OSError):
        await asyncio.open_connection("127.0.0.1", port)
    await endpoint.stop()


async def test_tcp_device_url_brackets_ipv6_literal() -> None:
    # An IPv6 bind must advertise the bracketed authority.
    endpoint = TcpEndpoint(CivSerialFake(), host="::1")
    await endpoint.start()
    try:
        assert endpoint.device_url == f"socket://[::1]:{endpoint.port}"
    finally:
        await endpoint.stop()


async def test_production_open_and_link_identity_through_socket_url() -> None:
    fake = CivSerialFake()
    endpoint = TcpEndpoint(fake)
    await endpoint.start()
    # No open_serial_connection seam: the production open_serial_port runs
    # on the socket:// URL against the endpoint's real TCP listener.
    link = SerialCivLink(device=endpoint.device_url, baudrate=115200)
    try:
        await link.connect()
        await link.send_written(_ident(fake))
        reply = await _link_reply(link)
        assert reply is not None
        assert reply.data == fake.model_id
        # The reply proves the server side opened its fake connection.
        assert fake.open_count == 1  # one TCP client, its own fake connection
    finally:
        await link.disconnect()
        await endpoint.stop()


async def test_tcp_vanish_drops_clients_and_closes_new_opens_until_reappear() -> None:
    fake = CivSerialFake()
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
        await _until(lambda: not fake._vanished)  # the reappear landed
        reader2, writer2 = await asyncio.open_connection("127.0.0.1", endpoint.port)
        ident = _ident(fake)
        writer2.write(ident)
        await writer2.drain()
        assert (await _read_reply(reader2, ident)).data == fake.model_id
        writer2.close()
    finally:
        await endpoint.stop()


async def test_tcp_stop_admits_no_handler_queued_before_shutdown() -> None:
    # No loop yield between connect and stop(): depending on scheduling
    # the accepted handler may or may not have started when stop() runs —
    # both outcomes must end clean (EOF, no registered client, no
    # leftover TcpEndpoint task).
    endpoint = TcpEndpoint(CivSerialFake())
    await endpoint.start()
    reader, _writer = await asyncio.open_connection("127.0.0.1", endpoint.port)
    await asyncio.wait_for(endpoint.stop(), 5.0)
    assert await asyncio.wait_for(reader.read(), 3.0) == b""  # EOF, no hang
    # stop() may return before the accepted handler has run its first
    # step, so it can finish one loop iteration after the EOF; wait for
    # the end conditions instead of asserting them instantly.
    await _until(lambda: endpoint._clients == {})
    await _until(
        lambda: (
            not any(
                "TcpEndpoint" in getattr(t.get_coro(), "__qualname__", "")
                for t in asyncio.all_tasks()
            )
        )
    )


@pytest.mark.skipif(sys.platform == "win32", reason="no os.openpty on Windows")
async def test_pty_start_is_transactional_while_fake_vanished() -> None:
    fake = CivSerialFake()
    endpoint = PtyEndpoint(fake)
    fake.usb_vanish()
    await _until(lambda: fake._vanished)  # the vanish must land before start()
    with pytest.raises(OSError):
        await endpoint.start()
    with pytest.raises(RuntimeError):
        _ = endpoint.device  # nothing published: no path, fd or reader
    fake.usb_reappear()
    await _until(lambda: not fake._vanished)
    await endpoint.start()
    await _pty_ident_reply(fake, endpoint.device)
    await endpoint.stop()
    assert fake.open_count == 1  # the failed start never opened the fake


@pytest.mark.skipif(sys.platform == "win32", reason="no os.openpty on Windows")
async def test_pty_openpty_failure_never_masks_or_leaks(monkeypatch) -> None:
    fake = CivSerialFake()
    endpoint = PtyEndpoint(fake)
    opened: list = []
    real_open = fake.open_serial_connection

    async def _record():
        pair = await real_open()
        opened.append(pair[1])
        return pair

    def _emfile():
        raise OSError(errno.EMFILE, "descriptor table full")

    monkeypatch.setattr(fake, "open_serial_connection", _record)
    monkeypatch.setattr(os, "openpty", _emfile)
    with pytest.raises(OSError) as exc:
        await endpoint.start()
    assert exc.value.errno == errno.EMFILE
    assert opened[0].closed  # the connection start() opened was closed
    with pytest.raises(RuntimeError):
        _ = endpoint.device  # nothing was published
    monkeypatch.undo()
    await endpoint.start()
    await _pty_ident_reply(fake, endpoint.device)
    await endpoint.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="no os.openpty on Windows")
@pytest.mark.parametrize("platform_name", ["linux", "darwin", "win32", "freebsd14"])
async def test_pty_guard_platform_matrix(monkeypatch, platform_name: str) -> None:
    monkeypatch.setattr(sys, "platform", platform_name)
    endpoint = PtyEndpoint(CivSerialFake())
    if platform_name in ("darwin", "linux"):
        await endpoint.start()  # the only two platforms that may start
        await endpoint.stop()
        return
    with pytest.raises(NotImplementedError):
        await endpoint.start()


@pytest.mark.skipif(sys.platform == "win32", reason="no os.openpty on Windows")
async def test_pty_identity_and_reappear_through_pyserial() -> None:
    fake = CivSerialFake()
    endpoint = PtyEndpoint(fake)
    await endpoint.start()
    try:
        await _pty_ident_reply(fake, endpoint.device)
        opens = fake.open_count
        fake.usb_vanish()
        await _until(lambda: fake._vanished)
        fake.usb_reappear(after=0.05)
        await _until(lambda: not fake._vanished)
        await _pty_ident_reply(fake, endpoint.device)
        assert fake.open_count == opens + 1
    finally:
        await endpoint.stop()
    with pytest.raises(RuntimeError):
        _ = endpoint.device
