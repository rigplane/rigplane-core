"""Regression tests for three quick-win infra fixes.

Issues:
- #1876: control-state broadcast floods logs when a subscriber stalls
- #1879: diagnostic logging forced to DEBUG by default
- #1877 (2a only): state-broadcast does heavy work even with zero subscribers
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from unittest.mock import patch

import platformdirs
import pytest

from rigplane._bounded_queue import BoundedQueue
from rigplane.diagnostics._logging import (
    SafeRotatingFileHandler,
    configure_diagnostic_logging,
)
from rigplane.web.server import WebServer


# ---------------------------------------------------------------------------
# Fix A (#1876) — stalled subscriber must NOT produce log warnings
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def _fresh_icom_logger() -> Any:
    """Ensure the rigplane logger is clean between tests."""
    icom_logger = logging.getLogger("rigplane")
    original_level = icom_logger.level
    original_handlers = list(icom_logger.handlers)
    yield icom_logger
    icom_logger.handlers = original_handlers
    icom_logger.setLevel(original_level)


def test_stalled_subscriber_no_log_warnings_on_overflow() -> None:
    """Fix A (#1876): put_drop_oldest replaces put_nowait + warning.

    A stalled (non-draining) control queue that is full must NOT produce
    'control state queue full' log warnings. Verify by filling a queue to
    maxsize and calling _broadcast_state_update many times — zero warnings
    should be emitted, and the server must not raise.
    """
    srv = WebServer(None)
    stalled: BoundedQueue[dict[str, object]] = BoundedQueue(maxsize=4)
    srv.register_control_event_queue(stalled)

    # Drain the initial state_update pushed by register_control_event_queue
    while not stalled.empty():
        stalled.get_nowait()

    # Fill the queue to maxsize with dummy items so it is "stalled"
    for _ in range(stalled.maxsize):
        stalled.put_nowait({"type": "state_update", "data": {}})

    assert stalled.full(), "pre-condition: queue must be full"

    warning_count = 0
    original_warning = logging.getLogger("rigplane.web.server").warning

    def _count_warning(msg: str, *args: object, **kwargs: object) -> None:
        nonlocal warning_count
        if "control state queue full" in str(msg):
            warning_count += 1
        original_warning(msg, *args, **kwargs)

    with patch.object(
        logging.getLogger("rigplane.web.server"),
        "warning",
        side_effect=_count_warning,
    ):
        # Broadcast many times with the queue still full
        for _ in range(10):
            srv._broadcast_state_update(force=True)  # noqa: SLF001

    assert warning_count == 0, (
        f"Expected 0 'control state queue full' warnings, got {warning_count}. "
        "Fix A (#1876): replace put_nowait+warning with put_drop_oldest."
    )

    # The queue must not be empty — drop-oldest ensures the newest event wins
    assert not stalled.empty(), "Queue should still contain items after drop-oldest"


# ---------------------------------------------------------------------------
# Fix B (#1879) — default effective level must be INFO, not DEBUG
# ---------------------------------------------------------------------------


@pytest.fixture()
def _isolated_icom_logger(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    """Isolate the rigplane logger for diagnostic-logging tests."""
    monkeypatch.delenv("RIGPLANE_DISABLE_DIAGNOSTIC_LOGGING", raising=False)
    monkeypatch.setattr(platformdirs, "user_cache_path", lambda app: tmp_path)
    icom_logger = logging.getLogger("rigplane")
    original_level = icom_logger.level
    icom_logger.setLevel(logging.NOTSET)
    yield icom_logger
    icom_logger.handlers = [
        h for h in icom_logger.handlers if not isinstance(h, SafeRotatingFileHandler)
    ]
    icom_logger.setLevel(original_level)


def test_default_effective_level_is_info_not_debug(
    _isolated_icom_logger: logging.Logger,
) -> None:
    """Fix B (#1879): when logger level is NOTSET, configure_diagnostic_logging
    must set it to INFO (not DEBUG) to avoid ~40-57 disk writes/sec on the loop.
    """
    configure_diagnostic_logging()
    level = _isolated_icom_logger.level
    assert level == logging.INFO, (
        f"Expected effective level INFO ({logging.INFO}), got {level}. "
        "Fix B (#1879): change 'setLevel(logging.DEBUG)' to 'setLevel(logging.INFO)'."
    )


def test_env_var_forces_debug_when_set(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fix B (#1879): ICOM_LOG_LEVEL=DEBUG must still opt-in to DEBUG.

    The env var escape hatch for debugging must continue to work
    after the default is changed to INFO.
    """
    monkeypatch.delenv("RIGPLANE_DISABLE_DIAGNOSTIC_LOGGING", raising=False)
    monkeypatch.setattr(platformdirs, "user_cache_path", lambda app: tmp_path)
    icom_logger = logging.getLogger("rigplane")
    original_level = icom_logger.level
    try:
        # Pre-set to DEBUG as if the env var or caller forced it
        icom_logger.setLevel(logging.DEBUG)
        configure_diagnostic_logging()
        # configure_diagnostic_logging must NOT override a level already set
        assert icom_logger.level == logging.DEBUG, (
            "Level explicitly set to DEBUG before configure_diagnostic_logging "
            "must be preserved (DEBUG is already set, not NOTSET)."
        )
    finally:
        icom_logger.handlers = [
            h
            for h in icom_logger.handlers
            if not isinstance(h, SafeRotatingFileHandler)
        ]
        icom_logger.setLevel(original_level)


# ---------------------------------------------------------------------------
# Fix C (#1877 part 2a) — skip expensive build when no subscribers
# ---------------------------------------------------------------------------


def test_no_subscribers_skips_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fix C (#1877 2a): with zero control subscribers and force=False,
    _build_public_state_from_snapshot must NOT be called.
    """
    srv = WebServer(None)
    assert not srv._control_event_queues, "pre-condition: no subscribers"  # noqa: SLF001

    build_calls: list[object] = []
    original_build = srv._build_public_state_from_snapshot  # noqa: SLF001

    def _spy_build(*args: object, **kwargs: object) -> object:
        build_calls.append(True)
        return original_build(*args, **kwargs)

    monkeypatch.setattr(srv, "_build_public_state_from_snapshot", _spy_build)

    srv._broadcast_state_update(force=False)  # noqa: SLF001

    assert len(build_calls) == 0, (
        f"Expected _build_public_state_from_snapshot NOT called with zero "
        f"subscribers and force=False, but was called {len(build_calls)} time(s). "
        "Fix C (#1877 2a): add 'if not force and not self._control_event_queues: return'."
    )


def test_connecting_client_still_gets_initial_state() -> None:
    """Fix C (#1877 2a): force=True (used on connect) must still build and deliver."""
    srv = WebServer(None)
    q: BoundedQueue[dict[str, object]] = BoundedQueue(maxsize=8)
    srv.register_control_event_queue(q)

    # Drain the connect-time event
    while not q.empty():
        q.get_nowait()

    # force=True path must always build
    srv._broadcast_state_update(force=True)  # noqa: SLF001
    assert not q.empty(), (
        "force=True must deliver a state_update even with one subscriber. "
        "Fix C (#1877 2a): ensure force=True bypasses the subscriber-count gate."
    )
    event = q.get_nowait()
    assert event["type"] == "state_update"
