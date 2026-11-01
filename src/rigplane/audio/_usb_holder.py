"""Best-effort name of the program that holds a Windows audio endpoint (MOR-4030).

Used only to enrich the one "the radio's audio input is in use by another
program" refusal. It can never decide whether audio opens: callers get ``None``
(the generic wording) on any trouble, and a lookup that runs past its bound is
abandoned. Only the executable's file name ever leaves this module, never a
path or a user name.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

logger = logging.getLogger(__name__)

HolderLookup = Callable[[str], "str | None"]
"""Takes the PortAudio device name of the endpoint, returns a program name or None."""

HOLDER_LOOKUP_TIMEOUT_S = 1.0


def find_holder_name(device_name: str) -> str | None:
    """The production lookup: no verified mechanism yet, so no name."""
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
