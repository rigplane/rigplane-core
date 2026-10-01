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


# Process-wide: stdlib logging should never raise on its own emit failures either.
logging.raiseExceptions = False
