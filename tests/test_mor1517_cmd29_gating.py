"""Tests for MOR-1517 — cmd29 wrapping must be gated on profile support.

Root cause: several IcomRadio SET/GET methods called their CI-V builders
without threading the profile's actual Command29 support through, so the
builder's ``command29=True`` default always fired. On profiles that declare
no ``[cmd29]`` routes at all (IC-7300), every affected command was silently
sent 0x29-wrapped and the radio ignored it — for SET methods this is a fully
silent failure since they are fire-and-forget.

``set_filter_width`` already had the correct pattern (branch on
``self._profile.supports_cmd29(cmd, sub)``). This test file locks in the same
pattern for PBT inner/outer and filter shape (the pinned defect) plus every
other sibling instance found by auditing radio.py for the same class of bug:
nb_level, nr_level, apf_type_level, digisel_shift, audio_peak_filter,
auto_notch, manual_notch, manual_notch_width, twin_peak_filter,
agc_time_constant, s_meter_sql_status, various_squelch, repeater_tone,
repeater_tsql, tone_freq, tsql_freq.

IC-7300 declares no ``[cmd29]`` section at all (single receiver) -> every
affected command must go out as a bare, unwrapped CI-V frame.
IC-7610 declares cmd29 routes for most of these commands -> existing wrapped
behavior must be unchanged for those. apf_type_level/digisel_shift are not
reachable on IC-7300 (it doesn't declare those commands at all) but IC-705
does declare them and also has no ``[cmd29]`` section, so IC-705 is used as
the unwrapped case for those two. manual_notch_width (0x16/0x57) turns out to
be absent from IC-7610's own cmd29 routes too, so it must be unwrapped on
*both* profiles -- see TestManualNotchWidthGating.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from rigplane.commands import (
    get_agc_time_constant,
    get_filter_shape,
    get_pbt_inner,
    get_pbt_outer,
    get_s_meter_sql_status,
    get_tone_freq,
    get_tsql_freq,
    get_various_squelch,
    set_agc_time_constant,
    set_apf_type_level,
    set_audio_peak_filter,
    set_auto_notch,
    set_digisel_shift,
    set_filter_shape,
    set_manual_notch,
    set_manual_notch_width,
    set_nb_level,
    set_nr_level,
    set_pbt_inner,
    set_pbt_outer,
    set_repeater_tone,
    set_repeater_tsql,
    set_tone_freq,
    set_tsql_freq,
    set_twin_peak_filter,
)
from rigplane.radio import IcomRadio
from rigplane.types import AudioPeakFilter, CivFrame, FilterShape

_IC7300_ADDR = 0x94
_IC7610_ADDR = 0x98
_IC705_ADDR = 0xA4


def _connected_icom(*, model: str) -> IcomRadio:
    """Build a minimally-connected IcomRadio for a given profile."""
    radio = IcomRadio(host="127.0.0.1", username="x", password="y", model=model)
    radio._civ_runtime._check_connected = lambda: None  # type: ignore[method-assign]
    radio._civ_runtime._connected = True  # type: ignore[attr-defined]
    return radio


def _mock_raw(radio: IcomRadio) -> AsyncMock:
    """Mock the fire-and-forget send path and return the mock for assertions."""
    mock = AsyncMock(return_value=None)
    radio._send_civ_raw = mock  # type: ignore[method-assign]
    return mock


def _mock_expect(radio: IcomRadio, response: CivFrame) -> AsyncMock:
    """Mock the request/response send path to return a canned response."""
    mock = AsyncMock(return_value=response)
    radio._send_civ_expect = mock  # type: ignore[method-assign]
    return mock


def _sent_civ(mock: AsyncMock) -> bytes:
    """Extract the outgoing CI-V frame bytes from a mocked send call."""
    args, kwargs = mock.call_args
    return args[0] if args else kwargs["civ_frame"]


# ---------------------------------------------------------------------------
# Pinned defect: PBT inner / PBT outer / filter shape (GET + SET)
# ---------------------------------------------------------------------------


class TestPbtInnerGating:
    @pytest.mark.asyncio
    async def test_set_pbt_inner_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        mock = _mock_raw(radio)
        await radio.set_pbt_inner(75, receiver=0)
        expected = set_pbt_inner(75, to_addr=_IC7300_ADDR, receiver=0, command29=False)
        assert _sent_civ(mock) == expected
        assert b"\x29" not in expected[: len(expected)][2:5]

    @pytest.mark.asyncio
    async def test_set_pbt_inner_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_pbt_inner(75, receiver=0)
        expected = set_pbt_inner(75, to_addr=_IC7610_ADDR, receiver=0, command29=True)
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_get_pbt_inner_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        response = CivFrame(
            to_addr=0xE0,
            from_addr=_IC7300_ADDR,
            command=0x14,
            sub=0x07,
            data=b"\x00\x64",
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_pbt_inner(receiver=0)
        expected = get_pbt_inner(to_addr=_IC7300_ADDR, receiver=0, command29=False)
        assert _sent_civ(mock) == expected
        assert value == 64

    @pytest.mark.asyncio
    async def test_get_pbt_inner_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        response = CivFrame(
            to_addr=0xE0,
            from_addr=_IC7610_ADDR,
            command=0x14,
            sub=0x07,
            data=b"\x00\x64",
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_pbt_inner(receiver=0)
        expected = get_pbt_inner(to_addr=_IC7610_ADDR, receiver=0, command29=True)
        assert _sent_civ(mock) == expected
        assert value == 64


class TestPbtOuterGating:
    @pytest.mark.asyncio
    async def test_set_pbt_outer_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        mock = _mock_raw(radio)
        await radio.set_pbt_outer(80, receiver=0)
        expected = set_pbt_outer(80, to_addr=_IC7300_ADDR, receiver=0, command29=False)
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_set_pbt_outer_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_pbt_outer(80, receiver=0)
        expected = set_pbt_outer(80, to_addr=_IC7610_ADDR, receiver=0, command29=True)
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_get_pbt_outer_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        response = CivFrame(
            to_addr=0xE0,
            from_addr=_IC7300_ADDR,
            command=0x14,
            sub=0x08,
            data=b"\x00\x50",
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_pbt_outer(receiver=0)
        expected = get_pbt_outer(to_addr=_IC7300_ADDR, receiver=0, command29=False)
        assert _sent_civ(mock) == expected
        assert value == 50

    @pytest.mark.asyncio
    async def test_get_pbt_outer_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        response = CivFrame(
            to_addr=0xE0,
            from_addr=_IC7610_ADDR,
            command=0x14,
            sub=0x08,
            data=b"\x00\x50",
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_pbt_outer(receiver=0)
        expected = get_pbt_outer(to_addr=_IC7610_ADDR, receiver=0, command29=True)
        assert _sent_civ(mock) == expected
        assert value == 50


class TestFilterShapeGating:
    @pytest.mark.asyncio
    async def test_set_filter_shape_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        mock = _mock_raw(radio)
        await radio.set_filter_shape(FilterShape.SOFT, receiver=0)
        expected = set_filter_shape(
            FilterShape.SOFT, to_addr=_IC7300_ADDR, receiver=0, command29=False
        )
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_set_filter_shape_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_filter_shape(FilterShape.SOFT, receiver=0)
        expected = set_filter_shape(
            FilterShape.SOFT, to_addr=_IC7610_ADDR, receiver=0, command29=True
        )
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_get_filter_shape_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        response = CivFrame(
            to_addr=0xE0, from_addr=_IC7300_ADDR, command=0x16, sub=0x56, data=b"\x01"
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_filter_shape(receiver=0)
        expected = get_filter_shape(to_addr=_IC7300_ADDR, receiver=0, command29=False)
        assert _sent_civ(mock) == expected
        assert value == FilterShape.SOFT

    @pytest.mark.asyncio
    async def test_get_filter_shape_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        response = CivFrame(
            to_addr=0xE0, from_addr=_IC7610_ADDR, command=0x16, sub=0x56, data=b"\x01"
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_filter_shape(receiver=0)
        expected = get_filter_shape(to_addr=_IC7610_ADDR, receiver=0, command29=True)
        assert _sent_civ(mock) == expected
        assert value == FilterShape.SOFT


# ---------------------------------------------------------------------------
# Sibling audit fixes: same defect class found in other level/DSP/tone
# commands that hardcoded command29=True the same way PBT/filter-shape did.
# ---------------------------------------------------------------------------


class TestNrLevelGating:
    @pytest.mark.asyncio
    async def test_set_nr_level_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        mock = _mock_raw(radio)
        await radio.set_nr_level(120, receiver=0)
        expected = set_nr_level(120, to_addr=_IC7300_ADDR, receiver=0, command29=False)
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_set_nr_level_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_nr_level(120, receiver=0)
        expected = set_nr_level(120, to_addr=_IC7610_ADDR, receiver=0, command29=True)
        assert _sent_civ(mock) == expected


class TestNbLevelGating:
    @pytest.mark.asyncio
    async def test_set_nb_level_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        mock = _mock_raw(radio)
        await radio.set_nb_level(90, receiver=0)
        expected = set_nb_level(90, to_addr=_IC7300_ADDR, receiver=0, command29=False)
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_set_nb_level_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_nb_level(90, receiver=0)
        expected = set_nb_level(90, to_addr=_IC7610_ADDR, receiver=0, command29=True)
        assert _sent_civ(mock) == expected


class TestApfTypeLevelGating:
    """IC-7300 doesn't declare apf_type_level at all, but IC-705 does (and
    has no [cmd29] section) -- IC-705 is the unwrapped case here."""

    @pytest.mark.asyncio
    async def test_set_apf_type_level_unwrapped_on_ic705(self) -> None:
        radio = _connected_icom(model="IC-705")
        mock = _mock_raw(radio)
        await radio.set_apf_type_level(3, receiver=0)
        expected = set_apf_type_level(
            3, to_addr=_IC705_ADDR, receiver=0, command29=False
        )
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_set_apf_type_level_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_apf_type_level(3, receiver=0)
        expected = set_apf_type_level(
            3, to_addr=_IC7610_ADDR, receiver=0, command29=True
        )
        assert _sent_civ(mock) == expected


class TestDigiselShiftGating:
    """Same IC-705-vs-IC-7610 shape as apf_type_level."""

    @pytest.mark.asyncio
    async def test_set_digisel_shift_unwrapped_on_ic705(self) -> None:
        radio = _connected_icom(model="IC-705")
        mock = _mock_raw(radio)
        await radio.set_digisel_shift(10, receiver=0)
        expected = set_digisel_shift(
            10, to_addr=_IC705_ADDR, receiver=0, command29=False
        )
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_set_digisel_shift_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_digisel_shift(10, receiver=0)
        expected = set_digisel_shift(
            10, to_addr=_IC7610_ADDR, receiver=0, command29=True
        )
        assert _sent_civ(mock) == expected


class TestAudioPeakFilterGating:
    @pytest.mark.asyncio
    async def test_set_audio_peak_filter_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        mock = _mock_raw(radio)
        await radio.set_audio_peak_filter(AudioPeakFilter.WIDE, receiver=0)
        expected = set_audio_peak_filter(
            AudioPeakFilter.WIDE, to_addr=_IC7300_ADDR, receiver=0, command29=False
        )
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_set_audio_peak_filter_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_audio_peak_filter(AudioPeakFilter.WIDE, receiver=0)
        expected = set_audio_peak_filter(
            AudioPeakFilter.WIDE, to_addr=_IC7610_ADDR, receiver=0, command29=True
        )
        assert _sent_civ(mock) == expected


class TestAutoNotchGating:
    @pytest.mark.asyncio
    async def test_set_auto_notch_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        mock = _mock_raw(radio)
        await radio.set_auto_notch(True, receiver=0)
        expected = set_auto_notch(
            True, to_addr=_IC7300_ADDR, receiver=0, command29=False
        )
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_set_auto_notch_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_auto_notch(True, receiver=0)
        expected = set_auto_notch(
            True, to_addr=_IC7610_ADDR, receiver=0, command29=True
        )
        assert _sent_civ(mock) == expected


class TestManualNotchGating:
    @pytest.mark.asyncio
    async def test_set_manual_notch_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        mock = _mock_raw(radio)
        await radio.set_manual_notch(True, receiver=0)
        expected = set_manual_notch(
            True, to_addr=_IC7300_ADDR, receiver=0, command29=False
        )
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_set_manual_notch_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_manual_notch(True, receiver=0)
        expected = set_manual_notch(
            True, to_addr=_IC7610_ADDR, receiver=0, command29=True
        )
        assert _sent_civ(mock) == expected


class TestManualNotchWidthGating:
    """0x16/0x57 (manual notch width) is not in IC-7610's [cmd29] routes
    either — it must go out unwrapped on BOTH profiles. Before this fix it
    was silently broken on IC-7610 too, not just IC-7300."""

    @pytest.mark.asyncio
    async def test_set_manual_notch_width_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        mock = _mock_raw(radio)
        await radio.set_manual_notch_width(2, receiver=0)
        expected = set_manual_notch_width(
            2, to_addr=_IC7300_ADDR, receiver=0, command29=False
        )
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_set_manual_notch_width_unwrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_manual_notch_width(2, receiver=0)
        expected = set_manual_notch_width(
            2, to_addr=_IC7610_ADDR, receiver=0, command29=False
        )
        assert _sent_civ(mock) == expected
        assert radio._profile.supports_cmd29(0x16, 0x57) is False


class TestTwinPeakFilterGating:
    @pytest.mark.asyncio
    async def test_set_twin_peak_filter_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        mock = _mock_raw(radio)
        await radio.set_twin_peak_filter(True, receiver=0)
        expected = set_twin_peak_filter(
            True, to_addr=_IC7300_ADDR, receiver=0, command29=False
        )
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_set_twin_peak_filter_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_twin_peak_filter(True, receiver=0)
        expected = set_twin_peak_filter(
            True, to_addr=_IC7610_ADDR, receiver=0, command29=True
        )
        assert _sent_civ(mock) == expected


class TestAgcTimeConstantGating:
    @pytest.mark.asyncio
    async def test_set_agc_time_constant_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        mock = _mock_raw(radio)
        await radio.set_agc_time_constant(5, receiver=0)
        expected = set_agc_time_constant(
            5, to_addr=_IC7300_ADDR, receiver=0, command29=False
        )
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_set_agc_time_constant_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_agc_time_constant(5, receiver=0)
        expected = set_agc_time_constant(
            5, to_addr=_IC7610_ADDR, receiver=0, command29=True
        )
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_get_agc_time_constant_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        response = CivFrame(
            to_addr=0xE0, from_addr=_IC7300_ADDR, command=0x1A, sub=0x04, data=b"\x05"
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_agc_time_constant(receiver=0)
        expected = get_agc_time_constant(
            to_addr=_IC7300_ADDR, receiver=0, command29=False
        )
        assert _sent_civ(mock) == expected
        assert value == 5

    @pytest.mark.asyncio
    async def test_get_agc_time_constant_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        response = CivFrame(
            to_addr=0xE0, from_addr=_IC7610_ADDR, command=0x1A, sub=0x04, data=b"\x05"
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_agc_time_constant(receiver=0)
        expected = get_agc_time_constant(
            to_addr=_IC7610_ADDR, receiver=0, command29=True
        )
        assert _sent_civ(mock) == expected
        assert value == 5


class TestSMeterSqlStatusGating:
    """GET-only command (no SET twin)."""

    @pytest.mark.asyncio
    async def test_get_s_meter_sql_status_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        response = CivFrame(
            to_addr=0xE0, from_addr=_IC7300_ADDR, command=0x15, sub=0x01, data=b"\x01"
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_s_meter_sql_status(receiver=0)
        expected = get_s_meter_sql_status(
            to_addr=_IC7300_ADDR, receiver=0, command29=False
        )
        assert _sent_civ(mock) == expected
        assert value is True

    @pytest.mark.asyncio
    async def test_get_s_meter_sql_status_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        response = CivFrame(
            to_addr=0xE0, from_addr=_IC7610_ADDR, command=0x15, sub=0x01, data=b"\x01"
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_s_meter_sql_status(receiver=0)
        expected = get_s_meter_sql_status(
            to_addr=_IC7610_ADDR, receiver=0, command29=True
        )
        assert _sent_civ(mock) == expected
        assert value is True


class TestVariousSquelchGating:
    """GET-only command (no SET twin)."""

    @pytest.mark.asyncio
    async def test_get_various_squelch_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        response = CivFrame(
            to_addr=0xE0, from_addr=_IC7300_ADDR, command=0x15, sub=0x05, data=b"\x01"
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_various_squelch(receiver=0)
        expected = get_various_squelch(
            to_addr=_IC7300_ADDR, receiver=0, command29=False
        )
        assert _sent_civ(mock) == expected
        assert value is True

    @pytest.mark.asyncio
    async def test_get_various_squelch_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        response = CivFrame(
            to_addr=0xE0, from_addr=_IC7610_ADDR, command=0x15, sub=0x05, data=b"\x01"
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_various_squelch(receiver=0)
        expected = get_various_squelch(to_addr=_IC7610_ADDR, receiver=0, command29=True)
        assert _sent_civ(mock) == expected
        assert value is True


class TestRepeaterToneGating:
    @pytest.mark.asyncio
    async def test_set_repeater_tone_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        mock = _mock_raw(radio)
        await radio.set_repeater_tone(True, receiver=0)
        expected = set_repeater_tone(
            True, to_addr=_IC7300_ADDR, receiver=0, command29=False
        )
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_set_repeater_tone_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_repeater_tone(True, receiver=0)
        expected = set_repeater_tone(
            True, to_addr=_IC7610_ADDR, receiver=0, command29=True
        )
        assert _sent_civ(mock) == expected


class TestRepeaterTsqlGating:
    @pytest.mark.asyncio
    async def test_set_repeater_tsql_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        mock = _mock_raw(radio)
        await radio.set_repeater_tsql(True, receiver=0)
        expected = set_repeater_tsql(
            True, to_addr=_IC7300_ADDR, receiver=0, command29=False
        )
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_set_repeater_tsql_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_repeater_tsql(True, receiver=0)
        expected = set_repeater_tsql(
            True, to_addr=_IC7610_ADDR, receiver=0, command29=True
        )
        assert _sent_civ(mock) == expected


class TestToneFreqGating:
    """tone.py's get/set_tone_freq required a new command29 branch (they
    previously called build_cmd29_frame directly with no way to opt out)."""

    @pytest.mark.asyncio
    async def test_set_tone_freq_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        mock = _mock_raw(radio)
        await radio.set_tone_freq(100.0, receiver=0)
        expected = set_tone_freq(
            100.0, to_addr=_IC7300_ADDR, receiver=0, command29=False
        )
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_set_tone_freq_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_tone_freq(100.0, receiver=0)
        expected = set_tone_freq(
            100.0, to_addr=_IC7610_ADDR, receiver=0, command29=True
        )
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_get_tone_freq_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        response = CivFrame(
            to_addr=0xE0,
            from_addr=_IC7300_ADDR,
            command=0x1B,
            sub=0x00,
            data=b"\x01\x00\x00",
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_tone_freq(receiver=0)
        expected = get_tone_freq(to_addr=_IC7300_ADDR, receiver=0, command29=False)
        assert _sent_civ(mock) == expected
        assert value == 100.0

    @pytest.mark.asyncio
    async def test_get_tone_freq_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        response = CivFrame(
            to_addr=0xE0,
            from_addr=_IC7610_ADDR,
            command=0x1B,
            sub=0x00,
            data=b"\x01\x00\x00",
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_tone_freq(receiver=0)
        expected = get_tone_freq(to_addr=_IC7610_ADDR, receiver=0, command29=True)
        assert _sent_civ(mock) == expected
        assert value == 100.0


class TestTsqlFreqGating:
    @pytest.mark.asyncio
    async def test_set_tsql_freq_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        mock = _mock_raw(radio)
        await radio.set_tsql_freq(100.0, receiver=0)
        expected = set_tsql_freq(
            100.0, to_addr=_IC7300_ADDR, receiver=0, command29=False
        )
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_set_tsql_freq_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        mock = _mock_raw(radio)
        await radio.set_tsql_freq(100.0, receiver=0)
        expected = set_tsql_freq(
            100.0, to_addr=_IC7610_ADDR, receiver=0, command29=True
        )
        assert _sent_civ(mock) == expected

    @pytest.mark.asyncio
    async def test_get_tsql_freq_unwrapped_on_ic7300(self) -> None:
        radio = _connected_icom(model="IC-7300")
        response = CivFrame(
            to_addr=0xE0,
            from_addr=_IC7300_ADDR,
            command=0x1B,
            sub=0x01,
            data=b"\x01\x00\x00",
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_tsql_freq(receiver=0)
        expected = get_tsql_freq(to_addr=_IC7300_ADDR, receiver=0, command29=False)
        assert _sent_civ(mock) == expected
        assert value == 100.0

    @pytest.mark.asyncio
    async def test_get_tsql_freq_wrapped_on_ic7610(self) -> None:
        radio = _connected_icom(model="IC-7610")
        response = CivFrame(
            to_addr=0xE0,
            from_addr=_IC7610_ADDR,
            command=0x1B,
            sub=0x01,
            data=b"\x01\x00\x00",
        )
        mock = _mock_expect(radio, response)
        value = await radio.get_tsql_freq(receiver=0)
        expected = get_tsql_freq(to_addr=_IC7610_ADDR, receiver=0, command29=True)
        assert _sent_civ(mock) == expected
        assert value == 100.0


# ---------------------------------------------------------------------------
# IC-7610 hard protocol rule sanity check (CLAUDE.md): cmd29 must remain
# untouched for freq/mode (0x05/0x06) — this fix must not disturb that.
# ---------------------------------------------------------------------------


class TestFreqModeUnaffected:
    @pytest.mark.asyncio
    async def test_ic7610_set_freq_still_direct_not_cmd29(self) -> None:
        """0x05 (freq) is never cmd29-wrapped on IC-7610, per CLAUDE.md."""
        radio = _connected_icom(model="IC-7610")
        assert radio._profile.supports_cmd29(0x05) is False

    @pytest.mark.asyncio
    async def test_ic7610_set_mode_still_direct_not_cmd29(self) -> None:
        """0x06 (mode) is never cmd29-wrapped on IC-7610, per CLAUDE.md."""
        radio = _connected_icom(model="IC-7610")
        assert radio._profile.supports_cmd29(0x06) is False
