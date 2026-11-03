from __future__ import annotations

import asyncio
import json

import pytest
from test_web_audio_session_rx import _SessionLanRadio, _make_ws
from test_web_auth_compare_digest import _MemoryWriter

from rigplane.web.server import WebConfig, WebServer


class RefusingRadio(_SessionLanRadio):
    def __init__(self, *, fails=True):
        super().__init__()
        self.fails = fails
        self.entered = asyncio.Event()
        self.gate = None

    async def start_rx(self, callback, *, jitter_depth=None):
        self.entered.set()
        if self.gate is not None:
            await self.gate.wait()
        if self.fails:
            raise OSError("private device /private/example could not open")
        await super().start_rx(callback, jitter_depth=jitter_depth)


@pytest.fixture
async def station():
    radio = RefusingRadio()
    server = WebServer(radio, WebConfig(radio_model="IC-7610"))
    yield server, radio, server._audio_broadcaster
    await server._audio_broadcaster._stop_relay()


async def info(server):
    writer = _MemoryWriter()
    await server._handle_http(writer, "GET", "/api/v1/info", {})
    assert writer.buffer.startswith(b"HTTP/1.1 200 ")
    return json.loads(writer.buffer.split(b"\r\n\r\n", 1)[1])["audioReceive"]


FAILED = {
    "schemaVersion": 1,
    "failure": {"stage": "rx_start", "code": "core_rx_start_failed", "reason": "other"},
}
CLEAR = {"schemaVersion": 1, "failure": None}


@pytest.mark.parametrize("with_ws", [True, False])
async def test_actual_browser_start_refusal_is_retained_after_client_close(
    station, with_ws
):
    server, radio, broadcaster = station
    assert await info(server) == CLEAR
    ws = _make_ws() if with_ws else None
    queue = await broadcaster.subscribe(ws=ws)
    assert radio.entered.is_set()
    assert radio.audio_session.rx_demand == 0
    assert await info(server) == FAILED
    if ws is not None:
        ws.send_text.assert_awaited_once()
    await broadcaster.unsubscribe(queue)
    assert await info(server) == FAILED
    radio.fails = False
    await broadcaster.ensure_relay()
    assert radio.audio_session.rx_demand == 1
    assert await info(server) == CLEAR


async def test_tap_start_refusal_is_visible_without_any_ws(station):
    server, _, broadcaster = station
    await broadcaster.ensure_relay()
    assert not broadcaster._client_ws
    assert await info(server) == FAILED


async def test_provider_retirement_clears_fact_then_new_refusal_sets_it(station):
    server, _, broadcaster = station
    await broadcaster.ensure_relay()
    assert await info(server) == FAILED
    server._on_provider_generation(9)
    assert await info(server) == CLEAR
    await broadcaster.ensure_relay()
    assert await info(server) == FAILED


async def test_source_replacement_does_not_expose_retired_failure(station):
    server, _, broadcaster = station
    await broadcaster.ensure_relay()
    assert await info(server) == FAILED
    replacement = RefusingRadio(fails=False)
    broadcaster._radio = replacement
    server._radio = replacement
    assert await info(server) == CLEAR
    await broadcaster.ensure_relay()
    assert await info(server) == CLEAR


@pytest.mark.parametrize("old_fails", [True, False])
async def test_late_retired_attempt_cannot_overwrite_current_source_fact(
    station, old_fails
):
    server, old, broadcaster = station
    old.fails = old_fails
    old.gate = asyncio.Event()
    pending = asyncio.create_task(broadcaster.ensure_relay())
    await asyncio.wait_for(old.entered.wait(), timeout=1)
    try:
        server._on_provider_generation(10)
        current = RefusingRadio(fails=not old_fails)
        broadcaster._radio = current
        server._radio = current
        await broadcaster._start_relay()
        expected = CLEAR if old_fails else FAILED
        assert await info(server) == expected
        current_subscription = broadcaster._subscription
    finally:
        old.gate.set()
        await pending
    assert await info(server) == expected
    assert broadcaster._subscription is current_subscription
    assert old.audio_session.rx_demand == 0


@pytest.mark.parametrize("old_fails", [True, False])
async def test_provider_retirement_rejects_late_rx_result_with_same_radio(
    station, old_fails
):
    server, radio, broadcaster = station
    radio.fails = old_fails
    radio.gate = asyncio.Event()
    source = radio.audio_session
    pending = asyncio.create_task(broadcaster.ensure_relay())
    try:
        await asyncio.wait_for(radio.entered.wait(), timeout=1)
        server._on_provider_generation(12)
        assert broadcaster._radio is radio
        assert radio.audio_session is source
        assert await info(server) == CLEAR
    finally:
        radio.gate.set()
        await pending
    assert await info(server) == CLEAR
    assert broadcaster._subscription is None
    assert source.rx_demand == 0
    radio.gate = None
    radio.fails = True
    await broadcaster.ensure_relay()
    assert await info(server) == FAILED


def _chained(outer, inner):
    outer.__cause__ = inner
    return outer


