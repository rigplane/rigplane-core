"""Tests for the Yaesu CAT serial adapter (MOR-3602).

The wire tests drive a real ``TcpEndpoint`` through pyserial's
``socket://`` URL — the same open the production link performs — or raw
TCP clients; the PTY test opens the slave path with ``os.open`` and
termios raw. Nothing here touches a real serial device.
"""

from __future__ import annotations

import ast
import asyncio
import gc
import json
import os
import select
import time
import weakref
from dataclasses import replace
from pathlib import Path

import pytest
import serial

from rigplane_testkit.serial.endpoints import PtyEndpoint, TcpEndpoint, pty_supported
from rigplane_testkit.sim.adapters import yaesu_cat as adapter
from rigplane_testkit.sim.adapters.yaesu_cat import (
    YaesuCatSerialFake,
    fidelity_for_banner,
)
from rigplane_testkit.yaesu.replay import RecordedCatAnswerTable

ROOT = Path(__file__).resolve().parents[2]
LEDGER = ROOT / "tests" / "fixtures" / "ftx1" / "model_conformance_ledger.json"
ADAPTER_PATH = ROOT / "rigplane_testkit" / "sim" / "adapters" / "yaesu_cat.py"


def _fake(seed: int = 0) -> YaesuCatSerialFake:
    return YaesuCatSerialFake.from_profile(ROOT, seed=seed)


def _connect(url: str) -> serial.SerialBase:
    """The production open: pyserial's ``socket://`` URL, 3 s timeout."""
    return serial.serial_for_url(url, baudrate=38400, timeout=3)


async def _write(port, data: bytes) -> None:
    await asyncio.to_thread(port.write, data)


async def _read_line(port, timeout: float = 3.0) -> bytes:
    return bytes(
        await asyncio.wait_for(asyncio.to_thread(port.read_until, b";"), timeout)
    )


async def _until(condition, timeout: float = 5.0) -> None:
    async def _poll() -> None:
        while not condition():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), timeout)


async def test_tcp_wire_identity_chunking_set_and_refusal() -> None:
    fake = _fake()
    endpoint = TcpEndpoint(fake)
    await endpoint.start()
    try:
        port = await asyncio.to_thread(_connect, endpoint.device_url)
        try:
            await _write(port, b"ID;")
            assert await _read_line(port) == b"ID0840;"

            # Two lines in one chunk.
            await _write(port, b"FA;MD0;")
            assert await _read_line(port) == b"FA147225000;"
            assert await _read_line(port) == b"MD04;"  # FM

            # A line split over chunks.
            for chunk in (b"I", b"D", b";"):
                await _write(port, chunk)
            assert await _read_line(port) == b"ID0840;"

            # A served SET gets no reply but changes state: the one reply
            # behind it is the read's, with the new value. (FB: the seeded
            # MEMORY mode refuses FA SETs, as recorded on the MAIN side.)
            await _write(port, b"FB007074000;FB;")
            assert await _read_line(port) == b"FB007074000;"
            assert fake.model.engine.state.receivers[1].frequency_hz == 7_074_000

            # An unserved SET answers '?;'.
            await _write(port, b"ZZ1;")
            assert await _read_line(port) == b"?;"
        finally:
            await asyncio.to_thread(port.close)
    finally:
        await endpoint.stop()


async def test_tcp_oversize_run_answers_refusal_once_and_recovers() -> None:
    fake = _fake()
    endpoint = TcpEndpoint(fake)
    await endpoint.start()
    try:
        port = await asyncio.to_thread(_connect, endpoint.device_url)
        try:
            # 65 bytes without a ';' cross the 64-byte run limit: one '?;'.
            await _write(port, b"X" * 65)
            assert await _read_line(port) == b"?;"
            # The tail up to the next ';' is discarded silently; the line
            # after it is served again.
            await _write(port, b"YYY;ID;")
            assert await _read_line(port) == b"ID0840;"
        finally:
            await asyncio.to_thread(port.close)
    finally:
        await endpoint.stop()


