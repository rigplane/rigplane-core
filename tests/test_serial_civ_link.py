"""Unit tests for production SerialCivLink framing and guardrails."""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from rigplane.backends.icom7610.drivers.serial_civ_link import (
    SerialCivLink,
    SerialFrameCodec,
    SerialFrameOverflowError,
    SerialFrameTimeoutError,
)
from rigplane.exceptions import CommandError

_ON = b"\x98\xe0\x1c\x00\x01"
_OFF = b"\x98\xe0\x1c\x00\x00"
_QUERY = b"\x98\xe0\x03"


class _FakeReader:
    def __init__(self) -> None:
        self._chunks: asyncio.Queue[bytes | BaseException] = asyncio.Queue()

    async def read(self, _n: int) -> bytes:
        item = await self._chunks.get()
        if isinstance(item, BaseException):
            raise item
        return item

    def push(self, chunk: bytes) -> None:
        self._chunks.put_nowait(chunk)

    def push_error(self, exc: BaseException) -> None:
        self._chunks.put_nowait(exc)


class _FakeWriter:
    def __init__(
        self,
        *,
        drain_gate: asyncio.Event | None = None,
        write_error: OSError | None = None,
        drain_error: OSError | RuntimeError | None = None,
        resist_cancel: bool = False,
        close_gate: asyncio.Event | None = None,
    ) -> None:
        self.writes: list[bytes] = []
        self.closed = False
        self._drain_gate = drain_gate
        self._write_error = write_error
        self._drain_error = drain_error
        self._resist_cancel = resist_cancel
        self._close_gate = close_gate
        self.write_started = asyncio.Event()
        self.drain_started = asyncio.Event()
        self.close_called = asyncio.Event()
        self.cancel_resisted = asyncio.Event()
        self.wait_closed_started = asyncio.Event()

    def write(self, data: bytes) -> None:
        self.write_started.set()
        if self.closed:
            raise ConnectionError("write on closed serial writer")
        if self._write_error is not None:
            raise self._write_error
        self.writes.append(bytes(data))

    async def drain(self) -> None:
        self.drain_started.set()
        if self._drain_gate is not None:
            try:
                await self._drain_gate.wait()
            except asyncio.CancelledError:
                if not self._resist_cancel:
                    raise
                self.cancel_resisted.set()
                await self._drain_gate.wait()
        if self._drain_error is not None:
            raise self._drain_error

    def close(self) -> None:
        self.closed = True
        self.close_called.set()

    async def wait_closed(self) -> None:
        self.wait_closed_started.set()
        if self._close_gate is not None:
            await self._close_gate.wait()


async def _make_link(
    *,
    codec: SerialFrameCodec | None = None,
    queue_size: int = 8,
    reader: _FakeReader | None = None,
    writer: _FakeWriter | None = None,
) -> tuple[SerialCivLink, _FakeReader, _FakeWriter]:
    fake_reader = reader or _FakeReader()
    fake_writer = writer or _FakeWriter()

    async def _open() -> tuple[_FakeReader, _FakeWriter]:
        return fake_reader, fake_writer

    link = SerialCivLink(
        device="/dev/tty.usbmodem-IC7610",
        baudrate=19200,
        codec=codec,
        max_write_queue=queue_size,
        open_serial_connection=_open,
    )
    await link.connect()
    return link, fake_reader, fake_writer


def _wire(payload: bytes) -> bytes:
    return b"\xfe\xfe" + payload + b"\xfd"


async def _cleanup_writes(
    link: SerialCivLink,
    tasks: list[asyncio.Task[None]],
    *writers: _FakeWriter,
    old_worker: asyncio.Task[None] | None = None,
) -> None:
    for writer in writers:
        if writer._drain_gate is not None:
            writer._drain_gate.set()
        if writer._close_gate is not None:
            writer._close_gate.set()
    # Release controlled drains before cancellation, including the fake which
    # deliberately resists cancellation. This helper is cleanup only.
    for _ in range(3):
        await asyncio.sleep(0)
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    if old_worker is not None and old_worker is not link._writer_task:
        old_worker.cancel()
        await asyncio.gather(old_worker, return_exceptions=True)
    await link.disconnect()


