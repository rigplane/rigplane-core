"""Tests for CI-V command encoding and decoding."""

import inspect
from pathlib import Path

import pytest
import rigplane.commands as raw_commands

from rigplane import IC_7610_ADDR
from rigplane.rig_loader import load_rig
from rigplane.commands import (
    CONTROLLER_ADDR,
    build_civ_frame,
    filter_hz_to_index,
    filter_index_to_hz,
    get_alc,
    get_freq,
    get_mode,
    get_rf_power,
    get_s_meter,
    get_swr,
    parse_ack_nak,
    parse_civ_frame,
    parse_frequency_response,
    parse_meter_response,
    parse_mode_response,
    ptt_off,
    ptt_on,
    set_filter_width,
    set_freq,
    set_mode,
    set_rf_power,
)
from rigplane.types import (
    AgcMode,
    AudioPeakFilter,
    BreakInMode,
    CivFrame,
    FilterShape,
    Mode,
    SsbTxBandwidth,
)
from _command_test_helpers import bind_default_addr_globals, bind_default_addr_module

RIG_DIR = Path(__file__).resolve().parents[1] / "rigs"
_CTCSS_DOMAIN = load_rig(RIG_DIR / "ic7300.toml").ctcss_tones_centihz
assert _CTCSS_DOMAIN is not None

# The eight Icom CI-V scope span presets (span code 0-7 -> Hz), copied
# verbatim from the module constant `commands/scope.py` held at c23d8cbd
# -- the commit before this change deleted it. Written out here rather
# than imported so these tests pin the decoded/encoded values against a
# table this file owns, independent of whatever any profile happens to
# declare -- ``TestScopeSpanPresets`` in ``test_rig_loader.py`` is what
# asserts the shipped profiles still declare exactly these.
_SPAN_PRESETS_HZ = (2500, 5000, 10000, 25000, 50000, 100000, 250000, 500000)

_ORIGINAL_COMMANDS = {
    name: getattr(raw_commands, name) for name in raw_commands.__all__
}
bind_default_addr_module(raw_commands, to_addr=IC_7610_ADDR)
bind_default_addr_globals(globals(), to_addr=IC_7610_ADDR)


class TestConstants:
    """Test CI-V address constants."""

    def test_ic7610_addr(self) -> None:
        assert IC_7610_ADDR == 0x98

    def test_controller_addr(self) -> None:
        assert CONTROLLER_ADDR == 0xE0

    def test_public_builders_require_explicit_to_addr(self) -> None:
        for name, obj in _ORIGINAL_COMMANDS.items():
            if not callable(obj):
                continue
            signature = inspect.signature(obj)
            to_addr = signature.parameters.get("to_addr")
            if to_addr is None:
                continue
            assert to_addr.default is inspect.Signature.empty, name


class TestBuildCivFrame:
    """Test CIV frame construction."""

    def test_minimal_frame(self) -> None:
        frame = build_civ_frame(0x98, 0xE0, 0x03)
        assert frame == b"\xfe\xfe\x98\xe0\x03\xfd"

    def test_with_sub_command(self) -> None:
        frame = build_civ_frame(0x98, 0xE0, 0x15, sub=0x02)
        assert frame == b"\xfe\xfe\x98\xe0\x15\x02\xfd"

    def test_with_data(self) -> None:
        frame = build_civ_frame(0x98, 0xE0, 0x05, data=b"\x00\x40\x07\x14\x00")
        assert frame == b"\xfe\xfe\x98\xe0\x05\x00\x40\x07\x14\x00\xfd"

    def test_with_sub_and_data(self) -> None:
        frame = build_civ_frame(0x98, 0xE0, 0x14, sub=0x0A, data=b"\x01\x28")
        assert frame == b"\xfe\xfe\x98\xe0\x14\x0a\x01\x28\xfd"


class TestParseCivFrame:
    """Test CIV frame parsing."""

    def test_parse_minimal(self) -> None:
        result = parse_civ_frame(b"\xfe\xfe\x98\xe0\x03\xfd")
        assert result == CivFrame(
            to_addr=0x98, from_addr=0xE0, command=0x03, sub=None, data=b""
        )

    def test_parse_with_data(self) -> None:
        result = parse_civ_frame(b"\xfe\xfe\xe0\x98\x03\x00\x40\x07\x14\x00\xfd")
        assert result == CivFrame(
            to_addr=0xE0,
            from_addr=0x98,
            command=0x03,
            sub=None,
            data=b"\x00\x40\x07\x14\x00",
        )

    def test_parse_ack(self) -> None:
        result = parse_civ_frame(b"\xfe\xfe\xe0\x98\xfb\xfd")
        assert result.command == 0xFB
        assert result.data == b""

    def test_parse_nak(self) -> None:
        result = parse_civ_frame(b"\xfe\xfe\xe0\x98\xfa\xfd")
        assert result.command == 0xFA

    def test_roundtrip(self) -> None:
        original = build_civ_frame(0x98, 0xE0, 0x14, sub=0x0A, data=b"\x01\x28")
        parsed = parse_civ_frame(original)
        assert parsed.to_addr == 0x98
        assert parsed.from_addr == 0xE0
        assert parsed.command == 0x14
        assert parsed.sub == 0x0A
        assert parsed.data == b"\x01\x28"

    def test_invalid_preamble(self) -> None:
        with pytest.raises(ValueError, match="preamble"):
            parse_civ_frame(b"\xfe\xff\x98\xe0\x03\xfd")

    def test_missing_terminator(self) -> None:
        with pytest.raises(ValueError, match="terminator"):
            parse_civ_frame(b"\xfe\xfe\x98\xe0\x03\xfe")

    def test_too_short(self) -> None:
        with pytest.raises(ValueError):
            parse_civ_frame(b"\xfe\xfe\x98\xe0")


