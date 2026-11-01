"""MOR-4030 case C: another program holds the radio's codec exclusively.

Windows refuses a shared open while an exclusive holder exists, so with
``exclusive-preferred`` the exclusive open is refused as busy AND the shared
fallback fails too: RX never starts. That case alone is raised as
``AudioDeviceBusyError`` (start-failure category ``device_in_use``), which the
RX-start refusal frame and the ``audioReceive`` status already carry to the UI.
Any other open failure keeps its current category and message.

A fake ``sounddevice`` stands in for PortAudio: no audio device, no Windows.
"""

from __future__ import annotations

import logging
import threading

import pytest
from test_audio_backend_capture_mode_mor4030 import (
    EXCLUSIVE,
    MME_DEVICE,
    WASAPI_DEVICE,
    FakeSd,
    _backend,
)

from rigplane.audio import _usb_holder
from rigplane.audio.backend import (
    AudioDeviceBusyError,
    AudioDeviceId,
    AudioDeviceInfo,
    FakeAudioBackend,
)
from rigplane.audio.usb_driver import (
    UsbAudioDriver,
    audio_start_failure_reason,
)

_BACKEND_LOGGER = "rigplane.audio.backend"


class FakePortAudioError(Exception):
    """The shape sounddevice's PortAudioError has: (message, code[, host error])."""


def _busy_errors():
    return {
        # paDeviceUnavailable, which PortAudio's WASAPI layer maps
        # AUDCLNT_E_DEVICE_IN_USE to.
        "code": FakePortAudioError("Error opening InputStream", -9985),
        "text": FakePortAudioError(
            "Error opening InputStream: Device unavailable [PaErrorCode -9985]"
        ),
        # An unmapped host error carrying AUDCLNT_E_DEVICE_IN_USE (0x8889000A).
        "host-error": FakePortAudioError(
            "Error opening InputStream: Unanticipated host error",
            -9999,
            ("Windows WASAPI", -2004287478, "The device is in use"),
        ),
    }


async def _start(backend, direction, device=WASAPI_DEVICE):
    if direction == "rx":
        stream = backend.open_rx(AudioDeviceId(device))
        await stream.start(lambda _pcm: None)
    else:
        stream = backend.open_tx(AudioDeviceId(device))
        await stream.start()
    return stream


@pytest.mark.parametrize("direction", ["rx", "tx"])
@pytest.mark.parametrize("busy", ["code", "text", "host-error"])
async def test_exclusive_busy_and_shared_failed_raises_the_busy_error(busy, direction):
    sd = FakeSd(exclusive_error=_busy_errors()[busy], refuse_shared=True)
    backend = _backend(sd)
    stream = (
        backend.open_rx(AudioDeviceId(WASAPI_DEVICE))
        if direction == "rx"
        else (backend.open_tx(AudioDeviceId(WASAPI_DEVICE)))
    )
    with pytest.raises(AudioDeviceBusyError) as raised:
        if direction == "rx":
            await stream.start(lambda _pcm: None)
        else:
            await stream.start()
    assert not stream.running
    # The shared failure is the cause; the exclusive refusal stays in the log.
    assert isinstance(raised.value.__cause__, OSError)
    assert audio_start_failure_reason(raised.value) == "device_in_use"
    assert [o["extra_settings"].exclusive for o in sd.opened] == [True, False]


@pytest.mark.parametrize("refuse_at", ["open", "start"])
async def test_busy_is_raised_whether_the_refusals_come_at_open_or_start(refuse_at):
    sd = FakeSd(
        exclusive_error=_busy_errors()["code"], refuse_shared=True, refuse_at=refuse_at
    )
    with pytest.raises(AudioDeviceBusyError):
        await _start(_backend(sd), "rx")


@pytest.mark.parametrize("direction", ["rx", "tx"])
async def test_exclusive_busy_but_shared_opens_is_not_the_busy_error(direction):
    sd = FakeSd(exclusive_error=_busy_errors()["code"])
    stream = await _start(_backend(sd), direction)
    assert stream.running and stream.exclusive is False
    await stream.stop()


@pytest.mark.parametrize("direction", ["rx", "tx"])
async def test_a_not_busy_exclusive_refusal_keeps_the_shared_error(direction):
    sd = FakeSd(
        exclusive_error=FakePortAudioError("Invalid number of channels", -9998),
        refuse_shared=True,
    )
    with pytest.raises(OSError, match="shared refused") as raised:
        await _start(_backend(sd), direction)
    assert not isinstance(raised.value, AudioDeviceBusyError)
    assert audio_start_failure_reason(raised.value) == "other"


@pytest.mark.parametrize(
    "capture_mode,device",
    [("shared", WASAPI_DEVICE), ("shared", MME_DEVICE), (EXCLUSIVE, MME_DEVICE)],
)
async def test_plain_open_failures_are_unchanged(capture_mode, device):
    sd = FakeSd(refuse_shared=True)
    # A device-unavailable error on a plain open is not a holder: no exclusive
    # open was refused first.
    sd.exclusive_error = _busy_errors()["code"]
    with pytest.raises(OSError, match="shared refused") as raised:
        await _start(_backend(sd, capture_mode), "rx", device)
    assert not isinstance(raised.value, AudioDeviceBusyError)


