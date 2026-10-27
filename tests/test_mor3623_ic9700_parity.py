"""MOR-3623: IC-9700 profile parity with the IC-705 (VHF/UHF V1).

Simulated/builder-level only: no IC-9700 simulator personality exists yet
(MOR-3606) and no IC-9700 has been on the bench, so nothing here is
hardware-verified (tier B-). Every fact is read from the shipped profile
through the same shared, profile-driven paths the IC-705 tests use:

* repeater shift over ``0F`` — the pattern of
  ``tests/test_mor2930_ic705_repeater_shift.py`` (IC-9700 CI-V Reference
  Guide (Icom, 2019) p.4: sets 10 = simplex, 11 = DUP−, 12 = DUP+; 13 is
  RPS, a DD-mode duplex that is not a shift direction);
* the ``16 5D`` tone squelch selector and its ``[tone_squelch_types]``
  table — the pattern of ``tests/test_mor2955_tone_squelch_type.py``
  (same selector command and codes as the IC-705);
* DV (CI-V 0x17) on every band and DD (0x22) only on the 1.2 GHz band.
  The band scope is the profile's own ``[memory] dd_groups = [3]`` fact
  (``commands/memory.py: _parse_block``), the only band-scoped mode
  mechanism the profiles carry; the mode-set path carries no frequency,
  so a band-conditional gate there would be a new mechanism, out of this
  profile-data slice;
* the band ``default_hz`` values (IARU Region 1 simplex calling
  frequencies on 2 m and 70 cm, band start edge on 23 cm).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rigplane.commands import (
    CONTROLLER_ADDR,
    build_civ_frame,
    get_repeater_shift,
    parse_civ_frame,
    parse_memory_entry,
    parse_repeater_shift_response,
    set_mode,
    set_repeater_shift,
)
from rigplane.core.command_dispatch import prepare_command_intent
from rigplane.core.radio_protocol import RepeaterShiftCapable
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.types import Mode, RepeaterShiftDirection
from rigplane.radio import IcomRadio
from rigplane.rig_loader import load_rig
from rigplane.runtime._state_queries import acquisition_query_resolver_for_profile
from rigplane.types import CivFrame
from rigplane.web.runtime_helpers import runtime_capabilities
from test_icom_memory_entry import _block, _ic9700_reply
from test_radio import MockTransport, _wrap_civ_in_udp

RIGS = Path(__file__).resolve().parent.parent / "rigs"
IC9700_PATH = RIGS / "ic9700.toml"
IC705_PATH = RIGS / "ic705.toml"
IC9700_ADDR = 0xA2

# Each band's default tuning frequency (the profile data this ticket sets:
# IARU Region 1 simplex calling on 2 m / 70 cm, band start on 23 cm), with
# its 1A 01 band code — the memory group number on this radio.
_BANDS = [
    pytest.param("2m", 145_500_000, 1, id="2m"),
    pytest.param("70cm", 433_500_000, 2, id="70cm"),
    pytest.param("23cm", 1_240_000_000, 3, id="23cm"),
]

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

SHIFT_PATH = FieldPath.parse("receiver.main.operator_controls.repeater_shift")
TYPE_PATH = FieldPath.parse("receiver.main.operator_controls.tone_squelch_type")
# CI-V observations name the receiver by number
# (``receiver.0.operator_controls.repeater_shift``).
SHIFT_OBSERVED = FieldPath.receiver("0", "operator_controls", "repeater_shift")
TYPE_OBSERVED = "receiver.0.operator_controls.tone_squelch_type"


@pytest.fixture(scope="module")
def ic9700_map():
    return load_rig(IC9700_PATH).to_command_map()


@pytest.fixture(scope="module")
def ic9700_profile():
    return load_rig(IC9700_PATH).to_profile()


@pytest.fixture(scope="module")
def ic9700_memory():
    memory = load_rig(IC9700_PATH).memory
    assert memory is not None
    return memory


@pytest.fixture
def transport() -> MockTransport:
    return MockTransport()


def _radio(transport: MockTransport, model: str = "IC-9700") -> IcomRadio:
    # timeout 2.0 per MOR-2121: 0.05 raced real scheduling delay under
    # ``pytest -n auto`` on a loaded host.
    r = IcomRadio("192.168.1.100", timeout=2.0, model=model)
    r._civ_transport = transport
    r._ctrl_transport = transport
    r._connected = True
    return r


def _reply(command: int, data: bytes = b"") -> bytes:
    return _wrap_civ_in_udp(
        build_civ_frame(CONTROLLER_ADDR, IC9700_ADDR, command, data=data)
    )


def _sent_frames(transport: MockTransport) -> list[CivFrame]:
    return [
        parse_civ_frame(packet[packet.index(b"\xfe\xfe") :])
        for packet in transport.sent_packets
        if b"\xfe\xfe" in packet
    ]


def _freq_digits(freq_hz: int) -> str:
    """Ten BCD-ish decimal digits, 1 GHz down to 1 Hz (``_block`` layout)."""
    return f"{freq_hz:010d}"


# ── Repeater shift over 0F ────────────────────────────────────────────


@pytest.mark.parametrize(("direction", "code"), _SET_CODES)
def test_set_frame_carries_the_guide_code(ic9700_map, direction, code) -> None:
    frame = parse_civ_frame(
        set_repeater_shift(direction, to_addr=IC9700_ADDR, cmd_map=ic9700_map)
    )
    assert (frame.command, frame.sub, frame.data) == (0x0F, None, bytes([code]))


def test_set_refuses_a_direction_with_no_code(ic9700_map) -> None:
    # ARS has no 0F code on this radio (13 is RPS, the DD-mode duplex).
    with pytest.raises(ValueError):
        set_repeater_shift(
            RepeaterShiftDirection.ARS, to_addr=IC9700_ADDR, cmd_map=ic9700_map
        )


def test_read_frame_is_bare_0f(ic9700_map) -> None:
    frame = parse_civ_frame(get_repeater_shift(to_addr=IC9700_ADDR, cmd_map=ic9700_map))
    assert (frame.command, frame.sub, frame.data) == (0x0F, None, b"")


@pytest.mark.parametrize(("byte", "direction"), _READBACKS)
def test_readback_decode(byte, direction) -> None:
    assert parse_repeater_shift_response(bytes([byte])) is direction


@pytest.mark.asyncio
@pytest.mark.parametrize(("band", "freq", "band_code"), _BANDS)
async def test_set_sends_the_guide_code_on_every_band(
    transport, band, freq, band_code, ic9700_profile
) -> None:
    """DUP− rides 0F 11 after tuning inside each of the three bands.

    The frequency is the band's own (new) default, so the test also pins
    that each band entry the repeater strip serves carries that default
    and its 1A 01 band code.
    """
    band_info = next(
        b for r in ic9700_profile.freq_ranges for b in r.bands if b.name == band
    )
    assert band_info.default == freq
    assert band_info.bsr_code == band_code

    radio = _radio(transport)
    transport.queue_response_on_send(1, _reply(0xFB))  # set_freq ACK
    transport.queue_response_on_send(2, _reply(0xFB))  # shift ACK
    await radio.set_freq(freq)
    await radio.set_repeater_shift(RepeaterShiftDirection.MINUS)
    sent = _sent_frames(transport)
    assert (sent[-1].command, sent[-1].data) == (0x0F, b"\x11")


@pytest.mark.asyncio
@pytest.mark.parametrize(("byte", "direction"), _READBACKS)
async def test_get_decodes_the_readback(transport, byte, direction) -> None:
    radio = _radio(transport)
    transport.queue_response(_reply(0x0F, bytes([byte])))
    assert await radio.get_repeater_shift() is direction


@pytest.mark.asyncio
async def test_dispatch_admits_the_ic9700(transport) -> None:
    intent = prepare_command_intent(
        _radio(transport),
        "set_repeater_shift",
        {"direction": int(RepeaterShiftDirection.MINUS), "receiver": 0},
        source="websocket",
    )
    assert intent.params["direction"] == int(RepeaterShiftDirection.MINUS)


def test_profile_declares_shift_without_ars(ic9700_profile) -> None:
    assert ic9700_profile.supports_capability("repeater_shift")
    assert not ic9700_profile.supports_capability("repeater_shift_ars")


def test_the_radio_projects_the_shift_for_the_strip(transport) -> None:
    """The repeater strip gate: capability tag + RepeaterShiftCapable."""
    radio = _radio(transport)
    assert isinstance(radio, RepeaterShiftCapable)
    assert "repeater_shift" in runtime_capabilities(radio)


def test_shift_field_is_polled_through_the_0f_read(ic9700_profile) -> None:
    assert ic9700_profile.state_acquisition is not None
    assert SHIFT_PATH in ic9700_profile.state_acquisition.pollable_paths()
    query = acquisition_query_resolver_for_profile(ic9700_profile)(SHIFT_PATH)
    assert query is not None
    assert (query.command, query.sub, query.data) == (0x0F, None, b"")


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
    _observe_0f(radio, byte, IC9700_ADDR)
    snapshot = radio._state_store.snapshot()
    assert snapshot.field(SHIFT_OBSERVED).value == int(direction)
    assert snapshot.field("global.tx_state.split").value is (byte == 0x01)


# ── Tone squelch type over 16 5D ──────────────────────────────────────

# Same selector command and codes as the IC-705 (MOR-2131): 00=OFF,
# 01=TONE, 02=TSQL, 03=DTCS, 06=DTCS (T), 07=TONE (T)/DTCS (R),
# 08=DTCS (T)/TSQL (R), 09=TONE (T)/TSQL (R).
_GUIDE_TABLE = {
    0x00: "off",
    0x01: "tone",
    0x02: "tsql",
    0x03: "dtcs",
    0x06: "dtcs_t",
    0x07: "tone_t_dtcs_r",
    0x08: "dtcs_t_tsql_r",
    0x09: "tone_t_tsql_r",
}


def test_the_table_is_the_ic705_table(ic9700_profile) -> None:
    table = ic9700_profile.tone_squelch_types
    assert table is not None
    assert {code: kind.value for code, kind in table.items()} == _GUIDE_TABLE
    ic705 = load_rig(IC705_PATH).to_profile()
    assert table == ic705.tone_squelch_types


def _tone_frame(code: int) -> CivFrame:
    return CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=IC9700_ADDR,
        command=0x16,
        sub=0x5D,
        data=bytes([code]),
        receiver=0x00,
    )


@pytest.mark.parametrize(
    ("code", "name", "tone", "tsql"),
    [
        pytest.param(0x00, "off", False, False, id="00-off"),
        pytest.param(0x01, "tone", True, False, id="01-tone"),
        pytest.param(0x02, "tsql", True, True, id="02-tsql"),
        pytest.param(0x03, "dtcs", None, None, id="03-dtcs"),
        pytest.param(0x09, "tone_t_tsql_r", None, None, id="09-tone-t-tsql-r"),
    ],
)
def test_16_5d_publishes_the_type_and_the_same_booleans(
    transport, code, name, tone, tsql
) -> None:
    radio = _radio(transport)
    radio._civ_runtime._update_state_cache_from_frame(_tone_frame(code))
    snapshot = radio._state_store.snapshot()
    assert snapshot.field(TYPE_OBSERVED).value == name
    assert snapshot.field("receiver.0.operator_toggles.repeater_tone").value is tone
    assert snapshot.field("receiver.0.operator_toggles.repeater_tsql").value is tsql
    radio._connected = False


def test_tone_type_field_is_polled_through_16_5d(ic9700_profile) -> None:
    assert ic9700_profile.state_acquisition is not None
    assert TYPE_PATH in ic9700_profile.state_acquisition.pollable_paths()
    query = acquisition_query_resolver_for_profile(ic9700_profile)(TYPE_PATH)
    assert query is not None
    assert (query.command, query.sub, query.data) == (0x16, 0x5D, b"")


# ── DV and DD modes ───────────────────────────────────────────────────


def test_dd_carries_the_civ_mode_byte() -> None:
    """House style of ``TestPskModeEnumValues``: the shared CI-V enum pins
    the guide's mode byte. DD is the IC-9700 1.2 GHz digital-data mode."""
    assert Mode.DV == 0x17
    assert Mode.DD == 0x22