def _install_replacement(link: SerialCivLink, writer: _FakeWriter) -> None:
    # Controlled transport-identity replacement, without any authority callback.
    link._reader = _FakeReader()
    link._writer = writer
    link._write_queue = asyncio.Queue(maxsize=link._max_write_queue)
    link._connected = True
    link._healthy = True
    link._writer_task = asyncio.create_task(link._writer_loop())


@pytest.mark.asyncio
async def test_send_written_waits_for_drain_and_preserves_raw_enqueue() -> None:
    gate = asyncio.Event()
    link, _, writer = await _make_link(writer=_FakeWriter(drain_gate=gate))
    tasks: list[asyncio.Task[None]] = []
    try:
        written = asyncio.create_task(link.send_written(_ON))
        tasks.append(written)
        await asyncio.wait_for(writer.drain_started.wait(), timeout=1)
        await link.send(_QUERY)
        assert writer.writes == [_wire(_ON)]
        assert not written.done(), "send_written completed before drain"
        gate.set()
        await asyncio.wait_for(written, timeout=1)
        await asyncio.wait_for(link.send_written(_OFF), timeout=1)
        assert writer.writes == [_wire(_ON), _wire(_QUERY), _wire(_OFF)]
    finally:
        await _cleanup_writes(link, tasks, writer)


@pytest.mark.asyncio
@pytest.mark.parametrize("guard_failure", ["false", "raises"])
async def test_send_written_rechecks_guard_at_writer(guard_failure: str) -> None:
    gate = asyncio.Event()
    link, _, writer = await _make_link(writer=_FakeWriter(drain_gate=gate))
    current = True
    failure = RuntimeError("guard failed at final write")
    tasks: list[asyncio.Task[None]] = []

    def is_current() -> bool:
        if not current and guard_failure == "raises":
            raise failure
        return current

    try:
        await link.send(_QUERY)
        await asyncio.wait_for(writer.drain_started.wait(), timeout=1)
        pending = asyncio.create_task(link.send_written(_ON, is_current=is_current))
        tasks.append(pending)
        await asyncio.sleep(0)
        assert not pending.done()
        current = False
        gate.set()
        result = await asyncio.wait_for(
            asyncio.gather(pending, return_exceptions=True), timeout=1
        )
        assert writer.writes == [_wire(_QUERY)], "stale ON reached serial writer"
        if guard_failure == "raises":
            assert result[0] is failure
        else:
            assert isinstance(result[0], CommandError)
        assert not writer.closed
        assert link.ready
        await asyncio.wait_for(link.send_written(_OFF), timeout=1)
        assert writer.writes == [_wire(_QUERY), _wire(_OFF)]
    finally:
        await _cleanup_writes(link, tasks, writer)


@pytest.mark.asyncio
@pytest.mark.parametrize("replacement", ["queue", "writer", "both"])
async def test_send_written_captures_session_before_backpressure(
    replacement: str,
) -> None:
    gate = asyncio.Event()
    link, _, writer = await _make_link(
        queue_size=1, writer=_FakeWriter(drain_gate=gate)
    )
    newer = _FakeWriter()
    tasks: list[asyncio.Task[None]] = []
    try:
        await link.send(_QUERY)
        await asyncio.wait_for(writer.drain_started.wait(), timeout=1)
        await link.send(_QUERY)
        pending = asyncio.create_task(link.send_written(_ON, is_current=lambda: True))
        tasks.append(pending)
        await asyncio.sleep(0)
        assert not pending.done()
        if replacement in {"queue", "both"}:
            link._write_queue = asyncio.Queue(maxsize=1)
        if replacement in {"writer", "both"}:
            link._writer = newer
        result = await asyncio.wait_for(
            asyncio.gather(pending, return_exceptions=True), timeout=1
        )
        assert writer.writes == [_wire(_QUERY)]
        assert newer.writes == [], "old queued ON crossed serial session identity"
        assert isinstance(result[0], ConnectionError)
        assert not newer.closed
        assert link.connected
    finally:
        await _cleanup_writes(link, tasks, writer, newer)
        writer.close()


