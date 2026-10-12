"""Serial CI-V bridge recorder: a TCP↔serial pump that logs JSONL.

The recorder opens the real USB CI-V device through
``rigplane.core.serial_open.open_serial_port`` — the production open,
which deasserts DTR/RTS (on a radio whose USB SEND key input is wired to
a modem line, an asserted line keys the transmitter) — and serves it on
a TCP listener. A client pointed at ``socket://127.0.0.1:<port>`` reaches
the device through pyserial's socket URL handler, the same open path the
Yaesu CAT fake proves reachable (``rigplane_testkit/yaesu/cat_fake.py``),
with zero production change.

Every chunk pumped in either direction is appended as one JSON line with
the same keys the LAN recorder writes (``IcomSessionRecorder._record``)::

    {"t": 1.234, "dir": "c2r", "port": "serial", "hex": "..."}

``t`` is seconds (``time.monotonic``) since :meth:`SerialCivRecorder.start`
and the file is flushed after every line. CI-V serial traffic carries no
credentials, so no ``redacted`` key exists. One TCP client is served at a
time; a second concurrent connection is closed immediately and counted in
``rejected_connections``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any

from rigplane.core.serial_open import open_serial_port

logger = logging.getLogger(__name__)

# Read size for both pump directions; a chunk larger than this lands in the
# JSONL as several lines, one per read.
_CHUNK = 4096

# CI-V frame envelope: preamble FE FE, terminator FD — the same split rule
# the serial fake's inbound splitter documents (``civ_serial.py``:
# split on FE FE, take to the first FD).
_PREAMBLE = b"\xfe\xfe"
_TERMINATOR = b"\xfd"


def civ_frames(path: str | Path) -> Iterator[tuple[float, str, bytes]]:
    """Yield ``(t, direction, frame)`` for every complete CI-V frame in a JSONL.

    Pure helper over a recorder file: frames split across chunks are
    re-assembled and chunks holding several frames are split, per
    direction; bytes before the first ``FE FE`` of a direction are
    discarded. Each frame carries the ``t`` of the chunk that completed it.
    """
    buffers: dict[str, bytearray] = {}
    with Path(path).open(encoding="utf-8") as file:
        for line in file:
            record = json.loads(line)
            direction = str(record["dir"])
            t = float(record["t"])
            buf = buffers.setdefault(direction, bytearray())
            buf += bytes.fromhex(str(record["hex"]))
            while True:
                start = buf.find(_PREAMBLE)
                if start < 0:
                    # No preamble: keep a trailing FE — the next chunk's
                    # first byte may complete the preamble — drop the rest.
                    if buf and buf[-1] == _PREAMBLE[0]:
                        del buf[:-1]
                    else:
                        buf.clear()
                    break
                end = buf.find(_TERMINATOR, start + 2)
                if end < 0:
                    del buf[:start]
                    break
                yield t, direction, bytes(buf[start : end + 1])
                del buf[: end + 1]


class SerialCivRecorder:
    """TCP↔serial bridge that records a USB CI-V session to a JSONL file.

    Args:
        device: Serial device path (e.g. ``/dev/ttyUSB0``).
        out_path: Destination JSONL file (truncated on :meth:`start`, after
            the device open succeeds).
        baudrate: Device baud rate.
        listen_host: Bind address for the client-facing TCP listener.
        listen_port: Bind port (0 = ephemeral).
        opener: Replacement for the real serial open step; tests mount
            :class:`rigplane_testkit.serial.CivSerialFake` via ``fake.opener``.
    """

    def __init__(
        self,
        device: str,
        out_path: str | Path,
        *,
        baudrate: int = 115200,
        listen_host: str = "127.0.0.1",
        listen_port: int = 0,
        opener: Any = None,
    ) -> None:
        self._device = device
        self._baudrate = baudrate
        self._out_path = Path(out_path)
        self._listen_host = listen_host
        self._listen_port = listen_port
        self._opener = opener
        self._file: IO[str] | None = None
        self._t0 = 0.0
        self.records_written = 0
        self.rejected_connections = 0
        self._server: asyncio.Server | None = None
        self._serial_reader: asyncio.StreamReader | None = None
        self._serial_writer: Any = None
        self._serial_pump: asyncio.Task[None] | None = None
        self._client_writer: asyncio.StreamWriter | None = None
        self._client_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Open the device, then the log and the TCP listener.

        The recording file truncates only after the device open succeeds,
        so a failed open leaves any previous recording untouched.
        """
        self._t0 = time.monotonic()
        self._serial_reader, self._serial_writer = await open_serial_port(
            self._device, baudrate=self._baudrate, opener=self._opener
        )
        self._file = self._out_path.open("w", encoding="utf-8")
        self._server = await asyncio.start_server(
            self._handle_client, self._listen_host, self._listen_port
        )
        self._serial_pump = asyncio.get_running_loop().create_task(
            self._pump_serial_to_client()
        )
        logger.info(
            "recording bridge socket://%s:%d -> %s, writing %s",
            self._listen_host,
            self.listen_port,
            self._device,
            self._out_path,
        )

    async def stop(self) -> None:
        """Close the client, listener, device and the JSONL file."""
        for task in (self._serial_pump, self._client_task):
            if task is not None and not task.done():
                task.cancel()
        for task in (self._serial_pump, self._client_task):
            if task is not None:
                with contextlib.suppress(asyncio.CancelledError, OSError):
                    await task
        self._serial_pump = None
        self._client_task = None
        if self._client_writer is not None:
            self._client_writer.close()
            self._client_writer = None
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        if self._serial_writer is not None:
            self._serial_writer.close()
            with contextlib.suppress(OSError):
                await self._serial_writer.wait_closed()
            self._serial_writer = None
        self._serial_reader = None
        if self._file is not None:
            self._file.close()
            self._file = None

    async def serve_forever(self) -> None:
        """Run until the serial pump ends or this is cancelled (CLI use).

        When the serial→client pump ends — an ``OSError`` or end of stream
        from the device — the bridge is stopped and this returns, so the
        CLI exits instead of serving a dead device.
        """
        pump = self._serial_pump
        if pump is None:
            return
        await asyncio.wait({pump})
        await self.stop()

    @property
    def listen_port(self) -> int:
        """Client-facing TCP port (0 while not listening)."""
        if self._server is None or not self._server.sockets:
            return 0
        return int(self._server.sockets[0].getsockname()[1])

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        current = self._client_writer
        if current is not None and not current.is_closing():
            self.rejected_connections += 1
            writer.close()
            return
        self._client_writer = writer
        self._client_task = asyncio.current_task()
        try:
            while True:
                data = await reader.read(_CHUNK)
                if not data:
                    break
                self._record("c2r", data)
                self._serial_writer.write(data)
                await self._serial_writer.drain()
        except OSError as error:
            logger.warning("client→serial pump ended: %s", error)
        finally:
            writer.close()
            if self._client_writer is writer:
                self._client_writer = None
            self._client_task = None

    async def _pump_serial_to_client(self) -> None:
        try:
            while True:
                data = await self._serial_reader.read(_CHUNK)
                if not data:
                    logger.warning(
                        "serial→client pump ended: end of stream from %s",
                        self._device,
                    )
                    break
                self._record("r2c", data)
                client = self._client_writer
                if client is not None and not client.is_closing():
                    client.write(data)
                    await client.drain()
        except OSError as error:
            logger.warning("serial→client pump ended: %s", error)

    def _record(self, direction: str, data: bytes) -> None:
        record: dict[str, object] = {
            "t": round(time.monotonic() - self._t0, 6),
            "dir": direction,
            "port": "serial",
            "hex": data.hex(),
        }
        if self._file is not None:
            self._file.write(json.dumps(record) + "\n")
            self._file.flush()
            self.records_written += 1


__all__ = ["SerialCivRecorder", "civ_frames"]
