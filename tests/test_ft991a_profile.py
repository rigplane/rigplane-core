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
from rigplane.backends.yaesu_cat.parser import CatParseError
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
    # MOR-4433: one code space per mode — the narrow and wide tables sit
    # at DIFFERENT code ranges (manual PDF p.17: SSB narrow 01-09, wide
    # 09-21; CW/RTTY/DATA narrow 01-10, wide 10-17).
    assert (ssb.first_code, ssb.narrow_first_code) == (9, 1)
    for mode in ("CW-U", "CW-L", "RTTY-L", "RTTY-U", "DATA-L", "DATA-U"):
        rule = config.filter_config[mode]
        assert rule.narrow_table and rule.table, mode
        assert (rule.first_code, rule.narrow_first_code) == (10, 1), mode


def test_predating_profiles_keep_profile_level_first_code():
    """Rules without per-table bases resolve to `[filters] first_code`."""
    for path in (FIXTURE, ROOT / "rigs/ftx1.toml"):
        config = load_rig(path)
        assert config.filter_config
        for mode, rule in config.filter_config.items():
            assert rule.first_code is None, (path, mode)
            assert rule.narrow_first_code is None, (path, mode)
        profile = config.to_profile()
        for mode in config.filter_config:
            rule = profile.resolve_filter_rule(mode)
            assert profile.resolve_filter_first_code(rule, narrow=False) == (
                profile.filter_width_first_code
            )
            assert profile.resolve_filter_first_code(rule, narrow=True) == (
                profile.filter_width_first_code
            )


def test_ft991a_declares_the_narrow_read_the_variant_requires():
    config = load_rig(RIG)
    assert config.to_profile().supports_command("get_narrow")
    assert "narrow" in config.to_profile().capabilities


def test_ft991a_declares_the_hamlib_ft991_model_id():
    # Hamlib groups the FT-991A under RIG_MODEL_FT991 = 1035
    # (yaesu/ft991.c); without it the loader default (2028, RIG_MODEL_FTX1)
    # would make rigctld dump_state report this radio as an FTX-1.
    config = load_rig(RIG)
    assert config.hamlib_model_id == 1035
    assert config.to_profile().hamlib_model_id == 1035


def test_ft991a_reports_its_hamlib_model_through_dump_state():
    # MOR-4433 P2: rigctld dump_state substitutes line 1 from the radio's
    # TOML id (closes #441). The FT-991A reports 1035 (RIG_MODEL_FT991),
    # not the 2028 FTX-1 default; the FTX-1 keeps reporting 2028.
    from rigplane.rigctld.routing import YaesuRouting

    ft991a = YaesuCatRadio("/dev/null", profile=load_rig(RIG))
    assert ft991a.hamlib_model_id == 1035
    assert YaesuRouting(ft991a, 100.0).dump_state()[1] == "1035"
    ftx1 = YaesuCatRadio("/dev/null", profile=load_rig(ROOT / "rigs/ftx1.toml"))
    assert YaesuRouting(ftx1, 100.0).dump_state()[1] == "2028"


def test_ft991a_omits_nb_nr_tuner_and_sub_mode():
    # MOR-4433 v1 omissions: NB/NR need a shared switch-read mechanism,
    # the AC tuner encoding needs typed native-state data, and the MD
    # selector is fixed at 0 (manual PDF p.12). Nothing may expect them.
    profile = load_rig(RIG).to_profile()
    for capability in ("nb", "nr", "tuner"):
        assert capability not in profile.capabilities
    for command in (
        "set_nb",
        "set_nr",
        "get_nb_level",
        "set_nb_level",
        "get_nr_level",
        "set_nr_level",
        "get_tuner",
        "set_tuner",
        "get_tuner_status",
        "set_tuner_status",
        "get_mode_sub",
        "set_mode_sub",
    ):
        assert not profile.supports_command(command), command


