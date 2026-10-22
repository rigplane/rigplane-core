"""MOR-3679: every shipped profile that has a CW mode names it in
``[modes] families.cw``, so no client keeps its own list of CW mode names
(MOR-3678: Pro's {CW, CWR, CW-R} missed the FTX-1's CW-U/CW-L).

The guard reads the profile TOML directly, so it sees every CW* name as
written; the vendor checks go through the loader and the runtime profile.
Vendor expectations are literals, never read back from the profile under
test.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from rigplane.rig_loader import load_rig

_RIGS_DIR = Path(__file__).resolve().parent.parent / "rigs"
_PROFILES = sorted(
    path for path in _RIGS_DIR.glob("*.toml") if not path.name.startswith("_")
)


def _modes(path: Path) -> dict[str, object]:
    with path.open("rb") as handle:
        modes = tomllib.load(handle)["modes"]
    assert isinstance(modes, dict)
    return modes


def test_profiles_found() -> None:
    assert len(_PROFILES) >= 9


@pytest.mark.parametrize("path", _PROFILES, ids=lambda path: path.name)
def test_profile_with_cw_mode_declares_cw_family(path: Path) -> None:
    modes = _modes(path)
    mode_list = modes["list"]
    assert isinstance(mode_list, list)
    cw_modes = [mode for mode in mode_list if str(mode).upper().startswith("CW")]
    if not cw_modes:
        pytest.skip(f"{path.name} lists no CW mode")
    families = modes.get("families")
    assert isinstance(families, dict), (
        f"{path.name}: CW modes {cw_modes} but no families"
    )
    assert "cw" in families, f"{path.name}: CW modes {cw_modes} but no families.cw"
    assert sorted(families["cw"]) == sorted(cw_modes), path.name


@pytest.mark.parametrize(
    ("vendor", "profile_file", "cw", "not_cw"),
    [
        ("icom", "ic7300.toml", ["CW", "CW-R"], ["USB", "LSB", "FM", "RTTY"]),
        ("yaesu", "ftx1.toml", ["CW-U", "CW-L"], ["USB", "LSB", "FM", "DATA-U"]),
        ("kenwood", "tx500.toml", ["CW", "CW-R"], ["USB", "LSB", "FM", "DIG"]),
    ],
)
def test_vendor_cw_family_exact(
    vendor: str, profile_file: str, cw: list[str], not_cw: list[str]
) -> None:
    rig = load_rig(_RIGS_DIR / profile_file)
    cw_family = rig.to_profile().mode_families["cw"]
    assert cw_family == tuple(cw), vendor
    for mode in not_cw:
        assert mode in rig.modes
        assert mode not in cw_family