@pytest.mark.asyncio
async def test_admitted_write_rechecks_writer_identity_before_write() -> None:
    gate = asyncio.Event()
    link, _, writer = await _make_link(
        queue_size=1, writer=_FakeWriter(drain_gate=gate)
    )
    newer = _FakeWriter()
    tasks: list[asyncio.Task[None]] = []
    try:
        await link.send(_QUERY)
        await asyncio.wait_for(writer.drain_started.wait(), timeout=1)
        pending = asyncio.create_task(link.send_written(_ON, is_current=lambda: True))
        tasks.append(pending)
        await asyncio.sleep(0)
        assert link._write_queue.qsize() == 1
        assert not pending.done()
        link._writer = newer
        gate.set()
        result = await asyncio.wait_for(
            asyncio.gather(pending, return_exceptions=True), timeout=1
        )
        assert writer.writes == [_wire(_QUERY)], "admitted ON used retired writer"
        assert newer.writes == [], "admitted ON crossed writer identity"
        assert isinstance(result[0], ConnectionError)
        assert not newer.closed
        assert link._writer is newer
    finally:
        await _cleanup_writes(link, tasks, writer, newer)
        writer.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("waiting_for_space", [False, True])
async def test_cancelled_queued_write_keeps_active_neighbor_and_successor(
    waiting_for_space: bool,
) -> None:
    gate = asyncio.Event()
    link, _, writer = await _make_link(
        queue_size=1, writer=_FakeWriter(drain_gate=gate)
    )
    tasks: list[asyncio.Task[None]] = []
    try:
        await link.send(_QUERY)
        await asyncio.wait_for(writer.drain_started.wait(), timeout=1)
        if waiting_for_space:
            await link.send(_QUERY)
        pending = asyncio.create_task(link.send_written(_ON))
        tasks.append(pending)
        await asyncio.sleep(0)
        assert not pending.done()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        assert not writer.closed, "queued cancellation retired active neighbor"
        assert link.ready
        gate.set()
        await asyncio.wait_for(link.send_written(_OFF), timeout=1)
        queries = [_wire(_QUERY)] * (2 if waiting_for_space else 1)
        assert writer.writes == [*queries, _wire(_OFF)]
    finally:
        await _cleanup_writes(link, tasks, writer)


@pytest.mark.asyncio
async def test_active_write_cancellation_closes_captured_not_replacement() -> None:
    gate = asyncio.Event()
    link, _, writer = await _make_link(writer=_FakeWriter(drain_gate=gate))
    newer = _FakeWriter()
    old_worker = link._writer_task
    tasks: list[asyncio.Task[None]] = []
    try:
        pending = asyncio.create_task(link.send_written(_ON))
        tasks.append(pending)
        await asyncio.wait_for(writer.drain_started.wait(), timeout=1)
        _install_replacement(link, newer)
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, timeout=1)
        assert writer.closed, "active cancellation left captured writer open"
        assert not newer.closed, "old cancellation closed replacement writer"
        assert link._writer is newer
        assert link.ready
        await asyncio.wait_for(link.send_written(_OFF), timeout=1)
        assert writer.writes == [_wire(_ON)]
        assert newer.writes == [_wire(_OFF)]
    finally:
        await _cleanup_writes(link, tasks, writer, newer, old_worker=old_worker)


