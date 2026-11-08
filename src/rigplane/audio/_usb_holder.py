"""Best-effort name of the program that holds a Windows audio endpoint (MOR-4030).

Used only to enrich the one "the radio's audio input is in use by another
program" refusal. It can never decide whether audio opens: callers get ``None``
(the generic wording) on any trouble, and a lookup that runs past its bound is
abandoned. Only the executable's file name ever leaves this module, never a
path or a user name.

The mechanism, verified on a real Windows host for exclusive, MME shared and
WASAPI shared holders: ``IAudioSessionManager2`` lists the sessions of the
capture endpoint (``_windows_audio_sessions``), and the holder is the process of
the active session that is not ours and not the system sounds. It works from the
holder's own interactive Windows session; a holder in another user's session, an
elevated or packaged (Store) program, or a WDM-KS holder gives no name.
"""

from __future__ import annotations

import logging
import os
import platform
import threading
from dataclasses import dataclass
from typing import Callable

logger = logging.getLogger(__name__)

HolderLookup = Callable[[str], "str | None"]
"""Takes the PortAudio device name of the endpoint, returns a program name or None."""

HOLDER_LOOKUP_TIMEOUT_S = 0.5

_SESSION_ACTIVE = 1
"""AudioSessionState value of a session that is using the endpoint right now."""


@dataclass(frozen=True, slots=True)
class AudioSession:
    """One program's audio session on an endpoint."""

    pid: int
    state: int
    system_sounds: bool


def _is_windows() -> bool:
    return platform.system() == "Windows"


def _list_capture_sessions(device_name: str) -> list[AudioSession] | None:
    """The sessions of the capture endpoint named *device_name*, or None."""
    from ._windows_audio_sessions import capture_sessions

    rows = capture_sessions(device_name)
    if rows is None:
        return None
    return [AudioSession(pid, state, system) for pid, state, system in rows]


def _process_exe_name(pid: int) -> str | None:
    from ._windows_audio_sessions import process_image_name

    return process_image_name(pid)


def pick_holder(
    sessions: list[AudioSession],
    own_pid: int,
    resolve: Callable[[int], str | None],
) -> str | None:
    """The executable name of the first active foreign session, else None.

    Skips our own process, pid 0, the system-sounds session and sessions that
    are not active; a process that cannot be resolved falls through to the
    next session.
    """
    for session in sessions:
        if (
            session.pid <= 0
            or session.pid == own_pid
            or session.system_sounds
            or session.state != _SESSION_ACTIVE
        ):
            continue
        try:
            name = _exe_name(resolve(session.pid))
        except Exception as exc:
            logger.debug(
                "usb-audio: holder process unresolved (%s)", type(exc).__name__
            )
            continue
        if name:
            return name
    return None


def find_holder_name(device_name: str) -> str | None:
    """The program holding the capture endpoint *device_name*, or None.

    Windows only; never raises. Callers bound it (``holder_exe_name``) and call
    it only when the in-use refusal has already fired.
    """
    if not _is_windows():
        return None
    try:
        sessions = _list_capture_sessions(device_name)
        if not sessions:
            return None
        return pick_holder(sessions, os.getpid(), _process_exe_name)
    except Exception as exc:
        logger.debug("usb-audio: holder lookup failed (%s)", type(exc).__name__)
        return None


def _exe_name(value: object) -> str | None:
    """The file name part of *value*, or ``None`` when nothing usable is left."""
    if not isinstance(value, str):
        return None
    name = value.strip().replace("\\", "/").rsplit("/", 1)[-1].strip()
    return name or None


def holder_exe_name(
    lookup: HolderLookup, device_name: str, timeout_s: float = HOLDER_LOOKUP_TIMEOUT_S
) -> str | None:
    """Run *lookup* for at most *timeout_s* seconds; never raise.

    The lookup runs on a daemon thread, so a slow one cannot delay the caller
    past the bound (it is abandoned and finishes unattended).
    """
    found: list[object] = []

    def run() -> None:
        try:
            found.append(lookup(device_name))
        except Exception as exc:
            # The type only: a message could carry a path or a user name.
            logger.debug("usb-audio: holder lookup failed (%s)", type(exc).__name__)

    thread = threading.Thread(target=run, name="rigplane-holder-lookup", daemon=True)
    thread.start()
    thread.join(timeout_s)
    if thread.is_alive() or not found:
        return None
    return _exe_name(found[0])
