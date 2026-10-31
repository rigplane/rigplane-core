"""MOR-2100: attenuator levels come from the profile, not constants."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from rigplane.profiles import get_radio_profile


@pytest.mark.parametrize(
    "key,expected",
    [("IC-7300", 20), ("IC-705", 20), ("FTX-1", 1)],
)
def test_attenuator_on_db_single_step(key: str, expected: int) -> None:
    """One declared non-zero step is the boolean "on" level."""
    assert get_radio_profile(key).attenuator_on_db() == expected


def test_attenuator_on_db_stepped_is_ambiguous() -> None:
    """Several non-zero steps (IC-7610) have no single "on" level."""
    assert get_radio_profile("IC-7610").attenuator_on_db() is None


def test_attenuator_on_db_missing_values_is_none() -> None:
    """No declared values means no "on" level either."""
    profile = dataclasses.replace(get_radio_profile("IC-7300"), att_values=None)
    assert profile.attenuator_on_db() is None
    empty = dataclasses.replace(get_radio_profile("IC-7300"), att_values=())
    assert empty.attenuator_on_db() is None


def test_no_attenuator_level_literals_in_source() -> None:
    """No hardcoded attenuator dB literal remains in the three modules."""
    root = Path(__file__).resolve().parent.parent / "src" / "rigplane"
    radio = (root / "runtime" / "radio.py").read_text()
    handler = (root / "rigctld" / "handler.py").read_text()
    cli = (root / "cli" / "__init__.py").read_text()
    assert "Attenuator: ON (18 dB)" not in cli
    assert "return 18 if" not in radio
    assert "(0, 6, 12, 18)" not in handler
    assert "(0, 6, 12, 18)" not in cli