@pytest.mark.asyncio
async def test_replacement_off_follows_last_possible_old_session_on() -> None:
    gate = asyncio.Event()
    writer = _FakeWriter(drain_gate=gate, resist_cancel=True)
    link, _, _ = await _make_link(writer=writer)
    newer = _FakeWriter()
    old_worker = link._writer_task
    tasks: list[asyncio.Task[None]] = []
    try:
        pending = asyncio.create_task(link.send_written(_ON))
        tasks.append(pending)
        await asyncio.wait_for(writer.drain_started.wait(), timeout=1)
        await link.send(_ON)
        pending.cancel()
        await asyncio.wait_for(writer.close_called.wait(), timeout=1)
        _install_replacement(link, newer)
        await asyncio.wait_for(link.send_written(_OFF), timeout=1)
        assert writer.closed
        assert writer.writes == [_wire(_ON)]
        assert newer.writes == [_wire(_OFF)]
        gate.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(pending, timeout=1)
        # A further completed write puts the assertion after the released old
        # drain as well as after the replacement's receive command.
        await asyncio.wait_for(link.send_written(_QUERY), timeout=1)
        assert writer.writes == [_wire(_ON)], "old queued ON escaped after new OFF"
        assert newer.writes == [_wire(_OFF), _wire(_QUERY)]
        assert not newer.closed
        assert link.ready
    finally:
        await _cleanup_writes(link, tasks, writer, newer, old_worker=old_worker)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_type", [OSError, RuntimeError])
async def test_retired_worker_exits_after_cancel_resistant_drain_error(
    failure_type: type[Exception],
) -> None:
    gate = asyncio.Event()
    writer = _FakeWriter(
        drain_gate=gate,
        drain_error=failure_type("late retired drain error"),
        resist_cancel=True,
    )
    link, _, _ = await _make_link(writer=writer)
    old_worker = link._writer_task
    assert old_worker is not None
    newer = _FakeWriter()
    pending = asyncio.create_task(link.send_written(_ON))
    try:
        await writer.drain_started.wait()
        pending.cancel()
        await writer.cancel_resisted.wait()
        _install_replacement(link, newer)
        gate.set()
        _, unfinished = await asyncio.wait({pending, old_worker}, timeout=1)
        assert not unfinished, "retired error path left worker or sender unfinished"
        assert pending.cancelled()
        assert writer.closed
        assert writer.writes == [_wire(_ON)]
        assert not newer.closed
        assert link.ready
        await link.send_written(_OFF)
        assert newer.writes == [_wire(_OFF)]
    finally:
        await _cleanup_writes(link, [pending], writer, newer, old_worker=old_worker)


@pytest.mark.asyncio
@pytest.mark.parametrize("initiator", ["disconnect", "active-cancel"])
async def test_overlapping_disconnect_joins_drain_and_wait_closed(
    initiator: str,
) -> None:
    gate, close_gate = asyncio.Event(), asyncio.Event()
    writer = _FakeWriter(
        drain_gate=gate, resist_cancel=True, close_gate=close_gate
    )
    link, _, _ = await _make_link(writer=writer)
    old_worker = link._writer_task
    assert old_worker is not None
    newer = _FakeWriter()
    pending = asyncio.create_task(link.send_written(_ON))
    tasks = [pending]
    try:
        await writer.drain_started.wait()
        if initiator == "disconnect":
            first = asyncio.create_task(link.disconnect())
            tasks.append(first)
        else:
            pending.cancel()
            first = pending
        await writer.cancel_resisted.wait()
        second = asyncio.create_task(link.disconnect())
        tasks.append(second)
        done, _ = await asyncio.wait({first, second}, timeout=0.05)
        assert not done, "overlapping disconnect skipped active drain retirement"
        assert not old_worker.done()
        _install_replacement(link, newer)
        gate.set()
        await writer.wait_closed_started.wait()
        done, _ = await asyncio.wait({first, second}, timeout=0.05)
        assert not done, "overlapping disconnect skipped captured wait_closed"
        assert old_worker.done()
        assert not newer.closed
        assert link.ready
        close_gate.set()
        _, unfinished = await asyncio.wait(set(tasks), timeout=1)
        assert not unfinished, "retirement callers did not settle after writer close"
        results = await asyncio.gather(*tasks, return_exceptions=True)
        if initiator == "active-cancel":
            assert isinstance(results[0], asyncio.CancelledError)
        else:
            assert isinstance(results[0], ConnectionError)
            assert results[1] is None
        assert results[-1] is None
        assert not newer.closed
        assert link._writer is newer
        await link.send_written(_OFF)
        assert newer.writes == [_wire(_OFF)]
    finally:
        await _cleanup_writes(link, tasks, writer, newer, old_worker=old_worker)


