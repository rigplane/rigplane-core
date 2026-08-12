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
    def __init__(self, *, drain_gate: asyncio.Event | None = None) -> None:
        self.writes: list[bytes] = []
        self.closed = False
        self._drain_gate = drain_gate

    def write(self, data: bytes) -> None:
        self.writes.append(bytes(data))

    async def drain(self) -> None:
        if self._drain_gate is not None:
            await self._drain_gate.wait()

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


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
