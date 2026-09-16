"""Tests for the shared serial-open helper and its call-site wiring (MOR-2228).

pyserial defaults to ``dtr=True, rts=True`` on port open. A radio whose
USB SEND/KEY input is wired to DTR or RTS keys its transmitter while such
a line is asserted, so every serial open in rigplane must request both
lines inactive and deassert them again after the open returns.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

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

    async def test_passes_dtr_and_rts_false_to_opener(self) -> None:
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
        assert kwargs["dtr"] is False
        assert kwargs["rts"] is False

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

    async def test_refused_control_line_write_does_not_fail_open(
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
            record.levelno == logging.DEBUG and "/dev/ttyTEST" in record.getMessage()
            for record in caplog.records
        )


class TestCallSiteWiring:
    """Each of the four serial open sites routes through the shared helper."""

    async def test_discovery_civ_probe(self) -> None:
        reader, writer = _Reader(), _Writer()
        opener, captured = _capturing_opener(reader, writer)

        await probe_serial_civ(
            "/dev/ttyTEST", baud_rates=[19200], timeout=0.01, _open_serial=opener
        )

        assert captured, "CI-V probe never opened the port"
        assert captured[0]["dtr"] is False
        assert captured[0]["rts"] is False

    async def test_discovery_xiegu_model_id_probe(self) -> None:
        reader, writer = _Reader(), _Writer()
        opener, captured = _capturing_opener(reader, writer)

        await probe_xiegu_model_id(
            "/dev/ttyTEST", 19200, timeout=0.01, _open_serial=opener
        )

        assert captured, "Xiegu model-ID probe never opened the port"
        assert captured[0]["dtr"] is False
        assert captured[0]["rts"] is False

    async def test_yaesu_cat_transport(self, monkeypatch: pytest.MonkeyPatch) -> None:
        serial = _RecordingSerial()
        reader, writer = _Reader(), _Writer(serial)
        mock_module = MagicMock()
        mock_module.open_serial_connection = AsyncMock(return_value=(reader, writer))
        monkeypatch.setitem(sys.modules, "serial_asyncio", mock_module)

        transport = YaesuCatTransport(device="/dev/ttyTEST", baudrate=38400)
        await transport.connect()

        kwargs = mock_module.open_serial_connection.call_args.kwargs
        assert kwargs["dtr"] is False
        assert kwargs["rts"] is False
        assert ("dtr", False) in serial.writes
        assert ("rts", False) in serial.writes

    async def test_serial_civ_link_default_opener(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        serial = _RecordingSerial()
        reader, writer = _Reader(), _Writer(serial)
        mock_module = MagicMock()
        mock_module.open_serial_connection = AsyncMock(return_value=(reader, writer))
        monkeypatch.setitem(sys.modules, "serial_asyncio", mock_module)

        link = SerialCivLink(device="/dev/ttyTEST", baudrate=19200)
        opener = link._resolve_opener()
        result = await opener()

        assert result == (reader, writer)
        kwargs = mock_module.open_serial_connection.call_args.kwargs
        assert kwargs["dtr"] is False
        assert kwargs["rts"] is False
        assert ("dtr", False) in serial.writes
        assert ("rts", False) in serial.writes
