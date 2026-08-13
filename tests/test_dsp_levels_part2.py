"""Unit tests for DSP level commands — Part 2: special cases (Issue #130)."""

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
from rigplane.types import CivFrame, FilterShape, SsbTxBandwidth
from _command_test_helpers import bind_default_addr_globals

bind_default_addr_globals(globals(), to_addr=IC_7610_ADDR)

# CI-V frame constants
_PREAMBLE = b"\xfe\xfe"
_TERMINATOR = b"\xfd"
_CMD_CMD29 = 0x29
_CMD_LEVEL = 0x14  # for DIGI-SEL shift
_CMD_PREAMP = 0x16  # for filter shape, SSB TX bandwidth
_CMD_CTL_MEM = 0x1A  # for AGC time constant, AF mute

# Sub-command bytes
_SUB_DIGISEL_SHIFT = 0x13
_SUB_AGC_TIME_CONSTANT = 0x04
_SUB_FILTER_SHAPE = 0x56
_SUB_SSB_TX_BANDWIDTH = 0x58
_SUB_AF_MUTE = 0x09


# ---------------------------------------------------------------------------
# Frame builder helpers
# ---------------------------------------------------------------------------


def _simple_get(cmd: int, sub: int) -> bytes:
    """Expected bytes for a simple (non-cmd29) get frame."""
    return _PREAMBLE + bytes([IC_7610_ADDR, CONTROLLER_ADDR, cmd, sub]) + _TERMINATOR


def _simple_set(cmd: int, sub: int, *data: int) -> bytes:
    """Expected bytes for a simple (non-cmd29) set frame."""
    return (
        _PREAMBLE
        + bytes([IC_7610_ADDR, CONTROLLER_ADDR, cmd, sub, *data])
        + _TERMINATOR
    )


def _cmd29_get(cmd: int, sub: int, receiver: int = RECEIVER_MAIN) -> bytes:
    """Expected bytes for a cmd29-wrapped get frame."""
    return (
        _PREAMBLE
        + bytes([IC_7610_ADDR, CONTROLLER_ADDR, _CMD_CMD29, receiver, cmd, sub])
        + _TERMINATOR
    )


def _cmd29_set(cmd: int, sub: int, *data: int, receiver: int = RECEIVER_MAIN) -> bytes:
    """Expected bytes for a cmd29-wrapped set frame."""
    return (
        _PREAMBLE
        + bytes([IC_7610_ADDR, CONTROLLER_ADDR, _CMD_CMD29, receiver, cmd, sub, *data])
        + _TERMINATOR
    )


def _response_frame(cmd: int, sub: int, data: bytes) -> CivFrame:
    """Build a CivFrame as a radio would return."""
    return CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=IC_7610_ADDR,
        command=cmd,
        sub=sub,
        data=data,
    )


# ---------------------------------------------------------------------------
# DIGI-SEL Shift (cmd29, int 0-255, 2-byte BCD)
# ---------------------------------------------------------------------------


