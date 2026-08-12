"""Unit tests for operator toggle commands (Issue #131)."""

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
from rigplane.types import AgcMode, AudioPeakFilter, BreakInMode, CivFrame
from _command_test_helpers import bind_default_addr_globals

bind_default_addr_globals(globals(), to_addr=IC_7610_ADDR)

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
# AGC Status (no cmd29, enum: AgcMode)
# ---------------------------------------------------------------------------


class TestAGCStatus:
    """Tests for get_agc / set_agc."""

    def test_get_agc_builds_correct_frame(self) -> None:
        assert commands.get_agc() == _simple_get(_SUB_AGC)

    def test_get_agc_sub_receiver_builds_cmd29_frame(self) -> None:
        assert commands.get_agc(receiver=RECEIVER_SUB) == _cmd29_get(
            _SUB_AGC, RECEIVER_SUB
        )

    def test_set_agc_fast_builds_correct_frame(self) -> None:
        assert commands.set_agc(AgcMode.FAST) == _simple_set(_SUB_AGC, 0x01)

    def test_set_agc_mid_builds_correct_frame(self) -> None:
        assert commands.set_agc(AgcMode.MID) == _simple_set(_SUB_AGC, 0x02)

    def test_set_agc_slow_builds_correct_frame(self) -> None:
        assert commands.set_agc(AgcMode.SLOW) == _simple_set(_SUB_AGC, 0x03)

    def test_set_agc_sub_receiver_builds_cmd29_frame(self) -> None:
        assert commands.set_agc(AgcMode.MID, receiver=RECEIVER_SUB) == _cmd29_set(
            _SUB_AGC, 0x02, RECEIVER_SUB
        )

    def test_set_agc_accepts_int(self) -> None:
        assert commands.set_agc(1) == commands.set_agc(AgcMode.FAST)

    def test_set_agc_builder_accepts_values_outside_the_ic7610_enum(self) -> None:
        """MOR-1522: the raw wire-command builder no longer polices the
        IC-7610 FAST/MID/SLOW enum's range — which AGC values are legal is
        a per-profile domain (e.g. the X6200 legitimately sends 0=OFF and
        3=AUTO), enforced one layer up in ``IcomRadio.set_agc`` /
        ``YaesuCatRadio.set_agc`` against the profile's declared
        ``[agc] modes`` (see ``TestAgcDomainValidation`` in
        ``tests/test_radio.py``). This builder only encodes the raw
        single-BCD-byte value.
        """
        assert commands.set_agc(0) == _simple_set(_SUB_AGC, 0x00)
        assert commands.set_agc(4) == _simple_set(_SUB_AGC, 0x04)

    def test_set_agc_rejects_value_outside_single_bcd_byte_range(self) -> None:
        with pytest.raises(ValueError):
            commands.set_agc(999)

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

    def test_get_agc_custom_addresses(self) -> None:
        frame = commands.get_agc(to_addr=0xA4, from_addr=0xE1)
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1


# ---------------------------------------------------------------------------
# Audio Peak Filter (cmd29, enum: AudioPeakFilter)
# ---------------------------------------------------------------------------


