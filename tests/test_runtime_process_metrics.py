"""Process metrics block of ``GET /api/v1/runtime`` (``WebServer._serve_runtime``)."""

from __future__ import annotations

import asyncio
import ctypes
import os
import subprocess
import sys
from types import SimpleNamespace
from typing import Any

import pytest

from rigplane.radio import IcomRadio
from rigplane.web import server as server_module
from rigplane.web.server import WebConfig, WebServer
from rigplane_testkit.icom.faults import FaultPlan, FaultRule

from _perf_helpers import fast_connect
from mock_server import MockIcomRadio
from test_audio_session_health import _FakeWriter, _response_json

_PROCESS_KEYS = {
    "rssBytes",
    "openFds",
    "threads",
    "asyncioTasks",
    "reconnectsTotal",
}


async def _runtime(srv: WebServer) -> dict[str, Any]:
    writer = _FakeWriter()
    await srv._handle_http(writer, "GET", "/api/v1/runtime")  # noqa: SLF001
    status, data = _response_json(writer)
    assert status == 200
    return data


def _stub_server() -> WebServer:
    radio = SimpleNamespace(
        model="IC-7610",
        backend_id="rigplane",
        connected=True,
        control_connected=True,
        radio_ready=True,
        capabilities=set(),
    )
    return WebServer(radio, WebConfig(host="127.0.0.1", port=0))


async def test_process_block_has_expected_keys_and_types() -> None:
    process = (await _runtime(_stub_server()))["process"]
    assert set(process) == _PROCESS_KEYS
    for key in ("rssBytes", "openFds"):
        assert process[key] is None or (
            type(process[key]) is int and process[key] > 0
        ), key
    assert type(process["threads"]) is int and process["threads"] >= 1
    assert type(process["asyncioTasks"]) is int and process["asyncioTasks"] >= 1
    # The stub radio has no started watchdog, so no count is observable.
    assert process["reconnectsTotal"] is None


async def test_asyncio_tasks_counts_a_task_created_by_the_test() -> None:
    srv = _stub_server()
    before = (await _runtime(srv))["process"]["asyncioTasks"]
    gate = asyncio.Event()
    task = asyncio.create_task(gate.wait())
    try:
        after = (await _runtime(srv))["process"]["asyncioTasks"]
    finally:
        gate.set()
        await task
    assert after == before + 1


async def test_open_fds_counts_a_descriptor_opened_by_the_test() -> None:
    srv = _stub_server()
    before = (await _runtime(srv))["process"]["openFds"]
    if before is None:
        return  # platform without a descriptor listing; type test covers null
    fd = os.open(os.devnull, os.O_RDONLY)
    try:
        after = (await _runtime(srv))["process"]["openFds"]
    finally:
        os.close(fd)
    assert after == before + 1


async def test_reconnects_total_null_for_serial_shaped_radio() -> None:
    """A serial radio inherits the callback setter but never emits status.

    ``backends/_icom_serial_base.py: _IcomSerialRadioBase`` inherits
    ``set_reconnect_status_callback`` from ``CoreRadio``, yet only the LAN
    watchdog/reconnect loops ever call it; the serial CI-V watchdog
    reconnects silently. Such a radio must report null ("not counted"),
    not a constant 0.
    """
    srv = _stub_server()
    srv._radio = SimpleNamespace(  # noqa: SLF001
        **vars(srv._radio),  # noqa: SLF001
        set_reconnect_status_callback=lambda callback: None,
        _watchdog_task=None,
    )
    assert (await _runtime(srv))["process"]["reconnectsTotal"] is None


async def test_reconnects_total_counts_reconnecting_edges_only() -> None:
    srv = _stub_server()
    watchdog_task = asyncio.create_task(asyncio.sleep(0))
    await watchdog_task  # done, like a watchdog that fired — still "started"
    srv._radio = SimpleNamespace(  # noqa: SLF001
        **vars(srv._radio),  # noqa: SLF001
        set_reconnect_status_callback=lambda callback: None,
        _watchdog_task=watchdog_task,
    )
    assert (await _runtime(srv))["process"]["reconnectsTotal"] == 0
    for status in (
        {"state": "reconnecting", "attempt": 1, "next_retry_seconds": 0.1},
        {"state": "reconnecting", "attempt": 1, "next_retry_seconds": 0.1},
        {"state": "reconnecting", "attempt": 2, "next_retry_seconds": 0.2},
        {"state": "connected", "attempt": 2, "next_retry_seconds": None},
        {"state": "reconnecting", "attempt": 1, "next_retry_seconds": 0.1},
    ):
        srv._on_reconnect_status(status)  # noqa: SLF001
    assert (await _runtime(srv))["process"]["reconnectsTotal"] == 2


