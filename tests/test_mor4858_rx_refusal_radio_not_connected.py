"""MOR-4858: RX audio refused with a typed reason when the radio is not answering.

A powered-off IC-7300 (USB chips present, identity read unanswered — Core
logs "identity not accepted") refused an RX audio request with the reason
``other`` and 0 frames: a customer reading only "other".

The shared RX admission (``AudioBroadcaster._start_relay``, all radios, no
per-radio branch) must report the typed refusal
``radio_not_connected`` — from the shared connect-time identity hold
(``serial_identity_hold``) — instead of ``other`` whenever Core already
knows the control link is not connected/answering. A normally answering
radio keeps the old classification (``other`` for an unclassified audio
failure) and still admits when its audio leg works.

Two seams pin this: the admission-level tests below drive
``AudioBroadcaster.subscribe`` against real backends, and the end-to-end
tests send ``audio_start`` through the real ``/api/v1/audio`` WebSocket of
the real web app and read the same retained reason back from
``/api/v1/info`` (the P1 seam: a regression that keeps the frame from
reaching the broadcaster, or drops the fact from ``/api/v1/info``, leaves
the admission tests green).

No test waits in real time: the ``virtual_clock`` fixture parks every loop
timer, ``_run`` pumps ``elapse`` until the connect completes, and the
CI-V fake's ``silence()`` window reads the same clock (``scope_to``), so a
silent connect costs virtual — not real — time.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Coroutine
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, TypeVar

import pytest
from test_icom7610_serial_radio import _FakeUsbAudioDriver
from test_web_server import (
    _close_ws,
    _http_get,
    _ws_connect,
    _ws_recv_frame,
    _ws_send_text,
)

from rigplane.audio.usb_driver import AudioDeviceSelectionError
from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.backends.icom7610.drivers.serial_civ_link import SerialCivLink
from rigplane.backends.yaesu_cat.radio import YaesuCatRadio
from rigplane.backends.yaesu_cat.transport import (
    CatTimeoutError,
    CatTransportError,
)
from rigplane.core.radio_protocol import RadioIdentityStatus, serial_identity_hold
from rigplane.rig_loader import load_rig
from rigplane.testing.timing import VirtualClock
from rigplane.web.handlers.audio import AudioBroadcaster
from rigplane.web.server import WebConfig, WebServer
from rigplane.ws import WS_OP_TEXT
from rigplane_testkit.serial import CivSerialFake
from rigplane_testkit.serial import civ_serial as civ_serial_module

_RIGS_DIR = Path(__file__).parents[1] / "rigs"
_DEVICE = "mor4858-civ-fake"  # synthetic node name; no real path is opened
_IF_FRAME = "IF00000014280000+000000200003"

NEW_REASON = "radio_not_connected"
_EXPECTED_ERROR_FRAME = {
    "type": "error",
    "message": f"audio_start: RX audio failed to start: {NEW_REASON}",
    "reason": NEW_REASON,
}

T = TypeVar("T")


async def _run(
    clock: VirtualClock, coro: Coroutine[Any, Any, T], *, limit: float = 60.0
) -> T:
    """Drive ``coro`` to completion on the virtual clock.

    The repo's shared pattern (``_Link.run`` in
    ``test_mor4384_wire_order.py``): the ``virtual_clock`` fixture parks
    every loop timer, so each ``elapse`` step advances timers and settles
    real socket I/O without spending real time.
    """
    task = asyncio.ensure_future(coro)  # type: ignore[arg-type]
    spent = 0.0
    while not task.done():
        assert spent < limit, "the virtual run never finished"
        await clock.elapse(0.01)
        spent += 0.01
    return task.result()


class _RecordingWs:
    """Minimal client double: records the text frames the broadcaster sends."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_text(self, payload: str) -> None:
        self.sent.append(payload)

    def is_alive(self) -> bool:
        return True


class _RefusingUsbAudioDriver(_FakeUsbAudioDriver):
    """Fake USB driver whose ``start_rx`` raises an unclassified error.

    The repo's fake audio backend convention (``_FakeUsbAudioDriver`` from
    ``test_icom7610_serial_radio``): the exception is deliberately NOT one
    of the classified audio failures, so the admission classifies it as
    ``other`` — the classification the hold override (or its absence)
    then decides on.
    """

    async def start_rx(self, callback, **kwargs) -> None:  # type: ignore[no-untyped-def]
        raise AudioDeviceSelectionError("no USB audio device matches the radio")


