"""MOR-3777 C1: ``rigplane.log`` has exactly one writer.

Two mechanisms used to write the same file: the always-on diagnostic
handler attached to the ``rigplane`` logger at ``import rigplane``
(``diagnostics/_logging.py``) and the CLI's ``RotatingFileHandler`` on the
root logger (``cli.main``). Every ``rigplane.*`` record reached both, so the
file got each line twice in two formats, and ``ICOM_LOG_FILE=off`` only
removed the CLI writer — the diagnostic one kept writing.

The integration tests drive the real entry point (``python -m rigplane``)
in a subprocess with diagnostic logging enabled (the test suite disables it
by default in ``conftest.py``, which is why the duplication was invisible).
The command is ``status`` against a serial port that does not exist, so it
fails fast, never touches a radio and never transmits.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from rigplane.diagnostics._logging import (
    SafeRotatingFileHandler,
    configure_diagnostic_logging,
)

_NO_SUCH_PORT = "/nonexistent/mor3777-tty"
_PROFILES_LOADED = "rigplane.profiles"
_CLI_STYLE = " rigplane.profiles [DEBUG] Loaded "
_DIAGNOSTIC_STYLE = " DEBUG rigplane.profiles Loaded "
_RECORD_START = re.compile(r"^\d{4}-\d\d-\d\d \d\d:\d\d:\d\d")


def _run_cli(
    tmp_path: Path, command: str, extra_env: dict[str, str]
) -> tuple[Path, subprocess.CompletedProcess[str]]:
    log_dir = tmp_path / "logs"
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {
            "RIGPLANE_DISABLE_DIAGNOSTIC_LOGGING",
            "ICOM_LOG_FILE",
            "ICOM_DEBUG",
            "PYTEST_CURRENT_TEST",
        }
    }
    env.update(
        {
            "RIGPLANE_LOG_DIR": str(log_dir),
            "HOME": str(tmp_path / "home"),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            "XDG_CONFIG_HOME": str(tmp_path / "config"),
            "XDG_DATA_HOME": str(tmp_path / "data"),
            "XDG_STATE_HOME": str(tmp_path / "state"),
            "ICOM_PID_FILE": "",
        }
    )
    env.update(extra_env)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "rigplane",
            "--backend",
            "serial",
            "--serial-port",
            _NO_SUCH_PORT,
            "--model",
            "IC-7300",
            command,
        ],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return log_dir, proc


def _profile_lines(path: Path) -> list[str]:
    return [
        line
        for line in path.read_text(encoding="utf-8").splitlines()
        if _PROFILES_LOADED in line and "Loaded" in line
    ]


def _assert_no_duplicate_records(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    assert lines, f"{path} is empty"
    # Strip the timestamp prefix and normalise both known formats down to
    # "<logger> <message>" so a record written once per writer is caught
    # even though the two writers format it differently.
    messages: list[str] = []
    for line in lines:
        if not _RECORD_START.match(line):
            continue  # traceback continuation lines repeat legitimately
        if " [" in line and "] " in line.split(" [", 1)[1]:
            head, _, msg = line.partition("] ")
            name = head.rsplit(" [", 1)[0].split(" ")[-1]
            messages.append(f"{name} {msg}")
        else:
            parts = line.split(" ", 4)
            if len(parts) == 5:
                messages.append(f"{parts[3]} {parts[4]}")
    dupes = {m: n for m, n in Counter(messages).items() if n > 1 and m.strip()}
    assert not dupes, f"duplicated records in {path}: {list(dupes)[:3]}"


# ---------------------------------------------------------------------------
# Integration: real CLI entry point in a subprocess
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("command", ["status", "serve"])
def test_cli_debug_run_writes_each_record_once(tmp_path: Path, command: str) -> None:
    log_dir, proc = _run_cli(tmp_path, command, {"ICOM_DEBUG": "1"})
    log_file = log_dir / "rigplane.log"

    assert proc.returncode != 0  # the fake serial port cannot open
    assert log_file.is_file(), proc.stderr
    profile_lines = _profile_lines(log_file)
    assert len(profile_lines) == 1, profile_lines
    assert _CLI_STYLE in profile_lines[0]
    _assert_no_duplicate_records(log_file)


@pytest.mark.parametrize("command", ["status", "serve"])
def test_cli_icom_log_file_off_writes_no_log_file(tmp_path: Path, command: str) -> None:
    # ICOM_DEBUG=1 makes rigplane.* emit DEBUG records, so a still-attached
    # writer would create the file.
    log_dir, proc = _run_cli(
        tmp_path, command, {"ICOM_DEBUG": "1", "ICOM_LOG_FILE": "off"}
    )

    assert proc.returncode != 0
    written = sorted(p.name for p in log_dir.glob("*")) if log_dir.exists() else []
    assert not (log_dir / "rigplane.log").exists(), written
    assert written == []


def test_cli_explicit_log_file_and_diagnostic_file_each_have_one_writer(
    tmp_path: Path,
) -> None:
    explicit = tmp_path / "explicit" / "cli.log"
    log_dir, proc = _run_cli(
        tmp_path, "status", {"ICOM_DEBUG": "1", "ICOM_LOG_FILE": str(explicit)}
    )

    assert proc.returncode != 0
    # The CLI writes the file it was told to write ...
    assert len(_profile_lines(explicit)) == 1
    _assert_no_duplicate_records(explicit)
    # ... and rigplane.log (what `rigplane diagnose` bundles) keeps exactly
    # one writer: the diagnostic handler.
    diag = log_dir / "rigplane.log"
    profile_lines = _profile_lines(diag)
    assert len(profile_lines) == 1, profile_lines
    assert _DIAGNOSTIC_STYLE in profile_lines[0]
    _assert_no_duplicate_records(diag)


# ---------------------------------------------------------------------------
# Unit: detach_diagnostic_logging + cli.main wiring (in-process)
# ---------------------------------------------------------------------------


@pytest.fixture
def _diagnostic_handler(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.delenv("RIGPLANE_DISABLE_DIAGNOSTIC_LOGGING", raising=False)
    monkeypatch.setenv("RIGPLANE_LOG_DIR", str(tmp_path / "logs"))
    icom_logger = logging.getLogger("rigplane")
    saved_level = icom_logger.level
    saved = list(icom_logger.handlers)
    icom_logger.handlers = [
        h for h in saved if not isinstance(h, SafeRotatingFileHandler)
    ]
    configure_diagnostic_logging()
    yield tmp_path / "logs" / "rigplane.log"
    for h in icom_logger.handlers:
        if isinstance(h, SafeRotatingFileHandler):
            h.close()
    icom_logger.handlers = [
        h for h in saved if not isinstance(h, SafeRotatingFileHandler)
    ]
    icom_logger.setLevel(saved_level)


def detach_diagnostic_logging(path: Path | None = None) -> bool:
    from rigplane.diagnostics._logging import (
        detach_diagnostic_logging as _detach,
    )

    return _detach(path)


def _diag_handlers() -> list[logging.Handler]:
    return [
        h
        for h in logging.getLogger("rigplane").handlers
        if isinstance(h, SafeRotatingFileHandler)
    ]


def test_detach_all_removes_and_closes_handler(_diagnostic_handler: Path) -> None:
    (handler,) = _diag_handlers()
    with patch.object(handler, "close", wraps=handler.close) as closed:
        assert detach_diagnostic_logging() is True
    closed.assert_called_once()
    assert _diag_handlers() == []
    logging.getLogger("rigplane.mor3777").warning("after detach")
    assert not _diagnostic_handler.exists()
    assert detach_diagnostic_logging() is False  # idempotent


def test_detach_for_other_path_keeps_handler(
    _diagnostic_handler: Path, tmp_path: Path
) -> None:
    assert detach_diagnostic_logging(tmp_path / "elsewhere.log") is False
    assert len(_diag_handlers()) == 1


def test_detach_for_same_path_removes_handler(_diagnostic_handler: Path) -> None:
    # A path spelled differently but resolving to the same file still matches.
    alias = _diagnostic_handler.parent / "." / _diagnostic_handler.name
    assert detach_diagnostic_logging(alias) is True
    assert _diag_handlers() == []


def _run_main_serve(env: dict[str, str], command: str = "serve") -> None:
    with patch.dict("os.environ", {"ICOM_PID_FILE": "", **env}, clear=False):
        with patch("sys.argv", ["rigplane", "--host", "127.0.0.1", command]):
            with patch("rigplane.cli._run", new_callable=AsyncMock, return_value=0):
                with patch(
                    "rigplane.cli.os._exit",
                    side_effect=lambda code: (_ for _ in ()).throw(SystemExit(code)),
                ):
                    from rigplane.cli import main

                    with pytest.raises(SystemExit):
                        main()


@pytest.fixture
def _restore_root_logging():
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    yield
    for h in root.handlers:
        if h not in saved_handlers:
            h.close()
    root.handlers = saved_handlers
    root.setLevel(saved_level)


@pytest.mark.usefixtures("_restore_root_logging")
def test_main_daemon_default_file_detaches_diagnostic_writer(
    _diagnostic_handler: Path,
) -> None:
    _run_main_serve({"ICOM_LOG_FILE": ""})
    assert _diag_handlers() == []
    cli_files = [
        Path(h.baseFilename)
        for h in logging.getLogger().handlers
        if isinstance(h, logging.FileHandler)
    ]
    assert cli_files == [_diagnostic_handler]


@pytest.mark.usefixtures("_restore_root_logging")
def test_main_log_file_off_detaches_diagnostic_writer(
    _diagnostic_handler: Path,
) -> None:
    _run_main_serve({"ICOM_LOG_FILE": "off"})
    assert _diag_handlers() == []
    assert not any(
        isinstance(h, logging.FileHandler) for h in logging.getLogger().handlers
    )


@pytest.mark.usefixtures("_restore_root_logging")
def test_main_explicit_other_file_keeps_diagnostic_writer(
    _diagnostic_handler: Path, tmp_path: Path
) -> None:
    _run_main_serve({"ICOM_LOG_FILE": str(tmp_path / "other.log")})
    assert len(_diag_handlers()) == 1


@pytest.mark.usefixtures("_restore_root_logging")
def test_main_short_command_without_cli_file_keeps_diagnostic_writer(
    _diagnostic_handler: Path,
) -> None:
    """Non-daemon, no debug, no ICOM_LOG_FILE: the CLI adds no file handler,
    so the diagnostic handler stays the sole writer and short invocations
    still leave a trail for bug reports
    (docs/plans/2026-05-03-diagnostic-data-collection-design.md)."""
    _run_main_serve({"ICOM_LOG_FILE": "", "ICOM_DEBUG": "0"}, command="status")
    assert len(_diag_handlers()) == 1
    assert not any(
        isinstance(h, logging.FileHandler) for h in logging.getLogger().handlers
    )
