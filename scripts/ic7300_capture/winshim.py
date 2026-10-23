"""Windows shim for a client that talks to the bridge over ``socket://``.

pyserial-asyncio's Windows transport polls ``serial.out_waiting``, which
pyserial's ``socket://`` handler does not implement, so a Core client pointed
at the bridge never finishes a write and hangs. Real COM ports have the
attribute; only this capture setup (client and bridge on one Windows host)
needs it. A capture-script workaround, not a Core fix.
"""

from __future__ import annotations

import sys


def apply() -> None:
    if sys.platform != "win32":
        return
    from serial.urlhandler import protocol_socket

    if not hasattr(protocol_socket.Serial, "out_waiting"):
        protocol_socket.Serial.out_waiting = property(lambda self: 0)  # type: ignore[attr-defined]
