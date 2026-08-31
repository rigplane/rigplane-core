"""Unit tests for operator toggle commands (Issue #131).

commands/dsp.py migrated onto the bound command map in MOR-2008 (batch
3): every builder this file exercises now requires ``cmd_map``, with no
hardcoded fallback left. The module-level ``cmd_map`` fixture below binds
IC-7610's real map -- ``rigs/ic7610.toml`` declares the same wire tuples
the deleted fallback used to build for every builder here (zero
divergence, confirmed by ``tests/command_map_parity_divergences.txt``
being empty), so passing it changes no expected frame in this file.
"""

from pathlib import Path

import pytest

from rigplane import commands
from rigplane import IC_7610_ADDR
from rigplane.commands import (
    CONTROLLER_ADDR,
    RECEIVER_MAIN,
    RECEIVER_SUB,
    parse_bool_response,
    parse_level_response,
)
from rigplane.rig_loader import load_rig
from rigplane.types import AgcMode, AudioPeakFilter, BreakInMode, CivFrame
from _command_test_helpers import bind_default_addr_globals

RIG_DIR = Path(__file__).resolve().parents[1] / "rigs"

bind_default_addr_globals(globals(), to_addr=IC_7610_ADDR)


@pytest.fixture()
def cmd_map():
    rig = load_rig(RIG_DIR / "ic7610.toml")
    return rig.to_command_map()


# CI-V frame constants
_PREAMBLE = b"\xfe\xfe"
_TERMINATOR = b"\xfd"
_CMD_PREAMP = 0x16
_CMD_CMD29 = 0x29

# Sub-command bytes under 0x16
_SUB_AGC = 0x12
_SUB_AUDIO_PEAK_FILTER = 0x32
_SUB_AUTO_NOTCH = 0x41
_SUB_COMPRESSOR = 0x44
_SUB_MONITOR = 0x45
_SUB_VOX = 0x46
_SUB_BREAK_IN = 0x47
_SUB_MANUAL_NOTCH = 0x48
_SUB_TWIN_PEAK_FILTER = 0x4F
_SUB_DIAL_LOCK = 0x50


def _simple_get(sub: int) -> bytes:
    """Expected bytes for a simple (non-cmd29) get frame."""
    return (
        _PREAMBLE
        + bytes([IC_7610_ADDR, CONTROLLER_ADDR, _CMD_PREAMP, sub])
        + _TERMINATOR
    )


def _simple_set(sub: int, value: int) -> bytes:
    """Expected bytes for a simple (non-cmd29) set frame."""
    return (
        _PREAMBLE
        + bytes([IC_7610_ADDR, CONTROLLER_ADDR, _CMD_PREAMP, sub, value])
        + _TERMINATOR
    )


def _cmd29_get(sub: int, receiver: int = RECEIVER_MAIN) -> bytes:
    """Expected bytes for a cmd29-wrapped get frame."""
    return (
        _PREAMBLE
        + bytes([IC_7610_ADDR, CONTROLLER_ADDR, _CMD_CMD29, receiver, _CMD_PREAMP, sub])
        + _TERMINATOR
    )


def _cmd29_set(sub: int, value: int, receiver: int = RECEIVER_MAIN) -> bytes:
    """Expected bytes for a cmd29-wrapped set frame."""
    return (
        _PREAMBLE
        + bytes(
            [
                IC_7610_ADDR,
                CONTROLLER_ADDR,
                _CMD_CMD29,
                receiver,
                _CMD_PREAMP,
                sub,
                value,
            ]
        )
        + _TERMINATOR
    )


def _response_frame(sub: int, data: bytes) -> CivFrame:
    """Build a CivFrame as a radio would return for command 0x16."""
    return CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=IC_7610_ADDR,
        command=_CMD_PREAMP,
        sub=sub,
        data=data,
    )


# ---------------------------------------------------------------------------
# AGC Status (cmd29, enum: AgcMode)
# ---------------------------------------------------------------------------