class TestAudioPeakFilter:
    """Tests for get_audio_peak_filter / set_audio_peak_filter."""

    def test_get_audio_peak_filter_main_receiver(self) -> None:
        assert commands.get_audio_peak_filter(receiver=RECEIVER_MAIN) == _cmd29_get(
            _SUB_AUDIO_PEAK_FILTER, RECEIVER_MAIN
        )

    def test_get_audio_peak_filter_sub_receiver(self) -> None:
        assert commands.get_audio_peak_filter(receiver=RECEIVER_SUB) == _cmd29_get(
            _SUB_AUDIO_PEAK_FILTER, RECEIVER_SUB
        )

    def test_set_audio_peak_filter_off_main(self) -> None:
        assert commands.set_audio_peak_filter(AudioPeakFilter.OFF) == _cmd29_set(
            _SUB_AUDIO_PEAK_FILTER, 0x00, RECEIVER_MAIN
        )

    def test_set_audio_peak_filter_nar_main(self) -> None:
        assert commands.set_audio_peak_filter(AudioPeakFilter.NAR) == _cmd29_set(
            _SUB_AUDIO_PEAK_FILTER, 0x03, RECEIVER_MAIN
        )

    def test_set_audio_peak_filter_wide_sub(self) -> None:
        assert commands.set_audio_peak_filter(
            AudioPeakFilter.WIDE, receiver=RECEIVER_SUB
        ) == _cmd29_set(_SUB_AUDIO_PEAK_FILTER, 0x01, RECEIVER_SUB)

    def test_set_audio_peak_filter_accepts_int(self) -> None:
        assert commands.set_audio_peak_filter(0) == commands.set_audio_peak_filter(
            AudioPeakFilter.OFF
        )

    def test_set_audio_peak_filter_rejects_invalid(self) -> None:
        with pytest.raises(ValueError):
            commands.set_audio_peak_filter(4)

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

    def test_get_audio_peak_filter_default_is_main(self) -> None:
        assert commands.get_audio_peak_filter() == commands.get_audio_peak_filter(
            receiver=RECEIVER_MAIN
        )


# ---------------------------------------------------------------------------
# Auto Notch (cmd29, boolean)
# ---------------------------------------------------------------------------


class TestAutoNotch:
    """Tests for get_auto_notch / set_auto_notch."""

    def test_get_auto_notch_main_receiver(self) -> None:
        assert commands.get_auto_notch(receiver=RECEIVER_MAIN) == _cmd29_get(
            _SUB_AUTO_NOTCH, RECEIVER_MAIN
        )

    def test_get_auto_notch_sub_receiver(self) -> None:
        assert commands.get_auto_notch(receiver=RECEIVER_SUB) == _cmd29_get(
            _SUB_AUTO_NOTCH, RECEIVER_SUB
        )

    def test_set_auto_notch_on_main(self) -> None:
        assert commands.set_auto_notch(True) == _cmd29_set(
            _SUB_AUTO_NOTCH, 0x01, RECEIVER_MAIN
        )

    def test_set_auto_notch_off_main(self) -> None:
        assert commands.set_auto_notch(False) == _cmd29_set(
            _SUB_AUTO_NOTCH, 0x00, RECEIVER_MAIN
        )

    def test_set_auto_notch_on_sub(self) -> None:
        assert commands.set_auto_notch(True, receiver=RECEIVER_SUB) == _cmd29_set(
            _SUB_AUTO_NOTCH, 0x01, RECEIVER_SUB
        )

    def test_set_auto_notch_off_sub(self) -> None:
        assert commands.set_auto_notch(False, receiver=RECEIVER_SUB) == _cmd29_set(
            _SUB_AUTO_NOTCH, 0x00, RECEIVER_SUB
        )

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

    def test_get_auto_notch_default_is_main(self) -> None:
        assert commands.get_auto_notch() == commands.get_auto_notch(
            receiver=RECEIVER_MAIN
        )


# ---------------------------------------------------------------------------
# Compressor Status (no cmd29, boolean)
# ---------------------------------------------------------------------------


class TestCompressor:
    """Tests for get_compressor / set_compressor."""

    def test_get_compressor_builds_correct_frame(self) -> None:
        assert commands.get_compressor() == _simple_get(_SUB_COMPRESSOR)

    def test_set_compressor_on_builds_correct_frame(self) -> None:
        assert commands.set_compressor(True) == _simple_set(_SUB_COMPRESSOR, 0x01)

    def test_set_compressor_off_builds_correct_frame(self) -> None:
        assert commands.set_compressor(False) == _simple_set(_SUB_COMPRESSOR, 0x00)

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

    def test_get_compressor_custom_addresses(self) -> None:
        frame = commands.get_compressor(to_addr=0xA4, from_addr=0xE1)
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1


# ---------------------------------------------------------------------------
# Monitor Status (no cmd29, boolean)
# ---------------------------------------------------------------------------


