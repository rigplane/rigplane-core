"""Serial ownership at the real POSIX open-to-asyncio handoff (MOR-3470)."""

from __future__ import annotations

import asyncio
import errno
import os
import sys
from types import SimpleNamespace

import pytest
import serial

from rigplane.backends.ic705 import Ic705SerialRadio
from rigplane.backends.yaesu_cat import YaesuCatRadio
from rigplane.backends.yaesu_cat.transport import CatTransportError
from rigplane.core.exceptions import ConnectionError as RadioConnectionError
from rigplane.core.serial_open import _open_with_idle_lines, open_serial_port
from rigplane.core.tx_observation import ObservedPtt, project_observed_ptt
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.testing.timing import virtual_clock
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import attach_managed_tx_composition
from test_managed_tx_http_route import _reader, _Writer
from test_managed_tx_production_composition import RecordingActuator

if os.name != "posix":
    pytest.skip("serial POSIX lifecycle test", allow_module_level=True)

serialposix = pytest.importorskip("serial.serialposix")
serial_asyncio = pytest.importorskip("serial_asyncio")


class _TerminalIO:
    port = "/dev/rigplane-test-only"

    def __init__(self):
        self.instance = None
        self.owned = set()
        self.opened = []
        self.closed = []
        self.configurations = []
        self.line_writes = []
        self.readers = set()
        self.open_error = None
        self.open_attempts = 0
        self.fail_configuration = None
        self.set_attributes_error = None
        self.close_failure = None
        self.close_observed = {}
        self.error = serialposix.termios.error(errno.EIO, "injected terminal failure")
        self.attributes = [
            0,
            0,
            0,
            0,
            serialposix.termios.B9600,
            serialposix.termios.B9600,
            [0] * serialposix.termios.NCCS,
        ]

    def _allocate(self):
        fd = 10000 + len(self.opened)
        self.opened.append(fd)
        self.owned.add(fd)
        return fd

    def open(self, path, flags):
        assert path == self.port
        assert flags == os.O_RDWR | os.O_NOCTTY | os.O_NONBLOCK
        self.open_attempts += 1
        if self.open_error is not None:
            raise self.open_error
        # Observe the real caller without replacing Serial construction/open.
        caller = sys._getframe(1)
        assert caller.f_code is serialposix.Serial.open.__code__
        self.instance = caller.f_locals["self"]
        assert type(self.instance) is serial.Serial
        assert not self.instance.is_open
        assert self.instance.dtr is False
        assert self.instance.rts is False
        return self._allocate()

    def pipe(self):
        return self._allocate(), self._allocate()

    def close(self, fd):
        assert fd in self.owned
        self.owned.remove(fd)
        self.closed.append(fd)

    def tcgetattr(self, fd):
        assert fd in self.owned
        self.configurations.append(
            (
                self.instance.is_open,
                self.instance.timeout,
                self.instance.write_timeout,
                len(self.owned),
            )
        )
        if len(self.configurations) == self.fail_configuration:
            if self.close_failure is not None:
                original_close = self.instance.close
                self.close_observed["original_close"] = original_close

                def close_then_fail():
                    self.close_observed["original_error"] = sys.exc_info()[1]
                    original_close()
                    raise self.close_failure

                self.instance.close = close_then_fail
            raise self.error
        return self.attributes[:6] + [self.attributes[6][:]]

    def tcsetattr(self, fd, when, attributes):
        assert fd in self.owned
        assert when == serialposix.termios.TCSANOW
        if self.set_attributes_error is not None:
            raise self.set_attributes_error
        self.attributes = attributes[:6] + [attributes[6][:]]

    def tcflush(self, fd, queue):
        assert fd in self.owned
        assert queue == serialposix.termios.TCIFLUSH

    def tcdrain(self, fd):
        assert fd in self.owned

    def fcntl(self, fd, command, flags):
        assert fd in self.owned
        assert command == serialposix.fcntl.F_SETFL
        assert flags == os.O_NONBLOCK
        return 0

    def ioctl(self, fd, command, bits):
        assert fd in self.owned
        assert command == serialposix.TIOCMBIC
        assert bits in (serialposix.TIOCM_DTR_str, serialposix.TIOCM_RTS_str)
        self.line_writes.append(bits)
        return 0

    def add_reader(self, fd, callback, *args):
        assert fd in self.owned
        assert fd not in self.readers
        self.readers.add(fd)

    def remove_reader(self, fd):
        assert fd in self.readers
        self.readers.remove(fd)
        return True

    def unexpected(self, *args, **kwargs):
        pytest.fail("unexpected terminal I/O")


