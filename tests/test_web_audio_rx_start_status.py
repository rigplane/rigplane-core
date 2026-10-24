from __future__ import annotations

import asyncio
import json

import pytest
from test_web_audio_session_rx import _NoSessionRadio, _SessionLanRadio, _make_ws
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


@pytest.mark.parametrize("retire", [False, True])
@pytest.mark.parametrize("deferred", [False, True], ids=["start", "session-push"])
async def test_tx_refusal_retains_candidates_only_for_current_radio(
    station, retire, deferred
):
    from rigplane.audio._usb_pairing import (
        AudioCandidate,
        PairRefused,
        WindowsAudioTopologyError,
    )
    from rigplane.web.handlers.audio import AudioHandler, browser_tx_audio_facts
    from rigplane.web.protocol import (
        AUDIO_CODEC_PCM16,
        AUDIO_HEADER_SIZE,
        MSG_TYPE_AUDIO_TX,
    )

    server, _, broadcaster = station
    entered, release = asyncio.Event(), asyncio.Event()

    class RefusingTxRadio(_SessionLanRadio if deferred else _NoSessionRadio):
        capabilities = {"audio", "tx"}
        has_usb_audio = True

        async def start_tx(self):
            entered.set()
            await release.wait()
            raise WindowsAudioTopologyError(
                PairRefused(
                    "no_codec",
                    "usb_topology_unavailable",
                    (
                        AudioCandidate(
                            "MME|capture", "USB Audio", "capture", "MME", 1, True
                        ),
                    ),
                    (),
                )
            )

    old = RefusingTxRadio()
    server._radio = broadcaster._radio = old
    assert browser_tx_audio_facts(old).path == ("session" if deferred else "neutral")
    handler = AudioHandler(_make_ws(), old, broadcaster=broadcaster)
    start = {"type": "audio_start", "direction": "tx"}
    if deferred:
        await handler._handle_control(start)
        frame = (
            bytes([MSG_TYPE_AUDIO_TX, AUDIO_CODEC_PCM16])
            + bytes(AUDIO_HEADER_SIZE - 2)
            + b"\x01\x00" * 960
        )
        pending = asyncio.create_task(handler._handle_tx_audio(frame))
    else:
        pending = asyncio.create_task(handler._handle_control(start))
    try:
        await asyncio.wait_for(entered.wait(), timeout=1)
        if retire:
            replacement = RefusingRadio(fails=False)
            broadcaster._radio = replacement
            server._radio = replacement
            server._on_provider_generation(11)
    finally:
        release.set()
        await pending
    try:
        status = await info(server)
        if retire:
            assert status == CLEAR
        else:
            assert status == {
                "schemaVersion": 1,
                "failure": {
                    "stage": "tx_start",
                    "code": "core_tx_start_failed",
                    "reason": "usb_topology_unavailable",
                    "candidates": [
                        {
                            "stableId": "MME|capture",
                            "displayName": "USB Audio",
                            "direction": "capture",
                            "hostApi": "MME",
                            "portAudioIndex": 1,
                            "nearRadio": True,
                        }
                    ],
                },
            }
        assert not handler._tx_active
    finally:
        await handler._stop_tx(reason="test cleanup", force=True)


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
