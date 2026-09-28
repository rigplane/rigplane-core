"""MOR-2503: profile-declared widths for fixed-width modes (FM/WFM/DV).

A fixed-width mode has no filter-width readback: the IC-7610 CI-V ``1A 03``
table has no FM row, so in FM the radio has no width to send. The profile
nevertheless declares each slot's width in ``[filters.width.<MODE>]``
(``fixed = true``, ``defaults``), and the mode response (0x01/0x04) carries
the selected FIL number — so the ingress can publish
``defaults[filter - 1]`` as a DECLARED ``filter_width`` observation, never a
measured one. A mode that declares no fixed table (SSB/CW) keeps publishing
no width from the mode frame; the measured ``1A 03`` path is untouched, and
a fixed mode that declares no ``defaults`` (X6200 AM/FM) gets nothing
invented for it.
"""

from __future__ import annotations

import pytest
from test_radio import MockTransport

from rigplane import IC_7610_ADDR
from rigplane.commands import CONTROLLER_ADDR
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.profiles import resolve_radio_profile
from rigplane.radio import IcomRadio
from rigplane.radio_state import RadioState
from rigplane.types import CivFrame

_FILTER_WIDTH = FieldPath.active("0", "freq_mode", "filter_width")
_MODE = FieldPath.active("0", "freq_mode", "mode")
_FILTER_NUM = FieldPath.active("0", "freq_mode", "filter_num")

# Mode bytes (CI-V): USB 0x01, FM 0x05, WFM 0x06, DV 0x17.
_USB = 0x01
_FM = 0x05
_WFM = 0x06
_DV = 0x17


def _radio(model: str) -> IcomRadio:
    transport = MockTransport()
    radio = IcomRadio("192.168.1.100", model=model)
    radio._civ_transport = transport  # type: ignore[attr-defined]
    radio._ctrl_transport = transport  # type: ignore[attr-defined]
    radio._connected = True  # type: ignore[attr-defined]
    radio._radio_state = RadioState()  # type: ignore[attr-defined]
    return radio


def _mode_frame(
    mode_byte: int,
    filter_num: int,
    *,
    from_addr: int = IC_7610_ADDR,
) -> CivFrame:
    return CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=from_addr,
        command=0x04,
        sub=None,
        data=bytes([mode_byte, filter_num]),
    )


def _observations(radio: IcomRadio, frame: CivFrame) -> list:
    return radio._civ_runtime._observations_from_frame(frame)  # noqa: SLF001


def _width_frame(
    index: int,
    *,
    from_addr: int = IC_7610_ADDR,
) -> CivFrame:
    return CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=from_addr,
        command=0x1A,
        sub=0x03,
        data=bytes([index]),
    )


@pytest.mark.parametrize(
    ("filter_num", "expected_hz"),
    [(1, 15000), (2, 10000), (3, 7000)],
)
def test_ic7610_fm_mode_response_publishes_declared_width(
    filter_num: int, expected_hz: int
) -> None:
    """IC-7610 FM: the mode response alone publishes the declared slot width."""
    radio = _radio("IC-7610")
    observations = _observations(radio, _mode_frame(_FM, filter_num))

    widths = [obs for obs in observations if obs.path == _FILTER_WIDTH]
    assert len(widths) == 1
    assert widths[0].value == expected_hz
    assert widths[0].quality == ("declared",)


def test_ic7610_fm_mode_response_still_publishes_mode_and_filter() -> None:
    """The declared width rides the mode response; mode and FIL are unchanged."""
    radio = _radio("IC-7610")
    observations = _observations(radio, _mode_frame(_FM, 2))

    assert [obs.value for obs in observations if obs.path == _MODE] == ["FM"]
    assert [obs.value for obs in observations if obs.path == _FILTER_NUM] == [2]


def test_ic7610_fm_undeclared_slot_publishes_no_width() -> None:
    """FIL4 has no declared default (the table lists three slots) — nothing."""
    radio = _radio("IC-7610")
    observations = _observations(radio, _mode_frame(_FM, 4))

    assert not [obs for obs in observations if obs.path == _FILTER_WIDTH]