@pytest.mark.asyncio
async def test_receive_complete_frame() -> None:
    link, reader, _ = await _make_link()
    try:
        reader.push(b"\xfe\xfe\x98\xe0\x03\xfd")
        assert await link.receive(timeout=0.05) == b"\xfe\xfe\x98\xe0\x03\xfd"
    finally:
        await link.disconnect()


@pytest.mark.asyncio
async def test_receive_split_frame_across_chunks() -> None:
    link, reader, _ = await _make_link()
    try:
        reader.push(b"\xfe\xfe\x98")
        reader.push(b"\xe0\x03")
        reader.push(b"\xfd")
        assert await link.receive(timeout=0.05) == b"\xfe\xfe\x98\xe0\x03\xfd"
    finally:
        await link.disconnect()


@pytest.mark.asyncio
async def test_receive_multiple_frames_in_single_chunk() -> None:
    link, reader, _ = await _make_link()
    try:
        reader.push(b"\xfe\xfe\x98\xe0\x03\xfd\xfe\xfe\x98\xe0\x04\xfd")
        assert await link.receive(timeout=0.05) == b"\xfe\xfe\x98\xe0\x03\xfd"
        assert await link.receive(timeout=0.05) == b"\xfe\xfe\x98\xe0\x04\xfd"
    finally:
        await link.disconnect()


@pytest.mark.asyncio
async def test_receive_ignores_garbage_before_sof() -> None:
    link, reader, _ = await _make_link()
    try:
        reader.push(b"garbage\x00\x01\xfe\xfe\x98\xe0\x03\xfd")
        assert await link.receive(timeout=0.05) == b"\xfe\xfe\x98\xe0\x03\xfd"
    finally:
        await link.disconnect()


@pytest.mark.asyncio
async def test_receive_recovers_from_malformed_partial_then_valid_frame() -> None:
    link, reader, _ = await _make_link()
    try:
        reader.push(b"\xfe\xfe\x98\xe0\x03\xfe\xfe\x98\xe0\x04\xfd")
        assert await link.receive(timeout=0.05) == b"\xfe\xfe\x98\xe0\x04\xfd"
    finally:
        await link.disconnect()


@pytest.mark.asyncio
async def test_receive_drops_collision_abort_pattern() -> None:
    link, reader, _ = await _make_link()
    try:
        reader.push(b"\xfe\xfe\x98\xe0\xfc\xfd\xfe\xfe\x98\xe0\x03\xfd")
        assert await link.receive(timeout=0.05) == b"\xfe\xfe\x98\xe0\x03\xfd"
    finally:
        await link.disconnect()


@pytest.mark.asyncio
async def test_partial_frame_timeout_raises() -> None:
    codec = SerialFrameCodec(max_frame_len=64, frame_timeout_s=0.001)
    link, reader, _ = await _make_link(codec=codec)
    try:
        reader.push(b"\xfe\xfe\x98")
        with pytest.raises(SerialFrameTimeoutError):
            await link.receive(timeout=0.02)
    finally:
        await link.disconnect()


@pytest.mark.asyncio
async def test_overflow_guard_raises() -> None:
    codec = SerialFrameCodec(max_frame_len=6, frame_timeout_s=0.1)
    link, reader, _ = await _make_link(codec=codec)
    try:
        reader.push(b"\xfe\xfe\x98\xe0")
        reader.push(b"\x03\x99\x98")
        with pytest.raises(SerialFrameOverflowError):
            await link.receive(timeout=0.05)
    finally:
        await link.disconnect()


