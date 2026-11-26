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
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

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
from rigplane.web.handlers.audio import AudioBroadcaster
from rigplane_testkit.serial import CivSerialFake

_RIGS_DIR = Path(__file__).parents[1] / "rigs"
_DEVICE = "mor4858-civ-fake"  # synthetic node name; no real path is opened
_IF_FRAME = "IF00000014280000+000000200003"

NEW_REASON = "radio_not_connected"


def _make_ws() -> SimpleNamespace:
    return SimpleNamespace(send_text=AsyncMock())


def _audio_driver(*, refusing: bool = False) -> SimpleNamespace:
    """USB-audio driver double: silent RX leg, no PortAudio."""
    if refusing:
        start_rx = AsyncMock(
            side_effect=AudioDeviceSelectionError(
                "no USB audio device matches the radio"
            )
        )
    else:
        start_rx = AsyncMock()
    return SimpleNamespace(start_rx=start_rx, stop_rx=AsyncMock(), stop_tx=AsyncMock())


# ---------------------------------------------------------------------------
# Icom personality: real IC-7300 serial backend over the in-memory CI-V fake
# ---------------------------------------------------------------------------


def _make_icom(*, silent: bool, refusing_audio: bool = False) -> Ic7300SerialRadio:
    fake = CivSerialFake()
    if silent:
        # The powered-off radio: the port opens, nothing answers — echo
        # included. Costs no real time (monotonic window).
        fake.silence(30.0)
    link = SerialCivLink(
        device=_DEVICE, open_serial_connection=fake.open_serial_connection
    )
    radio = Ic7300SerialRadio(
        device=_DEVICE,
        civ_link=link,
        audio_driver=_audio_driver(refusing=refusing_audio),
        _enumerate_serial_ports_fn=lambda: [],  # no real OS port enumeration
    )
    # Shrink the identity answer window: the silent connect sweeps the
    # connect-order bauds, one window each.
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
    monkeypatch: pytest.MonkeyPatch,
) -> YaesuCatRadio:
    config = replace(load_rig(_RIGS_DIR / "ftx1.toml"), expected_identity_ids=("0840",))
    radio = YaesuCatRadio(
        "/dev/fake-cat",
        profile=config,
        audio_driver=_audio_driver(refusing=refusing_audio),
    )
    radio._transport = (
        _SilentCatTransport() if not answers else _ScriptedCatTransport(answers)
    )
    # The hold's background re-read must not pace the test in real time.
    monkeypatch.setattr(YaesuCatRadio, "_IDENTITY_REREAD_BACKOFF_S", (0.01,))
    monkeypatch.setattr(YaesuCatRadio, "_IDENTITY_REREAD_STEADY_S", 0.01)
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


async def test_icom_not_answering_refuses_rx_with_radio_not_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Ic7300SerialRadio, "_SERIAL_IDENTITY_REREAD_BACKOFF_S", (0.01,))
    monkeypatch.setattr(Ic7300SerialRadio, "_SERIAL_IDENTITY_REREAD_STEADY_S", 0.01)
    radio = _make_icom(silent=True)
    await radio.connect()
    try:
        assert radio.connection_identity is not None
        assert radio.connection_identity.status is RadioIdentityStatus.NO_RESPONSE
        assert radio.connected is False
        assert serial_identity_hold(radio) is RadioIdentityStatus.NO_RESPONSE

        broadcaster = AudioBroadcaster(radio)
        ws = _make_ws()
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
            ws.send_text.assert_awaited_once()
            frame = json.loads(ws.send_text.await_args[0][0])
            assert frame == {
                "type": "error",
                "message": f"audio_start: RX audio failed to start: {NEW_REASON}",
                "reason": NEW_REASON,
            }
        finally:
            await broadcaster.unsubscribe(queue)
            await broadcaster._stop_relay()
    finally:
        await radio.disconnect()


