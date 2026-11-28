"""MOR-3883: on-demand USB audio device candidates for the configured radio.

The candidate list an RX-start refusal offers (MOR-4119) is available at any
time: ``UsbAudioDriver.audio_candidates`` builds it from the same
``_candidates()`` rule with the serial radio's hub, and ``GET
/api/v1/audio/candidates`` serves the same wire record — no RX start, no new
transport. A late or missing Windows topology returns the English name
fallback's list with source ``"name_heuristic"`` so the response records
which path produced it. Windows replays the recorded W1/W3 captures; macOS
replays the recorded IC-705 + FTX-1 session; Linux uses synthetic sysfs. No
audio device and no radio is touched.
"""

from __future__ import annotations

import json

import pytest
from test_icom7610_serial_radio import _FakeSerialCivLink
from test_usb_pairing_linux import LINUX_PORT, _inject_linux
from test_usb_pairing_macos import FTX_PORT, MAC_PORT, _inject_macos, _macos_session
from test_usb_pairing_windows import _inject_windows, _w1_session
from test_web_audio_rx_start_status import _SessionLanRadio
from test_web_auth_compare_digest import _MemoryWriter
from test_web_usb_pairing_integration import _w3_padded_session

from rigplane.audio import usb_driver
from rigplane.audio._usb_adapt_windows import adapt_windows
from rigplane.audio._usb_pairing import (
    PairRefused,
    candidates_for_serial_port,
    pair,
)
from rigplane.audio._usb_raw_windows import RawWindows
from rigplane.audio.backend import AudioDeviceConfig
from rigplane.audio.usb_driver import UsbAudioDriver
from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.core.types import AudioCodec
from rigplane.core.usb_identity import UsbAudioIdentity, UsbFunctionId
from rigplane.profiles import get_radio_profile
from rigplane.web.server import WebConfig, WebServer


def _driver(backend, serial_port, usb_identity=None):
    return UsbAudioDriver(
        AudioDeviceConfig(),
        backend=backend,
        serial_port=serial_port,
        usb_identity=usb_identity,
    )


def _near(candidates):
    return {
        (candidate.portaudio_index, candidate.direction)
        for candidate in candidates
        if candidate.near_radio
    }


# --- the one pairing rule ----------------------------------------------------


def test_candidates_rule_offers_the_refusal_list_for_the_serial_hub():
    """On demand equals what ``pair()`` refuses with — the same ``_candidates()``."""
    snapshot = adapt_windows(_w1_session())
    refusal = pair(
        snapshot, "COM3", UsbAudioIdentity(frozenset({UsbFunctionId("0D8C", "0016")}))
    )
    assert isinstance(refusal, PairRefused)
    listed = candidates_for_serial_port(snapshot, "COM3")
    assert listed == refusal.candidates
    assert listed[0].near_radio
    assert _near(listed) == {(1, "capture"), (5, "render")}


def test_candidates_rule_without_a_serial_port_leaves_every_candidate_far():
    snapshot = adapt_windows(_w1_session())
    listed = candidates_for_serial_port(snapshot, "COM9")
    assert listed
    assert all(not candidate.near_radio for candidate in listed)


def test_endpoint_is_part_of_the_stable_surface():
    from rigplane.web.api_contract import STABLE_HTTP_ENDPOINTS

    http = {(route["method"], route["path"]) for route in STABLE_HTTP_ENDPOINTS}
    assert ("GET", "/api/v1/audio/candidates") in http


# --- driver: topology path, several radio profiles ---------------------------

W3_RADIOS = {
    "IC-7300": ("COM3", {(2, "capture"), (6, "render")}),
    "IC-7610": ("COM5", {(1, "capture"), (5, "render")}),
}


@pytest.mark.parametrize("model", W3_RADIOS)
async def test_driver_lists_topology_candidates_for_each_windows_radio(
    monkeypatch, model
):
    port, near = W3_RADIOS[model]
    backend, _ = _inject_windows(monkeypatch, _w3_padded_session())
    identity = get_radio_profile(model).usb_identity
    result = await _driver(backend, port, identity).audio_candidates()
    assert result.source == "usb_topology"
    assert _near(result.candidates) == near
    assert result.candidates[0].near_radio
    # No model branch: the list follows the serial port's hub, not the
    # profile, so a driver without any identity offers the same devices.
    bare = await _driver(backend, port).audio_candidates()
    assert bare == result


@pytest.mark.parametrize(
    "profile,port,near",
    [
        ("IC-705", MAC_PORT, {(2, "capture"), (1, "render")}),
        ("FTX-1", FTX_PORT, {(3, "capture"), (3, "render")}),
    ],
)
async def test_driver_lists_topology_candidates_for_each_macos_radio(
    monkeypatch, profile, port, near
):
    backend, _ = _inject_macos(monkeypatch, _macos_session())
    result = await _driver(
        backend, port, get_radio_profile(profile).usb_identity
    ).audio_candidates()
    assert result.source == "usb_topology"
    assert _near(result.candidates) == near


async def test_driver_lists_topology_candidates_on_linux(tmp_path, monkeypatch):
    backend, _, _, _ = _inject_linux(tmp_path, monkeypatch)
    result = await _driver(backend, LINUX_PORT).audio_candidates()
    assert result.source == "usb_topology"
    assert _near(result.candidates) == {(0, "capture"), (0, "render")}


# --- driver: late or missing Windows topology --------------------------------