@pytest.mark.parametrize(("mode", "code"), [(Mode.DV, 0x17), (Mode.DD, 0x22)])
def test_set_mode_carries_the_mode_byte(ic9700_map, mode, code) -> None:
    frame = parse_civ_frame(set_mode(mode, to_addr=IC9700_ADDR, cmd_map=ic9700_map))
    assert (frame.command, frame.data) == (0x06, bytes([code]))


@pytest.mark.parametrize(("band", "freq", "band_code"), _BANDS)
def test_dv_is_accepted_in_every_band_memory(
    ic9700_memory, band, freq, band_code
) -> None:
    """DV (0x17) is a plain mode byte: every band's memory accepts it."""
    entry = parse_memory_entry(
        _ic9700_reply(
            _block(freq=_freq_digits(freq), mode=0x17),
            band=band_code,
            channel=1,
            name=b"",
        ),
        ic9700_memory,
    )
    assert entry.mode == "DV"


def test_dd_is_accepted_on_23cm_memory(ic9700_memory) -> None:
    entry = parse_memory_entry(
        _ic9700_reply(
            _block(freq="1298000000", mode=0x22), band=3, channel=1, name=b""
        ),
        ic9700_memory,
    )
    assert entry.mode == "DD"


@pytest.mark.parametrize(("band", "freq", "band_code"), _BANDS[:2])
def test_dd_is_refused_below_1200_mhz_memory(
    ic9700_memory, band, freq, band_code
) -> None:
    """The profile's dd_groups = [3] refuses DD outside the 1.2 GHz band
    with an explicit error — the profile-data band scope for DD."""
    with pytest.raises(ValueError, match="mode"):
        parse_memory_entry(
            _ic9700_reply(
                _block(freq=_freq_digits(freq), mode=0x22),
                band=band_code,
                channel=1,
                name=b"",
            ),
            ic9700_memory,
        )