@pytest.fixture
def terminal(monkeypatch):
    terminal = _TerminalIO()
    # Module-local replacements: no real syscall fallback, no global OS patch.
    monkeypatch.setattr(
        serialposix,
        "os",
        SimpleNamespace(
            O_RDWR=os.O_RDWR,
            O_NOCTTY=os.O_NOCTTY,
            O_NONBLOCK=os.O_NONBLOCK,
            open=terminal.open,
            pipe=terminal.pipe,
            close=terminal.close,
        ),
    )
    monkeypatch.setattr(
        serialposix,
        "fcntl",
        SimpleNamespace(
            F_SETFL=serialposix.fcntl.F_SETFL,
            fcntl=terminal.fcntl,
            ioctl=terminal.ioctl,
        ),
    )
    constants = {
        name: value
        for name, value in vars(serialposix.termios).items()
        if name.isupper()
    }
    monkeypatch.setattr(
        serialposix,
        "termios",
        SimpleNamespace(
            **constants,
            error=serialposix.termios.error,
            tcgetattr=terminal.tcgetattr,
            tcsetattr=terminal.tcsetattr,
            tcflush=terminal.tcflush,
            tcdrain=terminal.tcdrain,
        ),
    )
    monkeypatch.setattr(
        serialposix, "select", SimpleNamespace(select=terminal.unexpected)
    )
    yield terminal
    # Keep assertion failures from leaving an open fake fd until finalization.
    if terminal.instance is not None:
        terminal.instance.close()


@pytest.mark.asyncio
async def test_wrap_reconfiguration_failure_closes_before_error_return(terminal):
    terminal.fail_configuration = 3
    with pytest.raises(serial.SerialException) as raised:
        await open_serial_port(
            terminal.port, baudrate=9600, timeout=0.5, write_timeout=0.5
        )

    assert type(raised.value) is serial.SerialException
    assert raised.value.__context__ is terminal.error
    assert str(raised.value) == f"Could not configure port: {terminal.error}"
    traceback = raised.value.__traceback__
    frames = {}
    while traceback is not None:
        frames[traceback.tb_frame.f_code] = traceback.tb_frame
        traceback = traceback.tb_next
    assert open_serial_port.__code__ in frames
    assert (
        frames[_open_with_idle_lines.__code__].f_locals["serial_instance"]
        is terminal.instance
    )
    assert serial_asyncio.connection_for_serial.__code__ in frames
    transport_frame = frames[serial_asyncio.SerialTransport.__init__.__code__]
    assert transport_frame.f_locals["self"]._serial is terminal.instance
    assert serial.Serial.write_timeout.fset.__code__ in frames
    assert serialposix.Serial._reconfigure_port.__code__ in frames
    assert terminal.configurations == [
        (False, 0.5, 0.5, 1),
        (True, 0, 0.5, 5),
        (True, 0, 0, 5),
    ]
    assert terminal.line_writes == [
        serialposix.TIOCM_DTR_str,
        serialposix.TIOCM_RTS_str,
    ]
    # The exception and serial remain retained; no yield precedes these checks.
    assert not terminal.owned, "serial fds remain open when wrap failure returns"
    assert sorted(terminal.closed) == terminal.opened
    assert not terminal.instance.is_open