def _rx_start_failures():
    from rigplane.audio.usb_driver import (
        AudioCaptureOpenTimeoutError,
        AudioDeviceAmbiguousError,
        AudioDeviceSelectionError,
        AudioFormatUnsupportedError,
        AudioTopologyUnavailableError,
    )
    from rigplane.audio.backend import AudioDeviceBusyError
    from rigplane.audio._usb_pairing import PairRefused, WindowsAudioTopologyError

    private = "private device /private/example could not open"
    return {
        "usb_topology_names": _chained(
            AudioDeviceSelectionError(private),
            WindowsAudioTopologyError(
                PairRefused("endpoint_names", "usb_topology_names", (), (private,))
            ),
        ),
        "usb_topology_unavailable": AudioTopologyUnavailableError(private),
        "usb_topology_unavailable-graph": _chained(
            AudioDeviceSelectionError(private),
            WindowsAudioTopologyError(
                PairRefused("no_codec", "usb_topology_unavailable", (), (private,))
            ),
        ),
        "usb_ambiguous": AudioDeviceAmbiguousError(private),
        "usb_ambiguous-graph": _chained(
            AudioDeviceSelectionError(private),
            WindowsAudioTopologyError(
                PairRefused("contested", "usb_ambiguous", (), (private,))
            ),
        ),
        "open_timeout": AudioCaptureOpenTimeoutError(private),
        "device_in_use": AudioDeviceBusyError(private, device_index=7),
        "format": AudioFormatUnsupportedError(private),
        "other": AudioDeviceSelectionError(private),
        "other-os": OSError(private),
    }


@pytest.mark.parametrize(
    "case",
    [
        "usb_topology_names",
        "usb_topology_unavailable",
        "usb_topology_unavailable-graph",
        "usb_ambiguous",
        "usb_ambiguous-graph",
        "open_timeout",
        "device_in_use",
        "format",
        "other",
        "other-os",
    ],
)
async def test_refusal_reports_its_redacted_reason_category(station, case):
    server, radio, broadcaster = station
    error = _rx_start_failures()[case]

    async def refuse(callback, *, jitter_depth=None):
        raise error

    radio.start_rx = refuse
    await broadcaster.ensure_relay()
    status = await info(server)
    assert status == {
        "schemaVersion": 1,
        "failure": {
            "stage": "rx_start",
            "code": "core_rx_start_failed",
            "reason": case.split("-")[0],
        },
    }
    assert "private" not in json.dumps(status)


async def test_topology_unavailable_exposes_typed_candidates(station):
    from rigplane.audio._usb_pairing import AudioCandidate
    from rigplane.audio.usb_driver import AudioTopologyUnavailableError

    server, radio, broadcaster = station
    candidate = AudioCandidate("MME|name:Mic", "Mic", "capture", "MME", 3, False)
    error = AudioTopologyUnavailableError("unavailable", candidates=(candidate,))

    async def refuse(callback, *, jitter_depth=None):
        raise error

    radio.start_rx = refuse
    await broadcaster.ensure_relay()
    failure = (await info(server))["failure"]
    assert failure["reason"] == "usb_topology_unavailable"
    assert failure["candidates"] == [
        {
            "stableId": "MME|name:Mic",
            "displayName": "Mic",
            "direction": "capture",
            "hostApi": "MME",
            "portAudioIndex": 3,
            "nearRadio": False,
        }
    ]


class _BusyRadio(RefusingRadio):
    def __init__(self, holder):
        super().__init__()
        self.holder = holder

    async def start_rx(self, callback, *, jitter_depth=None):
        from rigplane.audio.backend import AudioDeviceBusyError

        raise AudioDeviceBusyError(
            "private device /private/example is in use",
            device_index=19,
            holder=self.holder,
        )


async def _busy_station(holder):
    radio = _BusyRadio(holder)
    server = WebServer(radio, WebConfig(radio_model="IC-7610"))
    return server, radio, server._audio_broadcaster


@pytest.mark.parametrize(
    "holder,expected",
    [("wsjtx.exe", {"holder": "wsjtx.exe"}), (None, {})],
)
async def test_in_use_refusal_names_the_holder_in_frame_and_status(holder, expected):
    server, _radio, broadcaster = await _busy_station(holder)
    try:
        ws = _make_ws()
        await broadcaster.subscribe(ws=ws)
        frame = json.loads(ws.send_text.await_args[0][0])
        assert frame == {
            "type": "error",
            "message": "audio_start: RX audio failed to start: device_in_use",
            "reason": "device_in_use",
            **expected,
        }
        assert await info(server) == {
            "schemaVersion": 1,
            "failure": {
                "stage": "rx_start",
                "code": "core_rx_start_failed",
                "reason": "device_in_use",
                **expected,
            },
        }
        assert "private" not in json.dumps(frame)
    finally:
        await broadcaster._stop_relay()


async def test_a_holder_name_never_rides_a_refusal_of_another_category(station):
    server, radio, broadcaster = station
    ws = _make_ws()
    await broadcaster.subscribe(ws=ws)
    frame = json.loads(ws.send_text.await_args[0][0])
    assert "holder" not in frame
    assert "holder" not in (await info(server))["failure"]
