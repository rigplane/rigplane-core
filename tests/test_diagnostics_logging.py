"""Tests for icom_lan.diagnostics._logging."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

import platformdirs
import pytest

from icom_lan.diagnostics._logging import (
    SafeRotatingFileHandler,
    configure_diagnostic_logging,
)


@pytest.fixture(autouse=True)
def _enable_diagnostic_logging(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Re-enable diagnostic logging locally for each test, then clean up."""
    monkeypatch.delenv("ICOM_LAN_DISABLE_DIAGNOSTIC_LOGGING", raising=False)
    yield
    icom_logger = logging.getLogger("icom_lan")
    icom_logger.handlers = [
        h for h in icom_logger.handlers if not isinstance(h, SafeRotatingFileHandler)
    ]


def test_handler_attached_to_icom_lan_logger_not_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(platformdirs, "user_cache_path", lambda app: tmp_path)
    configure_diagnostic_logging()
    icom_handlers = [
        h
        for h in logging.getLogger("icom_lan").handlers
        if isinstance(h, SafeRotatingFileHandler)
    ]
    root_handlers = [
        h
        for h in logging.getLogger().handlers
        if isinstance(h, SafeRotatingFileHandler)
    ]
    assert len(icom_handlers) == 1
    assert len(root_handlers) == 0


def test_init_swallows_permission_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def _raise_perm(app: str) -> Path:
        raise PermissionError("no perms for you")

    monkeypatch.setattr(platformdirs, "user_cache_path", _raise_perm)
    configure_diagnostic_logging()
    captured = capsys.readouterr()
    assert "icom-lan: diagnostic logging disabled" in captured.err


def test_init_swallows_oserror(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(platformdirs, "user_cache_path", lambda app: tmp_path)

    def _raise_oserror(self: Path, *args: Any, **kwargs: Any) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(Path, "mkdir", _raise_oserror)
    configure_diagnostic_logging()
    captured = capsys.readouterr()
    assert "icom-lan: diagnostic logging disabled" in captured.err


def test_emit_swallows_exception_marks_unhealthy(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    handler = SafeRotatingFileHandler(
        tmp_path / "test.log",
        maxBytes=1024,
        backupCount=1,
        delay=True,
    )

    def _raise_emit(self: Any, record: logging.LogRecord) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(RotatingFileHandler, "emit", _raise_emit)
    record = logging.LogRecord(
        "icom_lan.test", logging.DEBUG, __file__, 0, "msg", None, None
    )
    handler.emit(record)
    assert handler._unhealthy is True
    # Subsequent emit must be a no-op (no exception, no call to super.emit)
    handler.emit(record)
    assert handler._unhealthy is True


def test_disabled_via_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ICOM_LAN_DISABLE_DIAGNOSTIC_LOGGING", "1")
    configure_diagnostic_logging()
    icom_handlers = [
        h
        for h in logging.getLogger("icom_lan").handlers
        if isinstance(h, SafeRotatingFileHandler)
    ]
    assert len(icom_handlers) == 0


def test_idempotent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(platformdirs, "user_cache_path", lambda app: tmp_path)
    configure_diagnostic_logging()
    configure_diagnostic_logging()
    icom_handlers = [
        h
        for h in logging.getLogger("icom_lan").handlers
        if isinstance(h, SafeRotatingFileHandler)
    ]
    assert len(icom_handlers) == 1


def test_logging_raiseexceptions_false() -> None:
    # _logging module sets this at import time; module is already imported.
    assert logging.raiseExceptions is False


def test_log_file_writes_to_platformdirs_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(platformdirs, "user_cache_path", lambda app: tmp_path)
    configure_diagnostic_logging()
    logger = logging.getLogger("icom_lan.test")
    logger.debug("hello")
    log_file = tmp_path / "logs" / "icom-lan.log"
    assert log_file.exists()
    assert "hello" in log_file.read_text(encoding="utf-8")


def test_preset_logger_level_preserved(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Host-app-set level must be respected — diagnostic init must NOT force DEBUG.

    Regression test for Codex review on PR #1402: previously the init would
    overwrite any level >= DEBUG (including WARNING/INFO), leaking icom_lan
    DEBUG records into host-application handlers.
    """
    monkeypatch.setattr(platformdirs, "user_cache_path", lambda app: tmp_path)
    icom_logger = logging.getLogger("icom_lan")
    icom_logger.setLevel(logging.WARNING)
    try:
        configure_diagnostic_logging()
        # Init must NOT have downgraded the host-app's WARNING level to DEBUG.
        assert icom_logger.level == logging.WARNING
    finally:
        icom_logger.setLevel(logging.NOTSET)


def test_unset_logger_level_set_to_debug(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """When the logger level is NOTSET, init promotes it to DEBUG."""
    monkeypatch.setattr(platformdirs, "user_cache_path", lambda app: tmp_path)
    icom_logger = logging.getLogger("icom_lan")
    icom_logger.setLevel(logging.NOTSET)
    configure_diagnostic_logging()
    assert icom_logger.level == logging.DEBUG
