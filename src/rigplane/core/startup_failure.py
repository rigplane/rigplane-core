"""Versioned startup-abort documents for process supervisors."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Coroutine, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from enum import StrEnum
import errno
from functools import wraps
import json
import sys
from typing import Any, ParamSpec, TypeVar

STARTUP_FAILURE_PREFIX = "RIGPLANE_STARTUP_FAILURE "


class StartupCause(StrEnum):
    UNKNOWN = "unknown"
    CONFIGURATION_INVALID = "configuration_invalid"
    PROFILE_UNRESOLVED = "profile_unresolved"
    DISCOVERY_FAILED = "discovery_failed"
    AUDIO_BRIDGE_FAILED = "audio_bridge_failed"
    RADIO_IN_USE = "radio_in_use"
    SERIAL_OPEN_FAILED = "serial_open_failed"
    RADIO_OPEN_FAILED = "radio_open_failed"
    PORT_BUSY = "port_busy"
    LISTENER_BIND_FAILED = "listener_bind_failed"
    WEB_BIND_FAILED = "web_bind_failed"
    RIGCTLD_BIND_FAILED = "rigctld_bind_failed"
    TCI_BIND_FAILED = "tci_bind_failed"
    WEB_START_FAILED = "web_start_failed"


class StartupPhase(StrEnum):
    CONFIGURATION = "configuration"
    PROFILE_RESOLUTION = "profile_resolution"
    DISCOVERY = "discovery"
    AUDIO_BRIDGE = "audio_bridge"
    RADIO_LOCK = "radio_lock"
    LISTENER_PREFLIGHT = "listener_preflight"
    RADIO_OPEN = "radio_open"
    WEB_START = "web_start"
    WEB_BIND = "web_bind"
    RIGCTLD_BIND = "rigctld_bind"
    TCI_BIND = "tci_bind"


_DETAILS = {
    StartupCause.UNKNOWN: "Startup failed.",
    StartupCause.CONFIGURATION_INVALID: "Startup configuration is invalid.",
    StartupCause.PROFILE_UNRESOLVED: "The radio profile could not be resolved or loaded.",
    StartupCause.DISCOVERY_FAILED: "Radio discovery did not select one radio.",
    StartupCause.AUDIO_BRIDGE_FAILED: "The requested audio bridge could not start.",
    StartupCause.RADIO_IN_USE: "Another process or session owns the radio.",
    StartupCause.SERIAL_OPEN_FAILED: "The serial radio connection could not be opened.",
    StartupCause.RADIO_OPEN_FAILED: "The radio connection could not be opened.",
    StartupCause.PORT_BUSY: "A required port is already in use.",
    StartupCause.LISTENER_BIND_FAILED: "A required listener could not bind.",
    StartupCause.WEB_BIND_FAILED: "The web listener could not bind.",
    StartupCause.RIGCTLD_BIND_FAILED: "The rigctld listener could not bind.",
    StartupCause.TCI_BIND_FAILED: "The TCI listener could not bind.",
    StartupCause.WEB_START_FAILED: "Web startup did not complete.",
}


@dataclass(frozen=True)
class StartupFailure:
    schema: int
    cause: str
    phase: str
    detail: str


def parse_startup_failure(line: str) -> StartupFailure | None:
    """Read schema 1, preserving unknown codes; ignore other log lines/versions."""
    if not line.startswith(STARTUP_FAILURE_PREFIX):
        return None
    try:
        payload = json.loads(line[len(STARTUP_FAILURE_PREFIX) :])
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    if type(payload.get("schema")) is not int or payload["schema"] != 1:
        return None
    if not all(
        isinstance(payload.get(key), str) for key in ("cause", "phase", "detail")
    ):
        return None
    return StartupFailure(1, payload["cause"], payload["phase"], payload["detail"])


def _document(cause: StartupCause, phase: StartupPhase) -> StartupFailure:
    return StartupFailure(1, cause.value, phase.value, _DETAILS[cause])


def _exception_document(exc: BaseException) -> StartupFailure | None:
    seen: set[int] = set()
    while id(exc) not in seen:
        seen.add(id(exc))
        document = getattr(exc, "_rigplane_startup_failure", None)
        if isinstance(document, StartupFailure):
            return document
        nested = exc.__cause__ or exc.__context__
        if nested is None:
            break
        exc = nested
    return None


@contextmanager
def startup_operation(cause: StartupCause, phase: StartupPhase) -> Iterator[None]:
    """Attribute a propagated exception without changing its type or message."""
    try:
        yield
    except (Exception, SystemExit) as exc:
        if _exception_document(exc) is None:
            if (
                cause
                in (
                    StartupCause.SERIAL_OPEN_FAILED,
                    StartupCause.LISTENER_BIND_FAILED,
                    StartupCause.WEB_BIND_FAILED,
                    StartupCause.RIGCTLD_BIND_FAILED,
                    StartupCause.TCI_BIND_FAILED,
                )
                and isinstance(exc, OSError)
                and exc.errno in (errno.EADDRINUSE, errno.EBUSY)
            ):
                cause = StartupCause.PORT_BUSY
            setattr(exc, "_rigplane_startup_failure", _document(cause, phase))
        raise


@dataclass
class _Report:
    failure: StartupFailure | None = None
    completed: bool = False


_report: ContextVar[_Report | None] = ContextVar("startup_failure_report", default=None)


def record_startup_failure(
    exc: BaseException | None,
    cause: StartupCause,
    phase: StartupPhase,
) -> None:
    report = _report.get()
    if report is not None:
        if report.failure is not None:
            return
        document = _exception_document(exc) if exc is not None else None
        report.failure = document or _document(cause, phase)


def complete_startup() -> None:
    report = _report.get()
    if report is not None:
        report.completed = True


@contextmanager
def startup_failure_scope(cause: StartupCause, phase: StartupPhase) -> Iterator[None]:
    parent = _report.get()
    report = parent if parent is not None else _Report()
    token = _report.set(report)
    try:
        with startup_operation(cause, phase):
            yield
    except (Exception, SystemExit) as exc:
        if parent is None and (
            not isinstance(exc, SystemExit) or exc.code not in (None, 0)
        ):
            record_startup_failure(exc, cause, phase)
        raise
    finally:
        _report.reset(token)
        if parent is None and report.failure is not None and not report.completed:
            print(
                STARTUP_FAILURE_PREFIX
                + json.dumps(asdict(report.failure), separators=(",", ":")),
                file=sys.stderr,
                flush=True,
            )


_P = ParamSpec("_P")
_R = TypeVar("_R")


def report_startup_failures(
    cause: StartupCause, phase: StartupPhase
) -> Callable[[Callable[_P, Awaitable[_R]]], Callable[_P, Coroutine[Any, Any, _R]]]:
    def decorate(
        function: Callable[_P, Awaitable[_R]],
    ) -> Callable[_P, Coroutine[Any, Any, _R]]:
        @wraps(function)
        async def wrapped(*args: _P.args, **kwargs: _P.kwargs) -> _R:
            with startup_failure_scope(cause, phase):
                result = await function(*args, **kwargs)
                if isinstance(result, int) and result != 0:
                    record_startup_failure(
                        None,
                        StartupCause.CONFIGURATION_INVALID,
                        StartupPhase.CONFIGURATION,
                    )
                return result

        return wrapped

    return decorate