async def test_silence_consumes_lines_without_answering_or_applying() -> None:
    fake = _fake()
    endpoint = TcpEndpoint(fake)
    await endpoint.start()
    try:
        port = await asyncio.to_thread(_connect, endpoint.device_url)
        try:
            fake.silence(1.0)
            await _write(port, b"FB007074000;ID;")
            # Nothing answers inside the window (bounded negative read).
            port.timeout = 0.3
            assert await _read_line(port) == b""
            await _until(lambda: time.monotonic() >= fake._silent_until)
            # The silenced SET never applied (FB stays as seeded); reads
            # answer again.
            port.timeout = 3
            await _write(port, b"FB;ID;")
            assert await _read_line(port) == b"FB052900000;"
            assert await _read_line(port) == b"ID0840;"
        finally:
            await asyncio.to_thread(port.close)
    finally:
        await endpoint.stop()


async def test_tcp_vanish_drops_clients_and_closes_new_opens_until_reappear() -> None:
    fake = _fake()
    endpoint = TcpEndpoint(fake)
    await endpoint.start()
    try:
        reader, writer = await asyncio.open_connection("127.0.0.1", endpoint.port)
        fake.usb_vanish()  # at=0: fires on the next loop tick
        # The live connection drops: EOF, never data, never a hang.
        assert await asyncio.wait_for(reader.read(), 5.0) == b""
        writer.close()

        # While vanished, a new client is accepted then closed at once.
        gone_reader, gone_writer = await asyncio.open_connection(
            "127.0.0.1", endpoint.port
        )
        assert await asyncio.wait_for(gone_reader.read(), 5.0) == b""
        gone_writer.close()

        fake.usb_reappear(after=0.05)
        await _until(lambda: not fake._vanished)
        reader2, writer2 = await asyncio.open_connection("127.0.0.1", endpoint.port)
        writer2.write(b"ID;")
        await writer2.drain()
        assert await asyncio.wait_for(reader2.readuntil(b";"), 5.0) == b"ID0840;"
        writer2.close()
    finally:
        await endpoint.stop()


def _read_fd_until(fd: int, marker: bytes, timeout: float) -> bytes:
    deadline = time.monotonic() + timeout
    buf = bytearray()
    while marker not in buf:
        if time.monotonic() > deadline:
            raise TimeoutError(f"no {marker!r} within {timeout}s: {bytes(buf)!r}")
        ready, _, _ = select.select([fd], [], [], 0.05)
        if ready:
            chunk = os.read(fd, 256)
            if not chunk:
                raise OSError(f"pty returned EOF: {bytes(buf)!r}")
            buf += chunk
    return bytes(buf)


@pytest.mark.skipif(not pty_supported(), reason="PTY endpoints need macOS/Linux")
async def test_pty_round_trip_fa_through_slave_path() -> None:
    import tty

    fake = _fake()
    endpoint = PtyEndpoint(fake)
    await endpoint.start()
    try:
        fd = os.open(endpoint.device, os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK)
        try:
            tty.setraw(fd)
            os.write(fd, b"FA;")
            data = await asyncio.to_thread(_read_fd_until, fd, b";", 5.0)
            assert data == b"FA147225000;"
        finally:
            os.close(fd)
    finally:
        await endpoint.stop()


async def test_frequency_setter_bypasses_the_memory_mode_wire_refusal() -> None:
    fake = _fake()
    fake.model.engine.state.extra["main_memory_mode"] = True
    reader, writer = await fake.open_serial_connection()
    # The wire FA SET is refused in MEMORY mode (recorded MAIN behaviour)...
    writer.write(b"FA014074000;")
    assert await asyncio.wait_for(reader.readuntil(b";"), 3.0) == b"?;"
    assert fake.frequency_hz == 147_225_000  # unchanged
    # ...while the control-event setter applies straight on the engine.
    fake.frequency_hz = 7_074_000
    writer.write(b"FA;")
    assert await asyncio.wait_for(reader.readuntil(b";"), 3.0) == b"FA007074000;"
    # An engine refusal (here: above the FTX-1's 470 MHz top) raises.
    with pytest.raises(ValueError):
        fake.frequency_hz = 999_999_999
    assert fake.frequency_hz == 7_074_000
    writer.close()


async def test_unserved_unrecorded_request_is_counted_and_observed() -> None:
    seen: list[str] = []
    fake = YaesuCatSerialFake.from_profile(ROOT, on_not_modelled=seen.append)
    reader, writer = await fake.open_serial_connection()
    try:
        # QQ; is neither a native handler verb nor in the recording: the
        # not-modelled coverage gap answers '?;' and notifies the observer.
        writer.write(b"QQ;")
        assert await asyncio.wait_for(reader.readuntil(b";"), 3.0) == b"?;"
        # A malformed run is refused too, but is not a coverage gap.
        writer.write(b"1;")
        assert await asyncio.wait_for(reader.readuntil(b";"), 3.0) == b"?;"
    finally:
        writer.close()
    assert fake.model.not_modelled == {"QQ;": 1}
    assert seen == ["QQ;"]


