"""``[memory]`` profile section and ``memory_read`` capability (MOR-3627)."""

from __future__ import annotations

from pathlib import Path

import pytest

from rigplane.profiles import MemoryProfile, get_radio_profile
from rigplane.profiles.rig_loader import RigLoadError, load_rig
from test_profile_identity_metadata import _minimal_toml, _write_toml


def test_shipped_profiles_declare_memory_addressing_and_capability() -> None:
    ic705 = get_radio_profile("IC-705")
    assert ic705.memory == MemoryProfile("group_channel", 0, 99, 0, 99, recall=True)
    ic9700 = get_radio_profile("IC-9700")
    assert ic9700.memory == MemoryProfile("band_channel", 1, 3, 1, 99)
    for profile in (ic705, ic9700):
        assert "memory_read" in profile.capabilities
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
"""


def test_loader_reads_the_memory_section(tmp_path: Path) -> None:
    config = _load(tmp_path, _GOOD)
    assert config.to_profile().memory == MemoryProfile("band_channel", 1, 3, 1, 99)
    assert _load(tmp_path, "").memory is None


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