class TestAGCStatus:
    """Tests for get_agc / set_agc.

    MOR-1537: the builder's ``command29`` default is ``True`` (matching every
    other cmd29-eligible builder in this module, e.g. ``TestAudioPeakFilter``)
    — it no longer derives the wrap decision from ``receiver`` internally.
    The profile-aware caller (``IcomRadio.get_agc``/``set_agc``) is
    responsible for computing and passing the actual
    ``self._profile.supports_cmd29(0x16, 0x12)`` value; see
    ``tests/test_mor1537_cmd29_gating.py`` for that gating behavior,
    including the IC-7610 MAIN-receiver behavior change this pinned.
    """

    def test_get_agc_builds_correct_frame(self, cmd_map) -> None:
        assert commands.get_agc(cmd_map=cmd_map) == _cmd29_get(_SUB_AGC, RECEIVER_MAIN)

    def test_get_agc_default_is_main(self, cmd_map) -> None:
        assert commands.get_agc(cmd_map=cmd_map) == commands.get_agc(
            receiver=RECEIVER_MAIN, cmd_map=cmd_map
        )

    def test_get_agc_sub_receiver_builds_cmd29_frame(self, cmd_map) -> None:
        assert commands.get_agc(receiver=RECEIVER_SUB, cmd_map=cmd_map) == _cmd29_get(
            _SUB_AGC, RECEIVER_SUB
        )

    def test_get_agc_command29_false_builds_plain_frame(self, cmd_map) -> None:
        assert commands.get_agc(command29=False, cmd_map=cmd_map) == _simple_get(
            _SUB_AGC
        )

    def test_set_agc_fast_builds_correct_frame(self, cmd_map) -> None:
        assert commands.set_agc(AgcMode.FAST, cmd_map=cmd_map) == _cmd29_set(
            _SUB_AGC, 0x01, RECEIVER_MAIN
        )

    def test_set_agc_mid_builds_correct_frame(self, cmd_map) -> None:
        assert commands.set_agc(AgcMode.MID, cmd_map=cmd_map) == _cmd29_set(
            _SUB_AGC, 0x02, RECEIVER_MAIN
        )

    def test_set_agc_slow_builds_correct_frame(self, cmd_map) -> None:
        assert commands.set_agc(AgcMode.SLOW, cmd_map=cmd_map) == _cmd29_set(
            _SUB_AGC, 0x03, RECEIVER_MAIN
        )

    def test_set_agc_command29_false_builds_plain_frame(self, cmd_map) -> None:
        assert commands.set_agc(
            AgcMode.SLOW, command29=False, cmd_map=cmd_map
        ) == _simple_set(_SUB_AGC, 0x03)

    def test_set_agc_sub_receiver_builds_cmd29_frame(self, cmd_map) -> None:
        assert commands.set_agc(
            AgcMode.MID, receiver=RECEIVER_SUB, cmd_map=cmd_map
        ) == _cmd29_set(_SUB_AGC, 0x02, RECEIVER_SUB)

    def test_set_agc_accepts_int(self, cmd_map) -> None:
        assert commands.set_agc(1, cmd_map=cmd_map) == commands.set_agc(
            AgcMode.FAST, cmd_map=cmd_map
        )

    def test_set_agc_builder_accepts_values_outside_the_ic7610_enum(
        self, cmd_map
    ) -> None:
        """MOR-1522: the raw wire-command builder no longer polices the
        IC-7610 FAST/MID/SLOW enum's range — which AGC values are legal is
        a per-profile domain (e.g. the X6200 legitimately sends 0=OFF and
        3=AUTO), enforced one layer up in ``IcomRadio.set_agc`` /
        ``YaesuCatRadio.set_agc`` against the profile's declared
        ``[agc] modes`` (see ``TestAgcDomainValidation`` in
        ``tests/test_radio.py``). This builder only encodes the raw
        single-BCD-byte value.
        """
        assert commands.set_agc(0, cmd_map=cmd_map) == _cmd29_set(
            _SUB_AGC, 0x00, RECEIVER_MAIN
        )
        assert commands.set_agc(4, cmd_map=cmd_map) == _cmd29_set(
            _SUB_AGC, 0x04, RECEIVER_MAIN
        )

    def test_set_agc_rejects_value_outside_single_bcd_byte_range(self, cmd_map) -> None:
        with pytest.raises(ValueError):
            commands.set_agc(999, cmd_map=cmd_map)

    def test_parse_agc_response_fast(self) -> None:
        frame = _response_frame(_SUB_AGC, b"\x01")
        value = parse_level_response(
            frame, command=_CMD_PREAMP, sub=_SUB_AGC, bcd_bytes=1
        )
        assert AgcMode(value) == AgcMode.FAST

    def test_parse_agc_response_slow(self) -> None:
        frame = _response_frame(_SUB_AGC, b"\x03")
        value = parse_level_response(
            frame, command=_CMD_PREAMP, sub=_SUB_AGC, bcd_bytes=1
        )
        assert AgcMode(value) == AgcMode.SLOW

    def test_get_agc_custom_addresses(self, cmd_map) -> None:
        frame = commands.get_agc(to_addr=0xA4, from_addr=0xE1, cmd_map=cmd_map)
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1


