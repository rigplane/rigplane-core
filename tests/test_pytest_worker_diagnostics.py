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
import time
from unittest.mock import create_autospec

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


def test_quick_worker_diagnostics_wiring() -> None:
    text = (ROOT / ".github/workflows/quick.yml").read_text()
    assert "- name: Run tests" in text
    assert "- name: Bound worker diagnostics" in text
    assert "- name: Upload worker diagnostics" in text
    run = text.split("- name: Run tests", 1)[1].split("- name:", 1)[0]
    assert "-p pytest_worker_diagnostics" in run
    assert (
        "RIGPLANE_WORKER_DIAG_DIR: ${{ runner.temp }}/pytest-worker-diagnostics" in run
    )
    assert "PYTHONPATH: ${{ github.workspace }}/.github/scripts" in run
    finalize = text.split("- name: Bound worker diagnostics", 1)[1].split("- name:", 1)[
        0
    ]
    assert (
        "pytest_worker_diagnostics.py --finalize" in finalize
        and "${{ runner.temp }}/pytest-worker-diagnostics" in finalize
    )
    upload = text.split("- name: Upload worker diagnostics", 1)[1].split("- name:", 1)[
        0
    ]
    assert "actions/upload-artifact" in upload and "always()" in upload
    for leaf in (
        "events-*.jsonl",
        "phases-*.jsonl",
        "controller-*.jsonl",
        "dump-*.txt",
        "fatal-*.log",
        "timeout-*.log",
        "bounds.json",
    ):
        assert f"${{{{ runner.temp }}}}/pytest-worker-diagnostics/{leaf}" in upload


# The synthetic child took ~1.4 s in a green quick run on main (2026-10-22,
# run 39831600325) but missed a 30 s deadline in repeated quick runs of one
# loaded PR; the deadline only has to catch a real hang.
_QUALIFICATION_DEADLINE_S = 120


def _hang_diagnostics(pgid: int) -> str:
    def command_output(command: list[str]) -> str:
        try:
            return subprocess.run(
                command,
                capture_output=True,
                text=True,
                errors="replace",
                check=True,
                timeout=2,
            ).stdout
        except (OSError, subprocess.SubprocessError) as error:
            return f"unavailable: {error}"

    def read_source(path: Path) -> str:
        try:
            return path.read_text(errors="replace")
        except OSError as error:
            return f"unreadable: {error}"

    fields = (
        "pid,ppid,stat,wchan,etime,command"
        if sys.platform == "darwin"
        else "pid,ppid,stat,wchan:20,etime,cmd"
    )
    processes = command_output(["ps", "-g", str(pgid), "-o", fields])
    meminfo = "\n".join(read_source(Path("/proc/meminfo")).splitlines()[:10])
    cgroup = "\n".join(
        f"{leaf}:\n{read_source(Path('/sys/fs/cgroup') / leaf)[:500]}"
        for leaf in ("pids.current", "pids.max", "memory.events")
    )
    dmesg = "\n".join(command_output(["dmesg"]).splitlines()[-30:])[-1900:]
    return "\n".join(
        f"{header}:\n{content[:1900]}"
        for header, content in (
            ("Process group", processes),
            ("Meminfo", meminfo),
            ("Cgroup", cgroup),
            ("Dmesg", dmesg),
        )
    )


@pytest.mark.skipif(sys.platform != "linux", reason="Linux worker qualification")
@pytest.mark.parametrize("exit_during_diagnostics", [False, True])
def test_qualification_timeout_reports_diagnostics_and_reaps_group(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, exit_during_diagnostics: bool
) -> None:
    real_popen = subprocess.Popen
    children = []

    def spawn(*args, **kwargs):
        child = real_popen(*args, **kwargs)
        if args[0][:3] == [sys.executable, "-m", "pytest"]:
            children.append(child)
        return child

    real_diagnostics = _hang_diagnostics

    def diagnose(pgid: int) -> str:
        os.killpg(pgid, 0)
        diagnostics = real_diagnostics(pgid)
        if exit_during_diagnostics:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            children[0].wait(timeout=5)
        return diagnostics

    monkeypatch.setattr(
        subprocess, "Popen", create_autospec(real_popen, side_effect=spawn)
    )
    monkeypatch.setattr(sys.modules[__name__], "_QUALIFICATION_DEADLINE_S", 0.2)
    diagnostic_spy = create_autospec(real_diagnostics, side_effect=diagnose)
    monkeypatch.setattr(sys.modules[__name__], "_hang_diagnostics", diagnostic_spy)
    try:
        with pytest.raises(pytest.fail.Exception) as failure:
            test_real_worker_deaths_and_best_effort_writes(
                tmp_path, write_failure=False
            )
        message = str(failure.value)
        assert "child output tail:" in message
        for header in ("Process group:", "Meminfo:", "Cgroup:", "Dmesg:"):
            assert header in message
            assert message.index(header) > message.index("child output tail:")
        assert len(children) == 1
        diagnostic_spy.assert_called_once_with(children[0].pid)
        _assert_group_gone(children[0].pid)
    finally:
        for child in children:
            try:
                os.killpg(child.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            child.wait(timeout=5)


def _assert_group_gone(pgid: int, timeout_s: float = 5.0) -> None:
    """Fail unless the process group disappears within ``timeout_s``.

    After SIGKILL the controller is reaped by the test, but its xdist worker
    may still be exiting or waiting to be reaped by init for a moment.
    """
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            return
        if time.monotonic() >= deadline:
            pytest.fail(f"process group {pgid} still present after {timeout_s}s")
        time.sleep(0.05)


@pytest.mark.skipif(sys.platform == "win32", reason="needs POSIX ps and process groups")
def test_hang_diagnostics_includes_live_process_group() -> None:
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True
    )
    try:
        diagnostics = _hang_diagnostics(child.pid)
        processes = diagnostics.split("Process group:\n", 1)[1].split("\nMeminfo:", 1)[
            0
        ]
        assert any(
            line.split()[0] == str(child.pid)
            for line in processes.splitlines()
            if line.split()
        )
        assert len(diagnostics) <= 8000
        for header in ("Meminfo:", "Cgroup:", "Dmesg:"):
            assert header in diagnostics
    finally:
        child.kill()
        child.wait(timeout=5)


