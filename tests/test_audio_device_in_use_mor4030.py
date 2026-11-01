"""MOR-4030 case C: another program holds the radio's codec exclusively.

Windows refuses a shared open while an exclusive holder exists, so with
``exclusive-preferred`` the exclusive open is refused AND the shared fallback
fails too, both with the real run's ``Invalid device [PaErrorCode -9996]``: RX
never starts. That case alone (the shared failure is "device unavailable" or
"invalid device" or carries AUDCLNT_E_DEVICE_IN_USE, and the device still
enumerates with channels) is raised as ``AudioDeviceBusyError`` (start-failure
category ``device_in_use``), which the RX-start refusal frame and the
``audioReceive`` status already carry to the UI. Any other open failure keeps
its current category and message.

A fake ``sounddevice`` stands in for PortAudio: no audio device, no Windows.
"""

from __future__ import annotations

import logging

import pytest
from test_audio_backend_capture_mode_mor4030 import (
    EXCLUSIVE,
    MME_DEVICE,
    WASAPI_DEVICE,
    FakeSd,
    _backend,
)

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


# The real strings from the real-Windows run (IC-7300 codec held exclusively by
# another process): BOTH the exclusive open and the shared fallback raised this.
REAL_INVALID_DEVICE = "Error opening InputStream: Invalid device [PaErrorCode -9996]"


def _real_refusal():
    return FakePortAudioError(REAL_INVALID_DEVICE, -9996)


