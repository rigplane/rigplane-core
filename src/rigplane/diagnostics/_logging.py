"""Always-on diagnostic logging — best-effort rotating file handler.

Writes rigplane logs (DEBUG level) to a platformdirs-resolved cache
directory. Any I/O failure during init or emit is silently swallowed;
the application continues normally with stdout/stderr logging.

The handler is attached to `logging.getLogger("rigplane")`, NOT root,
so that when rigplane is imported as a library by a host application,
the host's loggers stay out of rigplane's diagnostic file.
"""

from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from rigplane.diagnostics._log_paths import resolve_core_log_dir

_DIAGNOSTIC_FORMATTER = logging.Formatter(
    "%(asctime)s %(levelname)s %(name)s %(message)s"
)
_LOG_FILE_NAME = "rigplane.log"
_MAX_BYTES = 5 * 1024 * 1024  # 5 MiB
_BACKUP_COUNT = 2  # keep 2 rotations → ~15 MiB total
# Mirrors the CLI false-value set at cli/__init__.py:main (ICOM_DEBUG).
_ICOM_DEBUG_FALSE_VALUES = ("", "0", "false", "no")


class SafeRotatingFileHandler(RotatingFileHandler):
    """RotatingFileHandler that never raises on emit; tracks unhealthy state cheaply."""

    def __init__(self, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)  # type: ignore[arg-type]
        self._unhealthy: bool = False

    def emit(self, record: logging.LogRecord) -> None:
        if self._unhealthy:
            return
        try:
            super().emit(record)
        except Exception:  # noqa: BLE001 — best-effort handler, swallow all
            self._unhealthy = True


def configure_diagnostic_logging() -> None:
    """Best-effort init. Any failure is silent; app continues with stdout/stderr.

    Idempotent — calling multiple times only attaches one handler.
    """
    if os.environ.get("RIGPLANE_DISABLE_DIAGNOSTIC_LOGGING") == "1":
        return
    icom_logger = logging.getLogger("rigplane")
    # Idempotency: skip if our handler already present.
    if any(isinstance(h, SafeRotatingFileHandler) for h in icom_logger.handlers):
        return
    # Migrate any legacy v1 platformdirs contents BEFORE we resolve the
    # rigplane log path, so existing log files come along on the very first
    # start of v2.0.0. The helper is idempotent and best-effort.
    try:
        from rigplane._platformdirs_migration import migrate_legacy_platformdirs

        migrate_legacy_platformdirs()
    except Exception:  # noqa: BLE001 — best-effort, swallow all
        pass
    try:
        log_dir = resolve_core_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        handler = SafeRotatingFileHandler(
            log_dir / _LOG_FILE_NAME,
            maxBytes=_MAX_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding="utf-8",
            delay=True,  # don't open file until first emit
        )
        handler.setLevel(logging.DEBUG)
        handler.setFormatter(_DIAGNOSTIC_FORMATTER)
        # Attach to "rigplane" logger, NOT root — see spec §4.1.
        icom_logger.addHandler(handler)
        # Respect an embedding application's explicitly configured level.
        if icom_logger.level == logging.NOTSET:
            debug_mode = (
                os.environ.get("ICOM_DEBUG", "").strip() not in _ICOM_DEBUG_FALSE_VALUES
            )
            icom_logger.setLevel(logging.DEBUG if debug_mode else logging.INFO)
    except Exception as exc:  # noqa: BLE001 — best-effort init, swallow all
        sys.stderr.write(f"rigplane: diagnostic logging disabled: {exc}\n")


def _is_same_log_file(handler_path: str, target: Path) -> bool:
    """Whether *handler_path* and *target* name the same file. Never raises.

    Compares file identity (device + inode via ``os.path.samefile``) when
    both exist, so hard links and case aliases on case-insensitive
    filesystems (macOS) match. Otherwise falls back to comparing normalized,
    resolved paths. Any resolution error — e.g. ``RuntimeError`` from
    ``Path.resolve()`` on a symlink loop, or ``OSError`` — reads as "not the
    same file".
    """
    try:
        if os.path.exists(handler_path) and os.path.exists(target):
            return os.path.samefile(handler_path, target)
    except (OSError, RuntimeError, ValueError):
        pass
    try:
        return os.path.normcase(str(Path(handler_path).resolve())) == os.path.normcase(
            str(target.expanduser().resolve())
        )
    except (OSError, RuntimeError, ValueError):
        return False


def diagnostic_log_path_for(path: Path) -> Path | None:
    """Pathname of the diagnostic handler writing the same file as *path*.

    ``None`` when no diagnostic handler writes that file. The CLI uses it to
    take over ``rigplane.log`` under its canonical name when ``ICOM_LOG_FILE``
    is an alias (hard link, case variant): rotation renames pathnames, so
    rotating the alias would leave ``rigplane.log`` — what diagnostic bundles
    collect — on the old inode with no writer. Never raises.
    """
    for handler in logging.getLogger("rigplane").handlers:
        if isinstance(handler, SafeRotatingFileHandler) and _is_same_log_file(
            handler.baseFilename, Path(path)
        ):
            return Path(handler.baseFilename)
    return None


def detach_diagnostic_logging(path: Path | None = None) -> bool:
    """Remove and close the diagnostic file handler; return whether one was removed.

    With *path*, only a handler writing that same file (same file identity,
    see :func:`_is_same_log_file`) is detached. The CLI calls this right
    after opening its own file handler and before attaching it (MOR-3777),
    so a log file never has two writers, and with no *path* when
    ``ICOM_LOG_FILE=off`` asks for no file logging at all. Idempotent;
    never raises — a handler whose path cannot be resolved is skipped.
    """
    icom_logger = logging.getLogger("rigplane")
    target = Path(path) if path is not None else None
    removed = False
    for handler in list(icom_logger.handlers):
        if not isinstance(handler, SafeRotatingFileHandler):
            continue
        if target is not None and not _is_same_log_file(handler.baseFilename, target):
            continue
        icom_logger.removeHandler(handler)
        try:
            handler.close()
        except Exception:  # noqa: BLE001 — best-effort, same as emit
            pass
        removed = True
    return removed


# Process-wide: stdlib logging should never raise on its own emit failures either.
logging.raiseExceptions = False