# ---------------------------------------------------------------------------
# Icom personality: real IC-7300 serial backend over the in-memory CI-V fake
# ---------------------------------------------------------------------------


def _make_icom(*, silent: bool, refusing_audio: bool = False) -> Ic7300SerialRadio:
    fake = CivSerialFake()
    if silent:
        # The powered-off radio: the port opens, nothing answers — echo
        # included. Virtual cost only (the fake's window reads the scoped
        # virtual clock, set by the caller).
        fake.silence(30.0)
    link = SerialCivLink(
        device=_DEVICE, open_serial_connection=fake.open_serial_connection
    )
    audio_driver = (
        _RefusingUsbAudioDriver() if refusing_audio else _FakeUsbAudioDriver()
    )
    radio = Ic7300SerialRadio(
        device=_DEVICE,
        civ_link=link,
        audio_driver=audio_driver,
        _enumerate_serial_ports_fn=lambda: [],  # no real OS port enumeration
    )
    # The identity answer window (a silent connect sweeps the connect-order
    # bauds, one window each). Virtual under the ``virtual_clock`` fixture:
    # no real waiting.
    radio._civ_get_timeout = 0.05
    return radio


# ---------------------------------------------------------------------------
# Yaesu personality: real FTX-1 CAT backend over a never-answering transport
# ---------------------------------------------------------------------------


class _SilentCatTransport:
    """CAT transport whose radio never answers (powered off)."""

    def __init__(self) -> None:
        self.connected = False
        self.stats = SimpleNamespace(reconnects=0)
        self.last_valid_frame_monotonic: float | None = None
        self.queries: list[str] = []
        self.baudrate = 38400

    def bind_provider_generation(self, _capture) -> None:
        pass

    def set_baudrate(self, baudrate: int) -> bool:
        self.baudrate = baudrate
        return True

    async def connect(self) -> None:
        self.connected = True

    async def close(self) -> None:
        self.connected = False
        self.last_valid_frame_monotonic = None

    async def query(self, command: str, **_: object) -> str:
        self.queries.append(command)
        if not self.connected:
            raise CatTransportError("Transport not connected")
        raise CatTimeoutError(f"Read timeout waiting for {command!r}")

    async def write(self, command: str, **_: object) -> None:
        if not self.connected:
            raise CatTransportError("Transport not connected")


def _make_yaesu(
    *,
    answers: list[str],
    refusing_audio: bool = False,
) -> YaesuCatRadio:
    config = replace(load_rig(_RIGS_DIR / "ftx1.toml"), expected_identity_ids=("0840",))
    audio_driver = (
        _RefusingUsbAudioDriver() if refusing_audio else _FakeUsbAudioDriver()
    )
    radio = YaesuCatRadio(
        "/dev/fake-cat",
        profile=config,
        audio_driver=audio_driver,
    )
    radio._transport = (
        _SilentCatTransport() if not answers else _ScriptedCatTransport(answers)
    )
    return radio


class _ScriptedCatTransport(_SilentCatTransport):
    """Silent transport that answers the scripted queries, then goes silent."""

    def __init__(self, answers: list[str]) -> None:
        super().__init__()
        self._answers = list(answers)

    async def query(self, command: str, **_: object) -> str:
        self.queries.append(command)
        if not self.connected:
            raise CatTransportError("Transport not connected")
        if self._answers:
            return self._answers.pop(0)
        raise CatTimeoutError(f"Read timeout waiting for {command!r}")