def test_banner_fidelity_is_A_for_the_committed_ledger() -> None:
    assert fidelity_for_banner(_fake(), LEDGER) == "A"


def _banner_fake(tmp_path, fixture_only: bool) -> YaesuCatSerialFake:
    if not fixture_only:
        return _fake()
    recording = Path("tests/fixtures/ftx1/cat_pairs.jsonl")
    for relative in (recording, adapter._RIG_REL):
        target = tmp_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / relative).read_bytes())
    profile = tmp_path / "fixture-only.toml"
    profile.write_text(
        'schema = "rigplane-sim-profile/1"\n'
        "[sim]\n"
        'id = "fixture-only"\n'
        'radio = "FTX-1"\n'
        'fidelity = "A"\n'
        f'evidence = ["{recording.as_posix()}"]\n'
        "[transport.yaesu_cat]\n"
        "baud = 38400\n"
        "[answers]\n"
        f'recorded = [{{ file = "{recording.as_posix()}", transport = "usb" }}]\n',
        encoding="utf-8",
    )
    return YaesuCatSerialFake.from_profile(tmp_path, profile)


@pytest.mark.parametrize("ledger_request", ["ID;", "QQ;"])
@pytest.mark.parametrize(
    ("fixture_only", "tier", "expected"),
    [(True, "A", "C"), (False, "A", "B"), (False, "B", "B"), (False, "C", "C")],
)
def test_banner_fidelity_never_upgrades_on_a_stale_ledger_entry(
    tmp_path, ledger_request, fixture_only, tier, expected
) -> None:
    fake = _banner_fake(tmp_path, fixture_only)
    fake._profile = replace(
        fake._profile, sim=replace(fake._profile.sim, fidelity=tier)
    )
    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps({ledger_request: {"recorded": "ID0840;", "model": "ID9999;"}}),
        encoding="utf-8",
    )
    assert adapter.unledgered_divergences(
        lambda table: adapter._fresh_seeded_model(fake, table), fake._recorded, ledger
    ) == {ledger_request: None}
    assert fidelity_for_banner(fake, ledger) == expected


def test_banner_fidelity_is_A_with_no_divergences_or_ledger_entries(tmp_path) -> None:
    ledger = tmp_path / "ledger.json"
    ledger.write_text("{}", encoding="utf-8")
    assert fidelity_for_banner(_fake(), ledger) == "A"


@pytest.mark.parametrize(
    ("fixture_only", "tier", "expected"),
    [(True, "A", "C"), (False, "A", "B"), (False, "B", "B"), (False, "C", "C")],
)
def test_banner_fidelity_never_upgrades_on_an_unledgered_divergence(
    monkeypatch, tmp_path, fixture_only, tier, expected
) -> None:
    fake = _banner_fake(tmp_path, fixture_only)
    fake._profile = replace(
        fake._profile, sim=replace(fake._profile.sim, fidelity=tier)
    )
    real_fresh = adapter._fresh_seeded_model

    def _mutating(fake: YaesuCatSerialFake, table) -> object:
        model = real_fresh(fake, table)
        original = model.handle

        def handle(line: str):
            if line == "ID;":
                return "ID9999;"
            return original(line)

        model.handle = handle
        return model

    monkeypatch.setattr(adapter, "_fresh_seeded_model", _mutating)
    assert fidelity_for_banner(fake, LEDGER) == expected


