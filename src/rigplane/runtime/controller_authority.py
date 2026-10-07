"""Ephemeral controller currency beside the existing managed TX authority."""

from __future__ import annotations

import asyncio
import secrets
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Literal


class ControllerError(PermissionError):
    def __init__(self, code: str, status: int = 403) -> None:
        self.code = code
        self.status = status
        super().__init__(code)


@dataclass(frozen=True, slots=True)
class ControllerTicket:
    authority: ControllerAuthority = field(repr=False)
    generation: int
    remote: bool
    session_id: str | None = None
    primary: bool = False


_current: ContextVar[ControllerTicket | None] = ContextVar("controller", default=None)


class ControllerAuthority:
    TTL_MS = 6000
    HEARTBEAT_MS = 2000

    def __init__(
        self,
        *,
        ready: Callable[[], bool],
        cleanup: Callable[[], Awaitable[None]],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ready = ready
        self._cleanup = cleanup
        self._clock = clock
        self.mode: Literal["local", "remote"] = "local"
        self.generation = 0
        self._state = "idle"
        self._key: str | None = None
        self._deadline = 0.0
        self._primary: ControllerTicket | None = None
        self._channels: dict[ControllerTicket, Callable[[], None]] = {}
        self._revocation: asyncio.Task[None] | None = None
        self._watchdog: asyncio.Task[None] | None = None
        self._taking_back = False

    def _expire(self) -> None:
        if self._key is not None and self._clock() >= self._deadline:
            self.revoke()

    def _is_ready(self) -> bool:
        try:
            return self._ready() is True
        except Exception:
            return False

    def status(self) -> dict[str, object]:
        self._expire()
        return {
            "protocol_version": 1,
            "mode": self.mode,
            "state": self._state,
            "generation": self.generation,
            "ttl_ms": self.TTL_MS,
            "heartbeat_ms": self.HEARTBEAT_MS,
            "remaining_ms": max(0, int((self._deadline - self._clock()) * 1000))
            if self._key is not None
            else 0,
        }

    async def set_mode(self, mode: str) -> None:
        if mode not in ("local", "remote"):
            raise ControllerError("invalid_request", 400)
        if self._taking_back:
            raise ControllerError("controller_busy", 409)
        if mode == self.mode and self._state != "blocked":
            return
        if mode == "local" and self.mode == "remote":
            self._taking_back = True
            try:
                if self._state != "blocked":
                    self.revoke()
                await self.settle()
                if not self._is_ready():
                    raise ControllerError("controller_not_ready", 503)
                self.generation += 1
                self.mode = "local"
                self._state = "idle"
            finally:
                self._taking_back = False
            return
        if not self._is_ready():
            raise ControllerError("controller_not_ready", 503)
        self.generation += 1
        self.mode = mode
        self._state = "idle"

    def acquire(self, kind: str) -> dict[str, object]:
        self._expire()
        if kind not in ("browser", "pro"):
            raise ControllerError("invalid_request", 400)
        if self.mode != "remote":
            raise ControllerError("remote_disabled")
        if self._taking_back or self._state in ("held", "revoking"):
            raise ControllerError("controller_busy", 409)
        if self._state == "blocked" or not self._is_ready():
            raise ControllerError("controller_not_ready", 503)
        self.generation += 1
        self._key = secrets.token_hex(32)
        self._deadline = self._clock() + self.TTL_MS / 1000
        self._state = "held"
        self._watchdog = asyncio.create_task(self._watch(self.generation))
        return {
            "controller_key": self._key,
            "generation": self.generation,
            "ttl_ms": self.TTL_MS,
            "heartbeat_ms": self.HEARTBEAT_MS,
        }

    def credential(self, key: object) -> ControllerTicket:
        self._expire()
        if not (
            type(key) is str
            and len(key) == 64
            and key.isascii()
            and self._key is not None
            and self._state == "held"
            and secrets.compare_digest(key, self._key)
        ):
            raise ControllerError("controller_invalid")
        return ControllerTicket(self, self.generation, True)

    def attach(self, key: object, role: str, session_id: str) -> ControllerTicket:
        grant = self.credential(key)
        if role not in ("primary", "auxiliary"):
            raise ControllerError("invalid_request", 400)
        primary = role == "primary"
        if not primary and self._primary is None:
            raise ControllerError("controller_primary_required")
        if primary and self._primary is not None:
            raise ControllerError("controller_busy", 409)
        ticket = ControllerTicket(self, grant.generation, True, session_id, primary)
        if primary:
            self._primary = ticket
        return ticket

    def capture(self) -> ControllerTicket:
        ticket = _current.get()
        if ticket is None or ticket.authority is not self:
            ticket = ControllerTicket(self, self.generation, False)
        self.validate(ticket)
        if ticket.remote and self._primary is None:
            raise ControllerError("controller_primary_required")
        return ticket

    @contextmanager
    def bind(self, ticket: ControllerTicket) -> Iterator[None]:
        self.validate(ticket)
        token = _current.set(ticket)
        try:
            yield
        finally:
            _current.reset(token)

    def validate(self, ticket: ControllerTicket) -> None:
        self._expire()
        if not (
            ticket.authority is self
            and ticket.generation == self.generation
            and (
                (not ticket.remote and self.mode == "local")
                or (
                    ticket.remote
                    and self.mode == "remote"
                    and self._state == "held"
                    and self._key is not None
                )
            )
        ):
            raise ControllerError("controller_invalid")

    def heartbeat(self, ticket: ControllerTicket) -> dict[str, object]:
        self.validate(ticket)
        if not ticket.primary or ticket is not self._primary:
            raise ControllerError("controller_primary_required")
        self._deadline = self._clock() + self.TTL_MS / 1000
        return {"type": "controller_heartbeat", "generation": self.generation}

    def add_channel(self, ticket: ControllerTicket, close: Callable[[], None]) -> None:
        self.validate(ticket)
        self._channels[ticket] = close

    def detach(self, ticket: ControllerTicket) -> None:
        self._channels.pop(ticket, None)
        if ticket is self._primary:
            self.revoke()

    async def release(self, key: object) -> None:
        self.credential(key)
        self.revoke()
        await self.settle()

    def revoke(self) -> None:
        """Fence synchronously; socket and TX cleanup stay owned until settled."""
        if self._state == "revoking" or self.mode != "remote":
            return
        if self._state == "idle" and self._key is None:
            return
        self.generation += 1
        self._key = None
        self._primary = None
        self._state = "revoking"
        channels, self._channels = self._channels, {}
        watchdog, self._watchdog = self._watchdog, None
        if watchdog is not None and watchdog is not asyncio.current_task():
            watchdog.cancel()

        async def cleanup() -> None:
            safe = False
            try:
                for close in channels.values():
                    try:
                        close()
                    except Exception:
                        pass
                await self._cleanup()
                safe = self._is_ready()
            finally:
                self._state = "idle" if safe else "blocked"

        self._revocation = asyncio.create_task(cleanup())
        self._revocation.add_done_callback(self._consume_cleanup)

    @staticmethod
    def _consume_cleanup(task: asyncio.Task[None]) -> None:
        if not task.cancelled():
            task.exception()

    async def settle(self) -> None:
        if self._revocation is not None:
            await asyncio.shield(self._revocation)

    async def _watch(self, generation: int) -> None:
        while generation == self.generation:
            await asyncio.sleep(self.HEARTBEAT_MS / 1000)
            self._expire()