@pytest.mark.parametrize(
    "backend", [Ic705SerialRadio, YaesuCatRadio], ids=["icom", "yaesu"]
)
@pytest.mark.parametrize(
    "retry_outcome",
    [
        "absent",
        "permission_denied",
        "initial_config",
        "initial_config_enoent",
        "message_only",
        "message_with_cause",
        "wrap",
        "busy",
        "timeout",
        "unknown",
        "api_channel",
    ],
)
@virtual_clock
async def test_detach_uses_every_serial_open_outcome(
    clock, terminal, tmp_path, monkeypatch, backend, retry_outcome
):
    terminal.open_error = FileNotFoundError(errno.ENOENT, "missing test port")
    radio = backend(device=terminal.port)
    composition = ManagedTxComposition(
        RecordingActuator(), config_path=tmp_path / "managed-tx.json"
    )
    install_managed_tx_composition(radio, composition)
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    attach_managed_tx_composition(server, composition)
    await composition.bind_state_store(server.command_state_store)
    try:
        with pytest.raises(RadioConnectionError):
            await radio.connect()
        radio.start_reconnect_recovery()
        assert not terminal.opened

        if retry_outcome == "busy":
            terminal.open_error = OSError(errno.EBUSY, "busy test port")
        elif retry_outcome == "timeout":
            terminal.open_error = TimeoutError(
                errno.ETIMEDOUT, "timed out opening test port"
            )
        elif retry_outcome == "unknown":
            terminal.open_error = RuntimeError("unknown test error")
        elif retry_outcome == "permission_denied":
            terminal.open_error = PermissionError(errno.EACCES, "refused test port")
        elif retry_outcome not in {
            "absent",
            "api_channel",
            "message_only",
            "message_with_cause",
        }:
            terminal.open_error = None
            if retry_outcome == "initial_config_enoent":
                terminal.set_attributes_error = FileNotFoundError(
                    errno.ENOENT, "port vanished during configuration"
                )
            else:
                terminal.fail_configuration = 2 if retry_outcome == "wrap" else 1

        reopen = (
            radio.soft_reconnect
            if isinstance(radio, Ic705SerialRadio)
            else radio.connect
        )
        def reject_without_errno(instance):
            terminal.open_attempts += 1
            error = serial.SerialException(
                f"could not open port {instance.port}: No such file or directory"
            )
            if retry_outcome == "message_with_cause":
                raise error from FileNotFoundError(errno.ENOENT, "missing test port")
            raise error

        with monkeypatch.context() as patch:
            if retry_outcome in {"message_only", "message_with_cause"}:
                patch.setattr(serial.Serial, "open", reject_without_errno)
            with pytest.raises((RadioConnectionError, CatTransportError)):
                await reopen()
        assert terminal.open_attempts == 2
        expected_configurations = {
            "initial_config": [False],
            "initial_config_enoent": [False],
            "wrap": [False, True],
        }
        assert [config[0] for config in terminal.configurations] == (
            expected_configurations.get(retry_outcome, [])
        )
        assert not terminal.owned

        # A subsequent proven failure cannot erase uncertainty from a prior open.
        terminal.open_error = FileNotFoundError(errno.ENOENT, "missing test port")
        with pytest.raises(RadioConnectionError):
            await reopen()
        assert terminal.open_attempts == 3
        assert radio.conn_state.value == "reconnecting"
        assert radio.serial_open_error is not None
        assert radio.control_connected is False
        assert not (await composition.authority.snapshot()).state.release_required
        assert (
            project_observed_ptt(server.command_state_store.snapshot())
            is ObservedPtt.UNKNOWN
        )

        reader, headers = _reader({"operation": "force_off"})
        headers["x-rigplane-release-for-detach"] = "1"
        writer = _Writer(api_channel=retry_outcome == "api_channel")
        await server._handle_http(
            writer,  # type: ignore[arg-type]
            "POST",
            "/api/v1/managed-transmit/command",
            headers=headers,
            reader=reader,
        )
        assert writer.status == 202
        projection = await composition.authority.snapshot()
        assert projection.provider_generation is None
        if retry_outcome in {"absent", "permission_denied", "message_with_cause"}:
            assert writer.payload["result"] == "accepted"
            assert writer.payload["releaseDisposition"] == "not_applicable"
            assert not projection.state.release_required
        else:
            assert writer.payload["result"] == "pending"
            assert "releaseDisposition" not in writer.payload
            assert projection.state.release_required
        if retry_outcome == "api_channel":
            # An API-channel request leaves serial recovery admitted.
            with pytest.raises(RadioConnectionError):
                await reopen()
            assert terminal.open_attempts == 4

            reader, headers = _reader({"operation": "force_off"})
            writer = _Writer("192.0.2.1")
            server._controller.mode = "remote"
            await server._handle_http(
                writer,  # type: ignore[arg-type]
                "POST",
                "/api/v1/managed-transmit/command",
                headers=headers,
                reader=reader,
            )
            assert writer.status == 202
            assert writer.payload["result"] == "pending"
    finally:
        await radio.disconnect()
        termination = asyncio.Event()
        termination.set()
        await composition.shutdown(termination)


