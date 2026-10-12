"""MOR-3391: one resident process per radio per computer — guard unit rows.

The guard lives in ``rigplane.cli._instance_guard``: resident commands take an
exclusive non-blocking OS file lock on a per-radio lock file before the radio
is opened. The OS lock is the authority; a pid written into the file is only
a hint for the refusal message.
"""

from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from rigplane.backends.config import LanBackendConfig, SerialBackendConfig
from rigplane.cli._instance_guard import (
    ResidentAlreadyRunningError,
    ResidentLock,
    acquire_resident_lock,
    radio_lock_identity,
)

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")

_CHILD_HOLDER = (
    "import sys, time\n"
    "from rigplane.cli._instance_guard import acquire_resident_lock\n"
    "guard = acquire_resident_lock(sys.argv[1], cache_dir=sys.argv[2])\n"
    "print('READY', flush=True)\n"
    "time.sleep(60)\n"
)


def _spawn_holder(identity: str, cache_dir: Path) -> subprocess.Popen[str]:
    """Start a child that holds the guard for *identity* until killed."""
    env = dict(os.environ)
    src_path = SRC_DIR + os.pathsep + env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = src_path
    proc = subprocess.Popen(
        [sys.executable, "-c", _CHILD_HOLDER, identity, str(cache_dir)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    assert proc.stdout is not None
    line = proc.stdout.readline()
    assert line.strip() == "READY", (
        f"holder child failed: {proc.stderr.read() if proc.stderr else ''}"
    )
    return proc


def _try_acquire(
    identity: str, cache_dir: Path, timeout_s: float = 10.0
) -> tuple[str, ResidentAlreadyRunningError | ResidentLock | None]:
    """Acquire in a worker thread with a bounded wait.

    The lock is non-blocking, so this always returns quickly. The bound
    exists so a regression to blocking mode fails instead of hanging the run.
    """
    results: queue.Queue[
        tuple[str, ResidentAlreadyRunningError | ResidentLock | None]
    ] = queue.Queue()

    def _worker() -> None:
        try:
            guard = acquire_resident_lock(identity, cache_dir=cache_dir)
        except ResidentAlreadyRunningError as exc:
            results.put(("refused", exc))
        else:
            results.put(("acquired", guard))

    worker = threading.Thread(target=_worker, daemon=True)
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        pytest.fail(
            "guard acquire hung past its bounded wait — the lock must be non-blocking"
        )
    return results.get_nowait()


@pytest.fixture()
def holder(tmp_path: Path):
    """A child process holding the guard for a LAN identity; killed on teardown."""
    identity = "lan:1.2.3.4:50001"
    proc = _spawn_holder(identity, tmp_path)
    yield proc, identity, tmp_path
    proc.kill()
    proc.wait(timeout=30)


def test_same_identity_refused_names_holder_pid(
    holder: tuple[subprocess.Popen[str], str, Path],
) -> None:
    """A second guard for the same radio is refused and names the holder's pid."""
    proc, identity, cache_dir = holder
    kind, result = _try_acquire(identity, cache_dir)
    assert kind == "refused"
    assert isinstance(result, ResidentAlreadyRunningError)
    assert str(proc.pid) in str(result)


def test_different_identity_not_blocked(
    holder: tuple[subprocess.Popen[str], str, Path],
) -> None:
    """A guard for another radio identity is unaffected by the held lock."""
    _, _, cache_dir = holder
    kind, result = _try_acquire("lan:1.2.3.9:50001", cache_dir)
    assert kind == "acquired"
    assert isinstance(result, ResidentLock)
    result.close()


def test_sigkill_releases_lock(tmp_path: Path) -> None:
    """After the holder dies by SIGKILL the same identity can be acquired."""
    identity = "lan:1.2.3.4:50001"
    proc = _spawn_holder(identity, tmp_path)
    proc.kill()  # SIGKILL: the OS releases the lock; no cleanup runs.
    proc.wait(timeout=30)
    kind, result = _try_acquire(identity, tmp_path)
    assert kind == "acquired"
    assert isinstance(result, ResidentLock)
    result.close()


def test_unwritable_cache_dir_warns_without_guard(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """An unusable cache dir logs one warning and starts without the guard."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    try:
        is_root = os.geteuid() == 0
    except AttributeError:  # Windows has no geteuid; chmod still applies.
        is_root = False
    if is_root:
        # Root bypasses permission bits, so a regular file stands in for the
        # unwritable directory: creating locks/ under it fails either way.
        blocker = tmp_path / "blocker"
        blocker.write_text("x")
        cache_dir = blocker
    else:
        os.chmod(cache_dir, 0o500)
    try:
        with caplog.at_level(logging.WARNING, logger="rigplane.cli._instance_guard"):
            guard = acquire_resident_lock("lan:1.2.3.4:50001", cache_dir=cache_dir)
    finally:
        if cache_dir.is_dir():
            os.chmod(cache_dir, 0o700)
    assert guard is None
    assert any(
        "without" in rec.message.lower() and "guard" in rec.message.lower()
        for rec in caplog.records
    ), f"expected a no-guard warning, got {[r.message for r in caplog.records]}"


def test_same_process_reacquire_returns_held_lock(tmp_path: Path) -> None:
    """Re-entering in the same process reuses the held lock (per-process guard)."""
    identity = "lan:1.2.3.4:50001"
    first = acquire_resident_lock(identity, cache_dir=tmp_path)
    assert isinstance(first, ResidentLock)
    try:
        second = acquire_resident_lock(identity, cache_dir=tmp_path)
        assert second is first
    finally:
        first.close()
    third = acquire_resident_lock(identity, cache_dir=tmp_path)
    assert isinstance(third, ResidentLock)
    assert third is not first
    third.close()


def test_radio_lock_identity_rows() -> None:
    """The lock identity keys on the radio endpoint, not on credentials."""
    lan = LanBackendConfig(host="1.2.3.4", port=50001, username="a", password="b")
    same_radio = LanBackendConfig(
        host=" 1.2.3.4 ", port=50001, username="c", password="d"
    )
    other_radio = LanBackendConfig(host="1.2.3.9", port=50001)
    serial = SerialBackendConfig(device="/dev/ttyUSB0")
    assert radio_lock_identity(lan) == radio_lock_identity(same_radio)
    assert radio_lock_identity(lan) != radio_lock_identity(other_radio)
    assert radio_lock_identity(lan) != radio_lock_identity(serial)


@pytest.mark.skipif(
    sys.platform != "win32",
    reason="Windows-only: exercises the msvcrt.locking branch",
)
def test_windows_branch_same_identity_refused(tmp_path: Path) -> None:
    """On Windows the second acquire for the same identity is refused."""
    identity = "lan:1.2.3.4:50001"
    first = acquire_resident_lock(identity, cache_dir=tmp_path)
    assert first is not None
    try:
        with pytest.raises(ResidentAlreadyRunningError):
            acquire_resident_lock(identity, cache_dir=tmp_path)
    finally:
        first.close()
