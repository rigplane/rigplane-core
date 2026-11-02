"""``rigplane.testing`` ships in the wheel and stays off the runtime path (MOR-4075).

rigplane-pro (and any downstream suite) imports ``rigplane.testing.timing`` through its
ordinary ``rigplane`` dependency. The repository's ``rigplane_testkit`` does not ship, so
the shared clock lives in the package. Two properties are pinned:

* nothing on the runtime path imports it (``import rigplane`` does not load it);
* the built wheel, installed into a clean virtual environment with no repository root on
  ``sys.path``, provides it and a scenario runs on it.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_importing_the_package_does_not_load_the_testing_helpers() -> None:
    done = subprocess.run(
        [
            sys.executable,
            "-I",
            "-c",
            "import sys, rigplane; print('rigplane.testing' in sys.modules)",
        ],
        cwd=ROOT / "src",
        capture_output=True,
        text=True,
        check=True,
    )
    assert done.stdout.strip() == "False"


def test_the_testing_helper_needs_nothing_beyond_the_standard_library() -> None:
    source = (ROOT / "src" / "rigplane" / "testing" / "timing.py").read_text(
        encoding="utf-8"
    )
    runtime_imports = [
        line
        for line in source.splitlines()
        if line.startswith(("import ", "from ")) and "TYPE_CHECKING" not in line
    ]
    third_party = [
        line
        for line in runtime_imports
        if line.split()[1].split(".")[0] in {"pytest", "rigplane_testkit"}
    ]
    assert third_party == []


_SCENARIO = textwrap.dedent(
    """
    import asyncio
    import importlib.util

    from rigplane.testing.timing import VirtualClock, virtual_clock, wait_until

    assert importlib.util.find_spec("rigplane_testkit") is None, "the repo root leaked in"

    @virtual_clock
    async def scenario(clock: VirtualClock) -> None:
        task = asyncio.ensure_future(asyncio.sleep(3600.0))
        await clock.elapse(3599.0)
        assert not task.done()
        await clock.elapse(2.0)
        assert task.done()

    scenario()
    assert wait_until(lambda: 42) == 42
    print("scenario ok")
    """
)


@pytest.mark.slow
def test_the_built_wheel_provides_the_helper_in_a_clean_environment(
    tmp_path: Path,
) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is needed to build and install the wheel")
    tree = tmp_path / "tree"
    shutil.copytree(
        ROOT,
        tree,
        ignore=shutil.ignore_patterns(
            ".git",
            ".venv",
            "node_modules",
            "frontend",
            "tests",
            "docs",
            "__pycache__",
            "*.pyc",
            "static",
            ".claude",
        ),
    )
    # The build hook skips the npm build when a prebuilt UI exists; a stub is enough here.
    (tree / "frontend" / "dist").mkdir(parents=True)
    (tree / "frontend" / "dist" / "index.html").write_text(
        "<html></html>", encoding="utf-8"
    )
    dist = tmp_path / "dist"
    subprocess.run(
        [uv, "build", "--wheel", "--out-dir", str(dist), str(tree)],
        check=True,
        capture_output=True,
        text=True,
        timeout=600,
    )
    wheels = sorted(dist.glob("rigplane-*.whl"))
    assert len(wheels) == 1, wheels
    with zipfile.ZipFile(wheels[0]) as built:
        names = set(built.namelist())
    assert "rigplane/testing/timing.py" in names
    assert not any(name.startswith("rigplane_testkit/") for name in names)
    venv = tmp_path / "venv"
    subprocess.run(
        [uv, "venv", str(venv)], check=True, capture_output=True, timeout=120
    )
    python = venv / ("Scripts" if sys.platform == "win32" else "bin") / "python"
    subprocess.run(
        [uv, "pip", "install", "--python", str(python), str(wheels[0])],
        check=True,
        capture_output=True,
        text=True,
        timeout=900,
    )
    done = subprocess.run(
        [str(python), "-I", "-c", _SCENARIO],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert done.returncode == 0, done.stderr + done.stdout
    assert "scenario ok" in done.stdout