def _in_use_shared_errors():
    return {
        "real-invalid-device": _real_refusal(),
        "device-unavailable-code": FakePortAudioError(
            "Error opening InputStream", -9985
        ),
        "device-unavailable-text": FakePortAudioError(
            "Error opening InputStream: Device unavailable [PaErrorCode -9985]"
        ),
        "host-error-in-use": FakePortAudioError(
            "Error opening InputStream: Unanticipated host error",
            -9999,
            ("Windows WASAPI", -2004287478, "AUDCLNT_E_DEVICE_IN_USE (0x8889000A)"),
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


def _real_sd(**kw):
    """Both attempts fail as in the real run: the same Invalid device error."""
    return FakeSd(
        exclusive_error=_real_refusal(),
        shared_error=_real_refusal(),
        refuse_shared=True,
        **kw,
    )


@pytest.mark.parametrize("direction", ["rx", "tx"])
async def test_the_real_run_refusal_raises_the_in_use_error(direction):
    sd = _real_sd()
    backend = _backend(sd)
    stream = (
        backend.open_rx(AudioDeviceId(WASAPI_DEVICE))
        if direction == "rx"
        else backend.open_tx(AudioDeviceId(WASAPI_DEVICE))
    )
    with pytest.raises(AudioDeviceBusyError) as raised:
        if direction == "rx":
            await stream.start(lambda _pcm: None)
        else:
            await stream.start()
    assert not stream.running
    assert raised.value.device_index == WASAPI_DEVICE
    # The shared failure is the cause; it is the real PortAudio error verbatim.
    assert raised.value.__cause__.args == (REAL_INVALID_DEVICE, -9996)
    assert audio_start_failure_reason(raised.value) == "device_in_use"
    assert [o["extra_settings"].exclusive for o in sd.opened] == [True, False]


@pytest.mark.parametrize("shared", list(_in_use_shared_errors()))
async def test_every_in_use_shape_of_the_shared_failure_raises_the_in_use_error(shared):
    sd = FakeSd(
        exclusive_error=_real_refusal(),
        shared_error=_in_use_shared_errors()[shared],
        refuse_shared=True,
    )
    with pytest.raises(AudioDeviceBusyError):
        await _start(_backend(sd), "rx")


async def test_any_exclusive_open_error_counts_when_the_shared_one_says_in_use():
    sd = FakeSd(
        exclusive_error=OSError("Error opening InputStream: something else"),
        shared_error=_real_refusal(),
        refuse_shared=True,
    )
    with pytest.raises(AudioDeviceBusyError):
        await _start(_backend(sd), "rx")


@pytest.mark.parametrize("refuse_at", ["open", "start"])
async def test_the_refusals_may_come_at_open_or_start(refuse_at):
    with pytest.raises(AudioDeviceBusyError):
        await _start(_backend(_real_sd(refuse_at=refuse_at)), "rx")


@pytest.mark.parametrize("direction", ["rx", "tx"])
async def test_exclusive_refused_but_shared_opens_is_not_the_in_use_error(direction):
    sd = FakeSd(exclusive_error=_real_refusal())
    stream = await _start(_backend(sd), direction)
    assert stream.running and stream.exclusive is False
    await stream.stop()


ACCESS_DENIED = FakePortAudioError(
    "Error opening InputStream: Unanticipated host error [PaErrorCode -9999]",
    -9999,
    ("Windows WASAPI", -2147024891, "Access is denied. (0x80070005)"),
)


@pytest.mark.parametrize("direction", ["rx", "tx"])
@pytest.mark.parametrize(
    "shared",
    [
        FakePortAudioError(
            "Error opening InputStream: Invalid number of channels", -9998
        ),
        FakePortAudioError("Error opening InputStream: Invalid sample rate", -9997),
        ACCESS_DENIED,
        OSError("Error opening stream: shared refused"),
    ],
    ids=["channels", "sample-rate", "access-denied", "plain-oserror"],
)
async def test_other_shared_failures_keep_their_own_error(shared, direction):
    sd = FakeSd(
        exclusive_error=_real_refusal(), shared_error=shared, refuse_shared=True
    )
    with pytest.raises(type(shared)) as raised:
        await _start(_backend(sd), direction)
    assert raised.value is shared
    assert audio_start_failure_reason(raised.value) == "other"


async def test_access_denied_on_both_attempts_stays_the_old_error():
    sd = FakeSd(
        exclusive_error=ACCESS_DENIED, shared_error=ACCESS_DENIED, refuse_shared=True
    )
    with pytest.raises(FakePortAudioError) as raised:
        await _start(_backend(sd), "rx")
    assert raised.value is ACCESS_DENIED


@pytest.mark.parametrize("direction", ["rx", "tx"])
async def test_invalid_device_for_a_device_that_no_longer_enumerates_stays_old(
    direction,
):
    sd = _real_sd(unplugged=(WASAPI_DEVICE,))
    with pytest.raises(FakePortAudioError) as raised:
        await _start(_backend(sd), direction)
    assert not isinstance(raised.value, AudioDeviceBusyError)
    assert raised.value.args == (REAL_INVALID_DEVICE, -9996)
    assert audio_start_failure_reason(raised.value) == "other"


async def test_invalid_device_for_a_device_without_channels_stays_old():
    sd = _real_sd()
    original = sd.query_devices

    def no_inputs(index):
        return {**original(index), "max_input_channels": 0}

    sd.query_devices = no_inputs  # type: ignore[method-assign]
    with pytest.raises(FakePortAudioError) as raised:
        await _start(_backend(sd), "rx")
    assert not isinstance(raised.value, AudioDeviceBusyError)


@pytest.mark.parametrize(
    "capture_mode,device",
    [("shared", WASAPI_DEVICE), ("shared", MME_DEVICE), (EXCLUSIVE, MME_DEVICE)],
)
async def test_plain_open_failures_are_unchanged(capture_mode, device):
    # The same real error on a plain open is not a holder: no exclusive open
    # was refused first.
    sd = FakeSd(shared_error=_real_refusal(), refuse_shared=True)
    with pytest.raises(FakePortAudioError) as raised:
        await _start(_backend(sd, capture_mode), "rx", device)
    assert not isinstance(raised.value, AudioDeviceBusyError)


async def test_the_in_use_refusal_logs_one_fallback_line_per_device(caplog):
    backend = _backend(_real_sd())
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
    assert REAL_INVALID_DEVICE in fallbacks[0]


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