@pytest.mark.parametrize(
    "error",
    [
        FileNotFoundError("missing command"),
        subprocess.CalledProcessError(1, ["diagnostic"]),
        subprocess.TimeoutExpired(["diagnostic"], timeout=2),
    ],
)
def test_hang_diagnostics_unavailable_sources(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    run = create_autospec(subprocess.run, side_effect=error)
    read = create_autospec(Path.read_text, side_effect=OSError("unreadable source"))
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(Path, "read_text", read)
    diagnostics = _hang_diagnostics(12345)
    for header in ("Process group:", "Meminfo:", "Cgroup:", "Dmesg:"):
        assert header in diagnostics
    assert diagnostics.count("unavailable") == 2
    assert diagnostics.count("unreadable") >= 4
    assert {call.args[0] for call in read.call_args_list} == {
        Path("/proc/meminfo"),
        Path("/sys/fs/cgroup/pids.current"),
        Path("/sys/fs/cgroup/pids.max"),
        Path("/sys/fs/cgroup/memory.events"),
    }
    assert len(run.call_args_list) == 2
    assert all(0 < call.kwargs["timeout"] <= 3 for call in run.call_args_list)
    assert all(call.kwargs["errors"] == "replace" for call in run.call_args_list)
    assert all(call.kwargs["check"] for call in run.call_args_list)


@pytest.mark.parametrize("long_dmesg", [False, True])
def test_hang_diagnostics_bounds_each_section(
    monkeypatch: pytest.MonkeyPatch, long_dmesg: bool
) -> None:
    lines = "\n".join(f"line-{index}" for index in range(100))
    kernel_log = (
        "\n".join("x" * 2000 for _ in range(100)) + "\nLATEST_KERNEL_EVENT"
        if long_dmesg
        else lines
    )
    results = [
        subprocess.CompletedProcess(["ps"], 0, stdout="x" * 20000),
        subprocess.CompletedProcess(["dmesg"], 0, stdout=kernel_log),
    ]
    run = create_autospec(subprocess.run, side_effect=results)
    read = create_autospec(Path.read_text, return_value=lines)
    monkeypatch.setattr(subprocess, "run", run)
    monkeypatch.setattr(Path, "read_text", read)
    diagnostics = _hang_diagnostics(12345)
    assert len(diagnostics) <= 8000
    for header in ("Process group:", "Meminfo:", "Cgroup:", "Dmesg:"):
        assert header in diagnostics
    meminfo = diagnostics.split("Meminfo:\n", 1)[1].split("\nCgroup:", 1)[0]
    assert "line-9" in meminfo and "line-10" not in meminfo
    dmesg = diagnostics.split("Dmesg:\n", 1)[1]
    if long_dmesg:
        assert dmesg.endswith("LATEST_KERNEL_EVENT")
    else:
        assert "line-70" in dmesg and "line-69" not in dmesg and "line-99" in dmesg
    for leaf in ("pids.current", "pids.max", "memory.events"):
        assert leaf in diagnostics


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
            code = child.wait(timeout=_QUALIFICATION_DEADLINE_S)
        except subprocess.TimeoutExpired:
            try:
                diagnostics = _hang_diagnostics(child.pid)
            finally:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                child.wait()
            tail = log.read_text(errors="replace")[-4000:]
            pytest.fail(
                f"synthetic diagnostics qualification exceeded "
                f"{_QUALIFICATION_DEADLINE_S}s; child output tail:\n{tail}"
                f"\n{diagnostics}"
            )
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
