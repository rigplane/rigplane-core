"""MOR-2955 (MOR-2131 step 1a): the tone squelch type, read through the
profile's ``[tone_squelch_types]`` table.

IC-705 CI-V Reference Guide (A7560-8EX-1, Jul. 2020, and A7560-8EX-6,
Jan. 2023), p.4: ``16 5D`` reads 00=OFF, 01=TONE, 02=TSQL, 03=DTCS,
06=DTCS (T), 07=TONE (T)/DTCS (R), 08=DTCS (T)/TSQL (R), 09=TONE (T)/TSQL (R).
Before this change codes 03 and 06-09 reached the state only as two unknown
CTCSS booleans.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from rigplane.commands import CONTROLLER_ADDR
from rigplane.core.state_pipeline_contracts import FieldPath
from rigplane.core.types import ToneSquelchType, ctcss_booleans_for_tone_squelch_type
from rigplane.profiles.rig_loader import RigLoadError
from rigplane.radio import IcomRadio
from rigplane.rig_loader import load_rig
from rigplane.runtime._state_queries import acquisition_query_resolver_for_profile
from rigplane.types import CivFrame
from test_radio import MockTransport
from test_rig_loader import _MINIMAL_TOML, _write_toml

IC705_PATH = Path(__file__).resolve().parent.parent / "rigs" / "ic705.toml"
IC705_ADDR = 0xA4
TYPE_PATH = "receiver.0.operator_controls.tone_squelch_type"

_GUIDE_CODES = [
    pytest.param(0x00, "off", False, False, id="00-off"),
    pytest.param(0x01, "tone", True, False, id="01-tone"),
    pytest.param(0x02, "tsql", True, True, id="02-tsql"),
    pytest.param(0x03, "dtcs", None, None, id="03-dtcs"),
    pytest.param(0x06, "dtcs_t", None, None, id="06-dtcs-t"),
    pytest.param(0x07, "tone_t_dtcs_r", None, None, id="07-tone-t-dtcs-r"),
    pytest.param(0x08, "dtcs_t_tsql_r", None, None, id="08-dtcs-t-tsql-r"),
    pytest.param(0x09, "tone_t_tsql_r", None, None, id="09-tone-t-tsql-r"),
]


def _frame(code: int) -> CivFrame:
    return CivFrame(
        to_addr=CONTROLLER_ADDR,
        from_addr=IC705_ADDR,
        command=0x16,
        sub=0x5D,
        data=bytes([code]),
        receiver=0x00,
    )


def _radio(model: str = "IC-705") -> IcomRadio:
    radio = IcomRadio("192.168.1.100", model=model)
    radio._civ_transport = MockTransport()
    radio._ctrl_transport = radio._civ_transport
    radio._connected = True
    return radio


# ── Profile table ────────────────────────────────────────────────────


def test_ic705_table_is_the_guide_table() -> None:
    table = load_rig(IC705_PATH).to_profile().tone_squelch_types
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


# ── Decode ───────────────────────────────────────────────────────────


@pytest.mark.parametrize(("code", "name", "tone", "tsql"), _GUIDE_CODES)
def test_16_5d_publishes_the_type_and_the_same_booleans(
    code: int, name: str, tone: bool | None, tsql: bool | None
) -> None:
    radio = _radio()
    radio._civ_runtime._update_state_cache_from_frame(_frame(code))
    snapshot = radio._state_store.snapshot()
    assert snapshot.field(TYPE_PATH).value == name
    assert snapshot.field("receiver.0.operator_toggles.repeater_tone").value is tone
    assert snapshot.field("receiver.0.operator_toggles.repeater_tsql").value is tsql
    radio._connected = False


def test_a_code_outside_the_table_publishes_an_unknown_type() -> None:
    """04 and 05 are not in the guide's list."""
    radio = _radio()
    radio._civ_runtime._update_state_cache_from_frame(_frame(0x04))
    snapshot = radio._state_store.snapshot()
    assert snapshot.field(TYPE_PATH).value is None
    assert snapshot.field("receiver.0.operator_toggles.repeater_tone").value is None
    assert snapshot.field("receiver.0.operator_toggles.repeater_tsql").value is None
    radio._connected = False


