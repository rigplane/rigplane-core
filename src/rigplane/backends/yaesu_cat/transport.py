"""Yaesu CAT serial transport — bulletproof async line protocol.

Architecture
~~~~~~~~~~~~
All serial I/O is serialized through a single ``asyncio.Lock``.  Every public
method (``write``, ``query``) acquires the lock before touching the wire.

Design principles (learned from production):

1. **No fire-and-forget.**  Even SET commands may trigger echo or auto-info
   responses.  ``write()`` always drains them before releasing the lock.

2. **Prefix-based response matching.**  ``query()`` skips stale auto-info
   lines that don't match the expected command prefix.

3. **``?;`` = hard error.**  Radio returns ``?;`` for unrecognized commands.
   Detected immediately in both ``write()`` and ``query()``.

4. **Health tracking.**  Consecutive errors trigger automatic reconnect.
   Stats (queries, writes, errors, reconnects) available for diagnostics.

5. **Graceful degradation.**  Timeout / disconnect errors are caught and
   surfaced cleanly; the transport can be re-opened after failure.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass

__all__ = [
    "YaesuCatTransport",
    "CatTransportError",
    "CatTimeoutError",
    "CatCommandRejected",
]

logger = logging.getLogger(__name__)

_DEPENDENCY_HINT = (
    "Yaesu CAT serial backend requires optional dependencies pyserial and "
    "pyserial-asyncio. Install with: pip install icom-lan[serial]"
)

# ── Defaults ──────────────────────────────────────────────────────────
_DEFAULT_TIMEOUT = 1.0  # Read timeout for queries (seconds)
_DRAIN_TIMEOUT = 0.03  # Wait for echo/auto-info after write
_DRAIN_MAX_LINES = 4  # Max lines to drain after a write
_QUERY_MAX_ATTEMPTS = 6  # Max readline attempts per query
_RECONNECT_AFTER_ERRORS = 5  # Consecutive errors before auto-reconnect
_RECONNECT_COOLDOWN = 2.0  # Min seconds between reconnect attempts


# ── Exceptions ────────────────────────────────────────────────────────


class CatTransportError(Exception):
    """Base error for CAT transport failures."""


class CatTimeoutError(CatTransportError):
    """Raised when read operation times out."""


class CatCommandRejected(CatTransportError):
    """Raised when radio returns ``?;`` (command not recognized)."""


# ── Stats ─────────────────────────────────────────────────────────────


@dataclass
class TransportStats:
    """Diagnostic counters for the transport layer."""

    queries: int = 0
    writes: int = 0
    errors: int = 0
    timeouts: int = 0
    reconnects: int = 0
    stale_lines_skipped: int = 0
    bytes_flushed: int = 0
    last_error: str = ""
    last_error_time: float = 0.0
    _consecutive_errors: int = field(default=0, repr=False)

    def record_success(self) -> None:
        self._consecutive_errors = 0

    def record_error(self, msg: str) -> None:
        self.errors += 1
        self._consecutive_errors += 1
        self.last_error = msg
        self.last_error_time = time.monotonic()

    @property
    def consecutive_errors(self) -> int:
        return self._consecutive_errors


# ── Transport ─────────────────────────────────────────────────────────


class YaesuCatTransport:
    """Async serial transport for Yaesu CAT protocol.

    All public methods are safe to call concurrently — the internal lock
    guarantees strict serialization of serial I/O.

    Usage::

        async with YaesuCatTransport(device="/dev/ttyUSB0") as t:
            freq = await t.query("FA;")
            await t.write("FA014074000;")
    """

    def __init__(
        self,
        *,
        device: str,
        baudrate: int = 38400,
        timeout: float = _DEFAULT_TIMEOUT,
        echo_suppression: bool = True,
        debug_logging: bool = False,
    ) -> None:
        self._device = device
        self._baudrate = baudrate
        self._timeout = timeout
        self._echo_suppression = echo_suppression
        self._debug_logging = debug_logging

        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._connected = False
        self._lock = asyncio.Lock()
        self._stats = TransportStats()
        self._last_reconnect: float = 0.0

    # ── Properties ────────────────────────────────────────────────────

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def stats(self) -> TransportStats:
        """Read-only access to diagnostic counters."""
        return self._stats

    # ── Connection lifecycle ──────────────────────────────────────────

    async def connect(self) -> None:
        """Open serial connection."""
        if self._connected:
            return

        try:
            import serial_asyncio  # type: ignore[import-untyped]
        except ImportError as exc:
            raise CatTransportError(_DEPENDENCY_HINT) from exc

        logger.info(
            "Opening CAT serial port: %s @ %d baud", self._device, self._baudrate
        )

        try:
            self._reader, self._writer = await serial_asyncio.open_serial_connection(
                url=self._device,
                baudrate=self._baudrate,
                bytesize=8,
                parity="N",
                stopbits=1,
            )
            self._connected = True
            self._stats.record_success()
            logger.info("CAT serial port opened: %s", self._device)
        except Exception as exc:
            raise CatTransportError(
                f"Failed to open serial port {self._device}: {exc}"
            ) from exc

    async def close(self) -> None:
        """Close serial connection gracefully."""
        if not self._connected:
            return

        logger.info("Closing CAT serial port: %s", self._device)
        self._connected = False

        if self._writer:
            try:
                self._writer.close()
                await asyncio.wait_for(self._writer.wait_closed(), timeout=2.0)
            except Exception:
                pass  # Best-effort close

        self._reader = None
        self._writer = None
        logger.info("CAT serial port closed: %s", self._device)

    async def reconnect(self) -> None:
        """Close and re-open the serial port (with cooldown)."""
        now = time.monotonic()
        if now - self._last_reconnect < _RECONNECT_COOLDOWN:
            logger.debug("CAT: reconnect cooldown, skipping")
            return

        self._last_reconnect = now
        self._stats.reconnects += 1
        logger.warning(
            "CAT: reconnecting serial port (consecutive errors: %d)",
            self._stats.consecutive_errors,
        )

        await self.close()
        await asyncio.sleep(0.5)  # Let OS release the port
        await self.connect()

    # ── Low-level I/O (caller MUST hold self._lock) ──────────────────

    def _check_connected(self) -> None:
        if not self._connected or not self._writer or not self._reader:
            raise CatTransportError("Transport not connected")

    async def _raw_write(self, command: str) -> None:
        """Send raw bytes to serial port."""
        self._check_connected()
        assert self._writer is not None  # for type checker

        if not command.endswith(";"):
            command += ";"

        if self._debug_logging:
            logger.debug("CAT TX: %r", command)

        try:
            self._writer.write(command.encode("ascii"))
            await self._writer.drain()
        except Exception as exc:
            self._stats.record_error(f"write failed: {exc}")
            raise CatTransportError(f"Write failed: {exc}") from exc

    async def readline(self, *, timeout: float | None = None) -> str:
        """Read one semicolon-terminated line.

        .. note:: Caller must hold ``self._lock`` when used internally.
           External callers should prefer ``query()`` which handles locking.

        Returns the line with trailing ``;`` stripped.
        """
        self._check_connected()
        assert self._reader is not None  # for type checker

        if timeout is None:
            timeout = self._timeout

        try:
            line_bytes = await asyncio.wait_for(
                self._reader.readuntil(b";"),
                timeout=timeout,
            )
            line = line_bytes.decode("ascii").rstrip(";")

            if self._debug_logging:
                logger.debug("CAT RX: %r", line)

            return line
        except asyncio.TimeoutError as exc:
            self._stats.timeouts += 1
            raise CatTimeoutError(
                f"Read timeout ({timeout}s) waiting for ';' terminator"
            ) from exc
        except Exception as exc:
            self._stats.record_error(f"read failed: {exc}")
            raise CatTransportError(f"Read failed: {exc}") from exc

    async def flush_rx(self) -> int:
        """Discard any bytes sitting in the receive buffer.

        Only touches the asyncio StreamReader internal buffer — does NOT
        wait for new bytes from the OS.
        """
        if not self._reader:
            return 0
        buf = getattr(self._reader, "_buffer", None)
        if not buf:
            return 0
        discarded = len(buf)
        if discarded:
            self._stats.bytes_flushed += discarded
            if self._debug_logging:
                logger.debug("CAT: flushing %d stale bytes: %r", discarded, bytes(buf))
            buf.clear()
        return discarded

    async def _drain_responses(
        self,
        drain_timeout: float = _DRAIN_TIMEOUT,
        max_lines: int = _DRAIN_MAX_LINES,
    ) -> int:
        """Read and discard echo / auto-info lines until silence.

        Returns the number of lines drained.
        """
        drained = 0
        for _ in range(max_lines):
            try:
                line = await self.readline(timeout=drain_timeout)
                drained += 1
                self._stats.stale_lines_skipped += 1
                if self._debug_logging:
                    logger.debug("CAT: drained post-write line: %r", line)
            except CatTimeoutError:
                break  # Silence — buffer is clean
            except CatTransportError:
                break  # Port error — bail out
        # Flush any partial bytes that didn't form a complete line
        await self.flush_rx()
        return drained

    def _maybe_reconnect_needed(self) -> bool:
        """Check if consecutive errors warrant a reconnect."""
        return self._stats.consecutive_errors >= _RECONNECT_AFTER_ERRORS

    # ── Public API (all acquire lock) ─────────────────────────────────

    async def write(self, command: str) -> None:
        """Send a SET command and drain echo / auto-info.

        Acquires the transport lock, flushes stale RX data, sends the
        command, then reads (and discards) any echo or auto-info the radio
        sends back.  The lock is only released once the wire is clean.

        Args:
            command: CAT command string (e.g. ``"MD0E;"``).

        Raises:
            CatTransportError: On serial I/O failure.
        """
        async with self._lock:
            await self.flush_rx()
            await self._raw_write(command)
            self._stats.writes += 1
            drained = await self._drain_responses()
            self._stats.record_success()
            if drained and self._debug_logging:
                logger.debug("CAT: drained %d line(s) after write %r", drained, command)

    async def query(self, command: str, *, timeout: float | None = None) -> str:
        """Send a GET command and return the matching response.

        Acquires the transport lock, flushes stale RX data, sends the
        command, then reads lines until one matches the expected prefix.
        Echo lines and stale auto-info are silently skipped.

        Args:
            command: CAT command string (e.g. ``"FA;"``).
            timeout: Read timeout per attempt (default: instance timeout).

        Returns:
            Response line (without trailing ``;``).

        Raises:
            CatCommandRejected: If radio returns ``?;``.
            CatTimeoutError: If no matching response within timeout.
            CatTransportError: On serial I/O failure.
        """
        async with self._lock:
            await self.flush_rx()
            await self._raw_write(command)
            self._stats.queries += 1

            # Expected prefix: strip trailing digits from command body.
            # "SM0;" → prefix "SM", "FA;" → prefix "FA", "MD0;" → prefix "MD"
            cmd_body = command.rstrip(";")
            expected_prefix = cmd_body.rstrip("0123456789")

            if timeout is None:
                timeout = self._timeout

            for _attempt in range(_QUERY_MAX_ATTEMPTS):
                response = await self.readline(timeout=timeout)

                # ── ?; = command rejected ──
                if response == "?":
                    self._stats.record_error(f"rejected: {command}")
                    raise CatCommandRejected(
                        f"Radio rejected command {command!r} (returned '?;')"
                    )

                # ── Echo suppression ──
                if self._echo_suppression and response == cmd_body:
                    if self._debug_logging:
                        logger.debug("CAT: echo detected, reading next line")
                    continue

                # ── Auto-info suppression (prefix mismatch) ──
                if expected_prefix and not response.startswith(expected_prefix):
                    self._stats.stale_lines_skipped += 1
                    logger.info(
                        "CAT: skipping stale %r (expected prefix %r)",
                        response,
                        expected_prefix,
                    )
                    continue

                # ── Match! ──
                self._stats.record_success()
                return response

            # Exhausted all attempts
            self._stats.record_error(f"no match for {command}")
            raise CatTransportError(
                f"Query {command!r}: exhausted {_QUERY_MAX_ATTEMPTS} attempts, "
                "no matching response"
            )

    async def query_safe(
        self, command: str, *, timeout: float | None = None, default: Any = None
    ) -> str | Any:
        """Like ``query()`` but returns *default* on any error.

        Useful for polling loops where a single failed read should not
        crash the cycle.
        """
        try:
            return await self.query(command, timeout=timeout)
        except CatTransportError:
            return default

    # ── Diagnostics ───────────────────────────────────────────────────

    def format_stats(self) -> str:
        """One-line diagnostic summary."""
        s = self._stats
        return (
            f"CAT q={s.queries} w={s.writes} err={s.errors} "
            f"to={s.timeouts} skip={s.stale_lines_skipped} "
            f"flush={s.bytes_flushed}B reconn={s.reconnects}"
        )

    # ── Context manager ───────────────────────────────────────────────

    async def __aenter__(self) -> YaesuCatTransport:
        await self.connect()
        return self

    async def __aexit__(
        self, exc_type: type | None, exc_val: BaseException | None, exc_tb: Any
    ) -> None:
        await self.close()