def test_dv_declares_the_fixed_width_dd_none(ic9700_profile) -> None:
    """DV copies the IC-705's fixed slots; DD is fixed with no modelled
    width in Hz — no slot width is invented for it."""
    assert ic9700_profile.declared_filter_width("DV", 1) == 7000
    assert ic9700_profile.declared_filter_width("DV", 3) == 7000
    assert ic9700_profile.declared_filter_width("DD", 1) is None


# ── Band defaults ─────────────────────────────────────────────────────


def test_band_defaults_are_the_calling_frequencies(ic9700_profile) -> None:
    bands = {
        b.name: (b.start, b.end, b.default)
        for r in ic9700_profile.freq_ranges
        for b in r.bands
    }
    assert bands["2m"] == (144_000_000, 148_000_000, 145_500_000)
    assert bands["70cm"] == (430_000_000, 450_000_000, 433_500_000)
    # 23 cm keeps its band start edge.
    assert bands["23cm"] == (1_240_000_000, 1_300_000_000, 1_240_000_000)


def test_repeater_ranges_stay_flagged(ic9700_profile) -> None:
    flagged = [
        (r.label, r.start, r.end) for r in ic9700_profile.freq_ranges if r.repeater
    ]
    assert flagged == [
        ("2m", 144_000_000, 148_000_000),
        ("70cm", 430_000_000, 450_000_000),
        ("23cm", 1_240_000_000, 1_300_000_000),
    ]