class TestDigiselShift:
    """Tests for get_digisel_shift / set_digisel_shift."""

    def test_get_digisel_shift_main_receiver(self) -> None:
        assert commands.get_digisel_shift(receiver=RECEIVER_MAIN) == _cmd29_get(
            _CMD_LEVEL, _SUB_DIGISEL_SHIFT, RECEIVER_MAIN
        )

    def test_get_digisel_shift_sub_receiver(self) -> None:
        assert commands.get_digisel_shift(receiver=RECEIVER_SUB) == _cmd29_get(
            _CMD_LEVEL, _SUB_DIGISEL_SHIFT, RECEIVER_SUB
        )

    def test_get_digisel_shift_default_is_main(self) -> None:
        assert commands.get_digisel_shift() == commands.get_digisel_shift(
            receiver=RECEIVER_MAIN
        )

    def test_set_digisel_shift_zero_main(self) -> None:
        # 0 -> 2-byte BCD: 0x00 0x00
        assert commands.set_digisel_shift(0, receiver=RECEIVER_MAIN) == _cmd29_set(
            _CMD_LEVEL, _SUB_DIGISEL_SHIFT, 0x00, 0x00, receiver=RECEIVER_MAIN
        )

    def test_set_digisel_shift_128_main(self) -> None:
        # 128 -> 2-byte BCD: 0x01 0x28
        assert commands.set_digisel_shift(128, receiver=RECEIVER_MAIN) == _cmd29_set(
            _CMD_LEVEL, _SUB_DIGISEL_SHIFT, 0x01, 0x28, receiver=RECEIVER_MAIN
        )

    def test_set_digisel_shift_255_main(self) -> None:
        # 255 -> 2-byte BCD: 0x02 0x55
        assert commands.set_digisel_shift(255, receiver=RECEIVER_MAIN) == _cmd29_set(
            _CMD_LEVEL, _SUB_DIGISEL_SHIFT, 0x02, 0x55, receiver=RECEIVER_MAIN
        )

    def test_set_digisel_shift_sub_receiver(self) -> None:
        assert commands.set_digisel_shift(64, receiver=RECEIVER_SUB) == _cmd29_set(
            _CMD_LEVEL, _SUB_DIGISEL_SHIFT, 0x00, 0x64, receiver=RECEIVER_SUB
        )

    def test_set_digisel_shift_default_receiver_is_main(self) -> None:
        assert commands.set_digisel_shift(100) == commands.set_digisel_shift(
            100, receiver=RECEIVER_MAIN
        )

    def test_set_digisel_shift_rejects_above_255(self) -> None:
        with pytest.raises(ValueError):
            commands.set_digisel_shift(256, receiver=RECEIVER_MAIN)

    def test_set_digisel_shift_rejects_negative(self) -> None:
        with pytest.raises(ValueError):
            commands.set_digisel_shift(-1, receiver=RECEIVER_MAIN)

    def test_parse_digisel_shift_zero(self) -> None:
        frame = _response_frame(_CMD_LEVEL, _SUB_DIGISEL_SHIFT, b"\x00\x00")
        assert (
            parse_level_response(
                frame, command=_CMD_LEVEL, sub=_SUB_DIGISEL_SHIFT, bcd_bytes=2
            )
            == 0
        )

    def test_parse_digisel_shift_128(self) -> None:
        frame = _response_frame(_CMD_LEVEL, _SUB_DIGISEL_SHIFT, b"\x01\x28")
        assert (
            parse_level_response(
                frame, command=_CMD_LEVEL, sub=_SUB_DIGISEL_SHIFT, bcd_bytes=2
            )
            == 128
        )

    def test_parse_digisel_shift_255(self) -> None:
        frame = _response_frame(_CMD_LEVEL, _SUB_DIGISEL_SHIFT, b"\x02\x55")
        assert (
            parse_level_response(
                frame, command=_CMD_LEVEL, sub=_SUB_DIGISEL_SHIFT, bcd_bytes=2
            )
            == 255
        )

    def test_get_digisel_shift_custom_addresses(self) -> None:
        frame = commands.get_digisel_shift(to_addr=0xA4, from_addr=0xE1)
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1


# ---------------------------------------------------------------------------
# AGC Time Constant (cmd29, BCD 0-13)
# ---------------------------------------------------------------------------


