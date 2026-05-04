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


def install_hooks() -> None:
    """Wire ``sys.excepthook`` to record into the global ring.

    Idempotent — safe to call multiple times. Preserves the prior excepthook
    so default reporting still happens.
    """
    global _PREVIOUS_EXCEPTHOOK
    if _PREVIOUS_EXCEPTHOOK is not None:
        return  # already installed
    _PREVIOUS_EXCEPTHOOK = sys.excepthook

    def _hook(exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
        try:
            _GLOBAL_RING.record(exc_type, exc, tb)
        except Exception:
            logger.warning("error_ring: record failed", exc_info=True)
        if _PREVIOUS_EXCEPTHOOK is not None:
            _PREVIOUS_EXCEPTHOOK(exc_type, exc, tb)

    sys.excepthook = _hook


def uninstall_hooks() -> None:
    """For tests — restore the previous excepthook."""
    global _PREVIOUS_EXCEPTHOOK
    if _PREVIOUS_EXCEPTHOOK is None:
        return
    sys.excepthook = _PREVIOUS_EXCEPTHOOK
    _PREVIOUS_EXCEPTHOOK = None
