"""The production ``SerialCivLink`` against ``CivSerialFake`` (MOR-3218).

These tests drive the unmodified link through its ``open_serial_connection``
seam: the fake owns the far end of the wire, the link owns framing, writer
serialization, health flags and reconnect-open error propagation. Identity
gate characterization on ``CoreRadio``/``IcomRadio`` is MOR-3211, not here.
"""

from __future__ import annotations

import asyncio
import errno
import time

import pytest

from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame, parse_civ_frame
from rigplane.core.serial_open import serial_open_diagnostic
from rigplane.types import CivFrame

from rigplane_testkit.serial import CivSerialFake

_DEVICE = "civ-serial-fake"  # synthetic node name; no real path is opened


def _link_for(fake: CivSerialFake) -> SerialCivLink:
    return SerialCivLink(
        device=_DEVICE, open_serial_connection=fake.open_serial_connection
    )


async def _receive_reply(
    link: SerialCivLink, fake: CivSerialFake, *, timeout: float = 2.0
) -> CivFrame | None:
    """Receive until the radio's reply arrives, skipping the USB CI-V echo.

    The echo carries ``to_addr == civ_addr`` (it is our own frame returned
    verbatim); the reply is addressed to the controller.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        raw = await link.receive(timeout=max(0.01, deadline - time.monotonic()))
        if raw is None:
            return None
        frame = parse_civ_frame(raw)
        if frame.to_addr == CONTROLLER_ADDR:
            return frame
    return None


async def test_frequency_get_set_round_trip_through_the_link() -> None:
    fake = CivSerialFake(frequency_hz=14_074_000)
    link = _link_for(fake)
    await link.connect()
    try:
        await link.send_written(build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x03))
        reply = await _receive_reply(link, fake)
        assert reply is not None
        assert reply.command == 0x03
        assert reply.data == b"\x00\x40\x07\x14\x00"

        await link.send_written(
            build_civ_frame(
                fake.civ_addr, CONTROLLER_ADDR, 0x05, data=b"\x00\x50\x13\x07\x00"
            )
        )
        ack = await _receive_reply(link, fake)
        assert ack is not None
        assert ack.command == 0xFB
        assert fake.frequency_hz == 7_135_000

        await link.send_written(build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x03))
        reply = await _receive_reply(link, fake)
        assert reply is not None
        assert reply.data == b"\x00\x50\x13\x07\x00"
    finally:
        await link.disconnect()
    assert not link.connected


async def test_identity_read_through_the_link() -> None:
    fake = CivSerialFake()
    link = _link_for(fake)
    await link.connect()
    try:
        await link.send_written(
            build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x19, sub=0x00)
        )
        reply = await _receive_reply(link, fake)
        assert reply is not None
        assert (reply.command, reply.sub) == (0x19, 0x00)
        assert reply.from_addr == fake.civ_addr
        assert reply.data == fake.model_id
    finally:
        await link.disconnect()


async def test_delayed_identity_answer_through_the_link() -> None:
    fake = CivSerialFake()
    fake.delay_identity_answer(0.15)
    link = _link_for(fake)
    await link.connect()
    try:
        started = time.monotonic()
        await link.send_written(
            build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x19, sub=0x00)
        )
        reply = await _receive_reply(link, fake)
        elapsed = time.monotonic() - started
        assert reply is not None
        assert elapsed >= 0.15
        assert reply.data == fake.model_id
    finally:
        await link.disconnect()


async def test_silent_link_reads_as_no_answer_while_staying_connected() -> None:
    """MOR-2861: silence is a receive timeout, not a disconnect or an error."""
    fake = CivSerialFake()
    link = _link_for(fake)
    await link.connect()
    try:
        fake.silence(0.4)
        await link.send_written(build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x03))
        assert await _receive_reply(link, fake, timeout=0.3) is None
        assert link.connected
        assert link.healthy

        # A write issued during the window is swallowed with the rest, so the
        # recovery probe must go out only after the silence has lapsed.
        await asyncio.sleep(0.2)
        await link.send_written(build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x03))
        reply = await _receive_reply(link, fake, timeout=1.0)
        assert reply is not None  # the window lapsed; the radio answers again
        assert reply.command == 0x03
    finally:
        await link.disconnect()


async def test_usb_vanish_unhealthy_then_reopen_reports_serial_not_found() -> None:
    fake = CivSerialFake()
    link = _link_for(fake)
    await link.connect()
    fake.usb_vanish(at=0.02)
    try:
        # The vanished device raises ENODEV on the wire: the link's read path
        # treats it as recoverable, drops healthy, and receive() times out.
        assert await link.receive(timeout=1.5) is None
        assert not link.healthy
    finally:
        await link.disconnect()

    # Reopening while the device is still gone surfaces the OSError that
    # serial_open_diagnostic maps for a disappeared device.
    with pytest.raises(OSError) as exc_info:
        await link.connect()
    assert exc_info.value.errno == errno.ENODEV
    assert serial_open_diagnostic(exc_info.value) == "serial_not_found: ENODEV"

    fake.usb_reappear(after=0.05)
    await asyncio.sleep(0.1)  # let the device reappear
    await link.connect()
    try:
        await link.send_written(
            build_civ_frame(fake.civ_addr, CONTROLLER_ADDR, 0x19, sub=0x00)
        )
        reply = await _receive_reply(link, fake)
        assert reply is not None
        assert reply.data == fake.model_id
    finally:
        await link.disconnect()
