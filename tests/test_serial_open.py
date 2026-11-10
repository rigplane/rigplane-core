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
import errno
import json
import logging
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest
import serial

import rigplane.core.serial_open as serial_open
from rigplane.backends.discovery import probe_serial_civ, probe_xiegu_model_id
from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
from rigplane.backends.yaesu_cat import YaesuCatTransport
from rigplane.core.serial_open import open_serial_port


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (PermissionError("private detail"), "serial_permission_denied"),
        (OSError(errno.EACCES, "private detail"), "serial_permission_denied: EACCES"),
        (OSError(errno.EPERM, "private detail"), "serial_permission_denied: EPERM"),
        (OSError(errno.EBUSY, "private detail"), "serial_busy: EBUSY"),
        (OSError(errno.ENOENT, "private detail"), "serial_not_found: ENOENT"),
        (OSError(errno.ENODEV, "private detail"), "serial_not_found: ENODEV"),
        (OSError(errno.EAGAIN, "private detail"), "serial_busy: EAGAIN"),
        (TimeoutError("private detail"), "serial_timeout"),
        (OSError(errno.ETIMEDOUT, "private detail"), "serial_timeout: ETIMEDOUT"),
        (RuntimeError("Permission denied /private/secret"), "serial_unknown"),
    ],
)
def test_serial_open_diagnostic_preserves_only_verified_cause(error, expected):
    from rigplane.core.serial_open import serial_open_diagnostic

    wrapper = RuntimeError("wrapper secret")
    wrapper.__cause__ = error
    assert serial_open_diagnostic(wrapper) == expected
    wrapper.__cause__ = None
    wrapper.__context__ = error
    wrapper.__suppress_context__ = False
    assert serial_open_diagnostic(wrapper) == expected
    error.__context__ = wrapper
    assert serial_open_diagnostic(wrapper) == expected


def test_serial_open_diagnostic_does_not_use_suppressed_context():
    from rigplane.core.serial_open import serial_open_diagnostic

    wrapper = RuntimeError("Permission denied")
    wrapper.__context__ = OSError(errno.EACCES, "private detail")
    wrapper.__suppress_context__ = True
    assert serial_open_diagnostic(wrapper) == "serial_unknown"


@pytest.mark.parametrize(
    ("detail", "winerror", "expected"),
    [
        ("Access is denied.", 5, "serial_busy"),
        ("Отказано в доступе.", 5, "serial_busy"),
        ("Zugriff verweigert.", 5, "serial_busy"),
        ("Sharing violation.", 32, "serial_busy"),
        ("File not found.", 2, "serial_not_found"),
        ("Path not found.", 3, "serial_not_found"),
        ("Unknown error.", 87, "serial_unknown"),
    ],
)
def test_windows_serial_open_diagnostic(detail, winerror, expected):
    message = (
        f"could not open port 'COM3': PermissionError(13, {detail!r}, None, {winerror})"
    )
    error = serial.SerialException(message)
    native = OSError(errno.EACCES, detail)
    native.winerror = winerror
    for candidate in (error, native):
        native_expected = (
            expected if winerror != 87 else "serial_permission_denied: EACCES"
        )
        wanted = native_expected if candidate is native else expected
        wrapper = RuntimeError("private wrapper")
        for wrapped in (candidate, wrapper):
            if wrapped is wrapper:
                wrapper.__cause__ = candidate
            assert serial_open.serial_open_diagnostic(wrapped) == wanted
        wrapper.__cause__ = None
        wrapper.__context__ = candidate
        wrapper.__suppress_context__ = False
        assert serial_open.serial_open_diagnostic(wrapper) == wanted
        assert detail not in serial_open.serial_open_diagnostic(wrapper)


@pytest.mark.parametrize(
    "message",
    [
        "unrelated private text",
        "could not open port: PermissionError(13, 'private text', None, 5) trailing",
        "could not open port: PermissionError(13, private text, None, 5)",
    ],
)
def test_windows_serial_open_diagnostic_rejects_unrelated_messages(message):
    assert (
        serial_open.serial_open_diagnostic(serial.SerialException(message))
        == "serial_unknown"
    )


