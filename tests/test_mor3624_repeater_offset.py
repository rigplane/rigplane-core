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
    return _wrap_civ_in_udp(build_civ_frame(CONTROLLER_ADDR, addr, command, data=data))


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
        pytest.param(1_000_000, bytes([0x00, 0x00, 0x01]), id="1mhz"),
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
        pytest.param(bytes([0x00, 0x00, 0x01]), 1_000_000, id="1mhz"),
        # 0x10 in the most-significant pair's high digit: 10 MHz, not 1 MHz.
        pytest.param(bytes([0x00, 0x00, 0x10]), 10_000_000, id="10mhz"),
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
async def test_ic9700_get_reads_the_offset_back(transport) -> None:
    """The dual-receiver roundtrip: MAIN selected, 0C query, 3-byte reply."""
    radio = _radio(transport, "IC-9700")
    radio._record_selection("MAIN")  # the plain 0C reaches MAIN only
    transport.queue_response(_reply(0x0C, 0xA2, bytes([0x00, 0x00, 0x01])))
    assert await radio.get_repeater_offset() == 1_000_000
    sent = _sent_frames(transport)[-1]
    assert (sent.command, sent.data) == (0x0C, b"")


@pytest.mark.asyncio
async def test_set_raises_when_the_radio_refuses(transport) -> None:
    radio = _radio(transport, "IC-705")
    transport.queue_response(_reply(0xFA, 0xA4))
    with pytest.raises(CommandError):
        await radio.set_repeater_offset(600_000)


# ── Input validation: the profile's declared maximum ─────────────────
# Owner guard rule 2026-10-30: G4 (the TX-range guard) is gone; the only
# validation left is plain value validation against the profile's declared
# maximum, plus the wire resolution in the builder. The tuned band and the
# resulting TX frequency are the radio's own refusal.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "offset"),
    [
        pytest.param("IC-705", 600_000, id="ic705-600k"),
        pytest.param("IC-9700", 7_600_000, id="ic9700-70cm"),
        pytest.param("IC-9700", 20_000_000, id="ic9700-23cm-10mhz-digit"),
    ],
)
async def test_offset_within_the_profile_maximum_is_sent(
    transport, model, offset
) -> None:
    radio = _radio(transport, model)
    addr = 0xA4 if model == "IC-705" else 0xA2
    if model == "IC-9700":
        # Dual receiver: the plain 0D reaches only the selected receiver
        # (MOR-3591), so MAIN must be selected before the write.
        radio._record_selection("MAIN")
    transport.queue_response(_reply(0xFB, addr))
    await radio.set_repeater_offset(offset)
    assert _sent_frames(transport)[-1].command == 0x0D


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "offset"),
    [
        # Unit mix-up: 600 kHz entered on the Hz-of-MHz scale (600 MHz).
        pytest.param("IC-705", 600_000_000, id="ic705-unit-mixup"),
        # Above the IC-9700 profile maximum (99,999,900 Hz, 23cm).
        pytest.param("IC-9700", 100_000_000, id="ic9700-above-profile-max"),
    ],
)
async def test_offset_above_the_profile_maximum_is_refused_before_wire_traffic(
    transport, model, offset
) -> None:
    radio = _radio(transport, model)
    with pytest.raises(ValueError, match="profile's declared maximum"):
        await radio.set_repeater_offset(offset)
    assert _sent_frames(transport) == []


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
    _observe_0c(radio, bytes([0x00, 0x00, 0x01]), 0xA2)
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


def test_dispatch_refuses_a_non_integer_offset(transport) -> None:
    """The bind's typed contract: an integer in Hz, nothing looser."""
    with pytest.raises(ValueError, match="integer"):
        prepare_command_intent(
            _radio(transport, "IC-705"),
            "set_repeater_offset",
            {"offset_hz": 600_050.5, "receiver": 0},
            source="websocket",
        )


def test_profile_offset_maximum_is_the_largest_declared_band_maximum() -> None:
    """Input validation uses the profile-wide declared maximum; the tuned
    band is the radio's own refusal (owner guard rule 2026-10-30)."""
    ic705 = load_rig(IC705_PATH).to_profile()
    ic9700 = load_rig(IC9700_PATH).to_profile()
    ic7610 = load_rig(RIGS / "ic7610.toml").to_profile()

    assert ic705.repeater_offset_max_hz == 9_999_900
    assert ic9700.repeater_offset_max_hz == 99_999_900
    # No repeater-flagged range declares a maximum: no profile bound.
    assert ic7610.repeater_offset_max_hz is None


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
