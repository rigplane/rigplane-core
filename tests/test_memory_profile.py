"""``[memory]`` profile section and ``memory_read`` capability (MOR-3627)."""

from __future__ import annotations

import dataclasses
from dataclasses import replace
from pathlib import Path

import pytest

from rigplane.profiles import MemoryProfile, get_radio_profile
from rigplane.profiles import RadioProfile
from rigplane.profiles.rig_loader import RigConfig, RigLoadError, load_rig
from test_profile_identity_metadata import _minimal_toml, _write_toml


def test_shipped_profiles_declare_memory_addressing_and_capability() -> None:
    ic705 = get_radio_profile("IC-705")
    # write_layout participates in dataclass equality; strip it here so
    # this test stays about the addressing facts (the write layouts have
    # their own assertions below).
    assert replace(ic705.memory, write_layout=None) == MemoryProfile(
        "group_channel", 0, 99, 0, 99, recall=True
    )
    ic9700 = get_radio_profile("IC-9700")
    assert replace(
        ic9700.memory, write_layout=None, frequency_ranges=()
    ) == MemoryProfile(
        "band_channel", 1, 3, 1, 99, dd_groups=(3,), offset_10mhz_groups=(3,)
    )
    assert ic9700.memory.dd_groups == (3,)
    assert ic705.memory.dd_groups == ()
    # The capability is declared together with the reader (list API PR).
    for profile in (ic705, ic9700):
        assert "memory_read" in profile.capabilities
    # IC-7300 gained a write-side [memory] section (MOR-3644 part 2); the
    # read listing stays off: no memory_read capability was added.
    ic7300 = get_radio_profile("IC-7300")
    assert replace(ic7300.memory, write_layout=None) == MemoryProfile(
        "channel", 0, 0, 1, 99
    )
    assert "memory_read" not in ic7300.capabilities


def test_shipped_profiles_declare_the_verified_write_layout() -> None:
    """MOR-3644: every shipped profile with memory commands declares a
    structurally valid write layout; the radios without one (FTX-1: the
    Yaesu backend has no write path; X6100/X6200: commands declared
    absent, no [memory] section) refuse writes."""
    for model in ("IC-7610", "IC-705", "IC-9700", "IC-7300", "IC-7300MK2"):
        memory = get_radio_profile(model).memory
        assert memory is not None, model
        assert memory.write_layout is not None, model
        assert not memory.write_layout.validation_errors(), model
    ftx1 = get_radio_profile("FTX-1").memory
    assert ftx1 is not None and ftx1.write_layout is None
    assert get_radio_profile("X6100").memory is None


def _load(tmp_path: Path, memory: str):
    return load_rig(_write_toml(tmp_path, _minimal_toml(memory)))


_GOOD = """
[memory]
addressing = "band_channel"
first_group = 1
last_group = 3
first_channel = 1
last_channel = 99
"""


def test_loader_reads_the_memory_section(tmp_path: Path) -> None:
    config = _load(tmp_path, _GOOD)
    assert config.to_profile().memory == MemoryProfile("band_channel", 1, 3, 1, 99)
    assert _load(tmp_path, "").memory is None


def test_loader_reads_memory_group_rules(tmp_path: Path) -> None:
    memory = _load(
        tmp_path, _GOOD + "dd_groups = [3]\noffset_10mhz_groups = [3]\n"
    ).memory
    assert memory.dd_groups == (3,)
    assert memory.offset_10mhz_groups == (3,)
    defaults = MemoryProfile("band_channel", 1, 3, 1, 99)
    assert defaults.dd_groups == defaults.offset_10mhz_groups == ()
    fields = {f.name: f for f in dataclasses.fields(MemoryProfile)}
    assert fields["dd_groups"].kw_only and fields["offset_10mhz_groups"].kw_only


