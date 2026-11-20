"""MOR-3624: repeater offset magnitude on Icom (``0C`` read / ``0D`` send).

IC-705 CI-V Reference Guide (A7560-8EX-1, Jul. 2020) p.3 and IC-9700 CI-V
Reference Guide (Icom, 2019) p.4: ``0C`` reads the duplex offset frequency
and ``0D`` writes it — 3-byte BCD, 100 Hz units, little-endian digit order
(the packing Hamlib's ``icom_set_rptr_offs`` uses with ``to_bcd``). The
10 MHz digit is valid only on a 1.2 GHz band, so the per-band maximum
lives in the rig profiles.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rigplane.commands import (
    CONTROLLER_ADDR,
    build_civ_frame,
    get_freq_offset,
    parse_civ_frame,
    parse_freq_offset_response,
    set_freq_offset,
)
from rigplane.core.command_dispatch import (
    CommandUnsupportedError,
    command_descriptor,
    prepare_command_intent,
)
from rigplane.core.radio_protocol import RepeaterOffsetCapable
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.exceptions import CommandError
from rigplane.radio import IcomRadio
from rigplane.rig_loader import load_rig
from rigplane.runtime._state_queries import acquisition_query_resolver_for_profile
from rigplane.types import CivFrame
from test_radio import MockTransport, _wrap_civ_in_udp
from rigplane_testkit.leaked_radio_guard import fake_connected

RIGS = Path(__file__).resolve().parent.parent / "rigs"
IC705_PATH = RIGS / "ic705.toml"
IC9700_PATH = RIGS / "ic9700.toml"
OFFSET_PATH = FieldPath.parse("receiver.main.operator_controls.repeater_offset_hz")
OFFSET_OBSERVED = FieldPath.receiver("0", "operator_controls", "repeater_offset_hz")
FREQ_OBSERVED = FieldPath.active("0", "freq_mode", "freq_hz")


@pytest.fixture(scope="module", params=["ic705", "ic9700"])
def rig(request):
    """Two radios declare the feature (IC-705, IC-9700); no model branches."""
    path = {"ic705": IC705_PATH, "ic9700": IC9700_PATH}[request.param]
    return request.param, load_rig(path).to_command_map(), load_rig(path).to_profile()


@pytest.fixture
def transport() -> MockTransport:
    return MockTransport()


def _radio(transport: MockTransport, model: str) -> IcomRadio:
    r = IcomRadio("192.168.1.100", timeout=0.05, model=model)
    r._civ_transport = transport
    r._ctrl_transport = transport
    fake_connected(r)
    return r


def _reply(command: int, addr: int, data: bytes = b"") -> bytes:
    return _wrap_civ_in_udp(
        build_civ_frame(CONTROLLER_ADDR, addr, command, data=data)
    )


def _sent_frames(transport: MockTransport) -> list[CivFrame]:
    return [
        parse_civ_frame(packet[packet.index(b"\xfe\xfe") :])
        for packet in transport.sent_packets
        if b"\xfe\xfe" in packet
    ]


# ── Builders ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("hz", "data"),
    [
        pytest.param(600_000, bytes([0x00, 0x60, 0x00]), id="600khz"),
        pytest.param(7_600_000, bytes([0x00, 0x60, 0x07]), id="7p6mhz"),
        pytest.param(0, bytes([0x00, 0x00, 0x00]), id="zero"),
        pytest.param(1_000_000, bytes([0x00, 0x00, 0x10]), id="1mhz"),
    ],
)
def test_set_frame_carries_little_endian_bcd(rig, hz, data) -> None:
    _, cmd_map, _ = rig
    frame = parse_civ_frame(set_freq_offset(hz, to_addr=0xA4, cmd_map=cmd_map))
    assert (frame.command, frame.sub, frame.data) == (0x0D, None, data)


@pytest.mark.parametrize(
    "hz", [pytest.param(-600_000, id="negative"), pytest.param(600_050, id="not-100hz")]
)
def test_set_refuses_values_the_wire_cannot_carry(rig, hz) -> None:
    _, cmd_map, _ = rig
    with pytest.raises(ValueError):
        set_freq_offset(hz, to_addr=0xA4, cmd_map=cmd_map)


def test_set_refuses_a_bool(rig) -> None:
    _, cmd_map, _ = rig
    with pytest.raises(ValueError):
        set_freq_offset(True, to_addr=0xA4, cmd_map=cmd_map)  # type: ignore[arg-type]


def test_read_frame_is_bare_0c(rig) -> None:
    _, cmd_map, _ = rig
    frame = parse_civ_frame(get_freq_offset(to_addr=0xA4, cmd_map=cmd_map))
    assert (frame.command, frame.sub, frame.data) == (0x0C, None, b"")


@pytest.mark.parametrize(
    ("data", "hz"),
    [
        pytest.param(bytes([0x00, 0x60, 0x00]), 600_000, id="600khz"),
        pytest.param(bytes([0x00, 0x00, 0x00]), 0, id="zero"),
        pytest.param(bytes([0x00, 0x60, 0x07]), 7_600_000, id="7p6mhz"),
    ],
)
def test_readback_decode(data, hz) -> None:
    assert parse_freq_offset_response(data) == hz


@pytest.mark.parametrize("data", [b"", b"\x00\x60", b"\x0a\x60\x00"])
def test_readback_outside_the_wire_shape_is_unknown(data) -> None:
    assert parse_freq_offset_response(data) is None


# ── CoreRadio ────────────────────────────────────────────────────────


def test_icom_radio_is_repeater_offset_capable(transport) -> None:
    assert isinstance(_radio(transport, "IC-705"), RepeaterOffsetCapable)


@pytest.mark.asyncio
async def test_get_decodes_the_readback(transport) -> None:
    radio = _radio(transport, "IC-705")
    transport.queue_response(_reply(0x0C, 0xA4, bytes([0x00, 0x60, 0x00])))
    assert await radio.get_repeater_offset() == 600_000


@pytest.mark.asyncio
async def test_set_sends_the_guide_frame_and_reads_back(transport) -> None:
    radio = _radio(transport, "IC-9700")
    transport.queue_response(_reply(0x0C, 0xA2, bytes([0x00, 0x00, 0x10])))
    assert await radio.get_repeater_offset() == 1_000_000
    transport.queue_response(_reply(0x0D, 0xA2))
    await radio.set_repeater_offset(1_000_000, receiver=0)
    sent = _sent_frames(transport)[-1]
    assert (sent.command, sent.data) == (0x0D, bytes([0x00, 0x00, 0x10]))


@pytest.mark.asyncio
async def test_set_raises_when_the_radio_refuses(transport) -> None:
    radio = _radio(transport, "IC-705")
    _observe_freq(radio, 146_520_000, 0xA4)
    transport.queue_response(_reply(0x0D, 0xA4, bytes([0xFA])))
    with pytest.raises(CommandError):
        await radio.set_repeater_offset(600_000)


# ── Guard G4: offset sanity ──────────────────────────────────────────


def _freq_data(hz: int) -> bytes:
    from rigplane.commands import bcd_encode_value

    return bcd_encode_value(hz // 10, byte_count=5)[::-1]


def _observe(radio: IcomRadio, command: int, data: bytes, from_addr: int) -> None:
    frame = CivFrame(
        to_addr=CONTROLLER_ADDR, from_addr=from_addr, command=command, sub=None, data=data
    )
    radio._civ_runtime._update_state_cache_from_frame(frame)


def _observe_freq(radio: IcomRadio, hz: int, from_addr: int) -> None:
    _observe(radio, 0x03, _freq_data(hz), from_addr)


def _observe_shift(radio: IcomRadio, code: int, from_addr: int) -> None:
    _observe(radio, 0x0F, bytes([code]), from_addr)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "hz", "offset"),
    [
        pytest.param("IC-705", 146_520_000, 600_000, id="ic705-2m-600k"),
        pytest.param("IC-9700", 446_000_000, 7_600_000, id="ic9700-70cm"),
        pytest.param("IC-9700", 1_294_500_000, 20_000_000, id="ic9700-23cm-10mhz-digit"),
    ],
)
async def test_in_range_offset_is_sent(transport, model, hz, offset) -> None:
    radio = _radio(transport, model)
    addr = 0xA4 if model == "IC-705" else 0xA2
    _observe_freq(radio, hz, addr)
    transport.queue_response(_reply(0x0D, addr))
    await radio.set_repeater_offset(offset)
    assert _sent_frames(transport)[-1].command == 0x0D


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "hz", "offset", "why"),
    [
        # G4: TX = RX + shift leaves the TX range (146.52 + 3 MHz > 148).
        pytest.param(
            "IC-705", 146_520_000, 3_000_000, "tx-outside-band", id="tx-leaves-range"
        ),
        # G4 unit mix-up: 600 kHz entered as Hz-of-MHz scale (600 MHz).
        pytest.param(
            "IC-705", 146_520_000, 600_000_000, "unit-mixup", id="unit-mixup"
        ),
        # Above the profile band maximum without the 1.2 GHz exception.
        pytest.param(
            "IC-9700", 146_520_000, 12_000_000, "band-max", id="above-band-max"
        ),
        # Offset is a repeater-band control; refuse on a non-repeater band.
        pytest.param(
            "IC-705", 14_074_000, 600_000, "not-a-repeater-band", id="hf-refused"
        ),
    ],
)
async def test_out_of_range_offset_is_refused_before_wire_traffic(
    transport, model, hz, offset, why
) -> None:
    radio = _radio(transport, model)
    _observe_freq(radio, hz, 0xA4 if model == "IC-705" else 0xA2)
    with pytest.raises(ValueError, match=why):
        await radio.set_repeater_offset(offset)
    assert _sent_frames(transport) == []


@pytest.mark.asyncio
async def test_offset_is_refused_when_the_frequency_is_unknown(transport) -> None:
    radio = _radio(transport, "IC-705")
    with pytest.raises(ValueError, match="frequency"):
        await radio.set_repeater_offset(600_000)
    assert _sent_frames(transport) == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("shift", "offset", "refused"),
    [
        pytest.param(0, 3_000_000, False, id="simplex-tx-is-rx"),
        pytest.param(1, 3_000_000, True, id="plus-out"),
        pytest.param(2, 3_000_000, False, id="minus-stays-in"),
    ],
)
async def test_tx_range_check_follows_the_shift_direction(
    transport, shift, offset, refused
) -> None:
    radio = _radio(transport, "IC-705")
    _observe_freq(radio, 147_000_000, 0xA4)
    _observe_shift(radio, {0: 0x10, 1: 0x12, 2: 0x11}[shift], 0xA4)
    if refused:
        with pytest.raises(ValueError, match="tx-outside-band"):
            await radio.set_repeater_offset(offset)
        assert _sent_frames(transport) == []
    else:
        transport.queue_response(_reply(0x0D, 0xA4))
        await radio.set_repeater_offset(offset)
        assert _sent_frames(transport)[-1].command == 0x0D


# ── Observation and state ────────────────────────────────────────────


def test_receiver_state_carries_the_offset_field() -> None:
    from rigplane.core.radio_state import ReceiverState

    assert ReceiverState().repeater_offset_hz == 0


def _observe_0c(radio: IcomRadio, data: bytes, from_addr: int) -> None:
    frame = CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=from_addr,
        command=0x0C,
        sub=None,
        data=data,
    )
    radio._civ_runtime._update_state_cache_from_frame(frame)


def test_0c_reply_publishes_the_offset(transport) -> None:
    radio = _radio(transport, "IC-705")
    _observe_0c(radio, bytes([0x00, 0x60, 0x00]), 0xA4)
    snapshot = radio._state_store.snapshot()
    assert snapshot.field(OFFSET_OBSERVED).value == 600_000


def test_unsolicited_offset_change_updates_the_state(transport) -> None:
    """MOR-3610 scenario: the radio moves the offset on its own."""
    radio = _radio(transport, "IC-9700")
    _observe_0c(radio, bytes([0x00, 0x60, 0x00]), 0xA2)
    _observe_0c(radio, bytes([0x00, 0x00, 0x10]), 0xA2)
    assert radio._state_store.snapshot().field(OFFSET_OBSERVED).value == 1_000_000


def test_a_profile_without_the_offset_getter_publishes_nothing(transport) -> None:
    """The IC-7610 profile declares no ``get_freq_offset``."""
    radio = _radio(transport, "IC-7610")
    _observe_0c(radio, bytes([0x00, 0x60, 0x00]), 0x98)
    snapshot = radio._state_store.snapshot()
    offset = {f.path: f for f in snapshot.fields}.get(OFFSET_OBSERVED)
    assert offset is None or offset.value is None


# ── Profiles, acquisition and dispatch ───────────────────────────────


def test_profiles_declare_the_capability_and_opcodes(rig) -> None:
    _, _, profile = rig
    assert profile.supports_capability("repeater_offset")
    assert profile.command_map.has("get_freq_offset")
    assert profile.command_map.has("set_freq_offset")


def test_offset_field_is_polled_through_the_0c_read(rig) -> None:
    _, _, profile = rig
    assert OFFSET_PATH in profile.state_acquisition.pollable_paths()
    query = acquisition_query_resolver_for_profile(profile)(OFFSET_PATH)
    assert query is not None
    assert (query.command, query.sub, query.data) == (0x0C, None, b"")


def test_dispatch_admits_set_repeater_offset(transport) -> None:
    descriptor = command_descriptor("set_repeater_offset")
    assert descriptor is not None
    intent = prepare_command_intent(
        _radio(transport, "IC-705"),
        "set_repeater_offset",
        {"offset_hz": 600_000, "receiver": 0},
        source="websocket",
    )
    assert intent.params["offset_hz"] == 600_000


def test_dispatch_refuses_a_profile_without_the_capability(transport) -> None:
    with pytest.raises(CommandUnsupportedError):
        prepare_command_intent(
            _radio(transport, "IC-7610"),
            "set_repeater_offset",
            {"offset_hz": 600_000, "receiver": 0},
            source="websocket",
        )


def test_dispatch_refuses_a_unit_mixup(transport) -> None:
    with pytest.raises(ValueError):
        prepare_command_intent(
            _radio(transport, "IC-705"),
            "set_repeater_offset",
            {"offset_hz": 600_050, "receiver": 0},
            source="websocket",
        )


def test_profile_band_offset_maxima_are_profile_data() -> None:
    """The 10 MHz digit is valid on 1.2 GHz only, so IC-9700 23cm allows more."""
    ic705 = load_rig(IC705_PATH).to_profile()
    ic9700 = load_rig(IC9700_PATH).to_profile()

    def maxima(profile):
        return {
            rng.label: rng.max_repeater_offset_hz
            for rng in profile.freq_ranges
            if rng.repeater
        }

    assert maxima(ic705) == {
        "2m": 9_999_900,
        "70cm": 9_999_900,
    }
    assert maxima(ic9700)["23cm"] == 99_999_900
    assert maxima(ic9700)["2m"] == 9_999_900
