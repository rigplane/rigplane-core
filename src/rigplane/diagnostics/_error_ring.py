"""In-process exception ring — bounded capture of recent tracebacks for diagnostics.

Hooks ``sys.excepthook`` (and optionally an asyncio loop hook) to record uncaught
exceptions into a bounded ring. The ``errors`` contributor reads from a module-level
singleton and serialises a snapshot.

Designed to be opt-in (call ``install_hooks()`` from CLI / web bootstrap), so that
library users (rigctld embedding, scripted automation) are not silently mutated by
import.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
import traceback
from collections import deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

_DEFAULT_CAPACITY = 50


@dataclass
class CapturedException:
    timestamp_unix: int
    type_name: str
    message: str
    traceback_lines: list[str] = field(default_factory=list)


class ExceptionRing:
    """Bounded ring of recently-captured uncaught exceptions."""

    def __init__(self, capacity: int = _DEFAULT_CAPACITY) -> None:
        self._capacity = capacity
        self._items: deque[CapturedException] = deque(maxlen=capacity)
        self._lock = Lock()

    def record(
        self,
        exc_type: type[BaseException],
        exc: BaseException,
        tb: Any,
    ) -> None:
        try:
            tb_lines = traceback.format_exception(exc_type, exc, tb)
        except Exception:
            tb_lines = []
        item = CapturedException(
            timestamp_unix=int(time.time()),
            type_name=exc_type.__name__,
            message=str(exc),
            traceback_lines=tb_lines,
        )
        with self._lock:
            self._items.append(item)

    def snapshot(self) -> list[CapturedException]:
        with self._lock:
            return list(self._items)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


_GLOBAL_RING = ExceptionRing()


def get_ring() -> ExceptionRing:
    return _GLOBAL_RING


_PREVIOUS_EXCEPTHOOK: Any = None
_PREVIOUS_THREADING_EXCEPTHOOK: Any = None


def install_hooks() -> None:
    """Wire ``sys.excepthook`` and ``threading.excepthook`` into the global ring.

    Idempotent — safe to call multiple times. Preserves the prior hooks so
    default reporting (stderr traceback, threading default) still happens.

    ``threading.excepthook`` (PEP 565, Python 3.8+) is required to capture
    uncaught exceptions raised in worker threads — they do NOT flow through
    ``sys.excepthook``.
    """
    global _PREVIOUS_EXCEPTHOOK, _PREVIOUS_THREADING_EXCEPTHOOK
    if _PREVIOUS_EXCEPTHOOK is not None:
        return  # already installed
    _PREVIOUS_EXCEPTHOOK = sys.excepthook
    _PREVIOUS_THREADING_EXCEPTHOOK = threading.excepthook

    def _hook(exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
        try:
            _GLOBAL_RING.record(exc_type, exc, tb)
        except Exception:
            logger.warning("error_ring: record failed", exc_info=True)
        if _PREVIOUS_EXCEPTHOOK is not None:
            _PREVIOUS_EXCEPTHOOK(exc_type, exc, tb)

    def _threading_hook(args: threading.ExceptHookArgs) -> None:
        # ``threading.ExceptHookArgs`` is a named tuple of
        # (exc_type, exc_value, exc_traceback, thread).
        exc_type = args.exc_type
        exc_value = args.exc_value
        exc_tb = args.exc_traceback
        try:
            if exc_type is not None and exc_value is not None:
                _GLOBAL_RING.record(exc_type, exc_value, exc_tb)
        except Exception:
            logger.warning("error_ring: threading record failed", exc_info=True)
        if _PREVIOUS_THREADING_EXCEPTHOOK is not None:
            _PREVIOUS_THREADING_EXCEPTHOOK(args)

    sys.excepthook = _hook
    threading.excepthook = _threading_hook


def uninstall_hooks() -> None:
    """For tests — restore the previous ``sys`` and ``threading`` excepthooks."""
    global _PREVIOUS_EXCEPTHOOK, _PREVIOUS_THREADING_EXCEPTHOOK
    if _PREVIOUS_EXCEPTHOOK is None:
        return
    sys.excepthook = _PREVIOUS_EXCEPTHOOK
    _PREVIOUS_EXCEPTHOOK = None
    if _PREVIOUS_THREADING_EXCEPTHOOK is not None:
        threading.excepthook = _PREVIOUS_THREADING_EXCEPTHOOK
        _PREVIOUS_THREADING_EXCEPTHOOK = None
