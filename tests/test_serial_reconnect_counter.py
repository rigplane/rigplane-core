"""Reconnect counting on the serial link paths.

Pins the serial CI-V soft-reconnect counter
(``backends/_icom_serial_base.py: _IcomSerialRadioBase``), the Yaesu CAT
transport counter exposure
(``backends/yaesu_cat/radio.py: YaesuCatRadio``), and how
``web/server.py: WebServer._runtime_process_payload`` serves them as
``process.reconnectsTotal``.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.backends.icom7610 import Icom7610SerialRadio
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.web.server import WebConfig, WebServer

from test_audio_session_health import _FakeWriter, _response_json
from test_icom7610_serial_radio import _FakeSerialCivLink


async def _runtime_process(srv: WebServer) -> dict[str, Any]:
    writer = _FakeWriter()
    await srv._handle_http(writer, "GET", "/api/v1/runtime")  # noqa: SLF001
    status, data = _response_json(writer)
    assert status == 200
    return data["process"]


def _serial_shaped_radio(**extra: Any) -> SimpleNamespace:
    return SimpleNamespace(
        model="IC-7300",
        backend_id="icom_serial",
        connected=True,
        control_connected=True,
        radio_ready=True,
        capabilities=set(),
        set_reconnect_status_callback=lambda callback: None,
        **extra,
    )


@pytest.mark.asyncio
async def test_serial_reconnect_counter_starts_at_zero() -> None:
    radio = Icom7610SerialRadio(
        device="/dev/ttyUSB0", civ_link=_FakeSerialCivLink()
    )
    assert radio.serial_reconnects_total == 0
    assert type(radio.serial_reconnects_total) is int


@pytest.mark.asyncio
async def test_serial_link_loss_and_recovery_counts_once() -> None:
    link = _FakeSerialCivLink()
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    await radio.connect()
    await radio._stop_civ_data_watchdog()  # noqa: SLF001
    try:
        assert radio.serial_reconnects_total == 0
        link.ready = False
        link.healthy = False
        await radio.soft_reconnect()
        assert radio.serial_reconnects_total == 1
        assert radio.connected is True
    finally:
        await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_two_recoveries_count_two() -> None:
    link = _FakeSerialCivLink()
    radio = Icom7610SerialRadio(device="/dev/ttyUSB0", civ_link=link)
    await radio.connect()
    await radio._stop_civ_data_watchdog()  # noqa: SLF001
    try:
        for _ in range(2):
            link.ready = False
            link.healthy = False
            await radio.soft_reconnect()
        assert radio.serial_reconnects_total == 2
    finally:
        await radio.disconnect()


@pytest.mark.asyncio
async def test_yaesu_reconnect_attempts_count() -> None:
    radio = YaesuCatRadio("/dev/null", profile="ftx1", audio_driver=MagicMock())
    assert radio.serial_reconnects_total == 0
    transport = radio._transport  # noqa: SLF001
    transport._connected = True  # noqa: SLF001
    transport._writer = SimpleNamespace(  # noqa: SLF001
        close=lambda: None, wait_closed=AsyncMock()
    )

    async def connect() -> None:
        transport._writer = SimpleNamespace(  # noqa: SLF001
            close=lambda: None, wait_closed=AsyncMock()
        )
        transport._connected = True  # noqa: SLF001
        transport._stats.record_success()  # noqa: SLF001

    transport.connect = AsyncMock(side_effect=connect)  # noqa: SLF001
    for _ in range(5):
        transport._stats.record_error("fixture disconnect")  # noqa: SLF001
    transport._last_reconnect = 0.0  # noqa: SLF001
    await transport.reconnect()
    assert radio.serial_reconnects_total == 1

    for _ in range(5):
        transport._stats.record_error("fixture disconnect")  # noqa: SLF001
    transport._last_reconnect = 0.0  # noqa: SLF001
    await transport.reconnect()
    assert radio.serial_reconnects_total == 2


@pytest.mark.asyncio
async def test_runtime_serves_zero_for_serial_radio_without_loss() -> None:
    srv = WebServer(
        _serial_shaped_radio(serial_reconnects_total=0),
        WebConfig(host="127.0.0.1", port=0),
    )
    assert (await _runtime_process(srv))["reconnectsTotal"] == 0


@pytest.mark.asyncio
async def test_runtime_serves_serial_reconnect_count() -> None:
    srv = WebServer(
        _serial_shaped_radio(serial_reconnects_total=3),
        WebConfig(host="127.0.0.1", port=0),
    )
    assert (await _runtime_process(srv))["reconnectsTotal"] == 3


@pytest.mark.asyncio
async def test_runtime_serves_yaesu_reconnect_count() -> None:
    radio = _serial_shaped_radio(serial_reconnects_total=2)
    radio.backend_id = "yaesu_cat"
    radio.model = "FTX-1"
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    assert (await _runtime_process(srv))["reconnectsTotal"] == 2


@pytest.mark.asyncio
async def test_runtime_ignores_a_non_integer_serial_counter() -> None:
    srv = WebServer(
        _serial_shaped_radio(serial_reconnects_total="3"),
        WebConfig(host="127.0.0.1", port=0),
    )
    assert (await _runtime_process(srv))["reconnectsTotal"] is None


@pytest.mark.asyncio
async def test_runtime_prefers_the_lan_count_when_a_watchdog_started() -> None:
    watchdog_task = asyncio.create_task(asyncio.sleep(0))
    await watchdog_task
    radio = _serial_shaped_radio(
        serial_reconnects_total=7, _watchdog_task=watchdog_task
    )
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    assert (await _runtime_process(srv))["reconnectsTotal"] == 0
