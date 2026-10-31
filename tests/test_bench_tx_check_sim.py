"""Owner-present TX bench tool checks (MOR-3839).

Covers scripts/bench/bench_tx_check.py: fast owner-gate/argument/profile
checks against a stub HTTP server (no simulator), plus the slow subprocess
runs in --sim mode against Core's simulator stack (run 1 proves the move
did not break existing behaviour, run 4 is the new in-flight force_off).
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
import tomllib

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL = REPO_ROOT / "scripts" / "bench" / "bench_tx_check.py"
RIGS_DIR = REPO_ROOT / "rigs"

# Only the --sim runs below stand up the simulator stack and key ten cycles;
# everything above stays in the default fast loop.


def read_minimum(profile="ic705"):
    with (RIGS_DIR / f"{profile}.toml").open("rb") as source:
        control = tomllib.load(source)["controls"]["rf_power"]
    assert control["raw_max"] == 255
    return control["raw_min"] / 255.0


def field_status(value, *, when):
    return {
        "observed": True,
        "freshness": "fresh",
        "availability": "available",
        "source": {"provider": "icom_civ", "source": "poll_response"},
        "lastObservedMonotonic": when,
    }


class StubHandler(BaseHTTPRequestHandler):
    """Minimal preflight server: answers reads, records every POST."""

    server_version = "StubBench/1"

    def _send(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        when = time.monotonic()
        if self.path == "/api/v1/runtime":
            self._send({"radio": {"model": "IC-705", "connected": True}})
        elif self.path == "/api/v1/state":
            minimum = self.server.minimum
            self._send(
                {
                    "ptt": False,
                    "powerLevel": minimum,
                    "fieldStatus": {
                        "ptt": field_status(False, when=when),
                        "powerLevel": field_status(minimum, when=when),
                    },
                }
            )
        elif self.path == "/api/v1/managed-transmit":
            self._send(
                {
                    "managedTransmit": {
                        "status": "available",
                        "releaseRequired": False,
                        "intent": {"kind": "rx"},
                    },
                    "txObservation": {"observedPtt": "off"},
                }
            )
        else:
            self._send({"error": "not found"}, status=404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.server.posts.append(self.path)
        self._send({"error": "must not write during a dry run"}, status=500)

    def log_message(self, *args):
        pass


@pytest.fixture()
def stub_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    server.minimum = read_minimum()
    server.posts = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join()


def run_tool(*argv, cwd, env_extra=None):
    env = dict(os.environ)
    env.pop("BENCH_OWNER_YES", None)
    env.update(env_extra or {})
    return subprocess.run(
        [sys.executable, str(TOOL), *argv],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_help_lists_run_4(tmp_path):
    proc = run_tool("--help", cwd=tmp_path)
    assert proc.returncode == 0
    assert "--run" in proc.stdout
    assert "4" in proc.stdout


def test_run_5_rejected_at_parse_time(tmp_path):
    proc = run_tool("--run", "5", "--label", "x", cwd=tmp_path)
    assert proc.returncode == 2
    assert "invalid choice" in proc.stderr


def test_run_4_accepted_at_parse_time(tmp_path):
    """--run 4 passes argparse; without a server it fails later, not with exit 2."""
    proc = run_tool(
        "--url",
        "http://127.0.0.1:1",
        "--label",
        "x",
        "--run",
        "4",
        "--profile",
        "ic705",
        cwd=tmp_path,
    )
    assert proc.returncode == 1
    assert "invalid choice" not in proc.stderr
    assert "DRY / READ ONLY" not in proc.stdout


def test_dry_without_owner_gates_writes_nothing(tmp_path, stub_server):
    url = f"http://127.0.0.1:{stub_server.server_port}"
    proc = run_tool(
        "--url", url, "--label", "dry", "--run", "1", "--profile", "ic705", cwd=tmp_path
    )
    assert proc.returncode == 0
    assert "DRY / READ ONLY" in proc.stdout
    assert stub_server.posts == []


def test_armed_flags_without_owner_env_stay_dry(tmp_path, stub_server):
    url = f"http://127.0.0.1:{stub_server.server_port}"
    proc = run_tool(
        "--url",
        url,
        "--label",
        "dry",
        "--run",
        "1",
        "--profile",
        "ic705",
        "--tx-armed",
        "--checklist-confirmed",
        "--no-clients-confirmed",
        cwd=tmp_path,
    )
    assert proc.returncode == 0
    assert "DRY / READ ONLY" in proc.stdout
    assert stub_server.posts == []


def test_rigs_dir_override_is_honoured(tmp_path, stub_server):
    url = f"http://127.0.0.1:{stub_server.server_port}"
    empty = tmp_path / "empty-rigs"
    empty.mkdir()
    missing = run_tool(
        "--url",
        url,
        "--label",
        "dry",
        "--run",
        "1",
        "--profile",
        "ic705",
        "--rigs-dir",
        str(empty),
        cwd=tmp_path,
    )
    assert missing.returncode == 1

    override = tmp_path / "rigs"
    override.mkdir()
    shutil.copy(RIGS_DIR / "ic705.toml", override / "ic705.toml")
    found = run_tool(
        "--url",
        url,
        "--label",
        "dry",
        "--run",
        "1",
        "--profile",
        "ic705",
        "--rigs-dir",
        str(override),
        cwd=tmp_path,
    )
    assert found.returncode == 0
    assert "DRY / READ ONLY" in found.stdout


def test_default_rigs_dir_works_from_any_directory(tmp_path, stub_server):
    """No --rigs-dir and a cwd far from the checkout still finds the profile."""
    url = f"http://127.0.0.1:{stub_server.server_port}"
    far = tmp_path / "some" / "nested" / "cwd"
    far.mkdir(parents=True)
    proc = run_tool(
        "--url", url, "--label", "dry", "--run", "1", "--profile", "ic705", cwd=far
    )
    assert proc.returncode == 0
    assert "DRY / READ ONLY" in proc.stdout


def load_tool_module():
    """Import the bench tool as a module (it is stdlib-only, no side effects)."""
    spec = importlib.util.spec_from_file_location("bench_tx_check", TOOL)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_windows_timer_is_noop_off_windows(monkeypatch):
    """Off Windows the tool must not touch the timer at all (real ctypes here)."""
    tool = load_tool_module()
    monkeypatch.setattr(sys, "platform", "linux")
    assert tool._raise_windows_timer_resolution() is None


def test_windows_timer_raises_resolution_on_windows(monkeypatch):
    """On win32: 1 ms multimedia timer plus above-normal scheduling."""
    tool = load_tool_module()
    events = []

    class FakeWinmm:
        def timeBeginPeriod(self, period):
            events.append(("timeBeginPeriod", period))

    class FakeKernel32:
        def GetCurrentProcess(self):
            return "process-handle"

        def SetPriorityClass(self, handle, priority):
            events.append(("SetPriorityClass", handle, priority))

    fake_windll = SimpleNamespace(winmm=FakeWinmm(), kernel32=FakeKernel32())
    monkeypatch.setattr(tool, "ctypes", SimpleNamespace(windll=fake_windll))
    monkeypatch.setattr(sys, "platform", "win32")
    assert tool._raise_windows_timer_resolution() is None
    assert events == [
        ("timeBeginPeriod", 1),
        ("SetPriorityClass", "process-handle", 0x8000),
    ]


def test_windows_timer_failure_prints_one_stderr_line(monkeypatch, capsys):
    """A timer failure is best effort: one stderr line, never fatal."""
    tool = load_tool_module()

    class FailingWinmm:
        def timeBeginPeriod(self, period):
            raise OSError("no multimedia timer here")

    fake_windll = SimpleNamespace(winmm=FailingWinmm())
    monkeypatch.setattr(tool, "ctypes", SimpleNamespace(windll=fake_windll))
    monkeypatch.setattr(sys, "platform", "win32")
    assert tool._raise_windows_timer_resolution() is None
    assert len(capsys.readouterr().err.strip().splitlines()) == 1


def journal_summary(cwd):
    bench_out = Path(cwd) / "bench-out"
    journals = sorted(bench_out.glob("*.jsonl"))
    assert len(journals) == 1
    summary = None
    for line in journals[0].read_text().splitlines():
        record = json.loads(line)
        if record.get("type") == "summary":
            summary = record
    assert summary is not None
    return summary


@pytest.mark.slow
def test_sim_run_1_unchanged(tmp_path):
    proc = run_tool(
        "--sim",
        "--label",
        "simrun1",
        "--run",
        "1",
        "--profile",
        "ic705",
        "--cycle-gap",
        "1.0",
        cwd=tmp_path,
        env_extra={"PATH": os.environ["PATH"]},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = journal_summary(tmp_path)
    assert summary["completed_cycles"] == 10
    assert summary["timing_invalid_cycles"] == []
    assert summary["error"] is None


@pytest.mark.slow
def test_sim_run_4_inflight_force_off(tmp_path):
    proc = run_tool(
        "--sim",
        "--label",
        "simrun4",
        "--run",
        "4",
        "--profile",
        "ic705",
        "--cycle-gap",
        "1.0",
        cwd=tmp_path,
        env_extra={"PATH": os.environ["PATH"]},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    summary = journal_summary(tmp_path)
    assert summary["completed_cycles"] == 10
    assert summary["timing_invalid_cycles"] == []
    assert summary["error"] is None