def test_loader_reads_optional_memory_frequency_ranges(tmp_path: Path) -> None:
    memory = _load(
        tmp_path,
        _GOOD
        + "frequency_ranges = [{group = 1, min_hz = 144000000, max_hz = 148000000}]\n",
    ).memory
    assert memory.frequency_ranges == ((1, 144_000_000, 148_000_000),)
    assert _load(tmp_path, _GOOD).memory.frequency_ranges == ()
    assert get_radio_profile("IC-705").memory.frequency_ranges == ()
    assert get_radio_profile("IC-9700").memory.frequency_ranges == (
        (1, 144_000_000, 148_000_000),
        (2, 430_000_000, 450_000_000),
        (3, 1_240_000_000, 1_300_000_000),
    )


@pytest.mark.parametrize(
    "ranges",
    [
        "1",
        "{}",
        "[1]",
        "[{group = true, min_hz = 1, max_hz = 2}]",
        "[{group = 0, min_hz = 1, max_hz = 2}]",
        "[{group = 4, min_hz = 1, max_hz = 2}]",
        "[{group = 1, min_hz = true, max_hz = 2}]",
        "[{group = 1, min_hz = 1, max_hz = 2.0}]",
        "[{group = 1, min_hz = 0, max_hz = 2}]",
        "[{group = 1, min_hz = 3, max_hz = 2}]",
        "[{group = 1, min_hz = 1}]",
        "[{group = 1, min_hz = 1, max_hz = 2, extra = 1}]",
        "[{group = 1, min_hz = 1, max_hz = 2}, {group = 1, min_hz = 2, max_hz = 3}]",
    ],
)
def test_loader_rejects_invalid_memory_frequency_ranges(
    tmp_path: Path, ranges: str
) -> None:
    with pytest.raises(RigLoadError, match=r"\[memory\].frequency_ranges"):
        _load(tmp_path, _GOOD + f"frequency_ranges = {ranges}\n")


@pytest.mark.parametrize("key", ["dd_groups", "offset_10mhz_groups"])
@pytest.mark.parametrize(
    "value", ["[0]", "[4]", "[3, 3]", '["3"]', "[true]", "[1.5]", "3", '"3"', "{}"]
)
def test_loader_rejects_invalid_memory_group_rules(
    tmp_path: Path, key: str, value: str
) -> None:
    with pytest.raises(RigLoadError, match=rf"\[memory\].{key}"):
        _load(tmp_path, _GOOD + f"{key} = {value}\n")


@pytest.mark.parametrize(
    "edit",
    [
        ('addressing = "band_channel"', 'addressing = "tape"'),
        ("first_group = 1", 'first_group = "1"'),
        ("first_group = 1", "first_group = 5"),  # first > last
        ("last_channel = 99", "last_channel = 0"),  # first > last
        ("last_channel = 99", "last_channel = 10000"),
        ("last_channel = 99", "last_channel = 99\nrecall = 1"),
        ("last_channel = 99", "last_channel = 99\nstray = 1"),
        ("last_channel = 99", "last_channel = 99\nrecall = true"),  # band_channel
        ('addressing = "band_channel"', "addressing = []"),
        ('addressing = "band_channel"', "addressing = {}"),
        ("last_group = 3", "last_group = 100"),  # a band code is one BCD byte
    ],
)
def test_loader_rejects_a_bad_memory_section(
    tmp_path: Path, edit: tuple[str, str]
) -> None:
    with pytest.raises(RigLoadError, match=r"\[memory\]"):
        _load(tmp_path, _GOOD.replace(*edit))


def test_largest_accepted_band_code_loads(tmp_path: Path) -> None:
    config = _load(tmp_path, _GOOD.replace("last_group = 3", "last_group = 99"))
    assert config.to_profile().memory.last_group == 99


def test_recall_is_accepted_only_for_group_channel_addressing(tmp_path: Path) -> None:
    group = _GOOD.replace("band_channel", "group_channel").replace(
        "last_channel = 99", "last_channel = 99\nrecall = true"
    )
    assert _load(tmp_path, group).to_profile().memory.recall is True