# ---------------------------------------------------------------------------
# Audio Peak Filter (cmd29, enum: AudioPeakFilter)
# ---------------------------------------------------------------------------


class TestAudioPeakFilter:
    """Tests for get_audio_peak_filter / set_audio_peak_filter."""

    def test_get_audio_peak_filter_main_receiver(self, cmd_map) -> None:
        assert commands.get_audio_peak_filter(
            receiver=RECEIVER_MAIN, cmd_map=cmd_map
        ) == _cmd29_get(_SUB_AUDIO_PEAK_FILTER, RECEIVER_MAIN)

    def test_get_audio_peak_filter_sub_receiver(self, cmd_map) -> None:
        assert commands.get_audio_peak_filter(
            receiver=RECEIVER_SUB, cmd_map=cmd_map
        ) == _cmd29_get(_SUB_AUDIO_PEAK_FILTER, RECEIVER_SUB)

    def test_set_audio_peak_filter_off_main(self, cmd_map) -> None:
        assert commands.set_audio_peak_filter(
            AudioPeakFilter.OFF, cmd_map=cmd_map
        ) == _cmd29_set(_SUB_AUDIO_PEAK_FILTER, 0x00, RECEIVER_MAIN)

    def test_set_audio_peak_filter_nar_main(self, cmd_map) -> None:
        assert commands.set_audio_peak_filter(
            AudioPeakFilter.NAR, cmd_map=cmd_map
        ) == _cmd29_set(_SUB_AUDIO_PEAK_FILTER, 0x03, RECEIVER_MAIN)

    def test_set_audio_peak_filter_wide_sub(self, cmd_map) -> None:
        assert commands.set_audio_peak_filter(
            AudioPeakFilter.WIDE, receiver=RECEIVER_SUB, cmd_map=cmd_map
        ) == _cmd29_set(_SUB_AUDIO_PEAK_FILTER, 0x01, RECEIVER_SUB)

    def test_set_audio_peak_filter_accepts_int(self, cmd_map) -> None:
        assert commands.set_audio_peak_filter(
            0, cmd_map=cmd_map
        ) == commands.set_audio_peak_filter(AudioPeakFilter.OFF, cmd_map=cmd_map)

    def test_set_audio_peak_filter_rejects_invalid(self, cmd_map) -> None:
        with pytest.raises(ValueError):
            commands.set_audio_peak_filter(4, cmd_map=cmd_map)

    def test_parse_audio_peak_filter_off(self) -> None:
        frame = _response_frame(_SUB_AUDIO_PEAK_FILTER, b"\x00")
        value = parse_level_response(
            frame, command=_CMD_PREAMP, sub=_SUB_AUDIO_PEAK_FILTER, bcd_bytes=1
        )
        assert AudioPeakFilter(value) == AudioPeakFilter.OFF

    def test_parse_audio_peak_filter_mid(self) -> None:
        frame = _response_frame(_SUB_AUDIO_PEAK_FILTER, b"\x02")
        value = parse_level_response(
            frame, command=_CMD_PREAMP, sub=_SUB_AUDIO_PEAK_FILTER, bcd_bytes=1
        )
        assert AudioPeakFilter(value) == AudioPeakFilter.MID

    def test_get_audio_peak_filter_default_is_main(self, cmd_map) -> None:
        assert commands.get_audio_peak_filter(
            cmd_map=cmd_map
        ) == commands.get_audio_peak_filter(receiver=RECEIVER_MAIN, cmd_map=cmd_map)


# ---------------------------------------------------------------------------
# Auto Notch (cmd29, boolean)
# ---------------------------------------------------------------------------


