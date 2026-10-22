"""``[memory]`` profile section and ``memory_read`` capability (MOR-3627)."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from rigplane.profiles import MemoryProfile, get_radio_profile
from rigplane.profiles import RadioProfile
from rigplane.profiles.rig_loader import RigConfig, RigLoadError, load_rig
from test_profile_identity_metadata import _minimal_toml, _write_toml


def test_shipped_profiles_declare_memory_addressing_and_capability() -> None:
    ic705 = get_radio_profile("IC-705")
    assert ic705.memory == MemoryProfile("group_channel", 0, 99, 0, 99)
    ic9700 = get_radio_profile("IC-9700")
    assert ic9700.memory == MemoryProfile(
        "band_channel", 1, 3, 1, 99, dd_groups=(3,), offset_10mhz_groups=(3,)
    )
    assert ic9700.memory.dd_groups == (3,)
    assert ic705.memory.dd_groups == ()
    # The capability is declared together with the reader (list API PR).
    for profile in (ic705, ic9700):
        assert "memory_read" not in profile.capabilities
    # Radios without a [memory] section stay without the listing.
    for model in ("IC-7300", "IC-7610"):
        profile = get_radio_profile(model)
        assert profile.memory is None
        assert "memory_read" not in profile.capabilities


def _load(tmp_path: Path, memory: str):
    return load_rig(_write_toml(tmp_path, _minimal_toml(memory)))


_GOOD = """
[memory]
addressing = "band_channel"
first_group = 1
last_group = 3
first_channel = 1
last_channel = 99
recall = true
"""


def test_loader_reads_the_memory_section(tmp_path: Path) -> None:
    config = _load(tmp_path, _GOOD)
    assert config.to_profile().memory == MemoryProfile(
        "band_channel", 1, 3, 1, 99, recall=True
    )
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
        ("recall = true", "recall = 1"),
        ("recall = true", "recall = true\nstray = 1"),
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


@pytest.mark.parametrize("cls", [RigConfig, RadioProfile])
def test_memory_is_keyword_only_so_no_positional_argument_shifts(cls: type) -> None:
    by_name = {f.name: f for f in dataclasses.fields(cls)}
    assert by_name["memory"].kw_only is True