def test_ic7610_usb_mode_response_publishes_no_width() -> None:
    """USB is not fixed-width: no width from the mode frame, ever (MOR-2503)."""
    radio = _radio("IC-7610")
    observations = _observations(radio, _mode_frame(_USB, 1))

    assert not [obs for obs in observations if obs.path == _FILTER_WIDTH]
    assert [obs.value for obs in observations if obs.path == _MODE] == ["USB"]
    assert [obs.value for obs in observations if obs.path == _FILTER_NUM] == [1]


@pytest.mark.parametrize(
    ("mode_byte", "mode_name", "expected_hz"),
    [(_WFM, "WFM", 200000), (_DV, "DV", 7000)],
)
def test_ic705_fixed_modes_publish_declared_widths(
    mode_byte: int, mode_name: str, expected_hz: int
) -> None:
    """IC-705 WFM and DV declare the same fixed shape — same rule applies."""
    radio = _radio("IC-705")
    observations = _observations(radio, _mode_frame(mode_byte, 1))

    assert [obs.value for obs in observations if obs.path == _MODE] == [mode_name]
    widths = [obs for obs in observations if obs.path == _FILTER_WIDTH]
    assert len(widths) == 1
    assert widths[0].value == expected_hz
    assert widths[0].quality == ("declared",)


def test_profile_declared_filter_width_rule() -> None:
    """The profile rule itself: fixed modes only, declared slots only."""
    ic7610 = resolve_radio_profile(model="IC-7610")
    assert ic7610.declared_filter_width("FM", 1) == 15000
    assert ic7610.declared_filter_width("FM", 3) == 7000
    # Non-fixed mode: no declared width.
    assert ic7610.declared_filter_width("USB", 1) is None
    # Out-of-table slot: no invented width.
    assert ic7610.declared_filter_width("FM", 4) is None
    assert ic7610.declared_filter_width("FM", 0) is None
    assert ic7610.declared_filter_width("FM", None) is None

    ic705 = resolve_radio_profile(model="IC-705")
    assert ic705.declared_filter_width("WFM", 2) == 200000
    assert ic705.declared_filter_width("DV", 3) == 7000

    # X6200 AM/FM are fixed but declare no defaults — nothing to publish.
    x6200 = resolve_radio_profile(model="X6200")
    assert x6200.declared_filter_width("AM", 1) is None
    assert x6200.declared_filter_width("FM", 1) is None


def test_ic7610_fm_1a03_answer_publishes_no_raw_index() -> None:
    """A non-NG 1A 03 answer in FM maps to no Hz — no raw-index overwrite.

    The scheduler still polls 1A 03 in FM (no ``available_when`` gates
    ``filter_width`` on mode), so a radio that answers anyway would reach
    ``_decode_filter_width`` with a fixed FM rule — which has no
    ``segments`` — and publish the raw BCD index over the declared width.
    """
    radio = _radio("IC-7610")
    radio._radio_state.main.mode = "FM"  # type: ignore[attr-defined]
    observations = _observations(radio, _width_frame(0x15))

    assert not [obs for obs in observations if obs.path == _FILTER_WIDTH]


def test_ic7610_usb_1a03_answer_still_publishes_measured_hz() -> None:
    """USB keeps the measured 1A 03 path: the index maps through segments."""
    from rigplane.commands import filter_index_to_hz

    radio = _radio("IC-7610")
    radio._radio_state.main.mode = "USB"  # type: ignore[attr-defined]
    observations = _observations(radio, _width_frame(0x15))

    rule = resolve_radio_profile(model="IC-7610").resolve_filter_rule("USB")
    expected = filter_index_to_hz(15, segments=rule.segments)  # type: ignore[arg-type]
    widths = [obs for obs in observations if obs.path == _FILTER_WIDTH]
    assert len(widths) == 1
    assert widths[0].value == expected
    assert widths[0].quality == ("confirmed",)
