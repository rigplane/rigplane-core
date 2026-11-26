"""TCP text transport for external Hamlib ``rigctld`` instances."""

from __future__ import annotations

import asyncio
import logging
import math
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TypeVar

from ...core.priority_exchange import ExchangeTier, PriorityExchangeGate
from ...exceptions import CommandError
from ...exceptions import ConnectionError as RadioConnectionError
from ...exceptions import TimeoutError as RadioTimeoutError

_LOGGER = logging.getLogger(__name__)
_T = TypeVar("_T")


@dataclass(frozen=True)
class RigctldFreshRead:
    """Trusted provider evidence for one decoded physical-radio read.

    Standard rigctld has no such evidence. An optional provider must attest
    the exact command/value and captured connection identity, using this
    process's monotonic clock at physical read completion. Cached responses
    must return no proof, never a new timestamp for an old physical read.
    """

    command: str
    value: object
    connection_generation: object
    timestamp_monotonic: float


_ERROR_HINTS = {
    -1: "invalid parameter",
    -4: "unsupported command",
    -5: "invalid configuration",
    -6: "protocol error",
    -8: "communication bus error",
}


class RigctldCommandError(CommandError):
    """A nonzero ``RPRT`` result from external ``rigctld``."""

    def __init__(self, command: str, code: int) -> None:
        self.command = command
        self.code = code
        hint = _ERROR_HINTS.get(code, "command failed")
        super().__init__(
            f"External rigctld command {command!r} failed with RPRT {code} ({hint})."
        )


