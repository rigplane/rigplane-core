"""Transport abstractions for the web layer.

Defines the :class:`Connection` protocol — the minimal, transport-agnostic
surface the WebSocket handlers depend on. ``WebSocketConnection`` (the
stdlib RFC 6455 implementation) structurally satisfies it; future transports
(e.g. WebRTC data channels) can satisfy the same protocol without touching
the handlers.
"""

from __future__ import annotations

from .connection import Connection  # noqa: TID251

__all__ = ["Connection"]
