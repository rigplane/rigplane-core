"""MOR-2930: IC-705 repeater shift direction over ``0F``.

IC-705 CI-V Reference Guide (A7560-8EX-1, Jul. 2020, and A7560-8EX-6,
Jan. 2023), p.3: command ``0F`` sets 10 = simplex, 11 = DUP−, 12 = DUP+
and reads 00 = split OFF, 01 = split ON, 11 = DUP−, 12 = DUP+. The
direction reaches the radio through the existing ``RepeaterShiftCapable``
contract and the ``set_repeater_shift`` command descriptor the FTX-1 uses.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rigplane.commands import (
    CONTROLLER_ADDR,
    build_civ_frame,
    get_repeater_shift,
    parse_civ_frame,
    parse_repeater_shift_response,
    set_repeater_shift,
)
from rigplane.core.command_dispatch import (
    CommandUnsupportedError,
    command_descriptor,
    prepare_command_intent,
)
from rigplane.core.radio_protocol import RepeaterShiftCapable
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.types import RepeaterShiftDirection
from rigplane.exceptions import CommandError
from rigplane.radio import IcomRadio
from rigplane.rig_loader import load_rig
from rigplane.runtime._state_queries import acquisition_query_resolver_for_profile
from rigplane.types import CivFrame
from test_radio import MockTransport, _wrap_civ_in_udp

IC705_PATH = Path(__file__).resolve().parent.parent / "rigs" / "ic705.toml"
IC705_ADDR = 0xA4
SHIFT_PATH = FieldPath.parse("receiver.main.operator_controls.repeater_shift")
# CI-V observations name the receiver by number, as
# ``test_civ_rx_coverage.py`` reads ``receiver.0.operator_controls.att``.
SHIFT_OBSERVED = FieldPath.receiver("0", "operator_controls", "repeater_shift")

_SET_CODES = [
    pytest.param(RepeaterShiftDirection.SIMPLEX, 0x10, id="simplex-10"),
    pytest.param(RepeaterShiftDirection.MINUS, 0x11, id="minus-11"),
    pytest.param(RepeaterShiftDirection.PLUS, 0x12, id="plus-12"),
]
_READBACKS = [
    pytest.param(0x00, RepeaterShiftDirection.SIMPLEX, id="00-split-off"),
    pytest.param(0x01, RepeaterShiftDirection.SIMPLEX, id="01-split-on"),
    pytest.param(0x11, RepeaterShiftDirection.MINUS, id="11-dup-minus"),
    pytest.param(0x12, RepeaterShiftDirection.PLUS, id="12-dup-plus"),
]


@pytest.fixture(scope="module")
def ic705_map():
    return load_rig(IC705_PATH).to_command_map()


@pytest.fixture
def transport() -> MockTransport:
    return MockTransport()


def _radio(transport: MockTransport, model: str = "IC-705") -> IcomRadio:
    r = IcomRadio("192.168.1.100", timeout=0.05, model=model)
    r._civ_transport = transport
    r._ctrl_transport = transport
    r._connected = True
    return r


def _reply(command: int, data: bytes = b"") -> bytes:
    return _wrap_civ_in_udp(
        build_civ_frame(CONTROLLER_ADDR, IC705_ADDR, command, data=data)
    )


def _sent_frames(transport: MockTransport) -> list[CivFrame]:
    return [
        parse_civ_frame(packet[packet.index(b"\xfe\xfe") :])
        for packet in transport.sent_packets
        if b"\xfe\xfe" in packet
    ]


# ── Builders ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(("direction", "code"), _SET_CODES)
def test_set_frame_carries_the_guide_code(ic705_map, direction, code) -> None:
    frame = parse_civ_frame(set_repeater_shift(direction, IC705_ADDR, cmd_map=ic705_map))
    assert (frame.command, frame.sub, frame.data) == (0x0F, None, bytes([code]))


@pytest.mark.parametrize("direction", [RepeaterShiftDirection.ARS, 4, True])
def test_set_refuses_a_direction_with_no_code(ic705_map, direction) -> None:
    with pytest.raises(ValueError):
        set_repeater_shift(direction, IC705_ADDR, cmd_map=ic705_map)


def test_read_frame_is_bare_0f(ic705_map) -> None:
    frame = parse_civ_frame(get_repeater_shift(IC705_ADDR, cmd_map=ic705_map))
    assert (frame.command, frame.sub, frame.data) == (0x0F, None, b"")


@pytest.mark.parametrize(("byte", "direction"), _READBACKS)
def test_readback_decode(byte, direction) -> None:
    assert parse_repeater_shift_response(bytes([byte])) is direction


@pytest.mark.parametrize("data", [b"", b"\x10", b"\x02"])
def test_readback_outside_the_guide_is_unknown(data) -> None:
    assert parse_repeater_shift_response(data) is None


# ── CoreRadio ────────────────────────────────────────────────────────


def test_icom_radio_is_repeater_shift_capable(transport) -> None:
    assert isinstance(_radio(transport), RepeaterShiftCapable)


@pytest.mark.asyncio
@pytest.mark.parametrize(("direction", "code"), _SET_CODES)
async def test_set_sends_the_guide_code(transport, direction, code) -> None:
    radio = _radio(transport)
    transport.queue_response(_reply(0xFB))
    await radio.set_repeater_shift(direction)
    sent = _sent_frames(transport)[-1]
    assert (sent.command, sent.data) == (0x0F, bytes([code]))


@pytest.mark.asyncio
async def test_set_raises_when_the_radio_refuses(transport) -> None:
    radio = _radio(transport)
    transport.queue_response(_reply(0xFA))
    with pytest.raises(CommandError):
        await radio.set_repeater_shift(RepeaterShiftDirection.MINUS)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("direction", "receiver", "error"),
    [
        pytest.param(RepeaterShiftDirection.ARS, 0, ValueError, id="ars"),
        pytest.param(RepeaterShiftDirection.PLUS, 1, CommandError, id="receiver-1"),
    ],
)
async def test_set_refuses_before_any_wire_traffic(
    transport, direction, receiver, error
) -> None:
    radio = _radio(transport)
    with pytest.raises(error):
        await radio.set_repeater_shift(direction, receiver=receiver)
    assert _sent_frames(transport) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("byte", "direction"), _READBACKS)
async def test_get_decodes_the_readback(transport, byte, direction) -> None:
    radio = _radio(transport)
    transport.queue_response(_reply(0x0F, bytes([byte])))
    assert await radio.get_repeater_shift() is direction


# ── Observation ──────────────────────────────────────────────────────


def _observe_0f(radio: IcomRadio, byte: int, from_addr: int) -> None:
    frame = CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=from_addr,
        command=0x0F,
        sub=None,
        data=bytes([byte]),
    )
    radio._civ_runtime._update_state_cache_from_frame(frame)


@pytest.mark.parametrize(("byte", "direction"), _READBACKS)
def test_0f_reply_publishes_the_shift(transport, byte, direction) -> None:
    radio = _radio(transport)
    _observe_0f(radio, byte, IC705_ADDR)
    snapshot = radio._state_store.snapshot()
    assert snapshot.field(SHIFT_OBSERVED).value == int(direction)
    assert snapshot.field("global.tx_state.split").value is (byte == 0x01)


def test_a_profile_without_the_shift_getter_publishes_no_shift(transport) -> None:
    """The IC-7610 profile declares no ``get_repeater_shift``."""
    radio = _radio(transport, model="IC-7610")
    _observe_0f(radio, 0x11, 0x98)
    snapshot = radio._state_store.snapshot()
    shift = {field.path: field for field in snapshot.fields}.get(SHIFT_OBSERVED)
    assert shift is None or shift.value is None
    assert snapshot.field("global.tx_state.split").value is False


# ── Profile, acquisition and dispatch ───────────────────────────────


def test_profile_declares_shift_without_ars() -> None:
    profile = load_rig(IC705_PATH).to_profile()
    assert profile.supports_capability("repeater_shift")
    assert not profile.supports_capability("repeater_shift_ars")


def test_shift_field_is_polled_through_the_0f_read() -> None:
    profile = load_rig(IC705_PATH).to_profile()
    assert SHIFT_PATH in profile.state_acquisition.pollable_paths()
    query = acquisition_query_resolver_for_profile(profile)(SHIFT_PATH)
    assert query is not None
    assert (query.command, query.sub, query.data) == (0x0F, None, b"")


def test_dispatch_admits_the_ic705_through_the_ftx1_descriptor(transport) -> None:
    descriptor = command_descriptor("set_repeater_shift")
    assert descriptor is not None
    intent = prepare_command_intent(
        _radio(transport),
        "set_repeater_shift",
        {"direction": int(RepeaterShiftDirection.MINUS), "receiver": 0},
        source="websocket",
    )
    assert intent.params["direction"] == int(RepeaterShiftDirection.MINUS)


def test_dispatch_refuses_a_profile_without_the_shift_setter(transport) -> None:
    with pytest.raises(CommandUnsupportedError):
        prepare_command_intent(
            _radio(transport, model="IC-7610"),
            "set_repeater_shift",
            {"direction": int(RepeaterShiftDirection.MINUS), "receiver": 0},
            source="websocket",
        )