# ── IC-705 / IC-9700 parity table ─────────────────────────────────────

_PARITY = {
    "IC-705": {
        "path": "ic705.toml",
        "dd": False,  # no DD mode: no 1.2 GHz band on this radio
        "2m_default": 146_520_000,
        "70cm_default": 446_000_000,
    },
    "IC-9700": {
        "path": "ic9700.toml",
        "dd": True,
        "2m_default": 145_500_000,
        "70cm_default": 433_500_000,
    },
}


@pytest.mark.parametrize("model", ["IC-705", "IC-9700"])
def test_parity_table(model) -> None:
    """One row per profile so a future drift on either side fails here."""
    expected = _PARITY[model]
    config = load_rig(RIGS / expected["path"])
    profile = config.to_profile()

    assert profile.supports_capability("repeater_shift")
    command_map = config.to_command_map()
    assert command_map.get("get_repeater_shift") == (0x0F,)
    assert command_map.get("set_repeater_shift") == (0x0F,)
    assert command_map.get("get_tone_squelch_type") == (0x16, 0x5D)
    assert command_map.get("set_tone_squelch_type") == (0x16, 0x5D)
    assert profile.tone_squelch_types is not None
    assert "DV" in profile.modes
    assert ("DD" in profile.modes) is expected["dd"]

    bands = {b.name: b.default for r in profile.freq_ranges for b in r.bands}
    assert bands["2m"] == expected["2m_default"]
    assert bands["70cm"] == expected["70cm_default"]