_GOOD_WRITE_LAYOUT = """
[memory.write_layout]
clear = "1a00_ff"
record_length = 10
fields = [
  { field = "frequency", offset = 0, length = 5, encoding = "frequency" },
  { field = "mode", offset = 5, length = 1, encoding = "mode" },
  { field = "name", offset = 6, length = 4, encoding = "name" },
]
"""


def test_loader_reads_the_write_layout(tmp_path: Path) -> None:
    from rigplane.core.types import MemoryWriteField, MemoryWriteLayout

    declared = _load(tmp_path, _GOOD + _GOOD_WRITE_LAYOUT).memory
    assert declared is not None
    assert declared.write_layout == MemoryWriteLayout(
        clear="1a00_ff",
        record_length=10,
        fields=(
            MemoryWriteField("frequency", 0, 5, "frequency"),
            MemoryWriteField("mode", 5, 1, "mode"),
            MemoryWriteField("name", 6, 4, "name"),
        ),
    )
    # A record without DTCS declares the tone-mode ceiling (IC-7300/MK2).
    capped = _load(
        tmp_path,
        _GOOD
        + _GOOD_WRITE_LAYOUT.replace(
            'clear = "1a00_ff"', 'clear = "1a00_ff"\ntone_max = 2'
        ),
    ).memory
    assert capped is not None and capped.write_layout is not None
    assert capped.write_layout.tone_max == 2
    # The default refuses: no declaration, no memory writes or clears.
    defaults = _load(tmp_path, _GOOD).memory
    assert defaults is not None
    assert defaults.write_layout is None
    assert dataclasses.fields(MemoryProfile)[-1].kw_only


def test_loader_reads_the_block_copy_count(tmp_path: Path) -> None:
    """``copies`` is plain layout data (MOR-3644): 1 by default, 2 for the
    manuals whose record carries the channel block twice (the TX/split
    copy), which needs the select byte at offset 0 and the name after the
    block."""
    from rigplane.core.types import MemoryWriteField, MemoryWriteLayout

    # The one-block layout above defaults to a single copy.
    single = _load(tmp_path, _GOOD + _GOOD_WRITE_LAYOUT).memory
    assert single is not None and single.write_layout is not None
    assert single.write_layout.copies == 1
    two_block = """
[memory.write_layout]
clear = "1a00_ff"
record_length = 15
copies = 2
fields = [
  { field = "select", offset = 0, length = 1, encoding = "select" },
  { field = "frequency", offset = 1, length = 5, encoding = "frequency" },
  { field = "mode", offset = 6, length = 1, encoding = "mode" },
  { field = "name", offset = 7, length = 8, encoding = "name" },
]
"""
    doubled = _load(tmp_path, _GOOD + two_block).memory
    assert doubled is not None
    assert doubled.write_layout == MemoryWriteLayout(
        clear="1a00_ff",
        record_length=15,
        copies=2,
        fields=(
            MemoryWriteField("select", 0, 1, "select"),
            MemoryWriteField("frequency", 1, 5, "frequency"),
            MemoryWriteField("mode", 6, 1, "mode"),
            MemoryWriteField("name", 7, 8, "name"),
        ),
    )
    assert doubled.write_layout.data_block_span() == (1, 7)
    # The wire record: select + 2 x the 6-byte block + the 8-byte name.
    assert doubled.write_layout.wire_record_length() == 21


@pytest.mark.parametrize("copies", ['"2"', "true", "0", "3", "-1"])
def test_loader_rejects_an_out_of_range_copy_count(tmp_path: Path, copies: str) -> None:
    with pytest.raises(RigLoadError, match=r"\[memory\].write_layout.copies"):
        _load(tmp_path, _GOOD + _GOOD_WRITE_LAYOUT + f"\ncopies = {copies}\n")