class TestAutoNotch:
    """Tests for get_auto_notch / set_auto_notch."""

    def test_get_auto_notch_main_receiver(self, cmd_map) -> None:
        assert commands.get_auto_notch(
            receiver=RECEIVER_MAIN, cmd_map=cmd_map
        ) == _cmd29_get(_SUB_AUTO_NOTCH, RECEIVER_MAIN)

    def test_get_auto_notch_sub_receiver(self, cmd_map) -> None:
        assert commands.get_auto_notch(
            receiver=RECEIVER_SUB, cmd_map=cmd_map
        ) == _cmd29_get(_SUB_AUTO_NOTCH, RECEIVER_SUB)

    def test_set_auto_notch_on_main(self, cmd_map) -> None:
        assert commands.set_auto_notch(True, cmd_map=cmd_map) == _cmd29_set(
            _SUB_AUTO_NOTCH, 0x01, RECEIVER_MAIN
        )

    def test_set_auto_notch_off_main(self, cmd_map) -> None:
        assert commands.set_auto_notch(False, cmd_map=cmd_map) == _cmd29_set(
            _SUB_AUTO_NOTCH, 0x00, RECEIVER_MAIN
        )

    def test_set_auto_notch_on_sub(self, cmd_map) -> None:
        assert commands.set_auto_notch(
            True, receiver=RECEIVER_SUB, cmd_map=cmd_map
        ) == _cmd29_set(_SUB_AUTO_NOTCH, 0x01, RECEIVER_SUB)

    def test_set_auto_notch_off_sub(self, cmd_map) -> None:
        assert commands.set_auto_notch(
            False, receiver=RECEIVER_SUB, cmd_map=cmd_map
        ) == _cmd29_set(_SUB_AUTO_NOTCH, 0x00, RECEIVER_SUB)

    def test_parse_auto_notch_on(self) -> None:
        frame = _response_frame(_SUB_AUTO_NOTCH, b"\x01")
        assert (
            parse_bool_response(frame, command=_CMD_PREAMP, sub=_SUB_AUTO_NOTCH) is True
        )

    def test_parse_auto_notch_off(self) -> None:
        frame = _response_frame(_SUB_AUTO_NOTCH, b"\x00")
        assert (
            parse_bool_response(frame, command=_CMD_PREAMP, sub=_SUB_AUTO_NOTCH)
            is False
        )

    def test_get_auto_notch_default_is_main(self, cmd_map) -> None:
        assert commands.get_auto_notch(cmd_map=cmd_map) == commands.get_auto_notch(
            receiver=RECEIVER_MAIN, cmd_map=cmd_map
        )


# ---------------------------------------------------------------------------
# Compressor Status (no cmd29, boolean)
# ---------------------------------------------------------------------------


class TestCompressor:
    """Tests for get_compressor / set_compressor."""

    def test_get_compressor_builds_correct_frame(self, cmd_map) -> None:
        assert commands.get_compressor(cmd_map=cmd_map) == _simple_get(_SUB_COMPRESSOR)

    def test_set_compressor_on_builds_correct_frame(self, cmd_map) -> None:
        assert commands.set_compressor(True, cmd_map=cmd_map) == _simple_set(
            _SUB_COMPRESSOR, 0x01
        )

    def test_set_compressor_off_builds_correct_frame(self, cmd_map) -> None:
        assert commands.set_compressor(False, cmd_map=cmd_map) == _simple_set(
            _SUB_COMPRESSOR, 0x00
        )

    def test_parse_compressor_on(self) -> None:
        frame = _response_frame(_SUB_COMPRESSOR, b"\x01")
        assert (
            parse_bool_response(frame, command=_CMD_PREAMP, sub=_SUB_COMPRESSOR) is True
        )

    def test_parse_compressor_off(self) -> None:
        frame = _response_frame(_SUB_COMPRESSOR, b"\x00")
        assert (
            parse_bool_response(frame, command=_CMD_PREAMP, sub=_SUB_COMPRESSOR)
            is False
        )

    def test_get_compressor_custom_addresses(self, cmd_map) -> None:
        frame = commands.get_compressor(to_addr=0xA4, from_addr=0xE1, cmd_map=cmd_map)
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1


# ---------------------------------------------------------------------------
# Monitor Status (no cmd29, boolean)
# ---------------------------------------------------------------------------


