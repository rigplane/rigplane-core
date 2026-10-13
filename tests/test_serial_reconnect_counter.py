"""Episode counting for serial-link reconnects.

Pins the serial CI-V episode counter
(``backends/_icom_serial_base.py: _IcomSerialRadioBase``), the Yaesu CAT
episode counter (``backends/yaesu_cat/transport.py: TransportStats``,
exposed as ``backends/yaesu_cat/radio.py: YaesuCatRadio``), and how
``web/server.py: WebServer._runtime_process_payload`` serves them as
``process.reconnectsTotal``. One loss of a working link is one episode;
retries inside it do not recount.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.types import AudioCodec
from rigplane.web.server import WebConfig, WebServer

from test_audio_session_health import _FakeWriter, _response_json
from test_icom7610_serial_radio import _FakeSerialCivLink, _FakeUsbAudioDriver
from test_yaesu_audio import FakeAudioDriver


def _serial_radio() -> tuple[Ic7300SerialRadio, _FakeSerialCivLink]:
    link = _FakeSerialCivLink(model_id=0x94)
    radio = Ic7300SerialRadio(
        device="/dev/ttyUSB-fake",
        civ_link=link,
        audio_driver=_FakeUsbAudioDriver(),
        audio_codec=AudioCodec.PCM_1CH_16BIT,
        # Hermetic on any host: no OS enumeration, so no real port is probed.
        _enumerate_serial_ports_fn=lambda: [],
    )
    return radio, link


async def _connect_without_watchdog(radio: Ic7300SerialRadio) -> None:
    await radio.connect()
    await radio._stop_civ_data_watchdog()  # noqa: SLF001


def _break_link(link: _FakeSerialCivLink) -> None:
    link.ready = False
    link.healthy = False


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
async def test_serial_declare_then_retries_count_one_episode() -> None:
    """The watchdog declares link-down, then retries recovery in-episode."""
    radio, link = _serial_radio()
    await _connect_without_watchdog(radio)
    try:
        assert radio.serial_reconnects_total == 0
        await radio._declare_serial_link_down()  # noqa: SLF001
        assert radio.serial_reconnects_total == 1
        _break_link(link)
        for _ in range(2):
            await radio.soft_reconnect()
        assert radio.serial_reconnects_total == 1
    finally:
        await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_two_separate_losses_count_two() -> None:
    """The link worked again between losses, so each loss is an episode."""
    radio, link = _serial_radio()
    await _connect_without_watchdog(radio)
    try:
        for _ in range(2):
            _break_link(link)
            await radio.soft_reconnect()
            assert radio.connected is True
        assert radio.serial_reconnects_total == 2
        assert type(radio.serial_reconnects_total) is int
    finally:
        await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_retries_inside_one_loss_count_once() -> None:
    """Failed reopen attempts keep the RECONNECTING state: still one episode."""
    radio, link = _serial_radio()
    await _connect_without_watchdog(radio)
    link._fail_connect = OSError("port gone")  # noqa: SLF001
    try:
        with pytest.raises(ConnectionError):
            await radio.soft_reconnect()
        assert radio.serial_reconnects_total == 1
        with pytest.raises(ConnectionError):
            await radio.soft_reconnect()
        assert radio.serial_reconnects_total == 1
    finally:
        link._fail_connect = None  # noqa: SLF001
        await radio.disconnect()


@pytest.mark.asyncio
async def test_serial_user_connect_from_disconnected_counts_zero() -> None:
    """The web ``radio_connect`` route after a user disconnect is no episode."""
    radio, link = _serial_radio()
    await _connect_without_watchdog(radio)
    await radio.disconnect()
    assert radio.connected is False
    try:
        _break_link(link)
        await radio.soft_reconnect()
        assert radio.serial_reconnects_total == 0
    finally:
        await radio.disconnect()


def _yaesu_radio() -> YaesuCatRadio:
    return YaesuCatRadio(
        "/dev/null",
        profile="ftx1",
        audio_driver=FakeAudioDriver(),  # type: ignore[arg-type]
    )


async def _yaesu_reconnect(transport: Any) -> None:
    """Run one transport reconnect without real serial hardware."""
    transport._last_reconnect = 0.0  # noqa: SLF001  # bypass the cooldown

    async def _fake_close() -> None:
        transport._connected = False  # noqa: SLF001
        transport._writer = None  # noqa: SLF001

    async def _fake_connect() -> None:
        transport._connected = True  # noqa: SLF001

    transport.close = _fake_close  # type: ignore[method-assign]
    transport.connect = _fake_connect  # type: ignore[method-assign]
    await transport.reconnect()


def _yaesu_lose_link(transport: Any) -> None:
    for _ in range(5):  # the transport's reconnect threshold
        transport._stats.record_error("fixture disconnect")  # noqa: SLF001


@pytest.mark.asyncio
async def test_yaesu_reconnect_episodes_count() -> None:
    """First reconnect after the link worked opens an episode; retries do not."""
    radio = _yaesu_radio()
    assert radio.serial_reconnects_total == 0
    transport = radio._transport  # noqa: SLF001

    _yaesu_lose_link(transport)
    await _yaesu_reconnect(transport)
    assert radio.serial_reconnects_total == 1
    assert transport.stats.reconnects == 1

    await _yaesu_reconnect(transport)
    assert transport.stats.reconnects == 2
    assert radio.serial_reconnects_total == 1

    # The link worked again (a successful exchange resets the error
    # streak), so the next loss is a new episode.
    transport._stats.record_success()  # noqa: SLF001
    _yaesu_lose_link(transport)
    await _yaesu_reconnect(transport)
    assert radio.serial_reconnects_total == 2


@pytest.mark.parametrize(
    ("total", "expected"),
    [(0, 0), (3, 3), ("3", None)],
)
@pytest.mark.asyncio
async def test_runtime_serves_serial_reconnect_count(
    total: Any, expected: int | None
) -> None:
    srv = WebServer(
        _serial_shaped_radio(serial_reconnects_total=total),
        WebConfig(host="127.0.0.1", port=0),
    )
    assert (await _runtime_process(srv))["reconnectsTotal"] == expected


@pytest.mark.asyncio
async def test_runtime_serves_yaesu_reconnect_count() -> None:
    radio = _serial_shaped_radio(serial_reconnects_total=2)
    radio.backend_id = "yaesu_cat"
    radio.model = "FTX-1"
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    assert (await _runtime_process(srv))["reconnectsTotal"] == 2


@pytest.mark.asyncio
async def test_runtime_prefers_the_lan_count_when_a_watchdog_started() -> None:
    watchdog_task = asyncio.create_task(asyncio.sleep(0))
    await watchdog_task
    radio = _serial_shaped_radio(
        serial_reconnects_total=7, _watchdog_task=watchdog_task
    )
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    assert (await _runtime_process(srv))["reconnectsTotal"] == 0
