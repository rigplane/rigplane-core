"""MOR-4428: narrow-dependent Yaesu filter-width (SH) tables.

The single-receiver fixture declares per-mode ``narrow_table``: while
NARROW is on, an SH code maps through the narrow table instead of the
wide one. FTX-1 declares no narrow data
and keeps today's single-table behavior with no extra NA query.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.exceptions import CommandError
from rigplane.profiles import FilterWidthRule
from rigplane.profiles.rig_loader import RigLoadError, load_rig
from rigplane_testkit.sim.adapters.yaesu_cat import YaesuCatSerialFake

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests/fixtures/rigs/yaesu_single_rx_fixture.toml"
SIM_FIXTURE = ROOT / "rigplane_testkit/sim/profiles/yaesu-single-rx-fixture.toml"
FTX1 = ROOT / "rigs/ftx1.toml"
CTCSS_CATALOG = ROOT / "rigs/_ctcss_tables_v1.toml"


@pytest.fixture
def radio():
    result = YaesuCatRadio("/dev/null", profile=load_rig(FIXTURE))
    result._transport._connected = True
    result._transport.write = AsyncMock()
    result._transport.query = AsyncMock()
    return result


def _write_variant(tmp_path: Path, old: str, new: str) -> Path:
    """Copy the fixture with one text replacement applied."""
    text = FIXTURE.read_text()
    assert old in text, f"fixture anchor missing: {old!r}"
    path = tmp_path / "variant.toml"
    path.write_text(text.replace(old, new))
    return path


def _query_fn(answers: dict[str, str], *, forbid_na: bool = False):
    """Build a transport.query replacement serving canned answers."""

    async def query(cmd: str, **kwargs: object) -> str:
        if forbid_na:
            assert not cmd.startswith("NA"), f"unexpected narrow query {cmd!r}"
        return answers[cmd]

    return query


# ---------------------------------------------------------------------------
# Loader: narrow variant accepted
# ---------------------------------------------------------------------------


def test_fixture_narrow_variant_loads() -> None:
    config = load_rig(FIXTURE)
    assert config.filter_width_encoding == "table_index"
    ssb = config.filter_config["SSB"]
    assert ssb.table == (2400, 2600, 3000, 3200, 4000)
    assert ssb.narrow_table == (1500, 1800, 2000)
    cw = config.filter_config["CW"]
    assert cw.narrow_table == (200, 300, 500)
    fm = config.filter_config["FM"]
    assert fm.fixed
    assert fm.narrow_table == ()


def test_ftx1_loads_with_no_narrow_variant() -> None:
    """FTX-1 gains no narrow data: every rule keeps empty narrow tuples."""
    config = load_rig(FTX1)
    assert config.filter_config
    for mode, rule in config.filter_config.items():
        assert rule.narrow_table == (), mode


# ---------------------------------------------------------------------------
# Loader: narrow variant rejected
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("old", "new", "match"),
    [
        (
            'encoding = "table_index"',
            'encoding = "segmented_bcd_index"',
            "table_index",
        ),
        (
            "[filters.width.FM]\nfixed = true",
            "[filters.width.FM]\nfixed = true\nnarrow_table = [8000]",
            "fixed",
        ),
        (
            "table = [2400, 2600, 3000, 3200, 4000]",
            "",
            "narrow_table needs a",
        ),
        (
            "narrow_table = [1500, 1800, 2000]",
            "narrow_table = [1500, 1500, 2000]",
            "strictly increasing",
        ),
        (
            'get_narrow = { cat = { read = "NA0;", parse = "NA0{state};" } }',
            'get_narrow = { absent = "Fixture variant" }',
            "get_narrow",
        ),
    ],
)
def test_narrow_variant_rejected(
    tmp_path: Path, old: str, new: str, match: str
) -> None:
    path = _write_variant(tmp_path, old, new)
    with pytest.raises(RigLoadError, match=match):
        load_rig(path)


def test_dual_receiver_variant_loads_with_both_narrow_reads(
    tmp_path: Path,
) -> None:
    """FTX-1 (two receivers, both NA reads) accepts an SSB narrow table."""
    text = FTX1.read_text()
    anchor = "defaults = [2400, 1800, 300]"
    assert anchor in text
    (tmp_path / "_ctcss_tables_v1.toml").write_text(CTCSS_CATALOG.read_text())
    path = tmp_path / "ftx1-narrow.toml"
    path.write_text(
        text.replace(anchor, anchor + "\nnarrow_table = [300, 600, 1200]", 1)
    )
    rule = load_rig(path).filter_config["SSB"]
    assert rule.narrow_table == (300, 600, 1200)


def test_dual_receiver_variant_rejects_missing_sub_narrow_read(
    tmp_path: Path,
) -> None:
    text = FTX1.read_text()
    anchor = "defaults = [2400, 1800, 300]"
    assert anchor in text
    lines = [
        'get_narrow_sub = { absent = "Fixture variant" }'
        if line.startswith("get_narrow_sub")
        else line
        for line in text.replace(
            anchor, anchor + "\nnarrow_table = [300, 600, 1200]", 1
        ).splitlines()
    ]
    (tmp_path / "_ctcss_tables_v1.toml").write_text(CTCSS_CATALOG.read_text())
    path = tmp_path / "ftx1-narrow-no-sub.toml"
    path.write_text("\n".join(lines))
    with pytest.raises(RigLoadError, match="get_narrow_sub"):
        load_rig(path)


# ---------------------------------------------------------------------------
# Radio: narrow/wide mapping over a mocked transport
# ---------------------------------------------------------------------------


def _wire(radio: YaesuCatRadio) -> list[str]:
    return [c.args[0] for c in radio._transport.query.call_args_list]


@pytest.mark.asyncio
async def test_read_wide_state_maps_through_wide_table(
    radio: YaesuCatRadio,
) -> None:
    """USB SH00 reads 2400 Hz when NARROW is off (NA0; still read)."""
    radio._transport.query.side_effect = _query_fn(
        {"MD0;": "MD02", "NA0;": "NA00", "SH0;": "SH0000"}
    )
    assert await radio.get_filter_width() == 2400
    assert _wire(radio) == ["MD0;", "SH0;", "NA0;"]


@pytest.mark.asyncio
async def test_read_narrow_selects_narrow_table(radio: YaesuCatRadio) -> None:
    """USB SH00 reads 1500 Hz narrow; NA0; is read before the SH answer is mapped."""
    radio._transport.query.side_effect = _query_fn({"NA0;": "NA01", "SH0;": "SH0000"})
    assert await radio.read_filter_width(mode="USB") == 1500
    assert _wire(radio) == ["SH0;", "NA0;"]


@pytest.mark.asyncio
async def test_read_without_mode_reads_mode_narrow_then_sh(
    radio: YaesuCatRadio,
) -> None:
    radio._transport.query.side_effect = _query_fn(
        {"MD0;": "MD02", "NA0;": "NA01", "SH0;": "SH0000"}
    )
    assert await radio.read_filter_width() == 1500
    assert _wire(radio) == ["MD0;", "SH0;", "NA0;"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("narrow_answer", "code_answer", "expected_hz"),
    [
        ("NA00", "SH0002", 3000),  # SSB code 2 is wide-table 3000 Hz
        ("NA01", "SH0002", 2000),  # ... and narrow-table 2000 Hz
        ("NA00", "SH0004", 4000),  # SSB code 4 exists only wide
    ],
)
async def test_same_code_maps_by_narrow_state(
    radio: YaesuCatRadio,
    narrow_answer: str,
    code_answer: str,
    expected_hz: int,
) -> None:
    radio._transport.query.side_effect = _query_fn(
        {"NA0;": narrow_answer, "SH0;": code_answer}
    )
    assert await radio.read_filter_width(mode="USB") == expected_hz


@pytest.mark.asyncio
async def test_code_invalid_in_narrow_state_reads_no_hz(
    radio: YaesuCatRadio,
) -> None:
    """SSB code 4 is outside the narrow table: (None, False), as today."""
    radio._transport.query.side_effect = _query_fn({"NA0;": "NA01", "SH0;": "SH0004"})
    reading = await radio.read_filter_width_reading(mode="USB")
    assert reading.hz is None
    assert reading.radio_default is False


@pytest.mark.asyncio
async def test_cw_pair_maps_by_narrow_state(radio: YaesuCatRadio) -> None:
    radio._transport.query.side_effect = _query_fn({"NA0;": "NA01", "SH0;": "SH0001"})
    assert await radio.read_filter_width(mode="CW") == 300
    radio._transport.query.side_effect = _query_fn({"NA0;": "NA00", "SH0;": "SH0001"})
    assert await radio.read_filter_width(mode="CW") == 800


@pytest.mark.asyncio
async def test_set_maps_hz_through_active_table(
    radio: YaesuCatRadio,
) -> None:
    """2400 Hz writes code 0 wide but code 2 (2000 Hz) narrow."""
    radio._transport.query.side_effect = _query_fn({"MD0;": "MD02", "NA0;": "NA00"})
    await radio.set_filter_width(2400)
    radio._transport.write.assert_called_once_with("SH0000;")
    assert _wire(radio) == ["MD0;", "NA0;"]
    radio._transport.query.side_effect = _query_fn({"MD0;": "MD02", "NA0;": "NA01"})
    radio._transport.write.reset_mock()
    await radio.set_filter_width(2400)
    radio._transport.write.assert_called_once_with("SH0002;")


@pytest.mark.asyncio
async def test_mode_without_narrow_variant_issues_no_na_query(
    tmp_path: Path,
) -> None:
    """A non-fixed rule without narrow data reads the wide table only."""
    path = _write_variant(
        tmp_path,
        "narrow_table = [200, 300, 500]\n",
        "",
    )
    radio = YaesuCatRadio("/dev/null", profile=load_rig(path))
    radio._transport._connected = True
    radio._transport.query = AsyncMock(
        side_effect=_query_fn({"SH0;": "SH0001"}, forbid_na=True)
    )
    assert await radio.read_filter_width(mode="CW") == 800
    assert [c.args[0] for c in radio._transport.query.call_args_list] == ["SH0;"]


@pytest.mark.asyncio
async def test_fixed_mode_reads_defaults_with_no_narrow_query(
    radio: YaesuCatRadio,
) -> None:
    radio._transport.query.side_effect = _query_fn({"SH0;": "SH0000"}, forbid_na=True)
    assert await radio.read_filter_width(mode="FM") == 9000
    assert _wire(radio) == ["SH0;"]


@pytest.mark.asyncio
async def test_fixed_mode_set_refuses_without_narrow_query(
    radio: YaesuCatRadio,
) -> None:
    radio._transport.query.side_effect = _query_fn({"MD0;": "MD04"}, forbid_na=True)
    with pytest.raises(CommandError, match="fixed-width"):
        await radio.set_filter_width(9000, receiver=0)
    assert _wire(radio) == ["MD0;"]


@pytest.mark.asyncio
async def test_ftx1_width_read_and_set_issue_no_narrow_query() -> None:
    """FTX-1 (no narrow data) keeps today's wire log exactly."""
    radio = YaesuCatRadio("/dev/null", profile=load_rig(FTX1))
    radio._transport._connected = True
    radio._transport.write = AsyncMock()
    radio._transport.query = AsyncMock(
        side_effect=_query_fn({"MD0;": "MD02", "SH0;": "SH0010"}, forbid_na=True)
    )
    assert await radio.get_filter_width() == 1950
    await radio.set_filter_width(2400)
    radio._transport.write.assert_called_once_with("SH0013;")
    assert [c.args[0] for c in radio._transport.query.call_args_list] == [
        "MD0;",
        "SH0;",
        "MD0;",
    ]