@pytest.mark.asyncio
async def test_windows_serial_open_error_reaches_radio_health_and_discovery():
    from rigplane.backends.discovery import CivProbeDiagnostic
    from rigplane.backends.icom7610 import Icom7610SerialRadio
    from rigplane.web.server import WebConfig, WebServer

    error = serial.SerialException(
        "could not open port 'COM3': PermissionError(13, 'Access is denied.', None, 5)"
    )
    link = SerialCivLink(
        device="COM3", open_serial_connection=AsyncMock(side_effect=error)
    )
    radio = Icom7610SerialRadio(device="COM3")
    radio._serial_session = link
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0, discovery=False))

    async def request(method: str, path: str) -> tuple[int, dict[str, Any]]:
        assert server._server is not None
        host, port = server._server.sockets[0].getsockname()[:2]
        reader, writer = await asyncio.open_connection(host, port)
        try:
            writer.write(
                f"{method} {path} HTTP/1.1\r\n"
                f"Host: {host}:{port}\r\n"
                "Content-Length: 0\r\nConnection: close\r\n\r\n".encode()
            )
            await writer.drain()
            raw = await asyncio.wait_for(reader.read(), timeout=10.0)
        finally:
            writer.close()
            await writer.wait_closed()
        head, _, body = raw.partition(b"\r\n\r\n")
        return int(head.split(b" ")[1]), json.loads(body)

    try:
        server._server = await asyncio.start_server(
            server._accept_client, "127.0.0.1", 0
        )
        status, body = await request("POST", "/api/v1/radio/connect")
        assert status == 500
        status, body = await request("GET", "/api/v1/runtime")
        assert status == 200
        for private_detail in (str(error), "PermissionError", "Access is denied"):
            assert private_detail not in json.dumps(body)
        health = body["station"]["health"]
        assert health["lastError"] == "serial_busy"
        assert health["likelyCause"] == "radio_not_connected"
    finally:
        await server.stop()
    sink = CivProbeDiagnostic()
    assert (
        await probe_serial_civ(
            "COM3",
            baud_rates=[19200],
            _open_serial=AsyncMock(side_effect=error),
            diagnostics=sink,
        )
        is None
    )
    assert sink.code == "serial_busy"


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["connect", "soft_reconnect"])
async def test_serial_permission_error_reaches_health_without_private_details(method):
    from rigplane.backends.icom7610 import Icom7610SerialRadio
    from rigplane.web.runtime_helpers import classify_radio_health
    from rigplane.web.state_schema import RadioHealthPublic
    from rigplane.web.server import WebConfig, WebServer
    from rigplane.core.state_pipeline_contracts import (
        FieldPath,
        Observation,
        SourceMetadata,
    )

    radio = Icom7610SerialRadio(device="/private/secret-device")
    error = OSError(errno.EACCES, "token=secret", "/private/secret-device")
    wrapped = RuntimeError("wrapper secret")
    wrapped.__cause__ = error
    radio._serial_session.connect = AsyncMock(side_effect=wrapped)
    radio._serial_session.disconnect = AsyncMock()
    radio._maybe_rediscover_serial_device = AsyncMock()
    with pytest.raises(Exception) as raised:
        await getattr(radio, method)()
    assert raised.value.__cause__ is wrapped
    assert "/private/secret-device" in str(raised.value)
    assert "wrapper secret" in str(raised.value)
    assert "secret" not in radio.last_error
    health = classify_radio_health(radio)
    assert health["likelyCause"] == "radio_not_connected"
    assert health["lastError"] == "serial_permission_denied: EACCES"
    assert RadioHealthPublic.model_validate(health).lastError == health["lastError"]
    server = WebServer(radio, WebConfig())
    assert server._build_radio_health()["lastError"] == health["lastError"]
    server.command_state_store.apply(
        Observation(
            path=FieldPath.parse("health.health.last_error"),
            value="stale token=secret /private/secret-device",
            source=SourceMetadata(
                source="local_reconcile", provider="web_lifecycle", transport="web"
            ),
            timestamp_monotonic=0.0,
            quality=("confirmed",),
        )
    )
    public = server._build_public_state_from_snapshot(
        server.command_state_store.snapshot()
    )
    assert public["radioHealth"]["lastError"] == health["lastError"]
    assert "secret" not in str(public["radioHealth"])
    station_health = server._station_readiness_payload()["health"]
    assert station_health["lastError"] == health["lastError"]
    assert "secret" not in str(health)
    radio._serial_session.connect = AsyncMock()
    radio._gate_serial_identity = AsyncMock(return_value=False)
    radio._start_civ_rx_pump = Mock()
    radio._start_civ_data_watchdog = Mock()
    radio._start_civ_worker = Mock()
    await getattr(radio, method)()
    assert radio.serial_open_error is None
    assert radio.last_error is None
    silent_health = classify_radio_health(radio, served_with_silent_link=True)
    assert silent_health["likelyCause"] == "radio_powered_off_likely"
    assert silent_health["lastError"] is None
    await radio.disconnect()
    assert radio.serial_open_error is None
    assert "secret" not in str(server._station_readiness_payload()["health"])
    delivered = server._build_public_state_from_snapshot(
        server._snapshot_for_delivery()
    )
    assert "secret" not in str(delivered["radioHealth"])


@pytest.mark.asyncio
async def test_serial_link_retains_original_os_error():
    error = OSError(errno.EPERM, "private detail")
    link = SerialCivLink(
        device="/private/secret-device",
        open_serial_connection=AsyncMock(side_effect=error),
    )
    with pytest.raises(OSError) as raised:
        await link.connect()
    assert raised.value is error


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
                "/dev/ttyTEST", 19200, 0xA4, timeout=0.01, _open_serial=_open
            )

        assert result is None
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
                "/dev/ttyTEST", 19200, 0xA4, timeout=0.01, _open_serial=_open
            )

        assert result is None
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
            "/dev/ttyTEST", 19200, 0xA4, timeout=0.01, _open_serial=opener
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


@pytest.mark.asyncio
async def test_discovery_diagnostic_sink_keeps_idle_control_lines():
    from rigplane.backends.discovery import CivProbeDiagnostic

    serial = _RecordingSerial()
    reader, writer = _Reader(), _Writer(serial)
    opener, captured = _capturing_opener(reader, writer)
    sink = CivProbeDiagnostic()
    await probe_serial_civ(
        "/dev/ttyTEST",
        [19200],
        0.01,
        _open_serial=opener,
        diagnostics=sink,
    )
    assert captured[0]["baudrate"] == 19200
    assert ("dtr", False) in serial.writes
    assert ("rts", False) in serial.writes
    assert sink.open_failure_codes == ()
