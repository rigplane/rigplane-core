"""MOR-3623: IC-9700 profile data for repeater work, the IC-705's shape.

Data only: the IC-9700 profile declares the repeater shift direction (CI-V
``0F``: 10 = simplex, 11 = DUP-, 12 = DUP+, IC-9700 CI-V Reference Guide p.4,
the same codes as the IC-705, MOR-2930), the ``16 5D`` tone squelch selector
and its ``[tone_squelch_types]`` table (guide p.5, the IC-705's codes,
MOR-2955), and the fixed-width DV filter row. Everything here reads the
shipped ``rigs/ic9700.toml`` through the existing loader, command map and
state-acquisition projection, plus the shared 16 5D builders both profiles
resolve through the map. The receiver guard itself is pinned at the wire
level by ``tests/test_mor3591_no_hidden_receiver_switch.py``.

Tier B-: no IC-9700 has been on the bench.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rigplane.commands import (
    get_repeater_shift,
    get_tone_squelch_type,
    parse_civ_frame,
    parse_tone_squelch_type_response,
    set_repeater_shift,
    set_tone_squelch_type,
)
from rigplane.core.command_dispatch import command_descriptor
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.types import Mode, RepeaterShiftDirection, ToneSquelchType
from rigplane.rig_loader import load_rig
from rigplane.runtime._state_queries import acquisition_query_resolver_for_profile

RIGS_DIR = Path(__file__).resolve().parent.parent / "rigs"
IC9700_PATH = RIGS_DIR / "ic9700.toml"
IC705_PATH = RIGS_DIR / "ic705.toml"
IC9700_ADDR = 0xA2

SHIFT_PATH = FieldPath.parse("receiver.main.operator_controls.repeater_shift")
TYPE_PATH = FieldPath.parse("receiver.main.operator_controls.tone_squelch_type")
TONE_PATH = FieldPath.parse("receiver.main.operator_toggles.repeater_tone")
TSQL_PATH = FieldPath.parse("receiver.main.operator_toggles.repeater_tsql")
SPLIT_PATH = FieldPath.parse("global.tx_state.split")

_SET_CODES = [
    pytest.param(RepeaterShiftDirection.SIMPLEX, 0x10, id="simplex-10"),
    pytest.param(RepeaterShiftDirection.MINUS, 0x11, id="minus-11"),
    pytest.param(RepeaterShiftDirection.PLUS, 0x12, id="plus-12"),
]


@pytest.fixture(scope="module")
def ic9700_config():
    return load_rig(IC9700_PATH)


@pytest.fixture(scope="module")
def ic9700_map(ic9700_config):
    return ic9700_config.to_command_map()


@pytest.fixture(scope="module")
def ic9700_profile(ic9700_config):
    return ic9700_config.to_profile()


# ── Repeater shift (0F) ──────────────────────────────────────────────


def test_profile_declares_shift_without_ars(ic9700_profile) -> None:
    """``0F`` has no ARS code (13 is the DD-mode RPS duplex), so no ARS cap."""
    assert ic9700_profile.supports_capability("repeater_shift")
    assert not ic9700_profile.supports_capability("repeater_shift_ars")


def test_command_map_declares_the_shift_pair(ic9700_map) -> None:
    for key in ("get_repeater_shift", "set_repeater_shift"):
        assert ic9700_map.has(key), key


@pytest.mark.parametrize(("direction", "code"), _SET_CODES)
def test_set_frame_carries_the_guide_code(ic9700_map, direction, code) -> None:
    frame = parse_civ_frame(
        set_repeater_shift(direction, to_addr=IC9700_ADDR, cmd_map=ic9700_map)
    )
    assert (frame.command, frame.sub, frame.data) == (0x0F, None, bytes([code]))


@pytest.mark.parametrize("direction", [RepeaterShiftDirection.ARS, 4, True])
def test_set_refuses_a_direction_with_no_code(ic9700_map, direction) -> None:
    with pytest.raises(ValueError):
        set_repeater_shift(direction, to_addr=IC9700_ADDR, cmd_map=ic9700_map)


def test_read_frame_is_bare_0f(ic9700_map) -> None:
    frame = parse_civ_frame(get_repeater_shift(to_addr=IC9700_ADDR, cmd_map=ic9700_map))
    assert (frame.command, frame.sub, frame.data) == (0x0F, None, b"")


def test_shift_field_is_polled_through_the_0f_read(ic9700_profile) -> None:
    assert ic9700_profile.state_acquisition is not None
    assert SHIFT_PATH in ic9700_profile.state_acquisition.pollable_paths()
    resolve = acquisition_query_resolver_for_profile(ic9700_profile)
    shift = resolve(SHIFT_PATH)
    split = resolve(SPLIT_PATH)
    assert shift is not None and split is not None
    assert (shift.command, shift.sub, shift.data) == (0x0F, None, b"")
    # One wire read feeds both fields, as on the IC-705.
    assert (shift.command, shift.sub, shift.data) == (
        split.command,
        split.sub,
        split.data,
    )


def test_dispatch_descriptor_is_the_shared_one() -> None:
    """No IC-9700 branch: the existing ``set_repeater_shift`` descriptor serves it."""
    assert command_descriptor("set_repeater_shift") is not None


# ── Tone squelch type (16 5D) ────────────────────────────────────────


def test_selector_pair_is_16_5d(ic9700_map) -> None:
    assert ic9700_map.get("get_tone_squelch_type") == (0x16, 0x5D)
    assert ic9700_map.get("set_tone_squelch_type") == (0x16, 0x5D)


def test_tone_squelch_types_table_is_the_ic705_guide_table(ic9700_profile) -> None:
    table = ic9700_profile.tone_squelch_types
    assert table is not None
    assert {code: kind.value for code, kind in table.items()} == {
        0x00: "off",
        0x01: "tone",
        0x02: "tsql",
        0x03: "dtcs",
        0x06: "dtcs_t",
        0x07: "tone_t_dtcs_r",
        0x08: "dtcs_t_tsql_r",
        0x09: "tone_t_tsql_r",
    }
    ic705 = load_rig(IC705_PATH).to_profile().tone_squelch_types
    assert table == ic705


def test_type_and_tone_booleans_ride_one_16_5d_query(ic9700_profile) -> None:
    assert ic9700_profile.state_acquisition is not None
    pollable = ic9700_profile.state_acquisition.pollable_paths()
    resolve = acquisition_query_resolver_for_profile(ic9700_profile)
    for path in (TYPE_PATH, TONE_PATH, TSQL_PATH):
        assert path in pollable, path
        query = resolve(path)
        assert query is not None, path
        assert (query.command, query.sub, query.data) == (0x16, 0x5D, b""), path


_TYPE_SET_CODES = [
    pytest.param(ToneSquelchType.OFF, 0x00, id="off-00"),
    pytest.param(ToneSquelchType.TONE, 0x01, id="tone-01"),
    pytest.param(ToneSquelchType.TSQL, 0x02, id="tsql-02"),
    pytest.param(ToneSquelchType.DTCS, 0x03, id="dtcs-03"),
    pytest.param(ToneSquelchType.DTCS_T, 0x06, id="dtcs-t-06"),
    pytest.param("tone", 0x01, id="str-tone-01"),
]


def test_type_read_frame_is_bare_16_5d(ic9700_map) -> None:
    frame = parse_civ_frame(
        get_tone_squelch_type(to_addr=IC9700_ADDR, cmd_map=ic9700_map)
    )
    assert (frame.command, frame.sub, frame.data) == (0x16, 0x5D, b"")


@pytest.mark.parametrize(("kind", "code"), _TYPE_SET_CODES)
def test_type_set_frame_carries_the_table_code(ic9700_map, kind, code) -> None:
    """The builder takes the raw table code; ``kind`` names its meaning."""
    frame = parse_civ_frame(
        set_tone_squelch_type(code, to_addr=IC9700_ADDR, cmd_map=ic9700_map)
    )
    assert (frame.command, frame.sub, frame.data) == (0x16, 0x5D, bytes([code]))


@pytest.mark.parametrize("code", [True, 0x100, -1, "01"])
def test_type_set_refuses_a_non_byte_code(ic9700_map, code) -> None:
    with pytest.raises(ValueError):
        set_tone_squelch_type(code, to_addr=IC9700_ADDR, cmd_map=ic9700_map)


def test_type_parse_names_the_table_kind(ic9700_profile) -> None:
    table = ic9700_profile.tone_squelch_types
    assert table is not None
    assert (
        parse_tone_squelch_type_response(bytes([0x02]), table) is ToneSquelchType.TSQL
    )
    assert parse_tone_squelch_type_response(bytes([0x05]), table) is None
    assert parse_tone_squelch_type_response(b"", table) is None


# ── Modes: DV in, DD out ─────────────────────────────────────────────


def test_dv_is_a_listed_fixed_width_mode(ic9700_profile) -> None:
    assert "DV" in ic9700_profile.modes
    rule = (ic9700_profile.filter_config or {}).get("DV")
    assert rule is not None
    assert rule.fixed is True
    assert rule.pbt_step_hz is None  # digital mode: no twin PBT


def test_dv_width_row_is_the_ic705_row(ic9700_profile) -> None:
    ic705 = load_rig(IC705_PATH).to_profile().filter_config or {}
    ic9700 = ic9700_profile.filter_config or {}
    assert ic9700["DV"] == ic705["DV"]


def test_dd_is_not_declared_until_band_aware_admission_exists(ic9700_profile) -> None:
    """DD (0x22) runs on 1.2 GHz only; without band-aware admission the radio
    would silently refuse it on 2 m / 70 cm. Follow-up work."""
    assert "DD" not in ic9700_profile.modes
    assert "DD" not in (ic9700_profile.filter_config or {})
    assert not hasattr(Mode, "DD")
