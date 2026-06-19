"""Unit tests for scripts/bump_downstream_pin.py.

Tests verify the precise file-edit logic against sample content
that mirrors the real rigplane-pro and rigplane-station pin files.
They also confirm the resulting pro state satisfies the same assertion
as rigplane-pro/tests/test_core_version_contract.py.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# Make the scripts/ directory importable.
SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from bump_downstream_pin import bump_pro, bump_station  # noqa: E402


# ── sample file content ───────────────────────────────────────────────────────

PRO_PYPROJECT_TEMPLATE = """\
[project]
name = "rigplane-pro"
version = "0.9.0-beta.8"
dependencies = [
    "aiohttp>=3.9",
    "rigplane[bridge]=={old}",
    "keyring>=25.0",
]
"""

STATION_PYPROJECT_TEMPLATE = """\
[project]
name = "rigplane-station"
dependencies = [
    "rigplane=={old}",
    "aiohttp>=3.13",
]
"""


# ── pro tests ─────────────────────────────────────────────────────────────────


def test_bump_pro_core_version(tmp_path: Path) -> None:
    (tmp_path / "CORE_VERSION").write_text("v2.10.1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        PRO_PYPROJECT_TEMPLATE.format(old="2.10.1"), encoding="utf-8"
    )

    bump_pro(tmp_path, "2.11.0")

    assert (tmp_path / "CORE_VERSION").read_text(encoding="utf-8").strip() == "v2.11.0"


def test_bump_pro_pyproject_pin(tmp_path: Path) -> None:
    (tmp_path / "CORE_VERSION").write_text("v2.10.1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        PRO_PYPROJECT_TEMPLATE.format(old="2.10.1"), encoding="utf-8"
    )

    bump_pro(tmp_path, "2.11.0")

    pyproject = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert '"rigplane[bridge]==2.11.0"' in pyproject
    assert '"rigplane[bridge]==2.10.1"' not in pyproject


def test_bump_pro_satisfies_version_contract(tmp_path: Path) -> None:
    """Verify the edited state satisfies test_core_version_contract.py logic."""
    import tomllib

    (tmp_path / "CORE_VERSION").write_text("v2.10.1\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        PRO_PYPROJECT_TEMPLATE.format(old="2.10.1"), encoding="utf-8"
    )

    bump_pro(tmp_path, "2.11.0")

    # Replicate the contract test assertion exactly.
    core_tag = (tmp_path / "CORE_VERSION").read_text(encoding="utf-8").strip()
    assert core_tag.startswith("v"), (
        f"CORE_VERSION must start with 'v', got {core_tag!r}"
    )
    core_version = core_tag.removeprefix("v")

    pyproject = tomllib.loads((tmp_path / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = pyproject["project"]["dependencies"]
    assert f"rigplane[bridge]=={core_version}" in dependencies, (
        f"rigplane[bridge]=={core_version} not found in dependencies: {dependencies}"
    )


def test_bump_pro_missing_core_version_file_raises(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        PRO_PYPROJECT_TEMPLATE.format(old="2.10.1"), encoding="utf-8"
    )
    with pytest.raises(FileNotFoundError, match="CORE_VERSION"):
        bump_pro(tmp_path, "2.11.0")


def test_bump_pro_missing_pin_line_raises(tmp_path: Path) -> None:
    (tmp_path / "CORE_VERSION").write_text("v2.10.1\n", encoding="utf-8")
    # pyproject.toml with a different pin version (simulates desync)
    (tmp_path / "pyproject.toml").write_text(
        PRO_PYPROJECT_TEMPLATE.format(old="2.9.0"), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="not found"):
        bump_pro(tmp_path, "2.11.0")


# ── station tests ─────────────────────────────────────────────────────────────


def test_bump_station_pyproject_pin(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        STATION_PYPROJECT_TEMPLATE.format(old="2.10.0"), encoding="utf-8"
    )

    bump_station(tmp_path, "2.11.0")

    pyproject = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert '"rigplane==2.11.0"' in pyproject
    assert '"rigplane==2.10.0"' not in pyproject


def test_bump_station_noop_if_already_current(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "pyproject.toml").write_text(
        STATION_PYPROJECT_TEMPLATE.format(old="2.11.0"), encoding="utf-8"
    )
    original = (tmp_path / "pyproject.toml").read_text(encoding="utf-8")

    bump_station(tmp_path, "2.11.0")

    assert (tmp_path / "pyproject.toml").read_text(encoding="utf-8") == original
    captured = capsys.readouterr()
    assert "no-op" in captured.out


def test_bump_station_missing_pin_raises(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'rigplane-station'\n", encoding="utf-8"
    )
    with pytest.raises(ValueError, match="not found"):
        bump_station(tmp_path, "2.11.0")