class TestMonitor:
    """Tests for get_monitor / set_monitor."""

    def test_get_monitor_builds_correct_frame(self) -> None:
        assert commands.get_monitor() == _simple_get(_SUB_MONITOR)

    def test_set_monitor_on_builds_correct_frame(self) -> None:
        assert commands.set_monitor(True) == _simple_set(_SUB_MONITOR, 0x01)

    def test_set_monitor_off_builds_correct_frame(self) -> None:
        assert commands.set_monitor(False) == _simple_set(_SUB_MONITOR, 0x00)

    def test_parse_monitor_on(self) -> None:
        frame = _response_frame(_SUB_MONITOR, b"\x01")
        assert parse_bool_response(frame, command=_CMD_PREAMP, sub=_SUB_MONITOR) is True

    def test_parse_monitor_off(self) -> None:
        frame = _response_frame(_SUB_MONITOR, b"\x00")
        assert (
            parse_bool_response(frame, command=_CMD_PREAMP, sub=_SUB_MONITOR) is False
        )

    def test_monitor_sub_byte_is_distinct_from_compressor(self) -> None:
        assert commands.get_monitor() != commands.get_compressor()


# ---------------------------------------------------------------------------
# Vox Status (no cmd29, boolean)
# ---------------------------------------------------------------------------


class TestVox:
    """Tests for get_vox / set_vox."""

    def test_get_vox_builds_correct_frame(self) -> None:
        assert commands.get_vox() == _simple_get(_SUB_VOX)

    def test_set_vox_on_builds_correct_frame(self) -> None:
        assert commands.set_vox(True) == _simple_set(_SUB_VOX, 0x01)

    def test_set_vox_off_builds_correct_frame(self) -> None:
        assert commands.set_vox(False) == _simple_set(_SUB_VOX, 0x00)

    def test_parse_vox_on(self) -> None:
        frame = _response_frame(_SUB_VOX, b"\x01")
        assert parse_bool_response(frame, command=_CMD_PREAMP, sub=_SUB_VOX) is True

    def test_parse_vox_off(self) -> None:
        frame = _response_frame(_SUB_VOX, b"\x00")
        assert parse_bool_response(frame, command=_CMD_PREAMP, sub=_SUB_VOX) is False

    def test_get_vox_custom_addresses(self) -> None:
        frame = commands.get_vox(to_addr=0xA4, from_addr=0xE1)
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1


# ---------------------------------------------------------------------------
# Break-In Status (no cmd29, enum: BreakInMode)
# ---------------------------------------------------------------------------


class TestBreakIn:
    """Tests for get_break_in / set_break_in."""

    def test_get_break_in_builds_correct_frame(self) -> None:
        assert commands.get_break_in() == _simple_get(_SUB_BREAK_IN)

    def test_set_break_in_off_builds_correct_frame(self) -> None:
        assert commands.set_break_in(BreakInMode.OFF) == _simple_set(
            _SUB_BREAK_IN, 0x00
        )

    def test_set_break_in_semi_builds_correct_frame(self) -> None:
        assert commands.set_break_in(BreakInMode.SEMI) == _simple_set(
            _SUB_BREAK_IN, 0x01
        )

    def test_set_break_in_full_builds_correct_frame(self) -> None:
        assert commands.set_break_in(BreakInMode.FULL) == _simple_set(
            _SUB_BREAK_IN, 0x02
        )

    def test_set_break_in_accepts_int(self) -> None:
        assert commands.set_break_in(0) == commands.set_break_in(BreakInMode.OFF)

    def test_set_break_in_rejects_value_above_maximum(self) -> None:
        with pytest.raises(ValueError):
            commands.set_break_in(3)

    def test_set_break_in_rejects_invalid_enum_int(self) -> None:
        with pytest.raises(ValueError):
            commands.set_break_in(999)

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

    def test_get_manual_notch_main_receiver(self) -> None:
        assert commands.get_manual_notch(receiver=RECEIVER_MAIN) == _cmd29_get(
            _SUB_MANUAL_NOTCH, RECEIVER_MAIN
        )

    def test_get_manual_notch_sub_receiver(self) -> None:
        assert commands.get_manual_notch(receiver=RECEIVER_SUB) == _cmd29_get(
            _SUB_MANUAL_NOTCH, RECEIVER_SUB
        )

    def test_set_manual_notch_on_main(self) -> None:
        assert commands.set_manual_notch(True) == _cmd29_set(
            _SUB_MANUAL_NOTCH, 0x01, RECEIVER_MAIN
        )

    def test_set_manual_notch_off_main(self) -> None:
        assert commands.set_manual_notch(False) == _cmd29_set(
            _SUB_MANUAL_NOTCH, 0x00, RECEIVER_MAIN
        )

    def test_set_manual_notch_on_sub(self) -> None:
        assert commands.set_manual_notch(True, receiver=RECEIVER_SUB) == _cmd29_set(
            _SUB_MANUAL_NOTCH, 0x01, RECEIVER_SUB
        )

    def test_set_manual_notch_off_sub(self) -> None:
        assert commands.set_manual_notch(False, receiver=RECEIVER_SUB) == _cmd29_set(
            _SUB_MANUAL_NOTCH, 0x00, RECEIVER_SUB
        )

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

    def test_get_manual_notch_default_is_main(self) -> None:
        assert commands.get_manual_notch() == commands.get_manual_notch(
            receiver=RECEIVER_MAIN
        )