class TestMonitor:
    """Tests for get_monitor / set_monitor."""

    def test_get_monitor_builds_correct_frame(self, cmd_map) -> None:
        assert commands.get_monitor(cmd_map=cmd_map) == _simple_get(_SUB_MONITOR)

    def test_set_monitor_on_builds_correct_frame(self, cmd_map) -> None:
        assert commands.set_monitor(True, cmd_map=cmd_map) == _simple_set(
            _SUB_MONITOR, 0x01
        )

    def test_set_monitor_off_builds_correct_frame(self, cmd_map) -> None:
        assert commands.set_monitor(False, cmd_map=cmd_map) == _simple_set(
            _SUB_MONITOR, 0x00
        )

    def test_parse_monitor_on(self) -> None:
        frame = _response_frame(_SUB_MONITOR, b"\x01")
        assert parse_bool_response(frame, command=_CMD_PREAMP, sub=_SUB_MONITOR) is True

    def test_parse_monitor_off(self) -> None:
        frame = _response_frame(_SUB_MONITOR, b"\x00")
        assert (
            parse_bool_response(frame, command=_CMD_PREAMP, sub=_SUB_MONITOR) is False
        )

    def test_monitor_sub_byte_is_distinct_from_compressor(self, cmd_map) -> None:
        assert commands.get_monitor(cmd_map=cmd_map) != commands.get_compressor(
            cmd_map=cmd_map
        )


# ---------------------------------------------------------------------------
# Vox Status (no cmd29, boolean)
# ---------------------------------------------------------------------------


class TestVox:
    """Tests for get_vox / set_vox."""

    def test_get_vox_builds_correct_frame(self, cmd_map) -> None:
        assert commands.get_vox(cmd_map=cmd_map) == _simple_get(_SUB_VOX)

    def test_set_vox_on_builds_correct_frame(self, cmd_map) -> None:
        assert commands.set_vox(True, cmd_map=cmd_map) == _simple_set(_SUB_VOX, 0x01)

    def test_set_vox_off_builds_correct_frame(self, cmd_map) -> None:
        assert commands.set_vox(False, cmd_map=cmd_map) == _simple_set(_SUB_VOX, 0x00)

    def test_parse_vox_on(self) -> None:
        frame = _response_frame(_SUB_VOX, b"\x01")
        assert parse_bool_response(frame, command=_CMD_PREAMP, sub=_SUB_VOX) is True

    def test_parse_vox_off(self) -> None:
        frame = _response_frame(_SUB_VOX, b"\x00")
        assert parse_bool_response(frame, command=_CMD_PREAMP, sub=_SUB_VOX) is False

    def test_get_vox_custom_addresses(self, cmd_map) -> None:
        frame = commands.get_vox(to_addr=0xA4, from_addr=0xE1, cmd_map=cmd_map)
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1


# ---------------------------------------------------------------------------
# Break-In Status (no cmd29, enum: BreakInMode)
# ---------------------------------------------------------------------------


class TestBreakIn:
    """Tests for get_break_in / set_break_in."""

    def test_get_break_in_builds_correct_frame(self, cmd_map) -> None:
        assert commands.get_break_in(cmd_map=cmd_map) == _simple_get(_SUB_BREAK_IN)

    def test_set_break_in_off_builds_correct_frame(self, cmd_map) -> None:
        assert commands.set_break_in(BreakInMode.OFF, cmd_map=cmd_map) == _simple_set(
            _SUB_BREAK_IN, 0x00
        )

    def test_set_break_in_semi_builds_correct_frame(self, cmd_map) -> None:
        assert commands.set_break_in(BreakInMode.SEMI, cmd_map=cmd_map) == _simple_set(
            _SUB_BREAK_IN, 0x01
        )

    def test_set_break_in_full_builds_correct_frame(self, cmd_map) -> None:
        assert commands.set_break_in(BreakInMode.FULL, cmd_map=cmd_map) == _simple_set(
            _SUB_BREAK_IN, 0x02
        )

    def test_set_break_in_accepts_int(self, cmd_map) -> None:
        assert commands.set_break_in(0, cmd_map=cmd_map) == commands.set_break_in(
            BreakInMode.OFF, cmd_map=cmd_map
        )

    def test_set_break_in_builder_accepts_values_outside_the_off_semi_full_enum(
        self,
        cmd_map,
    ) -> None:
        """MOR-1534: the raw wire-command builder no longer polices the
        IC-7610 OFF/SEMI/FULL enum's range — which break-in values are
        legal is a per-profile ``[break_in] values`` domain, enforced one
        layer up in ``CoreRadio.set_break_in`` (see
        ``TestBreakInDomainValidation`` in ``tests/test_radio.py``). This
        builder only encodes the raw single-BCD-byte value.
        """
        assert commands.set_break_in(3, cmd_map=cmd_map) == _simple_set(
            _SUB_BREAK_IN, 0x03
        )

    def test_set_break_in_rejects_invalid_enum_int(self, cmd_map) -> None:
        with pytest.raises(ValueError):
            commands.set_break_in(999, cmd_map=cmd_map)

    def test_parse_break_in_off(self) -> None:
        frame = _response_frame(_SUB_BREAK_IN, b"\x00")
        value = parse_level_response(
            frame, command=_CMD_PREAMP, sub=_SUB_BREAK_IN, bcd_bytes=1
        )
        assert BreakInMode(value) == BreakInMode.OFF

    def test_parse_break_in_semi(self) -> None:
        frame = _response_frame(_SUB_BREAK_IN, b"\x01")
        value = parse_level_response(
            frame, command=_CMD_PREAMP, sub=_SUB_BREAK_IN, bcd_bytes=1
        )
        assert BreakInMode(value) == BreakInMode.SEMI

    def test_parse_break_in_full(self) -> None:
        frame = _response_frame(_SUB_BREAK_IN, b"\x02")
        value = parse_level_response(
            frame, command=_CMD_PREAMP, sub=_SUB_BREAK_IN, bcd_bytes=1
        )
        assert BreakInMode(value) == BreakInMode.FULL


