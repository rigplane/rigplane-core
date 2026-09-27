"""MOR-2792 — digital-silence state is readable and published in audioSession.

The USB capture watchdog (MOR-1436) already detects a sustained run of
bit-exact-zero RX frames (macOS Microphone-permission hole). That detection
now carries a readable ``silent-now`` flag and the runtime audio-session
payload reports it so the page can tell the operator.

FakeAudioBackend is the only audio double. Frame timing uses
``frame_ms=1000`` (one frame == one second) so ``_SILENCE_WARN_SECONDS``
zero-frames model the window without real sleeps — same seam as
``test_usb_audio_rx_silence_watchdog_mor1436.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.audio.session import AudioSession
from rigplane.audio.usb_driver import _SILENCE_WARN_SECONDS, UsbAudioDriver
from rigplane.web.server import WebConfig, WebServer

_SILENT_FRAME = b"\x00" * 1920
_LOUD_FRAME = b"\x11\x22" * 960
# Tiny non-zero samples — a quiet band still has noise, never bit-exact zeros.
_QUIET_FRAME = b"\x01\x00" * 960


def _fake_devices() -> list[AudioDeviceInfo]:
    return [
        AudioDeviceInfo(
            id=AudioDeviceId(1),
            name="USB Audio CODEC",
            input_channels=1,
            output_channels=1,
            default_samplerate=48_000,
            is_default_input=True,
            is_default_output=True,
        ),
    ]


class _SilenceRadio:
    """Minimal radio double exposing the driver's silence flag to the session."""

    def __init__(self, driver: UsbAudioDriver) -> None:
        self._audio_driver = driver
        self._audio_session: AudioSession | None = None

    @property
    def rx_silent(self) -> bool:
        return self._audio_driver.rx_silent

    @property
    def audio_session(self) -> AudioSession:
        if self._audio_session is None:
            self._audio_session = AudioSession(self)
        return self._audio_session


def _start() -> tuple[UsbAudioDriver, FakeAudioBackend, Any, AudioSession]:
    backend = FakeAudioBackend(_fake_devices())
    driver = UsbAudioDriver(backend=backend)
    radio = _SilenceRadio(driver)
    session = radio.audio_session
    return driver, backend, radio, session


class _FakeWriter:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:
        return None


def _response_json(writer: _FakeWriter) -> dict[str, Any]:
    import json

    raw = bytes(writer.buffer)
    body = raw.split(b"\r\n\r\n", 1)[-1]
    return json.loads(body)


async def _runtime_payload(radio: Any) -> dict[str, Any]:
    web_radio = SimpleNamespace(
        model="IC-7610",
        backend_id="rigplane",
        connected=True,
        control_connected=True,
        radio_ready=True,
        capabilities=set(),
        _audio_session=radio.audio_session,
    )
    srv = WebServer(web_radio, WebConfig(host="127.0.0.1", port=0))
    writer = _FakeWriter()
    await srv._handle_http(writer, "GET", "/api/v1/runtime")  # noqa: SLF001
    return _response_json(writer)


@pytest.mark.asyncio
async def test_silent_now_false_before_the_window() -> None:
    driver, backend, _radio, _session = _start()
    await driver.start_rx(lambda _frame: None, frame_ms=1000)
    stream = backend.rx_streams[0]
    for _ in range(_SILENCE_WARN_SECONDS - 1):
        stream.inject_frame(_SILENT_FRAME)
    assert driver.rx_silent is False


@pytest.mark.asyncio
async def test_payload_reports_silent_after_the_window_of_zeros() -> None:
    driver, backend, radio, _session = _start()
    await driver.start_rx(lambda _frame: None, frame_ms=1000)
    stream = backend.rx_streams[0]
    for _ in range(_SILENCE_WARN_SECONDS):
        stream.inject_frame(_SILENT_FRAME)

    assert driver.rx_silent is True
    payload = await _runtime_payload(radio)
    assert payload["audioSession"]["enabled"] is True
    assert payload["audioSession"]["rxSilent"] is True


@pytest.mark.asyncio
async def test_non_zero_frame_clears_silent_now() -> None:
    driver, backend, radio, _session = _start()
    await driver.start_rx(lambda _frame: None, frame_ms=1000)
    stream = backend.rx_streams[0]
    for _ in range(_SILENCE_WARN_SECONDS):
        stream.inject_frame(_SILENT_FRAME)
    assert driver.rx_silent is True

    stream.inject_frame(_LOUD_FRAME)
    assert driver.rx_silent is False
    payload = await _runtime_payload(radio)
    assert payload["audioSession"]["rxSilent"] is False


@pytest.mark.asyncio
async def test_quiet_non_zero_stream_never_raises_silent_now() -> None:
    driver, backend, radio, _session = _start()
    await driver.start_rx(lambda _frame: None, frame_ms=1000)
    stream = backend.rx_streams[0]
    for _ in range(_SILENCE_WARN_SECONDS * 3):
        stream.inject_frame(_QUIET_FRAME)
    assert driver.rx_silent is False
    payload = await _runtime_payload(radio)
    assert payload["audioSession"]["rxSilent"] is False
