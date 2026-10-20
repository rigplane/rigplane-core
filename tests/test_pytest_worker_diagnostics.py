"""CI worker diagnostics survive real subprocess deaths and failed writes."""

from __future__ import annotations

import importlib.util
import asyncio
import json
import os
from pathlib import Path
import signal
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / ".github/scripts/pytest_worker_diagnostics.py"


def test_full_worker_diagnostics_wiring() -> None:
    text = (ROOT / ".github/workflows/full.yml").read_text()
    assert "-p pytest_worker_diagnostics" in text
    assert "-n auto" in text and "--timeout=300 --timeout-method=thread" in text
    assert (
        "RIGPLANE_WORKER_DIAG_DIR: ${{ runner.temp }}/pytest-worker-diagnostics" in text
    )
    upload = text.split("- name: Upload worker diagnostics", 1)[1].split("- name:", 1)[
        0
    ]
    assert "if: always()" in upload and "retention-days: 3" in upload
    assert "${{ matrix.python-version }}" in upload
    assert (
        "uv run --no-sync python .github/scripts/pytest_worker_diagnostics.py --finalize"
        in text
    )
    assert "rc=${PIPESTATUS[0]}" in text and 'exit "$rc"' in text


@pytest.mark.skipif(
    sys.platform != "linux", reason="Linux signal/resource qualification"
)
@pytest.mark.parametrize("write_failure", [False, True])
def test_real_worker_deaths_and_best_effort_writes(
    tmp_path: Path, write_failure: bool
) -> None:
    diagnostic = tmp_path / "diagnostics"
    diagnostic.mkdir()
    sabotage = ""
    if write_failure:
        sabotage = """
    p = Path(os.environ['RIGPLANE_WORKER_DIAG_DIR'])
    p.rename(p.with_name('before-failure'))
    p.write_text('intentional diagnostic write failure')
"""
    fixture = tmp_path / "test_synthetic.py"
    fixture.write_text(f"""import os, signal, resource, sys, threading
from pathlib import Path
import pytest

@pytest.mark.parametrize('value', ['PRIVATE_PARAMETER_SENTINEL'])
def test_01_assertion(value):
    assert not any(n == 'rigplane' or n.startswith('rigplane.') for n in sys.modules)
    assert False, 'INTENTIONAL_SYNTHETIC_ASSERTION'

def test_02_timeout():
{sabotage or "    pass"}
    threading.Event().wait()

def test_03_fatal():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    os.kill(os.getpid(), signal.SIGABRT)
""")
    env = dict(os.environ)
    env.update(
        PYTHONPATH=str(HELPER.parent),
        RIGPLANE_WORKER_DIAG_DIR=str(diagnostic),
        RIGPLANE_DIAGNOSTICS_SYNTHETIC="1",
        PRIVATE_ENV_SENTINEL="PRIVATE_ENV_SENTINEL_VALUE",
    )
    env.pop("PYTEST_ADDOPTS", None)
    command = [
        sys.executable,
        "-m",
        "pytest",
        str(fixture),
        "--noconftest",
        "-n",
        "1",
        "--dist=load",
        "--max-worker-restart=2",
        "-p",
        "pytest_worker_diagnostics",
        "--timeout=1",
        "--timeout-method=thread",
        "--tb=short",
        "-q",
        "--rootdir=" + str(tmp_path),
    ]
    log = tmp_path / "pytest.log"
    with log.open("wb") as stream:
        child = subprocess.Popen(
            command,
            cwd=tmp_path,
            env=env,
            stdout=stream,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            code = child.wait(timeout=30)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid, signal.SIGKILL)
            child.wait()
            pytest.fail("synthetic diagnostics qualification exceeded 30s")
    output = log.read_text()
    assert code == 1 and "3 failed" in output and "INTERNALERROR" not in output
    assert "INTENTIONAL_SYNTHETIC_ASSERTION" in output
    with pytest.raises(ProcessLookupError):
        os.killpg(child.pid, 0)
    if write_failure:
        assert diagnostic.is_file()
        return
    exits = [
        json.loads(line)
        for line in (diagnostic / "controller-reaped-exit.jsonl")
        .read_text()
        .splitlines()
    ]
    assert sorted(row["returncode"] for row in exits) == [-6, 0, 1]
    assert next(row for row in exits if row["returncode"] == -6)["signal"] == 6
    timeout = next(diagnostic.glob("timeout-*.log")).read_text()
    assert "PYTEST_TIMEOUT_CALLBACK" in timeout and "test_02_timeout" in timeout
    fatal = [p.read_text() for p in diagnostic.glob("fatal-*.log")]
    assert any(
        "FATAL_SESSIONSTART_ENABLED" in s and "Fatal Python error: Aborted" in s
        for s in fatal
    )
    for path in diagnostic.iterdir():
        if path.is_file():
            text = path.read_text()
            assert "PRIVATE_PARAMETER_SENTINEL" not in text
            assert "PRIVATE_ENV_SENTINEL_VALUE" not in text


def test_diagnostic_artifact_bounds(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("worker_diag_control", HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    log = tmp_path / "fatal-gw0-123.log"
    log.write_bytes(b"x" * (module.MAX_FILE_BYTES + 1))
    module.finalize_directory(tmp_path)
    assert log.stat().st_size <= module.MAX_FILE_BYTES
    assert b"TRUNCATED" in log.read_bytes()
    assert (tmp_path / "bounds.json").is_file()


async def test_snapshot_omits_values_and_task_names(tmp_path: Path) -> None:
    spec = importlib.util.spec_from_file_location("worker_diag_snapshot", HELPER)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    observer = module._Diagnostics(tmp_path, "gw0")
    case = observer.start("test_synthetic.py::test_example[PRIVATE_PARAMETER_SENTINEL]")
    task = asyncio.create_task(asyncio.sleep(0), name="PRIVATE_TASK_SENTINEL")
    try:
        observer._snapshot(case, 120)
        snapshot = next(tmp_path.glob("dump-*.txt")).read_text()
        assert "AWAIT depth=" in snapshot
        assert "PRIVATE_PARAMETER_SENTINEL" not in snapshot
        assert "PRIVATE_TASK_SENTINEL" not in snapshot
        assert len(snapshot.encode()) <= module._MAX_DUMP_BYTES
    finally:
        observer.finish(case)
        await task