# ---------------------------------------------------------------------------
# Manual Notch (cmd29, boolean)
# ---------------------------------------------------------------------------


class TestManualNotch:
    """Tests for get_manual_notch / set_manual_notch."""

    def test_get_manual_notch_main_receiver(self, cmd_map) -> None:
        assert commands.get_manual_notch(
            receiver=RECEIVER_MAIN, cmd_map=cmd_map
        ) == _cmd29_get(_SUB_MANUAL_NOTCH, RECEIVER_MAIN)

    def test_get_manual_notch_sub_receiver(self, cmd_map) -> None:
        assert commands.get_manual_notch(
            receiver=RECEIVER_SUB, cmd_map=cmd_map
        ) == _cmd29_get(_SUB_MANUAL_NOTCH, RECEIVER_SUB)

    def test_set_manual_notch_on_main(self, cmd_map) -> None:
        assert commands.set_manual_notch(True, cmd_map=cmd_map) == _cmd29_set(
            _SUB_MANUAL_NOTCH, 0x01, RECEIVER_MAIN
        )

    def test_set_manual_notch_off_main(self, cmd_map) -> None:
        assert commands.set_manual_notch(False, cmd_map=cmd_map) == _cmd29_set(
            _SUB_MANUAL_NOTCH, 0x00, RECEIVER_MAIN
        )

    def test_set_manual_notch_on_sub(self, cmd_map) -> None:
        assert commands.set_manual_notch(
            True, receiver=RECEIVER_SUB, cmd_map=cmd_map
        ) == _cmd29_set(_SUB_MANUAL_NOTCH, 0x01, RECEIVER_SUB)

    def test_set_manual_notch_off_sub(self, cmd_map) -> None:
        assert commands.set_manual_notch(
            False, receiver=RECEIVER_SUB, cmd_map=cmd_map
        ) == _cmd29_set(_SUB_MANUAL_NOTCH, 0x00, RECEIVER_SUB)

    def test_parse_manual_notch_on(self) -> None:
        frame = _response_frame(_SUB_MANUAL_NOTCH, b"\x01")
        assert (
            parse_bool_response(frame, command=_CMD_PREAMP, sub=_SUB_MANUAL_NOTCH)
            is True
        )

    def test_parse_manual_notch_off(self) -> None:
        frame = _response_frame(_SUB_MANUAL_NOTCH, b"\x00")
        assert (
            parse_bool_response(frame, command=_CMD_PREAMP, sub=_SUB_MANUAL_NOTCH)
            is False
        )

    def test_get_manual_notch_default_is_main(self, cmd_map) -> None:
        assert commands.get_manual_notch(cmd_map=cmd_map) == commands.get_manual_notch(
            receiver=RECEIVER_MAIN, cmd_map=cmd_map
        )


# ---------------------------------------------------------------------------
# Twin Peak Filter (cmd29, boolean)
# ---------------------------------------------------------------------------