def test_loader_rejects_a_copy_count_without_a_block_span(tmp_path: Path) -> None:
    """copies = 2 on a table with no leading select field (the repeat
    starts after it) is a structural error, not a silently wrong wire."""
    with pytest.raises(RigLoadError, match="copies > 1 needs the select field"):
        _load(tmp_path, _GOOD + _GOOD_WRITE_LAYOUT + "\ncopies = 2\n")


@pytest.mark.parametrize(
    "edit",
    [
        ('clear = "1a00_ff"', 'clear = "civ_26"'),  # not a clear form
        ('clear = "1a00_ff"', "clear = []"),  # junk member type
        ("record_length = 10", "record_length = 0"),
        ("record_length = 10", 'record_length = "10"'),
        ("record_length = 10", "record_length = 11"),  # undeclared tail byte
        (
            'encoding = "mode"',
            'encoding = "civ_47"',
        ),  # not a vocabulary encoding
        (
            '{ field = "mode", offset = 5, length = 1, encoding = "mode" },',
            '{ field = "mode", offset = 6, length = 1, encoding = "mode" },',  # gap
        ),
        (
            '{ field = "mode", offset = 5, length = 1, encoding = "mode" },',
            '{ field = "mode", offset = 4, length = 1, encoding = "mode" },',  # overlap
        ),
        ("record_length = 10", "record_length = 8"),  # name runs past
        (
            '{ field = "name", offset = 6, length = 4, encoding = "name" },',
            '{ field = "name", offset = 6, length = 4, encoding = "name" },\n'
            '  { field = "name2", offset = 6, length = 4, encoding = "name" },',  # two name fields
        ),
        (
            '{ field = "mode", offset = 5, length = 1, encoding = "mode" },',
            '{ field = "mode", offset = 5, length = 2, encoding = "mode" },',  # wrong width
        ),
        (
            '{ field = "frequency", offset = 0, length = 5, encoding = "frequency" },',
            '{ field = "frequency", offset = 0, length = 5, encoding = "frequency", max_hz = 0 },',  # bad max_hz
        ),
        (
            '{ field = "mode", offset = 5, length = 1, encoding = "mode" },',
            '{ field = "mode", offset = 5, length = 1, encoding = "mode", max_hz = 100 },',  # max_hz off frequency
        ),
        ("fields = [", "stray = 1\nfields = ["),  # unknown key
    ],
)
def test_loader_rejects_a_broken_write_layout(
    tmp_path: Path, edit: tuple[str, str]
) -> None:
    with pytest.raises(RigLoadError, match=r"\[memory\].write_layout"):
        _load(tmp_path, _GOOD + _GOOD_WRITE_LAYOUT.replace(*edit))


@pytest.mark.parametrize("tone_max", ["4", '"2"', "true", "-1"])
def test_loader_rejects_an_out_of_range_tone_max(tmp_path: Path, tone_max: str) -> None:
    with pytest.raises(RigLoadError, match=r"\[memory\].write_layout.tone_max"):
        _load(tmp_path, _GOOD + _GOOD_WRITE_LAYOUT + f"\ntone_max = {tone_max}\n")


# A layout with tone/tsql fields: an unset tone writes the declared
# default (a zero field is not a valid CTCSS value), so the default is
# required here and must be a member of the profile's CTCSS domain.
_TONE_WRITE_LAYOUT = """
[memory.write_layout]
clear = "1a00_ff"
record_length = 16
default_tone_centihz = 8850
fields = [
  { field = "frequency", offset = 0, length = 5, encoding = "frequency" },
  { field = "mode", offset = 5, length = 1, encoding = "mode" },
  { field = "tone", offset = 6, length = 3, encoding = "tone" },
  { field = "tsql", offset = 9, length = 3, encoding = "tsql" },
  { field = "dtcs", offset = 12, length = 3, encoding = "fixed", value = "000023" },
  { field = "name", offset = 15, length = 1, encoding = "name" },
]
"""

_CTCSS_CATALOG = (
    "schema_version = 1\n\n[tables.standard_50]\nvalues_centihz = [8850, 12300]\n"
)