def _timeout_topology(monkeypatch):
    original_run = usb_driver.bounded_portaudio_pool.run_bounded

    async def timed_out(fn, *, what, direction, timeout, **kwargs):
        if what == "optional USB topology":
            # The on-demand read keeps the selection path's 5 s cap.
            assert timeout == 5.0
            raise usb_driver.AudioCaptureOpenTimeoutError("topology timed out")
        return await original_run(
            fn, what=what, direction=direction, timeout=timeout, **kwargs
        )

    monkeypatch.setattr(usb_driver.bounded_portaudio_pool, "run_bounded", timed_out)


async def test_driver_late_windows_topology_lists_name_fallback(monkeypatch):
    backend, _ = _inject_windows(monkeypatch, _w3_padded_session())
    _timeout_topology(monkeypatch)
    result = await _driver(
        backend, "COM3", get_radio_profile("IC-7300").usb_identity
    ).audio_candidates()
    assert result.source == "name_heuristic"
    assert {
        (candidate.portaudio_index, candidate.direction)
        for candidate in result.candidates
    } >= {(1, "capture"), (2, "capture"), (5, "render"), (6, "render")}
    assert all(
        candidate.stable_id.startswith("MME|name:") and not candidate.near_radio
        for candidate in result.candidates
    )


async def test_driver_missing_windows_topology_lists_name_fallback(monkeypatch):
    backend, _ = _inject_windows(monkeypatch, _w3_padded_session())
    monkeypatch.setattr(
        "rigplane.audio._usb_raw_windows.read_raw_windows",
        lambda timeout_s: RawWindows("cfgmgr32", ()),
    )
    result = await _driver(
        backend, "COM3", get_radio_profile("IC-7300").usb_identity
    ).audio_candidates()
    assert result.source == "name_heuristic"
    assert result.candidates
    assert all(not candidate.near_radio for candidate in result.candidates)


# --- web endpoint ------------------------------------------------------------


async def _get(server, path):
    writer = _MemoryWriter()
    await server._handle_http(writer, "GET", path, {})
    status_line = writer.buffer.split(b"\r\n", 1)[0]
    body = json.loads(writer.buffer.split(b"\r\n\r\n", 1)[1])
    return status_line, body


def _icom_radio():
    return Ic7300SerialRadio(
        device="COM3",
        profile=get_radio_profile("IC-7300"),
        audio_codec=AudioCodec.PCM_1CH_16BIT,
        civ_link=_FakeSerialCivLink(model_id=0x94),
        _enumerate_serial_ports_fn=lambda: [],
    )


async def test_endpoint_serves_candidates_without_rx_start(monkeypatch):
    backend, _ = _inject_windows(monkeypatch, _w3_padded_session())
    monkeypatch.setattr(usb_driver, "PortAudioBackend", lambda: backend)
    radio = _icom_radio()
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    try:
        status_line, record = await _get(server, "/api/v1/audio/candidates")
        assert status_line.startswith(b"HTTP/1.1 200 ")
        assert record["schemaVersion"] == 1
        assert record["source"] == "usb_topology"
        assert record["candidates"][0]["nearRadio"] is True
        assert set(record["candidates"][0]) == {
            "stableId",
            "displayName",
            "direction",
            "hostApi",
            "portAudioIndex",
            "nearRadio",
            "unmatched",
        }
        assert {
            (candidate["portAudioIndex"], candidate["direction"])
            for candidate in record["candidates"]
            if candidate["nearRadio"]
        } == {(2, "capture"), (6, "render")}
        # At any time: no RX start, no stream opened, no session demand.
        assert radio.audio_session.rx_demand == 0
        assert not (backend.rx_streams or backend.tx_streams or backend.duplex_streams)
    finally:
        await server.stop()


async def test_endpoint_reports_name_fallback_source(monkeypatch):
    backend, _ = _inject_windows(monkeypatch, _w3_padded_session())
    monkeypatch.setattr(usb_driver, "PortAudioBackend", lambda: backend)
    _timeout_topology(monkeypatch)
    server = WebServer(_icom_radio(), WebConfig(host="127.0.0.1", port=0))
    try:
        status_line, record = await _get(server, "/api/v1/audio/candidates")
        assert status_line.startswith(b"HTTP/1.1 200 ")
        assert record["source"] == "name_heuristic"
        assert record["candidates"]
        assert all(not candidate["nearRadio"] for candidate in record["candidates"])
    finally:
        await server.stop()


async def test_endpoint_serves_the_yaesu_driver(monkeypatch):
    backend, _ = _inject_macos(monkeypatch, _macos_session())
    monkeypatch.setattr(usb_driver, "PortAudioBackend", lambda: backend)
    radio = YaesuCatRadio(device=FTX_PORT)
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    try:
        status_line, record = await _get(server, "/api/v1/audio/candidates")
        assert status_line.startswith(b"HTTP/1.1 200 ")
        assert record["source"] == "usb_topology"
        assert {
            (candidate["portAudioIndex"], candidate["direction"])
            for candidate in record["candidates"]
            if candidate["nearRadio"]
        } == {(3, "capture"), (3, "render")}
    finally:
        await server.stop()


async def test_endpoint_refuses_radios_without_a_usb_audio_driver():
    server = WebServer(_SessionLanRadio(), WebConfig(radio_model="IC-7610"))
    try:
        status_line, body = await _get(server, "/api/v1/audio/candidates")
        assert status_line.startswith(b"HTTP/1.1 404 ")
        assert body["error"] == "unavailable"
    finally:
        await server.stop()
