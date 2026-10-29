"""TCI listener: one session per socket, broadcast, ping liveness (stdlib only).

Wire behavior (spec 7.4(a)): pings go out every :data:`PING_INTERVAL_S`
from a task of their own, while a separate deadline watchdog aborts a
session whose last inbound frame is older than :data:`DEADLINE_S` —
independent of whether heartbeat writes make progress. Broadcasts hold
for :data:`COALESCE_WINDOW_S` and flush back-to-back, so a slow reader
never stalls other sessions or the watchdog. Each session retains at
most :data:`MAX_QUEUED_OUTBOUND` outbound payloads: a stalled writer
that keeps inbound alive is aborted through the normal teardown
instead of growing memory.
"""

from __future__ import annotations

import asyncio
import itertools
import logging
import time
from collections.abc import Callable, Sequence
from typing import Any

from rigplane.tci.handler import CommandOutcome, TciHandler
from rigplane.tci.protocol import parse_line
from rigplane.tci.ws import (
    WS_OP_BINARY,
    WS_OP_TEXT,
    AllowHeaders,
    UpgradeDecline,
    WebSocketConnection,
    WebSocketError,
    make_frame,
    upgrade_response,
)

__all__ = [
    "CLOSE_TIMEOUT_S",
    "COALESCE_WINDOW_S",
    "DEADLINE_S",
    "MAX_QUEUED_OUTBOUND",
    "PING_INTERVAL_S",
    "STOP_TIMEOUT_S",
    "TEARDOWN_TIMEOUT_S",
    "TciServer",
    "TciSession",
]

#: Seconds between server pings, per session, from a dedicated task.
PING_INTERVAL_S = 5.0
#: Seconds of inbound silence after which a session is aborted.
DEADLINE_S = 15.0
#: Broadcasts hold this long so one flush carries the whole burst.
COALESCE_WINDOW_S = 0.03
#: Upper bound for the whole session-teardown phase of ``stop()``.
STOP_TIMEOUT_S = 5.0
#: Bound on one graceful ``close()``: past it the transport is aborted.
CLOSE_TIMEOUT_S = 2.0
#: Bound on each wait for a session's ``on_disconnect`` (two waits max).
TEARDOWN_TIMEOUT_S = 2.0
#: Bound on queued outbound payload per session: frames in the outbox
#: plus lines held for coalescing (one held line becomes one frame at
#: flush). Past it the session is aborted instead of retaining more.
MAX_QUEUED_OUTBOUND = 128
#: Largest HTTP upgrade head accepted (bytes, before the blank line).
_MAX_UPGRADE_HEAD = 16 * 1024

logger = logging.getLogger(__name__)


def _parse_http_request(head: bytes) -> tuple[str, dict[str, str]] | None:
    """Split an upgrade head into (target, lowercase headers).

    Returns ``None`` for anything that is not a ``GET`` request over
    ``HTTP/1.1`` with ``name: value`` header lines.
    """
    try:
        text = head.decode("ascii")
    except UnicodeDecodeError:
        return None
    lines = text.split("\r\n")
    request = lines[0].split(" ")
    if len(request) != 3 or request[0] != "GET" or request[2] != "HTTP/1.1":
        return None
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        name, separator, value = line.partition(":")
        if not separator or not name.strip():
            return None
        headers[name.strip().lower()] = value.strip()
    return request[1], headers


