"""Ephemeral controller currency beside the existing managed TX authority."""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Literal

logger = logging.getLogger(__name__)


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
    # Managed-TX owner of the multi-client session this ticket belongs to;
    # ``None`` for the exclusive grant and for sessionless clients.
    tx_owner: str | None = None


@dataclass(slots=True)
class _Session:
    """One multi-client session: correlation for TX ownership, not identity."""

    key: str
    deadline: float
    primary: ControllerTicket | None = None
    channels: dict[ControllerTicket, Callable[[], None]] = field(default_factory=dict)


_current: ContextVar[ControllerTicket | None] = ContextVar("controller", default=None)


class ControllerAuthority:
    """Controller currency in two modes.

    ``local`` (the default) is multi-session: every ``acquire`` opens its own
    session, keyed by a random correlation key (anyone may obtain one; it
    authenticates nobody). A session's expiry or primary loss fences that
    session only: its channels close and ``release_session`` releases the
    transmitter it holds. Sessionless local clients keep their old semantics.
    ``remote`` is the exclusive single-grant mode.
    """

    TTL_MS = 6000
    HEARTBEAT_MS = 2000

    def __init__(
        self,
        *,
        ready: Callable[[], bool],
        cleanup: Callable[[], Awaitable[None]],
        clock: Callable[[], float] = time.monotonic,
        tx_ready: Callable[[], bool] = lambda: True,
        release_session: Callable[[str], Awaitable[object]] | None = None,
    ) -> None:
        self._ready = ready
        self._cleanup = cleanup
        self._clock = clock
        self._tx_ready = tx_ready
        self._release_session = release_session
        self._sessions: dict[str, _Session] = {}
        self._session_watchdog: asyncio.Task[None] | None = None
        self._session_fences: set[asyncio.Future[object]] = set()
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
        self._controls: dict[str, ControllerTicket] = {}
        self._audio: tuple[ControllerTicket, object, str | None] | None = None
        self._audio_revoking = False

    def _expire(self) -> None:
        if self._key is not None and self._clock() >= self._deadline:
            self.revoke()
        now = self._clock()
        for owner in [o for o, s in self._sessions.items() if now >= s.deadline]:
            self._end_session(owner)

    def _end_session(self, owner: str) -> None:
        """Fence one session synchronously, then release the TX it holds."""
        session = self._sessions.pop(owner, None)
        if session is None:
            return
        watchdog = self._session_watchdog
        if (
            not self._sessions
            and watchdog is not None
            and watchdog is not asyncio.current_task()
        ):
            self._session_watchdog = None
            watchdog.cancel()
        for close in tuple(session.channels.values()):
            try:
                close()
            except Exception:
                pass
        if self._release_session is not None:
            fence = asyncio.ensure_future(self._release_session(owner))
            self._session_fences.add(fence)
            fence.add_done_callback(self._session_fences.discard)
            fence.add_done_callback(self._log_failed_release)

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
        for owner in tuple(self._sessions):
            self._end_session(owner)
        self.generation += 1
        self.mode = mode
        self._state = "idle"

    def acquire(self, kind: str) -> dict[str, object]:
        self._expire()
        if kind not in ("browser", "pro"):
            raise ControllerError("invalid_request", 400)
        if self.mode == "local":
            return self._open_session()
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

    def _open_session(self) -> dict[str, object]:
        # Not sequential: an owner is never accepted from a client, and a
        # guessable one would invite trying.
        owner = f"controller-session-{secrets.token_hex(8)}"
        key = secrets.token_hex(32)
        self._sessions[owner] = _Session(key, self._clock() + self.TTL_MS / 1000)
        if self._session_watchdog is None or self._session_watchdog.done():
            self._session_watchdog = asyncio.create_task(self._watch_sessions())
        return {
            "controller_key": key,
            "generation": self.generation,
            "ttl_ms": self.TTL_MS,
            "heartbeat_ms": self.HEARTBEAT_MS,
        }

    def credential(self, key: object) -> ControllerTicket:
        self._expire()
        if not (type(key) is str and len(key) == 64 and key.isascii()):
            raise ControllerError("controller_invalid")
        if self.mode == "local":
            for owner, session in self._sessions.items():
                if secrets.compare_digest(key, session.key):
                    return ControllerTicket(
                        self, self.generation, False, tx_owner=owner
                    )
            raise ControllerError("controller_invalid")
        if not (
            self._key is not None
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
        session = None if grant.tx_owner is None else self._sessions[grant.tx_owner]
        current = self._primary if session is None else session.primary
        if not primary and current is None:
            raise ControllerError("controller_primary_required")
        if primary and current is not None:
            raise ControllerError("controller_busy", 409)
        ticket = ControllerTicket(
            self, grant.generation, grant.remote, session_id, primary, grant.tx_owner
        )
        if primary:
            if session is None:
                self._primary = ticket
            else:
                session.primary = ticket
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
                (
                    not ticket.remote
                    and self.mode == "local"
                    and (ticket.tx_owner is None or ticket.tx_owner in self._sessions)
                )
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
        session = None if ticket.tx_owner is None else self._sessions[ticket.tx_owner]
        primary = self._primary if session is None else session.primary
        if not ticket.primary or ticket is not primary:
            raise ControllerError("controller_primary_required")
        deadline = self._clock() + self.TTL_MS / 1000
        if session is None:
            self._deadline = deadline
        else:
            session.deadline = deadline
        return {"type": "controller_heartbeat", "generation": self.generation}

    def validate_positive(self, ticket: ControllerTicket) -> None:
        self.validate(ticket)
        if ticket.remote:
            try:
                ready = not self._audio_revoking and self._tx_ready() is True
            except Exception:
                ready = False
            if not ready:
                raise ControllerError("controller_not_ready", 503)

    def add_channel(self, ticket: ControllerTicket, close: Callable[[], None]) -> None:
        self.validate(ticket)
        if ticket.tx_owner is None:
            self._channels[ticket] = close
        else:
            self._sessions[ticket.tx_owner].channels[ticket] = close

    def register_control(self, ticket: ControllerTicket) -> None:
        self.validate(ticket)
        if ticket.session_id is None:
            raise ControllerError("controller_invalid")
        self._controls[ticket.session_id] = ticket

    def claim_audio(self, ticket: ControllerTicket, source: object) -> None:
        self.validate(ticket)
        if self._audio is not None and self._audio[1] is not source:
            raise ControllerError("controller_audio_busy", 409)
        if self._audio_revoking:
            raise ControllerError("controller_audio_busy", 409)
        self._audio = (ticket, source, None)

    def audio_matches(
        self, ticket: ControllerTicket, source: object, owner: str
    ) -> bool:
        self.validate(ticket)
        control = self._controls.get(owner)
        audio = self._audio
        if (
            self._audio_revoking
            or control is None
            or audio is None
            or audio[:2] != (ticket, source)
        ):
            return False
        self.validate(control)
        if audio[2] is not None and audio[2] != owner:
            return False
        self._audio = (ticket, source, owner)
        return True

    def fence_audio(self, ticket: ControllerTicket, source: object) -> bool:
        if (
            self._audio_revoking
            or self._audio is None
            or self._audio[:2] != (ticket, source)
        ):
            return False
        self._audio_revoking = True
        try:
            self.validate(ticket)
        except ControllerError:
            return False
        return True

    def release_audio(self, ticket: ControllerTicket, source: object) -> bool:
        audio = self._audio
        if audio is None or audio[:2] != (ticket, source):
            return False
        self._audio = None
        self._audio_revoking = False
        try:
            self.validate(ticket)
        except ControllerError:
            return False
        return True

    def detach(self, ticket: ControllerTicket) -> None:
        self._channels.pop(ticket, None)
        if ticket.tx_owner is not None:
            session = self._sessions.get(ticket.tx_owner)
            if session is not None:
                session.channels.pop(ticket, None)
                if ticket is session.primary:
                    self._end_session(ticket.tx_owner)
            return
        if self._controls.get(ticket.session_id) is ticket:
            self._controls.pop(ticket.session_id)
        if ticket is self._primary:
            self.revoke()

    def disconnect_control(self, session_id: str) -> None:
        ticket = self._controls.get(session_id)
        if ticket is not None:
            self.detach(ticket)

    async def release(self, key: object) -> None:
        ticket = self.credential(key)
        if ticket.tx_owner is not None:
            self._end_session(ticket.tx_owner)
        else:
            self.revoke()
        await self.settle()

    def revoke(self) -> None:
        """Fence synchronously; socket and TX cleanup stay owned until settled."""
        if self.mode == "local":
            self.generation += 1
            for owner in tuple(self._sessions):
                self._end_session(owner)
            return
        if self._state == "revoking":
            return
        if self._state == "idle" and self._key is None:
            return
        self.generation += 1
        self._key = None
        self._primary = None
        self._controls.clear()
        self._audio = None
        self._audio_revoking = False
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
    def _log_failed_release(fence: asyncio.Future[Any]) -> None:
        if not fence.cancelled() and (error := fence.exception()) is not None:
            logger.warning("controller session TX release failed", exc_info=error)

    @staticmethod
    def _consume_cleanup(task: asyncio.Future[Any]) -> None:
        if not task.cancelled():
            task.exception()

    async def settle(self) -> None:
        if self._revocation is not None:
            await asyncio.shield(self._revocation)
        if self._session_fences:
            await asyncio.gather(
                *(asyncio.shield(fence) for fence in tuple(self._session_fences)),
                return_exceptions=True,
            )

    async def _watch(self, generation: int) -> None:
        while generation == self.generation:
            await asyncio.sleep(self.HEARTBEAT_MS / 1000)
            self._expire()

    async def _watch_sessions(self) -> None:
        while self._sessions:
            await asyncio.sleep(self.HEARTBEAT_MS / 1000)
            self._expire()