async def test_the_busy_refusal_logs_one_fallback_line_per_device(caplog):
    sd = FakeSd(exclusive_error=_busy_errors()["code"], refuse_shared=True)
    backend = _backend(sd)
    with caplog.at_level(logging.WARNING, logger=_BACKEND_LOGGER):
        for _ in range(3):
            with pytest.raises(AudioDeviceBusyError):
                await _start(backend, "rx")
    fallbacks = [
        r.getMessage()
        for r in caplog.records
        if r.name == _BACKEND_LOGGER and "exclusive open" in r.getMessage()
    ]
    assert len(fallbacks) == 1


# --- through the driver ------------------------------------------------------


def _backend_whose_rx_open_is_busy():
    backend = FakeAudioBackend(
        [
            AudioDeviceInfo(
                id=AudioDeviceId(1),
                name="USB Audio CODEC",
                input_channels=1,
                output_channels=1,
                default_samplerate=48_000,
                is_default_input=True,
                is_default_output=True,
            )
        ]
    )

    def busy():
        raise AudioDeviceBusyError("busy", device_index=1)

    backend.block_rx_open = busy
    return backend


async def test_driver_start_failure_carries_the_device_in_use_category():
    driver = UsbAudioDriver(backend=_backend_whose_rx_open_is_busy())
    with pytest.raises(AudioDeviceBusyError) as raised:
        await driver.start_rx(lambda _frame: None)
    assert audio_start_failure_reason(raised.value) == "device_in_use"
    assert driver.rx_running is False


async def test_a_retry_after_the_holder_left_opens_the_device():
    backend = _backend_whose_rx_open_is_busy()
    driver = UsbAudioDriver(backend=backend)
    with pytest.raises(AudioDeviceBusyError):
        await driver.start_rx(lambda _frame: None)
    backend.block_rx_open = None
    await driver.start_rx(lambda _frame: None)
    assert driver.rx_running is True
    await driver.stop_rx()


def test_the_category_is_one_of_the_publishable_reasons():
    from typing import get_args

    from rigplane.audio.usb_driver import AudioStartFailureReason

    assert "device_in_use" in get_args(AudioStartFailureReason)


# --- the holder-name seam (production lookup returns None for now) ----------


def _lookup_returning(value):
    calls: list[str] = []

    def lookup(device_name: str):
        calls.append(device_name)
        return value

    lookup.calls = calls  # type: ignore[attr-defined]
    return lookup


async def _busy_with_lookup(lookup, *, timeout=None):
    sd = FakeSd(exclusive_error=_busy_errors()["code"], refuse_shared=True)
    backend = _backend(sd)
    policy = backend._capture_policy
    policy.holder_lookup = lookup
    if timeout is not None:
        policy.holder_timeout_s = timeout
    with pytest.raises(AudioDeviceBusyError) as raised:
        await _start(backend, "rx")
    return raised.value


async def test_holder_name_found_is_exposed_and_logged_as_the_exe_name_only(caplog):
    lookup = _lookup_returning(r"C:\Users\someone\AppData\Local\WSJT-X\wsjtx.exe")
    with caplog.at_level(logging.WARNING, logger=_BACKEND_LOGGER):
        error = await _busy_with_lookup(lookup)
    assert error.holder == "wsjtx.exe"
    assert lookup.calls == ["Microphone (USB Audio CODEC)"]
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "wsjtx.exe" in text
    assert "someone" not in text and "AppData" not in text


@pytest.mark.parametrize("value", [None, "", "   ", "\\", "C:\\"])
async def test_holder_name_not_found_gives_no_name(value):
    error = await _busy_with_lookup(_lookup_returning(value))
    assert error.holder is None


async def test_a_raising_holder_lookup_never_breaks_the_failure():
    def boom(_device_name):
        raise OSError("registry unavailable at C:\\Users\\someone")

    error = await _busy_with_lookup(boom)
    assert isinstance(error, AudioDeviceBusyError)
    assert error.holder is None


async def test_a_slow_holder_lookup_is_abandoned_after_its_bound():
    release = threading.Event()

    def slow(_device_name):
        release.wait(5)
        return "late.exe"

    try:
        error = await _busy_with_lookup(slow, timeout=0.05)
    finally:
        release.set()
    assert error.holder is None


async def test_the_lookup_is_not_asked_when_the_open_is_not_a_busy_refusal():
    lookup = _lookup_returning("wsjtx.exe")
    sd = FakeSd(
        exclusive_error=FakePortAudioError("Invalid number of channels", -9998),
        refuse_shared=True,
    )
    backend = _backend(sd)
    backend._capture_policy.holder_lookup = lookup
    with pytest.raises(OSError):
        await _start(backend, "rx")
    assert lookup.calls == []


def test_the_production_lookup_has_no_mechanism_yet():
    assert _usb_holder.find_holder_name("Microphone (USB Audio CODEC)") is None
