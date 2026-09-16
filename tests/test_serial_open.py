"""Tests for the shared serial-open helper and its call-site wiring (MOR-2228).

pyserial asserts DTR and RTS by default on port open. A radio whose USB
SEND/KEY input is wired to DTR or RTS keys its transmitter while such a
line is asserted, so every serial open in rigplane routes through
``open_serial_port``, which drives both lines low on the still-closed
pyserial instance and deasserts them again after the open returns.

``test_real_pyserial_construction_with_helper_kwargs`` executes the REAL
pyserial constructor (``loop://`` needs no device) with the exact kwargs
the helper builds. A fake opener can never catch the class of bug where a
kwarg is not accepted by pyserial — that is why this test exists.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock

import pytest
import serial

import rigplane.core.serial_open as serial_open
from rigplane.backends.discovery import probe_serial_civ, probe_xiegu_model_id
from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
from rigplane.backends.yaesu_cat import YaesuCatTransport
from rigplane.core.serial_open import open_serial_port


class _RecordingSerial:
    """Stand-in for the pyserial object behind SerialTransport.serial."""

    def __init__(self, *, refuse_writes: bool = False) -> None:
        self._refuse_writes = refuse_writes
        self.writes: list[tuple[str, bool]] = []

    @property
    def dtr(self) -> bool:
        return True

    @dtr.setter
    def dtr(self, value: bool) -> None:
        if self._refuse_writes:
            raise OSError("port refuses DTR write")
        self.writes.append(("dtr", value))

    @property
    def rts(self) -> bool:
        return True

    @rts.setter
    def rts(self, value: bool) -> None:
        if self._refuse_writes:
            raise OSError("port refuses RTS write")
        self.writes.append(("rts", value))


class _FakeTransport:
    def __init__(self, serial: _RecordingSerial) -> None:
        self.serial = serial


class _Writer:
    """Minimal writer; exposes .transport.serial only when given one."""

    def __init__(self, serial: _RecordingSerial | None = None) -> None:
        if serial is not None:
            self.transport = _FakeTransport(serial)

    def write(self, data: bytes) -> None:
        pass

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        pass

    async def wait_closed(self) -> None:
        pass


class _Reader:
    async def read(self, n: int) -> bytes:
        raise asyncio.TimeoutError("no data")


def _capturing_opener(reader: Any, writer: Any) -> tuple[Any, list[dict[str, Any]]]:
    captured: list[dict[str, Any]] = []

    async def _open(**kwargs: Any) -> tuple[Any, Any]:
        captured.append(kwargs)
        return reader, writer

    return _open, captured


class TestOpenSerialPort:
    """Unit tests for rigplane.core.serial_open.open_serial_port."""

    async def test_forwards_constructor_kwargs_only_to_opener(self) -> None:
        reader, writer = _Reader(), _Writer()
        opener, captured = _capturing_opener(reader, writer)

        result = await open_serial_port(
            url="/dev/ttyTEST", baudrate=19200, opener=opener, bytesize=8
        )

        assert result == (reader, writer)
        assert len(captured) == 1
        kwargs = captured[0]
        assert kwargs["url"] == "/dev/ttyTEST"
        assert kwargs["baudrate"] == 19200
        assert kwargs["bytesize"] == 8
        # dtr/rts are post-construction properties, not constructor
        # kwargs — passing them would make pyserial raise ValueError.
        assert "dtr" not in kwargs
        assert "rts" not in kwargs

    async def test_deasserts_lines_on_the_real_serial_object(self) -> None:
        serial = _RecordingSerial()
        reader, writer = _Reader(), _Writer(serial)
        opener, _ = _capturing_opener(reader, writer)

        await open_serial_port(url="/dev/ttyTEST", baudrate=19200, opener=opener)

        assert ("dtr", False) in serial.writes
        assert ("rts", False) in serial.writes

    async def test_tolerates_writer_without_serial_attribute(self) -> None:
        reader, writer = _Reader(), _Writer()
        opener, _ = _capturing_opener(reader, writer)

        result = await open_serial_port(
            url="/dev/ttyTEST", baudrate=19200, opener=opener
        )

        assert result == (reader, writer)

    async def test_refused_control_line_write_warns_but_does_not_fail_open(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        serial = _RecordingSerial(refuse_writes=True)
        reader, writer = _Reader(), _Writer(serial)
        opener, _ = _capturing_opener(reader, writer)

        with caplog.at_level(logging.DEBUG):
            result = await open_serial_port(
                url="/dev/ttyTEST", baudrate=19200, opener=opener
            )

        assert result == (reader, writer)
        assert any(
            record.levelno == logging.WARNING and "/dev/ttyTEST" in record.getMessage()
            for record in caplog.records
        )


class TestRealPyserialConstruction:
    """Execute the real pyserial construction path with no hardware."""

    def test_real_pyserial_construction_with_helper_kwargs(self) -> None:
        instance = serial_open._configure_serial_idle_lines(
            "loop://", baudrate=19200, bytesize=8, parity="N", stopbits=1
        )
        try:
            assert instance.is_open
            assert instance.dtr is False
            assert instance.rts is False
        finally:
            instance.close()


class TestOpenErrorClassification:
    """Expected open failures stay quiet; unexpected ones warn (MOR-2228)."""

    async def test_civ_probe_unexpected_open_error_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        async def _open(**kwargs: Any) -> tuple[Any, Any]:
            raise ValueError("unexpected keyword arguments")

        with caplog.at_level(logging.DEBUG):
            result = await probe_serial_civ(
                "/dev/ttyTEST", baud_rates=[19200], timeout=0.01, _open_serial=_open
            )

        assert result is None
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "ValueError" in r.getMessage() and "cannot open" not in r.getMessage()
            for r in warnings
        )

    async def test_civ_probe_expected_open_error_stays_quiet(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        async def _open(**kwargs: Any) -> tuple[Any, Any]:
            raise serial.SerialException("could not open port")

        with caplog.at_level(logging.DEBUG):
            result = await probe_serial_civ(
                "/dev/ttyTEST", baud_rates=[19200], timeout=0.01, _open_serial=_open
            )

        assert result is None
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any("cannot open" in r.getMessage() for r in caplog.records)

    async def test_xiegu_probe_unexpected_open_error_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        async def _open(**kwargs: Any) -> tuple[Any, Any]:
            raise TypeError("programming error")

        with caplog.at_level(logging.DEBUG):
            result = await probe_xiegu_model_id(
                "/dev/ttyTEST", 19200, timeout=0.01, _open_serial=_open
            )

        assert result is False
        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert any(
            "TypeError" in r.getMessage() and "cannot open" not in r.getMessage()
            for r in warnings
        )

    async def test_xiegu_probe_expected_open_error_stays_quiet(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        async def _open(**kwargs: Any) -> tuple[Any, Any]:
            raise OSError("device busy")

        with caplog.at_level(logging.DEBUG):
            result = await probe_xiegu_model_id(
                "/dev/ttyTEST", 19200, timeout=0.01, _open_serial=_open
            )

        assert result is False
        assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


class TestCallSiteWiring:
    """Each of the four serial open sites routes through the shared helper."""

    async def test_discovery_civ_probe(self) -> None:
        serial = _RecordingSerial()
        reader, writer = _Reader(), _Writer(serial)
        opener, captured = _capturing_opener(reader, writer)

        await probe_serial_civ(
            "/dev/ttyTEST", baud_rates=[19200], timeout=0.01, _open_serial=opener
        )

        assert captured, "CI-V probe never opened the port"
        assert captured[0]["url"] == "/dev/ttyTEST"
        assert ("dtr", False) in serial.writes
        assert ("rts", False) in serial.writes

    async def test_discovery_xiegu_model_id_probe(self) -> None:
        serial = _RecordingSerial()
        reader, writer = _Reader(), _Writer(serial)
        opener, captured = _capturing_opener(reader, writer)

        await probe_xiegu_model_id(
            "/dev/ttyTEST", 19200, timeout=0.01, _open_serial=opener
        )

        assert captured, "Xiegu model-ID probe never opened the port"
        assert captured[0]["url"] == "/dev/ttyTEST"
        assert ("dtr", False) in serial.writes
        assert ("rts", False) in serial.writes

    async def test_yaesu_cat_transport(self, monkeypatch: pytest.MonkeyPatch) -> None:
        serial = _RecordingSerial()
        reader, writer = _Reader(), _Writer(serial)
        mock_open = AsyncMock(return_value=(reader, writer))
        monkeypatch.setattr(serial_open, "_open_with_idle_lines", mock_open)

        transport = YaesuCatTransport(device="/dev/ttyTEST", baudrate=38400)
        await transport.connect()

        kwargs = mock_open.call_args.kwargs
        assert kwargs["url"] == "/dev/ttyTEST"
        assert kwargs["bytesize"] == 8
        assert kwargs["parity"] == "N"
        assert kwargs["stopbits"] == 1
        assert ("dtr", False) in serial.writes
        assert ("rts", False) in serial.writes

    async def test_serial_civ_link_default_opener(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        serial = _RecordingSerial()
        reader, writer = _Reader(), _Writer(serial)
        mock_open = AsyncMock(return_value=(reader, writer))
        monkeypatch.setattr(serial_open, "_open_with_idle_lines", mock_open)

        link = SerialCivLink(device="/dev/ttyTEST", baudrate=19200)
        opener = link._resolve_opener()
        result = await opener()

        assert result == (reader, writer)
        kwargs = mock_open.call_args.kwargs
        assert kwargs["url"] == "/dev/ttyTEST"
        assert kwargs["baudrate"] == 19200
        assert ("dtr", False) in serial.writes
        assert ("rts", False) in serial.writes