class TestTwinPeakFilter:
    """Tests for get_twin_peak_filter / set_twin_peak_filter."""

    def test_get_twin_peak_filter_main_receiver(self, cmd_map) -> None:
        assert commands.get_twin_peak_filter(
            receiver=RECEIVER_MAIN, cmd_map=cmd_map
        ) == _cmd29_get(_SUB_TWIN_PEAK_FILTER, RECEIVER_MAIN)

    def test_get_twin_peak_filter_sub_receiver(self, cmd_map) -> None:
        assert commands.get_twin_peak_filter(
            receiver=RECEIVER_SUB, cmd_map=cmd_map
        ) == _cmd29_get(_SUB_TWIN_PEAK_FILTER, RECEIVER_SUB)

    def test_set_twin_peak_filter_on_main(self, cmd_map) -> None:
        assert commands.set_twin_peak_filter(True, cmd_map=cmd_map) == _cmd29_set(
            _SUB_TWIN_PEAK_FILTER, 0x01, RECEIVER_MAIN
        )

    def test_set_twin_peak_filter_off_main(self, cmd_map) -> None:
        assert commands.set_twin_peak_filter(False, cmd_map=cmd_map) == _cmd29_set(
            _SUB_TWIN_PEAK_FILTER, 0x00, RECEIVER_MAIN
        )

    def test_set_twin_peak_filter_on_sub(self, cmd_map) -> None:
        assert commands.set_twin_peak_filter(
            True, receiver=RECEIVER_SUB, cmd_map=cmd_map
        ) == _cmd29_set(_SUB_TWIN_PEAK_FILTER, 0x01, RECEIVER_SUB)

    def test_set_twin_peak_filter_off_sub(self, cmd_map) -> None:
        assert commands.set_twin_peak_filter(
            False, receiver=RECEIVER_SUB, cmd_map=cmd_map
        ) == _cmd29_set(_SUB_TWIN_PEAK_FILTER, 0x00, RECEIVER_SUB)

    def test_parse_twin_peak_filter_on(self) -> None:
        frame = _response_frame(_SUB_TWIN_PEAK_FILTER, b"\x01")
        assert (
            parse_bool_response(frame, command=_CMD_PREAMP, sub=_SUB_TWIN_PEAK_FILTER)
            is True
        )

    def test_parse_twin_peak_filter_off(self) -> None:
        frame = _response_frame(_SUB_TWIN_PEAK_FILTER, b"\x00")
        assert (
            parse_bool_response(frame, command=_CMD_PREAMP, sub=_SUB_TWIN_PEAK_FILTER)
            is False
        )

    def test_get_twin_peak_filter_default_is_main(self, cmd_map) -> None:
        assert commands.get_twin_peak_filter(
            cmd_map=cmd_map
        ) == commands.get_twin_peak_filter(receiver=RECEIVER_MAIN, cmd_map=cmd_map)


# ---------------------------------------------------------------------------
# Dial Lock Status (no cmd29, boolean)
# ---------------------------------------------------------------------------


class TestDialLock:
    """Tests for get_dial_lock / set_dial_lock."""

    def test_get_dial_lock_builds_correct_frame(self, cmd_map) -> None:
        assert commands.get_dial_lock(cmd_map=cmd_map) == _simple_get(_SUB_DIAL_LOCK)

    def test_set_dial_lock_on_builds_correct_frame(self, cmd_map) -> None:
        assert commands.set_dial_lock(True, cmd_map=cmd_map) == _simple_set(
            _SUB_DIAL_LOCK, 0x01
        )

    def test_set_dial_lock_off_builds_correct_frame(self, cmd_map) -> None:
        assert commands.set_dial_lock(False, cmd_map=cmd_map) == _simple_set(
            _SUB_DIAL_LOCK, 0x00
        )

    def test_parse_dial_lock_on(self) -> None:
        frame = _response_frame(_SUB_DIAL_LOCK, b"\x01")
        assert (
            parse_bool_response(frame, command=_CMD_PREAMP, sub=_SUB_DIAL_LOCK) is True
        )

    def test_parse_dial_lock_off(self) -> None:
        frame = _response_frame(_SUB_DIAL_LOCK, b"\x00")
        assert (
            parse_bool_response(frame, command=_CMD_PREAMP, sub=_SUB_DIAL_LOCK) is False
        )

    def test_get_dial_lock_custom_addresses(self, cmd_map) -> None:
        frame = commands.get_dial_lock(to_addr=0xA4, from_addr=0xE1, cmd_map=cmd_map)
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1

    def test_dial_lock_sub_byte_is_distinct_from_twin_peak(self, cmd_map) -> None:
        assert commands.get_dial_lock(cmd_map=cmd_map) != commands.get_twin_peak_filter(
            cmd_map=cmd_map
        )
