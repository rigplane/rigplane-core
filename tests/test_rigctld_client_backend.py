"""Tests for the external Hamlib ``rigctld`` client backend."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

import fake_rigctld
from fake_rigctld import FakeRigctldBehavior, FakeRigctldServer
from rigplane.backends.config import RigctldBackendConfig
from rigplane.backends.factory import create_radio
from rigplane.backends.rigctld_client import RigctldClientRadio, RigctldTransport
from rigplane.backends.rigctld_client.radio import (
    _float_to_level_255,
    _level_255_to_float,
    _preamp_level_to_db,
)
from rigplane.exceptions import CommandError
from rigplane.exceptions import ConnectionError as RadioConnectionError
from rigplane.exceptions import TimeoutError as RadioTimeoutError


def test_supports_command_gates_vfo_operations_on_observed_provider_support() -> None:
    radio = RigctldClientRadio(host="127.0.0.1", port=4532)

    assert radio.supports_command("get_freq")
    assert not radio.supports_command("get_vfo_slot")
    assert not radio.supports_command("set_vfo_slot")
    assert not radio.supports_command("unknown_operation")

    radio._vfo_supported = True
    assert radio.supports_command("get_vfo_slot")
    assert radio.supports_command("set_vfo_slot")


async def test_transport_connect_query_and_close() -> None:
    async with FakeRigctldServer() as server:
        transport = RigctldTransport(host=server.host, port=server.port)

        await transport.connect()
        try:
            assert transport.connected
            assert await transport.query("f", response_lines=1) == ["14074000"]
            assert transport.connected
        finally:
            await transport.close()

        assert not transport.connected


async def test_transport_serializes_requests() -> None:
    behavior = FakeRigctldBehavior(command_delays={"f": 0.02})

    async with FakeRigctldServer(behavior=behavior) as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        try:
            results = await asyncio.gather(
                transport.query("f", response_lines=1),
                transport.query("t", response_lines=1),
            )
        finally:
            await transport.close()

    assert results == [["14074000"], ["0"]]
    assert server.commands_seen == ["f", "t"]


async def test_transport_timeout_eof_malformed_and_negative_rprt() -> None:
    timeout_behavior = FakeRigctldBehavior(command_delays={"f": 0.2})
    async with FakeRigctldServer(behavior=timeout_behavior) as server:
        transport = RigctldTransport(
            host=server.host,
            port=server.port,
            timeout=0.01,
        )
        await transport.connect()
        try:
            with pytest.raises(RadioTimeoutError, match="timed out"):
                await transport.query("f", response_lines=1)
        finally:
            await transport.close()

    eof_behavior = FakeRigctldBehavior(disconnect_commands={"f"})
    async with FakeRigctldServer(behavior=eof_behavior) as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        try:
            with pytest.raises(RadioConnectionError, match="closed"):
                await transport.query("f", response_lines=1)
        finally:
            await transport.close()

    malformed_behavior = FakeRigctldBehavior(malformed_responses={"F": b"nope\n"})
    async with FakeRigctldServer(behavior=malformed_behavior) as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        try:
            with pytest.raises(CommandError, match="malformed"):
                await transport.command("F 14074000")
        finally:
            await transport.close()

    unsupported_behavior = FakeRigctldBehavior(unsupported_commands={"F"})
    async with FakeRigctldServer(behavior=unsupported_behavior) as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        try:
            with pytest.raises(CommandError, match="unsupported"):
                await transport.command("F 14074000")
        finally:
            await transport.close()

    unsupported_query = FakeRigctldBehavior(unsupported_commands={"m"})
    async with FakeRigctldServer(behavior=unsupported_query) as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        try:
            with pytest.raises(CommandError, match="unsupported"):
                await transport.query("m", response_lines=2)
        finally:
            await transport.close()


@pytest.mark.parametrize("code", [0, -1, -5, -6, -8, -37, 1])
async def test_transport_command_accepts_only_rprt_zero_and_preserves_failure_code(
    code: int,
) -> None:
    behavior = FakeRigctldBehavior(
        malformed_responses={"F": f"RPRT {code}\n".encode("ascii")}
    )
    async with FakeRigctldServer(behavior=behavior) as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        try:
            if code == 0:
                await transport.command("F 14074000")
            else:
                with pytest.raises(CommandError) as exc_info:
                    await transport.command("F 14074000")
                assert exc_info.value.command == "F 14074000"
                assert exc_info.value.code == code
            assert transport.connected
        finally:
            await transport.close()


class _ExchangeStream:
    def __init__(self, phase: str = "read", *, hold_close: bool = False) -> None:
        self.phase = phase
        self.entered = asyncio.Event()
        self.release = asyncio.Event()
        self.close_entered = asyncio.Event()
        self.close_release = asyncio.Event()
        if not hold_close:
            self.close_release.set()
        self.responses: asyncio.Queue[bytes | Exception] = asyncio.Queue()
        self.cancel_on_entry: asyncio.Task | None = None
        self.writes: list[bytes] = []
        self.closes = 0
        self.reads = 0

    def _enter(self) -> None:
        self.entered.set()
        if self.cancel_on_entry is not None:
            self.cancel_on_entry.cancel("cancel exchange")

    async def read(self, size: int) -> bytes:
        if self.phase == "stale":
            self._enter()
            await self.release.wait()
        raise TimeoutError

    async def readline(self) -> bytes:
        self.reads += 1
        if self.phase == "resync" and self.reads == 1:
            return b"stray\n"
        self._enter()
        response = await self.responses.get()
        if isinstance(response, Exception):
            raise response
        return response

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        if self.phase == "write":
            self._enter()
            await self.release.wait()

    def is_closing(self) -> bool:
        return bool(self.closes)

    def close(self) -> None:
        self.closes += 1

    async def wait_closed(self) -> None:
        self.close_entered.set()
        await self.close_release.wait()


async def _connect_exchange(
    monkeypatch: pytest.MonkeyPatch, *streams: _ExchangeStream
) -> RigctldTransport:
    available = iter(streams)

    async def connect(*args: object) -> tuple[_ExchangeStream, _ExchangeStream]:
        stream = next(available)
        return stream, stream

    monkeypatch.setattr(asyncio, "open_connection", connect)
    transport = RigctldTransport(host="127.0.0.1")
    await transport.connect()
    return transport


async def _finish_exchanges(
    transport: RigctldTransport,
    streams: tuple[_ExchangeStream, ...],
    tasks: list[asyncio.Task],
) -> None:
    for stream in streams:
        stream.release.set()
        stream.close_release.set()
    for task in tasks:
        if not task.done():
            task.cancel()
    await asyncio.wait_for(asyncio.gather(*tasks, return_exceptions=True), 1)
    await asyncio.wait_for(transport.close(), 1)


@pytest.mark.parametrize(
    ("operation", "phase"),
    [
        ("command", "read"),
        ("query", "read"),
        ("command", "stale"),
        ("query", "stale"),
        ("command", "resync"),
        ("command", "write"),
    ],
)
async def test_cancelled_exchange_quarantines_before_close_barrier(
    operation: str, phase: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    stream = _ExchangeStream(phase, hold_close=True)
    replacement = _ExchangeStream()
    transport = await _connect_exchange(monkeypatch, stream, replacement)
    notifications = []

    def advance() -> int:
        notifications.append((transport.connected, stream.closes))
        if phase == "write":
            raise RuntimeError("callback failed")
        return len(notifications)

    transport.bind_provider_generation(advance=advance)
    request = (
        transport.query("f", response_lines=1)
        if operation == "query"
        else transport.command("T 1")
    )
    tasks = [asyncio.create_task(request)]
    stream.cancel_on_entry = tasks[0]
    try:
        await asyncio.wait_for(stream.entered.wait(), 1)
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(tasks[0], 1)
        expected_write = b"f\n" if operation == "query" else b"T 1\n"
        assert stream.writes == ([] if phase == "stale" else [expected_write])
        assert stream.closes == 1 and not transport.connected
        assert notifications == [(False, 1)]
        assert not stream.close_entered.is_set()
        finish = transport.connect() if operation == "query" else transport.close()
        tasks.append(asyncio.create_task(finish))
        await asyncio.wait_for(stream.close_entered.wait(), 1)
        assert not tasks[-1].done()
        stream.close_release.set()
        await asyncio.wait_for(tasks[-1], 1)
        assert stream.closes == 1 and notifications == [(False, 1)]
        if operation == "query":
            assert transport._writer is replacement and transport.connected
    finally:
        await _finish_exchanges(transport, (stream, replacement), tasks)


async def test_cancelled_lock_waiter_keeps_active_exchange(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stream = _ExchangeStream()
    transport = await _connect_exchange(monkeypatch, stream)
    tasks = [asyncio.create_task(transport.command("T 1"))]
    queued = asyncio.Event()

    async def second() -> None:
        queued.set()
        await transport.command("T 0")

    try:
        await asyncio.wait_for(stream.entered.wait(), 1)
        tasks.append(asyncio.create_task(second()))
        await asyncio.wait_for(queued.wait(), 1)
        tasks[-1].cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(tasks[-1], 1)
        assert transport.connected and stream.closes == 0
        assert stream.writes == [b"T 1\n"]
        stream.responses.put_nowait(b"RPRT 0\n")
        await asyncio.wait_for(tasks[0], 1)
        assert transport.connected
    finally:
        await _finish_exchanges(transport, (stream,), tasks)


@pytest.mark.parametrize("interruption", ["cancel", "eof", "oserror", "timeout"])
async def test_old_exchange_interruption_does_not_retire_replacement(
    interruption: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    old, new = _ExchangeStream(), _ExchangeStream()
    transport = await _connect_exchange(monkeypatch, old, new)
    notifications = []
    transport.bind_provider_generation(
        advance=lambda: notifications.append(transport.connected)
        or len(notifications)
    )
    tasks = [asyncio.create_task(transport.query("f", response_lines=1))]
    try:
        await asyncio.wait_for(old.entered.wait(), 1)
        await transport.close()
        await transport.connect()
        assert transport._writer is new
        if interruption == "cancel":
            tasks[0].cancel()
            expected = asyncio.CancelledError
        else:
            response = {
                "eof": b"",
                "oserror": OSError("lost"),
                "timeout": TimeoutError(),
            }[interruption]
            old.responses.put_nowait(response)
            expected = (
                RadioTimeoutError
                if interruption == "timeout"
                else RadioConnectionError
            )
        with pytest.raises(expected):
            await asyncio.wait_for(tasks[0], 1)
        assert transport.connected and new.closes == 0
        assert notifications == [False]
    finally:
        await _finish_exchanges(transport, (old, new), tasks)


async def test_delayed_cancelled_rprt_cannot_complete_next_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered, release, next_written = asyncio.Event(), asyncio.Event(), asyncio.Event()
    write_response = fake_rigctld._write_response

    async def delayed_response(writer: asyncio.StreamWriter, data: bytes) -> None:
        if data == b"RPRT 0\n" and not entered.is_set():
            entered.set()
            await release.wait()
        await write_response(writer, data)

    monkeypatch.setattr(fake_rigctld, "_write_response", delayed_response)
    behavior = FakeRigctldBehavior(malformed_responses={"T 0": b"RPRT -5\n"})
    async with FakeRigctldServer(behavior=behavior) as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        tasks = [asyncio.create_task(transport.command("T 1"))]
        try:
            await asyncio.wait_for(entered.wait(), 1)
            tasks[0].cancel()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(tasks[0], 1)
            await transport.connect()
            writer = transport._writer
            assert writer is not None
            write = writer.write

            def observe_write(data: bytes) -> None:
                write(data)
                next_written.set()

            monkeypatch.setattr(writer, "write", observe_write)
            tasks.append(asyncio.create_task(transport.command("T 0")))
            await asyncio.wait_for(next_written.wait(), 1)
            release.set()
            with pytest.raises(CommandError) as caught:
                await asyncio.wait_for(tasks[-1], 1)
            assert caught.value.code == -5
        finally:
            release.set()
            await _finish_exchanges(transport, (), tasks)


@pytest.mark.parametrize(
    "failure",
    "stale_eof stale_oserror resync_eof resync_oserror read_timeout "
    "write_timeout write_oserror".split(),
)
async def test_radio_transport_loss_retires_exactly_once(
    failure: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with FakeRigctldServer() as server:
        timeout = 0.01 if failure == "read_timeout" else 5.0
        radio = RigctldClientRadio(host=server.host, port=server.port, timeout=timeout)
        await radio.connect()
        transitions: list[bool] = []
        radio.bind_provider_generation(
            advance=lambda: transitions.append(radio.connected) or len(transitions)
        )
        reader = radio._transport._reader  # noqa: SLF001
        writer = radio._transport._writer  # noqa: SLF001
        assert reader is not None and writer is not None
        writes: list[bytes] = []
        monkeypatch.setattr(writer, "write", writes.append)
        read_result = OSError("read lost") if failure.endswith("oserror") else b""
        failing_read = AsyncMock(side_effect=[read_result])
        if failure.startswith("stale"):
            monkeypatch.setattr(reader, "read", failing_read)
        elif failure.startswith("resync"):
            monkeypatch.setattr(reader, "read", AsyncMock(side_effect=TimeoutError))
            monkeypatch.setattr(
                reader, "readline", AsyncMock(side_effect=[b"stray\n", read_result])
            )
        elif failure.startswith("write"):
            error = TimeoutError if failure.endswith("timeout") else OSError("lost")
            monkeypatch.setattr(writer, "drain", AsyncMock(side_effect=error))
        error_type = (
            RadioTimeoutError if failure.endswith("timeout") else RadioConnectionError
        )
        operation = (
            radio.get_freq()
            if failure.startswith("stale") or failure == "read_timeout"
            else radio.set_freq(7_050_000)
        )
        with pytest.raises(error_type):
            await operation
        expected_write = b"f\n" if failure == "read_timeout" else b"F 7050000\n"
        expected = [] if failure.startswith("stale") else [expected_write]
        assert writes == expected and transitions == [False]
        await radio.disconnect()
        assert transitions == [False]


async def test_radio_reconnect_does_not_double_advance_transport_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    behavior = FakeRigctldBehavior(disconnect_commands={"f"})
    async with FakeRigctldServer(behavior=behavior) as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        advance = MagicMock(return_value=1)
        radio.bind_provider_generation(advance=advance)
        with pytest.raises(RadioConnectionError):
            await radio.get_freq()
        assert advance.call_count == 1
        with pytest.raises(RadioConnectionError, match="not connected"):
            await radio.get_freq()
        await radio.disconnect()
        assert advance.call_count == 1
        await radio.connect()
        assert radio.connected and advance.call_count == 1
        old_writer = radio._transport._writer  # noqa: SLF001
        assert old_writer is not None
        close = MagicMock(side_effect=old_writer.close)
        monkeypatch.setattr(old_writer, "is_closing", lambda: True)
        monkeypatch.setattr(old_writer, "close", close)
        monkeypatch.setattr(asyncio, "open_connection", AsyncMock(side_effect=OSError))
        with pytest.raises(RadioConnectionError):
            await radio.connect()
        assert not radio.connected
        monkeypatch.undo()
        await radio.connect()
        assert close.call_count == 1
        assert advance.call_count == 2
        await radio.disconnect()
        assert advance.call_count == 3
        await radio.disconnect()
        assert advance.call_count == 3


async def test_transport_concurrent_connect_owns_one_cycle(monkeypatch) -> None:
    transport = RigctldTransport(host="127.0.0.1")
    entered, release = asyncio.Event(), asyncio.Event()
    writers: list[MagicMock] = []

    async def open_connection(*args: object) -> tuple[MagicMock, MagicMock]:
        if not writers:
            entered.set()
            await release.wait()
        writer = MagicMock()
        writer.is_closing.return_value = False
        writer.wait_closed = AsyncMock()
        writers.append(writer)
        return MagicMock(), writer

    monkeypatch.setattr(asyncio, "open_connection", open_connection)
    advance = MagicMock(return_value=1)
    transport.bind_provider_generation(advance=advance)
    tasks = [asyncio.create_task(transport.connect())]
    await entered.wait()
    tasks.append(asyncio.create_task(transport.connect()))
    await asyncio.sleep(0)
    release.set()
    await asyncio.gather(*tasks)
    assert len(writers) == 1 and advance.call_count == 0
    await transport.connect()
    assert len(writers) == 1
    await transport.close()
    await transport.connect()
    await transport.close()
    assert advance.call_count == 2
    assert [writer.close.call_count for writer in writers] == [1, 1]


async def test_radio_core_frequency_mode_ptt_and_vfo() -> None:
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            assert radio.connected
            assert radio.radio_ready
            assert radio.backend_id == "rigctld"
            assert radio.model == "External rigctld"
            assert radio.capabilities == {
                "tx",
                "vfo",
                "rf_gain",
                "af_level",
                "preamp",
                "attenuator",
                "nb",
                "nr",
            }
            assert radio.supports_command("set_freq")
            assert radio.supports_command("get_vfo_slot")
            assert not radio.supports_command("start_audio_rx_opus")

            assert await radio.get_freq() == 14_074_000
            await radio.set_freq(7_050_000)
            assert radio.radio_state.main.freq == 14_074_000
            assert await radio.get_freq() == 7_050_000
            assert radio.radio_state.main.freq == 7_050_000

            assert await radio.get_mode() == ("USB", 2400)
            await radio.set_mode("LSB", 1800)
            assert radio.radio_state.main.mode == "USB"
            assert radio.radio_state.main.filter_width == 2400
            assert await radio.get_mode() == ("LSB", 1800)
            assert radio.radio_state.main.mode == "LSB"
            assert radio.radio_state.main.filter_width == 1800

            assert await radio.get_ptt() is False
            await radio.set_ptt(True)
            assert radio.radio_state.ptt is False
            assert await radio.get_ptt() is True
            assert radio.radio_state.ptt is True

            assert await radio.get_vfo_slot() == "A"
            await radio.set_vfo_slot("B")
            assert radio.radio_state.main.active_slot == "A"
            assert await radio.get_vfo_slot() == "B"
            assert radio.radio_state.main.active_slot == "B"
        finally:
            await radio.disconnect()

    assert server.commands_seen == [
        "v",
        "f",
        "F 7050000",
        "f",
        "m",
        "M LSB 1800",
        "m",
        "t",
        "T 1",
        "t",
        "v",
        "V VFOB",
        "v",
    ]


async def test_failed_core_setters_leave_radio_state_unchanged() -> None:
    behavior = FakeRigctldBehavior(unsupported_commands={"F", "M", "T", "V"})
    async with FakeRigctldServer(behavior=behavior) as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            assert await radio.get_freq() == 14_074_000
            assert await radio.get_mode() == ("USB", 2400)
            assert await radio.get_ptt() is False
            assert await radio.get_vfo_slot() == "A"

            for setter in (
                radio.set_freq(7_050_000),
                radio.set_mode("LSB", 1800),
                radio.set_ptt(True),
                radio.set_vfo_slot("B"),
            ):
                with pytest.raises(CommandError, match="unsupported"):
                    await setter
                assert radio.radio_state.main.freq == 14_074_000
                assert radio.radio_state.main.mode == "USB"
                assert radio.radio_state.main.filter_width == 2400
                assert radio.radio_state.ptt is False
                assert radio.radio_state.main.active_slot == "A"
        finally:
            await radio.disconnect()

    assert server.commands_seen == [
        "v",
        "f",
        "m",
        "t",
        "v",
        "F 7050000",
        "M LSB 1800",
        "T 1",
        "V VFOB",
    ]


async def test_radio_reports_actionable_connection_failure() -> None:
    radio = RigctldClientRadio(host="127.0.0.1", port=9, timeout=0.01)

    with pytest.raises(RadioConnectionError, match="127.0.0.1:9"):
        await radio.connect()


async def test_radio_rejects_unsupported_data_mode() -> None:
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            assert await radio.get_data_mode() is False
            with pytest.raises(CommandError, match="data mode"):
                await radio.set_data_mode(True)
        finally:
            await radio.disconnect()


async def test_get_data_mode_does_not_synthesize_private_radio_state() -> None:
    """MOR-434: a public read returns a flat value, not synthesized state.

    ``get_data_mode`` is the representative public read with no live
    rigctld query: it returns a flat ``False`` and must not fabricate or
    mutate the private ``self._state`` ``RadioState`` mirror. The consumer
    pipeline is fed by ``RigctldClientObservationAdapter`` instead; the
    ``_state`` mirror is legacy compat only and stays untouched by reads
    that have no observation to apply.
    """
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            state_before = radio.radio_state
            data_mode_before = state_before.main.data_mode

            result = await radio.get_data_mode()

            assert result is False
            # No new RadioState synthesized — same object identity.
            assert radio.radio_state is state_before
            # No mutation of the legacy private mirror.
            assert radio.radio_state.main.data_mode == data_mode_before
        finally:
            await radio.disconnect()


def test_config_factory_builds_rigctld_client_backend() -> None:
    config = RigctldBackendConfig(host="localhost")

    radio = create_radio(config)

    assert isinstance(radio, RigctldClientRadio)
    assert config.backend == "rigctld"
    assert config.port == 4532
    assert radio.backend_id == "rigctld"


def test_config_validates_rigctld_client_backend() -> None:
    with pytest.raises(ValueError, match="host"):
        RigctldBackendConfig(host="")
    with pytest.raises(ValueError, match="port"):
        RigctldBackendConfig(host="localhost", port=0)
    with pytest.raises(ValueError, match="timeout"):
        RigctldBackendConfig(host="localhost", timeout=0)


async def test_rigctld_levels_roundtrip() -> None:
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            await radio.set_rf_gain(200)
            assert abs(await radio.get_rf_gain() - 200) <= 2

            await radio.set_af_level(120)
            assert abs(await radio.get_af_level() - 120) <= 2
        finally:
            await radio.disconnect()


async def test_rigctld_preamp_attenuator_nb_nr() -> None:
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            assert await radio.get_preamp() == 0
            await radio.set_preamp(1)
            assert await radio.get_preamp() == 1

            assert await radio.get_attenuator() is False
            await radio.set_attenuator(True)
            assert await radio.get_attenuator() is True
            assert await radio.get_attenuator_level() == 6

            assert await radio.get_nb() is False
            await radio.set_nb(True)
            assert await radio.get_nb() is True

            assert await radio.get_nr() is False
            await radio.set_nr(True)
            assert await radio.get_nr() is True
        finally:
            await radio.disconnect()


def test_preamp_level_to_db_rejects_out_of_domain_level() -> None:
    """MOR-1529: an unrecognized preamp level must fail loud, not be
    silently coerced to OFF (0 dB) — this backend has no ``RigProfile`` to
    validate against (it talks to an already-running external rigctld
    daemon), so its own fixed 0/1/2 mapping must reject anything else."""
    for legal in (0, 1, 2):
        assert _preamp_level_to_db(legal) in {"0", "10", "20"}
    with pytest.raises(ValueError, match=r"preamp level must be one of"):
        _preamp_level_to_db(3)
    with pytest.raises(ValueError, match=r"preamp level must be one of"):
        _preamp_level_to_db(-1)


async def test_rigctld_set_preamp_out_of_domain_raises_not_silently_off() -> None:
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            with pytest.raises(ValueError, match=r"preamp level must be one of"):
                await radio.set_preamp(3)
        finally:
            await radio.disconnect()


async def test_rigctld_unsupported_level_raises_command_error() -> None:
    behavior = FakeRigctldBehavior(unsupported_commands={"l RF"})
    async with FakeRigctldServer(behavior=behavior) as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            with pytest.raises(CommandError):
                await radio.get_rf_gain()
        finally:
            await radio.disconnect()


async def test_rigctld_capabilities_include_levels() -> None:
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            assert {
                "rf_gain",
                "af_level",
                "preamp",
                "attenuator",
                "nb",
                "nr",
            } <= radio.capabilities
            for command in (
                "get_rf_gain",
                "set_rf_gain",
                "get_af_level",
                "set_af_level",
                "get_preamp",
                "set_preamp",
                "get_attenuator",
                "set_attenuator",
                "get_nb",
                "set_nb",
                "get_nr",
                "set_nr",
            ):
                assert radio.supports_command(command)
        finally:
            await radio.disconnect()


def test_level_scale_conversions_roundtrip_and_clamp() -> None:
    # Round-trip: a 0..255 level survives encode->decode within rounding.
    for level in (0, 50, 128, 200, 255):
        encoded = _level_255_to_float(level)
        assert abs(_float_to_level_255(float(encoded)) - level) <= 1

    # Clamp at boundaries.
    assert _level_255_to_float(-10) == "0.000"
    assert _level_255_to_float(999) == "1.000"
    assert _float_to_level_255(-1.0) == 0
    assert _float_to_level_255(2.0) == 255
    assert _float_to_level_255(0.0) == 0
    assert _float_to_level_255(1.0) == 255


# ---------------------------------------------------------------------------
# Stale-buffer / re-sync hardening tests (MOR-182)
# ---------------------------------------------------------------------------


async def test_command_drains_stray_preceding_line() -> None:
    """SET command must succeed even when a stray value line precedes RPRT 0.

    Regression: L AF 0.784 → server sends "0.0392157\\nRPRT 0\\n"; transport
    used to read only one line, consuming the stray value and then
    _parse_rprt("0.0392157") raised CommandError.
    """
    behavior = FakeRigctldBehavior(extra_lines={"L AF 0.784": b"0.0392157\n"})
    async with FakeRigctldServer(behavior=behavior) as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        try:
            # Must NOT raise; real RPRT 0 follows the stray line.
            await transport.command("L AF 0.784")
        finally:
            await transport.close()


async def test_leftover_line_discarded_between_transactions() -> None:
    """A leftover line in the buffer from transaction A must not corrupt B.

    Simulates the U NB 1 → "0\\nRPRT 0\\n" scenario: if transaction A
    somehow leaves a line in the reader, the pre-drain in transaction B
    eats it so B reads its own RPRT 0.
    """
    behavior = FakeRigctldBehavior(extra_lines={"U NB 1": b"0\n"})
    async with FakeRigctldServer(behavior=behavior) as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        try:
            # First call: server sends "0\nRPRT 0\n".  With the re-sync loop
            # inside command() this should succeed.
            await transport.command("U NB 1")
            # Second call with a normal command must still work (no leftover
            # from the first lingering in the buffer).
            await transport.command("U NB 0")
        finally:
            await transport.close()


async def test_get_reads_value_after_drain() -> None:
    """GET (query) path is unaffected by the pre-drain (no leftover → no-op)."""
    async with FakeRigctldServer() as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        try:
            result = await transport.query("l AF", response_lines=1)
        finally:
            await transport.close()
    assert result == ["0.300"]


async def test_negative_rprt_still_raises() -> None:
    """l RF → RPRT -11 (unsupported) must still raise CommandError.

    The re-sync loop must not discard RPRT-shaped lines — it must accept
    them immediately so _raise_rprt can fire.
    """
    behavior = FakeRigctldBehavior(unsupported_commands={"l RF"})
    async with FakeRigctldServer(behavior=behavior) as server:
        transport = RigctldTransport(host=server.host, port=server.port)
        await transport.connect()
        try:
            with pytest.raises(CommandError, match="command failed|unsupported"):
                await transport.command("l RF")
        finally:
            await transport.close()
