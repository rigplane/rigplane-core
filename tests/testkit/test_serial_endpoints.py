"""Real TCP and PTY endpoints for ``CivSerialFake`` (MOR-3594).

The endpoints give the in-memory serial fake real byte streams, and these
tests drive them the way an installed app would: raw TCP clients, the
production ``open_serial_port`` open over pyserial's ``socket://`` handler
through an unmodified ``SerialCivLink``, and (macOS/Linux) a real
pseudo-terminal slave opened with pyserial. Vanish/reappear teardown and
the no-leak lifecycle are pinned on the real streams.
"""

from __future__ import annotations

import asyncio
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
    # An IPv6 bind must advertise the bracketed authority, or the URL's
    # colons read as port delimiters and no open can parse it.
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
    # No open_serial_connection seam: the link's default opener runs the
    # production open_serial_port on the socket:// URL (pyserial socket
    # handler) against the endpoint's real TCP listener.
    link = SerialCivLink(device=endpoint.device_url, baudrate=115200)
    try:
        await link.connect()
        assert fake.open_count == 1  # one TCP client, its own fake connection
        await link.send_written(_ident(fake))
        reply = await _link_reply(link)
        assert reply is not None
        assert reply.data == fake.model_id
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

        # While vanished, a new client is accepted then closed at once:
        # the fake refuses its open with ENODEV, so nothing answers.
        gone_reader, gone_writer = await asyncio.open_connection(
            "127.0.0.1", endpoint.port
        )
        assert await asyncio.wait_for(gone_reader.read(), 5.0) == b""
        gone_writer.close()

        fake.usb_reappear(after=0.05)
        await asyncio.sleep(0.4)  # the reappear window plus a wide margin
        reader2, writer2 = await asyncio.open_connection("127.0.0.1", endpoint.port)
        ident = _ident(fake)
        writer2.write(ident)
        await writer2.drain()
        assert (await _read_reply(reader2, ident)).data == fake.model_id
        writer2.close()
    finally:
        await endpoint.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="no os.openpty on Windows")
async def test_pty_identity_and_reappear_through_pyserial() -> None:
    fake = CivSerialFake()
    endpoint = PtyEndpoint(fake)
    await endpoint.start()
    loop = asyncio.get_running_loop()
    port = await loop.run_in_executor(
        None, lambda: serial.Serial(endpoint.device, 115200, timeout=3)
    )
    try:

        def _read() -> bytes:
            return bytes(port.read_until(b"\xfd"))

        ident = _ident(fake)
        await loop.run_in_executor(None, port.write, ident)
        echo = await asyncio.wait_for(loop.run_in_executor(None, _read), 5.0)
        assert echo == ident
        assert (
            parse_civ_frame(
                await asyncio.wait_for(loop.run_in_executor(None, _read), 5.0)
            ).data
            == fake.model_id
        )

        # usb_vanish closes the lifetime connection; after usb_reappear the
        # next bytes open a fresh one and are answered again.
        opens = fake.open_count
        fake.usb_vanish()
        fake.usb_reappear(after=0.05)
        await asyncio.sleep(0.4)  # the window plus a wide margin
        await loop.run_in_executor(None, port.write, ident)
        echo = await asyncio.wait_for(loop.run_in_executor(None, _read), 5.0)
        assert echo == ident
        assert (
            parse_civ_frame(
                await asyncio.wait_for(loop.run_in_executor(None, _read), 5.0)
            ).data
            == fake.model_id
        )
        assert fake.open_count == opens + 1
    finally:
        await loop.run_in_executor(None, port.close)
        await endpoint.stop()
    with pytest.raises(RuntimeError):
        _ = endpoint.device