@pytest.mark.asyncio
async def test_writer_serialization_and_backpressure() -> None:
    drain_gate = asyncio.Event()
    writer = _FakeWriter(drain_gate=drain_gate)
    link, _reader, fake_writer = await _make_link(queue_size=1, writer=writer)
    try:
        t1 = asyncio.create_task(link.send(b"\x98\xe0\x03"))
        await asyncio.sleep(0)
        t2 = asyncio.create_task(link.send(b"\x98\xe0\x04"))
        await asyncio.sleep(0)
        t3 = asyncio.create_task(link.send(b"\x98\xe0\x05"))
        await asyncio.sleep(0)

        assert t1.done()
        assert not t3.done()

        drain_gate.set()
        await asyncio.wait_for(asyncio.gather(t1, t2, t3), timeout=0.2)
        assert fake_writer.writes == [
            b"\xfe\xfe\x98\xe0\x03\xfd",
            b"\xfe\xfe\x98\xe0\x04\xfd",
            b"\xfe\xfe\x98\xe0\x05\xfd",
        ]
    finally:
        await link.disconnect()


@pytest.mark.asyncio
async def test_receive_recoverable_io_error_returns_next_valid_frame() -> None:
    link, reader, _ = await _make_link()
    try:
        reader.push_error(OSError("temporary serial read failure"))
        reader.push(b"\xfe\xfe\x98\xe0\x03\xfd")
        # Timeout must exceed backoff (0.5s in _read_once) to allow recovery
        assert await link.receive(timeout=1.0) == b"\xfe\xfe\x98\xe0\x03\xfd"
        assert link.connected is True
        assert link.healthy is True
    finally:
        await link.disconnect()