def _load_with_ctcss(tmp_path: Path, memory: str):
    (tmp_path / "_ctcss_tables_v1.toml").write_text(_CTCSS_CATALOG)
    return load_rig(
        _write_toml(
            tmp_path, _minimal_toml('[ctcss]\ntable = "standard_50"\n' + memory)
        )
    )


def test_loader_reads_the_fixed_encoding_and_the_tone_default(tmp_path: Path) -> None:
    """``fixed`` is the one way to declare a constant byte run (its hex
    ``value`` in the profile), and ``default_tone_centihz`` is the tone an
    unset tone/tsql field carries."""
    declared = _load_with_ctcss(tmp_path, _GOOD + _TONE_WRITE_LAYOUT).memory
    assert declared is not None and declared.write_layout is not None
    layout = declared.write_layout
    assert layout.default_tone_centihz == 8850
    dtcs = next(field for field in layout.fields if field.field == "dtcs")
    assert dtcs.encoding == "fixed" and dtcs.value == "000023"


@pytest.mark.parametrize(
    "edit",
    [
        ('value = "000023"', 'value = "0002"'),  # shorter than the field
        ('value = "000023"', 'value = "0000zz"'),  # not hex
        ('value = "000023"', "value = 23"),  # not a string
        (', value = "000023" },', " },"),  # a fixed field without a value
        (
            '{ field = "mode", offset = 5, length = 1, encoding = "mode" },',
            '{ field = "mode", offset = 5, length = 1, encoding = "mode", value = "00" },',  # value off a fixed field
        ),
        ("default_tone_centihz = 8850", "default_tone_centihz = 9999"),  # off-domain
        ("default_tone_centihz = 8850", 'default_tone_centihz = "8850"'),
    ],
)
def test_loader_rejects_a_broken_fixed_field_or_tone_default(
    tmp_path: Path, edit: tuple[str, str]
) -> None:
    with pytest.raises(RigLoadError, match=r"\[memory\].write_layout"):
        _load_with_ctcss(tmp_path, _GOOD + _TONE_WRITE_LAYOUT.replace(*edit))


def test_loader_requires_the_tone_default_with_tone_fields(tmp_path: Path) -> None:
    with pytest.raises(RigLoadError, match=r"default_tone_centihz"):
        _load_with_ctcss(
            tmp_path,
            _GOOD + _TONE_WRITE_LAYOUT.replace("default_tone_centihz = 8850\n", ""),
        )


def test_loader_rejects_a_tone_default_without_tone_fields(tmp_path: Path) -> None:
    with pytest.raises(RigLoadError, match=r"default_tone_centihz"):
        _load(
            tmp_path,
            _GOOD
            + _GOOD_WRITE_LAYOUT.replace(
                'clear = "1a00_ff"',
                'clear = "1a00_ff"\ndefault_tone_centihz = 8850',
            ),
        )


@pytest.mark.parametrize("value", ['"civ_26"', "3", "[]", "true", '""'])
def test_loader_rejects_a_non_table_write_layout(tmp_path: Path, value: str) -> None:
    with pytest.raises(RigLoadError, match=r"\[memory\].write_layout"):
        _load(tmp_path, _GOOD + f"write_layout = {value}\n")


def test_a_channel_list_has_only_group_zero(tmp_path: Path) -> None:
    flat = _GOOD.replace("band_channel", "channel").replace(
        "first_group = 1", "first_group = 0"
    )
    with pytest.raises(RigLoadError, match="only group 0"):
        _load(tmp_path, flat)
    ok = flat.replace("last_group = 3", "last_group = 0")
    assert _load(tmp_path, ok).to_profile().memory == MemoryProfile(
        "channel", 0, 0, 1, 99
    )


@pytest.mark.parametrize("cls", [RigConfig, RadioProfile])
def test_memory_is_keyword_only_so_no_positional_argument_shifts(cls: type) -> None:
    by_name = {f.name: f for f in dataclasses.fields(cls)}
    assert by_name["memory"].kw_only is True