class TestFrequencyCommands:
    """Test frequency get/set commands.

    commands/freq.py migrated onto the bound command map in MOR-2008
    (batch 2): get_freq/set_freq now require cmd_map -- zero divergence,
    so IC-7610's own map declares the identical bytes the fallback used
    to build, and the expected frames below are unchanged.
    """

    @pytest.fixture()
    def cmd_map(self):
        rig = load_rig(RIG_DIR / "ic7610.toml")
        return rig.to_command_map()

    def test_get_frequency(self, cmd_map) -> None:
        frame = get_freq(cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x03\xfd"

    def test_get_frequency_requires_cmd_map(self) -> None:
        """cmd_map is required keyword-only -- MOR-2006 Q6's API break."""
        with pytest.raises(TypeError, match="MOR-2006"):
            get_freq()  # type: ignore[call-arg]

    def test_set_frequency_14mhz(self, cmd_map) -> None:
        frame = set_freq(14_074_000, cmd_map=cmd_map)
        expected_bcd = b"\x00\x40\x07\x14\x00"
        assert frame == b"\xfe\xfe\x98\xe0\x05" + expected_bcd + b"\xfd"

    def test_set_frequency_7mhz(self, cmd_map) -> None:
        frame = set_freq(7_074_000, cmd_map=cmd_map)
        expected_bcd = b"\x00\x40\x07\x07\x00"
        assert frame == b"\xfe\xfe\x98\xe0\x05" + expected_bcd + b"\xfd"

    def test_set_frequency_custom_addr(self, cmd_map) -> None:
        frame = set_freq(14_074_000, to_addr=0xA4, from_addr=0xE1, cmd_map=cmd_map)
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1

    def test_set_frequency_rejects_explicit_none_the_same_way(self, cmd_map) -> None:
        """An explicit ``cmd_map=None`` must hit the same Q6 explanation as
        omitting it entirely."""
        with pytest.raises(TypeError, match="MOR-2006"):
            set_freq(14_074_000, cmd_map=None)

    def test_parse_frequency_response(self) -> None:
        # Radio responds with cmd 0x03 + 5 bytes BCD
        resp = parse_civ_frame(b"\xfe\xfe\xe0\x98\x03\x00\x40\x07\x14\x00\xfd")
        freq = parse_frequency_response(resp)
        assert freq == 14_074_000

    def test_parse_frequency_response_band_edge(self) -> None:
        resp = parse_civ_frame(b"\xfe\xfe\xe0\x98\x02\x00\x40\x07\x14\x00\xfd")
        freq = parse_frequency_response(resp)
        assert freq == 14_074_000

    def test_parse_frequency_response_wrong_cmd(self) -> None:
        resp = CivFrame(
            to_addr=0xE0, from_addr=0x98, command=0x04, sub=None, data=b"\x00" * 5
        )
        with pytest.raises(ValueError, match="frequency"):
            parse_frequency_response(resp)


class TestModeCommands:
    """Test mode get/set commands.

    commands/mode.py migrated onto the bound command map in MOR-2008
    (batch 2): get_mode/set_mode now require cmd_map -- zero divergence,
    so IC-7610's own map declares the identical bytes the fallback used
    to build, and the expected frames below are unchanged.
    """

    @pytest.fixture()
    def cmd_map(self):
        rig = load_rig(RIG_DIR / "ic7610.toml")
        return rig.to_command_map()

    def test_get_mode(self, cmd_map) -> None:
        frame = get_mode(cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x04\xfd"

    def test_set_mode_usb(self, cmd_map) -> None:
        frame = set_mode(Mode.USB, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x06\x01\xfd"

    def test_set_mode_with_filter(self, cmd_map) -> None:
        frame = set_mode(Mode.CW, filter_width=2, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x06\x03\x02\xfd"

    def test_set_mode_requires_cmd_map(self) -> None:
        """cmd_map is required keyword-only -- MOR-2006 Q6's API break."""
        with pytest.raises(TypeError, match="MOR-2006"):
            set_mode(Mode.USB)  # type: ignore[call-arg]

    def test_parse_mode_response(self) -> None:
        resp = CivFrame(
            to_addr=0xE0, from_addr=0x98, command=0x04, sub=None, data=b"\x01"
        )
        mode, filt = parse_mode_response(resp)
        assert mode == Mode.USB
        assert filt is None

    def test_parse_mode_response_with_filter(self) -> None:
        resp = CivFrame(
            to_addr=0xE0, from_addr=0x98, command=0x04, sub=None, data=b"\x03\x02"
        )
        mode, filt = parse_mode_response(resp)
        assert mode == Mode.CW
        assert filt == 2

    def test_parse_mode_response_wrong_cmd(self) -> None:
        resp = CivFrame(
            to_addr=0xE0, from_addr=0x98, command=0x03, sub=None, data=b"\x01"
        )
        with pytest.raises(ValueError, match="mode"):
            parse_mode_response(resp)

    def test_parse_mode_response_empty_payload_raises(self) -> None:
        resp = CivFrame(to_addr=0xE0, from_addr=0x98, command=0x04, sub=None, data=b"")
        with pytest.raises(ValueError, match="payload too short"):
            parse_mode_response(resp)


class TestPowerCommands:
    """Test RF power get/set commands.

    commands/levels.py migrated onto the bound command map in MOR-2006
    Steps 5..N (module 2): both builders now require ``cmd_map``, and
    ``rigs/ic7610.toml``'s ``get_rf_power``/``set_rf_power`` declare the
    same ``[0x14, 0x0A]`` wire tuple the fallback used to build, so the
    expected frames below are unchanged.
    """

    @pytest.fixture()
    def cmd_map(self):
        rig = load_rig(RIG_DIR / "ic7610.toml")
        return rig.to_command_map()

    def test_get_power(self, cmd_map) -> None:
        frame = get_rf_power(cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x14\x0a\xfd"

    def test_set_power(self, cmd_map) -> None:
        # Power level is 0-255 encoded as 2-byte BCD (00-02 55)
        frame = set_rf_power(128, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x14\x0a\x01\x28\xfd"

    def test_set_power_zero(self, cmd_map) -> None:
        frame = set_rf_power(0, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x14\x0a\x00\x00\xfd"

    def test_set_power_max(self, cmd_map) -> None:
        frame = set_rf_power(255, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x14\x0a\x02\x55\xfd"

    def test_set_power_out_of_range(self, cmd_map) -> None:
        with pytest.raises(ValueError):
            set_rf_power(256, cmd_map=cmd_map)
        with pytest.raises(ValueError):
            set_rf_power(-1, cmd_map=cmd_map)


class TestMeterCommands:
    """Test meter reading commands.

    commands/meters.py migrated onto the bound command map in MOR-2008
    (batch 2): all ten builders now require cmd_map -- zero divergence,
    so IC-7610's own map declares the identical bytes the fallback used
    to build, and the expected frames below are unchanged.
    """

    @pytest.fixture()
    def cmd_map(self):
        rig = load_rig(RIG_DIR / "ic7610.toml")
        return rig.to_command_map()

    def test_get_s_meter(self, cmd_map) -> None:
        frame = get_s_meter(cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x15\x02\xfd"

    def test_get_swr(self, cmd_map) -> None:
        frame = get_swr(cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x15\x12\xfd"

    def test_get_alc(self, cmd_map) -> None:
        frame = get_alc(cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x15\x13\xfd"

    def test_get_s_meter_requires_cmd_map(self) -> None:
        """cmd_map is required keyword-only -- MOR-2006 Q6's API break."""
        with pytest.raises(TypeError, match="MOR-2006"):
            get_s_meter()  # type: ignore[call-arg]

    def test_parse_meter_response(self) -> None:
        # Meter values are 2-byte BCD: 0x01 0x20 = 120
        resp = CivFrame(
            to_addr=0xE0, from_addr=0x98, command=0x15, sub=0x02, data=b"\x01\x20"
        )
        value = parse_meter_response(resp)
        assert value == 120

    def test_parse_meter_response_zero(self) -> None:
        resp = CivFrame(
            to_addr=0xE0, from_addr=0x98, command=0x15, sub=0x02, data=b"\x00\x00"
        )
        value = parse_meter_response(resp)
        assert value == 0

    def test_parse_meter_response_max(self) -> None:
        resp = CivFrame(
            to_addr=0xE0, from_addr=0x98, command=0x15, sub=0x02, data=b"\x02\x55"
        )
        value = parse_meter_response(resp)
        assert value == 255


class TestFilterWidthCommands:
    """Test DSP IF filter width command encoding.

    commands/mode.py migrated onto the bound command map in MOR-2008
    (batch 2): set_filter_width now requires cmd_map -- zero divergence,
    so IC-7610's own map declares the identical bytes the fallback used
    to build, and the expected frames below are unchanged.
    """

    @pytest.fixture()
    def cmd_map(self):
        rig = load_rig(RIG_DIR / "ic7610.toml")
        return rig.to_command_map()

    def test_filter_hz_to_index_uses_segmented_ssb_ranges(self) -> None:
        segments = (
            {"hz_min": 50, "hz_max": 500, "step_hz": 50, "index_min": 0},
            {"hz_min": 600, "hz_max": 3600, "step_hz": 100, "index_min": 10},
        )
        assert filter_hz_to_index(50, segments=segments) == 0
        assert filter_hz_to_index(500, segments=segments) == 9
        assert filter_hz_to_index(600, segments=segments) == 10
        assert filter_hz_to_index(1500, segments=segments) == 19
        assert filter_hz_to_index(3600, segments=segments) == 40

    def test_filter_index_to_hz_uses_segmented_ssb_ranges(self) -> None:
        segments = (
            {"hz_min": 50, "hz_max": 500, "step_hz": 50, "index_min": 0},
            {"hz_min": 600, "hz_max": 3600, "step_hz": 100, "index_min": 10},
        )
        assert filter_index_to_hz(0, segments=segments) == 50
        assert filter_index_to_hz(9, segments=segments) == 500
        assert filter_index_to_hz(10, segments=segments) == 600
        assert filter_index_to_hz(19, segments=segments) == 1500
        assert filter_index_to_hz(40, segments=segments) == 3600

    def test_set_filter_width_cmd29_frame(self, cmd_map) -> None:
        frame = set_filter_width(19, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x29\x00\x1a\x03\x00\x19\xfd"

    def test_set_filter_width_sub_receiver_cmd29_frame(self, cmd_map) -> None:
        frame = set_filter_width(19, receiver=1, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x29\x01\x1a\x03\x00\x19\xfd"

    def test_set_filter_width_requires_cmd_map(self) -> None:
        """cmd_map is required keyword-only -- MOR-2006 Q6's API break."""
        with pytest.raises(TypeError, match="MOR-2006"):
            set_filter_width(19)  # type: ignore[call-arg]

    def test_parse_meter_response_short_payload_raises(self) -> None:
        resp = CivFrame(
            to_addr=0xE0, from_addr=0x98, command=0x15, sub=0x02, data=b"\x01"
        )
        with pytest.raises(ValueError, match="payload too short"):
            parse_meter_response(resp)


class TestPttCommands:
    """Test PTT on/off commands.

    commands/ptt.py migrated onto the bound command map in MOR-2007
    Steps 5..N (module 4): both builders now require cmd_map. IC-7610's
    declared ptt_on/ptt_off bytes are unchanged from the deleted
    fallback's (every CI-V profile already agreed, per the empty
    tests/command_map_parity_divergences.txt), so the expected literals
    below are unchanged too -- only the cmd_map= wiring is new.
    """

    @pytest.fixture()
    def cmd_map(self):
        rig = load_rig(RIG_DIR / "ic7610.toml")
        return rig.to_command_map()

    def test_ptt_on(self, cmd_map) -> None:
        frame = ptt_on(cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x1c\x00\x01\xfd"

    def test_ptt_off(self, cmd_map) -> None:
        frame = ptt_off(cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x1c\x00\x00\xfd"

    def test_ptt_on_requires_cmd_map(self) -> None:
        """cmd_map is required keyword-only -- MOR-2006 Q6's API break."""
        with pytest.raises(TypeError, match="MOR-2006"):
            ptt_on()  # type: ignore[call-arg]

    def test_ptt_off_rejects_explicit_none_the_same_way(self, cmd_map) -> None:
        """An explicit ``cmd_map=None`` must hit the same Q6 explanation as
        omitting it entirely."""
        with pytest.raises(TypeError, match="MOR-2006"):
            ptt_off(cmd_map=None)


class TestAckNak:
    """Test ACK/NAK response detection."""

    def test_ack(self) -> None:
        resp = CivFrame(to_addr=0xE0, from_addr=0x98, command=0xFB, sub=None, data=b"")
        assert parse_ack_nak(resp) is True

    def test_nak(self) -> None:
        resp = CivFrame(to_addr=0xE0, from_addr=0x98, command=0xFA, sub=None, data=b"")
        assert parse_ack_nak(resp) is False

    def test_not_ack_nak(self) -> None:
        resp = CivFrame(
            to_addr=0xE0, from_addr=0x98, command=0x03, sub=None, data=b"\x00" * 5
        )
        assert parse_ack_nak(resp) is None


class TestEdgeCases:
    """Test edge cases."""

    def test_parse_empty_data_frame(self) -> None:
        frame = build_civ_frame(0x98, 0xE0, 0x03)
        parsed = parse_civ_frame(frame)
        assert parsed.data == b""
        assert parsed.sub is None

    def test_unknown_command_roundtrip(self) -> None:
        frame = build_civ_frame(0x98, 0xE0, 0xFF, data=b"\x01\x02\x03")
        parsed = parse_civ_frame(frame)
        assert parsed.command == 0xFF
        assert parsed.data == b"\x01\x02\x03"

    def test_civframe_equality(self) -> None:
        a = CivFrame(to_addr=0x98, from_addr=0xE0, command=0x03, sub=None, data=b"")
        b = CivFrame(to_addr=0x98, from_addr=0xE0, command=0x03, sub=None, data=b"")
        assert a == b

    def test_civframe_with_sub(self) -> None:
        f = CivFrame(
            to_addr=0x98, from_addr=0xE0, command=0x15, sub=0x02, data=b"\x01\x20"
        )
        assert f.sub == 0x02


class TestCommand29:
    """Test Command29 framing for dual-receiver radios (IC-7610).

    commands/dsp.py migrated onto the bound command map in MOR-2008 batch
    3: its get_preamp/set_preamp/get_attenuator/set_attenuator_level
    builders now require ``cmd_map``, and ``rigs/ic7610.toml`` declares the
    same wire tuples the fallback used to build for all four -- no
    divergence rows name IC-7610 -- so the expected frames below are
    unchanged; only the ``cmd_map=`` wiring is new.
    """

    @pytest.fixture()
    def cmd_map(self):
        rig = load_rig(RIG_DIR / "ic7610.toml")
        return rig.to_command_map()

    def test_build_cmd29_frame_basic(self) -> None:
        from rigplane.commands import RECEIVER_MAIN, build_cmd29_frame

        frame = build_cmd29_frame(0x98, 0xE0, 0x16, sub=0x02, receiver=RECEIVER_MAIN)
        # FE FE 98 E0 29 00 16 02 FD
        assert frame == bytes.fromhex("fefe98e0 29 00 16 02 fd".replace(" ", ""))

    def test_cmd29_with_data(self) -> None:
        from rigplane.commands import RECEIVER_MAIN, build_cmd29_frame

        frame = build_cmd29_frame(
            0x98, 0xE0, 0x16, sub=0x02, data=b"\x01", receiver=RECEIVER_MAIN
        )
        assert frame == bytes.fromhex("fefe98e0 29 00 16 02 01 fd".replace(" ", ""))

    def test_cmd29_sub_receiver(self) -> None:
        from rigplane.commands import RECEIVER_SUB, build_cmd29_frame

        frame = build_cmd29_frame(
            0x98, 0xE0, 0x16, sub=0x02, data=b"\x02", receiver=RECEIVER_SUB
        )
        assert frame == bytes.fromhex("fefe98e0 29 01 16 02 02 fd".replace(" ", ""))

    def test_cmd29_no_sub(self) -> None:
        from rigplane.commands import RECEIVER_MAIN, build_cmd29_frame

        # ATT command has no sub-byte
        frame = build_cmd29_frame(0x98, 0xE0, 0x11, receiver=RECEIVER_MAIN)
        assert frame == bytes.fromhex("fefe98e0 29 00 11 fd".replace(" ", ""))

    def test_cmd29_att_with_data(self) -> None:
        from rigplane.commands import RECEIVER_MAIN, build_cmd29_frame

        frame = build_cmd29_frame(
            0x98, 0xE0, 0x11, data=b"\x18", receiver=RECEIVER_MAIN
        )
        assert frame == bytes.fromhex("fefe98e0 29 00 11 18 fd".replace(" ", ""))

    def test_parse_cmd29_response_preamp(self) -> None:
        # Radio response: FE FE E0 98 29 00 16 02 01 FD
        data = bytes.fromhex("fefee098 29 00 16 02 01 fd".replace(" ", ""))
        parsed = parse_civ_frame(data)
        assert parsed.command == 0x16  # Unwrapped to real command
        assert parsed.sub == 0x02
        assert parsed.data == b"\x01"  # Preamp level 1

    def test_parse_cmd29_response_att(self) -> None:
        # Radio response: FE FE E0 98 29 00 11 18 FD
        data = bytes.fromhex("fefee098 29 00 11 18 fd".replace(" ", ""))
        parsed = parse_civ_frame(data)
        assert parsed.command == 0x11
        assert parsed.sub is None
        assert parsed.data == b"\x18"

    def test_get_preamp_uses_cmd29(self, cmd_map) -> None:
        from rigplane.commands import get_preamp

        frame = get_preamp(cmd_map=cmd_map)
        assert frame[4] == 0x29  # Command byte is 0x29
        assert frame[5] == 0x00  # MAIN receiver
        assert frame[6] == 0x16  # Original preamp command
        assert frame[7] == 0x02  # Preamp status sub

    def test_set_preamp_uses_cmd29(self, cmd_map) -> None:
        from rigplane.commands import set_preamp

        frame = set_preamp(1, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == 0x00
        assert frame[6] == 0x16
        assert frame[7] == 0x02
        assert frame[8] == 0x01  # Level 1 in BCD

    def test_get_attenuator_uses_cmd29(self, cmd_map) -> None:
        from rigplane.commands import get_attenuator

        frame = get_attenuator(cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == 0x00
        assert frame[6] == 0x11

    def test_set_attenuator_level_uses_cmd29(self, cmd_map) -> None:
        from rigplane.commands import set_attenuator_level

        frame = set_attenuator_level(18, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == 0x00
        assert frame[6] == 0x11
        assert frame[7] == 0x18  # 18 in BCD


class TestCmd29ReceiverRouting:
    """Test that per-receiver SET commands use cmd29 when receiver=SUB.

    commands/levels.py migrated onto the bound command map in MOR-2006
    Steps 5..N (module 2): its rf_gain/af_level/squelch builders now
    require ``cmd_map``. commands/dsp.py migrated in MOR-2008 batch 3: its
    nb/nr/ip_plus/digisel builders now require ``cmd_map`` too.
    ``rigs/ic7610.toml`` declares the same wire tuples the fallback used to
    build for every one of them -- no divergence rows name IC-7610 -- so
    the expected frames below are unchanged; only the ``cmd_map=`` wiring
    is new.
    """

    @pytest.fixture()
    def cmd_map(self):
        rig = load_rig(RIG_DIR / "ic7610.toml")
        return rig.to_command_map()

    def test_set_frequency_main_no_cmd29(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_MAIN, set_freq

        frame = set_freq(14_074_000, receiver=RECEIVER_MAIN, cmd_map=cmd_map)
        assert frame[4] == 0x05  # Direct freq set, no cmd29 prefix

    def test_set_frequency_sub_uses_cmd29(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_SUB, set_freq

        frame = set_freq(14_074_000, receiver=RECEIVER_SUB, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == 0x01  # SUB receiver
        assert frame[6] == 0x05  # Freq set command

    def test_set_mode_main_no_cmd29(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_MAIN, set_mode
        from rigplane.types import Mode

        frame = set_mode(Mode.USB, receiver=RECEIVER_MAIN, cmd_map=cmd_map)
        assert frame[4] == 0x06  # Direct mode set, no cmd29 prefix

    def test_set_mode_sub_uses_cmd29(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_SUB, set_mode
        from rigplane.types import Mode

        frame = set_mode(Mode.USB, receiver=RECEIVER_SUB, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == 0x01  # SUB receiver
        assert frame[6] == 0x06  # Mode set command

    def test_set_rf_gain_main_wrapped_by_default(self, cmd_map) -> None:
        # MOR-1543: set_rf_gain's builder now defaults command29=True like
        # every other cmd29-eligible setter, instead of deriving the wrap
        # decision from receiver internally. IC-7610 declares a cmd29 route
        # for 0x14/0x02, so MAIN wraps too.
        from rigplane.commands import RECEIVER_MAIN, set_rf_gain

        frame = set_rf_gain(128, receiver=RECEIVER_MAIN, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == 0x00  # MAIN
        assert frame[6] == 0x14
        assert frame[7] == 0x02  # RF gain sub

    def test_set_rf_gain_main_unwrapped_with_override(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_MAIN, set_rf_gain

        frame = set_rf_gain(
            128, receiver=RECEIVER_MAIN, command29=False, cmd_map=cmd_map
        )
        assert frame[4] == 0x14  # Direct level cmd, no cmd29 prefix
        assert frame[5] == 0x02  # RF gain sub

    def test_set_rf_gain_sub_uses_cmd29(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_SUB, set_rf_gain

        frame = set_rf_gain(128, receiver=RECEIVER_SUB, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == 0x01  # SUB receiver
        assert frame[6] == 0x14  # Level command
        assert frame[7] == 0x02  # RF gain sub

    def test_set_af_level_main_wrapped_by_default(self, cmd_map) -> None:
        # MOR-1543: same fix as set_rf_gain — IC-7610 declares a cmd29 route
        # for 0x14/0x01, so MAIN wraps too.
        from rigplane.commands import RECEIVER_MAIN, set_af_level

        frame = set_af_level(200, receiver=RECEIVER_MAIN, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == 0x00  # MAIN
        assert frame[6] == 0x14
        assert frame[7] == 0x01  # AF level sub

    def test_set_af_level_main_unwrapped_with_override(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_MAIN, set_af_level

        frame = set_af_level(
            200, receiver=RECEIVER_MAIN, command29=False, cmd_map=cmd_map
        )
        assert frame[4] == 0x14
        assert frame[5] == 0x01  # AF level sub

    def test_set_af_level_sub_uses_cmd29(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_SUB, set_af_level

        frame = set_af_level(200, receiver=RECEIVER_SUB, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == 0x01
        assert frame[6] == 0x14
        assert frame[7] == 0x01  # AF level sub

    def test_get_rf_gain_main_wrapped_by_default(self, cmd_map) -> None:
        # MOR-1543: get_rf_gain's builder now defaults command29=True.
        from rigplane.commands import RECEIVER_MAIN, get_rf_gain

        frame = get_rf_gain(receiver=RECEIVER_MAIN, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == 0x00  # MAIN
        assert frame[6] == 0x14
        assert frame[7] == 0x02  # RF gain sub

    def test_get_rf_gain_main_unwrapped_with_override(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_MAIN, get_rf_gain

        frame = get_rf_gain(receiver=RECEIVER_MAIN, command29=False, cmd_map=cmd_map)
        assert frame[4] == 0x14  # Direct level cmd, no cmd29 prefix
        assert frame[5] == 0x02  # RF gain sub

    def test_get_rf_gain_sub_uses_cmd29(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_SUB, get_rf_gain

        frame = get_rf_gain(receiver=RECEIVER_SUB, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == 0x01  # SUB receiver
        assert frame[6] == 0x14  # Level command
        assert frame[7] == 0x02  # RF gain sub

    def test_get_rf_gain_default_is_main(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_MAIN, get_rf_gain

        assert get_rf_gain(cmd_map=cmd_map) == get_rf_gain(
            receiver=RECEIVER_MAIN, cmd_map=cmd_map
        )

    def test_get_af_level_main_wrapped_by_default(self, cmd_map) -> None:
        # MOR-1543: get_af_level's builder now defaults command29=True.
        from rigplane.commands import RECEIVER_MAIN, get_af_level

        frame = get_af_level(receiver=RECEIVER_MAIN, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == 0x00  # MAIN
        assert frame[6] == 0x14
        assert frame[7] == 0x01  # AF level sub

    def test_get_af_level_main_unwrapped_with_override(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_MAIN, get_af_level

        frame = get_af_level(receiver=RECEIVER_MAIN, command29=False, cmd_map=cmd_map)
        assert frame[4] == 0x14
        assert frame[5] == 0x01  # AF level sub

    def test_get_af_level_sub_uses_cmd29(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_SUB, get_af_level

        frame = get_af_level(receiver=RECEIVER_SUB, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == 0x01  # SUB receiver
        assert frame[6] == 0x14
        assert frame[7] == 0x01  # AF level sub

    def test_get_af_level_default_is_main(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_MAIN, get_af_level

        assert get_af_level(cmd_map=cmd_map) == get_af_level(
            receiver=RECEIVER_MAIN, cmd_map=cmd_map
        )

    def test_set_squelch_main_wrapped_by_default(self, cmd_map) -> None:
        # MOR-1543: set_squelch's builder now defaults command29=True.
        from rigplane.commands import RECEIVER_MAIN, set_squelch

        frame = set_squelch(100, receiver=RECEIVER_MAIN, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == 0x00  # MAIN
        assert frame[6] == 0x14
        assert frame[7] == 0x03  # SQL sub

    def test_set_squelch_main_unwrapped_with_override(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_MAIN, set_squelch

        frame = set_squelch(
            100, receiver=RECEIVER_MAIN, command29=False, cmd_map=cmd_map
        )
        assert frame[4] == 0x14
        assert frame[5] == 0x03  # SQL sub

    def test_set_squelch_sub_uses_cmd29(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_SUB, set_squelch

        frame = set_squelch(100, receiver=RECEIVER_SUB, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == 0x01
        assert frame[6] == 0x14
        assert frame[7] == 0x03  # SQL sub

    def test_set_nb_main_wrapped_by_default(self, cmd_map) -> None:
        # MOR-1537 follow-up: set_nb's builder now defaults command29=True
        # like every other cmd29-eligible setter, instead of deriving the
        # wrap decision from receiver internally.
        from rigplane.commands import RECEIVER_MAIN, set_nb

        frame = set_nb(True, receiver=RECEIVER_MAIN, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == 0x00  # MAIN
        assert frame[6] == 0x16
        assert frame[7] == 0x22  # NB sub

    def test_set_nb_main_unwrapped_with_override(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_MAIN, set_nb

        frame = set_nb(True, receiver=RECEIVER_MAIN, command29=False, cmd_map=cmd_map)
        assert frame[4] == 0x16  # Direct cmd, no cmd29
        assert frame[5] == 0x22  # NB sub

    def test_set_nb_sub_uses_cmd29(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_SUB, set_nb

        frame = set_nb(True, receiver=RECEIVER_SUB, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == 0x01
        assert frame[6] == 0x16
        assert frame[7] == 0x22  # NB sub
        assert frame[8] == 0x01  # on=True

    def test_set_nr_main_wrapped_by_default(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_MAIN, set_nr

        frame = set_nr(False, receiver=RECEIVER_MAIN, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == 0x00  # MAIN
        assert frame[6] == 0x16
        assert frame[7] == 0x40  # NR sub

    def test_set_nr_main_unwrapped_with_override(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_MAIN, set_nr

        frame = set_nr(False, receiver=RECEIVER_MAIN, command29=False, cmd_map=cmd_map)
        assert frame[4] == 0x16
        assert frame[5] == 0x40  # NR sub

    def test_set_nr_sub_uses_cmd29(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_SUB, set_nr

        frame = set_nr(False, receiver=RECEIVER_SUB, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == 0x01
        assert frame[6] == 0x16
        assert frame[7] == 0x40  # NR sub
        assert frame[8] == 0x00  # on=False

    def test_set_ip_plus_main_wrapped_by_default(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_MAIN, set_ip_plus

        frame = set_ip_plus(True, receiver=RECEIVER_MAIN, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == 0x00  # MAIN
        assert frame[6] == 0x16
        assert frame[7] == 0x65  # IP+ sub

    def test_set_ip_plus_main_unwrapped_with_override(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_MAIN, set_ip_plus

        frame = set_ip_plus(
            True, receiver=RECEIVER_MAIN, command29=False, cmd_map=cmd_map
        )
        assert frame[4] == 0x16
        assert frame[5] == 0x65  # IP+ sub

    def test_set_ip_plus_sub_uses_cmd29(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_SUB, set_ip_plus

        frame = set_ip_plus(True, receiver=RECEIVER_SUB, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == 0x01
        assert frame[6] == 0x16
        assert frame[7] == 0x65  # IP+ sub
        assert frame[8] == 0x01  # on=True

    def test_set_digisel_sub_uses_cmd29(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_SUB, set_digisel

        frame = set_digisel(True, receiver=RECEIVER_SUB, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == 0x01
        assert frame[6] == 0x16
        assert frame[7] == 0x4E  # DIGI-SEL sub

    def test_backward_compat_no_receiver_arg(self, cmd_map) -> None:
        """All functions remain backward-compatible (no receiver arg = MAIN)."""
        from rigplane.commands import (
            set_af_level,
            set_frequency,
            set_ip_plus,
            set_mode,
            set_nb,
            set_nr,
            set_rf_gain,
            set_squelch,
        )
        from rigplane.types import Mode

        # freq/mode never use cmd29 (hard exclusion, IC-7610 CI-V quirk).
        assert set_frequency(14_000_000, cmd_map=cmd_map)[4] == 0x05
        assert set_mode(Mode.USB, cmd_map=cmd_map)[4] == 0x06
        # MOR-1537 follow-up: nb/nr/ip_plus builders now default
        # command29=True; no receiver arg still targets MAIN (0x00).
        assert set_nb(True, cmd_map=cmd_map)[4:6] == b"\x29\x00"
        assert set_nr(True, cmd_map=cmd_map)[4:6] == b"\x29\x00"
        assert set_ip_plus(True, cmd_map=cmd_map)[4:6] == b"\x29\x00"
        # MOR-1543: rf_gain/af_level/squelch builders now default
        # command29=True; no receiver arg still targets MAIN (0x00).
        assert set_rf_gain(128, cmd_map=cmd_map)[4:6] == b"\x29\x00"
        assert set_af_level(200, cmd_map=cmd_map)[4:6] == b"\x29\x00"
        assert set_squelch(50, cmd_map=cmd_map)[4:6] == b"\x29\x00"


class TestDspLevelParityCommands:
    """Test IC-7610 DSP/level parity command builders and parsers.

    commands/levels.py migrated onto the bound command map in MOR-2006
    Steps 5..N (module 2): every builder here now requires ``cmd_map``, and
    ``rigs/ic7610.toml`` declares the same wire tuples the fallback used to
    build for every command this class exercises -- no divergence rows name
    IC-7610 (`tests/command_map_parity_divergences.txt`) -- so the expected
    frames below are unchanged; only the ``cmd_map=`` wiring is new.
    """

    @pytest.fixture()
    def cmd_map(self):
        rig = load_rig(RIG_DIR / "ic7610.toml")
        return rig.to_command_map()

    @pytest.mark.parametrize(
        ("getter_name", "setter_name", "sub", "receiver"),
        [
            ("get_apf_type_level", "set_apf_type_level", 0x05, 1),
            ("get_nr_level", "set_nr_level", 0x06, 1),
            ("get_pbt_inner", "set_pbt_inner", 0x07, 1),
            ("get_pbt_outer", "set_pbt_outer", 0x08, 1),
            ("get_notch_filter", "set_notch_filter", 0x0D, 1),
            ("get_nb_level", "set_nb_level", 0x12, 1),
            ("get_digisel_shift", "set_digisel_shift", 0x13, 1),
        ],
    )
    def test_cmd29_level_builders(
        self,
        cmd_map,
        getter_name: str,
        setter_name: str,
        sub: int,
        receiver: int,
    ) -> None:
        import rigplane.commands as commands

        getter = getattr(commands, getter_name)
        setter = getattr(commands, setter_name)
        expected = bytes([0xFE, 0xFE, 0x98, 0xE0, 0x29, receiver, 0x14, sub])

        assert getter(receiver=receiver, cmd_map=cmd_map) == expected + b"\xfd"
        assert (
            setter(128, receiver=receiver, cmd_map=cmd_map)
            == expected + b"\x01\x28\xfd"
        )

    @pytest.mark.parametrize(
        ("getter_name", "setter_name", "sub", "value"),
        [
            ("get_cw_pitch", "set_cw_pitch", 0x09, 600),
            ("get_mic_gain", "set_mic_gain", 0x0B, 128),
            ("get_key_speed", "set_key_speed", 0x0C, 30),
            ("get_compressor_level", "set_compressor_level", 0x0E, 128),
            ("get_break_in_delay", "set_break_in_delay", 0x0F, 128),
            ("get_drive_gain", "set_drive_gain", 0x14, 128),
            ("get_monitor_gain", "set_monitor_gain", 0x15, 128),
            ("get_vox_gain", "set_vox_gain", 0x16, 128),
            ("get_anti_vox_gain", "set_anti_vox_gain", 0x17, 128),
        ],
    )
    def test_level_builders(
        self,
        cmd_map,
        getter_name: str,
        setter_name: str,
        sub: int,
        value: int,
    ) -> None:
        import rigplane.commands as commands

        getter = getattr(commands, getter_name)
        setter = getattr(commands, setter_name)

        assert getter(cmd_map=cmd_map) == bytes(
            [0xFE, 0xFE, 0x98, 0xE0, 0x14, sub, 0xFD]
        )
        assert setter(value, cmd_map=cmd_map).startswith(
            bytes([0xFE, 0xFE, 0x98, 0xE0, 0x14, sub])
        )
        assert setter(value, cmd_map=cmd_map).endswith(b"\xfd")

    @pytest.mark.parametrize(
        ("getter_name", "setter_name", "prefix", "value", "expected_payload"),
        [
            ("get_ref_adjust", "set_ref_adjust", b"\x00\x70", 511, b"\x05\x11"),
            ("get_dash_ratio", "set_dash_ratio", b"\x02\x28", 45, b"\x45"),
            ("get_nb_depth", "set_nb_depth", b"\x02\x90", 9, b"\x09"),
            ("get_nb_width", "set_nb_width", b"\x02\x91", 255, b"\x02\x55"),
        ],
    )
    def test_ctl_mem_level_builders(
        self,
        cmd_map,
        getter_name: str,
        setter_name: str,
        prefix: bytes,
        value: int,
        expected_payload: bytes,
    ) -> None:
        import rigplane.commands as commands

        getter = getattr(commands, getter_name)
        setter = getattr(commands, setter_name)

        assert getter(cmd_map=cmd_map) == b"\xfe\xfe\x98\xe0\x1a\x05" + prefix + b"\xfd"
        assert (
            setter(value, cmd_map=cmd_map)
            == b"\xfe\xfe\x98\xe0\x1a\x05" + prefix + expected_payload + b"\xfd"
        )

    def test_af_mute_builders(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_SUB, get_af_mute, set_af_mute

        assert get_af_mute(cmd_map=cmd_map) == b"\xfe\xfe\x98\xe0\x29\x00\x1a\x09\xfd"
        assert (
            get_af_mute(receiver=RECEIVER_SUB, cmd_map=cmd_map)
            == b"\xfe\xfe\x98\xe0\x29\x01\x1a\x09\xfd"
        )
        assert (
            set_af_mute(True, cmd_map=cmd_map)
            == b"\xfe\xfe\x98\xe0\x29\x00\x1a\x09\x01\xfd"
        )
        assert (
            set_af_mute(False, receiver=RECEIVER_SUB, cmd_map=cmd_map)
            == b"\xfe\xfe\x98\xe0\x29\x01\x1a\x09\x00\xfd"
        )

    def test_parse_level_response_direct_level(self) -> None:
        from rigplane.commands import parse_level_response

        frame = CivFrame(
            to_addr=0xE0,
            from_addr=0x98,
            command=0x14,
            sub=0x13,
            data=b"\x01\x99",
        )
        assert parse_level_response(frame, sub=0x13) == 199

    def test_parse_level_response_with_ctl_mem_prefix(self) -> None:
        from rigplane.commands import parse_level_response

        frame = CivFrame(
            to_addr=0xE0,
            from_addr=0x98,
            command=0x1A,
            sub=0x05,
            data=b"\x00\x70\x05\x11",
        )
        assert (
            parse_level_response(frame, command=0x1A, sub=0x05, prefix=b"\x00\x70")
            == 511
        )

    def test_parse_bool_response(self) -> None:
        from rigplane.commands import parse_bool_response

        frame = CivFrame(
            to_addr=0xE0,
            from_addr=0x98,
            command=0x1A,
            sub=0x09,
            data=b"\x01",
        )
        assert parse_bool_response(frame, command=0x1A, sub=0x09) is True

    def test_parse_level_response_rejects_wrong_prefix(self) -> None:
        from rigplane.commands import parse_level_response

        frame = CivFrame(
            to_addr=0xE0,
            from_addr=0x98,
            command=0x1A,
            sub=0x05,
            data=b"\x02\x90\x00",
        )
        with pytest.raises(ValueError, match="prefix"):
            parse_level_response(frame, command=0x1A, sub=0x05, prefix=b"\x00\x70")

    def test_set_nb_width_rejects_out_of_range(self, cmd_map) -> None:
        from rigplane.commands import set_nb_width

        with pytest.raises(ValueError, match="0-255"):
            set_nb_width(256, cmd_map=cmd_map)

    def test_set_manual_notch_width_wire_bounds_match_siblings(self, cmd_map) -> None:
        """MOR-1542: set_manual_notch_width hardcoded minimum=0/maximum=2 —
        the currently-known WIDE/MID/NAR domain — instead of the wire-format
        single-BCD-byte range (0-99) its three siblings (set_break_in,
        set_filter_shape, set_ssb_tx_bandwidth) leave to the profile-aware
        caller (MOR-1534). Domain legality stays enforced at the CoreRadio
        seat (see TestManualNotchWidthDomainValidation in test_radio.py)."""
        from rigplane.commands import set_manual_notch_width

        assert set_manual_notch_width(3, cmd_map=cmd_map).endswith(b"\x03\xfd")
        with pytest.raises(ValueError, match="0-99"):
            set_manual_notch_width(100, cmd_map=cmd_map)


class TestOperatorToggleParityCommands:
    """Test IC-7610 operator toggle/status parity command builders.

    MOR-2008 batch 2 migrated three of the getters/setters this class
    parametrizes over (get_s_meter_sql_status/get_filter_shape/
    get_agc_time_constant/set_filter_shape/set_agc_time_constant/
    get_overflow_status/get_ssb_tx_bandwidth/set_ssb_tx_bandwidth) onto
    the bound command map; the rest are dsp.py/config.py builders still
    unmigrated at this head. ``cmd_map`` is passed to every parametrized
    call below rather than splitting the table: IC-7610's own map declares
    every name in it byte-identically to its fallback (zero divergence,
    confirmed by ``tests/command_map_parity_divergences.txt`` being empty),
    so passing it to an unmigrated builder here is a no-op on the frame it
    builds, not a behaviour change.
    """

    @pytest.fixture()
    def cmd_map(self):
        rig = load_rig(RIG_DIR / "ic7610.toml")
        return rig.to_command_map()

    @pytest.mark.parametrize(
        ("getter_name", "sub", "receiver"),
        [
            ("get_s_meter_sql_status", 0x01, 1),
            ("get_audio_peak_filter", 0x32, 1),
            ("get_auto_notch", 0x41, 1),
            ("get_manual_notch", 0x48, 1),
            ("get_twin_peak_filter", 0x4F, 1),
            ("get_filter_shape", 0x56, 1),
            ("get_agc_time_constant", 0x04, 1),
            # MOR-1537: get_agc's builder now defaults command29=True like
            # every other cmd29-eligible getter here, instead of deriving
            # the wrap decision from receiver internally.
            ("get_agc", 0x12, 1),
        ],
    )
    def test_cmd29_operator_getters(
        self,
        getter_name: str,
        sub: int,
        receiver: int,
        cmd_map,
    ) -> None:
        import rigplane.commands as commands

        getter = getattr(commands, getter_name)
        command = 0x15 if sub == 0x01 else 0x1A if sub == 0x04 else 0x16

        assert getter(receiver=receiver, cmd_map=cmd_map) == bytes(
            [0xFE, 0xFE, 0x98, 0xE0, 0x29, receiver, command, sub, 0xFD]
        )

    @pytest.mark.parametrize(
        ("setter_name", "value", "expected_tail"),
        [
            ("set_audio_peak_filter", AudioPeakFilter.MID, b"\x29\x01\x16\x32\x02\xfd"),
            ("set_auto_notch", True, b"\x29\x01\x16\x41\x01\xfd"),
            ("set_manual_notch", False, b"\x29\x01\x16\x48\x00\xfd"),
            ("set_twin_peak_filter", True, b"\x29\x01\x16\x4f\x01\xfd"),
            ("set_filter_shape", FilterShape.SOFT, b"\x29\x01\x16\x56\x01\xfd"),
            ("set_agc_time_constant", 13, b"\x29\x01\x1a\x04\x13\xfd"),
            # MOR-1537: set_agc's builder now defaults command29=True like
            # every other cmd29-eligible setter here.
            ("set_agc", AgcMode.SLOW, b"\x29\x01\x16\x12\x03\xfd"),
        ],
    )
    def test_cmd29_operator_setters(
        self,
        setter_name: str,
        value: object,
        expected_tail: bytes,
        cmd_map,
    ) -> None:
        import rigplane.commands as commands

        setter = getattr(commands, setter_name)
        assert setter(value, receiver=1, cmd_map=cmd_map).endswith(expected_tail)

    @pytest.mark.parametrize(
        ("getter_name", "sub"),
        [
            ("get_overflow_status", 0x07),
            ("get_compressor", 0x44),
            ("get_monitor", 0x45),
            ("get_vox", 0x46),
            ("get_break_in", 0x47),
            ("get_dial_lock", 0x50),
            ("get_ssb_tx_bandwidth", 0x58),
        ],
    )
    def test_direct_operator_getters(self, getter_name: str, sub: int, cmd_map) -> None:
        import rigplane.commands as commands

        getter = getattr(commands, getter_name)
        command = 0x15 if sub == 0x07 else 0x16
        assert getter(cmd_map=cmd_map) == bytes(
            [0xFE, 0xFE, 0x98, 0xE0, command, sub, 0xFD]
        )

    @pytest.mark.parametrize(
        ("setter_name", "value", "expected_tail"),
        [
            ("set_compressor", True, b"\x16\x44\x01\xfd"),
            ("set_monitor", False, b"\x16\x45\x00\xfd"),
            ("set_vox", True, b"\x16\x46\x01\xfd"),
            ("set_break_in", BreakInMode.FULL, b"\x16\x47\x02\xfd"),
            ("set_dial_lock", True, b"\x16\x50\x01\xfd"),
            ("set_ssb_tx_bandwidth", SsbTxBandwidth.NAR, b"\x16\x58\x02\xfd"),
        ],
    )
    def test_direct_operator_setters(
        self,
        setter_name: str,
        value: object,
        expected_tail: bytes,
        cmd_map,
    ) -> None:
        import rigplane.commands as commands

        setter = getattr(commands, setter_name)
        assert setter(value, cmd_map=cmd_map).endswith(expected_tail)

    @pytest.mark.parametrize(
        ("frame", "kwargs", "expected"),
        [
            (
                CivFrame(0xE0, 0x98, 0x16, 0x12, b"\x03"),
                {"command": 0x16, "sub": 0x12, "bcd_bytes": 1},
                3,
            ),
            (
                CivFrame(0xE0, 0x98, 0x1A, 0x04, b"\x13"),
                {"command": 0x1A, "sub": 0x04, "bcd_bytes": 1},
                13,
            ),
        ],
    )
    def test_parse_single_byte_bcd_operator_values(
        self,
        frame: CivFrame,
        kwargs: dict[str, int],
        expected: int,
    ) -> None:
        from rigplane.commands import parse_level_response

        assert parse_level_response(frame, **kwargs) == expected

    @pytest.mark.parametrize(
        ("frame", "kwargs"),
        [
            (CivFrame(0xE0, 0x98, 0x15, 0x01, b"\x01"), {"command": 0x15, "sub": 0x01}),
            (CivFrame(0xE0, 0x98, 0x15, 0x07, b"\x01"), {"command": 0x15, "sub": 0x07}),
            (CivFrame(0xE0, 0x98, 0x16, 0x41, b"\x01"), {"command": 0x16, "sub": 0x41}),
            (CivFrame(0xE0, 0x98, 0x16, 0x44, b"\x01"), {"command": 0x16, "sub": 0x44}),
        ],
    )
    def test_parse_operator_bool_response(
        self,
        frame: CivFrame,
        kwargs: dict[str, int],
    ) -> None:
        from rigplane.commands import parse_bool_response

        assert parse_bool_response(frame, **kwargs) is True


# --- Transceiver status family (#136) ---


class TestTransceiverStatusBuilders:
    """Test CI-V builders for transceiver_status commands.

    Migrated onto the bound command map in MOR-2008
    (`docs/plans/2026-08-29-profile-driven-command-bytes.md` §4 Steps 5..N):
    batch 1 for the system.py builders here -- band edge, tuner/XFC
    status, RIT/XIT; batch 2 for the meters.py builders -- various-squelch
    and the power/comp/Vd/Id meters. Every builder here now requires
    ``cmd_map``.
    """

    @pytest.fixture()
    def cmd_map(self):
        rig = load_rig(RIG_DIR / "ic7610.toml")
        return rig.to_command_map()

    def test_get_band_edge_freq(self, cmd_map) -> None:
        from rigplane.commands import get_band_edge_freq

        frame = get_band_edge_freq(cmd_map=cmd_map)
        assert b"\xfe\xfe\x98\xe0\x02\xfd" == frame

    def test_get_band_edge_freq_requires_cmd_map(self) -> None:
        """cmd_map is required keyword-only -- MOR-2006 Q6's API break."""
        from rigplane.commands import get_band_edge_freq

        with pytest.raises(TypeError, match="MOR-2006"):
            get_band_edge_freq()  # type: ignore[call-arg]

    def test_get_various_squelch_main(self, cmd_map) -> None:
        from rigplane.commands import get_various_squelch

        frame = get_various_squelch(receiver=0x00, cmd_map=cmd_map)
        # Command29 frame: FE FE to from 29 00 15 05 FD
        assert frame[4] == 0x29  # cmd29 prefix
        assert b"\x15\x05" in frame

    def test_get_various_squelch_sub(self, cmd_map) -> None:
        from rigplane.commands import get_various_squelch

        frame = get_various_squelch(receiver=0x01, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == 0x01  # SUB receiver

    def test_get_power_meter(self, cmd_map) -> None:
        from rigplane.commands import get_power_meter

        frame = get_power_meter(cmd_map=cmd_map)
        assert b"\xfe\xfe\x98\xe0\x15\x11\xfd" == frame

    def test_get_comp_meter(self, cmd_map) -> None:
        from rigplane.commands import get_comp_meter

        frame = get_comp_meter(cmd_map=cmd_map)
        assert b"\xfe\xfe\x98\xe0\x15\x14\xfd" == frame

    def test_get_vd_meter(self, cmd_map) -> None:
        from rigplane.commands import get_vd_meter

        frame = get_vd_meter(cmd_map=cmd_map)
        assert b"\xfe\xfe\x98\xe0\x15\x15\xfd" == frame

    def test_get_id_meter(self, cmd_map) -> None:
        from rigplane.commands import get_id_meter

        frame = get_id_meter(cmd_map=cmd_map)
        assert b"\xfe\xfe\x98\xe0\x15\x16\xfd" == frame

    def test_get_tuner_status(self, cmd_map) -> None:
        from rigplane.commands import get_tuner_status

        frame = get_tuner_status(cmd_map=cmd_map)
        assert b"\xfe\xfe\x98\xe0\x1c\x01\xfd" == frame

    def test_set_tuner_status_on(self, cmd_map) -> None:
        from rigplane.commands import set_tuner_status

        frame = set_tuner_status(1, cmd_map=cmd_map)
        assert b"\x1c\x01\x01" in frame

    def test_set_tuner_status_tune(self, cmd_map) -> None:
        from rigplane.commands import set_tuner_status

        frame = set_tuner_status(2, cmd_map=cmd_map)
        assert b"\x1c\x01\x02" in frame

    def test_set_tuner_status_off(self, cmd_map) -> None:
        from rigplane.commands import set_tuner_status

        frame = set_tuner_status(0, cmd_map=cmd_map)
        assert b"\x1c\x01\x00" in frame

    def test_set_tuner_status_invalid(self, cmd_map) -> None:
        from rigplane.commands import set_tuner_status

        with pytest.raises(ValueError, match="0, 1, or 2"):
            set_tuner_status(3, cmd_map=cmd_map)

    def test_set_tuner_status_rejects_explicit_none_the_same_way(self) -> None:
        """An explicit ``cmd_map=None`` must hit the same Q6 explanation as
        omitting it entirely."""
        from rigplane.commands import set_tuner_status

        with pytest.raises(TypeError, match="MOR-2006"):
            set_tuner_status(1, cmd_map=None)

    def test_get_rit_frequency(self, cmd_map) -> None:
        from rigplane.commands import get_rit_frequency

        frame = get_rit_frequency(cmd_map=cmd_map)
        assert b"\xfe\xfe\x98\xe0\x21\x00\xfd" == frame

    def test_set_rit_frequency_positive(self, cmd_map) -> None:
        from rigplane.commands import set_rit_frequency

        frame = set_rit_frequency(150, cmd_map=cmd_map)
        # 150 Hz → BCD: d0=0x50 (50), d1=0x01 (01), sign=0x00 (positive)
        assert b"\x21\x00\x50\x01\x00" in frame

    def test_set_rit_frequency_negative(self, cmd_map) -> None:
        from rigplane.commands import set_rit_frequency

        frame = set_rit_frequency(-200, cmd_map=cmd_map)
        # 200 Hz → BCD: d0=0x00, d1=0x02, sign=0x01 (negative)
        assert b"\x21\x00\x00\x02\x01" in frame

    def test_set_rit_frequency_zero(self, cmd_map) -> None:
        from rigplane.commands import set_rit_frequency

        frame = set_rit_frequency(0, cmd_map=cmd_map)
        assert b"\x21\x00\x00\x00\x00" in frame

    def test_set_rit_frequency_out_of_range(self, cmd_map) -> None:
        from rigplane.commands import set_rit_frequency

        with pytest.raises(ValueError, match="±9999"):
            set_rit_frequency(10000, cmd_map=cmd_map)
        with pytest.raises(ValueError, match="±9999"):
            set_rit_frequency(-10000, cmd_map=cmd_map)

    def test_get_rit_status(self, cmd_map) -> None:
        from rigplane.commands import get_rit_status

        frame = get_rit_status(cmd_map=cmd_map)
        assert b"\xfe\xfe\x98\xe0\x21\x01\xfd" == frame

    def test_set_rit_status_on(self, cmd_map) -> None:
        from rigplane.commands import set_rit_status

        frame = set_rit_status(True, cmd_map=cmd_map)
        assert b"\x21\x01\x01" in frame

    def test_set_rit_status_off(self, cmd_map) -> None:
        from rigplane.commands import set_rit_status

        frame = set_rit_status(False, cmd_map=cmd_map)
        assert b"\x21\x01\x00" in frame

    def test_get_rit_tx_status(self, cmd_map) -> None:
        from rigplane.commands import get_rit_tx_status

        frame = get_rit_tx_status(cmd_map=cmd_map)
        assert b"\xfe\xfe\x98\xe0\x21\x02\xfd" == frame

    def test_set_rit_tx_status_on(self, cmd_map) -> None:
        from rigplane.commands import set_rit_tx_status

        frame = set_rit_tx_status(True, cmd_map=cmd_map)
        assert b"\x21\x02\x01" in frame


class TestRitFrequencyParser:
    """Test parse_rit_frequency_response."""

    def test_positive_150hz(self) -> None:
        from rigplane.commands import parse_rit_frequency_response

        # 150 Hz positive: d0=0x50, d1=0x01, sign=0x00
        assert parse_rit_frequency_response(b"\x50\x01\x00") == 150

    def test_negative_200hz(self) -> None:
        from rigplane.commands import parse_rit_frequency_response

        assert parse_rit_frequency_response(b"\x00\x02\x01") == -200

    def test_zero(self) -> None:
        from rigplane.commands import parse_rit_frequency_response

        assert parse_rit_frequency_response(b"\x00\x00\x00") == 0

    def test_max_positive(self) -> None:
        from rigplane.commands import parse_rit_frequency_response

        # 9999 Hz: d0=0x99, d1=0x99, sign=0x00
        assert parse_rit_frequency_response(b"\x99\x99\x00") == 9999

    def test_max_negative(self) -> None:
        from rigplane.commands import parse_rit_frequency_response

        assert parse_rit_frequency_response(b"\x99\x99\x01") == -9999

    def test_short_data_returns_zero(self) -> None:
        from rigplane.commands import parse_rit_frequency_response

        assert parse_rit_frequency_response(b"\x50\x01") == 0
        assert parse_rit_frequency_response(b"") == 0


class TestAdvancedScopeParsers:
    """Test response parsing for advanced_scope commands."""

    def test_parse_scope_mode_response_with_receiver_prefix(self) -> None:
        from rigplane.commands import parse_scope_mode_response

        frame = CivFrame(0xE0, 0x98, 0x27, 0x14, b"\x01\x03")
        receiver, mode = parse_scope_mode_response(frame)
        assert receiver == 1
        assert mode == 3

    def test_parse_scope_span_response_from_bcd_frequency(self) -> None:
        from rigplane.commands import parse_scope_span_response
        from rigplane.types import bcd_encode

        frame = CivFrame(0xE0, 0x98, 0x27, 0x15, b"\x00" + bcd_encode(250_000))
        receiver, span = parse_scope_span_response(frame, _SPAN_PRESETS_HZ)
        assert receiver == 0
        assert span == 6

    def test_parse_scope_ref_response(self) -> None:
        from rigplane.commands import parse_scope_ref_response

        # -10.5 dB: 10dB=1, 1dB=0, 0.1dB=5 → byte0=0x10, byte1=0x50, sign=0x01
        frame = CivFrame(0xE0, 0x98, 0x27, 0x19, b"\x00\x10\x50\x01")
        receiver, ref_db = parse_scope_ref_response(frame)
        assert receiver == 0
        assert ref_db == -10.5

    def test_parse_scope_during_tx_response(self) -> None:
        from rigplane.commands import parse_scope_during_tx_response

        frame = CivFrame(0xE0, 0x98, 0x27, 0x1B, b"\x01")
        assert parse_scope_during_tx_response(frame) is True

    def test_parse_scope_center_type_response(self) -> None:
        from rigplane.commands import parse_scope_center_type_response

        frame = CivFrame(0xE0, 0x98, 0x27, 0x1C, b"\x00\x02")
        receiver, center_type = parse_scope_center_type_response(frame)
        assert receiver == 0
        assert center_type == 2

    def test_parse_scope_fixed_edge_response(self) -> None:
        from rigplane.commands import parse_scope_fixed_edge_response
        from rigplane.types import bcd_encode

        frame = CivFrame(
            0xE0,
            0x98,
            0x27,
            0x1E,
            b"\x06\x04" + bcd_encode(14_000_000) + bcd_encode(14_350_000),
        )
        bounds = parse_scope_fixed_edge_response(frame)
        assert bounds.range_index == 6
        assert bounds.edge == 4
        assert bounds.start_hz == 14_000_000
        assert bounds.end_hz == 14_350_000

    def test_parse_scope_fixed_edge_response_captured_ic7610(self) -> None:
        # Raw CI-V captured on the live IC-7610 (MOR-662):
        #   RX fe fe e0 98 27 1e 01 01 00 00 50 00 00 00 00 50 01 00 fd
        # Payload after 27 1e: range=01 edge=01 start(5B BCD) end(5B BCD).
        from rigplane.commands import parse_scope_fixed_edge_response

        frame = CivFrame(
            0xE0,
            0x98,
            0x27,
            0x1E,
            bytes.fromhex("0101" + "0000500000" + "0000500100"),
        )
        bounds = parse_scope_fixed_edge_response(frame)
        assert bounds.range_index == 1
        assert bounds.edge == 1
        assert bounds.start_hz == 500_000
        assert bounds.end_hz == 1_500_000

    def test_parse_scope_span_response_from_single_byte_index(self) -> None:
        from rigplane.commands import parse_scope_span_response

        frame = CivFrame(0xE0, 0x98, 0x27, 0x15, b"\x05")
        receiver, span = parse_scope_span_response(frame, _SPAN_PRESETS_HZ)
        assert receiver is None
        assert span == 5

    def test_parse_scope_rbw_response(self) -> None:
        from rigplane.commands import parse_scope_rbw_response

        frame = CivFrame(0xE0, 0x98, 0x27, 0x1F, b"\x01\x02")
        receiver, rbw = parse_scope_rbw_response(frame)
        assert receiver == 1
        assert rbw == 2

    def test_parse_scope_speed_response_with_receiver(self) -> None:
        from rigplane.commands import parse_scope_speed_response

        # sub=0x1A, receiver=0, speed=1
        frame = CivFrame(0xE0, 0x98, 0x27, 0x1A, b"\x00\x01")
        receiver, speed = parse_scope_speed_response(frame)
        assert receiver == 0
        assert speed == 1

    def test_parse_scope_speed_response_sub_receiver(self) -> None:
        from rigplane.commands import parse_scope_speed_response

        # sub=0x1A, receiver=1 (sub), speed=2
        frame = CivFrame(0xE0, 0x98, 0x27, 0x1A, b"\x01\x02")
        receiver, speed = parse_scope_speed_response(frame)
        assert receiver == 1
        assert speed == 2

    def test_parse_scope_speed_response_no_receiver(self) -> None:
        from rigplane.commands import parse_scope_speed_response

        # sub=0x1A, no receiver prefix, speed=0
        frame = CivFrame(0xE0, 0x98, 0x27, 0x1A, b"\x00")
        receiver, speed = parse_scope_speed_response(frame)
        assert receiver is None
        assert speed == 0

    def test_parse_scope_hold_response_true(self) -> None:
        from rigplane.commands import parse_scope_hold_response

        # sub=0x17, receiver=0, hold=True (0x01)
        frame = CivFrame(0xE0, 0x98, 0x27, 0x17, b"\x00\x01")
        receiver, hold = parse_scope_hold_response(frame)
        assert receiver == 0
        assert hold is True

    def test_parse_scope_hold_response_false(self) -> None:
        from rigplane.commands import parse_scope_hold_response

        # sub=0x17, receiver=1, hold=False (0x00)
        frame = CivFrame(0xE0, 0x98, 0x27, 0x17, b"\x01\x00")
        receiver, hold = parse_scope_hold_response(frame)
        assert receiver == 1
        assert hold is False

    def test_parse_scope_hold_response_no_receiver(self) -> None:
        from rigplane.commands import parse_scope_hold_response

        # sub=0x17, no receiver prefix, hold=True
        frame = CivFrame(0xE0, 0x98, 0x27, 0x17, b"\x01")
        receiver, hold = parse_scope_hold_response(frame)
        assert receiver is None
        assert hold is True

    def test_parse_scope_vbw_response_true(self) -> None:
        from rigplane.commands import parse_scope_vbw_response

        # sub=0x1D, receiver=0, vbw=True (0x01)
        frame = CivFrame(0xE0, 0x98, 0x27, 0x1D, b"\x00\x01")
        receiver, vbw = parse_scope_vbw_response(frame)
        assert receiver == 0
        assert vbw is True

    def test_parse_scope_vbw_response_false(self) -> None:
        from rigplane.commands import parse_scope_vbw_response

        # sub=0x1D, receiver=1, vbw=False (0x00)
        frame = CivFrame(0xE0, 0x98, 0x27, 0x1D, b"\x01\x00")
        receiver, vbw = parse_scope_vbw_response(frame)
        assert receiver == 1
        assert vbw is False

    def test_parse_scope_vbw_response_no_receiver(self) -> None:
        from rigplane.commands import parse_scope_vbw_response

        # sub=0x1D, no receiver prefix, vbw=False
        frame = CivFrame(0xE0, 0x98, 0x27, 0x1D, b"\x00")
        receiver, vbw = parse_scope_vbw_response(frame)
        assert receiver is None
        assert vbw is False


class TestAdvancedScopeValidation:
    """Negative tests for scope builder input validation.

    commands/scope.py migrated onto the bound command map in MOR-2007
    Steps 5..N (module 5): every builder now requires cmd_map. Passed
    explicitly below so each call reaches its own ``_validate_scope_*``
    check (raising the intended ValueError) rather than stopping at the
    Q6 "missing cmd_map" TypeError first -- a call omitting cmd_map can no
    longer reach its own validation logic at all.
    """

    @pytest.fixture()
    def cmd_map(self):
        rig = load_rig(RIG_DIR / "ic7610.toml")
        return rig.to_command_map()

    def test_scope_set_mode_rejects_out_of_range(self, cmd_map) -> None:
        from rigplane.commands import scope_set_mode

        with pytest.raises(ValueError, match="scope mode must be 0-3"):
            scope_set_mode(5, cmd_map=cmd_map)

    def test_scope_set_span_rejects_negative(self, cmd_map) -> None:
        from rigplane.commands import scope_set_span

        with pytest.raises(ValueError, match="scope span must be 0-7"):
            scope_set_span(-1, cmd_map=cmd_map, presets=_SPAN_PRESETS_HZ)

    def test_scope_set_edge_rejects_zero(self, cmd_map) -> None:
        from rigplane.commands import scope_set_edge

        with pytest.raises(ValueError, match="scope edge must be 1-4"):
            scope_set_edge(0, cmd_map=cmd_map)

    def test_scope_set_speed_rejects_out_of_range(self, cmd_map) -> None:
        from rigplane.commands import scope_set_speed

        with pytest.raises(ValueError, match="scope speed must be 0-2"):
            scope_set_speed(3, cmd_map=cmd_map)

    def test_scope_set_center_type_rejects_out_of_range(self, cmd_map) -> None:
        from rigplane.commands import scope_set_center_type

        with pytest.raises(ValueError, match="scope center type must be 0-2"):
            scope_set_center_type(5, cmd_map=cmd_map)

    def test_scope_set_rbw_rejects_out_of_range(self, cmd_map) -> None:
        from rigplane.commands import scope_set_rbw

        with pytest.raises(ValueError, match="scope rbw must be 0-2"):
            scope_set_rbw(3, cmd_map=cmd_map)

    def test_scope_set_ref_rejects_out_of_range(self, cmd_map) -> None:
        from rigplane.commands import scope_set_ref

        with pytest.raises(ValueError, match="scope ref must be"):
            scope_set_ref(15.0, cmd_map=cmd_map)

    def test_scope_set_fixed_edge_rejects_end_before_start(self, cmd_map) -> None:
        from rigplane.commands import scope_set_fixed_edge

        with pytest.raises(ValueError, match="end_hz must be greater"):
            scope_set_fixed_edge(
                edge=1, start_hz=14_350_000, end_hz=14_000_000, cmd_map=cmd_map
            )

    def test_scope_set_fixed_edge_rejects_edge_out_of_range(self, cmd_map) -> None:
        from rigplane.commands import scope_set_fixed_edge

        with pytest.raises(ValueError, match="scope fixed edge must be 1-4"):
            scope_set_fixed_edge(
                edge=5, start_hz=14_000_000, end_hz=14_350_000, cmd_map=cmd_map
            )

    def test_scope_receiver_rejects_invalid(self, cmd_map) -> None:
        from rigplane.commands import scope_main_sub

        with pytest.raises(ValueError, match="scope receiver must be 0 or 1"):
            scope_main_sub(5, cmd_map=cmd_map)

    def test_scope_payload_rejects_invalid_receiver(self, cmd_map) -> None:
        from rigplane.commands import scope_set_mode

        with pytest.raises(ValueError, match="scope receiver must be 0 or 1"):
            scope_set_mode(0, receiver=5, cmd_map=cmd_map)


class TestToneTsqlCommands:
    """Tests for tone/TSQL command builders and parsers (#134).

    commands/tone.py migrated onto the bound command map in MOR-2008
    (batch 2): all eight builders now require cmd_map -- zero divergence,
    so every profile that declares these commands agrees with the
    fallback's own bytes exactly, and the expected frames below are
    unchanged. Uses IC-7300, not IC-7610: IC-7610 has no FM-repeater CTCSS
    tone feature and does not declare this family at all (MOR-660/661/682,
    re-checked and left that way at D2 MOR-2017 -- see
    ``rigs/ic7610.toml``'s own ``{ absent = ... }`` row for each of the
    eight commands), so building a cmd_map from it and calling any of
    these builders directly (bypassing ``BoundCommands``, the layer that
    turns a declared-absent lookup into ``CommandError``) would still
    raise ``KeyError`` for every builder in this class.
    """

    @pytest.fixture()
    def cmd_map(self):
        rig = load_rig(RIG_DIR / "ic7300.toml")
        return rig.to_command_map()

    # --- Repeater Tone (0x16 0x42) ---

    def test_get_repeater_tone_main_uses_cmd29(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_MAIN, get_repeater_tone

        frame = get_repeater_tone(receiver=RECEIVER_MAIN, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == RECEIVER_MAIN
        assert frame[6] == 0x16
        assert frame[7] == 0x42

    def test_get_repeater_tone_sub_uses_cmd29(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_SUB, get_repeater_tone

        frame = get_repeater_tone(receiver=RECEIVER_SUB, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == RECEIVER_SUB
        assert frame[6] == 0x16
        assert frame[7] == 0x42

    def test_set_repeater_tone_on(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_MAIN, set_repeater_tone

        frame = set_repeater_tone(True, receiver=RECEIVER_MAIN, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[6] == 0x16
        assert frame[7] == 0x42
        assert frame[8] == 0x01

    def test_set_repeater_tone_off(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_MAIN, set_repeater_tone

        frame = set_repeater_tone(False, receiver=RECEIVER_MAIN, cmd_map=cmd_map)
        assert frame[8] == 0x00

    def test_get_repeater_tone_requires_cmd_map(self) -> None:
        """cmd_map is required keyword-only -- MOR-2006 Q6's API break."""
        from rigplane.commands import get_repeater_tone

        with pytest.raises(TypeError, match="MOR-2006"):
            get_repeater_tone()  # type: ignore[call-arg]

    # --- Repeater TSQL (0x16 0x43) ---

    def test_get_repeater_tsql_uses_cmd29(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_MAIN, get_repeater_tsql

        frame = get_repeater_tsql(receiver=RECEIVER_MAIN, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[6] == 0x16
        assert frame[7] == 0x43

    def test_set_repeater_tsql_on_sub(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_SUB, set_repeater_tsql

        frame = set_repeater_tsql(True, receiver=RECEIVER_SUB, cmd_map=cmd_map)
        assert frame[4] == 0x29
        assert frame[5] == RECEIVER_SUB
        assert frame[6] == 0x16
        assert frame[7] == 0x43
        assert frame[8] == 0x01

    def test_set_repeater_tsql_rejects_explicit_none_the_same_way(
        self, cmd_map
    ) -> None:
        """An explicit ``cmd_map=None`` must hit the same Q6 explanation as
        omitting it entirely."""
        from rigplane.commands import set_repeater_tsql

        with pytest.raises(TypeError, match="MOR-2006"):
            set_repeater_tsql(True, cmd_map=None)

    # --- Tone frequency encoding/decoding ---
    #
    # MOR-2091: this class duplicated test_tone_tsql.py's _BCD_TABLE bug --
    # byte values pinned from the buggy encoder's own output, not from the
    # radio or the manuals (both landed in the same commit as the codec
    # itself, d21d2d66, #134). Corrected here the same way; see
    # tests/test_tone_tsql.py's _BCD_TABLE header comment for the
    # documented layout, the four manuals checked against it, and the live
    # IC-7300 capture for 88.5 Hz (00 08 85).

    def test_encode_tone_freq_88_5(self) -> None:
        from rigplane.commands import _encode_tone_freq

        assert _encode_tone_freq(8850) == bytes([0x00, 0x08, 0x85])

    def test_encode_tone_freq_110_9(self) -> None:
        from rigplane.commands import _encode_tone_freq

        assert _encode_tone_freq(11090) == bytes([0x00, 0x11, 0x09])

    def test_encode_tone_freq_100_0(self) -> None:
        from rigplane.commands import _encode_tone_freq

        assert _encode_tone_freq(10000) == bytes([0x00, 0x10, 0x00])

    def test_encode_tone_freq_67_0(self) -> None:
        from rigplane.commands import _encode_tone_freq

        assert _encode_tone_freq(6700) == bytes([0x00, 0x06, 0x70])

    def test_encode_tone_freq_254_1(self) -> None:
        from rigplane.commands import _encode_tone_freq

        assert _encode_tone_freq(25410) == bytes([0x00, 0x25, 0x41])

    def test_encode_tone_freq_rejects_out_of_range(self) -> None:
        from rigplane.commands import _encode_tone_freq

        with pytest.raises(ValueError, match="0.1 Hz"):
            _encode_tone_freq(8851)

    def test_decode_tone_freq_88_5(self) -> None:
        from rigplane.commands import _decode_tone_freq

        assert _decode_tone_freq(bytes([0x00, 0x08, 0x85])) == 8850

    def test_decode_tone_freq_110_9(self) -> None:
        from rigplane.commands import _decode_tone_freq

        assert _decode_tone_freq(bytes([0x00, 0x11, 0x09])) == 11090

    def test_decode_tone_freq_roundtrip(self) -> None:
        from rigplane.commands import _decode_tone_freq, _encode_tone_freq

        for freq in [6700, 8850, 10000, 11090, 12730, 20350, 25410]:
            encoded = _encode_tone_freq(freq)
            assert _decode_tone_freq(encoded) == freq

    # --- Tone Frequency command (0x1B 0x00) ---

    def test_get_tone_freq_uses_cmd29(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_MAIN, get_tone_freq

        frame = get_tone_freq(
            receiver=RECEIVER_MAIN,
            cmd_map=cmd_map,
            ctcss_tones_centihz=_CTCSS_DOMAIN,
        )
        assert frame[4] == 0x29
        assert frame[5] == RECEIVER_MAIN
        assert frame[6] == 0x1B
        assert frame[7] == 0x00

    def test_set_tone_freq_encodes_bcd(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_MAIN, set_tone_freq

        frame = set_tone_freq(
            8850,
            receiver=RECEIVER_MAIN,
            cmd_map=cmd_map,
            ctcss_tones_centihz=_CTCSS_DOMAIN,
        )
        assert frame[4] == 0x29
        assert frame[6] == 0x1B
        assert frame[7] == 0x00
        assert frame[8:11] == bytes([0x00, 0x08, 0x85])

    def test_set_tone_freq_rejects_out_of_range(self, cmd_map) -> None:
        from rigplane.commands import set_tone_freq

        with pytest.raises(ValueError, match="not declared"):
            set_tone_freq(5000, cmd_map=cmd_map, ctcss_tones_centihz=_CTCSS_DOMAIN)

    # --- TSQL Frequency command (0x1B 0x01) ---

    def test_get_tsql_freq_uses_cmd29(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_SUB, get_tsql_freq

        frame = get_tsql_freq(
            receiver=RECEIVER_SUB,
            cmd_map=cmd_map,
            ctcss_tones_centihz=_CTCSS_DOMAIN,
        )
        assert frame[4] == 0x29
        assert frame[5] == RECEIVER_SUB
        assert frame[6] == 0x1B
        assert frame[7] == 0x01

    def test_set_tsql_freq_encodes_bcd(self, cmd_map) -> None:
        from rigplane.commands import RECEIVER_MAIN, set_tsql_freq

        frame = set_tsql_freq(
            11090,
            receiver=RECEIVER_MAIN,
            cmd_map=cmd_map,
            ctcss_tones_centihz=_CTCSS_DOMAIN,
        )
        assert frame[4] == 0x29
        assert frame[6] == 0x1B
        assert frame[7] == 0x01
        assert frame[8:11] == bytes([0x00, 0x11, 0x09])

    # --- Response parsers ---

    def test_parse_tone_freq_response(self) -> None:
        from rigplane import IC_7610_ADDR
        from rigplane.commands import (
            CONTROLLER_ADDR,
            RECEIVER_MAIN,
            build_cmd29_frame,
            parse_civ_frame,
            parse_tone_freq_response,
        )

        civ = build_cmd29_frame(
            CONTROLLER_ADDR,
            IC_7610_ADDR,
            0x1B,
            sub=0x00,
            data=bytes([0x00, 0x08, 0x85]),
            receiver=RECEIVER_MAIN,
        )
        frame = parse_civ_frame(civ)
        receiver, freq = parse_tone_freq_response(
            frame, ctcss_tones_centihz=_CTCSS_DOMAIN
        )
        assert receiver == RECEIVER_MAIN
        assert freq == 8850

    def test_parse_tsql_freq_response(self) -> None:
        from rigplane import IC_7610_ADDR
        from rigplane.commands import (
            CONTROLLER_ADDR,
            RECEIVER_SUB,
            build_cmd29_frame,
            parse_civ_frame,
            parse_tsql_freq_response,
        )

        civ = build_cmd29_frame(
            CONTROLLER_ADDR,
            IC_7610_ADDR,
            0x1B,
            sub=0x01,
            data=bytes([0x00, 0x11, 0x09]),
            receiver=RECEIVER_SUB,
        )
        frame = parse_civ_frame(civ)
        receiver, freq = parse_tsql_freq_response(
            frame, ctcss_tones_centihz=_CTCSS_DOMAIN
        )
        assert receiver == RECEIVER_SUB
        assert freq == 11090

    def test_build_memory_mode_get(self) -> None:
        """get_memory_mode is PROVENANCE OPEN on every shipped Icom profile
        (MOR-2008 batch 4) -- no profile declares it, so this pins pure
        byte assembly against a synthetic literal map, the same pattern
        ``TestExpect: test_expect_on_speech_resolves_per_map_probe`` uses.
        """
        from rigplane.command_map import CommandMap
        from rigplane.commands import build_memory_mode_get

        civ = build_memory_mode_get(cmd_map=CommandMap({"get_memory_mode": (0x08,)}))
        # FE FE 98 E0 08 FD
        assert civ == b"\xfe\xfe\x98\xe0\x08\xfd"

    def test_build_memory_mode_get_requires_cmd_map(self) -> None:
        """cmd_map is required keyword-only -- MOR-2006 Q6's API break."""
        from rigplane.commands import build_memory_mode_get

        with pytest.raises(TypeError, match="MOR-2006"):
            build_memory_mode_get()  # type: ignore[call-arg]

    def test_build_memory_mode_set(self, cmd_map) -> None:
        from rigplane.commands import build_memory_mode_set

        civ = build_memory_mode_set(42, cmd_map=cmd_map)
        # FE FE 98 E0 08 00 42 FD (channel 42 in BCD)
        assert civ == b"\xfe\xfe\x98\xe0\x08\x00\x42\xfd"

    def test_build_memory_mode_set_validates_range(self, cmd_map) -> None:
        from rigplane.commands import build_memory_mode_set

        with pytest.raises(ValueError, match="Channel must be 1-101"):
            build_memory_mode_set(0, cmd_map=cmd_map)
        with pytest.raises(ValueError, match="Channel must be 1-101"):
            build_memory_mode_set(102, cmd_map=cmd_map)

    def test_parse_memory_mode_response(self) -> None:
        from rigplane.commands import parse_civ_frame, parse_memory_mode_response

        # Response: FE FE E0 98 08 00 42 FD (channel 42)
        civ = b"\xfe\xfe\xe0\x98\x08\x00\x42\xfd"
        frame = parse_civ_frame(civ)
        channel = parse_memory_mode_response(frame)
        assert channel == 42

    def test_build_memory_write(self, cmd_map) -> None:
        from rigplane.commands import build_memory_write

        civ = build_memory_write(cmd_map=cmd_map)
        # FE FE 98 E0 09 FD
        assert civ == b"\xfe\xfe\x98\xe0\x09\xfd"

    def test_build_memory_to_vfo(self, cmd_map) -> None:
        from rigplane.commands import build_memory_to_vfo

        civ = build_memory_to_vfo(99, cmd_map=cmd_map)
        # FE FE 98 E0 0A 00 99 FD (channel 99 in BCD)
        assert civ == b"\xfe\xfe\x98\xe0\x0a\x00\x99\xfd"

    def test_build_memory_clear(self, cmd_map) -> None:
        from rigplane.commands import build_memory_clear

        civ = build_memory_clear(1, cmd_map=cmd_map)
        # FE FE 98 E0 0B 00 01 FD (channel 1 in BCD)
        assert civ == b"\xfe\xfe\x98\xe0\x0b\x00\x01\xfd"

    def test_build_memory_clear_requires_cmd_map(self) -> None:
        """cmd_map is required keyword-only -- MOR-2006 Q6's API break."""
        from rigplane.commands import build_memory_clear

        with pytest.raises(TypeError, match="MOR-2006"):
            build_memory_clear(1)  # type: ignore[call-arg]

    def test_build_memory_contents_get(self, cmd_map) -> None:
        from rigplane.commands import build_memory_contents_get

        civ = build_memory_contents_get(50, cmd_map=cmd_map)
        # FE FE 98 E0 1A 00 00 50 FD (0x1A sub=0x00, channel 50 BCD)
        assert civ == b"\xfe\xfe\x98\xe0\x1a\x00\x00\x50\xfd"

    def test_build_memory_contents_set(self, cmd_map) -> None:
        from rigplane.commands import build_memory_contents_set
        from rigplane.types import MemoryChannel

        mem = MemoryChannel(
            channel=42,
            frequency_hz=14074000,
            mode=1,  # USB
            filter=1,
            scan=0,
            datamode=0,
            tonemode=0,
            tone_freq_hz=None,
            tsql_freq_hz=None,
            name="FT8",
        )
        civ = build_memory_contents_set(mem, cmd_map=cmd_map)
        # FE FE 98 E0 1A 00 <channel 2 bytes> <payload 26 bytes> FD
        # Structure: FE FE(2) + to/from(2) + cmd(1) + sub(1) + data(28) + FD(1) = 35 bytes
        assert len(civ) == 35
        assert civ[:8] == b"\xfe\xfe\x98\xe0\x1a\x00\x00\x42"
        # Payload starts at offset 8: scan(1) + freq(5) + ...
        # Check freq at offset 8+1 = 9: 14.074 MHz = 0x00 0x40 0x07 0x14 0x00
        assert civ[9:14] == b"\x00\x40\x07\x14\x00"
        # Check name at offset 8+1+5+1+1+1+3+3 = 23: "FT8" padded
        assert civ[23:26] == b"FT8"

    def test_parse_memory_contents_response(self) -> None:
        from rigplane.commands import parse_civ_frame, parse_memory_contents_response

        # Build minimal response: channel 42, freq 14.074 MHz, mode USB, filter 1, name "TEST"
        # data = channel(2) + payload(26)
        data = bytearray(28)
        data[0:2] = b"\x00\x42"  # channel 42
        data[2] = 0  # scan off
        data[3:8] = b"\x00\x40\x07\x14\x00"  # 14.074 MHz
        data[8] = 0x01  # USB
        data[9] = 0x01  # filter 1
        data[10] = 0x00  # datamode=0, tonemode=0
        data[17:21] = b"TEST"

        # FE FE E0 98 1A 00 <data> FD
        civ = b"\xfe\xfe\xe0\x98\x1a\x00" + bytes(data) + b"\xfd"
        frame = parse_civ_frame(civ)
        mem = parse_memory_contents_response(frame)

        assert mem.channel == 42
        assert mem.frequency_hz == 14074000
        assert mem.mode == 1
        assert mem.filter == 1
        assert mem.scan == 0
        assert mem.name == "TEST"

    def test_build_band_stack_get(self, cmd_map) -> None:
        from rigplane.commands import build_band_stack_get

        civ = build_band_stack_get(15, 1, cmd_map=cmd_map)  # band 15 (20m), register 1
        # FE FE 98 E0 1A 01 0F 01 FD (0x1A sub=0x01, band=15, reg=1)
        assert civ == b"\xfe\xfe\x98\xe0\x1a\x01\x0f\x01\xfd"

    def test_build_band_stack_get_validates_range(self, cmd_map) -> None:
        from rigplane.commands import build_band_stack_get

        with pytest.raises(ValueError, match="Band must be 0-24"):
            build_band_stack_get(25, 1, cmd_map=cmd_map)
        with pytest.raises(ValueError, match="Register must be 1-3"):
            build_band_stack_get(15, 0, cmd_map=cmd_map)
        with pytest.raises(ValueError, match="Register must be 1-3"):
            build_band_stack_get(15, 4, cmd_map=cmd_map)

    def test_build_band_stack_get_requires_cmd_map(self) -> None:
        """cmd_map is required keyword-only -- MOR-2006 Q6's API break."""
        from rigplane.commands import build_band_stack_get

        with pytest.raises(TypeError, match="MOR-2006"):
            build_band_stack_get(15, 1)  # type: ignore[call-arg]

    def test_build_band_stack_set(self, cmd_map) -> None:
        from rigplane.commands import set_bsr
        from rigplane.types import BandStackRegister

        bsr = BandStackRegister(
            band=15,  # 20m
            register=1,
            frequency_hz=14200000,
            mode=1,  # USB
            filter=1,
        )
        civ = set_bsr(bsr, cmd_map=cmd_map)
        # FE FE 98 E0 1A 01 0F 01 <freq 5 bytes> <mode 1 byte> <filter 1 byte> FD
        assert civ[:8] == b"\xfe\xfe\x98\xe0\x1a\x01\x0f\x01"
        # freq 14.200 MHz = 00 00 20 14 00 (BCD little-endian)
        assert civ[8:13] == b"\x00\x00\x20\x14\x00"
        assert civ[13] == 0x01  # mode
        assert civ[14] == 0x01  # filter

    def test_parse_band_stack_response(self) -> None:
        from rigplane.commands import parse_band_stack_response, parse_civ_frame

        # Response: band=15, reg=1, freq=14.200 MHz, mode=1, filter=1
        payload = bytes([15, 1]) + b"\x00\x00\x20\x14\x00" + bytes([0x01, 0x01])
        civ = b"\xfe\xfe\xe0\x98\x1a\x01" + payload + b"\xfd"
        frame = parse_civ_frame(civ)
        bsr = parse_band_stack_response(frame)

        assert bsr.band == 15
        assert bsr.register == 1
        assert bsr.frequency_hz == 14200000
        assert bsr.mode == 1
        assert bsr.filter == 1


class TestSystemConfigCommands:
    """Tests for system/config command builders and parsers (#135)."""

    # --- REF Adjust (0x1A 0x05 0x00 0x70) ---
    #
    # commands/levels.py migrated onto the bound command map in MOR-2006
    # Steps 5..N (module 2): both builders now require ``cmd_map``, and
    # ``rigs/ic7610.toml``'s ``get_ref_adjust``/``set_ref_adjust`` declare
    # the same ``[0x1A, 0x05, 0x00, 0x70]`` wire tuple the fallback used to
    # build, so the expected frames below are unchanged. Reuses the
    # ``cmd_map`` fixture defined below for config.py's own tests.

    def test_get_ref_adjust_frame(self, cmd_map) -> None:
        from rigplane.commands import get_ref_adjust

        frame = get_ref_adjust(cmd_map=cmd_map)
        # FE FE 98 E0 1A 05 00 70 FD
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x00\x70\xfd"

    def test_set_ref_adjust_255(self, cmd_map) -> None:
        from rigplane.commands import set_ref_adjust

        frame = set_ref_adjust(255, cmd_map=cmd_map)
        # 255 as 2-byte BCD: 0x02 0x55
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x00\x70\x02\x55\xfd"

    def test_set_ref_adjust_0(self, cmd_map) -> None:
        from rigplane.commands import set_ref_adjust

        frame = set_ref_adjust(0, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x00\x70\x00\x00\xfd"

    def test_set_ref_adjust_511(self, cmd_map) -> None:
        from rigplane.commands import set_ref_adjust

        frame = set_ref_adjust(511, cmd_map=cmd_map)
        # 511 as 2-byte BCD: 0x05 0x11
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x00\x70\x05\x11\xfd"

    def test_set_ref_adjust_rejects_out_of_range(self, cmd_map) -> None:
        from rigplane.commands import set_ref_adjust

        with pytest.raises(ValueError, match="REF Adjust must be 0-511"):
            set_ref_adjust(-1, cmd_map=cmd_map)
        with pytest.raises(ValueError, match="REF Adjust must be 0-511"):
            set_ref_adjust(512, cmd_map=cmd_map)

    def test_parse_ref_adjust_response(self) -> None:
        from rigplane.commands import parse_civ_frame, parse_level_response

        # Radio responds with REF Adjust = 256
        civ = b"\xfe\xfe\xe0\x98\x1a\x05\x00\x70\x02\x56\xfd"
        frame = parse_civ_frame(civ)
        value = parse_level_response(frame, command=0x1A, sub=0x05, prefix=b"\x00\x70")
        assert value == 256

    def test_ref_adjust_roundtrip(self, cmd_map) -> None:
        from rigplane.commands import (
            parse_civ_frame,
            parse_level_response,
            set_ref_adjust,
        )

        for v in [0, 128, 256, 511]:
            frame = set_ref_adjust(v, cmd_map=cmd_map)
            response = b"\xfe\xfe" + bytes([frame[3], frame[2]]) + frame[4:]
            parsed = parse_civ_frame(response)
            assert (
                parse_level_response(parsed, command=0x1A, sub=0x05, prefix=b"\x00\x70")
                == v
            )

    # --- Dash Ratio (0x1A 0x05 0x02 0x28) ---
    #
    # Same MOR-2006 migration as REF Adjust above -- ``rigs/ic7610.toml``'s
    # dash-ratio wire tuple matches the deleted fallback, so the expected
    # frames are unchanged.

    def test_get_dash_ratio_frame(self, cmd_map) -> None:
        from rigplane.commands import get_dash_ratio

        frame = get_dash_ratio(cmd_map=cmd_map)
        # FE FE 98 E0 1A 05 02 28 FD
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x02\x28\xfd"

    def test_set_dash_ratio_28(self, cmd_map) -> None:
        from rigplane.commands import set_dash_ratio

        frame = set_dash_ratio(28, cmd_map=cmd_map)
        # 28 as 1-byte BCD: 0x28
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x02\x28\x28\xfd"

    def test_set_dash_ratio_30(self, cmd_map) -> None:
        from rigplane.commands import set_dash_ratio

        frame = set_dash_ratio(30, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x02\x28\x30\xfd"

    def test_set_dash_ratio_45(self, cmd_map) -> None:
        from rigplane.commands import set_dash_ratio

        frame = set_dash_ratio(45, cmd_map=cmd_map)
        # 45 as 1-byte BCD: 0x45
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x02\x28\x45\xfd"

    def test_set_dash_ratio_rejects_out_of_range(self, cmd_map) -> None:
        from rigplane.commands import set_dash_ratio

        with pytest.raises(ValueError, match="Dash Ratio must be 28-45"):
            set_dash_ratio(27, cmd_map=cmd_map)
        with pytest.raises(ValueError, match="Dash Ratio must be 28-45"):
            set_dash_ratio(46, cmd_map=cmd_map)

    def test_parse_dash_ratio_response(self) -> None:
        from rigplane.commands import parse_civ_frame, parse_level_response

        # Radio responds with Dash Ratio = 35 (0x35)
        civ = b"\xfe\xfe\xe0\x98\x1a\x05\x02\x28\x35\xfd"
        frame = parse_civ_frame(civ)
        value = parse_level_response(
            frame, command=0x1A, sub=0x05, prefix=b"\x02\x28", bcd_bytes=1
        )
        assert value == 35

    def test_dash_ratio_roundtrip(self, cmd_map) -> None:
        from rigplane.commands import (
            parse_civ_frame,
            parse_level_response,
            set_dash_ratio,
        )

        for v in [28, 30, 35, 40, 45]:
            frame = set_dash_ratio(v, cmd_map=cmd_map)
            response = b"\xfe\xfe" + bytes([frame[3], frame[2]]) + frame[4:]
            parsed = parse_civ_frame(response)
            assert (
                parse_level_response(
                    parsed, command=0x1A, sub=0x05, prefix=b"\x02\x28", bcd_bytes=1
                )
                == v
            )

    # --- Antenna Selection (0x12) ---
    #
    # commands/antenna.py migrated onto the bound command map in MOR-2008
    # (batch 2): all eight builders now require cmd_map -- zero
    # divergence, so IC-7610's own map declares the identical bytes the
    # fallback used to build, and the expected frames below are unchanged.

    def test_get_antenna_1_frame(self, cmd_map) -> None:
        from rigplane.commands import get_antenna_1

        frame = get_antenna_1(cmd_map=cmd_map)
        # FE FE 98 E0 12 00 FD
        assert frame == b"\xfe\xfe\x98\xe0\x12\x00\xfd"

    def test_get_antenna_1_requires_cmd_map(self) -> None:
        """cmd_map is required keyword-only -- MOR-2006 Q6's API break."""
        from rigplane.commands import get_antenna_1

        with pytest.raises(TypeError, match="MOR-2006"):
            get_antenna_1()  # type: ignore[call-arg]

    def test_set_antenna_1_on(self, cmd_map) -> None:
        from rigplane.commands import set_antenna_1

        frame = set_antenna_1(True, cmd_map=cmd_map)
        # FE FE 98 E0 12 00 01 FD
        assert frame == b"\xfe\xfe\x98\xe0\x12\x00\x01\xfd"

    def test_set_antenna_1_off(self, cmd_map) -> None:
        from rigplane.commands import set_antenna_1

        frame = set_antenna_1(False, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x12\x00\x00\xfd"

    def test_set_antenna_1_rejects_explicit_none_the_same_way(self, cmd_map) -> None:
        """An explicit ``cmd_map=None`` must hit the same Q6 explanation as
        omitting it entirely."""
        from rigplane.commands import set_antenna_1

        with pytest.raises(TypeError, match="MOR-2006"):
            set_antenna_1(True, cmd_map=None)

    def test_get_antenna_2_frame(self, cmd_map) -> None:
        from rigplane.commands import get_antenna_2

        frame = get_antenna_2(cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x12\x01\xfd"

    def test_set_antenna_2_on(self, cmd_map) -> None:
        from rigplane.commands import set_antenna_2

        frame = set_antenna_2(True, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x12\x01\x01\xfd"

    def test_get_rx_antenna_ant1_frame(self, cmd_map) -> None:
        from rigplane.commands import get_rx_antenna_ant1

        frame = get_rx_antenna_ant1(cmd_map=cmd_map)
        # IC-7610 CI-V: RX-ANT is encoded as data byte on 0x12 0x00 (ANT1)
        assert frame == b"\xfe\xfe\x98\xe0\x12\x00\xfd"

    def test_set_rx_antenna_ant1_on(self, cmd_map) -> None:
        from rigplane.commands import set_rx_antenna_ant1

        frame = set_rx_antenna_ant1(True, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x12\x00\x01\xfd"

    def test_get_rx_antenna_ant2_frame(self, cmd_map) -> None:
        from rigplane.commands import get_rx_antenna_ant2

        frame = get_rx_antenna_ant2(cmd_map=cmd_map)
        # IC-7610 CI-V: RX-ANT is encoded as data byte on 0x12 0x01 (ANT2)
        assert frame == b"\xfe\xfe\x98\xe0\x12\x01\xfd"

    def test_set_rx_antenna_ant2_off(self, cmd_map) -> None:
        from rigplane.commands import set_rx_antenna_ant2

        frame = set_rx_antenna_ant2(False, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x12\x01\x00\xfd"

    def test_parse_antenna_bool_response(self) -> None:
        from rigplane.commands import parse_bool_response, parse_civ_frame

        # Radio responds: FE FE E0 98 12 00 01 FD (ANT1 = ON)
        civ = b"\xfe\xfe\xe0\x98\x12\x00\x01\xfd"
        frame = parse_civ_frame(civ)
        result = parse_bool_response(frame, command=0x12, sub=0x00)
        assert result is True

    def test_parse_antenna_bool_response_off(self) -> None:
        from rigplane.commands import parse_bool_response, parse_civ_frame

        civ = b"\xfe\xfe\xe0\x98\x12\x01\x00\xfd"
        frame = parse_civ_frame(civ)
        result = parse_bool_response(frame, command=0x12, sub=0x01)
        assert result is False

    # --- Modulation Levels, Modulation Input Routing and CI-V Options ---
    #
    # commands/config.py migrated onto the bound command map in MOR-2006
    # Steps 5..N (module 1): every builder here now requires ``cmd_map``,
    # and the wire bytes it builds are whatever the profile declares (IC-7610
    # extended menu addresses, per ``rigs/ic7610.toml``) rather than a
    # hardcoded fallback -- so the expected frames below are pinned against
    # the real IC-7610 map, not hand-typed CI-V literals, per MOR-2006's
    # "prefer load_rig(...) maps over hand literals" convention.

    @pytest.fixture()
    def cmd_map(self):
        rig = load_rig(RIG_DIR / "ic7610.toml")
        return rig.to_command_map()

    def test_get_acc1_mod_level_frame(self, cmd_map) -> None:
        from rigplane.commands import get_acc1_mod_level

        frame = get_acc1_mod_level(cmd_map=cmd_map)
        # rigs/ic7610.toml: get_acc1_mod_level = [0x1A, 0x05, 0x00, 0x88]
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x00\x88\xfd"

    def test_set_acc1_mod_level_128(self, cmd_map) -> None:
        from rigplane.commands import set_acc1_mod_level

        frame = set_acc1_mod_level(128, cmd_map=cmd_map)
        # 128 BCD-encoded: 0x01 0x28
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x00\x88\x01\x28\xfd"

    def test_set_acc1_mod_level_0(self, cmd_map) -> None:
        from rigplane.commands import set_acc1_mod_level

        frame = set_acc1_mod_level(0, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x00\x88\x00\x00\xfd"

    def test_set_acc1_mod_level_255(self, cmd_map) -> None:
        from rigplane.commands import set_acc1_mod_level

        frame = set_acc1_mod_level(255, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x00\x88\x02\x55\xfd"

    def test_set_acc1_mod_level_rejects_out_of_range(self, cmd_map) -> None:
        from rigplane.commands import set_acc1_mod_level

        with pytest.raises(ValueError, match="Level must be 0-255"):
            set_acc1_mod_level(256, cmd_map=cmd_map)

    def test_get_acc1_mod_level_requires_cmd_map(self) -> None:
        """cmd_map is required keyword-only -- MOR-2006 Q6's API break."""
        from rigplane.commands import get_acc1_mod_level

        with pytest.raises(TypeError, match="MOR-2006"):
            get_acc1_mod_level()  # type: ignore[call-arg]

    def test_get_acc1_mod_level_rejects_explicit_none_the_same_way(self) -> None:
        """An explicit ``cmd_map=None`` must hit the same Q6 explanation as
        omitting it entirely -- not a bare ``AttributeError`` from
        ``_build_from_map``'s ``None.get(name)``, the exact path
        de-delegating from the shared ``_build_ctl_mem_get``/
        ``_build_ctl_mem_set`` templates (both deleted now, this
        migration's rationale for deleting them) would otherwise reopen."""
        from rigplane.commands import get_acc1_mod_level

        with pytest.raises(TypeError, match="MOR-2006"):
            get_acc1_mod_level(cmd_map=None)

    def test_get_usb_mod_level_frame(self, cmd_map) -> None:
        from rigplane.commands import get_usb_mod_level

        frame = get_usb_mod_level(cmd_map=cmd_map)
        # rigs/ic7610.toml: get_usb_mod_level = [0x1A, 0x05, 0x00, 0x89]
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x00\x89\xfd"

    def test_set_usb_mod_level_100(self, cmd_map) -> None:
        from rigplane.commands import set_usb_mod_level

        frame = set_usb_mod_level(100, cmd_map=cmd_map)
        # 100 BCD-encoded: 0x01 0x00
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x00\x89\x01\x00\xfd"

    def test_get_lan_mod_level_frame(self, cmd_map) -> None:
        from rigplane.commands import get_lan_mod_level

        frame = get_lan_mod_level(cmd_map=cmd_map)
        # rigs/ic7610.toml: get_lan_mod_level = [0x1A, 0x05, 0x00, 0x90]
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x00\x90\xfd"

    def test_set_lan_mod_level_50(self, cmd_map) -> None:
        from rigplane.commands import set_lan_mod_level

        frame = set_lan_mod_level(50, cmd_map=cmd_map)
        # 50 BCD-encoded: 0x00 0x50
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x00\x90\x00\x50\xfd"

    def test_parse_mod_level_response(self) -> None:
        from rigplane.commands import parse_civ_frame, parse_level_response

        # ACC1 mod level = 128 (0x01 0x28) at the IC-7610 extended address.
        civ = b"\xfe\xfe\xe0\x98\x1a\x05\x00\x88\x01\x28\xfd"
        frame = parse_civ_frame(civ)
        level = parse_level_response(frame, command=0x1A, sub=0x05, prefix=b"\x00\x88")
        assert level == 128

    def test_get_data_off_mod_input_frame(self, cmd_map) -> None:
        from rigplane.commands import get_data_off_mod_input

        frame = get_data_off_mod_input(cmd_map=cmd_map)
        # rigs/ic7610.toml: get_data_off_mod_input = [0x1A, 0x05, 0x00, 0x91]
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x00\x91\xfd"

    def test_set_data_off_mod_input_mic(self, cmd_map) -> None:
        from rigplane.commands import set_data_off_mod_input

        frame = set_data_off_mod_input(0, cmd_map=cmd_map)  # MIC
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x00\x91\x00\xfd"

    def test_set_data_off_mod_input_lan(self, cmd_map) -> None:
        from rigplane.commands import set_data_off_mod_input

        frame = set_data_off_mod_input(5, cmd_map=cmd_map)  # LAN
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x00\x91\x05\xfd"

    def test_set_data_off_mod_input_rejects_out_of_range(self, cmd_map) -> None:
        from rigplane.commands import set_data_off_mod_input

        with pytest.raises(ValueError, match="0-5"):
            set_data_off_mod_input(6, cmd_map=cmd_map)

    def test_get_data1_mod_input_frame(self, cmd_map) -> None:
        from rigplane.commands import get_data1_mod_input

        frame = get_data1_mod_input(cmd_map=cmd_map)
        # rigs/ic7610.toml: get_data1_mod_input = [0x1A, 0x05, 0x00, 0x92]
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x00\x92\xfd"

    def test_set_data1_mod_input_lan(self, cmd_map) -> None:
        from rigplane.commands import set_data1_mod_input

        frame = set_data1_mod_input(5, cmd_map=cmd_map)  # LAN
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x00\x92\x05\xfd"

    def test_set_data1_mod_input_rejects_out_of_range(self, cmd_map) -> None:
        from rigplane.commands import set_data1_mod_input

        with pytest.raises(ValueError, match="0-5"):
            set_data1_mod_input(6, cmd_map=cmd_map)

    def test_get_data2_mod_input_frame(self, cmd_map) -> None:
        from rigplane.commands import get_data2_mod_input

        frame = get_data2_mod_input(cmd_map=cmd_map)
        # rigs/ic7610.toml: get_data2_mod_input = [0x1A, 0x05, 0x00, 0x93]
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x00\x93\xfd"

    def test_set_data2_mod_input_lan(self, cmd_map) -> None:
        from rigplane.commands import set_data2_mod_input

        frame = set_data2_mod_input(5, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x00\x93\x05\xfd"

    def test_get_data3_mod_input_frame(self, cmd_map) -> None:
        from rigplane.commands import get_data3_mod_input

        frame = get_data3_mod_input(cmd_map=cmd_map)
        # rigs/ic7610.toml: get_data3_mod_input = [0x1A, 0x05, 0x00, 0x94]
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x00\x94\xfd"

    def test_set_data3_mod_input_lan(self, cmd_map) -> None:
        from rigplane.commands import set_data3_mod_input

        frame = set_data3_mod_input(5, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x00\x94\x05\xfd"

    def test_set_data3_mod_input_lan_usb(self, cmd_map) -> None:
        from rigplane.commands import set_data3_mod_input

        frame = set_data3_mod_input(4, cmd_map=cmd_map)  # LAN+USB
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x00\x94\x04\xfd"

    def test_parse_mod_input_response(self) -> None:
        from rigplane.commands import parse_civ_frame, parse_level_response

        # Data Off mod input = 3 (USB) at the IC-7610 extended address.
        civ = b"\xfe\xfe\xe0\x98\x1a\x05\x00\x91\x03\xfd"
        frame = parse_civ_frame(civ)
        source = parse_level_response(
            frame, command=0x1A, sub=0x05, prefix=b"\x00\x91", bcd_bytes=1
        )
        assert source == 3

    def test_get_civ_transceive_frame(self, cmd_map) -> None:
        from rigplane.commands import get_civ_transceive

        frame = get_civ_transceive(cmd_map=cmd_map)
        # rigs/ic7610.toml: get_civ_transceive = [0x1A, 0x05, 0x01, 0x12]
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x01\x12\xfd"

    def test_set_civ_transceive_on(self, cmd_map) -> None:
        from rigplane.commands import set_civ_transceive

        frame = set_civ_transceive(True, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x01\x12\x01\xfd"

    def test_set_civ_transceive_off(self, cmd_map) -> None:
        from rigplane.commands import set_civ_transceive

        frame = set_civ_transceive(False, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x01\x12\x00\xfd"

    def test_get_civ_output_ant_frame(self, cmd_map) -> None:
        from rigplane.commands import get_civ_output_ant

        frame = get_civ_output_ant(cmd_map=cmd_map)
        # rigs/ic7610.toml: get_civ_output_ant = [0x1C, 0x04] -- a different
        # CI-V command family entirely, not another 0x1A 0x05 menu address.
        assert frame == b"\xfe\xfe\x98\xe0\x1c\x04\xfd"

    def test_set_civ_output_ant_on(self, cmd_map) -> None:
        from rigplane.commands import set_civ_output_ant

        frame = set_civ_output_ant(True, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x1c\x04\x01\xfd"

    def test_parse_civ_bool_response(self) -> None:
        from rigplane.commands import parse_bool_response, parse_civ_frame

        # CI-V transceive = ON at the IC-7610 extended address.
        civ = b"\xfe\xfe\xe0\x98\x1a\x05\x01\x12\x01\xfd"
        frame = parse_civ_frame(civ)
        result = parse_bool_response(frame, command=0x1A, sub=0x05, prefix=b"\x01\x12")
        assert result is True

    def test_parse_civ_output_ant_off_response(self) -> None:
        from rigplane.commands import parse_bool_response, parse_civ_frame

        # IC-7610's get_civ_output_ant is plain 0x1C 0x04, no data prefix.
        civ = b"\xfe\xfe\xe0\x98\x1c\x04\x00\xfd"
        frame = parse_civ_frame(civ)
        result = parse_bool_response(frame, command=0x1C, sub=0x04)
        assert result is False

    # --- System Date (0x1A 0x05 0x01 0x58) ---
    #
    # commands/system.py migrated onto the bound command map in MOR-2008
    # (batch 1): ``get_system_date``/``set_system_date`` now resolve the
    # direction-prefixed keys ``rigs/ic7610.toml`` actually declares (the
    # owner ruling: the bare ``"system_date"`` key the pre-migration
    # ``cmd_map`` branch resolved was dead on arrival, present on no
    # profile). IC-7610's own tuple is byte-identical to the deleted
    # fallback's ``0x01 0x58``, so the expected frames below are unchanged.

    def test_get_system_date_frame(self, cmd_map) -> None:
        from rigplane.commands import get_system_date

        frame = get_system_date(cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x01\x58\xfd"

    def test_get_system_date_requires_cmd_map(self) -> None:
        """cmd_map is required keyword-only -- MOR-2006 Q6's API break."""
        from rigplane.commands import get_system_date

        with pytest.raises(TypeError, match="MOR-2006"):
            get_system_date()  # type: ignore[call-arg]

    def test_set_system_date_rejects_explicit_none_the_same_way(self, cmd_map) -> None:
        """An explicit ``cmd_map=None`` must hit the same Q6 explanation as
        omitting it entirely."""
        from rigplane.commands import set_system_date

        with pytest.raises(TypeError, match="MOR-2006"):
            set_system_date(2026, 3, 7, cmd_map=None)

    def test_set_system_date_2026_03_07(self, cmd_map) -> None:
        from rigplane.commands import set_system_date

        frame = set_system_date(2026, 3, 7, cmd_map=cmd_map)
        # FE FE 98 E0 1A 05 01 58 20 26 03 07 FD
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x01\x58\x20\x26\x03\x07\xfd"

    def test_set_system_date_2000_01_01(self, cmd_map) -> None:
        from rigplane.commands import set_system_date

        frame = set_system_date(2000, 1, 1, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x01\x58\x20\x00\x01\x01\xfd"

    def test_set_system_date_rejects_invalid_month(self, cmd_map) -> None:
        from rigplane.commands import set_system_date

        with pytest.raises(ValueError, match="Month must be 1-12"):
            set_system_date(2026, 0, 1, cmd_map=cmd_map)
        with pytest.raises(ValueError, match="Month must be 1-12"):
            set_system_date(2026, 13, 1, cmd_map=cmd_map)

    def test_set_system_date_rejects_invalid_day(self, cmd_map) -> None:
        from rigplane.commands import set_system_date

        with pytest.raises(ValueError, match="Day must be 1-31"):
            set_system_date(2026, 3, 0, cmd_map=cmd_map)
        with pytest.raises(ValueError, match="Day must be 1-31"):
            set_system_date(2026, 3, 32, cmd_map=cmd_map)

    def test_set_system_date_rejects_year_too_low(self, cmd_map) -> None:
        from rigplane.commands import set_system_date

        with pytest.raises(ValueError, match="Year must be 2000-2099"):
            set_system_date(1999, 1, 1, cmd_map=cmd_map)

    def test_set_system_date_rejects_year_too_high(self, cmd_map) -> None:
        from rigplane.commands import set_system_date

        with pytest.raises(ValueError, match="Year must be 2000-2099"):
            set_system_date(2100, 1, 1, cmd_map=cmd_map)

    def test_set_system_date_accepts_year_boundaries(self, cmd_map) -> None:
        from rigplane.commands import set_system_date

        # Should accept 2000 and 2099
        frame_2000 = set_system_date(2000, 1, 1, cmd_map=cmd_map)
        assert frame_2000 == b"\xfe\xfe\x98\xe0\x1a\x05\x01\x58\x20\x00\x01\x01\xfd"

        frame_2099 = set_system_date(2099, 12, 31, cmd_map=cmd_map)
        assert frame_2099 == b"\xfe\xfe\x98\xe0\x1a\x05\x01\x58\x20\x99\x12\x31\xfd"

    def test_parse_system_date_response(self) -> None:
        from rigplane.commands import parse_civ_frame, parse_system_date_response

        # Response: 2026-03-07
        civ = b"\xfe\xfe\xe0\x98\x1a\x05\x01\x58\x20\x26\x03\x07\xfd"
        frame = parse_civ_frame(civ)
        year, month, day = parse_system_date_response(frame)
        assert year == 2026
        assert month == 3
        assert day == 7

    def test_parse_system_date_response_2000(self) -> None:
        from rigplane.commands import parse_civ_frame, parse_system_date_response

        civ = b"\xfe\xfe\xe0\x98\x1a\x05\x01\x58\x20\x00\x12\x31\xfd"
        frame = parse_civ_frame(civ)
        year, month, day = parse_system_date_response(frame)
        assert year == 2000
        assert month == 12
        assert day == 31

    def test_system_date_roundtrip(self, cmd_map) -> None:
        from rigplane.commands import (
            parse_civ_frame,
            parse_system_date_response,
            set_system_date,
        )

        frame = set_system_date(2025, 6, 15, cmd_map=cmd_map)
        # Simulate radio echoing back the frame (swap addresses)
        response = b"\xfe\xfe" + bytes([frame[3], frame[2]]) + frame[4:]
        parsed = parse_civ_frame(response)
        year, month, day = parse_system_date_response(parsed)
        assert (year, month, day) == (2025, 6, 15)

    # --- System Time (0x1A 0x05 0x01 0x59) ---

    def test_get_system_time_frame(self, cmd_map) -> None:
        from rigplane.commands import get_system_time

        frame = get_system_time(cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x01\x59\xfd"

    def test_set_system_time_16_45(self, cmd_map) -> None:
        from rigplane.commands import set_system_time

        frame = set_system_time(16, 45, cmd_map=cmd_map)
        # FE FE 98 E0 1A 05 01 59 16 45 FD
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x01\x59\x16\x45\xfd"

    def test_set_system_time_00_00(self, cmd_map) -> None:
        from rigplane.commands import set_system_time

        frame = set_system_time(0, 0, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x01\x59\x00\x00\xfd"

    def test_set_system_time_23_59(self, cmd_map) -> None:
        from rigplane.commands import set_system_time

        frame = set_system_time(23, 59, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x01\x59\x23\x59\xfd"

    def test_set_system_time_rejects_invalid_hour(self, cmd_map) -> None:
        from rigplane.commands import set_system_time

        with pytest.raises(ValueError, match="Hour must be 0-23"):
            set_system_time(24, 0, cmd_map=cmd_map)

    def test_set_system_time_rejects_invalid_minute(self, cmd_map) -> None:
        from rigplane.commands import set_system_time

        with pytest.raises(ValueError, match="Minute must be 0-59"):
            set_system_time(12, 60, cmd_map=cmd_map)

    def test_parse_system_time_response(self) -> None:
        from rigplane.commands import parse_civ_frame, parse_system_time_response

        # Response: 16:45
        civ = b"\xfe\xfe\xe0\x98\x1a\x05\x01\x59\x16\x45\xfd"
        frame = parse_civ_frame(civ)
        hour, minute = parse_system_time_response(frame)
        assert hour == 16
        assert minute == 45

    def test_system_time_roundtrip(self, cmd_map) -> None:
        from rigplane.commands import (
            parse_civ_frame,
            parse_system_time_response,
            set_system_time,
        )

        frame = set_system_time(9, 5, cmd_map=cmd_map)
        response = b"\xfe\xfe" + bytes([frame[3], frame[2]]) + frame[4:]
        parsed = parse_civ_frame(response)
        hour, minute = parse_system_time_response(parsed)
        assert (hour, minute) == (9, 5)

    # --- UTC Offset (0x1A 0x05 0x01 0x62) ---

    def test_get_utc_offset_frame(self, cmd_map) -> None:
        from rigplane.commands import get_utc_offset

        frame = get_utc_offset(cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x01\x62\xfd"

    def test_set_utc_offset_plus_05_30(self, cmd_map) -> None:
        from rigplane.commands import set_utc_offset

        frame = set_utc_offset(5, 30, False, cmd_map=cmd_map)
        # FE FE 98 E0 1A 05 01 62 05 30 00 FD
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x01\x62\x05\x30\x00\xfd"

    def test_set_utc_offset_minus_08_00(self, cmd_map) -> None:
        from rigplane.commands import set_utc_offset

        frame = set_utc_offset(8, 0, True, cmd_map=cmd_map)
        # FE FE 98 E0 1A 05 01 62 08 00 01 FD
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x01\x62\x08\x00\x01\xfd"

    def test_set_utc_offset_zero(self, cmd_map) -> None:
        from rigplane.commands import set_utc_offset

        frame = set_utc_offset(0, 0, False, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x01\x62\x00\x00\x00\xfd"

    def test_set_utc_offset_max_positive(self, cmd_map) -> None:
        from rigplane.commands import set_utc_offset

        frame = set_utc_offset(14, 0, False, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x1a\x05\x01\x62\x14\x00\x00\xfd"

    def test_set_utc_offset_rejects_invalid_hours(self, cmd_map) -> None:
        from rigplane.commands import set_utc_offset

        with pytest.raises(ValueError, match="hours must be 0-14"):
            set_utc_offset(15, 0, False, cmd_map=cmd_map)

    def test_set_utc_offset_rejects_invalid_minutes(self, cmd_map) -> None:
        from rigplane.commands import set_utc_offset

        with pytest.raises(ValueError, match="minutes must be 0/15/30/45"):
            set_utc_offset(5, 10, False, cmd_map=cmd_map)
        with pytest.raises(ValueError, match="minutes must be 0/15/30/45"):
            set_utc_offset(5, 60, False, cmd_map=cmd_map)

    def test_parse_utc_offset_response_positive(self) -> None:
        from rigplane.commands import parse_civ_frame, parse_utc_offset_response

        # +05:30
        civ = b"\xfe\xfe\xe0\x98\x1a\x05\x01\x62\x05\x30\x00\xfd"
        frame = parse_civ_frame(civ)
        hours, minutes, is_negative = parse_utc_offset_response(frame)
        assert hours == 5
        assert minutes == 30
        assert is_negative is False

    def test_parse_utc_offset_response_negative(self) -> None:
        from rigplane.commands import parse_civ_frame, parse_utc_offset_response

        # -08:00
        civ = b"\xfe\xfe\xe0\x98\x1a\x05\x01\x62\x08\x00\x01\xfd"
        frame = parse_civ_frame(civ)
        hours, minutes, is_negative = parse_utc_offset_response(frame)
        assert hours == 8
        assert minutes == 0
        assert is_negative is True

    def test_utc_offset_roundtrip(self, cmd_map) -> None:
        from rigplane.commands import (
            parse_civ_frame,
            parse_utc_offset_response,
            set_utc_offset,
        )

        frame = set_utc_offset(9, 45, True, cmd_map=cmd_map)
        response = b"\xfe\xfe" + bytes([frame[3], frame[2]]) + frame[4:]
        parsed = parse_civ_frame(response)
        hours, minutes, is_negative = parse_utc_offset_response(parsed)
        assert (hours, minutes, is_negative) == (9, 45, True)

    # --- Speech (0x13) ---
    #
    # commands/speech.py migrated onto the bound command map in MOR-2008
    # (batch 1): ``get_speech`` now requires ``cmd_map``. IC-7610's own
    # ``get_speech`` tuple is byte-identical to the deleted fallback's
    # ``0x13``, so the expected frames below are unchanged.

    def test_speech_all(self, cmd_map) -> None:
        from rigplane.commands import get_speech

        frame = get_speech(0, cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x13\x00\xfd"

    def test_speech_freq(self, cmd_map) -> None:
        from rigplane.commands import get_speech

        frame = get_speech(1, cmd_map=cmd_map)
        assert b"\x13\x01" in frame

    def test_speech_mode(self, cmd_map) -> None:
        from rigplane.commands import get_speech

        frame = get_speech(2, cmd_map=cmd_map)
        assert b"\x13\x02" in frame

    def test_speech_invalid(self, cmd_map) -> None:
        from rigplane.commands import get_speech

        with pytest.raises(ValueError, match="0, 1, or 2"):
            get_speech(3, cmd_map=cmd_map)

    def test_speech_requires_cmd_map(self) -> None:
        """cmd_map is required keyword-only -- MOR-2006 Q6's API break."""
        from rigplane.commands import get_speech

        with pytest.raises(TypeError, match="MOR-2006"):
            get_speech()  # type: ignore[call-arg]

    def test_speech_rejects_explicit_none_the_same_way(self, cmd_map) -> None:
        """An explicit ``cmd_map=None`` must hit the same Q6 explanation as
        omitting it entirely."""
        from rigplane import IC_7610_ADDR
        from rigplane.commands import get_speech

        with pytest.raises(TypeError, match="MOR-2006"):
            get_speech(0, to_addr=IC_7610_ADDR, cmd_map=None)

    def test_speech_cmd_map_prefers_set_speech_key(self) -> None:
        """Rig profiles may expose set_speech (wfview Set-only) instead of get_speech."""
        from rigplane import IC_7610_ADDR
        from rigplane.command_map import CommandMap
        from rigplane.commands import get_speech

        cm_get = CommandMap({"get_speech": (0x13,)})
        cm_set = CommandMap({"set_speech": (0x13,)})
        assert get_speech(0, to_addr=IC_7610_ADDR, cmd_map=cm_set) == get_speech(
            0, to_addr=IC_7610_ADDR, cmd_map=cm_get
        )

    # --- Transceiver ID (0x19 0x00) ---

    def test_get_transceiver_id(self, cmd_map) -> None:
        from rigplane.commands import get_transceiver_id

        frame = get_transceiver_id(cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x19\x00\xfd"

    # --- XFC Status (0x1C 0x02) ---

    def test_get_xfc_status(self, cmd_map) -> None:
        from rigplane.commands import get_xfc_status

        frame = get_xfc_status(cmd_map=cmd_map)
        assert frame == b"\xfe\xfe\x98\xe0\x1c\x02\xfd"

    def test_set_xfc_status_on(self, cmd_map) -> None:
        from rigplane.commands import set_xfc_status

        frame = set_xfc_status(True, cmd_map=cmd_map)
        assert b"\x1c\x02\x01" in frame

    def test_set_xfc_status_off(self, cmd_map) -> None:
        from rigplane.commands import set_xfc_status

        frame = set_xfc_status(False, cmd_map=cmd_map)
        assert b"\x1c\x02\x00" in frame


class TestFallbackAuditRemoved:
    """Step Z (cmd-map epic) deletes the Step 1 fallback-audit scaffolding.

    Guards against the three pieces `commands/LAYER.md` named for deletion
    coming back: the module, its env-config knob, and the charter-exception
    section that documented it.
    """

    def test_fallback_audit_module_is_gone(self) -> None:
        with pytest.raises(ModuleNotFoundError):
            import rigplane.commands._fallback_audit  # noqa: F401

    def test_env_config_knob_is_gone(self) -> None:
        import rigplane.core.env_config as env_config

        assert not hasattr(env_config, "get_command_fallback_audit_enabled")
        assert "get_command_fallback_audit_enabled" not in env_config.__all__

    def test_layer_md_charter_exception_is_gone(self) -> None:
        layer_md = (
            Path(__file__).resolve().parents[1] / "src/rigplane/commands/LAYER.md"
        )
        text = layer_md.read_text()
        assert "_fallback_audit" not in text
        assert "Charter exception: Step 1 measurement hook" not in text


class TestScopeSpanPresetsAreCallerSupplied:
    """The span table reaches `commands/scope.py` as an argument, not as a
    module constant.

    ``commands/`` may not import ``profiles/`` (``.importlinter``), so
    ``parse_scope_span_response`` and ``scope_set_span`` take the presets
    as a parameter and every caller reads
    ``RadioProfile.scope_span_presets_hz`` off the resolved profile.
    """

    @pytest.fixture()
    def cmd_map(self):
        return load_rig(RIG_DIR / "ic7610.toml").to_command_map()

    @pytest.mark.parametrize("index", range(len(_SPAN_PRESETS_HZ)))
    def test_decode_and_encode_match_the_pre_change_outputs(
        self, cmd_map, index: int
    ) -> None:
        """Every preset decodes to its index and encodes back to its BCD
        payload, matching the outputs recorded at c23d8cbd (the commit
        before the constant was deleted) by calling the then-current
        ``parse_scope_span_response``/``scope_set_span`` over all eight
        presets. The eight ``encode=`` frames recorded there are the eight
        ``expected_frame`` values below."""
        from rigplane.commands import parse_scope_span_response, scope_set_span
        from rigplane.types import bcd_encode

        hz = _SPAN_PRESETS_HZ[index]

        reply = CivFrame(0xE0, 0x98, 0x27, 0x15, b"\x00" + bcd_encode(hz))
        assert parse_scope_span_response(reply, _SPAN_PRESETS_HZ) == (0, index)

        # to_addr is bound to IC_7610_ADDR module-wide by
        # bind_default_addr_module above, matching the recorded frames.
        expected_frame = bytes.fromhex("fefe98e02715") + bcd_encode(hz) + b"\xfd"
        assert (
            scope_set_span(index, cmd_map=cmd_map, presets=_SPAN_PRESETS_HZ)
            == expected_frame
        )

    @pytest.mark.parametrize("preset_hz", _SPAN_PRESETS_HZ)
    @pytest.mark.parametrize("offset", ["minus1", "plus1", "half", "double"])
    def test_near_miss_widths_decode_exactly_as_before(
        self, preset_hz: int, offset: str
    ) -> None:
        """Each preset +/-1 Hz, halved and doubled: an exact table member
        decodes to its own index (the table is roughly 2x-spaced, so some
        halves and doubles ARE other presets), and anything else raises.
        Both branches were recorded at c23d8cbd over these same 32 inputs
        and are reproduced here."""
        from rigplane.commands import parse_scope_span_response
        from rigplane.types import bcd_encode

        value = {
            "minus1": preset_hz - 1,
            "plus1": preset_hz + 1,
            "half": preset_hz // 2,
            "double": preset_hz * 2,
        }[offset]
        frame = CivFrame(0xE0, 0x98, 0x27, 0x15, b"\x00" + bcd_encode(value))

        if value in _SPAN_PRESETS_HZ:
            assert parse_scope_span_response(frame, _SPAN_PRESETS_HZ) == (
                0,
                _SPAN_PRESETS_HZ.index(value),
            )
        else:
            with pytest.raises(
                ValueError, match=f"Unknown scope span frequency {value}"
            ):
                parse_scope_span_response(frame, _SPAN_PRESETS_HZ)

    def test_a_different_preset_table_moves_the_decoded_index(self) -> None:
        """The presets argument -- not any table inside `commands/scope.py`
        -- decides the index. 25000 Hz is index 3 in the shipped table and
        index 1 in this one; the SET encoder follows the same table."""
        from rigplane.commands import parse_scope_span_response
        from rigplane.types import bcd_encode

        other = (10_000, 25_000, 100_000)
        frame = CivFrame(0xE0, 0x98, 0x27, 0x15, b"\x00" + bcd_encode(25_000))

        assert parse_scope_span_response(frame, _SPAN_PRESETS_HZ) == (0, 3)
        assert parse_scope_span_response(frame, other) == (0, 1)

    def test_a_different_preset_table_moves_the_encoded_payload(self, cmd_map) -> None:
        from rigplane.commands import scope_set_span
        from rigplane.types import bcd_encode

        other = (10_000, 25_000, 100_000)
        frame = scope_set_span(1, cmd_map=cmd_map, presets=other)
        assert frame[6:11] == bcd_encode(25_000)

    def test_a_shorter_table_bounds_both_directions(self, cmd_map) -> None:
        """The legal index range comes from the table's length, in the
        one-byte reply branch as well as in the SET encoder."""
        from rigplane.commands import parse_scope_span_response, scope_set_span

        other = (10_000, 25_000, 100_000)
        with pytest.raises(ValueError, match="scope span must be 0-2"):
            scope_set_span(3, cmd_map=cmd_map, presets=other)
        with pytest.raises(ValueError, match="scope span must be 0-2"):
            parse_scope_span_response(CivFrame(0xE0, 0x98, 0x27, 0x15, b"\x03"), other)

    def test_a_profile_declaring_no_presets_decodes_and_encodes_nothing(
        self, cmd_map
    ) -> None:
        """``RadioProfile.scope_span_presets_hz`` defaults to ``()``. Both
        directions then refuse through the errors they already raised --
        the BCD branch's "Unknown scope span frequency" and
        ``_validate_scope_range``'s message -- rather than a new sentinel.
        No shipped profile is in this state: at this commit the four rigs
        declaring ``get_scope_span`` (ic7300, ic705, ic7610, ic9700) are
        exactly the four declaring ``[scope].span_presets_hz``.
        ``test_rig_loader.py: TestScopeSpanPresets`` pins the values those
        four declare, not that correspondence."""
        from rigplane.commands import parse_scope_span_response, scope_set_span
        from rigplane.types import bcd_encode

        frame = CivFrame(0xE0, 0x98, 0x27, 0x15, b"\x00" + bcd_encode(25_000))
        with pytest.raises(ValueError, match="Unknown scope span frequency 25000"):
            parse_scope_span_response(frame, ())
        with pytest.raises(ValueError, match="scope span must be 0--1"):
            scope_set_span(0, cmd_map=cmd_map, presets=())

    def test_shipped_ic7610_profile_supplies_the_same_table(self) -> None:
        """The presets the production caller passes are the ones these
        tests pin: `runtime/_scope_runtime.py: ScopeRuntimeMixin` reads
        ``self._profile.scope_span_presets_hz``."""
        rig = load_rig(RIG_DIR / "ic7610.toml")
        assert rig.to_profile().scope_span_presets_hz == _SPAN_PRESETS_HZ