@pytest.mark.asyncio
async def test_dependency_missing_hint(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _unexpected_open() -> tuple[_FakeReader, _FakeWriter]:
        raise AssertionError("open must not be called when deps are missing")

    link = SerialCivLink(
        device="/dev/tty.usbmodem-IC7610",
        open_serial_connection=_unexpected_open,
        require_optional_deps=True,
    )

    def _raise_missing() -> None:
        raise ImportError(
            "Serial backend requires optional dependencies pyserial and "
            "pyserial-asyncio. Install with: pip install rigplane[serial]"
        )

    monkeypatch.setattr(link, "_ensure_serial_dependencies", _raise_missing)
    with pytest.raises(ImportError, match="rigplane\\[serial\\]"):
        await link.connect()


@pytest.mark.asyncio
async def test_disconnect_closes_writer_and_marks_not_ready() -> None:
    link, _reader, writer = await _make_link()
    await link.disconnect()
    assert writer.closed is True
    assert link.connected is False
    assert link.healthy is False


@pytest.mark.asyncio
async def test_send_when_disconnected_raises() -> None:
    link, _reader, _writer = await _make_link()
    await link.disconnect()
    with pytest.raises(ConnectionError):
        await link.send(b"\x98\xe0\x03")


@pytest.mark.asyncio
async def test_receive_returns_none_on_timeout_without_data() -> None:
    link, _reader, _writer = await _make_link()
    try:
        assert await link.receive(timeout=0.005) is None
    finally:
        await link.disconnect()


@pytest.mark.asyncio
async def test_receive_closed_stream_marks_unhealthy() -> None:
    link, reader, _writer = await _make_link()
    try:
        reader.push(b"")
        assert await link.receive(timeout=0.02) is None
        assert link.connected is False
        assert link.healthy is False
    finally:
        await link.disconnect()


@pytest.mark.asyncio
async def test_writer_worker_cancellation_does_not_leak_tasks() -> None:
    link, _reader, _writer = await _make_link()
    try:
        task = link._writer_task
        assert task is not None
    finally:
        await link.disconnect()

    assert task is not None
    assert task.done()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_disconnect_drops_stale_rx_frames_before_reconnect() -> None:
    reader1 = _FakeReader()
    reader2 = _FakeReader()
    writer1 = _FakeWriter()
    writer2 = _FakeWriter()
    session = 0

    async def _open() -> tuple[_FakeReader, _FakeWriter]:
        nonlocal session
        session += 1
        if session == 1:
            return reader1, writer1
        return reader2, writer2

    link = SerialCivLink(
        device="/dev/tty.usbmodem-IC7610",
        open_serial_connection=_open,
    )
    await link.connect()
    try:
        frame1 = b"\xfe\xfe\x98\xe0\x01\xfd"
        frame2 = b"\xfe\xfe\x98\xe0\x02\xfd"
        frame3 = b"\xfe\xfe\x98\xe0\x03\xfd"
        reader1.push(frame1 + frame2)
        assert await link.receive(timeout=0.05) == frame1

        await link.disconnect()
        await link.connect()
        reader2.push(frame3)
        assert await link.receive(timeout=0.05) == frame3
    finally:
        await link.disconnect()


@pytest.mark.asyncio
async def test_disconnect_drops_stale_tx_queue_before_reconnect() -> None:
    drain_gate = asyncio.Event()
    reader1 = _FakeReader()
    reader2 = _FakeReader()
    writer1 = _FakeWriter(drain_gate=drain_gate)
    writer2 = _FakeWriter()
    session = 0

    async def _open() -> tuple[_FakeReader, _FakeWriter]:
        nonlocal session
        session += 1
        if session == 1:
            return reader1, writer1
        return reader2, writer2

    link = SerialCivLink(
        device="/dev/tty.usbmodem-IC7610",
        max_write_queue=1,
        open_serial_connection=_open,
    )
    await link.connect()
    try:
        await link.send(b"\x98\xe0\x01")
        await asyncio.sleep(0)
        await link.send(b"\x98\xe0\x02")
        await asyncio.sleep(0)

        await link.disconnect()
        drain_gate.set()
        await link.connect()

        await link.send(b"\x98\xe0\x03")
        await asyncio.sleep(0)
        assert writer2.writes == [b"\xfe\xfe\x98\xe0\x03\xfd"]
    finally:
        await link.disconnect()


@pytest.mark.asyncio
async def test_send_backpressure_unblocks_on_disconnect() -> None:
    drain_gate = asyncio.Event()
    writer = _FakeWriter(drain_gate=drain_gate)
    link, _reader, _writer = await _make_link(queue_size=1, writer=writer)
    try:
        await link.send(b"\x98\xe0\x01")
        await asyncio.sleep(0)
        await link.send(b"\x98\xe0\x02")
        await asyncio.sleep(0)

        blocked_send = asyncio.create_task(link.send(b"\x98\xe0\x03"))
        await asyncio.sleep(0)
        assert not blocked_send.done()

        await link.disconnect()
        with pytest.raises(ConnectionError):
            await asyncio.wait_for(blocked_send, timeout=0.1)
    finally:
        drain_gate.set()
        await link.disconnect()


def test_codec_encodes_unframed_payload() -> None:
    codec = SerialFrameCodec(max_frame_len=64, frame_timeout_s=0.01)
    assert codec.encode(b"\x98\xe0\x03") == b"\xfe\xfe\x98\xe0\x03\xfd"


def test_codec_keeps_already_framed_payload() -> None:
    codec = SerialFrameCodec(max_frame_len=64, frame_timeout_s=0.01)
    frame = b"\xfe\xfe\x98\xe0\x03\xfd"
    assert codec.encode(frame) == frame


def test_serial_civ_link_default_baudrate_is_115200() -> None:
    link = SerialCivLink(device="/dev/tty.usbmodem-IC7610")
    assert link._baudrate == 115200


def test_set_device_rebinds_path_while_disconnected() -> None:
    """MOR-1453: rediscovery rebinds the link to a renumbered device node."""
    link = SerialCivLink(device="/dev/cu.usbserial-1420")
    link.set_device("/dev/cu.usbserial-9931")
    assert link._device == "/dev/cu.usbserial-9931"


def test_set_device_rejects_empty_path() -> None:
    link = SerialCivLink(device="/dev/cu.usbserial-1420")
    with pytest.raises(ValueError, match="non-empty"):
        link.set_device("   ")


@pytest.mark.asyncio
async def test_set_device_rejects_while_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    link = SerialCivLink(device="/dev/cu.usbserial-1420")

    async def _open() -> tuple[_FakeReader, _FakeWriter]:
        return _FakeReader(), _FakeWriter()

    link._open_serial_connection = _open
    await link.connect()
    try:
        with pytest.raises(RuntimeError, match="Cannot change device"):
            link.set_device("/dev/cu.usbserial-9931")
    finally:
        await link.disconnect()
