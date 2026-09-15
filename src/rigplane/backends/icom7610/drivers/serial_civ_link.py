"""Production Serial CI-V link for IC-7610 USB serial backend."""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from ....core.exceptions import CommandError

logger = logging.getLogger(__name__)

_SOF = b"\xfe\xfe"
_EOF = 0xFD
_ABORT = 0xFC


class SerialFrameError(RuntimeError):
    """Base error for serial frame parse failures."""


class SerialFrameTimeoutError(SerialFrameError):
    """Raised when an incomplete frame exceeded the partial timeout."""


class SerialFrameOverflowError(SerialFrameError):
    """Raised when an incomplete frame exceeded max_frame_len."""


class SerialFrameCodec:
    """CI-V FE FE ... FD frame codec with malformed-stream recovery."""

    def __init__(
        self, *, max_frame_len: int = 1024, frame_timeout_s: float = 0.2
    ) -> None:
        if max_frame_len < 4:
            raise ValueError("max_frame_len must be >= 4")
        if frame_timeout_s <= 0:
            raise ValueError("frame_timeout_s must be > 0")
        self._max_frame_len = max_frame_len
        self._frame_timeout_s = frame_timeout_s
        self._buffer = bytearray()
        self._partial_since: float | None = None

    def encode(self, payload: bytes) -> bytes:
        """Encode payload as CI-V frame unless already framed."""
        if payload.startswith(_SOF) and payload.endswith(bytes([_EOF])):
            return payload
        return _SOF + payload + bytes([_EOF])

    def feed(self, data: bytes) -> list[bytes]:
        """Feed stream bytes and return complete CI-V frames."""
        if not data:
            return []
        self._buffer.extend(data)
        frames: list[bytes] = []

        while True:
            start = self._buffer.find(_SOF)
            if start < 0:
                # Keep one trailing FE to support split SOF across chunks.
                if self._buffer and self._buffer[-1] == _SOF[0]:
                    self._buffer[:] = self._buffer[-1:]
                else:
                    self._buffer.clear()
                    self._partial_since = None
                break

            if start > 0:
                del self._buffer[:start]

            end = self._buffer.find(bytes([_EOF]), len(_SOF))
            abort = self._buffer.find(bytes([_ABORT]), len(_SOF))
            nested_start = self._buffer.find(_SOF, len(_SOF))

            if abort >= 0 and (end < 0 or abort < end):
                logger.debug("Dropping aborted/collision frame candidate.")
                del self._buffer[: abort + 1]
                self._partial_since = None
                continue

            if nested_start >= 0 and (end < 0 or nested_start < end):
                # Current frame is malformed/truncated; resync on the newer SOF.
                logger.debug(
                    "Malformed frame candidate; resynchronizing on nested SOF."
                )
                del self._buffer[:nested_start]
                self._partial_since = None
                continue

            if end < 0:
                if len(self._buffer) > self._max_frame_len:
                    self._buffer.clear()
                    self._partial_since = None
                    raise SerialFrameOverflowError(
                        "Partial serial frame exceeded max_frame_len."
                    )
                if self._partial_since is None:
                    self._partial_since = time.monotonic()
                break

            frame = bytes(self._buffer[: end + 1])
            del self._buffer[: end + 1]
            self._partial_since = None

            if len(frame) > self._max_frame_len:
                logger.warning("Dropping oversized complete frame: len=%d", len(frame))
                continue
            if _ABORT in frame[len(_SOF) : -1]:
                logger.debug("Dropping frame containing abort/collision byte.")
                continue

            frames.append(frame)

        return frames

    def expire_partial(self, *, now: float | None = None) -> bool:
        """Expire partial frame if timeout elapsed; return True on expiration."""
        if self._partial_since is None:
            return False
        timestamp = time.monotonic() if now is None else now
        if (timestamp - self._partial_since) <= self._frame_timeout_s:
            return False
        self._buffer.clear()
        self._partial_since = None
        return True

    def reset(self) -> None:
        """Reset buffered parser state."""
        self._buffer.clear()
        self._partial_since = None


@dataclass(slots=True)
class _WriteRequest:
    payload: bytes
    queue: asyncio.Queue[bytes | _WriteRequest | None]
    writer: Any
    result: asyncio.Future[None]
    is_current: Callable[[], bool] | None = None
    started: bool = False


