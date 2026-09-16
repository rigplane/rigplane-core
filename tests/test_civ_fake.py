"""Tests for ``tests/civ_fake.py`` — the shared CI-V radio fake (MOR-2492)."""

from __future__ import annotations

import asyncio

import pytest

from civ_fake import CivRadioFake, CivSerialPort
from rigplane.profiles import resolve_radio_profile
from rigplane.types import CivFrame

_PROBE_CMD = bytes(
    [0xFE, 0xFE, 0x00, 0xE0, 0x19, 0x00, 0xFD]
)  # transceiver ID, broadcast
_XIEGU_MODEL_ID_CMD = bytes([0xFE, 0xFE, 0xA4, 0xE0, 0x1D, 0x19, 0xFD])

_IC7610_ID_REPLY = bytes([0xFE, 0xFE, 0xE0, 0x98, 0x19, 0x00, 0x01, 0x06, 0xFD])
_XIEGU_MODEL_ID_REPLY = bytes([0xFE, 0xFE, 0xE0, 0xA4, 0x1D, 0x19, 0x62, 0x00, 0xFD])
_IC705_NAK_REPLY = bytes([0xFE, 0xFE, 0xE0, 0xA4, 0xFA, 0xFD])


def test_identity_comes_from_the_profile_not_a_table() -> None:
    radio = CivRadioFake("IC-705")
    assert radio.model == "IC-705"
    assert radio.civ_addr == resolve_radio_profile(model="IC-705").civ_addr == 0xA4
    assert radio.model_id == b"\x01\x05"
    assert CivRadioFake("IC-705", civ_addr=0xA5).civ_addr == 0xA5
    assert CivRadioFake().model == "IC-7610"


def test_handle_transceiver_id_round_trip() -> None:
    radio = CivRadioFake("IC-7610")
    assert radio.handle(_PROBE_CMD) == _IC7610_ID_REPLY
    addressed = bytes([0xFE, 0xFE, 0x98, 0xE0, 0x19, 0x00, 0xFD])
    assert radio.handle(addressed) == _IC7610_ID_REPLY


def test_handle_xiegu_model_id_reply() -> None:
    radio = CivRadioFake("X6200")
    assert radio.civ_addr == 0xA4  # the X6200 / IC-705 address collision
    assert radio.handle(_XIEGU_MODEL_ID_CMD) == _XIEGU_MODEL_ID_REPLY


def test_handle_naks_opcode_the_model_does_not_implement() -> None:
    # An IC-705 refuses 0x1D 0x19 because that is what an IC-705 is.
    radio = CivRadioFake("IC-705")
    assert radio.handle(_XIEGU_MODEL_ID_CMD) == _IC705_NAK_REPLY


def test_commands_records_parsed_frames_in_order() -> None:
    radio = CivRadioFake("X6200")
    radio.handle(_PROBE_CMD)
    radio.handle(_XIEGU_MODEL_ID_CMD)
    commands = radio.commands
    assert len(commands) == 2
    assert all(isinstance(c, CivFrame) for c in commands)
    probe, xiegu = commands
    assert (probe.to_addr, probe.from_addr, probe.command, probe.sub) == (
        0x00,
        0xE0,
        0x19,
        0x00,
    )
    assert (xiegu.to_addr, xiegu.command, xiegu.sub, xiegu.data) == (
        0xA4,
        0x1D,
        None,
        b"\x19",
    )


def test_handle_ignores_frames_for_other_addresses() -> None:
    radio = CivRadioFake("IC-705")
    for_ic7610 = bytes([0xFE, 0xFE, 0x98, 0xE0, 0x19, 0x00, 0xFD])
    assert radio.handle(for_ic7610) is None
    assert len(radio.commands) == 1  # still recorded


@pytest.mark.asyncio
async def test_silent_port_accepts_writes_but_reads_never_yield() -> None:
    port = CivSerialPort(CivRadioFake("X6200"), silent=True)
    reader, writer = await port.opener(url="/dev/x", baudrate=19200)
    writer.write(_XIEGU_MODEL_ID_CMD)
    await writer.drain()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(reader.read(64), timeout=0.01)
    writer.close()
    await writer.wait_closed()
    assert writer.closed is True


@pytest.mark.asyncio
async def test_opener_call_shape_and_port_round_trip() -> None:
    port = CivSerialPort(CivRadioFake("IC-7610"))
    reader, writer = await port.opener(url="/dev/ttyUSB0", baudrate=19200, bytesize=8)
    writer.write(_PROBE_CMD)
    await writer.drain()
    reply = await asyncio.wait_for(reader.read(64), timeout=0.05)
    assert reply == _IC7610_ID_REPLY
    writer.close()
    await writer.wait_closed()
    # A baud sweep reopens the port: each opener() call yields a fresh pair.
    reader2, writer2 = await port.opener(url="/dev/ttyUSB0", baudrate=9600)
    assert reader2 is not reader
    assert writer2 is not writer


@pytest.mark.asyncio
async def test_port_deframes_split_writes() -> None:
    port = CivSerialPort(CivRadioFake("IC-7610"))
    reader, writer = await port.opener(url="/dev/ttyUSB0", baudrate=19200)
    writer.write(_PROBE_CMD[:4])
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(reader.read(64), timeout=0.01)
    writer.write(_PROBE_CMD[4:])
    reply = await asyncio.wait_for(reader.read(64), timeout=0.05)
    assert reply == _IC7610_ID_REPLY
