"""``rigplane_testkit.leaked_radio_guard``: fail the test that leaks a Radio (MOR-4333).

A connected ``CoreRadio`` that is garbage collected without ``disconnect()`` logs a
WARNING from its finalizer. The finalizer runs whenever the collector gets to it, so the
warning used to land in an unrelated neighbour test (one that asserts "no warnings") and
fail that one. The guard collects after every test that left a connected radio behind and
fails the test that left it.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = "rigplane_testkit.leaked_radio_guard"

_LEAK = """
from rigplane.runtime._connection_state import RadioConnectionState
from rigplane.runtime.radio import CoreRadio


def _connected_radio():
    radio = CoreRadio("127.0.0.1")
    radio._conn_state = RadioConnectionState.CONNECTED
    return radio


def _drop_in_a_cycle(radio):
    # Only the cyclic collector can reclaim this one, like a radio held by a pending task.
    radio.cycle = radio


def test_leaks_a_connected_radio():
    _drop_in_a_cycle(_connected_radio())


def test_innocent_neighbour_after_the_leak():
    assert True


def test_disconnects_before_dropping_it():
    radio = _connected_radio()
    _drop_in_a_cycle(radio)
    radio._conn_state = RadioConnectionState.DISCONNECTED


def test_never_connected_radio_is_not_a_leak():
    _drop_in_a_cycle(CoreRadio("127.0.0.1"))


_KEPT = []


def test_radio_still_referenced_at_teardown_is_not_collected_yet():
    _KEPT.append(_connected_radio())
"""


def _run(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    (tmp_path / "test_sample.py").write_text(textwrap.dedent(source))
    return subprocess.run(
        [
            sys.executable, "-m", "pytest", "-p", PLUGIN, "-p", "no:cacheprovider",
            "-p", "no:xdist", "-q", "-rA", "--rootdir", str(tmp_path), "-c", "/dev/null",
            str(tmp_path / "test_sample.py"),
        ],
        cwd=tmp_path,
        env={"PYTHONPATH": f"{ROOT}:{ROOT / 'src'}", "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )


def test_the_guard_is_loaded_for_the_suite(request) -> None:
    assert request.config.pluginmanager.hasplugin(PLUGIN)


def test_the_test_that_leaks_a_connected_radio_fails_not_its_neighbour(tmp_path) -> None:
    result = _run(tmp_path, _LEAK)
    out = result.stdout

    assert "ERROR test_sample.py::test_leaks_a_connected_radio" in out, out
    assert "test_leaks_a_connected_radio" in out.split("short test summary info")[-1]
    assert "PASSED test_sample.py::test_innocent_neighbour_after_the_leak" in out, out
    assert "PASSED test_sample.py::test_disconnects_before_dropping_it" in out, out
    assert "PASSED test_sample.py::test_never_connected_radio_is_not_a_leak" in out, out
    assert (
        "PASSED test_sample.py::test_radio_still_referenced_at_teardown_is_not_collected_yet"
        in out
    ), out
    assert "ERROR test_sample.py::test_innocent_neighbour_after_the_leak" not in out
    assert result.returncode != 0


def test_the_failure_names_the_leaked_radio_warning(tmp_path) -> None:
    out = _run(tmp_path, _LEAK).stdout

    assert "Radio collected with active connection" in out, out