# ---------------------------------------------------------------------------
# Twin Peak Filter (cmd29, boolean)
# ---------------------------------------------------------------------------


class TestTwinPeakFilter:
    """Tests for get_twin_peak_filter / set_twin_peak_filter."""

    def test_get_twin_peak_filter_main_receiver(self) -> None:
        assert commands.get_twin_peak_filter(receiver=RECEIVER_MAIN) == _cmd29_get(
            _SUB_TWIN_PEAK_FILTER, RECEIVER_MAIN
        )

    def test_get_twin_peak_filter_sub_receiver(self) -> None:
        assert commands.get_twin_peak_filter(receiver=RECEIVER_SUB) == _cmd29_get(
            _SUB_TWIN_PEAK_FILTER, RECEIVER_SUB
        )

    def test_set_twin_peak_filter_on_main(self) -> None:
        assert commands.set_twin_peak_filter(True) == _cmd29_set(
            _SUB_TWIN_PEAK_FILTER, 0x01, RECEIVER_MAIN
        )

    def test_set_twin_peak_filter_off_main(self) -> None:
        assert commands.set_twin_peak_filter(False) == _cmd29_set(
            _SUB_TWIN_PEAK_FILTER, 0x00, RECEIVER_MAIN
        )

    def test_set_twin_peak_filter_on_sub(self) -> None:
        assert commands.set_twin_peak_filter(True, receiver=RECEIVER_SUB) == _cmd29_set(
            _SUB_TWIN_PEAK_FILTER, 0x01, RECEIVER_SUB
        )

    def test_set_twin_peak_filter_off_sub(self) -> None:
        assert commands.set_twin_peak_filter(
            False, receiver=RECEIVER_SUB
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

    def test_get_twin_peak_filter_default_is_main(self) -> None:
        assert commands.get_twin_peak_filter() == commands.get_twin_peak_filter(
            receiver=RECEIVER_MAIN
        )


# ---------------------------------------------------------------------------
# Dial Lock Status (no cmd29, boolean)
# ---------------------------------------------------------------------------


class TestDialLock:
    """Tests for get_dial_lock / set_dial_lock."""

    def test_get_dial_lock_builds_correct_frame(self) -> None:
        assert commands.get_dial_lock() == _simple_get(_SUB_DIAL_LOCK)

    def test_set_dial_lock_on_builds_correct_frame(self) -> None:
        assert commands.set_dial_lock(True) == _simple_set(_SUB_DIAL_LOCK, 0x01)

    def test_set_dial_lock_off_builds_correct_frame(self) -> None:
        assert commands.set_dial_lock(False) == _simple_set(_SUB_DIAL_LOCK, 0x00)

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

    def test_get_dial_lock_custom_addresses(self) -> None:
        frame = commands.get_dial_lock(to_addr=0xA4, from_addr=0xE1)
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1

    def test_dial_lock_sub_byte_is_distinct_from_twin_peak(self) -> None:
        assert commands.get_dial_lock() != commands.get_twin_peak_filter()
