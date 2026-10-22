"""One installed-wheel software proof for the frozen Core b13 input."""

from __future__ import annotations

import json
import hashlib
import sys
from importlib import metadata
from pathlib import Path

import pytest
import rigplane
from rigplane.rigctld.contract import ClientSession, RigctldCommand, RigctldResponse
from rigplane.rigctld.protocol import format_response


task_root = Path(sys.argv[1]).resolve()
source_root = task_root / "source"
source_src = (source_root / "src").resolve()
installed_package = Path(metadata.distribution("rigplane").locate_file("rigplane")).resolve()


def assert_installed_import() -> None:
    assert metadata.version("rigplane") == "3.0.0b13"
    assert Path(rigplane.__file__).resolve().parent == installed_package
    assert str(installed_package).startswith(str(task_root / "installed") + "/")
    assert source_src not in {Path(item or ".").resolve() for item in sys.path}


assert_installed_import()
installed_nodes = [
    str(task_root / 'installed-tests/test_ic7300_swr_cadence.py') + '::test_swr_client_reads_do_not_starve_other_ic7300_tx_meters',
    str(task_root / 'installed-tests/test_combined_acquisition_drain.py') + '::test_completed_request_in_other_seats_snapshot_is_not_reclaimed',
]
copied_tests = {}
for name in ('test_ic7300_swr_cadence.py', 'test_combined_acquisition_drain.py'):
    data = (task_root / 'installed-tests' / name).read_bytes()
    assert data == (source_root / 'tests' / name).read_bytes()
    copied_tests[name] = hashlib.sha256(data).hexdigest()
# Only the two accepted fake-wire/in-memory meter regressions, outside source.
# No conftest, source helpers or source/src are needed for these nodes.
installed_meter_result = pytest.main([
    '-c', '/dev/null', '--noconftest', '--rootdir', str(task_root / 'pytest-root'),
    '--import-mode=importlib', '-o', 'pythonpath=', '-o', 'asyncio_mode=auto',
    '-o', 'asyncio_default_fixture_loop_scope=function', '--timeout=30',
    '--timeout-method=thread', '-q', *installed_nodes,
])
assert int(installed_meter_result) == 0, f'installed-wheel meter regressions failed: {installed_meter_result}'
assert_installed_import()
lock = RigctldCommand("\\get_lock_mode", "get_lock_mode")
normal = format_response(lock, RigctldResponse(values=["0"]), ClientSession())
error = format_response(lock, RigctldResponse(values=["0"], error=-5), ClientSession())
extended = format_response(
    lock, RigctldResponse(values=["0"]), ClientSession(extended_mode=True)
)
ordinary = format_response(
    RigctldCommand("f", "get_freq"), RigctldResponse(values=["14074000"]), ClientSession()
)
assert normal == b"0\nRPRT 0\n"
assert error == b"RPRT -5\n"
assert extended == b"get_lock_mode:\n0\nRPRT 0\n"
assert ordinary == b"14074000\n"

node = str(source_root / "tests/test_rigctld_server.py") + (
    "::TestSemiIntegrationSerialMockRadio::test_get_lock_mode_keeps_next_reply_aligned"
)
pytest_result = pytest.main(
    [
        "-c", "/dev/null", "--noconftest", "--rootdir", str(task_root / "pytest-root"),
        "--import-mode=importlib", "-o", f"pythonpath={source_root / 'tests'}",
        "-o", "asyncio_mode=auto", "-o", "asyncio_default_fixture_loop_scope=function",
        "--timeout=30", "--timeout-method=thread", "-q", node,
    ]
)
assert int(pytest_result) == 0, f"installed-wheel fake TCP regression failed: {pytest_result}"
assert_installed_import()
evidence = {
    "package_version": metadata.version("rigplane"),
    "package_path": str(Path(rigplane.__file__).resolve()),
    "distribution_package_path": str(installed_package),
    "formatter_normal": normal.decode("ascii"),
    "formatter_error": error.decode("ascii"),
    "formatter_extended": extended.decode("ascii"),
    "ordinary_get": ordinary.decode("ascii"),
    "fake_tcp_node": node,
    "fake_tcp_exit_code": int(pytest_result),
    "installed_meter_nodes": installed_nodes,
    "installed_meter_exit_code": int(installed_meter_result),
    "installed_meter_test_sha256": copied_tests,
    "source_src_on_import_path": False,
    "hardware_gui_owner_app": "not accessed; test-only SerialMockRadio and loopback port0",
}
(task_root / "installed-software-smoke.json").write_text(
    json.dumps(evidence, indent=2) + "\n", encoding="utf-8"
)
