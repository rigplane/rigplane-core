"""Internal backend driver contracts for IC-7610 core orchestration."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class CivLink(Protocol):
    """Minimal CI-V transport contract used by backend assemblies."""

    async def send(self, frame: bytes) -> None:
        """Send one encoded CI-V frame."""

    async def receive(self, timeout: float | None = None) -> bytes | None:
        """Receive one encoded CI-V frame or ``None`` on timeout."""


@runtime_checkable
class SessionDriver(Protocol):
    """Minimal backend session lifecycle contract."""

    async def connect(self) -> None:
        """Open backend session resources."""

    async def disconnect(self) -> None:
        """Release backend session resources."""

    @property
    def connected(self) -> bool:
        """Whether the backend session is currently active."""


@runtime_checkable
class AudioDriver(Protocol):
    """Minimal audio path contract for backend implementations."""

    async def start_rx(self) -> None:
        """Start audio receive path."""

    async def stop_rx(self) -> None:
        """Stop audio receive path."""

    async def start_tx(self) -> None:
        """Start audio transmit path."""

    async def stop_tx(self) -> None:
        """Stop audio transmit path."""


__all__ = ["AudioDriver", "CivLink", "SessionDriver"]