class RigctldTransport:
    """Serialized line-oriented TCP client for external ``rigctld``."""

    def __init__(
        self,
        *,
        host: str,
        port: int = 4532,
        timeout: float = 5.0,
        fresh_read_provenance: (
            Callable[[str, object, object], RigctldFreshRead | None] | None
        ) = None,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._exchange_gate = PriorityExchangeGate()
        self._lifecycle_lock = asyncio.Lock()
        self._provider_generation_advance: Callable[[], int] | None = None
        self._connection_retired = True
        self._fresh_read_provenance = fresh_read_provenance
        self.last_valid_frame_monotonic: float | None = None

    def bind_provider_generation(
        self, *, advance: Callable[[], int] | None = None
    ) -> None:
        """Bind the existing provider-generation lifecycle callback."""

        self._provider_generation_advance = advance

    @property
    def connected(self) -> bool:
        writer = self._writer
        return writer is not None and not writer.is_closing()

    async def connect(self) -> None:
        async with self._lifecycle_lock:
            await self._connect_locked()

    async def _connect_locked(self) -> None:
        if self.connected:
            return
        self.last_valid_frame_monotonic = None
        if self._reader is not None or self._writer is not None:
            await self._close_locked()
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=self.timeout,
            )
        except TimeoutError as exc:
            raise RadioTimeoutError(
                f"Timed out connecting to external rigctld at "
                f"{self.host}:{self.port} after {self.timeout:.3g}s."
            ) from exc
        except OSError as exc:
            raise RadioConnectionError(
                f"Failed to connect to external rigctld at "
                f"{self.host}:{self.port}: {exc}"
            ) from exc
        self._reader, self._writer = reader, writer
        self._connection_retired = False

    async def close(self) -> None:
        async with self._lifecycle_lock:
            await self._close_locked()

    async def _close_locked(self) -> None:
        writer = self._writer
        self._retire_connection(self._reader, writer)
        if writer is None:
            return
        try:
            await asyncio.shield(writer.wait_closed())
        except OSError:
            pass
        if self._writer is writer:
            self._writer = None

    def _retire_connection(
        self,
        reader: asyncio.StreamReader | None,
        writer: asyncio.StreamWriter | None,
    ) -> None:
        """Quarantine only the captured connection, retaining its close barrier."""
        if self._reader is not reader or self._writer is not writer:
            return
        self.last_valid_frame_monotonic = None
        had_connection = reader is not None or writer is not None
        self._reader = None
        if had_connection and not self._connection_retired:
            self._connection_retired = True
            if writer is not None:
                writer.close()
            advance = self._provider_generation_advance
            if advance is not None:
                try:
                    advance()
                except Exception:
                    _LOGGER.exception(
                        "external rigctld provider generation callback failed"
                    )

    async def _close_connection(
        self,
        reader: asyncio.StreamReader | None,
        writer: asyncio.StreamWriter | None,
    ) -> None:
        async with self._lifecycle_lock:
            if self._reader is reader and self._writer is writer:
                await self._close_locked()

    @asynccontextmanager
    async def _exchange(
        self, *, urgent: bool = False
    ) -> AsyncIterator[tuple[asyncio.StreamReader | None, asyncio.StreamWriter | None]]:
        tier = ExchangeTier.FORCE_RELEASE if urgent else ExchangeTier.ORDINARY
        async with self._exchange_gate.exchange(tier=tier):
            reader, writer = self._reader, self._writer
            try:
                yield reader, writer
            except asyncio.CancelledError:
                # No await: quarantine precedes release of transaction ownership.
                self._retire_connection(reader, writer)
                raise

    async def _drain_stale(
        self,
        command: str,
        reader: asyncio.StreamReader | None,
        writer: asyncio.StreamWriter | None,
    ) -> None:
        if reader is None:
            return
        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=0.001)
            except (asyncio.TimeoutError, TimeoutError):
                return
            except OSError as exc:
                await self._close_connection(reader, writer)
                raise RadioConnectionError(
                    f"Connection to external rigctld at {self.host}:{self.port} "
                    f"failed while reading response to {command!r}: {exc}"
                ) from exc
            if not chunk:
                await self._close_connection(reader, writer)
                raise RadioConnectionError(
                    f"External rigctld at {self.host}:{self.port} closed the "
                    f"connection while handling {command!r}."
                )
            _LOGGER.debug("rigctld transport: drained %d stale bytes", len(chunk))

    async def query(
        self,
        command: str,
        *,
        response_lines: int,
        is_current: Callable[[], bool] | None = None,
        urgent: bool = False,
        _on_admitted: Callable[[object, float], None] | None = None,
    ) -> list[str]:
        """Send a command and read a fixed number of response lines.

        *is_current* and *urgent* (MOR-2862) are honoured the same way
        :meth:`command` honours them: the exchange rides the urgent
        admission tier and the currency guard is checked before the
        command bytes are sent. Ordinary reads leave both at their
        defaults.
        """
        if response_lines <= 0:
            raise ValueError("response_lines must be > 0")

        entry_reader, entry_writer = self._reader, self._writer
        async with self._exchange(urgent=urgent) as (reader, writer):

            def read_is_current() -> bool:
                return (
                    reader is entry_reader
                    and writer is entry_writer
                    and self._reader is reader
                    and self._writer is writer
                    and is_current is not None
                    and is_current()
                )

            guard = read_is_current if is_current is not None else None
            self._require_write_currency(guard)
            await self._drain_stale(command, reader, writer)
            if _on_admitted is not None and writer is not None:
                _on_admitted(writer, time.monotonic())
            await self._write_line(command, reader, writer, is_current=guard)
            lines: list[str] = []
            for _ in range(response_lines):
                line = await self._read_line(command, reader, writer)
                if line.startswith("RPRT "):
                    code = _parse_rprt(line, command)
                    if code != 0:
                        _raise_rprt(command, code)
                    raise CommandError(
                        f"External rigctld returned status {line!r} for query "
                        f"{command!r}; expected {response_lines} data line(s)."
                    )
                lines.append(line)
        return lines

    async def query_decoded(
        self,
        command: str,
        *,
        response_lines: int,
        decode: Callable[[list[str]], _T],
        is_current: Callable[[], bool] | None = None,
        urgent: bool = False,
    ) -> _T:
        """Decode a query, then validate optional physical-read provenance.

        The writer identity is the existing connection-generation fence.
        Neither daemon traffic, ACKs nor successfully decoded cache hits
        imply radio liveness without independent trusted provider evidence.
        This contract does not confer managed-TX verified-readback authority.
        """
        generation: object | None = None
        started = 0.0

        def record_admission(writer: object, timestamp: float) -> None:
            nonlocal generation, started
            generation, started = writer, timestamp

        lines = await self.query(
            command,
            response_lines=response_lines,
            is_current=is_current,
            urgent=urgent,
            _on_admitted=record_admission,
        )
        value = decode(lines)
        provenance = self._fresh_read_provenance
        if provenance is None or generation is None:
            return value
        try:
            proof = provenance(command, value, generation)
            now = time.monotonic()
            previous = self.last_valid_frame_monotonic
            if (
                isinstance(proof, RigctldFreshRead)
                and self.connected
                and self._writer is generation
                and proof.connection_generation is generation
                and proof.command == command
                and type(proof.value) is type(value)
                and proof.value == value
                and math.isfinite(proof.timestamp_monotonic)
                and started <= proof.timestamp_monotonic <= now
                and (previous is None or proof.timestamp_monotonic > previous)
            ):
                self.last_valid_frame_monotonic = proof.timestamp_monotonic
        except Exception:
            # An unavailable evidence source must not break an ordinary read
            # or invent liveness. Existing accepted evidence ages naturally.
            _LOGGER.debug("rigctld fresh-read provenance unavailable", exc_info=True)
        return value

    async def command(
        self,
        command: str,
        *,
        is_current: Callable[[], bool] | None = None,
        urgent: bool = False,
    ) -> None:
        """Send a write command and require ``RPRT 0`` success."""
        entry_reader, entry_writer = self._reader, self._writer
        async with self._exchange(urgent=urgent) as (reader, writer):

            def write_is_current() -> bool:
                return (
                    reader is entry_reader
                    and writer is entry_writer
                    and self._reader is reader
                    and self._writer is writer
                    and is_current is not None
                    and is_current()
                )

            guard = write_is_current if is_current is not None else None
            self._require_write_currency(guard)
            await self._drain_stale(command, reader, writer)
            await self._write_line(command, reader, writer, is_current=guard)
            # Re-sync: do ONE blocking read for the server's response.
            # If it is not RPRT-shaped (stray value line that arrived in the
            # same transaction window), attempt non-blocking reads to find the
            # real RPRT that should be buffered right behind it.  We only skip
            # lines that have an immediately-buffered successor — a lone
            # malformed response (nothing else buffered) is left in `line` so
            # that _parse_rprt can raise its normal "malformed" CommandError.
            _MAX_RESYNC = 4
            line = await self._read_line(command, reader, writer)
            for _ in range(_MAX_RESYNC - 1):
                if line.startswith("RPRT ") or reader is None:
                    break
                try:
                    raw = await asyncio.wait_for(reader.readline(), timeout=0.001)
                except (asyncio.TimeoutError, TimeoutError):
                    # Nothing else buffered — `line` is the actual response.
                    break
                except OSError as exc:
                    await self._close_connection(reader, writer)
                    raise RadioConnectionError(
                        f"Connection to external rigctld at {self.host}:{self.port} "
                        f"failed while reading response to {command!r}: {exc}"
                    ) from exc
                if not raw:
                    await self._close_connection(reader, writer)
                    raise RadioConnectionError(
                        f"External rigctld at {self.host}:{self.port} closed the "
                        f"connection while handling {command!r}."
                    )
                _LOGGER.debug(
                    "rigctld transport: skipping non-RPRT line for %r: %r",
                    command,
                    line,
                )
                try:
                    line = raw.decode("ascii").rstrip("\r\n")
                except UnicodeDecodeError:
                    line = raw.decode("latin-1").rstrip("\r\n")

        code = _parse_rprt(line, command)
        if code != 0:
            _raise_rprt(command, code)

    async def _write_line(
        self,
        command: str,
        reader: asyncio.StreamReader | None,
        writer: asyncio.StreamWriter | None,
        *,
        is_current: Callable[[], bool] | None = None,
    ) -> None:
        if reader is None or writer is None or writer.is_closing():
            raise RadioConnectionError(
                "External rigctld is not connected; call connect() first."
            )
        line = command.strip()
        if not line:
            raise CommandError("External rigctld command must be non-empty.")
        self._require_write_currency(is_current)
        try:
            writer.write(f"{line}\n".encode("ascii"))
            await asyncio.wait_for(writer.drain(), timeout=self.timeout)
        except TimeoutError as exc:
            await self._close_connection(reader, writer)
            raise RadioTimeoutError(
                f"External rigctld command {line!r} timed out while writing "
                f"after {self.timeout:.3g}s."
            ) from exc
        except (OSError, RuntimeError) as exc:
            await self._close_connection(reader, writer)
            raise RadioConnectionError(
                f"Connection to external rigctld at {self.host}:{self.port} "
                f"failed while sending {line!r}: {exc}"
            ) from exc

    @staticmethod
    def _require_write_currency(is_current: Callable[[], bool] | None) -> None:
        if is_current is None:
            return
        try:
            current = is_current()
        except (Exception, asyncio.CancelledError) as exc:
            raise CommandError("Managed rigctld write currency check failed.") from exc
        if not current:
            raise CommandError("Managed rigctld write is no longer current.")

    async def _read_line(
        self,
        command: str,
        reader: asyncio.StreamReader | None,
        writer: asyncio.StreamWriter | None,
    ) -> str:
        if reader is None:
            raise RadioConnectionError("External rigctld connection is closed.")
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
        except TimeoutError as exc:
            await self._close_connection(reader, writer)
            raise RadioTimeoutError(
                f"External rigctld command {command!r} timed out after "
                f"{self.timeout:.3g}s."
            ) from exc
        except OSError as exc:
            await self._close_connection(reader, writer)
            raise RadioConnectionError(
                f"Connection to external rigctld at {self.host}:{self.port} "
                f"failed while reading response to {command!r}: {exc}"
            ) from exc
        if raw == b"":
            await self._close_connection(reader, writer)
            raise RadioConnectionError(
                f"External rigctld at {self.host}:{self.port} closed the "
                f"connection while handling {command!r}."
            )
        try:
            return raw.decode("ascii").rstrip("\r\n")
        except UnicodeDecodeError as exc:
            raise CommandError(
                f"External rigctld returned non-ASCII response to {command!r}."
            ) from exc


def _parse_rprt(line: str, command: str) -> int:
    parts = line.split()
    if len(parts) != 2 or parts[0] != "RPRT":
        raise CommandError(
            f"External rigctld returned malformed status for {command!r}: {line!r}."
        )
    try:
        return int(parts[1])
    except ValueError as exc:
        raise CommandError(
            f"External rigctld returned malformed status for {command!r}: {line!r}."
        ) from exc


def _raise_rprt(command: str, code: int) -> None:
    raise RigctldCommandError(command, code)