@pytest.mark.asyncio
async def test_sub_mode_read_refuses_on_the_single_receiver(radio):
    # The ordinary single-receiver path (receiver 0) is unaffected; only
    # the sub-receiver form refuses (the command is undeclared, so the
    # backend raises before any wire traffic).
    radio._transport.query = AsyncMock(side_effect=["MD02"])
    mode, _ = await radio.get_mode(0)
    assert mode == "USB"
    assert radio._transport.query.call_count == 1
    with pytest.raises(CommandError, match="get_mode_sub"):
        await radio.get_mode(1)
    assert radio._transport.query.call_count == 1


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
    # Manual PDF p.16: the RM answer is P1 plus three P2 digits (0-255), so
    # COMP (P1 3) reading 3 arrives as "RM3003" (the transport strips ";").
    radio._transport.query.side_effect = ["RM3003"]
    assert await radio.read_comp_meter() == 3
    # The headless PC answer likewise arrives without ";".
    radio._transport.query.side_effect = ["PC075"]
    assert await radio.read_power() == (0, 75)


@pytest.mark.asyncio
async def test_width_maps_narrow_and_wide_tables_by_na_state(radio):
    # MD02 reads USB, whose rule declares the narrow variant, so the NA
    # state selects the SH table AND its code base (MOR-4433: narrow base
    # 01, wide base 09). SH code 00 is the mode-dependent "(Default)"
    # (manual PDF p.17) and carries no Hz by design -- real codes go here:
    # code 07 is 1500 Hz narrow, code 14 is 2400 Hz wide. The wire carries
    # two-digit codes (manual SH table, PDF p.17): SH007;/SH014;.
    radio._transport.query = AsyncMock(side_effect=["MD02", "SH007", "NA01"])
    assert await radio.get_filter_width() == 1500
    radio._transport.query = AsyncMock(side_effect=["MD02", "SH014", "NA00"])
    assert await radio.get_filter_width() == 2400


@pytest.mark.asyncio
async def test_set_filter_width_uses_the_active_table_base(radio):
    # USB wide 2400 Hz is table index 5 from base 09 -> SH014;, not SH06;
    radio._transport.query = AsyncMock(side_effect=["MD02", "NA00"])
    await radio.set_filter_width(2400)
    radio._transport.write.assert_called_once_with("SH014;")
    # USB narrow 1500 Hz is table index 6 from base 01 -> SH07;.
    radio._transport.query = AsyncMock(side_effect=["MD02", "NA01"])
    radio._transport.write.reset_mock()
    await radio.set_filter_width(1500)
    radio._transport.write.assert_called_once_with("SH007;")


@pytest.mark.asyncio
async def test_shared_boundary_code_reads_1800_hz_in_both_states(radio):
    # Manual PDF p.17: code 09 is 1800 Hz in BOTH SSB states (narrow
    # index 8 from base 01, wide index 0 from base 09).
    radio._transport.query = AsyncMock(side_effect=["MD02", "SH009", "NA01"])
    assert await radio.get_filter_width() == 1800
    radio._transport.query = AsyncMock(side_effect=["MD02", "SH009", "NA00"])
    assert await radio.get_filter_width() == 1800


@pytest.mark.asyncio
async def test_cw_wide_table_counts_from_code_10(radio):
    # MD03 reads CW-U: CW wide 2400 Hz is table index 6 from base 10 -> SH16;.
    radio._transport.query = AsyncMock(side_effect=["MD03", "NA00"])
    await radio.set_filter_width(2400, receiver=0)
    radio._transport.write.assert_called_once_with("SH016;")
    radio._transport.query = AsyncMock(side_effect=["MD03", "SH016", "NA00"])
    assert await radio.get_filter_width() == 2400


