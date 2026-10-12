"""Packaging guard: sounddevice held below 0.5 on Windows x64.

The PortAudio build bundled with sounddevice 0.5.x crashed the Core process
during initialisation on the Windows runner, so Windows x64 installs stay on
the 0.4.x line. Windows ARM64 is excluded from the hold — no 0.4.x release
ships a ``win_arm64`` wheel, so ARM64 resolves the unbounded requirement —
and other platforms track sounddevice unpinned. This module pins the declared
markers in ``pyproject.toml`` and the resolved 0.4.x entry in ``uv.lock``;
both fail if the pin is widened or dropped. Note: the generated lock resolves
the Windows fork on ``sys_platform`` alone, so ``uv.lock`` still assigns
Windows ARM64 to 0.4.7; only installers that evaluate the markers per
platform resolve ARM64 to the unbounded requirement.
"""

from __future__ import annotations

import pathlib
import re
import tomllib

try:
    from packaging.markers import Marker
    from packaging.requirements import Requirement
except ImportError:  # pragma: no cover - test env provides packaging
    Marker = None  # type: ignore[assignment]
    Requirement = None  # type: ignore[assignment]

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
WIN_X64_MARKER = 'sys_platform == "win32" and platform_machine != "ARM64"'
OTHER_MARKER = 'sys_platform != "win32" or platform_machine == "ARM64"'

# (environment, requirement index that must apply): index 0 is the <0.5
# hold, index 1 is the unbounded requirement. Each environment must match
# exactly one of the two markers.
MARKER_POINTS = [
    ({"sys_platform": "win32", "platform_machine": "AMD64"}, 0),
    ({"sys_platform": "win32", "platform_machine": "ARM64"}, 1),
    ({"sys_platform": "linux", "platform_machine": "x86_64"}, 1),
    ({"sys_platform": "darwin", "platform_machine": "arm64"}, 1),
]


def _dependencies() -> list[str]:
    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    return list(pyproject["project"]["dependencies"])


def _sounddevice_requirements() -> list[str]:
    reqs = []
    for dep in _dependencies():
        if Requirement is not None:
            name = Requirement(dep).name
        else:
            name = re.split(r"[<>=!;\s\[]", dep, maxsplit=1)[0].strip()
        if name == "sounddevice":
            reqs.append(dep)
    return reqs


def _marker_of(dep: str) -> str:
    if Requirement is not None:
        marker = Requirement(dep).marker
        return str(marker) if marker is not None else ""
    parts = dep.split(";", maxsplit=1)
    marker = parts[1].strip() if len(parts) == 2 else ""
    return marker.replace("'", '"')


def _admits(version: str, specifier: str) -> bool:
    if Requirement is not None:
        return version in Requirement(f"x{specifier}").specifier
    upper = re.search(r"<\s*([0-9][0-9.]*)", specifier)
    if upper is None:
        return True
    return tuple(int(p) for p in version.split(".")) < tuple(
        int(p) for p in upper.group(1).split(".")
    )


def _ordered_reqs() -> tuple[str, str]:
    reqs = _sounddevice_requirements()
    assert len(reqs) == 2, f"expected 2 sounddevice requirements, got {reqs}"
    markers = sorted(_marker_of(dep) for dep in reqs)
    assert markers == sorted([OTHER_MARKER, WIN_X64_MARKER]), (
        f"expected complementary win32/ARM64 markers, got {markers}"
    )
    by_marker = {_marker_of(dep): dep for dep in reqs}
    return by_marker[WIN_X64_MARKER], by_marker[OTHER_MARKER]


def test_sounddevice_has_windows_x64_and_other_requirements() -> None:
    held, _other = _ordered_reqs()
    assert "sounddevice" in held


def test_sounddevice_markers_split_platforms() -> None:
    assert Marker is not None, "packaging is required for marker evaluation"
    held, other = _ordered_reqs()
    reqs = [held, other]
    for env, expected in MARKER_POINTS:
        applies = [
            i for i, dep in enumerate(reqs) if Marker(_marker_of(dep)).evaluate(env)
        ]
        assert applies == [expected], (
            f"env {env}: expected only requirement {expected} to apply, "
            f"got {applies} ({reqs})"
        )


def test_windows_x64_sounddevice_excludes_0_5() -> None:
    held, _other = _ordered_reqs()
    specifier = held.split(";", maxsplit=1)[0][len("sounddevice") :]
    assert _admits("0.4.7", specifier), f"Windows pin must admit 0.4.7: {held}"
    assert not _admits("0.5.0", specifier), f"Windows pin must exclude 0.5.0: {held}"


def test_other_sounddevice_has_no_upper_bound() -> None:
    _held, other = _ordered_reqs()
    specifier = other.split(";", maxsplit=1)[0][len("sounddevice") :]
    assert "<" not in specifier, (
        f"non-Windows/ARM64 sounddevice must stay unpinned above: {other}"
    )


def test_lock_contains_sounddevice_0_4() -> None:
    lock = (REPO_ROOT / "uv.lock").read_text(encoding="utf-8")
    stanzas = re.findall(
        r'\[\[package\]\]\nname = "sounddevice"\nversion = "([^"]+)"', lock
    )
    assert stanzas, "uv.lock has no sounddevice package entry"
    assert any(v.startswith("0.4.") for v in stanzas), (
        f"uv.lock must resolve sounddevice 0.4.x for Windows x64, got {stanzas}"
    )
