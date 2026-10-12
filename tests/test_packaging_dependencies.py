"""Packaging guard: sounddevice held below 0.5 on Windows.

The PortAudio build bundled with sounddevice 0.5.x crashed the Core process
during initialisation on the Windows runner, so Windows installs stay on the
0.4.x line while other platforms track sounddevice unpinned. This module pins
the declared markers in ``pyproject.toml`` and the resolved 0.4.x entry in
``uv.lock``; both fail if the pin is widened or dropped.
"""

from __future__ import annotations

import pathlib
import re
import tomllib

try:
    from packaging.requirements import Requirement
except ImportError:  # pragma: no cover - test env provides packaging
    Requirement = None  # type: ignore[assignment]

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
WIN_MARKER = 'sys_platform == "win32"'
NON_WIN_MARKER = 'sys_platform != "win32"'


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


def test_sounddevice_has_windows_and_non_windows_requirements() -> None:
    reqs = _sounddevice_requirements()
    assert len(reqs) == 2, f"expected 2 sounddevice requirements, got {reqs}"
    markers = sorted(_marker_of(dep) for dep in reqs)
    assert markers == [NON_WIN_MARKER, WIN_MARKER], (
        f"expected complementary win32 markers, got {markers}"
    )


def test_windows_sounddevice_excludes_0_5() -> None:
    reqs = _sounddevice_requirements()
    windows = [dep for dep in reqs if _marker_of(dep) == WIN_MARKER]
    assert len(windows) == 1, f"expected one Windows sounddevice pin, got {reqs}"
    dep = windows[0]
    specifier = dep.split(";", maxsplit=1)[0][len("sounddevice") :]
    assert _admits("0.4.6", specifier), f"Windows pin must admit 0.4.6: {dep}"
    assert not _admits("0.5.0", specifier), (
        f"Windows pin must exclude 0.5.0: {dep}"
    )


def test_non_windows_sounddevice_has_no_upper_bound() -> None:
    reqs = _sounddevice_requirements()
    others = [dep for dep in reqs if _marker_of(dep) == NON_WIN_MARKER]
    assert len(others) == 1, f"expected one non-Windows requirement, got {reqs}"
    specifier = others[0].split(";", maxsplit=1)[0][len("sounddevice") :]
    assert "<" not in specifier, (
        f"non-Windows sounddevice must stay unpinned above: {others[0]}"
    )


def test_lock_contains_sounddevice_0_4() -> None:
    lock = (REPO_ROOT / "uv.lock").read_text(encoding="utf-8")
    stanzas = re.findall(
        r'\[\[package\]\]\nname = "sounddevice"\nversion = "([^"]+)"', lock
    )
    assert stanzas, "uv.lock has no sounddevice package entry"
    assert any(v.startswith("0.4.") for v in stanzas), (
        f"uv.lock must resolve sounddevice 0.4.x for Windows, got {stanzas}"
    )