async def test_reconnects_total_increments_after_forced_reconnect(
    mock_radio: MockIcomRadio,
) -> None:
    """Silencing the IC-7610 simulator trips the watchdog's reconnect; it is counted."""
    radio = IcomRadio(
        host="127.0.0.1",
        port=mock_radio.control_port,
        username="testuser",
        password="testpass",
        model="IC-7610",
        auto_reconnect=True,
        reconnect_delay=5.0,
        watchdog_timeout=0.3,
    )
    with fast_connect():
        await radio.connect()
    srv = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    srv._attach_reconnect_status_listener()  # noqa: SLF001  # start() wiring
    try:
        assert (await _runtime(srv))["process"]["reconnectsTotal"] == 0
        mock_radio.fault_plan = FaultPlan(
            control_to_client=FaultRule(drop_probability=1.0),
            civ_to_client=FaultRule(drop_probability=1.0),
        )
        for _ in range(40):  # watchdog checks every 0.5 s
            await asyncio.sleep(0.1)
            if srv._connection_status is not None:  # noqa: SLF001
                break
        data = await _runtime(srv)
        assert data["connection"]["state"] == "reconnecting"
        assert data["process"]["reconnectsTotal"] == 1
    finally:
        srv._detach_reconnect_status_listener()  # noqa: SLF001
        radio._intentional_disconnect = True  # noqa: SLF001
        radio._control_phase._stop_reconnect()  # noqa: SLF001
        radio._control_phase._stop_watchdog()  # noqa: SLF001
        mock_radio.fault_plan = None
        await radio.disconnect()


def _write_taskinfo(buf: Any, **fields: int) -> None:
    info = ctypes.cast(
        buf,
        ctypes.POINTER(server_module._ProcTaskinfo),  # noqa: SLF001
    ).contents
    for name, value in fields.items():
        setattr(info, name, value)


def test_macos_rss_bytes_reads_resident_size_from_proc_taskinfo() -> None:
    """A libproc fake filling ``pti_resident_size`` yields exactly that value."""

    def fake_pidinfo(pid: int, flavor: int, arg: int, buf: Any, size: int) -> int:
        _write_taskinfo(
            buf, pti_virtual_size=999_999_999, pti_resident_size=123_456_789
        )
        return ctypes.sizeof(server_module._ProcTaskinfo)  # noqa: SLF001

    assert server_module._macos_rss_bytes(fake_pidinfo) == 123_456_789  # noqa: SLF001


def test_macos_rss_bytes_rejects_a_short_or_zero_write() -> None:
    """A return count other than ``sizeof(proc_taskinfo)`` means ``None``."""
    size = ctypes.sizeof(server_module._ProcTaskinfo)  # noqa: SLF001
    for written in (0, size - 4, size + 4):
        assert (
            server_module._macos_rss_bytes(  # noqa: SLF001
                lambda pid, flavor, arg, buf, bufsize, written=written: written
            )
            is None
        )


def test_macos_rss_bytes_swallows_oserror() -> None:
    """A failing libproc call reports ``None`` instead of raising."""

    def raising_pidinfo(pid: int, flavor: int, arg: int, buf: Any, size: int) -> int:
        raise OSError("libproc unavailable")

    assert server_module._macos_rss_bytes(raising_pidinfo) is None  # noqa: SLF001


def test_macos_rss_bytes_none_without_a_pidinfo_function() -> None:
    assert server_module._macos_rss_bytes(None) is None  # noqa: SLF001


@pytest.mark.skipif(sys.platform != "darwin", reason="the libproc reader is macOS-only")
async def test_rss_bytes_matches_ps_on_macos() -> None:
    """The endpoint's rssBytes is the current RSS: within ±50 % of ``ps``."""
    rss = (await _runtime(_stub_server()))["process"]["rssBytes"]
    assert type(rss) is int
    ps_out = subprocess.check_output(
        ["ps", "-o", "rss=", "-p", str(os.getpid())], text=True
    )
    ps_bytes = int(ps_out.strip()) * 1024
    assert abs(rss - ps_bytes) <= ps_bytes // 2