class SerialCivLink:
    """Async serial CI-V link with framing, writer serialization, and health flags."""

    def __init__(
        self,
        *,
        device: str,
        baudrate: int = 115200,
        read_chunk_size: int = 512,
        connect_timeout_s: float = 5.0,
        codec: SerialFrameCodec | None = None,
        max_write_queue: int = 64,
        open_serial_connection: Callable[[], Awaitable[tuple[Any, Any]]] | None = None,
        require_optional_deps: bool = False,
    ) -> None:
        if not device.strip():
            raise ValueError("device must be non-empty")
        if baudrate <= 0:
            raise ValueError("baudrate must be > 0")
        if read_chunk_size <= 0:
            raise ValueError("read_chunk_size must be > 0")
        if connect_timeout_s <= 0:
            raise ValueError("connect_timeout_s must be > 0")
        if max_write_queue <= 0:
            raise ValueError("max_write_queue must be > 0")

        self._device = device
        self._baudrate = baudrate
        self._read_chunk_size = read_chunk_size
        self._connect_timeout_s = connect_timeout_s
        self._codec = codec or SerialFrameCodec()
        self._open_serial_connection = open_serial_connection
        self._require_optional_deps = require_optional_deps

        self._reader: Any | None = None
        self._writer: Any | None = None
        self._connected = False
        self._healthy = False
        self._max_write_queue = max_write_queue
        self._frames: asyncio.Queue[bytes] = asyncio.Queue()
        self._write_queue: asyncio.Queue[bytes | _WriteRequest | None] = asyncio.Queue(
            maxsize=max_write_queue
        )
        self._writer_task: asyncio.Task[None] | None = None
        self._retirement: tuple[Any, asyncio.Task[None]] | None = None

    @property
    def connected(self) -> bool:
        """Whether serial transport is currently connected."""
        return self._connected

    @property
    def healthy(self) -> bool:
        """Whether I/O path is currently healthy."""
        return self._healthy

    @property
    def ready(self) -> bool:
        """Readiness signal suitable for higher-level radio_ready logic."""
        return self._connected and self._healthy

    def set_device(self, device: str) -> None:
        """Rebind this link to a different device node path (MOR-1453).

        Used when the configured path has vanished (e.g. a renumbered
        macOS ``/dev/cu.usbserial-*`` node after a USB replug) and a
        sibling node has been discovered and identity-confirmed. Only
        valid while disconnected -- callers must ``disconnect()`` first.
        """
        if not device.strip():
            raise ValueError("device must be non-empty")
        if self._connected:
            raise RuntimeError("Cannot change device while connected.")
        self._device = device

    async def connect(self) -> None:
        """Open serial CI-V transport."""
        if self._connected:
            return
        if self._require_optional_deps:
            self._ensure_serial_dependencies()

        self._reset_session_buffers()
        opener = self._resolve_opener()
        reader, writer = await asyncio.wait_for(
            opener(), timeout=self._connect_timeout_s
        )
        self._reader = reader
        self._writer = writer
        self._connected = True
        self._healthy = True
        self._writer_task = asyncio.create_task(
            self._writer_loop(self._write_queue, writer), name="serial-civ-writer"
        )

    async def disconnect(self) -> None:
        """Close serial CI-V transport and worker tasks."""
        if not self._connected and self._writer is None:
            self._healthy = False
            if self._retirement is not None:
                await self._join_retirement(self._retirement[1])
            return

        await self._retire_writer(self._write_queue, self._writer, self._writer_task)

    async def _retire_writer(
        self,
        queue: asyncio.Queue[bytes | _WriteRequest | None],
        writer: Any,
        worker: asyncio.Task[None] | None,
    ) -> None:
        previous = self._retirement
        if previous is not None and previous[0] is writer:
            await self._join_retirement(previous[1])
            return
        # Detach and close captured resources before joining a worker whose
        # drain may resist cancellation. Never clear a replacement after await.
        if self._writer is writer and self._write_queue is queue:
            self._connected = False
            self._healthy = False
            self._writer = None
            self._reader = None
            self._writer_task = None
            self._reset_session_buffers()
        self._fail_queued_writes(queue)
        close_error: Exception | None = None
        if writer is not None:
            try:
                writer.close()
            except Exception as exc:
                close_error = exc
        if worker is not None and not worker.done() and not worker.cancelling():
            worker.cancel()
        cleanup = asyncio.create_task(
            self._finish_retirement(
                writer,
                worker,
                previous[1] if previous is not None else None,
                close_error,
            ),
            name="serial-civ-retirement",
        )
        self._retirement = (writer, cleanup)
        await self._join_retirement(cleanup)

    async def _join_retirement(self, cleanup: asyncio.Task[None]) -> None:
        try:
            await asyncio.shield(cleanup)
        finally:
            if (
                cleanup.done()
                and self._retirement is not None
                and self._retirement[1] is cleanup
            ):
                self._retirement = None

    @staticmethod
    async def _finish_retirement(
        writer: Any,
        worker: asyncio.Task[None] | None,
        previous: asyncio.Task[None] | None,
        close_error: Exception | None,
    ) -> None:
        try:
            try:
                if worker is not None:
                    with contextlib.suppress(asyncio.CancelledError):
                        await worker
            finally:
                if writer is not None:
                    wait_closed = getattr(writer, "wait_closed", None)
                    if wait_closed is not None:
                        with contextlib.suppress(Exception):
                            await wait_closed()
        finally:
            try:
                if previous is not None:
                    await asyncio.shield(previous)
            finally:
                if close_error is not None:
                    raise close_error

    @staticmethod
    def _fail_queued_writes(queue: asyncio.Queue[bytes | _WriteRequest | None]) -> None:
        while not queue.empty():
            item = queue.get_nowait()
            if isinstance(item, _WriteRequest) and not item.result.done():
                item.result.set_exception(ConnectionError("Serial writer retired."))

    def _write_session_is_current(
        self, queue: asyncio.Queue[bytes | _WriteRequest | None], writer: Any
    ) -> bool:
        return (
            self._connected
            and writer is not None
            and self._writer is writer
            and self._write_queue is queue
        )

    async def send_written(
        self, frame: bytes, *, is_current: Callable[[], bool] | None = None
    ) -> None:
        """Wait for this session's local write/drain, not a radio ACK."""
        queue, writer, worker = self._write_queue, self._writer, self._writer_task
        if worker is None or worker.done():
            raise ConnectionError("Serial writer is not running.")
        request = _WriteRequest(
            bytes(frame),
            queue,
            writer,
            asyncio.get_running_loop().create_future(),
            is_current,
        )
        try:
            while True:
                if not self._write_session_is_current(queue, writer):
                    raise ConnectionError("Serial write session is no longer current.")
                if is_current is not None and not is_current():
                    raise CommandError("Serial CI-V write is no longer current.")
                try:
                    queue.put_nowait(request)
                    break
                except asyncio.QueueFull:
                    await asyncio.sleep(0)
            await request.result
        except asyncio.CancelledError as cancelled:
            request.result.cancel()
            if request.started:
                try:
                    await self._retire_writer(queue, writer, worker)
                except Exception as exc:
                    raise cancelled from exc
            raise

    async def send(self, frame: bytes) -> None:
        """Queue one CI-V payload/frame for serialized sending."""
        payload = bytes(frame)
        while True:
            if not self._connected:
                raise ConnectionError("Serial CI-V link is disconnected.")
            try:
                self._write_queue.put_nowait(payload)
                return
            except asyncio.QueueFull:
                await asyncio.sleep(0)

    async def receive(self, timeout: float | None = None) -> bytes | None:
        """Receive one full framed CI-V packet, or None on timeout."""
        if not self._connected:
            return None
        timeout_s = 5.0 if timeout is None else timeout
        deadline = time.monotonic() + timeout_s

        while True:
            if not self._frames.empty():
                return self._frames.get_nowait()

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                if self._codec.expire_partial():
                    self._healthy = False
                    raise SerialFrameTimeoutError(
                        "Timed out waiting for complete frame."
                    )
                return None

            try:
                chunk = await asyncio.wait_for(
                    self._read_once(),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                if self._codec.expire_partial():
                    self._healthy = False
                    raise SerialFrameTimeoutError(
                        "Timed out waiting for complete frame."
                    )
                return None

            if chunk is None:
                return None
            for parsed in self._codec.feed(chunk):
                self._frames.put_nowait(parsed)

    async def _read_once(self) -> bytes | None:
        reader = self._reader
        if reader is None:
            self._connected = False
            self._healthy = False
            return None
        try:
            chunk = await reader.read(self._read_chunk_size)
        except (OSError, asyncio.IncompleteReadError) as exc:
            logger.warning("Recoverable serial read error: %s", exc)
            self._healthy = False
            await asyncio.sleep(0.5)  # backoff to prevent log/CPU flood
            return b""
        except Exception as exc:
            # Catch pyserial SerialException ("device reports readiness...")
            # which is not a subclass of OSError.
            if "SerialException" in type(exc).__name__:
                logger.warning("Recoverable serial read error: %s", exc)
                self._healthy = False
                await asyncio.sleep(0.5)
                return b""
            raise
        except Exception:
            logger.exception("Unrecoverable serial read error.")
            self._connected = False
            self._healthy = False
            return None

        if chunk == b"":
            # EOF / stream closed by peer.
            self._connected = False
            self._healthy = False
            return None

        self._healthy = True
        return bytes(chunk)

    async def _writer_loop(
        self,
        queue: asyncio.Queue[bytes | _WriteRequest | None] | None = None,
        writer: Any = None,
    ) -> None:
        queue = self._write_queue if queue is None else queue
        writer = self._writer if writer is None else writer
        worker = asyncio.current_task()
        request: _WriteRequest | None = None
        try:
            while True:
                item = await queue.get()
                request = item if isinstance(item, _WriteRequest) else None
                if item is None:
                    return
                if request is not None and request.result.done():
                    continue
                if not self._write_session_is_current(queue, writer):
                    return
                payload = item.payload if isinstance(item, _WriteRequest) else item
                if request is not None:
                    try:
                        if request.queue is not queue or request.writer is not writer:
                            raise ConnectionError("Serial write session changed.")
                        if request.is_current is not None and not request.is_current():
                            raise CommandError(
                                "Serial CI-V write is no longer current."
                            )
                    except Exception as exc:
                        request.result.set_exception(exc)
                        continue
                    request.started = True
                try:
                    writer.write(self._codec.encode(payload))
                    await writer.drain()
                except (OSError, RuntimeError) as exc:
                    if request is not None:
                        request.started = False
                        if not request.result.done():
                            request.result.set_exception(exc)
                    logger.warning("Recoverable serial write error: %s", exc)
                    if not self._write_session_is_current(queue, writer) or (
                        worker is not None and worker.cancelling()
                    ):
                        return
                    self._healthy = False
                    await asyncio.sleep(0)
                except Exception as exc:
                    if request is not None and not request.result.done():
                        request.result.set_exception(exc)
                    logger.exception("Unrecoverable serial write error.")
                    if self._write_session_is_current(queue, writer):
                        self._connected = False
                        self._healthy = False
                    return
                else:
                    if not self._write_session_is_current(queue, writer) or (
                        worker is not None and worker.cancelling()
                    ):
                        return
                    self._healthy = True
                    if request is not None and not request.result.done():
                        request.result.set_result(None)
                finally:
                    if request is not None:
                        request.started = False
        finally:
            if request is not None and not request.result.done():
                request.result.set_exception(ConnectionError("Serial writer retired."))
            self._fail_queued_writes(queue)

    def _resolve_opener(self) -> Callable[[], Awaitable[tuple[Any, Any]]]:
        if self._open_serial_connection is not None:
            return self._open_serial_connection

        self._ensure_serial_dependencies()
        serial_asyncio = importlib.import_module("serial_asyncio")

        async def _open() -> tuple[Any, Any]:
            reader, writer = await serial_asyncio.open_serial_connection(
                url=self._device,
                baudrate=self._baudrate,
            )
            return (reader, writer)

        return _open

    def _ensure_serial_dependencies(self) -> None:
        from rigplane._optional_deps import _require_pyserial_asyncio

        # pyserial-asyncio depends on pyserial, so a single check covers both.
        _require_pyserial_asyncio()

    def _reset_session_buffers(self) -> None:
        """Drop buffered RX/TX data between sessions."""
        self._frames = asyncio.Queue()
        self._write_queue = asyncio.Queue(maxsize=self._max_write_queue)
        self._codec.reset()


__all__ = [
    "SerialCivLink",
    "SerialFrameCodec",
    "SerialFrameError",
    "SerialFrameOverflowError",
    "SerialFrameTimeoutError",
]