class TestAGCTimeConstant:
    """Tests for get_agc_time_constant / set_agc_time_constant."""

    def test_get_agc_time_constant_main_receiver(self) -> None:
        assert commands.get_agc_time_constant(receiver=RECEIVER_MAIN) == _cmd29_get(
            _CMD_CTL_MEM, _SUB_AGC_TIME_CONSTANT, RECEIVER_MAIN
        )

    def test_get_agc_time_constant_sub_receiver(self) -> None:
        assert commands.get_agc_time_constant(receiver=RECEIVER_SUB) == _cmd29_get(
            _CMD_CTL_MEM, _SUB_AGC_TIME_CONSTANT, RECEIVER_SUB
        )

    def test_get_agc_time_constant_default_is_main(self) -> None:
        assert commands.get_agc_time_constant() == commands.get_agc_time_constant(
            receiver=RECEIVER_MAIN
        )

    def test_set_agc_time_constant_zero_main(self) -> None:
        # 0 -> BCD byte 0x00
        assert commands.set_agc_time_constant(0, receiver=RECEIVER_MAIN) == _cmd29_set(
            _CMD_CTL_MEM, _SUB_AGC_TIME_CONSTANT, 0x00, receiver=RECEIVER_MAIN
        )

    def test_set_agc_time_constant_bcd_encoding_twelve(self) -> None:
        # 12 -> BCD byte 0x12 (NOT 0x0C)
        frame = commands.set_agc_time_constant(12, receiver=RECEIVER_MAIN)
        assert frame == _cmd29_set(
            _CMD_CTL_MEM, _SUB_AGC_TIME_CONSTANT, 0x12, receiver=RECEIVER_MAIN
        )

    def test_set_agc_time_constant_bcd_encoding_nine(self) -> None:
        # 9 -> BCD byte 0x09
        frame = commands.set_agc_time_constant(9, receiver=RECEIVER_MAIN)
        assert frame == _cmd29_set(
            _CMD_CTL_MEM, _SUB_AGC_TIME_CONSTANT, 0x09, receiver=RECEIVER_MAIN
        )

    def test_set_agc_time_constant_max_value(self) -> None:
        # 13 -> BCD byte 0x13
        assert commands.set_agc_time_constant(13, receiver=RECEIVER_MAIN) == _cmd29_set(
            _CMD_CTL_MEM, _SUB_AGC_TIME_CONSTANT, 0x13, receiver=RECEIVER_MAIN
        )

    def test_set_agc_time_constant_sub_receiver(self) -> None:
        assert commands.set_agc_time_constant(5, receiver=RECEIVER_SUB) == _cmd29_set(
            _CMD_CTL_MEM, _SUB_AGC_TIME_CONSTANT, 0x05, receiver=RECEIVER_SUB
        )

    def test_set_agc_time_constant_default_receiver_is_main(self) -> None:
        assert commands.set_agc_time_constant(7) == commands.set_agc_time_constant(
            7, receiver=RECEIVER_MAIN
        )

    def test_set_agc_time_constant_rejects_over_13(self) -> None:
        with pytest.raises(ValueError):
            commands.set_agc_time_constant(14, receiver=RECEIVER_MAIN)

    def test_set_agc_time_constant_rejects_negative(self) -> None:
        with pytest.raises(ValueError):
            commands.set_agc_time_constant(-1, receiver=RECEIVER_MAIN)

    def test_parse_agc_time_constant_zero(self) -> None:
        frame = _response_frame(_CMD_CTL_MEM, _SUB_AGC_TIME_CONSTANT, b"\x00")
        assert (
            parse_level_response(
                frame, command=_CMD_CTL_MEM, sub=_SUB_AGC_TIME_CONSTANT, bcd_bytes=1
            )
            == 0
        )

    def test_parse_agc_time_constant_twelve(self) -> None:
        # BCD byte 0x12 -> value 12
        frame = _response_frame(_CMD_CTL_MEM, _SUB_AGC_TIME_CONSTANT, b"\x12")
        assert (
            parse_level_response(
                frame, command=_CMD_CTL_MEM, sub=_SUB_AGC_TIME_CONSTANT, bcd_bytes=1
            )
            == 12
        )

    def test_parse_agc_time_constant_thirteen(self) -> None:
        frame = _response_frame(_CMD_CTL_MEM, _SUB_AGC_TIME_CONSTANT, b"\x13")
        assert (
            parse_level_response(
                frame, command=_CMD_CTL_MEM, sub=_SUB_AGC_TIME_CONSTANT, bcd_bytes=1
            )
            == 13
        )


# ---------------------------------------------------------------------------
# Filter Shape (cmd29, enum: FilterShape)
# ---------------------------------------------------------------------------