@pytest.mark.parametrize(
    ("recorded_reply", "model_reply", "expected"),
    [
        ("ID0840;", "ID9999;", "A"),
        ("ID0840;", "ID1234;", "B"),
        ("ID1234;", "ID9999;", "B"),
    ],
)
def test_banner_requires_both_ledger_values_to_match(
    monkeypatch, tmp_path, recorded_reply, model_reply, expected
) -> None:
    fake = _fake()
    real_fresh = adapter._fresh_seeded_model

    def _mutating(fake: YaesuCatSerialFake, table):
        model = real_fresh(fake, table)
        original = model.handle
        model.handle = lambda line: "ID9999;" if line == "ID;" else original(line)
        return model

    ledger = tmp_path / "ledger.json"
    ledger.write_text(
        json.dumps({"ID;": {"recorded": recorded_reply, "model": model_reply}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(adapter, "_fresh_seeded_model", _mutating)
    assert fidelity_for_banner(fake, ledger) == expected


@pytest.mark.parametrize(
    "content",
    [
        "",
        " \t\n\n ",
        '{"dir": "r2c", "line": "ID0840;"}\n',
        '{"dir": "c2r", "line": "ID;"}\n',
        '{"dir": "c2r", "line": "ID;"}\n{"dir": "r2c", "line": "FA147225000;"}\n',
    ],
    ids=["empty", "whitespace", "unsolicited-only", "request-only", "unpaired"],
)
def test_recording_requires_request_reply_evidence(tmp_path, content) -> None:
    profile = adapter.load_sim_profile(ROOT / adapter._PROFILE_REL, root=ROOT)
    recording = tmp_path / profile.answers.recorded[0][0]
    recording.parent.mkdir(parents=True)
    recording.write_text(content, encoding="utf-8")
    with pytest.raises(ValueError) as error:
        adapter._recorded_table(tmp_path, profile)
    assert str(recording) in str(error.value)


@pytest.mark.parametrize(
    ("fixture_only", "tier", "expected"),
    [(True, "A", "C"), (False, "A", "B"), (False, "B", "B"), (False, "C", "C")],
)
def test_empty_table_cannot_pass_conformance_or_upgrade_banner(
    tmp_path, fixture_only, tier, expected
) -> None:
    fake = _banner_fake(tmp_path, fixture_only)
    fake._profile = replace(
        fake._profile, sim=replace(fake._profile.sim, fidelity=tier)
    )
    fake._recorded = RecordedCatAnswerTable()
    with pytest.raises(ValueError, match="no request/reply evidence"):
        adapter.recorded_divergences(fake.model, fake._recorded)
    assert fidelity_for_banner(fake, LEDGER) == expected


def test_adapter_imports_only_the_allowed_modules() -> None:
    tree = ast.parse(ADAPTER_PATH.read_text())
    modules: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
            names.update(alias.name for alias in node.names)
    # The production parser/formatter must not generate the replies.
    assert "CatCommandParser" not in names
    assert "format_command" not in names
    for module in modules:
        assert not module.startswith("rigplane.backends"), module
        assert not module.startswith("rigplane.commands"), module
        assert module.split(".")[0] != "tests", module
        if module == "rigplane" or module.startswith("rigplane."):
            assert module.startswith("rigplane.profiles"), module


class _DeliveryClock:
    def __init__(self) -> None:
        self.now = 10.0
        self.handles: list[asyncio.TimerHandle] = []

    def time(self) -> float:
        return self.now

    def call_at(self, when, callback) -> asyncio.TimerHandle:
        handle = asyncio.TimerHandle(when, callback, (), asyncio.get_running_loop())
        self.handles.append(handle)
        return handle

    @property
    def active(self) -> list[asyncio.TimerHandle]:
        return [handle for handle in self.handles if not handle.cancelled()]

    def advance(self, seconds: float) -> None:
        self.now += seconds
        while due := [handle for handle in self.active if handle.when() <= self.now]:
            handle = min(due, key=lambda handle: handle.when())
            self.handles.remove(handle)
            handle._run()


def _with_latency(milliseconds: float | None) -> YaesuCatSerialFake:
    fake = _fake()
    return YaesuCatSerialFake(
        fake.model,
        profile=replace(
            fake._profile,
            timing=replace(fake._profile.timing, reply_latency_ms=milliseconds),
        ),
        rig=fake._rig,
        recorded=fake._recorded,
    )


@pytest.mark.parametrize(
    "milliseconds", [float("inf"), float("-inf"), float("nan"), -1, True, False, "9"]
)
def test_constructor_rejects_invalid_reply_latency(milliseconds) -> None:
    with pytest.raises(ValueError, match=r"^profile timing\.reply_latency_ms:"):
        _with_latency(milliseconds)


@pytest.mark.parametrize("milliseconds", [None, 0, 9, 250.5])
def test_constructor_accepts_absent_or_finite_nonnegative_latency(milliseconds) -> None:
    fake = _with_latency(milliseconds)
    assert fake._reply_latency == (milliseconds or 0.0) / 1000.0


def test_factory_rejects_infinite_profile_latency(tmp_path) -> None:
    profile = tmp_path / "latency.toml"
    profile.write_text(
        (ROOT / adapter._PROFILE_REL)
        .read_text()
        .replace("reply_latency_ms = 9", "reply_latency_ms = inf")
    )
    with pytest.raises(ValueError, match=r"^profile timing\.reply_latency_ms:"):
        YaesuCatSerialFake.from_profile(ROOT, profile)


@pytest.mark.parametrize("method", ["silence", "usb_vanish", "usb_reappear"])
@pytest.mark.parametrize(
    "duration", [float("inf"), float("-inf"), float("nan"), True, "1"]
)
def test_fault_durations_reject_invalid_values_before_scheduling(
    method, duration
) -> None:
    fake = _fake()
    with pytest.raises(ValueError, match=rf"^{method} "):
        getattr(fake, method)(duration)
    assert fake._silent_until == 0.0
    assert not fake._vanished


@pytest.mark.parametrize("method", ["silence", "usb_vanish", "usb_reappear"])
@pytest.mark.parametrize("duration", [-1, 0, 0.5])
async def test_fault_durations_accept_finite_values(
    method, duration, monkeypatch
) -> None:
    fake = _fake()
    scheduled = []
    monkeypatch.setattr(
        asyncio.get_running_loop(),
        "call_later",
        lambda delay, callback: scheduled.append(delay),
    )
    monkeypatch.setattr(adapter.time, "monotonic", lambda: 10.0)
    getattr(fake, method)(duration)
    if method == "silence":
        assert fake._silent_until == 10.0 + max(0.0, duration)
        assert not scheduled
    else:
        assert scheduled == [max(0.0, duration)]


async def _scheduled_connection(fake):
    reader, writer = await fake.open_serial_connection()
    connection = writer._connection
    clock = _DeliveryClock()
    connection._loop = clock
    return reader, writer, connection, clock


async def test_reply_latency_comes_from_loaded_profile(tmp_path) -> None:
    profile = tmp_path / "latency.toml"
    profile.write_text(
        (ROOT / adapter._PROFILE_REL)
        .read_text()
        .replace("reply_latency_ms = 9", "reply_latency_ms = 250")
    )
    fake = YaesuCatSerialFake.from_profile(ROOT, profile)
    reader, writer = await fake.open_serial_connection()
    connection = writer._connection
    assert connection._loop is asyncio.get_running_loop()
    writer.write(b"ID;")
    assert isinstance(connection._timer, asyncio.TimerHandle)
    assert not reader._buffer
    assert connection._latency == 0.25
    writer.close()

    reader, writer, connection, clock = await _scheduled_connection(fake)
    writer.write(b"ID;")
    assert len(clock.active) == 1
    clock.advance(0.249)
    assert not reader._buffer
    clock.advance(0.002)
    assert bytes(reader._buffer) == b"ID0840;"
    assert connection._timer is None
    assert not connection._pending
    assert not clock.active
    writer.close()


@pytest.mark.parametrize("chunks", [(b"ID;FA;MD0;",), (b"ID;", b"FA;", b"MD0;")])
async def test_delayed_replies_keep_fifo_at_equal_deadlines(chunks) -> None:
    fake = _with_latency(100)
    reader, writer, connection, clock = await _scheduled_connection(fake)
    for chunk in chunks:
        writer.write(chunk)
    assert len(clock.active) == 1
    assert len({due for due, _ in connection._pending}) == 1
    assert not reader._buffer
    clock.advance(0.101)
    assert bytes(reader._buffer) == b"ID0840;FA147225000;MD04;"
    assert not clock.active
    assert connection._timer is None
    writer.close()


async def test_separate_deadlines_schedule_only_the_fifo_head() -> None:
    fake = _with_latency(100)
    reader, writer, connection, clock = await _scheduled_connection(fake)
    writer.write(b"ID;")
    clock.advance(0.05)
    writer.write(b"FA;")
    assert len(clock.active) == 1
    clock.advance(0.051)
    assert bytes(reader._buffer) == b"ID0840;"
    assert len(clock.active) == 1
    clock.advance(0.05)
    assert bytes(reader._buffer) == b"ID0840;FA147225000;"
    assert connection._timer is None
    assert not clock.active
    writer.close()


@pytest.mark.parametrize("retire", ["close", "fail", "vanish", "gc"])
async def test_retirement_drops_pending_delivery(retire) -> None:
    fake = _with_latency(100)
    reader, writer, connection, clock = await _scheduled_connection(fake)
    writer.write(b"ID;")
    handle = connection._timer
    fake_ref = weakref.ref(fake)
    if retire == "close":
        writer.close()
    elif retire == "fail":
        connection.fail(OSError("direct failure"))
    elif retire == "vanish":
        fake._vanish_now()
        fake._reappear_now()
        new_reader, new_writer, _, new_clock = await _scheduled_connection(fake)
        new_writer.write(b"FA;")
        new_clock.advance(0.101)
        assert bytes(new_reader._buffer) == b"FA147225000;"
        new_writer.close()
    else:
        del fake
        gc.collect()
        assert fake_ref() is None
    if retire != "gc":
        assert not fake._connections
    assert handle.cancelled()
    assert connection._timer is None
    assert not connection._pending
    assert not clock.active
    clock.advance(1)
    assert not reader._buffer
    if retire in ("fail", "vanish"):
        with pytest.raises(OSError):
            await reader.read()
    else:
        assert reader.at_eof()
    writer.close()


@pytest.mark.parametrize("retire", ["close", "fail"])
async def test_retired_sessions_are_not_retained_by_live_fake(retire) -> None:
    fake = _fake()
    for _ in range(5):
        reader, writer = await fake.open_serial_connection()
        connection_ref = weakref.ref(writer._connection)
        assert len(fake._connections) == 1
        if retire == "close":
            writer.close()
        else:
            writer._connection.fail(OSError("direct failure"))
        assert not fake._connections
        del reader, writer
        gc.collect()
        assert connection_ref() is None
    assert fake.open_count == 5


async def test_closed_session_keeps_eof_after_repeated_retirement_and_vanish() -> None:
    fake = _fake()
    reader, writer = await fake.open_serial_connection()
    writer.close()
    writer.close()
    writer._connection.fail(OSError("late failure"))
    fake._vanish_now()
    assert not fake._connections
    assert writer._connection.error is None
    assert reader.exception() is None
    assert reader.at_eof()
    assert await reader.read() == b""


async def test_vanish_fails_every_live_session_and_retires_them() -> None:
    fake = _fake()
    sessions = [await fake.open_serial_connection() for _ in range(3)]
    assert len(fake._connections) == len(sessions)
    fake._vanish_now()
    assert not fake._connections
    for reader, writer in sessions:
        error = reader.exception()
        assert isinstance(error, OSError)
        assert error.errno == adapter._VANISH_ERRNO
        with pytest.raises(OSError) as raised:
            await reader.read()
        assert raised.value is error
        writer._connection.fail(OSError("late failure"))
        writer.close()
        writer.close()
        assert reader.exception() is error


@pytest.mark.parametrize("milliseconds", [None, 0.0])
async def test_absent_or_zero_latency_delivers_synchronously(milliseconds) -> None:
    fake = _with_latency(milliseconds)
    reader, writer, connection, clock = await _scheduled_connection(fake)
    writer.write(b"ID;FA;")
    assert bytes(reader._buffer) == b"ID0840;FA147225000;"
    assert connection._timer is None
    assert not clock.active
    writer.close()


@pytest.mark.parametrize("wire_bytes", [b"QQ;", b"\xff;", b"X" * 65, b"ID;"])
async def test_refusal_paths_share_reply_latency(wire_bytes, monkeypatch) -> None:
    fake = _with_latency(100)
    if wire_bytes == b"ID;":

        def broken_model(_line):
            raise RuntimeError("model failure")

        monkeypatch.setattr(fake.model, "handle", broken_model)
    reader, writer, _, clock = await _scheduled_connection(fake)
    writer.write(wire_bytes)
    assert not reader._buffer
    clock.advance(0.101)
    assert bytes(reader._buffer) == b"?;"
    assert not clock.active
    writer.close()


async def test_silence_preserves_scheduled_replies_but_suppresses_new_lines() -> None:
    fake = _with_latency(100)
    reader, writer, _, clock = await _scheduled_connection(fake)
    writer.write(b"ID;")
    fake.silence(60)
    writer.write(b"FB007074000;FA;")
    clock.advance(0.101)
    assert bytes(reader._buffer) == b"ID0840;"
    assert fake.model.engine.state.receivers[1].frequency_hz == 52_900_000
    assert not clock.active
    writer.close()