def test_a_profile_without_the_selector_publishes_no_type() -> None:
    radio = _radio(model="IC-7300")
    radio._civ_runtime._update_state_cache_from_frame(_frame(0x03))
    with pytest.raises(KeyError):
        radio._state_store.snapshot().field(TYPE_PATH)
    radio._connected = False


def test_ic705_polls_the_type_through_16_5d() -> None:
    profile = load_rig(IC705_PATH).to_profile()
    path = FieldPath.parse("receiver.main.operator_controls.tone_squelch_type")
    assert profile.state_acquisition is not None
    assert path in profile.state_acquisition.pollable_paths()
    query = acquisition_query_resolver_for_profile(profile)(path)
    assert query is not None
    assert (query.command, query.sub, query.data) == (0x16, 0x5D, b"")


# ── Neutral rule ─────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("kind", "pair"),
    [
        (ToneSquelchType.OFF, (False, False)),
        (ToneSquelchType.TONE, (True, False)),
        (ToneSquelchType.TSQL, (True, True)),
        (ToneSquelchType.DTCS, (None, None)),
        (ToneSquelchType.DTCS_T, (None, None)),
        (ToneSquelchType.TONE_T_DTCS_R, (None, None)),
        (ToneSquelchType.DTCS_T_TSQL_R, (None, None)),
        (ToneSquelchType.TONE_T_TSQL_R, (None, None)),
        (None, (None, None)),
    ],
)
def test_ctcss_booleans_for_each_type(kind, pair) -> None:
    assert ctcss_booleans_for_tone_squelch_type(kind) == pair


# ── Loader ───────────────────────────────────────────────────────────


# Where _toml declares the selector: under [commands], under its
# overrides, or as absent.
_SELECTORS = {
    "commands": "get_tone_squelch_type = [0x16, 0x5D]\n\n[commands.overrides]",
    "overrides": "[commands.overrides]\nget_tone_squelch_type = [0x16, 0x5D]",
    "absent": (
        'get_tone_squelch_type = { absent = "not on this radio" }\n\n'
        "[commands.overrides]"
    ),
}


def _toml(*, selector: str | None, table: str | None) -> str:
    text = _MINIMAL_TOML
    if selector is not None:
        text = text.replace("[commands.overrides]", _SELECTORS[selector])
    if table is not None:
        text += f"\n[tone_squelch_types]\n{table}\n"
    return text


@pytest.mark.parametrize("selector", ["commands", "overrides"])
def test_selector_without_a_table_refuses_to_load(tmp_path, selector) -> None:
    path = _write_toml(tmp_path, _toml(selector=selector, table=None))
    with pytest.raises(RigLoadError, match="needs a \\[tone_squelch_types\\] table"):
        load_rig(path)


def test_an_absent_selector_needs_no_table(tmp_path) -> None:
    path = _write_toml(tmp_path, _toml(selector="absent", table=None))
    assert load_rig(path).to_profile().tone_squelch_types is None


@pytest.mark.parametrize(
    ("table", "message"),
    [
        pytest.param('0 = "dcs"', "is not one of", id="unknown-type"),
        pytest.param('x = "off"', "is not a code", id="key-not-a-code"),
        pytest.param('256 = "off"', "is not one byte", id="code-over-a-byte"),
    ],
)
def test_bad_table_refuses_to_load(tmp_path, table, message) -> None:
    path = _write_toml(tmp_path, _toml(selector="commands", table=table))
    with pytest.raises(RigLoadError, match=message):
        load_rig(path)


def test_no_selector_and_no_table_loads_without_a_table(tmp_path) -> None:
    path = _write_toml(tmp_path, _toml(selector=None, table=None))
    assert load_rig(path).to_profile().tone_squelch_types is None
