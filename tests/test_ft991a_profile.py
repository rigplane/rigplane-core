"""MOR-4433: FT-991A product profile, discovery, sim and follow-up coverage.

TDD companion to rigs/ft991a.toml: the profile loads through the real
loader, drives the CAT wire like the PR-A/PR-B fixture tests, admits and
executes equalize through the declared vfo_a_to_b primitive (follow-up A),
and reports narrow-aware admitted widths (follow-up B). Not run on the
build host here; build-host CI owns the verdict.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from rigplane.backends.discovery import _parse_yaesu_id_response
from rigplane.backends.yaesu_cat.poller import YaesuCatPoller
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.core.exceptions import CommandError
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import StateStore
from rigplane.profiles import resolve_radio_profile
from rigplane.profiles.rig_loader import load_rig
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.radio_poller import VfoEqualize
from rigplane.web.runtime_helpers import projected_vfo_capability_tags
from rigplane.web.server import _serialize_filter_config
from rigplane_testkit.sim.adapters.yaesu_cat import YaesuCatSerialFake

ROOT = Path(__file__).resolve().parents[1]
RIG = ROOT / "rigs/ft991a.toml"
SIM_PROFILE = ROOT / "rigplane_testkit/sim/profiles/ft991a.toml"
FIXTURE = ROOT / "tests/fixtures/rigs/yaesu_single_rx_fixture.toml"


@pytest.fixture
def radio():
    result = YaesuCatRadio("/dev/null", profile=load_rig(RIG))
    result._transport._connected = True
    result._transport.write = AsyncMock()
    result._transport.query = AsyncMock()
    return result


# ---------------------------------------------------------------------------
# Loader: product profile through the real loader
# ---------------------------------------------------------------------------


def test_ft991a_profile_loads_with_single_receiver_topology():
    config = load_rig(RIG)
    assert config.id == "yaesu_ft991a"
    assert config.model == "FT-991A"
    assert config.receiver_count == 1
    assert config.transceiver_count == 1
    assert config.vfo_scheme == "ab"
    assert config.expected_identity_ids == ("0670",)
    assert config.default_baud == 38400
    assert config.split.write_map == {False: "2", True: "3"}
    assert config.split.read_map == {"0": False, "1": True}
    assert config.modes == (
        "LSB",
        "USB",
        "CW-U",
        "FM",
        "AM",
        "RTTY-L",
        "CW-L",
        "DATA-L",
        "RTTY-U",
        "DATA-FM",
        "FM-N",
        "DATA-U",
        "AM-N",
        "C4FM",
    )
    assert config.mode_codes == tuple("123456789ABCDE")
    assert config.to_profile().mode_families["cw"] == ("CW-U", "CW-L")


def test_ft991a_narrow_tables_come_from_the_manual_sh_table():
    config = load_rig(RIG)
    assert config.filter_width_encoding == "table_index"
    assert config.filter_width_first_code == 1
    assert config.filter_width_radio_default_code == 0
    ssb = config.filter_config["SSB"]
    assert ssb.narrow_table == (200, 400, 600, 850, 1100, 1350, 1500, 1650, 1800)
    assert ssb.table == (
        1800,
        1950,
        2100,
        2200,
        2300,
        2400,
        2500,
        2600,
        2700,
        2800,
        2900,
        3000,
        3200,
    )
    for mode in ("CW-U", "CW-L", "RTTY-L", "RTTY-U", "DATA-L", "DATA-U"):
        rule = config.filter_config[mode]
        assert rule.narrow_table and rule.table, mode


def test_ft991a_declares_the_narrow_read_the_variant_requires():
    config = load_rig(RIG)
    assert config.to_profile().supports_command("get_narrow")
    assert "narrow" in config.to_profile().capabilities


# ---------------------------------------------------------------------------
# Wire: CAT double mirroring the PR-A fixture contract
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_split_uses_ft_write_and_answer_maps(radio):
    await radio.set_split(True)
    await radio.set_split(False)
    assert [c.args[0] for c in radio._transport.write.call_args_list] == [
        "FT3;",
        "FT2;",
    ]
    radio._transport.query.side_effect = ["FT1", "FT0"]
    assert await radio.read_split() is True
    assert await radio.get_split() is False
    assert radio.receiver_count == radio.transceiver_count == 1


@pytest.mark.asyncio
async def test_direct_vfo_commands_and_absent_selection(radio):
    await radio.swap_vfo_ab()
    await radio.vfo_a_to_b()
    await radio.vfo_b_to_a()
    assert [c.args[0] for c in radio._transport.write.call_args_list] == [
        "SV;",
        "AB;",
        "BA;",
    ]
    for operation in (
        radio.get_vfo_slot(),
        radio.set_vfo_slot("B"),
        radio.get_vfo_select(),
        radio.set_vfo_select(1),
    ):
        with pytest.raises(NotImplementedError):
            await operation
    assert await radio.get_active_receiver() == 0
    assert not radio.supports_command("get_vfo_select")
    assert not radio.supports_command("get_vfo_slot")


@pytest.mark.asyncio
async def test_poller_sends_no_vs_fr_st(radio):
    wire: list[str] = []

    async def query(command, **kwargs):
        wire.append(command)
        return {"FT;": "FT0"}.get(command, "?")

    radio._transport.query = query
    await YaesuCatPoller(radio)._poll_slow()
    assert "VS;" not in wire
    assert "FR;" not in wire
    assert "ST;" not in wire


@pytest.mark.asyncio
async def test_single_value_rm_and_headless_pc(radio):
    radio._transport.query.side_effect = ["RM503"]
    assert await radio.read_comp_meter() == 3
    radio._transport.query.side_effect = ["PC075;"]
    assert await radio.read_power() == (0, 75)


@pytest.mark.asyncio
async def test_width_maps_narrow_and_wide_tables_by_na_state(radio):
    radio._transport.query = AsyncMock(side_effect=["MD02", "SH0000", "NA01"])
    assert await radio.get_filter_width() == 1500
    radio._transport.query = AsyncMock(side_effect=["MD02", "SH0000", "NA00"])
    assert await radio.get_filter_width() == 2400


@pytest.mark.asyncio
async def test_mode_without_a_table_refuses_like_ftx1_c4fm(radio):
    radio._transport.query = AsyncMock(side_effect=["MD04", "MD0E"])
    with pytest.raises(CommandError, match="without a width table"):
        await radio.set_filter_width(9000)
    with pytest.raises(CommandError, match="without a width table"):
        await radio.set_filter_width(500)


# ---------------------------------------------------------------------------
# Follow-up A: equalize is the declared A->B copy
# ---------------------------------------------------------------------------


def test_ft991a_and_fixture_admit_equalize_via_vfo_a_to_b():
    for path in (RIG, FIXTURE):
        profile = load_rig(path).to_profile()
        assert profile.supports_vfo_command("vfo_swap")
        assert profile.supports_vfo_command("vfo_equalize")
    radio = YaesuCatRadio("/dev/null", profile=load_rig(RIG))
    assert projected_vfo_capability_tags(radio, None) == {
        "vfo_swap",
        "vfo_equalize",
    }


@pytest.mark.asyncio
async def test_equalize_writes_ab_and_poller_dispatches_it(radio):
    await radio.equalize_vfo_ab(0)
    radio._transport.write.assert_called_once_with("AB;")
    handler = ControlHandler.__new__(ControlHandler)
    handler._radio = radio
    queued: list[object] = []
    assert (
        handler._enqueue_rc_frequency(
            "vfo_equalize", {}, SimpleNamespace(put=queued.append), radio
        )
        == {}
    )
    assert isinstance(queued[0], VfoEqualize)
    poller = YaesuCatPoller(radio)
    poller._post_write_freq_mode_readback = AsyncMock()
    radio._transport.write.reset_mock()
    await poller._execute_command(VfoEqualize())
    assert [c.args[0] for c in radio._transport.write.await_args_list] == ["AB;"]


@pytest.mark.asyncio
async def test_equalize_still_refused_without_vfo_a_to_b(tmp_path):
    path = tmp_path / "no-copy.toml"
    path.write_text(
        FIXTURE.read_text().replace('vfo_a_to_b     = { cat = { write = "AB;" } }', "")
    )
    config = load_rig(path)
    assert not config.to_profile().supports_vfo_command("vfo_equalize")
    radio = YaesuCatRadio("/dev/null", profile=config)
    with pytest.raises(NotImplementedError, match="vfo_a_to_b"):
        await radio.equalize_vfo_ab(0)
    handler = ControlHandler.__new__(ControlHandler)
    handler._radio = radio
    with pytest.raises(ValueError, match="not supported"):
        handler._enqueue_rc_frequency(
            "vfo_equalize", {}, SimpleNamespace(put=list.append), radio
        )
    poller = YaesuCatPoller(radio)
    poller._post_write_freq_mode_readback = AsyncMock()
    with pytest.raises(NotImplementedError, match="vfo_a_to_b"):
        await poller._execute_command(VfoEqualize())


@pytest.mark.parametrize(
    ("model", "swap", "equalize"),
    [
        ("IC-7300", True, True),
        ("FTX-1", True, True),
        ("FT-991A", True, True),
        ("TX-500", False, False),
        ("X6200", False, False),
    ],
)
def test_other_families_admission_unchanged(model, swap, equalize):
    profile = resolve_radio_profile(model=model)
    assert profile.supports_vfo_command("vfo_swap") is swap
    assert profile.supports_vfo_command("vfo_equalize") is equalize


# ---------------------------------------------------------------------------
# Follow-up B: narrow-aware admitted width and filter config projection
# ---------------------------------------------------------------------------


def _store_with_narrow(mode: str, narrow: bool, *, stale: bool = False):
    store = StateStore()
    store.apply(
        Observation(
            path=FieldPath.active("main", "freq_mode", "mode"),
            value=mode,
            source=SourceMetadata(source="yaesu_poll_response", provider="yaesu_cat"),
            timestamp_monotonic=0.0,
            max_age=5.0,
        )
    )
    store.apply(
        Observation(
            path=FieldPath.active("main", "operator_toggles", "narrow"),
            value=narrow,
            source=SourceMetadata(source="yaesu_poll_response", provider="yaesu_cat"),
            timestamp_monotonic=0.0,
            max_age=5.0,
        )
    )
    if stale:
        store.mark_stale_due(now=5.1)
    return store


def test_admitted_width_selects_table_by_narrow():
    profile = load_rig(RIG).to_profile()
    assert profile.admitted_filter_width("USB", 2400) == 2400
    assert profile.admitted_filter_width("USB", 2400, narrow=True) == 1800
    assert profile.admitted_filter_width("USB", 400, narrow=True) == 400
    assert profile.admitted_filter_width("FM", 9000, narrow=True) is None
    ftx1 = load_rig(ROOT / "rigs/ftx1.toml").to_profile()
    assert ftx1.admitted_filter_width("USB", 2400, narrow=True) == 2400


def test_serialized_filter_config_carries_narrow_variant_only_where_declared():
    ft991a = _serialize_filter_config(load_rig(RIG).to_profile())
    assert ft991a["SSB"]["narrowTable"] == [
        200,
        400,
        600,
        850,
        1100,
        1350,
        1500,
        1650,
        1800,
    ]
    ftx1 = _serialize_filter_config(load_rig(ROOT / "rigs/ftx1.toml").to_profile())
    assert "narrowTable" not in ftx1["SSB"]


# ---------------------------------------------------------------------------
# Discovery + sim end to end
# ---------------------------------------------------------------------------


def test_discovery_maps_yaesu_ids():
    assert (
        _parse_yaesu_id_response("/dev/ttyUSB0", 38400, "ID0670").profile_id
        == "yaesu_ft991a"
    )
    assert (
        _parse_yaesu_id_response("/dev/ttyUSB0", 38400, "ID0840").profile_id
        == "yaesu_ftx1"
    )


@pytest.mark.asyncio
async def test_ft991a_split_swap_copy_and_width_roundtrip(monkeypatch):
    fake = YaesuCatSerialFake.from_profile(ROOT, SIM_PROFILE, rig_path=RIG)
    wire: list[str] = []
    handle = fake.model.handle_with_outcome

    def logged(line):
        wire.append(line)
        return handle(line)

    monkeypatch.setattr(fake.model, "handle_with_outcome", logged)

    async def open_port(*args, **kwargs):
        return await fake.open_serial_connection()

    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.transport.open_serial_port", open_port
    )
    radio = YaesuCatRadio("/dev/fixture", profile=load_rig(RIG))
    await radio._transport.connect()
    try:
        for enabled in (True, False):
            await radio.set_split(enabled)
            assert await radio.get_split() is enabled
        await radio.set_freq(14_074_000)
        await radio.set_freq(14_080_000, receiver=1)
        await radio.swap_vfo_ab()
        assert await radio.get_freq() == 14_080_000
        assert await radio.get_freq(1) == 14_074_000
        await radio.vfo_a_to_b()
        assert await radio.get_freq(1) == 14_080_000
        await radio.set_freq(14_090_000, receiver=1)
        await radio.vfo_b_to_a()
        assert await radio.get_freq() == 14_090_000
        await radio.equalize_vfo_ab(0)
        assert await radio.get_freq(1) == 14_090_000
        await radio.set_mode("USB")
        await radio.set_narrow(False)
        await radio.set_filter_width(1800)
        assert await radio.get_filter_width() == 1800
        await radio.set_narrow(True)
        assert await radio.get_filter_width() == 1800
        await radio.set_filter_width(400)
        assert await radio.get_filter_width() == 400
        await radio.set_narrow(False)
        assert await radio.get_filter_width() == 1950
        await YaesuCatPoller(radio)._poll_slow()
        assert not any(cmd.startswith(("VS", "FR", "ST")) for cmd in wire)
        assert fake.model.handle("ID;") == "ID0670;"
        for command in ("VS;", "FR;", "ST;"):
            assert fake.model.handle(command) == "?;"
    finally:
        await radio._transport.close()
