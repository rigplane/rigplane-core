"""TCP text transport for external Hamlib ``rigctld`` instances."""

from __future__ import annotations

import asyncio
import logging

from ...exceptions import CommandError
from ...exceptions import ConnectionError as RadioConnectionError
from ...exceptions import TimeoutError as RadioTimeoutError

_LOGGER = logging.getLogger(__name__)

_ERROR_HINTS = {
    -1: "invalid parameter",
    -4: "unsupported command",
    -5: "invalid configuration",
    -6: "protocol error",
    -8: "communication bus error",
}


class RigctldTransport:
    """Serialized line-oriented TCP client for external ``rigctld``."""

    def __init__(self, *, host: str, port: int = 4532, timeout: float = 5.0) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        writer = self._writer
        return writer is not None and not writer.is_closing()

    async def connect(self) -> None:
        if self.connected:
            return
        try:
            self._reader, self._writer = await asyncio.wait_for(
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

    async def close(self) -> None:
        writer = self._writer
        self._reader = None
        self._writer = None
        if writer is None:
            return
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass

    async def _drain_stale(self) -> None:
        """Discard any unread bytes left in the socket buffer from a prior
        transaction (e.g. a late/out-of-band frame the bridge injected) so the
        next command reads only its own reply."""
        reader = self._reader
        if reader is None:
            return
        while True:
            try:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=0.001)
            except (asyncio.TimeoutError, TimeoutError):
                return
            if not chunk:
                return  # EOF
            _LOGGER.debug("rigctld transport: drained %d stale bytes", len(chunk))

    async def query(self, command: str, *, response_lines: int) -> list[str]:
        """Send a command and read a fixed number of response lines."""
        if response_lines <= 0:
            raise ValueError("response_lines must be > 0")

        async with self._lock:
            await self._drain_stale()
            await self._write_line(command)
            lines: list[str] = []
            for _ in range(response_lines):
                line = await self._read_line(command)
                if line.startswith("RPRT "):
                    code = _parse_rprt(line, command)
                    if code < 0:
                        _raise_rprt(command, code)
                    raise CommandError(
                        f"External rigctld returned status {line!r} for query "
                        f"{command!r}; expected {response_lines} data line(s)."
                    )
                lines.append(line)
        return lines

    async def command(self, command: str) -> None:
        """Send a write command and require ``RPRT 0`` success."""
        async with self._lock:
            await self._drain_stale()
            await self._write_line(command)
            # Re-sync: do ONE blocking read for the server's response.
            # If it is not RPRT-shaped (stray value line that arrived in the
            # same transaction window), attempt non-blocking reads to find the
            # real RPRT that should be buffered right behind it.  We only skip
            # lines that have an immediately-buffered successor — a lone
            # malformed response (nothing else buffered) is left in `line` so
            # that _parse_rprt can raise its normal "malformed" CommandError.
            _MAX_RESYNC = 4
            line = await self._read_line(command)
            reader = self._reader
            for _ in range(_MAX_RESYNC - 1):
                if line.startswith("RPRT ") or reader is None:
                    break
                try:
                    raw = await asyncio.wait_for(reader.readline(), timeout=0.001)
                except (asyncio.TimeoutError, TimeoutError):
                    # Nothing else buffered — `line` is the actual response.
                    break
                if not raw:
                    break  # EOF
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
        if code < 0:
            _raise_rprt(command, code)

    async def _write_line(self, command: str) -> None:
        reader = self._reader
        writer = self._writer
        if reader is None or writer is None or writer.is_closing():
            raise RadioConnectionError(
                "External rigctld is not connected; call connect() first."
            )
        line = command.strip()
        if not line:
            raise CommandError("External rigctld command must be non-empty.")
        try:
            writer.write(f"{line}\n".encode("ascii"))
            await asyncio.wait_for(writer.drain(), timeout=self.timeout)
        except TimeoutError as exc:
            await self.close()
            raise RadioTimeoutError(
                f"External rigctld command {line!r} timed out while writing "
                f"after {self.timeout:.3g}s."
            ) from exc
        except (OSError, RuntimeError) as exc:
            await self.close()
            raise RadioConnectionError(
                f"Connection to external rigctld at {self.host}:{self.port} "
                f"failed while sending {line!r}: {exc}"
            ) from exc

    async def _read_line(self, command: str) -> str:
        reader = self._reader
        if reader is None:
            raise RadioConnectionError("External rigctld connection is closed.")
        try:
            raw = await asyncio.wait_for(reader.readline(), timeout=self.timeout)
        except TimeoutError as exc:
            await self.close()
            raise RadioTimeoutError(
                f"External rigctld command {command!r} timed out after "
                f"{self.timeout:.3g}s."
            ) from exc
        except OSError as exc:
            await self.close()
            raise RadioConnectionError(
                f"Connection to external rigctld at {self.host}:{self.port} "
                f"failed while reading response to {command!r}: {exc}"
            ) from exc
        if raw == b"":
            await self.close()
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
    hint = _ERROR_HINTS.get(code, "command failed")
    raise CommandError(
        f"External rigctld command {command!r} failed with RPRT {code} ({hint})."
    )