# ---------------------------------------------------------------------------
# Not answering -> the new typed refusal, on the frame AND the retained fact
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
async def test_icom_not_answering_refuses_rx_with_radio_not_connected(
    virtual_clock: VirtualClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    virtual_clock.scope_to(monkeypatch, civ_serial_module)
    radio = _make_icom(silent=True)
    await _run(virtual_clock, radio.connect())
    try:
        assert radio.connection_identity is not None
        assert radio.connection_identity.status is RadioIdentityStatus.NO_RESPONSE
        assert radio.connected is False
        assert serial_identity_hold(radio) is RadioIdentityStatus.NO_RESPONSE

        broadcaster = AudioBroadcaster(radio)
        ws = _RecordingWs()
        queue = await broadcaster.subscribe(ws=ws)
        try:
            status = broadcaster.rx_start_status()
            assert status == {
                "schemaVersion": 1,
                "failure": {
                    "stage": "rx_start",
                    "code": "core_rx_start_failed",
                    "reason": NEW_REASON,
                },
            }
            assert [json.loads(frame) for frame in ws.sent] == [_EXPECTED_ERROR_FRAME]
        finally:
            await broadcaster.unsubscribe(queue)
            await broadcaster._stop_relay()
    finally:
        await radio.disconnect()


@pytest.mark.timeout(60)
async def test_yaesu_not_answering_refuses_rx_with_radio_not_connected(
    virtual_clock: VirtualClock,
) -> None:
    radio = _make_yaesu(answers=[])
    await _run(virtual_clock, radio.connect())
    try:
        assert radio.connection_identity is not None
        assert radio.connection_identity.status is RadioIdentityStatus.NO_RESPONSE
        assert radio.connected is False
        assert serial_identity_hold(radio) is RadioIdentityStatus.NO_RESPONSE

        broadcaster = AudioBroadcaster(radio)
        ws = _RecordingWs()
        queue = await broadcaster.subscribe(ws=ws)
        try:
            status = broadcaster.rx_start_status()
            assert status == {
                "schemaVersion": 1,
                "failure": {
                    "stage": "rx_start",
                    "code": "core_rx_start_failed",
                    "reason": NEW_REASON,
                },
            }
            assert [json.loads(frame) for frame in ws.sent] == [_EXPECTED_ERROR_FRAME]
        finally:
            await broadcaster.unsubscribe(queue)
            await broadcaster._stop_relay()
    finally:
        await radio.disconnect()


# ---------------------------------------------------------------------------
# End to end (P1): the same refusal through the real audio WS and /api/v1/info
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
async def test_icom_not_answering_refusal_reaches_ws_client_and_info(
    virtual_clock: VirtualClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Silent IC-7300: ``audio_start`` over the real WS reports the typed reason.

    Fails on the old code: the frame and the retained fact both carried
    ``other`` before the hold override.
    """
    virtual_clock.scope_to(monkeypatch, civ_serial_module)
    radio = _make_icom(silent=True)
    await _run(virtual_clock, radio.connect())
    try:
        assert serial_identity_hold(radio) is RadioIdentityStatus.NO_RESPONSE
        server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
        try:
            server._server = await asyncio.start_server(
                server._accept_client, "127.0.0.1", 0
            )
            reader, writer, _ = await _ws_connect(
                "127.0.0.1", server.port, "/api/v1/audio"
            )
            try:
                await _ws_send_text(
                    writer,
                    json.dumps({"type": "audio_start", "direction": "rx"}),
                )
                messages = []
                # The clock stays frozen here: the timeout below can never
                # fire spuriously — the read completes on real socket data,
                # and ``pytest.mark.timeout`` guards a server that stays silent.
                async with asyncio.timeout(5):
                    while True:
                        opcode, payload = await _ws_recv_frame(reader)
                        assert opcode == WS_OP_TEXT
                        message = json.loads(payload)
                        messages.append(message)
                        if message["type"] == "audio_format":
                            break
                errors = [m for m in messages if m["type"] == "error"]
                assert errors == [_EXPECTED_ERROR_FRAME]
                status, _, body = await _http_get(
                    "127.0.0.1", server.port, "/api/v1/info"
                )
                assert status == 200
                failure = json.loads(body)["audioReceive"]["failure"]
                assert failure["reason"] == NEW_REASON
            finally:
                await _close_ws(writer)
        finally:
            await server.stop()
    finally:
        await radio.disconnect()


@pytest.mark.timeout(60)
async def test_yaesu_not_answering_refusal_reaches_ws_client_and_info(
    virtual_clock: VirtualClock,
) -> None:
    """Silent FTX-1: ``audio_start`` over the real WS reports the typed reason.

    Fails on the old code: the frame and the retained fact both carried
    ``other`` before the hold override.
    """
    radio = _make_yaesu(answers=[])
    await _run(virtual_clock, radio.connect())
    try:
        assert serial_identity_hold(radio) is RadioIdentityStatus.NO_RESPONSE
        server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
        try:
            server._server = await asyncio.start_server(
                server._accept_client, "127.0.0.1", 0
            )
            reader, writer, _ = await _ws_connect(
                "127.0.0.1", server.port, "/api/v1/audio"
            )
            try:
                await _ws_send_text(
                    writer,
                    json.dumps({"type": "audio_start", "direction": "rx"}),
                )
                messages = []
                # The clock stays frozen here: the timeout below can never
                # fire spuriously — the read completes on real socket data,
                # and ``pytest.mark.timeout`` guards a server that stays silent.
                async with asyncio.timeout(5):
                    while True:
                        opcode, payload = await _ws_recv_frame(reader)
                        assert opcode == WS_OP_TEXT
                        message = json.loads(payload)
                        messages.append(message)
                        if message["type"] == "audio_format":
                            break
                errors = [m for m in messages if m["type"] == "error"]
                assert errors == [_EXPECTED_ERROR_FRAME]
                status, _, body = await _http_get(
                    "127.0.0.1", server.port, "/api/v1/info"
                )
                assert status == 200
                failure = json.loads(body)["audioReceive"]["failure"]
                assert failure["reason"] == NEW_REASON
            finally:
                await _close_ws(writer)
        finally:
            await server.stop()
    finally:
        await radio.disconnect()


# ---------------------------------------------------------------------------
# Answering radio: unclassified audio failure stays `other`, working audio admits
# ---------------------------------------------------------------------------


@pytest.mark.timeout(60)
async def test_icom_answering_with_failing_audio_still_reports_other(
    virtual_clock: VirtualClock,
) -> None:
    radio = _make_icom(silent=False, refusing_audio=True)
    await _run(virtual_clock, radio.connect())
    try:
        assert radio.connected is True
        assert serial_identity_hold(radio) is None

        broadcaster = AudioBroadcaster(radio)
        ws = _RecordingWs()
        queue = await broadcaster.subscribe(ws=ws)
        try:
            status = broadcaster.rx_start_status()
            assert status["failure"] is not None
            assert status["failure"]["reason"] == "other"
            assert [json.loads(frame) for frame in ws.sent] == [
                {
                    "type": "error",
                    "message": "audio_start: RX audio failed to start: other",
                    "reason": "other",
                }
            ]
        finally:
            await broadcaster.unsubscribe(queue)
            await broadcaster._stop_relay()
    finally:
        await radio.disconnect()


@pytest.mark.timeout(60)
async def test_yaesu_answering_with_failing_audio_still_reports_other(
    virtual_clock: VirtualClock,
) -> None:
    radio = _make_yaesu(
        answers=["ID0840", _IF_FRAME],
        refusing_audio=True,
    )
    await _run(virtual_clock, radio.connect())
    try:
        assert radio.connected is True
        assert serial_identity_hold(radio) is None

        broadcaster = AudioBroadcaster(radio)
        ws = _RecordingWs()
        queue = await broadcaster.subscribe(ws=ws)
        try:
            status = broadcaster.rx_start_status()
            assert status["failure"] is not None
            assert status["failure"]["reason"] == "other"
            assert [json.loads(frame) for frame in ws.sent] == [
                {
                    "type": "error",
                    "message": "audio_start: RX audio failed to start: other",
                    "reason": "other",
                }
            ]
        finally:
            await broadcaster.unsubscribe(queue)
            await broadcaster._stop_relay()
    finally:
        await radio.disconnect()


@pytest.mark.timeout(60)
async def test_icom_answering_with_working_audio_admits(
    virtual_clock: VirtualClock,
) -> None:
    radio = _make_icom(silent=False)
    await _run(virtual_clock, radio.connect())
    try:
        assert radio.connected is True
        broadcaster = AudioBroadcaster(radio)
        ws = _RecordingWs()
        queue = await broadcaster.subscribe(ws=ws)
        try:
            assert broadcaster.rx_start_status() == {
                "schemaVersion": 1,
                "failure": None,
            }
            assert broadcaster._subscription is not None
            assert ws.sent == []
        finally:
            await broadcaster.unsubscribe(queue)
            await broadcaster._stop_relay()
    finally:
        await radio.disconnect()


@pytest.mark.timeout(60)
async def test_yaesu_answering_with_working_audio_admits(
    virtual_clock: VirtualClock,
) -> None:
    radio = _make_yaesu(answers=["ID0840", _IF_FRAME])
    await _run(virtual_clock, radio.connect())
    try:
        assert radio.connected is True
        broadcaster = AudioBroadcaster(radio)
        ws = _RecordingWs()
        queue = await broadcaster.subscribe(ws=ws)
        try:
            assert broadcaster.rx_start_status() == {
                "schemaVersion": 1,
                "failure": None,
            }
            assert broadcaster._subscription is not None
            assert ws.sent == []
        finally:
            await broadcaster.unsubscribe(queue)
            await broadcaster._stop_relay()
    finally:
        await radio.disconnect()