# ---------------------------------------------------------------------------
# Sim round trip through the real CAT transport (PR-A pattern)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_narrow_width_roundtrip_over_cat_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = YaesuCatSerialFake.from_profile(ROOT, SIM_FIXTURE, rig_path=FIXTURE)
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
    radio = YaesuCatRadio("/dev/fixture", profile=load_rig(FIXTURE))
    await radio._transport.connect()
    try:
        await radio.set_mode("USB")
        await radio.set_narrow(False)
        await radio.set_filter_width(2400)
        assert await radio.get_filter_width() == 2400
        await radio.set_narrow(True)
        # The stored SH code is unchanged; the narrow table re-maps it.
        assert await radio.get_filter_width() == 1500
        await radio.set_filter_width(2000)
        assert await radio.get_filter_width() == 2000
        await radio.set_narrow(False)
        # Code 2 in the wide table.
        assert await radio.get_filter_width() == 3000
        assert wire[-3:] == ["MD0;", "SH0;", "NA0;"]
    finally:
        await radio._transport.close()


def test_filter_width_rule_keeps_existing_positional_bindings() -> None:
    """Existing SDK callers construct ``FilterWidthRule((9000,), True)``."""
    rule = FilterWidthRule((9000,), True)
    assert rule.defaults == (9000,)
    assert rule.fixed is True
    assert rule.narrow_table == ()