@pytest.mark.parametrize(
    "backend", [Ic705SerialRadio, YaesuCatRadio], ids=["icom", "yaesu"]
)
@pytest.mark.parametrize(
    "explicit_disconnect", [True, False], ids=["disconnect", "replug"]
)
@virtual_clock
async def test_explicit_connect_after_detach_seal(
    clock, terminal, tmp_path, monkeypatch, backend, explicit_disconnect
):
    terminal.open_error = FileNotFoundError(errno.ENOENT, "missing test port")
    radio = backend(device=terminal.port)
    composition = ManagedTxComposition(
        RecordingActuator(), config_path=tmp_path / "managed-tx.json"
    )
    install_managed_tx_composition(radio, composition)
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    attach_managed_tx_composition(server, composition)
    await composition.bind_state_store(server.command_state_store)
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "add_reader", terminal.add_reader)
    monkeypatch.setattr(loop, "remove_reader", terminal.remove_reader)

    async def no_identity(*args, **kwargs):
        return None

    if isinstance(radio, Ic705SerialRadio):
        monkeypatch.setattr(radio, "_gate_serial_identity", no_identity)
        reopen = radio.soft_reconnect
    else:
        monkeypatch.setattr(radio, "_read_identity_answer", no_identity)
        monkeypatch.setattr(radio, "_sweep_identity_bauds", no_identity)
        reopen = radio.connect

    try:
        with pytest.raises(RadioConnectionError):
            await radio.connect()
        radio.start_reconnect_recovery()

        reader, headers = _reader({"operation": "force_off"})
        headers["x-rigplane-release-for-detach"] = "1"
        detach = _Writer()
        await server._handle_http(
            detach,  # type: ignore[arg-type]
            "POST",
            "/api/v1/managed-transmit/command",
            headers=headers,
            reader=reader,
        )
        assert detach.status == 202
        assert detach.payload["releaseDisposition"] == "not_applicable"

        terminal.open_error = None
        if explicit_disconnect:
            disconnect = _Writer()
            await server._handle_http(
                disconnect, "POST", "/api/v1/radio/disconnect"  # type: ignore[arg-type]
            )
            assert disconnect.status == 200

        with pytest.raises(RadioConnectionError, match="detaching binding"):
            await reopen()
        assert terminal.open_attempts == 1

        connect = _Writer()
        await server._handle_http(
            connect, "POST", "/api/v1/radio/connect"  # type: ignore[arg-type]
        )
        assert connect.status == 200
        assert terminal.open_attempts == 2
        assert terminal.instance.is_open
        assert radio.control_connected
        assert not (await composition.authority.snapshot()).state.release_required
    finally:
        await radio.disconnect()
        termination = asyncio.Event()
        termination.set()
        await composition.shutdown(termination)


@pytest.mark.asyncio
async def test_wrap_failure_keeps_original_error_when_close_also_fails(terminal):
    terminal.fail_configuration = 3
    close_error = RuntimeError("injected close failure")
    terminal.close_failure = close_error
    try:
        with pytest.raises(serial.SerialException) as raised:
            await open_serial_port(
                terminal.port, baudrate=9600, timeout=0.5, write_timeout=0.5
            )
    finally:
        if (
            terminal.instance is not None
            and "original_close" in terminal.close_observed
        ):
            terminal.instance.close = terminal.close_observed["original_close"]

    assert raised.value is terminal.close_observed["original_error"]
    assert raised.value.__context__ is terminal.error
    assert close_error not in (raised.value, raised.value.__context__)
    assert terminal.configurations[-1] == (True, 0, 0, 5)
    assert not terminal.owned
    assert sorted(terminal.closed) == terminal.opened
    assert not terminal.instance.is_open


@pytest.mark.asyncio
async def test_initial_reconfiguration_failure_closes_before_error_return(terminal):
    terminal.fail_configuration = 1
    with pytest.raises(serial.SerialException) as raised:
        await open_serial_port(
            terminal.port, baudrate=9600, timeout=0.5, write_timeout=0.5
        )

    assert type(raised.value) is serial.SerialException
    assert raised.value.__context__ is terminal.error
    assert str(raised.value) == f"Could not configure port: {terminal.error}"
    assert raised.value.__traceback__ is not None
    assert terminal.configurations == [(False, 0.5, 0.5, 1)]
    assert terminal.line_writes == []
    assert terminal.closed == terminal.opened
    assert not terminal.owned
    assert terminal.instance.fd is None
    assert not terminal.instance.is_open


@pytest.mark.asyncio
async def test_successful_wrap_keeps_ownership_until_writer_close(
    terminal, monkeypatch
):
    loop = asyncio.get_running_loop()
    monkeypatch.setattr(loop, "add_reader", terminal.add_reader)
    monkeypatch.setattr(loop, "remove_reader", terminal.remove_reader)
    monkeypatch.setattr(loop, "add_writer", terminal.unexpected)
    monkeypatch.setattr(loop, "remove_writer", terminal.unexpected)
    reader, writer = await open_serial_port(
        terminal.port, baudrate=9600, timeout=0.5, write_timeout=0.5
    )
    try:
        await asyncio.sleep(0)
        assert isinstance(writer.transport, serial_asyncio.SerialTransport)
        assert writer.transport.serial is terminal.instance
        assert reader._transport is writer.transport
        assert terminal.instance.is_open
        assert len(terminal.owned) == 5
        assert terminal.closed == []
        assert terminal.readers == {terminal.instance.fileno()}
        assert terminal.configurations == [
            (False, 0.5, 0.5, 1),
            (True, 0, 0.5, 5),
            (True, 0, 0, 5),
        ]
        assert (
            terminal.line_writes
            == [
                serialposix.TIOCM_DTR_str,
                serialposix.TIOCM_RTS_str,
            ]
            * 2
        )
    finally:
        writer.close()
        await writer.wait_closed()
    assert reader.at_eof()
    assert not terminal.readers
    assert not terminal.owned
    assert sorted(terminal.closed) == terminal.opened
    assert not terminal.instance.is_open
