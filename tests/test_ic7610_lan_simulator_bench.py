"""Bench-nightly IC-7610 LAN simulator entrypoint."""

from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SIM_SCRIPT = REPO_ROOT / "scripts" / "bench" / "ic7610_lan_simulator.py"
WORKFLOW_INVOCATION = [
    "uv",
    "run",
    "python",
    str(SIM_SCRIPT.relative_to(REPO_ROOT)),
]


@pytest.mark.timeout(30)
def test_workflow_invocation_writes_control_port(tmp_path: Path) -> None:
    """Same argv as bench-nightly Start IC-7610 LAN simulator (without setsid)."""
    port_file = tmp_path / "sim-control-port"
    proc = subprocess.Popen(
        [*WORKFLOW_INVOCATION, "--port-file", str(port_file)],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 15.0
        while time.monotonic() < deadline:
            if port_file.is_file() and port_file.read_text(encoding="utf-8").strip():
                break
            if proc.poll() is not None:
                err = proc.stderr.read() if proc.stderr else ""
                pytest.fail(
                    f"simulator exited {proc.returncode} before writing port file: {err}"
                )
            time.sleep(0.1)
        else:
            pytest.fail("simulator never wrote the control port file")
        port = int(port_file.read_text(encoding="utf-8").strip())
        assert 1 <= port <= 65535
    finally:
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=5)


def test_module_imports_without_pytest_pythonpath() -> None:
    """Workflow uses `uv run python scripts/bench/...`, not pytest's pythonpath."""
    completed = subprocess.run(
        [
            "uv",
            "run",
            "python",
            "-c",
            "import importlib.util; "
            "spec=importlib.util.spec_from_file_location("
            "'ic7610_lan_simulator', 'scripts/bench/ic7610_lan_simulator.py'); "
            "mod=importlib.util.module_from_spec(spec); "
            "assert spec.loader; spec.loader.exec_module(mod)",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