@pytest.mark.asyncio
async def test_narrow_only_code_in_wide_state_reads_no_hz(radio):
    # SH03 is a narrow-table code; in the wide state (base 09) it is
    # outside the active table: (None, False), as for any out-of-table code.
    radio._transport.query = AsyncMock(side_effect=["MD02", "SH003", "NA00"])
    reading = await radio.read_filter_width_reading()
    assert reading.hz is None
    assert reading.radio_default is False


@pytest.mark.asyncio
async def test_sh_literal_frames_match_the_manual_table(radio):
    # Manual PDF p.17: SSB wide 2400 Hz is code 14, answered and written
    # as SH014; -- the extra digit of the old three-digit shape (SH0014;)
    # is rejected by the parse template.
    radio._transport.query = AsyncMock(side_effect=["MD02", "SH014", "NA00"])
    reading = await radio.read_filter_width_reading()
    assert (reading.hz, reading.radio_default) == (2400, False)
    radio._transport.write.reset_mock()
    radio._transport.query = AsyncMock(side_effect=["MD02", "SH0014", "NA00"])
    with pytest.raises(CatParseError):
        await radio.get_filter_width()


@pytest.mark.asyncio
async def test_sh_reset_code_00_reads_radio_default_and_writes_two_digits(
    radio,
):
    # Manual PDF p.17: code 00 is the mode-dependent "(Default)". The
    # reset command writes SH000;; a read of SH000; reports the default.
    await radio.reset_filter_width()
    radio._transport.write.assert_called_once_with("SH000;")
    radio._transport.query = AsyncMock(side_effect=["MD02", "SH000", "NA00"])
    reading = await radio.read_filter_width_reading()
    assert reading.hz is None
    assert reading.radio_default is True


@pytest.mark.asyncio
async def test_if_shift_literal_frames_match_the_manual_table(radio):
    # Manual PDF p.11: one fixed P1 digit, the sign and four offset
    # digits -- IS0+0200; for +200 Hz. The old two-zero shape
    # (IS00+0200;) is rejected by the parse template.
    radio._transport.query = AsyncMock(side_effect=["IS0+0200"])
    assert await radio.get_if_shift() == 200
    await radio.set_if_shift(200)
    radio._transport.write.assert_called_once_with("IS0+0200;")
    radio._transport.write.reset_mock()
    radio._transport.query = AsyncMock(side_effect=["IS0-0200"])
    assert await radio.get_if_shift() == -200
    await radio.set_if_shift(-200)
    radio._transport.write.assert_called_once_with("IS0-0200;")
    radio._transport.query = AsyncMock(side_effect=["IS00+0200"])
    with pytest.raises(CatParseError):
        await radio.get_if_shift()


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
async def test_equalize_writes_ab(radio):
    # Unit-level wire fact; the API-to-outcome coverage (enqueue through
    # public state) lives in the HTTP + CAT-simulator harness in
    # tests/test_yaesu_split_vfo_profile.py, which keeps the real
    # handler, queue, dispatch and readback active.
    await radio.equalize_vfo_ab(0)
    radio._transport.write.assert_called_once_with("AB;")


@pytest.mark.asyncio
async def test_equalize_still_refused_without_vfo_a_to_b(tmp_path):
    path = tmp_path / "no-copy.toml"
    # Drop both directional-copy declarations with their exact file text:
    # a whitespace-mismatched replace would silently keep the primitive
    # and the variant would still admit equalize.
    text = FIXTURE.read_text()
    text = text.replace('vfo_a_to_b = { cat = { write = "AB;" } }', "")
    text = text.replace('vfo_b_to_a = { cat = { write = "BA;" } }', "")
    path.write_text(text)
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


