"""The band plans must ship in the wheel (MOR-3631 packaging).

``band-plans/`` lives at the repository root, outside the ``rigplane``
package, so the wheel only contains it when ``pyproject.toml`` force-includes
it -- exactly how ``rigs/`` is shipped (``rigs`` -> ``rigplane/rigs``).  Before
this fix the directory was missing from every installed wheel and from the
frozen sidecar, and an installed server silently served no band plan at all.

The test builds the wheel's file layout from the ``force-include`` table of
``pyproject.toml`` (so it goes red if the mapping is removed), then asks the
real loader to find and read the data from that installed layout.
"""

from __future__ import annotations

import shutil
import tomllib
from pathlib import Path

import pytest

from rigplane.web.band_plan import (
    _REGION_HAM_FILE,
    BandPlanRegistry,
    bundled_band_plans_dir,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"


def _wheel_force_include() -> dict[str, str]:
    data = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))
    wheel = data["tool"]["hatch"]["build"]["targets"]["wheel"]
    return dict(wheel.get("force-include", {}))


def _install_layout(site_packages: Path) -> Path:
    """Materialise the wheel's force-include mapping; return the module path."""
    for source, target in _wheel_force_include().items():
        src = REPO_ROOT / source
        assert src.is_dir(), f"force-include source {source!r} does not exist"
        shutil.copytree(src, site_packages / target)
    module = site_packages / "rigplane" / "web" / "band_plan.py"
    module.parent.mkdir(parents=True, exist_ok=True)
    module.write_text("# stand-in for the installed module\n", encoding="utf-8")
    return module


def test_wheel_force_includes_band_plans_next_to_the_rigs() -> None:
    mapping = _wheel_force_include()
    assert mapping.get("rigs") == "rigplane/rigs"
    assert mapping.get("band-plans") == "rigplane/band-plans"


def test_installed_layout_resolves_to_the_packaged_band_plans(tmp_path: Path) -> None:
    module = _install_layout(tmp_path)

    resolved = bundled_band_plans_dir(module)

    assert resolved == tmp_path / "rigplane" / "band-plans"
    assert (resolved / "_config.toml").is_file()
    for ham_file in set(_REGION_HAM_FILE.values()):
        assert (resolved / ham_file).is_file(), f"{ham_file} missing from the wheel"
    for layer_file in ("broadcast.toml", "utility.toml"):
        assert (resolved / layer_file).is_file(), f"{layer_file} missing from the wheel"


@pytest.mark.parametrize("region", sorted(_REGION_HAM_FILE))
def test_installed_layout_serves_every_region_including_vhf(
    tmp_path: Path, region: str
) -> None:
    module = _install_layout(tmp_path)
    plans = tmp_path / "rigplane" / "band-plans"
    # The persisted choice is the only thing that differs between installs.
    (plans / "_config.toml").write_text(
        f'[settings]\nregion = "{region}"\n', encoding="utf-8"
    )
    assert bundled_band_plans_dir(module) == plans

    registry = BandPlanRegistry(bundled_band_plans_dir(module))
    registry.load()

    assert registry.region == region
    ham = [s for s in registry.get_segments(0, 2_000_000_000) if s["layer"] == "ham"]
    assert any(s["start"] <= 7_100_000 <= s["end"] for s in ham), "no HF plan"
    assert any(s["start"] <= 145_500_000 <= s["end"] for s in ham), "no 2 m plan"
    assert any(s["start"] <= 1_296_200_000 <= s["end"] for s in ham), "no 23 cm plan"


def test_source_checkout_still_resolves_to_the_repository_directory() -> None:
    module = REPO_ROOT / "src" / "rigplane" / "web" / "band_plan.py"

    assert bundled_band_plans_dir(module) == REPO_ROOT / "band-plans"