class TestFilterShape:
    """Tests for get_filter_shape / set_filter_shape."""

    def test_get_filter_shape_main_receiver(self) -> None:
        assert commands.get_filter_shape(receiver=RECEIVER_MAIN) == _cmd29_get(
            _CMD_PREAMP, _SUB_FILTER_SHAPE, RECEIVER_MAIN
        )

    def test_get_filter_shape_sub_receiver(self) -> None:
        assert commands.get_filter_shape(receiver=RECEIVER_SUB) == _cmd29_get(
            _CMD_PREAMP, _SUB_FILTER_SHAPE, RECEIVER_SUB
        )

    def test_get_filter_shape_default_is_main(self) -> None:
        assert commands.get_filter_shape() == commands.get_filter_shape(
            receiver=RECEIVER_MAIN
        )

    def test_set_filter_shape_sharp_main(self) -> None:
        assert commands.set_filter_shape(
            FilterShape.SHARP, receiver=RECEIVER_MAIN
        ) == _cmd29_set(_CMD_PREAMP, _SUB_FILTER_SHAPE, 0x00, receiver=RECEIVER_MAIN)

    def test_set_filter_shape_soft_main(self) -> None:
        assert commands.set_filter_shape(
            FilterShape.SOFT, receiver=RECEIVER_MAIN
        ) == _cmd29_set(_CMD_PREAMP, _SUB_FILTER_SHAPE, 0x01, receiver=RECEIVER_MAIN)

    def test_set_filter_shape_sharp_sub_receiver(self) -> None:
        assert commands.set_filter_shape(
            FilterShape.SHARP, receiver=RECEIVER_SUB
        ) == _cmd29_set(_CMD_PREAMP, _SUB_FILTER_SHAPE, 0x00, receiver=RECEIVER_SUB)

    def test_set_filter_shape_soft_sub_receiver(self) -> None:
        assert commands.set_filter_shape(
            FilterShape.SOFT, receiver=RECEIVER_SUB
        ) == _cmd29_set(_CMD_PREAMP, _SUB_FILTER_SHAPE, 0x01, receiver=RECEIVER_SUB)

    def test_set_filter_shape_accepts_int(self) -> None:
        assert commands.set_filter_shape(0) == commands.set_filter_shape(
            FilterShape.SHARP
        )

    def test_set_filter_shape_accepts_int_one(self) -> None:
        assert commands.set_filter_shape(1) == commands.set_filter_shape(
            FilterShape.SOFT
        )

    def test_set_filter_shape_default_receiver_is_main(self) -> None:
        assert commands.set_filter_shape(
            FilterShape.SHARP
        ) == commands.set_filter_shape(FilterShape.SHARP, receiver=RECEIVER_MAIN)

    def test_set_filter_shape_rejects_invalid_enum(self) -> None:
        with pytest.raises(ValueError):
            commands.set_filter_shape(999, receiver=RECEIVER_MAIN)

    def test_set_filter_shape_builder_accepts_values_outside_the_sharp_soft_enum(
        self,
    ) -> None:
        """MOR-1534: the raw wire-command builder no longer polices the
        IC-7610 SHARP/SOFT enum's range — which filter-shape values are
        legal is a per-profile ``[filter_shape] values`` domain, enforced
        one layer up in ``CoreRadio.set_filter_shape`` (see
        ``TestFilterShapeDomainValidation`` in ``tests/test_radio.py``).
        This builder only encodes the raw single-BCD-byte value.
        """
        assert commands.set_filter_shape(2, receiver=RECEIVER_MAIN) == _cmd29_set(
            _CMD_PREAMP, _SUB_FILTER_SHAPE, 0x02, receiver=RECEIVER_MAIN
        )

    def test_parse_filter_shape_sharp(self) -> None:
        frame = _response_frame(_CMD_PREAMP, _SUB_FILTER_SHAPE, b"\x00")
        value = parse_level_response(
            frame, command=_CMD_PREAMP, sub=_SUB_FILTER_SHAPE, bcd_bytes=1
        )
        assert FilterShape(value) == FilterShape.SHARP

    def test_parse_filter_shape_soft(self) -> None:
        frame = _response_frame(_CMD_PREAMP, _SUB_FILTER_SHAPE, b"\x01")
        value = parse_level_response(
            frame, command=_CMD_PREAMP, sub=_SUB_FILTER_SHAPE, bcd_bytes=1
        )
        assert FilterShape(value) == FilterShape.SOFT