def test_serialized_filter_config_carries_no_narrow_table():
    # MOR-4433: the wire contract (FilterModeConfig) has no narrow-table
    # field and nothing consumes one, so the projection omits it even
    # where the profile declares the variant. The narrow table stays
    # server-side, where admitted_filter_width maps through it.
    for path in (RIG, ROOT / "rigs/ftx1.toml"):
        config = _serialize_filter_config(load_rig(path).to_profile())
        assert config, path
        for rule in config.values():
            assert "narrowTable" not in rule, path


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
async def test_cli_model_selects_ft991a_profile_through_factory(monkeypatch):
    """MOR-4433: the documented entry points drive the FT-991A profile.

    ``rigplane --model FT-991A`` builds a ``YaesuCatBackendConfig``
    carrying the model (the CLI forwards it); the factory resolves it to
    ``yaesu_ft991a`` instead of the ``"ftx1"`` default, and the assembled
    radio completes the identity handshake with a fake FT-991A answering
    ``ID0670;``. Without a model the factory keeps the FTX-1 default,
    exactly as before.
    """
    from rigplane.backends.config import YaesuCatBackendConfig
    from rigplane.backends.factory import create_radio
    from rigplane.cli import _build_backend_config, _build_parser

    args = _build_parser().parse_args(
        ["--model", "FT-991A", "--serial-port", "/dev/fixture", "status"]
    )
    config = await _build_backend_config(args)
    assert isinstance(config, YaesuCatBackendConfig)
    assert config.model == "FT-991A"
    fake = YaesuCatSerialFake.from_profile(ROOT, SIM_PROFILE, rig_path=RIG)

    async def open_port(*args, **kwargs):
        return await fake.open_serial_connection()

    monkeypatch.setattr(
        "rigplane.backends.yaesu_cat.transport.open_serial_port", open_port
    )
    radio = create_radio(config)
    assert radio._config.id == "yaesu_ft991a"
    await radio._transport.connect()
    try:
        assert await radio.get_id() == "0670"
    finally:
        await radio._transport.close()
    fallback = create_radio(YaesuCatBackendConfig(device="/dev/null"))
    assert fallback._config.id == "yaesu_ftx1"


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
        # The stored SH code is unchanged; the narrow table re-maps it:
        # code 09 is 1800 Hz wide AND narrow (manual PDF p.17), so the
        # width stays 1800 across the state flip.
        assert await radio.get_filter_width() == 1800
        await radio.set_filter_width(400)
        assert await radio.get_filter_width() == 400
        await radio.set_narrow(False)
        # SH02 from base 09 is outside the wide table: no Hz, as for any
        # out-of-table code.
        assert await radio.get_filter_width() is None
        await YaesuCatPoller(radio)._poll_slow()
        assert not any(cmd.startswith(("VS", "FR", "ST")) for cmd in wire)
        assert fake.model.handle("ID;") == "ID0670;"
        for command in ("VS;", "FR;", "ST;"):
            assert fake.model.handle(command) == "?;"
    finally:
        await radio._transport.close()


def test_factory_forwards_the_model_in_both_yaesu_branches():
    """MOR-4433: explicit model resolves its own profile; no model keeps FTX-1."""
    from rigplane.backends.config import SerialBackendConfig, YaesuCatBackendConfig
    from rigplane.backends.factory import create_radio

    cases = (
        (YaesuCatBackendConfig(device="/dev/null", model="FTX-1"), "yaesu_ftx1"),
        (YaesuCatBackendConfig(device="/dev/null", model="FT-991A"), "yaesu_ft991a"),
        (YaesuCatBackendConfig(device="/dev/null"), "yaesu_ftx1"),
        (SerialBackendConfig(device="/dev/null", model="FTX-1"), "yaesu_ftx1"),
        (SerialBackendConfig(device="/dev/null", model="FT-991A"), "yaesu_ft991a"),
        # Yaesu models routed to the CAT backend without a shipped profile
        # keep the pre-MOR-4433 FTX-1 default (no new failure for them).
        (SerialBackendConfig(device="/dev/null", model="FT-710"), "yaesu_ftx1"),
        (SerialBackendConfig(device="/dev/null", model="FTDX10"), "yaesu_ftx1"),
    )
    for config, profile_id in cases:
        assert create_radio(config)._config.id == profile_id
