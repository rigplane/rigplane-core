"""Relative mode writes preserve omitted fields and require observed acceptance."""

from dataclasses import replace

import pytest

from rigplane.commands.command_map import CommandMap
from rigplane.core.exceptions import CommandError, CommandRejectedError, TimeoutError
from rigplane.core.radio_protocol import RelativeVfoModeWritable, RelativeVfoState
from rigplane.runtime.radio import IcomRadio

from test_runtime_relative_vfo_frequency import FakeCivTransport, response


@pytest.fixture(params=["IC-7300", "IC-7300MK2", "IC-705"])
def route(request, monkeypatch):
    radio = IcomRadio("127.0.0.1", model=request.param)
    wire = FakeCivTransport()
    monkeypatch.setattr(radio, "_check_connected", lambda: None)
    monkeypatch.setattr(radio, "_send_civ_raw", wire)
    return radio, wire, radio.profile.civ_addr


async def test_explicit_mode_tuple_writes_full_payload_and_returns_observed_state(
    route,
):
    radio, wire, addr = route
    wire.replies = [
        response(addr, "FB"),
        response(addr, "25 01 00 40 07 14 00"),
        response(addr, "26 01 04 00 03"),
    ]
    assert isinstance(radio, RelativeVfoModeWritable)
    observed = await radio.write_relative_vfo_mode(
        0x01, selected=False, data=True, filter_width=2
    )
    assert observed == RelativeVfoState(14_074_000, "RTTY", 3, 0)
    assert wire.frames == [
        bytes.fromhex(f"FE FE {addr:02X} E0 26 01 01 01 02 FD"),
        bytes.fromhex(f"FE FE {addr:02X} E0 25 01 FD"),
        bytes.fromhex(f"FE FE {addr:02X} E0 26 01 FD"),
    ]


@pytest.mark.parametrize(
    "fields,payload",
    [({}, "01 03"), ({"data": False}, "00 03"), ({"filter_width": 2}, "01 02")],
)
async def test_omitted_fields_are_preserved_from_target_readback(
    route, fields, payload
):
    radio, wire, addr = route
    wire.replies = [
        response(addr, "25 01 00 40 07 14 00"),
        response(addr, "26 01 01 01 03"),
        response(addr, "FB"),
        response(addr, "25 01 00 40 07 14 00"),
        response(addr, f"26 01 01 {payload}"),
    ]
    await radio.write_relative_vfo_mode(0x01, selected=False, **fields)
    assert wire.frames[2] == bytes.fromhex(f"FE FE {addr:02X} E0 26 01 01 {payload} FD")
    assert all(frame[4] in (0x25, 0x26) for frame in wire.frames)


@pytest.mark.parametrize(
    "reply,error",
    [
        ("FA", CommandRejectedError),
        ("26 01 01 00 01", CommandError),
        (None, CommandError),
        ("timeout", TimeoutError),
    ],
)
async def test_mode_requires_positive_ack_before_readback(route, reply, error):
    radio, wire, addr = route
    if reply == "timeout":
        wire.replies = [TimeoutError("timeout")]
    else:
        wire.replies = [response(addr, reply) if reply is not None else None]
    with pytest.raises(error):
        await radio.write_relative_vfo_mode(
            0x01, selected=False, data=False, filter_width=1
        )
    assert len(wire.frames) == 1


@pytest.mark.parametrize(
    "missing", ["set_unselected_mode", "get_unselected_mode", "get_unselected_freq"]
)
async def test_missing_command_refuses_before_io(route, missing):
    radio, wire, _addr = route
    cmd_map = radio.profile.command_map
    radio._profile = replace(
        radio.profile,
        command_map=CommandMap(
            {key: cmd_map.get(key) for key in cmd_map if key != missing}
        ),
    )
    with pytest.raises(CommandError, match="unavailable"):
        await radio.write_relative_vfo_mode(0x01, selected=False)
    assert wire.frames == []


@pytest.mark.parametrize(
    "mode,fields",
    [
        (0xFF, {}),
        (0x01, {"data": 2}),
        (0x01, {"filter_width": 0}),
        (0x01, {"filter_width": 2400}),
    ],
)
async def test_invalid_tuple_refuses_before_io(route, mode, fields):
    radio, wire, _addr = route
    with pytest.raises(CommandError):
        await radio.write_relative_vfo_mode(mode, selected=False, **fields)
    assert wire.frames == []


@pytest.mark.parametrize("reply", ["26 01 01", "26 01 01 02 03", "26 00 01 01 03"])
async def test_invalid_preservation_readback_does_not_write(route, reply):
    radio, wire, addr = route
    wire.replies = [response(addr, "25 01 00 40 07 14 00"), response(addr, reply)]
    with pytest.raises(CommandError):
        await radio.write_relative_vfo_mode(0x01, selected=False, filter_width=2)
    assert len(wire.frames) == 2
    assert all(len(frame) <= 7 for frame in wire.frames)


@pytest.mark.parametrize("reply", [None, "26 01 01", "26 00 01 01 03"])
async def test_missing_or_wrong_mode_readback_is_failure(route, reply):
    radio, wire, addr = route
    wire.replies = [
        response(addr, "FB"),
        response(addr, "25 01 00 40 07 14 00"),
        response(addr, reply) if reply is not None else None,
    ]
    with pytest.raises(CommandError):
        await radio.write_relative_vfo_mode(
            0x01, selected=False, data=False, filter_width=1
        )


async def test_profile_identity_is_not_a_mode_gate(route):
    radio, wire, addr = route
    radio._profile = replace(radio.profile, id="arbitrary_profile_id")
    wire.replies = [
        response(addr, "FB"),
        response(addr, "25 01 00 40 07 14 00"),
        response(addr, "26 01 01 00 01"),
    ]
    observed = await radio.write_relative_vfo_mode(
        0x01, selected=False, data=False, filter_width=1
    )
    assert observed == RelativeVfoState(14_074_000, "USB", 1, 0)
    assert wire.frames == [
        bytes.fromhex(f"FE FE {addr:02X} E0 26 01 01 00 01 FD"),
        bytes.fromhex(f"FE FE {addr:02X} E0 25 01 FD"),
        bytes.fromhex(f"FE FE {addr:02X} E0 26 01 FD"),
    ]


async def test_selected_mode_uses_existing_selected_command_declaration(route):
    radio, wire, addr = route
    if not radio.profile.command_map.has("set_selected_mode"):
        with pytest.raises(CommandError, match="unavailable"):
            await radio.write_relative_vfo_mode(0x01, selected=True)
        assert wire.frames == []
        return
    wire.replies = [
        response(addr, "FB"),
        response(addr, "25 00 00 40 07 14 00"),
        response(addr, "26 00 01 00 01"),
    ]
    await radio.write_relative_vfo_mode(0x01, selected=True, data=False, filter_width=1)
    assert wire.frames[0] == bytes.fromhex(f"FE FE {addr:02X} E0 26 00 01 00 01 FD")
