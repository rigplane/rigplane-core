"""Where the LAN CI-V order counters are reported (MOR-4205).

The counters of ``IcomCivTransport.rx_order_stats`` reach the periodic
"Transport health" log line and ``GET /api/v1/runtime`` (``civIngress``), so
a running station shows whether its link reorders, duplicates or loses
datagrams.
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from rigplane.backends.icom7610.drivers.serial_session import SerialCivTransport
from rigplane.commands import CONTROLLER_ADDR, build_civ_frame
from rigplane.core.transport import IcomCivTransport, IcomTransport
from rigplane.runtime.radio_reconnect import watchdog_loop
from rigplane.testing.timing import VirtualClock
from rigplane.web.api_contract import RESPONSE_FIELD_CONTRACTS, CivIngressPayload
from rigplane.web.server import WebConfig, WebServer

from lan_reorder_fake import LanRadioFake
from test_audio_session_health import _FakeWriter, _response_json

pytestmark = pytest.mark.timeout(10)


def _civ_transport_with_one_of_each() -> IcomCivTransport:
    """A CI-V transport that saw one reordered and one duplicated datagram."""
    transport = IcomCivTransport()
    radio = LanRadioFake(transport)
    for n in range(3):
        radio.packet(build_civ_frame(CONTROLLER_ADDR, 0xA4, 0x03, data=bytes([n])))
    radio.deliver(1, 3, 2, 2)
    return transport


async def _runtime(radio: Any) -> dict[str, Any]:
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    writer = _FakeWriter()
    await srv._handle_http(writer, "GET", "/api/v1/runtime")  # noqa: SLF001
    status, data = _response_json(writer)
    assert status == 200
    return data


def _radio(civ_transport: object) -> SimpleNamespace:
    return SimpleNamespace(
        model="IC-705",
        backend_id="rigplane",
        connected=True,
        control_connected=True,
        radio_ready=True,
        capabilities=set(),
        _civ_transport=civ_transport,
    )


async def test_runtime_reports_the_lan_civ_order_counters() -> None:
    data = await _runtime(_radio(_civ_transport_with_one_of_each()))

    civ_ingress = data["civIngress"]
    assert set(civ_ingress) == CivIngressPayload.__required_keys__
    assert civ_ingress == CivIngressPayload(
        ordered=True, reordered=1, duplicate=1, lost=0, late=0, reanchored=0
    )


def test_civ_ingress_is_a_stable_runtime_field() -> None:
    assert "civIngress" in RESPONSE_FIELD_CONTRACTS["/api/v1/runtime"]["required"]


@pytest.mark.parametrize("serial", [False, True], ids=["none", "serial"])
async def test_runtime_reports_no_counters_without_a_lan_civ_transport(
    serial: bool,
) -> None:
    civ_transport = SerialCivTransport(civ_link=object()) if serial else None  # type: ignore[arg-type]
    assert (await _runtime(_radio(civ_transport)))["civIngress"] is None


async def test_transport_health_line_carries_the_counters(
    virtual_clock: VirtualClock, caplog: pytest.LogCaptureFixture
) -> None:
    radio = SimpleNamespace(
        _connected=True,
        WATCHDOG_CHECK_INTERVAL=0.5,
        _WATCHDOG_HEALTH_LOG_INTERVAL=0.0,
        _watchdog_timeout=1e9,
        _ctrl_transport=IcomTransport(),
        _civ_transport=_civ_transport_with_one_of_each(),
    )
    caplog.set_level(logging.INFO, logger="rigplane.runtime.radio_reconnect")
    task = asyncio.create_task(watchdog_loop(radio))  # type: ignore[arg-type]
    try:
        await virtual_clock.elapse(radio.WATCHDOG_CHECK_INTERVAL)
    finally:
        radio._connected = False
        await virtual_clock.elapse(radio.WATCHDOG_CHECK_INTERVAL)
        await task

    lines = [r.getMessage() for r in caplog.records]
    health = [line for line in lines if "Transport health" in line]
    assert health, "no Transport health line was logged"
    assert (
        "civ_reordered=1 civ_duplicate=1 civ_lost=0 civ_late=0 civ_reanchored=0"
        in health[0]
    ), health[0]