async def test_yaesu_not_answering_refuses_rx_with_radio_not_connected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio = _make_yaesu(answers=[], monkeypatch=monkeypatch)
    await radio.connect()
    try:
        assert radio.connection_identity is not None
        assert radio.connection_identity.status is RadioIdentityStatus.NO_RESPONSE
        assert radio.connected is False
        assert serial_identity_hold(radio) is RadioIdentityStatus.NO_RESPONSE

        broadcaster = AudioBroadcaster(radio)
        ws = _make_ws()
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
            ws.send_text.assert_awaited_once()
            frame = json.loads(ws.send_text.await_args[0][0])
            assert frame == {
                "type": "error",
                "message": f"audio_start: RX audio failed to start: {NEW_REASON}",
                "reason": NEW_REASON,
            }
        finally:
            await broadcaster.unsubscribe(queue)
            await broadcaster._stop_relay()
    finally:
        await radio.disconnect()


# ---------------------------------------------------------------------------
# Answering radio: unclassified audio failure stays `other`, working audio admits
# ---------------------------------------------------------------------------


async def test_icom_answering_with_failing_audio_still_reports_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Ic7300SerialRadio, "_SERIAL_IDENTITY_REREAD_BACKOFF_S", (0.01,))
    monkeypatch.setattr(Ic7300SerialRadio, "_SERIAL_IDENTITY_REREAD_STEADY_S", 0.01)
    radio = _make_icom(silent=False, refusing_audio=True)
    await radio.connect()
    try:
        assert radio.connected is True
        assert serial_identity_hold(radio) is None

        broadcaster = AudioBroadcaster(radio)
        ws = _make_ws()
        queue = await broadcaster.subscribe(ws=ws)
        try:
            status = broadcaster.rx_start_status()
            assert status["failure"] is not None
            assert status["failure"]["reason"] == "other"
            frame = json.loads(ws.send_text.await_args[0][0])
            assert frame["reason"] == "other"
        finally:
            await broadcaster.unsubscribe(queue)
            await broadcaster._stop_relay()
    finally:
        await radio.disconnect()


async def test_yaesu_answering_with_failing_audio_still_reports_other(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio = _make_yaesu(
        answers=["ID0840", _IF_FRAME],
        refusing_audio=True,
        monkeypatch=monkeypatch,
    )
    await radio.connect()
    try:
        assert radio.connected is True
        assert serial_identity_hold(radio) is None

        broadcaster = AudioBroadcaster(radio)
        ws = _make_ws()
        queue = await broadcaster.subscribe(ws=ws)
        try:
            status = broadcaster.rx_start_status()
            assert status["failure"] is not None
            assert status["failure"]["reason"] == "other"
            frame = json.loads(ws.send_text.await_args[0][0])
            assert frame["reason"] == "other"
        finally:
            await broadcaster.unsubscribe(queue)
            await broadcaster._stop_relay()
    finally:
        await radio.disconnect()


async def test_icom_answering_with_working_audio_admits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Ic7300SerialRadio, "_SERIAL_IDENTITY_REREAD_BACKOFF_S", (0.01,))
    monkeypatch.setattr(Ic7300SerialRadio, "_SERIAL_IDENTITY_REREAD_STEADY_S", 0.01)
    radio = _make_icom(silent=False)
    await radio.connect()
    try:
        assert radio.connected is True
        broadcaster = AudioBroadcaster(radio)
        ws = _make_ws()
        queue = await broadcaster.subscribe(ws=ws)
        try:
            assert broadcaster.rx_start_status() == {
                "schemaVersion": 1,
                "failure": None,
            }
            assert broadcaster._subscription is not None
            ws.send_text.assert_not_awaited()
        finally:
            await broadcaster.unsubscribe(queue)
            await broadcaster._stop_relay()
    finally:
        await radio.disconnect()


async def test_yaesu_answering_with_working_audio_admits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    radio = _make_yaesu(answers=["ID0840", _IF_FRAME], monkeypatch=monkeypatch)
    await radio.connect()
    try:
        assert radio.connected is True
        broadcaster = AudioBroadcaster(radio)
        ws = _make_ws()
        queue = await broadcaster.subscribe(ws=ws)
        try:
            assert broadcaster.rx_start_status() == {
                "schemaVersion": 1,
                "failure": None,
            }
            assert broadcaster._subscription is not None
            ws.send_text.assert_not_awaited()
        finally:
            await broadcaster.unsubscribe(queue)
            await broadcaster._stop_relay()
    finally:
        await radio.disconnect()