# ---------------------------------------------------------------------------
# SSB TX Bandwidth (GLOBAL — no cmd29, enum: SsbTxBandwidth)
# ---------------------------------------------------------------------------


class TestSsbTxBandwidth:
    """Tests for get_ssb_tx_bandwidth / set_ssb_tx_bandwidth."""

    def test_get_ssb_tx_bandwidth_builds_simple_frame(self) -> None:
        assert commands.get_ssb_tx_bandwidth() == _simple_get(
            _CMD_PREAMP, _SUB_SSB_TX_BANDWIDTH
        )

    def test_get_ssb_tx_bandwidth_no_cmd29(self) -> None:
        # Frame must not contain the 0x29 receiver-prefix byte
        frame = commands.get_ssb_tx_bandwidth()
        assert _CMD_CMD29 not in frame[4:]  # after preamble+addresses, no 0x29

    def test_set_ssb_tx_bandwidth_wide(self) -> None:
        assert commands.set_ssb_tx_bandwidth(SsbTxBandwidth.WIDE) == _simple_set(
            _CMD_PREAMP, _SUB_SSB_TX_BANDWIDTH, 0x00
        )

    def test_set_ssb_tx_bandwidth_mid(self) -> None:
        assert commands.set_ssb_tx_bandwidth(SsbTxBandwidth.MID) == _simple_set(
            _CMD_PREAMP, _SUB_SSB_TX_BANDWIDTH, 0x01
        )

    def test_set_ssb_tx_bandwidth_nar(self) -> None:
        assert commands.set_ssb_tx_bandwidth(SsbTxBandwidth.NAR) == _simple_set(
            _CMD_PREAMP, _SUB_SSB_TX_BANDWIDTH, 0x02
        )

    def test_set_ssb_tx_bandwidth_accepts_int_zero(self) -> None:
        assert commands.set_ssb_tx_bandwidth(0) == commands.set_ssb_tx_bandwidth(
            SsbTxBandwidth.WIDE
        )

    def test_set_ssb_tx_bandwidth_accepts_int_two(self) -> None:
        assert commands.set_ssb_tx_bandwidth(2) == commands.set_ssb_tx_bandwidth(
            SsbTxBandwidth.NAR
        )

    def test_set_ssb_tx_bandwidth_rejects_invalid_enum(self) -> None:
        with pytest.raises(ValueError):
            commands.set_ssb_tx_bandwidth(999)

    def test_set_ssb_tx_bandwidth_builder_accepts_values_outside_the_wide_mid_nar_enum(
        self,
    ) -> None:
        """MOR-1534: the raw wire-command builder no longer polices the
        IC-7610 WIDE/MID/NAR enum's range — which bandwidth values are
        legal is a per-profile ``[ssb_tx_bw] values`` domain, enforced one
        layer up in ``CoreRadio.set_ssb_tx_bandwidth`` (see
        ``TestSsbTxBandwidthDomainValidation`` in ``tests/test_radio.py``).
        This builder only encodes the raw single-BCD-byte value.
        """
        assert commands.set_ssb_tx_bandwidth(3) == _simple_set(
            _CMD_PREAMP, _SUB_SSB_TX_BANDWIDTH, 0x03
        )

    def test_parse_ssb_tx_bandwidth_wide(self) -> None:
        frame = _response_frame(_CMD_PREAMP, _SUB_SSB_TX_BANDWIDTH, b"\x00")
        value = parse_level_response(
            frame, command=_CMD_PREAMP, sub=_SUB_SSB_TX_BANDWIDTH, bcd_bytes=1
        )
        assert SsbTxBandwidth(value) == SsbTxBandwidth.WIDE

    def test_parse_ssb_tx_bandwidth_nar(self) -> None:
        frame = _response_frame(_CMD_PREAMP, _SUB_SSB_TX_BANDWIDTH, b"\x02")
        value = parse_level_response(
            frame, command=_CMD_PREAMP, sub=_SUB_SSB_TX_BANDWIDTH, bcd_bytes=1
        )
        assert SsbTxBandwidth(value) == SsbTxBandwidth.NAR

    def test_ssb_tx_bandwidth_distinct_from_filter_shape(self) -> None:
        assert commands.get_ssb_tx_bandwidth() != commands.get_filter_shape()

    def test_get_ssb_tx_bandwidth_custom_addresses(self) -> None:
        frame = commands.get_ssb_tx_bandwidth(to_addr=0xA4, from_addr=0xE1)
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1


