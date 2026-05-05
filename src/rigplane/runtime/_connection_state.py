"""Radio-level connection state machine."""

from __future__ import annotations

from enum import Enum

__all__ = ["RadioConnectionState"]


class RadioConnectionState(Enum):
    """Connection state for IcomRadio.

    Transitions::

        DISCONNECTED ──connect()──► CONNECTING ──success──► CONNECTED
             ▲                          │                       │
             │                        fail                 disconnect()
             │                          │                       │
             └──────────────────────────┘               DISCONNECTING
                                                               │
        RECONNECTING ◄──watchdog timeout──────────────────────┘
             │
          connect()
             │
          CONNECTING …
    """

    DISCONNECTED = "disconnected"
    """Cleanly disconnected or never connected."""

    CONNECTING = "connecting"
    """connect() is in progress."""

    CONNECTED = "connected"
    """Fully authenticated and operational."""

    DISCONNECTING = "disconnecting"
    """disconnect() is in progress."""

    RECONNECTING = "reconnecting"
    """Connection lost; auto-reconnect is waiting to retry."""
