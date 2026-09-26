"""MOR-2504: every shipped profile mode must be expressible by its backend.

A profile that lists a mode its backend cannot set or read back offers a
button the radio will refuse. Icom and Xiegu CI-V radios coerce the profile
label through ``Mode`` (hyphen to underscore). A Yaesu CAT radio expresses a
mode only when ``[modes].codes`` names it. A profile with no backend yet is
not on this path.
"""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from rigplane.profiles.rig_loader import RigConfig, discover_rigs
from rigplane.radio import IcomRadio

RIGS_DIR = Path(__file__).resolve().parent.parent / "rigs"

_ICOM_MODE_PROTOCOLS = frozenset({"civ"})
_YAESU_MODE_PROTOCOLS = frozenset({"yaesu_cat"})
# Labels with no Mode member at all. Pre-existing, recorded by MOR-1487;
# not a hyphen/underscore drift. strict xfail so a future mapping shows up
# as a failure here instead of staying invisible.
_KNOWN_UNMAPPED = {("X6200", "DIGI"), ("X6100", "DIGI")}


def _expressible(rig: RigConfig, label: str) -> bool:
    if rig.protocol_type in _ICOM_MODE_PROTOCOLS:
        try:
            IcomRadio._coerce_mode(label)
        except ValueError:
            return False
        return True
    if rig.protocol_type in _YAESU_MODE_PROTOCOLS:
        if rig.mode_codes is None or len(rig.mode_codes) != len(rig.modes):
            return False
        return bool(rig.mode_codes[rig.modes.index(label)])
    return False


def _shipped_mode_cases() -> list[object]:
    cases = []
    for model, rig in sorted(discover_rigs(RIGS_DIR).items()):
        if rig.protocol_type not in _ICOM_MODE_PROTOCOLS | _YAESU_MODE_PROTOCOLS:
            continue
        for label in rig.modes:
            marks = []
            if (model, label) in _KNOWN_UNMAPPED:
                marks.append(
                    pytest.mark.xfail(
                        strict=True,
                        reason=f"{model} {label!r} has no Mode member (MOR-1487)",
                    )
                )
            cases.append(
                pytest.param(rig, label, id=f"{model}:{label}", marks=marks)
            )
    return cases


@pytest.mark.parametrize(("rig", "label"), _shipped_mode_cases())
def test_shipped_profile_mode_is_expressible(rig: RigConfig, label: str) -> None:
    assert _expressible(rig, label), (
        f"{rig.model} lists {label!r}, which its {rig.protocol_type} backend "
        "cannot express"
    )


def test_planted_unknown_mode_fails_the_guard() -> None:
    rig = discover_rigs(RIGS_DIR)["IC-7610"]
    planted = replace(rig, modes=(*rig.modes, "NOT-A-MODE"))
    assert _expressible(planted, "NOT-A-MODE") is False
    with pytest.raises(AssertionError, match="NOT-A-MODE"):
        assert _expressible(planted, "NOT-A-MODE"), (
            f"{planted.model} lists 'NOT-A-MODE', which its "
            f"{planted.protocol_type} backend cannot express"
        )


def test_yaesu_mode_without_a_code_fails_the_guard() -> None:
    rig = discover_rigs(RIGS_DIR)["FTX-1"]
    planted = replace(rig, mode_codes=None)
    assert _expressible(planted, rig.modes[0]) is False