# ---------------------------------------------------------------------------
# AF Mute (cmd29, boolean)
# ---------------------------------------------------------------------------


class TestAfMute:
    """Tests for get_af_mute / set_af_mute."""

    def test_get_af_mute_main_receiver(self) -> None:
        assert commands.get_af_mute(receiver=RECEIVER_MAIN) == _cmd29_get(
            _CMD_CTL_MEM, _SUB_AF_MUTE, RECEIVER_MAIN
        )

    def test_get_af_mute_sub_receiver(self) -> None:
        assert commands.get_af_mute(receiver=RECEIVER_SUB) == _cmd29_get(
            _CMD_CTL_MEM, _SUB_AF_MUTE, RECEIVER_SUB
        )

    def test_get_af_mute_default_is_main(self) -> None:
        assert commands.get_af_mute() == commands.get_af_mute(receiver=RECEIVER_MAIN)

    def test_set_af_mute_on_main_receiver(self) -> None:
        assert commands.set_af_mute(True, receiver=RECEIVER_MAIN) == _cmd29_set(
            _CMD_CTL_MEM, _SUB_AF_MUTE, 0x01, receiver=RECEIVER_MAIN
        )

    def test_set_af_mute_off_main_receiver(self) -> None:
        assert commands.set_af_mute(False, receiver=RECEIVER_MAIN) == _cmd29_set(
            _CMD_CTL_MEM, _SUB_AF_MUTE, 0x00, receiver=RECEIVER_MAIN
        )

    def test_set_af_mute_on_sub_receiver(self) -> None:
        assert commands.set_af_mute(True, receiver=RECEIVER_SUB) == _cmd29_set(
            _CMD_CTL_MEM, _SUB_AF_MUTE, 0x01, receiver=RECEIVER_SUB
        )

    def test_set_af_mute_off_sub_receiver(self) -> None:
        assert commands.set_af_mute(False, receiver=RECEIVER_SUB) == _cmd29_set(
            _CMD_CTL_MEM, _SUB_AF_MUTE, 0x00, receiver=RECEIVER_SUB
        )

    def test_set_af_mute_default_receiver_is_main(self) -> None:
        assert commands.set_af_mute(True) == commands.set_af_mute(
            True, receiver=RECEIVER_MAIN
        )

    def test_parse_af_mute_on(self) -> None:
        frame = _response_frame(_CMD_CTL_MEM, _SUB_AF_MUTE, b"\x01")
        assert (
            parse_bool_response(frame, command=_CMD_CTL_MEM, sub=_SUB_AF_MUTE) is True
        )

    def test_parse_af_mute_off(self) -> None:
        frame = _response_frame(_CMD_CTL_MEM, _SUB_AF_MUTE, b"\x00")
        assert (
            parse_bool_response(frame, command=_CMD_CTL_MEM, sub=_SUB_AF_MUTE) is False
        )

    def test_get_af_mute_custom_addresses(self) -> None:
        frame = commands.get_af_mute(to_addr=0xA4, from_addr=0xE1)
        assert frame[2] == 0xA4
        assert frame[3] == 0xE1

    def test_af_mute_distinct_from_agc_time_constant(self) -> None:
        assert commands.get_af_mute() != commands.get_agc_time_constant()
