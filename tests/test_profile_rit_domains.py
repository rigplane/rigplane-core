"""MOR-1883: every declared ``controls.rit`` uses the exact domain form.

Since MOR-1730 the frontend accepts only the exact declaration
(mapping/raw_step/raw_origin/...); a legacy ``raw_center`` shape validates
as malformed and fail-closes the RIT/XIT offset control. This register-wide
guard makes a legacy-shaped declaration a test failure, so no profile can
silently regress to a disabled slider again.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

RIGS_DIR = Path(__file__).resolve().parents[1] / "rigs"
_EXACT_KEYS = {
    "mapping",
    "raw_min",
    "raw_max",
    "raw_step",
    "raw_origin",
    "display_min",
    "display_max",
    "display_step",
    "display_origin",
    "display_unit",
    "quantization",
    "restoration",
}

_RIT_PROFILES = sorted(
    path.name
    for path in RIGS_DIR.glob("*.toml")
    if "rit" in tomllib.loads(path.read_text()).get("controls", {})
)


def test_rit_declarations_exist() -> None:
    """The guard below must actually be guarding something."""
    assert _RIT_PROFILES, "no profile declares controls.rit anymore?"


@pytest.mark.parametrize("name", _RIT_PROFILES)
def test_declared_rit_domain_uses_the_exact_form(name: str) -> None:
    rit = tomllib.loads((RIGS_DIR / name).read_text())["controls"]["rit"]
    missing = _EXACT_KEYS - set(rit)
    assert not missing, (
        f"{name}: controls.rit is missing exact-form keys {sorted(missing)}; "
        "a legacy raw_center shape fail-closes the RIT/XIT control (MOR-1883)"
    )
    assert "raw_center" not in rit, f"{name}: legacy raw_center key present"
    assert rit["mapping"] in ("identity", "linear", "centered", "lookup")
    assert isinstance(rit["raw_origin"], int)
    assert rit["raw_min"] <= rit["raw_origin"] <= rit["raw_max"]


def test_ic7300_rit_domain_matches_the_wire_codec() -> None:
    """The bench radio's range is the CI-V builder's ±9999 Hz, 1 Hz steps.

    Evidence: ``commands/system.py::set_rit_frequency`` validates ±9999 Hz
    (1 Hz BCD); the owner wrote 4701 Hz to the live IC-7300 on 2026-08-15;
    the Full Manual gives RIT ±9.999 kHz. The old legacy ±999 contradicted
    all three.
    """
    rit = tomllib.loads((RIGS_DIR / "ic7300.toml").read_text())["controls"]["rit"]
    assert (rit["raw_min"], rit["raw_max"], rit["raw_step"], rit["raw_origin"]) == (
        -9999,
        9999,
        1,
        0,
    )
