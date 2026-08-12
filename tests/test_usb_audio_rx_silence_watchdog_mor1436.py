"""MOR-1436 — RX-capture silence watchdog.

Bench incident: macOS hands microphone-unpermissioned capture contexts (e.g.
ssh-spawned processes) all-zero CoreAudio frames — the stream opens fine and
frames keep flowing, but every sample is exactly 0. The server streamed
digital silence for hours with zero indication. ``UsbAudioDriver.start_rx``
now wraps the delivered callback with a cheap all-zero-frame counter that
warns once after ~10s of unbroken bit-exact silence and logs one INFO on
recovery, re-arming for the next silent stretch — no periodic spam, no new
timers, no change to frame delivery itself.

Frames are ``frame_ms=1000`` (one frame == one second) so ten injected
zero-frames model the ~10s threshold without a slow test.
"""

from __future__ import annotations

import logging

import pytest

from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.audio.usb_driver import UsbAudioDriver

_LOGGER_NAME = "rigplane.audio.usb_driver"
_SILENT_FRAME = b"\x00" * 1920
_LOUD_FRAME = b"\x11\x22" * 960


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


def _make_driver() -> tuple[UsbAudioDriver, FakeAudioBackend]:
    backend = FakeAudioBackend(_fake_devices())
    driver = UsbAudioDriver(backend=backend)
    return driver, backend


def _warning_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == _LOGGER_NAME and r.levelno == logging.WARNING
    ]


def _info_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == _LOGGER_NAME
        and r.levelno == logging.INFO
        and "RX audio signal detected" in r.getMessage()
    ]


@pytest.mark.asyncio
async def test_silence_watchdog_warns_once_after_threshold(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """(a) N seconds of zero frames -> exactly one warning."""
    driver, backend = _make_driver()
    await driver.start_rx(lambda _frame: None, frame_ms=1000)
    stream = backend.rx_streams[0]

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        for _ in range(10):
            stream.inject_frame(_SILENT_FRAME)

    warnings = _warning_records(caplog)
    assert len(warnings) == 1
    assert "digital silence" in warnings[0].getMessage()
    assert "Microphone" in warnings[0].getMessage()


@pytest.mark.asyncio
async def test_silence_watchdog_does_not_repeat_while_still_silent(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """(b) continued zeros after the threshold -> no repeat warning."""
    driver, backend = _make_driver()
    await driver.start_rx(lambda _frame: None, frame_ms=1000)
    stream = backend.rx_streams[0]

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        for _ in range(30):
            stream.inject_frame(_SILENT_FRAME)

    assert len(_warning_records(caplog)) == 1


@pytest.mark.asyncio
async def test_silence_watchdog_recovers_and_rearms(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """(c) a non-zero frame after warning -> recovery INFO + re-arm."""
    driver, backend = _make_driver()
    await driver.start_rx(lambda _frame: None, frame_ms=1000)
    stream = backend.rx_streams[0]

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        for _ in range(10):
            stream.inject_frame(_SILENT_FRAME)
        assert len(_warning_records(caplog)) == 1

        stream.inject_frame(_LOUD_FRAME)
        assert len(_info_records(caplog)) == 1

        # Re-armed: another full silent stretch warns again.
        for _ in range(10):
            stream.inject_frame(_SILENT_FRAME)

    assert len(_warning_records(caplog)) == 2
    assert len(_info_records(caplog)) == 1


@pytest.mark.asyncio
async def test_silence_watchdog_silent_on_normal_audio(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """(d) normal (non-zero) audio from the start -> no warning."""
    driver, backend = _make_driver()
    await driver.start_rx(lambda _frame: None, frame_ms=1000)
    stream = backend.rx_streams[0]

    with caplog.at_level(logging.WARNING, logger=_LOGGER_NAME):
        for _ in range(30):
            stream.inject_frame(_LOUD_FRAME)

    assert len(_warning_records(caplog)) == 0


@pytest.mark.asyncio
async def test_silence_watchdog_delivers_every_frame_unchanged() -> None:
    """No behavior change to frame delivery itself: callback sees each frame."""
    driver, backend = _make_driver()
    received: list[bytes] = []
    await driver.start_rx(received.append, frame_ms=1000)
    stream = backend.rx_streams[0]

    stream.inject_frame(_SILENT_FRAME)
    stream.inject_frame(_LOUD_FRAME)

    assert received == [_SILENT_FRAME, _LOUD_FRAME]
