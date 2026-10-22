"""Serial ownership at the real POSIX open-to-asyncio handoff (MOR-3470)."""

from __future__ import annotations

import asyncio
import errno
import os
import sys
from types import SimpleNamespace

import pytest
import serial

from rigplane.core.serial_open import _open_with_idle_lines, open_serial_port

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
        self.fail_configuration = None
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
