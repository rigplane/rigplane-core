"""MOR-4338: a USB radio powered off and on again gets its audio back.

PortAudio lists the OS audio devices once, when it starts. A radio that was
powered off overnight while the station kept running is missing from that
list (or sits at a stale index) when it returns, so the audio start that
follows its return failed until the server was restarted. The driver now
re-scans the devices and retries the start once, and the backend only
restarts PortAudio while no stream is open anywhere in the process (a restart
closes every open stream).

The driver tests use the radios' real USB codec name shapes; the backend
tests use the real ``PortAudioBackend`` over a fake ``sounddevice``.
"""

from __future__ import annotations

import pytest
from test_audio_backend_capture_mode_mor4030 import (
    MME_DEVICE,
    FakeSd,
    _backend,
)

from rigplane.audio.backend import (
    AudioDeviceId,
    AudioDeviceInfo,
    DeviceRefreshingBackend,
    FakeAudioBackend,
)
from rigplane.audio.usb_driver import (
    AudioAlreadyStartedError,
    AudioDeviceSelectionError,
    UsbAudioDriver,
)

# Name shapes the codecs of different radio families present.
_CODEC_NAMES = [
    pytest.param("USB Audio CODEC", id="icom-ic7300-ic705"),
    pytest.param("ICOM USB Audio", id="icom-named"),
    pytest.param("Yaesu USB Audio", id="yaesu-ftx1"),
]


def _codec(name: str) -> AudioDeviceInfo:
    return AudioDeviceInfo(
        id=AudioDeviceId(3), name=name, input_channels=1, output_channels=1
    )


class _RescanSd(FakeSd):
    """``sounddevice`` with the restart pair PortAudio re-scans devices with."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[str] = []

    def _terminate(self) -> None:
        self.calls.append("terminate")

    def _initialize(self) -> None:
        self.calls.append("initialize")


class _ReturnedRadioBackend(FakeAudioBackend):
    """A backend whose device list is the stale scan until it is refreshed."""

    def __init__(
        self,
        stale: list[AudioDeviceInfo],
        fresh: list[AudioDeviceInfo],
        *,
        can_refresh: bool = True,
    ) -> None:
        super().__init__(stale)
        self._fresh = fresh
        self._can_refresh = can_refresh
        self.refreshes = 0

    def refresh_devices(self) -> bool:
        self.refreshes += 1
        if not self._can_refresh:
            return False
        self._devices = self._fresh
        return True


def test_refresh_restarts_portaudio_when_no_stream_is_open():
    sd = _RescanSd()
    backend = _backend(sd, "shared")

    assert backend.refresh_devices() is True
    assert sd.calls == ["terminate", "initialize"]


async def test_refresh_never_restarts_portaudio_under_an_open_stream():
    sd = _RescanSd()
    backend = _backend(sd, "shared")
    stream = backend.open_rx(AudioDeviceId(MME_DEVICE))
    await stream.start(lambda _pcm: None)

    assert backend.refresh_devices() is False
    assert sd.calls == []

    await stream.stop()
    assert backend.refresh_devices() is True


async def test_an_open_stream_of_another_backend_also_blocks_the_restart():
    sd = _RescanSd()
    first, second = _backend(sd, "shared"), _backend(sd, "shared")
    stream = first.open_tx(AudioDeviceId(MME_DEVICE))
    await stream.start()

    assert second.refresh_devices() is False
    assert sd.calls == []
    await stream.stop()


def test_refresh_is_declined_without_the_restart_pair():
    class _PlainSd(FakeSd):
        pass

    assert _backend(_PlainSd(), "shared").refresh_devices() is False


@pytest.mark.parametrize("name", _CODEC_NAMES)
async def test_rx_start_after_the_radio_returns_rescans_and_succeeds(name):
    backend = _ReturnedRadioBackend(stale=[], fresh=[_codec(name)])
    driver = UsbAudioDriver(backend=backend)

    await driver.start_rx(lambda _pcm: None)

    assert driver.rx_running
    assert backend.refreshes == 1
    await driver.stop_rx()


@pytest.mark.parametrize("name", _CODEC_NAMES)
async def test_tx_start_after_the_radio_returns_rescans_and_succeeds(name):
    backend = _ReturnedRadioBackend(stale=[], fresh=[_codec(name)])
    driver = UsbAudioDriver(backend=backend)

    await driver.start_tx()

    assert driver.tx_running
    assert backend.refreshes == 1
    await driver.stop_tx()


async def test_a_healthy_start_never_rescans():
    backend = _ReturnedRadioBackend(
        stale=[_codec("USB Audio CODEC")], fresh=[_codec("USB Audio CODEC")]
    )
    driver = UsbAudioDriver(backend=backend)

    await driver.start_rx(lambda _pcm: None)

    assert backend.refreshes == 0
    await driver.stop_rx()


async def test_a_declined_rescan_raises_the_original_failure_once():
    backend = _ReturnedRadioBackend(
        stale=[], fresh=[_codec("USB Audio CODEC")], can_refresh=False
    )
    driver = UsbAudioDriver(backend=backend)

    with pytest.raises(AudioDeviceSelectionError):
        await driver.start_rx(lambda _pcm: None)

    assert backend.refreshes == 1


async def test_a_start_that_still_fails_after_the_rescan_is_retried_only_once():
    backend = _ReturnedRadioBackend(stale=[], fresh=[])
    driver = UsbAudioDriver(backend=backend)

    with pytest.raises(AudioDeviceSelectionError):
        await driver.start_rx(lambda _pcm: None)

    assert backend.refreshes == 1


async def test_a_lifecycle_error_is_not_a_device_failure():
    backend = _ReturnedRadioBackend(
        stale=[_codec("USB Audio CODEC")], fresh=[_codec("USB Audio CODEC")]
    )
    driver = UsbAudioDriver(backend=backend)
    await driver.start_rx(lambda _pcm: None)

    with pytest.raises(AudioAlreadyStartedError):
        await driver.start_rx(lambda _pcm: None)

    assert backend.refreshes == 0
    await driver.stop_rx()


async def test_a_backend_without_a_rescan_keeps_the_original_failure():
    backend = FakeAudioBackend([])
    assert not isinstance(backend, DeviceRefreshingBackend)
    driver = UsbAudioDriver(backend=backend)

    with pytest.raises(AudioDeviceSelectionError):
        await driver.start_rx(lambda _pcm: None)