def _status_line(status: int, reason: str, body: bytes) -> bytes:
    return (
        f"HTTP/1.1 {status} {reason}\r\n"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("ascii") + body


def _retain_unsettled(
    task: asyncio.Task[Any], unsettled: set[asyncio.Task[Any]]
) -> None:
    """Keep ownership of an unfinished callback until it ends, then reap.

    A settled-but-abandoned task is referenced only by reference cycles
    (its own frame, shield leftovers): without a strong root it can be
    garbage-collected mid-finalizer, so the ``unsettled`` registry on
    the server holds it. The done-callback drops it and retrieves any
    late outcome, so nothing is ever unretrieved.
    """
    if task.done():
        _reap_session_task(task)
        return
    unsettled.add(task)

    def _done(finished: asyncio.Task[Any]) -> None:
        unsettled.discard(finished)
        _reap_session_task(finished)

    task.add_done_callback(_done)


async def _settle_disconnect_task(
    task: asyncio.Task[Any], session_id: str, unsettled: set[asyncio.Task[Any]]
) -> None:
    """Cancel the callback and observe its finalization, bounded.

    Called after the bounded waits are spent: the ``cancel()`` lets a
    callback parked in ``try/finally`` run its finalizers, and awaiting
    the task keeps ownership until that finalization is observed. The
    wait itself is bounded by :data:`TEARDOWN_TIMEOUT_S`, independent
    of child cancellation: a callback that keeps awaiting past it is
    retained in ``unsettled`` (never dropped, registry removal and
    transport release do not wait for it). A handler error stays
    absorbed (fail-soft).
    """
    task.cancel()
    try:
        async with asyncio.timeout(TEARDOWN_TIMEOUT_S):
            # Shielded: cancelling this settle (a stop/finish timeout)
            # must not propagate a second cancellation into the child
            # (an ``await task`` chains the waiter's cancellation into
            # the child's ``_fut_waiter`` and would kill the finalizer
            # it just entered). The shield keeps the wait bounded while
            # the child runs on; an unfinished child is retained below.
            await asyncio.shield(task)
    except TimeoutError:
        logger.debug("tci %s: disconnect did not settle; retaining", session_id)
        _retain_unsettled(task, unsettled)
    except asyncio.CancelledError:
        if task.done():
            # The error is the child's own outcome (it ended cancelled);
            # finalization was observed, nothing to retain.
            logger.debug("tci %s: disconnect settled as cancelled", session_id)
            return
        # This settle was cancelled while the child was still running:
        # retain it and let the cancellation continue.
        logger.debug("tci %s: settle cancelled; retaining disconnect", session_id)
        _retain_unsettled(task, unsettled)
        raise
    except Exception:
        logger.debug("tci %s: disconnect failed", session_id, exc_info=True)


async def _run_guarded_disconnect(
    handler: TciHandler, session_id: str, unsettled: set[asyncio.Task[Any]]
) -> None:
    """Run ``on_disconnect`` exactly once; survives cancellation.

    The callback task is retained and re-awaited after the first
    cancellation (the bounded handback shape of ``rigctld``): when the
    bounded re-wait itself is cancelled or times out, the callback is
    cancelled and its settlement is bounded, so ``try/finally``
    finalizers still run but never wedge the teardown. Each wait lasts
    at most :data:`TEARDOWN_TIMEOUT_S`. A handler error is absorbed
    (fail-soft). Used both for live sessions and for handlers whose
    ``on_connect`` never completed.
    """
    task = asyncio.ensure_future(handler.on_disconnect())
    try:
        try:
            await asyncio.wait_for(asyncio.shield(task), TEARDOWN_TIMEOUT_S)
        except asyncio.CancelledError:
            try:
                await asyncio.wait_for(asyncio.shield(task), TEARDOWN_TIMEOUT_S)
            except (asyncio.CancelledError, TimeoutError):
                logger.debug(
                    "tci %s: disconnect wait failed after cancel; settling",
                    session_id,
                )
                await _settle_disconnect_task(task, session_id, unsettled)
        except TimeoutError:
            logger.debug("tci %s: disconnect timed out; retrying once", session_id)
            try:
                await asyncio.wait_for(asyncio.shield(task), TEARDOWN_TIMEOUT_S)
            except (asyncio.CancelledError, TimeoutError):
                logger.debug("tci %s: disconnect unsettled; settling", session_id)
                await _settle_disconnect_task(task, session_id, unsettled)
    except Exception:
        logger.debug("tci %s: disconnect failed", session_id, exc_info=True)


def _reap_session_task(task: asyncio.Task[Any]) -> None:
    """Done-callback: retrieve a late session task so nothing is unretrieved."""
    if task.cancelled():
        return
    try:
        task.exception()
    except Exception:
        pass


async def _join_session_tasks(tasks: list[asyncio.Task[Any]], session_id: str) -> None:
    """Join session tasks, bounded independently of child cancellation.

    A cancelled callback that awaits gated cleanup forever must not wedge
    this join past :data:`TEARDOWN_TIMEOUT_S`: the graceful close and the
    guarded disconnect still run while unfinished tasks are retained and
    reaped on completion.
    """
    try:
        async with asyncio.timeout(TEARDOWN_TIMEOUT_S):
            await asyncio.gather(*tasks, return_exceptions=True)
    except TimeoutError:
        logger.debug("tci %s: task join timed out; disconnecting anyway", session_id)
        for task in tasks:
            if not task.done():
                task.add_done_callback(_reap_session_task)


class TciSession:
    """One accepted socket: reader, sender, ping, and watchdog tasks."""

    def __init__(
        self,
        session_id: str,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        handler: TciHandler,
        server: TciServer,
        clock: Callable[[], float],
    ) -> None:
        self._id = session_id
        self._reader = reader
        self._writer = writer
        self._handler = handler
        self._server = server
        self._clock = clock
        # Design 4.2: per-message deflate stays off for TCI (the upgrade
        # ignores the extension offer), so the connection never compresses.
        self._conn = WebSocketConnection(reader, writer, clock=clock)
        self._outbox: asyncio.Queue[bytes] = asyncio.Queue(maxsize=MAX_QUEUED_OUTBOUND)
        self._pending: list[str] = []
        self._flush_handle: asyncio.TimerHandle | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._done = False
        self._finished = asyncio.Event()
        self._teardown_started = False

    @property
    def session_id(self) -> str:
        """Stable per-connection id (``tci-session-N``)."""
        return self._id

    def _offer(self, frame: bytes) -> bool:
        """Enqueue one frame; ``False`` when the stalled peer overflowed.

        A full queue means the writer stopped draining while inbound
        stayed alive (so the watchdog cannot fire): the session is
        aborted and its normal teardown scheduled, and the frame is
        dropped instead of retained.
        """
        try:
            self._outbox.put_nowait(frame)
        except asyncio.QueueFull:
            self._overflow()
            return False
        return True

    def _overflow(self) -> None:
        """Abort a stalled session and schedule its normal teardown.

        Runs synchronously from any enqueue path (reader task, timer
        callback, loop-thread broadcast): the transport drops at once,
        and ``finish()`` is scheduled next tick so a sender blocked in
        ``drain`` is cancelled too. Latecomers just wait it out.
        """
        self._conn.abort()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        if not self._done:
            loop.call_soon(lambda: asyncio.create_task(self.finish()))

    def _send_now(self, line: str) -> bool:
        """Queue one reply; ``False`` when the session overflowed.

        When a coalesced flush is already scheduled, the reply queues
        behind it: a peer that just received an older broadcast line
        must never display this newer reply first.
        """
        if self._done:
            return False
        if self._pending:
            if len(self._pending) + 1 > MAX_QUEUED_OUTBOUND:
                self._overflow()
                return False
            self._pending.append(line)
            return True
        return self._offer(make_frame(WS_OP_TEXT, line.encode("utf-8")))

    def _hold(self, lines: Sequence[str]) -> None:
        """Hold broadcast lines for one coalescing window, then flush."""
        if self._done:
            return
        if len(self._pending) + len(lines) > MAX_QUEUED_OUTBOUND:
            self._overflow()
            return
        self._pending.extend(lines)
        if self._flush_handle is None:
            loop = asyncio.get_running_loop()
            self._flush_handle = loop.call_later(
                self._server.coalesce_window, self._flush
            )

    def _flush(self) -> None:
        self._flush_handle = None
        if self._done or not self._pending:
            return
        lines, self._pending = self._pending, []
        for line in lines:
            if not self._offer(make_frame(WS_OP_TEXT, line.encode("utf-8"))):
                break

    def launch(self) -> None:
        """Start reader, sender, ping, and watchdog tasks."""
        for coro in (
            self._reader_loop(),
            self._sender_loop(),
            self._ping_loop(),
            self._watchdog_loop(),
        ):
            self._tasks.add(asyncio.create_task(coro))

    async def check_deadline(self, now: float) -> bool:
        """Abort and tear down when inbound went silent; True if aborted.

        A permanently blocked ``drain`` does not stop this: ``abort()``
        never awaits, and the teardown is the same guarded path every
        finish takes.
        """
        if self._done:
            return False
        if now - self._conn.last_activity <= self._server._deadline:
            return False
        self._conn.abort()
        await self.finish()
        return True

    async def finish(self) -> None:
        """Stop every task, close the transport, tear down exactly once.

        A caller that arrives while another ``finish()`` is already
        running waits for it to complete instead of returning early:
        otherwise ``stop()`` could observe the detached transport (and
        return) before ``on_disconnect`` has run. Session tasks themselves
        never wait (the winner reaps them; waiting would deadlock the
        winner's gather against the waiter's task).

        The graceful ``close()`` is bounded (a blocked ``drain`` falls
        back to ``abort()``), and the disconnect plus registry removal
        run unconditionally: even when this ``finish()`` is cancelled
        mid-close, the session is released and latecomers observe
        completion.
        """
        current = asyncio.current_task()
        if self._done:
            if current is not None and current not in self._tasks:
                await self._finished.wait()
            return
        self._done = True
        try:
            if self._flush_handle is not None:
                self._flush_handle.cancel()
                self._flush_handle = None
            others = [task for task in self._tasks if task is not current]
            for task in others:
                task.cancel()
            if others:
                try:
                    await _join_session_tasks(others, self._id)
                finally:
                    # Transport cleanup is unconditional across
                    # cancellation during the join: a cancel landing
                    # inside ``gather`` must not skip the close/abort
                    # below (it used to jump straight to the outer
                    # ``finally``, leaving an open socket with no
                    # session owner). The shield lets the bounded
                    # close run even while this finish is being
                    # cancelled; any failure still aborts.
                    try:
                        async with asyncio.timeout(CLOSE_TIMEOUT_S):
                            await asyncio.shield(self._conn.close())
                    except (TimeoutError, OSError, asyncio.CancelledError):
                        self._conn.abort()
            else:
                try:
                    # Always close, even when a close frame already marked the
                    # connection closed: the transport still needs its own
                    # teardown. Skipping it here left the socket attached to the
                    # listener, so ``stop()`` hung in ``wait_closed()`` forever.
                    # ``close()`` sends the close frame only once and
                    # ``writer.close()`` is idempotent.
                    async with asyncio.timeout(CLOSE_TIMEOUT_S):
                        await self._conn.close()
                except (TimeoutError, OSError, asyncio.CancelledError):
                    # A close that cannot flush (or a shutdown racing it)
                    # must not wedge the teardown: drop the transport.
                    self._conn.abort()
        finally:
            try:
                await self._teardown()
            except asyncio.CancelledError:
                pass
            finally:
                self._server._drop_session(self)
                self._finished.set()

    async def _teardown(self) -> None:
        """Run ``on_disconnect`` exactly once; survives one cancellation."""
        if self._teardown_started:
            return
        self._teardown_started = True
        await _run_guarded_disconnect(
            self._handler, self._id, self._server._unsettled
        )

    async def _handle_text(self, payload: bytes) -> None:
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            logger.debug("tci %s: non-utf8 text frame; closing", self._id)
            await self.finish()
            return
        for command in parse_line(text):
            try:
                outcome = await self._handler.on_command(command.name, command.args)
            except Exception:
                logger.debug("tci %s: on_command failed; session continues", self._id)
                continue
            if not isinstance(outcome, CommandOutcome):
                logger.debug("tci %s: bad outcome type; ignored", self._id)
                continue
            sender_ok = True
            for line in outcome.to_sender:
                if not self._send_now(line):
                    sender_ok = False
                    break
            if outcome.to_others:
                # The accepted change is already applied: observers get
                # it even when this sender's own reply overflowed (its
                # teardown is already scheduled by ``_overflow``).
                self._server._fanout(outcome.to_others, exclude=self)
            if not sender_ok:
                return

    async def _reader_loop(self) -> None:
        try:
            while not self._done:
                opcode, payload = await self._conn.recv()
                if opcode == WS_OP_TEXT:
                    await self._handle_text(payload)
                elif opcode == WS_OP_BINARY:
                    try:
                        await self._handler.on_binary(bytes(payload))
                    except Exception:
                        logger.debug(
                            "tci %s: on_binary failed; session continues", self._id
                        )
        except (EOFError, ConnectionError, OSError, WebSocketError):
            # A protocol violation (e.g. a compression bit without a
            # negotiated deflate) ends the session like a reset does;
            # ``finish()`` still runs the guarded teardown exactly once.
            pass
        finally:
            await self.finish()

    async def _sender_loop(self) -> None:
        try:
            while not self._done:
                frame = await self._outbox.get()
                try:
                    self._writer.write(frame)
                    await self._writer.drain()
                except OSError:
                    break
        except Exception:
            pass
        finally:
            await self.finish()

    async def _ping_loop(self) -> None:
        try:
            while not self._done:
                await asyncio.sleep(self._server.ping_interval)
                if self._done:
                    break
                try:
                    async with asyncio.timeout(self._server.ping_interval):
                        await self._conn.send_ping()
                except Exception:
                    # One bounded write failing (a peer that briefly
                    # stops reading) must not kill the heartbeat: keep
                    # attempting within the deadline policy. The
                    # watchdog still owns liveness, so a dead session
                    # is finished from there.
                    continue
        except asyncio.CancelledError:
            pass

    async def _watchdog_loop(self) -> None:
        try:
            while not self._done:
                delay = (
                    self._conn.last_activity + self._server._deadline
                ) - self._clock()
                await asyncio.sleep(max(0.0, delay))
                if self._done:
                    break
                if await self.check_deadline(self._clock()):
                    break
        except asyncio.CancelledError:
            pass


class TciServer:
    """TCI listener: upgrade, one session per socket, broadcast fan-out.

    The Origin/Host ``allow`` predicate is injected (the adapter builds
    it); a failing predicate refuses the upgrade with its decline.
    ``broadcast`` holds lines for :data:`COALESCE_WINDOW_S` so one
    flush carries bursts together; ``broadcast_binary`` fans out at
    once. Neither ever awaits a session's drain, so a slow writer
    cannot stall other sessions or the watchdog.
    """

    def __init__(
        self,
        handler_factory: Callable[[], TciHandler],
        *,
        host: str = "127.0.0.1",
        port: int = 50001,
        allow: AllowHeaders,
        clock: Callable[[], float] = time.monotonic,
        ping_interval: float = PING_INTERVAL_S,
        coalesce_window: float = COALESCE_WINDOW_S,
        stop_timeout: float = STOP_TIMEOUT_S,
        deadline: float = DEADLINE_S,
    ) -> None:
        self._handler_factory = handler_factory
        self._host = host
        self._port = port
        self._allow = allow
        self._clock = clock
        self._ping_interval = ping_interval
        self._coalesce_window = coalesce_window
        self._stop_timeout = stop_timeout
        self._deadline = deadline
        self._listener: asyncio.Server | None = None
        self._starting = False
        self._sessions: dict[str, TciSession] = {}
        self._setup_tasks: set[asyncio.Task[Any]] = set()
        # Disconnect callbacks that outlived their bounded waits: held
        # strongly until they end (then dropped and reaped), so an
        # abandoned finalizer is never garbage-collected mid-cleanup.
        self._unsettled: set[asyncio.Task[Any]] = set()
        self._generation = 0
        self._ids = itertools.count(1)

    @property
    def ping_interval(self) -> float:
        """Seconds between server pings (defaults to PING_INTERVAL_S)."""
        return self._ping_interval

    @property
    def coalesce_window(self) -> float:
        """Broadcast hold window (defaults to COALESCE_WINDOW_S)."""
        return self._coalesce_window

    @property
    def deadline(self) -> float:
        """Inbound-silence seconds before the watchdog aborts (defaults to DEADLINE_S)."""
        return self._deadline

    @property
    def sessions(self) -> tuple[TciSession, ...]:
        """Live sessions, oldest first."""
        return tuple(self._sessions.values())

    @property
    def bound_port(self) -> int:
        """Actual bound port (``port=0`` picks an ephemeral one)."""
        if self._listener is None:
            raise RuntimeError("server is not started")
        sockets = self._listener.sockets
        if not sockets:
            raise RuntimeError("server has no sockets")
        return int(sockets[0].getsockname()[1])

    async def start(self) -> None:
        """Bind the listener; raises ``OSError`` when the port is taken.

        Lifecycle ownership covers the in-flight bind: overlapping
        ``start()`` calls are rejected, and a bind outlived by a
        concurrent ``stop()`` is closed instead of installed, with the
        invalidation reported.
        """
        if self._listener is not None or self._starting:
            raise RuntimeError("server is already started")
        self._starting = True
        start_gen = self._generation
        try:
            listener = await asyncio.start_server(
                self._accept_request, self._host, self._port
            )
        except BaseException:
            self._starting = False
            raise
        if (
            not self._starting
            or self._generation != start_gen
            or self._listener is not None
        ):
            # A concurrent stop() invalidated this bind (or an
            # overlapping start slipped in): close the orphan instead
            # of installing a listener after shutdown.
            listener.close()
            try:
                async with asyncio.timeout(self._stop_timeout):
                    await listener.wait_closed()
            except TimeoutError:
                logger.debug("tci start: orphan wait_closed timed out")
            self._starting = False
            raise RuntimeError("server was stopped during startup")
        self._listener = listener
        self._starting = False
        self._generation += 1
        sockets = self._listener.sockets or ()
        names = ", ".join(str(sock.getsockname()) for sock in sockets)
        logger.info("tci listening on %s", names)

    async def stop(self) -> None:
        """Stop accepting, finish every session, wait for release.

        Every phase is bounded by ``stop_timeout``: ``wait_closed()``
        waits for each session transport to detach from the listener,
        so sessions are finished first — waiting before finishing them
        deadlocked when a session was still open. Accepts still parked
        in the upgrade or ``on_connect`` are cancelled first, and any
        accept that outlives the stop is refused promotion, so no
        session appears after ``stop()`` returns.
        """
        self._generation += 1
        # Invalidate an in-flight bind: its result is closed, not installed.
        self._starting = False
        listener, self._listener = self._listener, None
        if listener is not None:
            listener.close()
        sessions = list(self._sessions.values())
        pending = [task for task in list(self._setup_tasks) if not task.done()]
        for task in pending:
            task.cancel()
        if sessions or pending:
            try:
                async with asyncio.timeout(self._stop_timeout):
                    await asyncio.gather(
                        *(session.finish() for session in sessions),
                        *pending,
                        return_exceptions=True,
                    )
            except TimeoutError:
                logger.debug("tci stop: teardown timed out")
        if listener is not None:
            try:
                async with asyncio.timeout(self._stop_timeout):
                    await listener.wait_closed()
            except TimeoutError:
                logger.debug("tci stop: listener wait_closed timed out")

    async def serve_forever(self) -> None:
        """Serve until cancelled; ``stop()`` still owns the teardown."""
        if self._listener is None:
            raise RuntimeError("server is not started")
        await self._listener.serve_forever()

    def broadcast(self, lines: Sequence[str]) -> None:
        """Fan broadcast lines out to every session (coalesced; loop thread)."""
        self._fanout(lines)

    def broadcast_binary(self, payload: bytes) -> None:
        """Fan one binary frame out to every session at once (loop thread)."""
        frame = make_frame(WS_OP_BINARY, bytes(payload))
        for session in list(self._sessions.values()):
            session._offer(frame)

    def _fanout(self, lines: Sequence[str], exclude: TciSession | None = None) -> None:
        for session in list(self._sessions.values()):
            if session is exclude:
                continue
            session._hold(list(lines))

    async def _refuse(
        self, writer: asyncio.StreamWriter, decline: UpgradeDecline
    ) -> None:
        logger.debug("tci: upgrade refused with %d", decline.status)
        writer.write(_status_line(decline.status, decline.reason, decline.body))
        try:
            await writer.drain()
        except OSError:
            pass
        writer.close()

    async def _open_session(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        handler: TciHandler,
    ) -> TciSession | None:
        """Create a session, send the init burst, launch its tasks.

        Returns ``None`` when ``on_connect`` fails or the server
        stopped while it ran: the transport is closed, the handler is
        cleaned up exactly once, and no session is ever promoted after
        ``stop()``.
        """
        session_id = f"tci-session-{next(self._ids)}"
        setup_gen = self._generation
        try:
            burst = await handler.on_connect()
        except asyncio.CancelledError:
            # Shutdown cancelled the pending setup: the handler started
            # but never completed, so it is cleaned up exactly once.
            writer.close()
            await self._guarded_disconnect(handler)
            return None
        except Exception:
            logger.debug("tci %s: on_connect failed", session_id)
            writer.close()
            await self._guarded_disconnect(handler)
            return None
        if self._generation != setup_gen:
            writer.close()
            await self._guarded_disconnect(handler)
            return None
        session = TciSession(session_id, reader, writer, handler, self, self._clock)
        self._sessions[session_id] = session
        for line in burst:
            session._offer(make_frame(WS_OP_TEXT, line.encode("utf-8")))
        session.launch()
        logger.info("tci %s: connected", session_id)
        return session

    async def _guarded_disconnect(self, handler: TciHandler) -> None:
        await _run_guarded_disconnect(handler, "startup", self._unsettled)

    def _drop_session(self, session: TciSession) -> None:
        self._sessions.pop(session.session_id, None)

    async def _accept_request(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        setup_gen = self._generation
        if self._listener is None and setup_gen > 0:
            # Stopped (or restarted): a connection accepted by the old
            # listener must never become a session on the new state.
            writer.close()
            return
        task = asyncio.current_task()
        if task is not None:
            self._setup_tasks.add(task)
        try:
            try:
                head = await reader.readuntil(b"\r\n\r\n")
            except (asyncio.LimitOverrunError, asyncio.IncompleteReadError):
                writer.close()
                return
            if len(head) > _MAX_UPGRADE_HEAD:
                writer.close()
                return
            parsed = _parse_http_request(head)
            if parsed is None:
                await self._refuse(
                    writer, UpgradeDecline(400, "Bad Request", b"bad request")
                )
                return
            _target, headers = parsed
            # Design 4.2: the TCI upgrade ignores the extension offer (the
            # 101 carries no Sec-WebSocket-Extensions), so no TCI frame is
            # ever compressed, even when the client offers deflate.
            result = upgrade_response(
                headers, allow=self._allow, allow_deflate=False, strict_tci=True
            )
            if isinstance(result, UpgradeDecline):
                await self._refuse(writer, result)
                return
            try:
                handler = self._handler_factory()
            except Exception:
                logger.debug("tci: handler factory failed")
                writer.close()
                return
            try:
                writer.write(result.response)
                await writer.drain()
            except OSError:
                writer.close()
                return
            if self._generation != setup_gen:
                # Stopped after the 101 went out: ``on_connect`` never
                # ran, so there is nothing to clean up — just close.
                writer.close()
                return
            await self._open_session(reader, writer, handler)
        except asyncio.CancelledError:
            # Shutdown owns the pending accept: close it and let the
            # cancellation continue.
            writer.close()
            raise
        finally:
            if task is not None:
                self._setup_tasks.discard(task)
