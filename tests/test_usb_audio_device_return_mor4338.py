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

import asyncio
import threading
from collections.abc import Callable

import pytest
from test_audio_backend_capture_mode_mor4030 import (
    MME_DEVICE,
    FakeSd,
    _backend,
)

from test_icom7610_serial_radio import _FakeSerialCivLink

from rigplane.audio.backend import (
    AudioDeviceId,
    AudioDeviceInfo,
    DeviceRefreshingBackend,
    FakeAudioBackend,
    _OpenStreamCount,
    _open_streams,
    device_scan_generation,
)
from rigplane.audio._portaudio_enum import read_portaudio_enumeration
from rigplane.audio.bridge import AudioBridge
from rigplane.backends.ic705 import Ic705SerialRadio
from rigplane.backends.ic7300 import Ic7300SerialRadio
from rigplane.types import AudioCodec
from rigplane.audio.usb_driver import (
    AudioAlreadyStartedError,
    AudioDeviceSelectionError,
    AudioDriverLifecycleError,
    AudioFormatUnsupportedError,
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
        stale_probes_fail: bool = False,
    ) -> None:
        super().__init__(stale)
        self._fresh = fresh
        self._can_refresh = can_refresh
        self._stale_probes_fail = stale_probes_fail
        self.refreshes = 0
        # Set to hold ``refresh_devices`` until the test releases it.
        self.on_refresh: Callable[[], None] | None = None
        # Hold ``list_devices`` after a refresh until released (pauses the
        # retry's device selection).
        self.on_list_after_refresh: Callable[[], None] | None = None
        self.list_gate: threading.Event | None = None
        self.refresh_gate: threading.Event | None = None

    def list_devices(self):
        if self.refreshes and self.list_gate is not None:
            if self.on_list_after_refresh is not None:
                self.on_list_after_refresh()
            self.list_gate.wait()
        return super().list_devices()

    def check_sample_rate(self, device, sample_rate, *, direction="rx") -> bool:
        if self._stale_probes_fail and self.refreshes == 0:
            return False  # the stale index of a returned device fails every probe
        return super().check_sample_rate(device, sample_rate, direction=direction)

    def refresh_devices(self) -> bool:
        if self.on_refresh is not None:
            self.on_refresh()
        if self.refresh_gate is not None:
            self.refresh_gate.wait()
        self.refreshes += 1
        if not self._can_refresh:
            return False
        self._devices = self._fresh
        _open_streams.restart_if_idle(lambda: None)  # the real scan counter
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


def test_a_stream_cannot_open_while_the_restart_runs():
    count = _OpenStreamCount()
    taken_by_other: list[bool] = []

    def restart() -> None:
        # ``retain`` takes this lock, so no stream can take its lease now.
        other = threading.Thread(
            target=lambda: taken_by_other.append(count._lock.acquire(blocking=False))
        )
        other.start()
        other.join()

    assert count.restart_if_idle(restart) is True
    assert taken_by_other == [False]
    count.retain()
    assert count.restart_if_idle(lambda: None) is False


def test_a_failed_restart_is_retried_by_the_next_refresh():
    class _FailsOnce(_RescanSd):
        broken = True

        def _terminate(self) -> None:
            if self.broken:
                raise RuntimeError("PortAudio not initialized")  # nothing to stop
            super()._terminate()

        def _initialize(self) -> None:
            if self.broken:
                self.broken = False
                raise RuntimeError("initialize failed")
            super()._initialize()

    sd = _FailsOnce()
    backend = _backend(sd, "shared")

    with pytest.raises(RuntimeError, match="initialize failed"):
        backend.refresh_devices()
    sd.broken = True  # uninitialized again: terminate raises, initialize works
    sd._initialize = lambda: sd.calls.append("initialize")  # type: ignore[method-assign]
    assert backend.refresh_devices() is True
    assert sd.calls == ["initialize"]


@pytest.mark.parametrize("name", _CODEC_NAMES)
async def test_a_stale_index_that_fails_every_probe_is_rescanned(name):
    backend = _ReturnedRadioBackend(
        stale=[_codec(name)], fresh=[_codec(name)], stale_probes_fail=True
    )
    rx = UsbAudioDriver(backend=backend)
    await rx.start_rx(lambda _pcm: None)
    assert rx.rx_running and backend.refreshes == 1
    await rx.stop_rx()

    backend = _ReturnedRadioBackend(
        stale=[_codec(name)], fresh=[_codec(name)], stale_probes_fail=True
    )
    tx = UsbAudioDriver(backend=backend)
    await tx.start_tx()
    assert tx.tx_running and backend.refreshes == 1
    await tx.stop_tx()


async def test_a_format_failure_that_survives_the_rescan_is_raised():
    backend = _ReturnedRadioBackend(
        stale=[_codec("USB Audio CODEC")],
        fresh=[_codec("USB Audio CODEC")],
        can_refresh=False,
        stale_probes_fail=True,
    )
    driver = UsbAudioDriver(backend=backend)

    with pytest.raises(AudioFormatUnsupportedError):
        await driver.start_rx(lambda _pcm: None)

    assert backend.refreshes == 1


@pytest.mark.parametrize("stop", ["stop_rx", "stop_tx"])
async def test_a_stop_during_the_rescan_is_not_followed_by_a_reopen(stop):
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    backend = _ReturnedRadioBackend(stale=[], fresh=[_codec("USB Audio CODEC")])
    backend.refresh_gate = threading.Event()
    backend.on_refresh = lambda: loop.call_soon_threadsafe(entered.set)
    driver = UsbAudioDriver(backend=backend)
    starting = asyncio.ensure_future(
        driver.start_rx(lambda _pcm: None) if stop == "stop_rx" else driver.start_tx()
    )
    await entered.wait()

    await getattr(driver, stop)()
    backend.refresh_gate.set()

    with pytest.raises(AudioDeviceSelectionError):
        await starting
    assert not driver.rx_running
    assert not driver.tx_running


@pytest.mark.parametrize(
    ("radio_cls", "model_id"),
    [
        pytest.param(Ic7300SerialRadio, 0x94, id="ic7300"),
        pytest.param(Ic705SerialRadio, 0xA4, id="ic705"),
    ],
)
async def test_radio_rx_subscription_after_the_codec_returns_delivers_audio(
    radio_cls, model_id
):
    """The web handler's RX route (``audio_session.subscribe_rx``) over the real
    serial radio, the real driver and a backend whose scan predates the return."""
    backend = _ReturnedRadioBackend(stale=[], fresh=[_codec("USB Audio CODEC")])
    radio = radio_cls(
        device="/dev/ttyUSB-fake",
        civ_link=_FakeSerialCivLink(model_id=model_id),
        audio_driver=UsbAudioDriver(backend=backend),
        audio_codec=AudioCodec.PCM_1CH_16BIT,
        _enumerate_serial_ports_fn=lambda: [],
    )
    radio._SERIAL_WATCHDOG_INTERVAL_S = 3600.0
    await radio.connect()
    try:
        sub = await radio.audio_session.subscribe_rx("web")
        frame = b"\x01\x02" * 960
        backend.rx_streams[0].inject_frame(frame)
        packet = await sub.get()
        assert packet is not None and packet.data == frame
        assert backend.refreshes == 1
    finally:
        await radio.disconnect()


class _QueryRecordingSd(_RescanSd):
    """Records whether the native-access guard is held during each query."""

    def __init__(self) -> None:
        super().__init__()
        self.held: list[tuple[str, bool]] = []

    def query_devices(self, index=None):
        self.held.append(("query", _open_streams._lock._is_owned()))
        return [] if index is None else super().query_devices(index)

    def check_input_settings(self, **kw):
        self.held.append(("probe", _open_streams._lock._is_owned()))
        super().check_input_settings(**kw)


def test_enumeration_and_format_probes_run_under_the_native_guard():
    sd = _QueryRecordingSd()
    backend = _backend(sd, "shared")

    backend.list_devices()
    backend.check_sample_rate(AudioDeviceId(MME_DEVICE), 48_000)

    assert sd.held == [("query", True), ("probe", True)]


def test_the_restart_holds_the_native_guard_against_other_threads():
    seen: list[bool] = []

    class _Sd(_RescanSd):
        def _terminate(self) -> None:
            other = threading.Thread(
                target=lambda: seen.append(_open_streams._lock.acquire(blocking=False))
            )
            other.start()
            other.join()

    assert _backend(_Sd(), "shared").refresh_devices() is True
    assert seen == [False]


@pytest.mark.parametrize("stop", ["stop_tx"])  # RX selects under its own lock
async def test_a_stop_during_the_retry_device_selection_opens_nothing(stop):
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    backend = _ReturnedRadioBackend(stale=[], fresh=[_codec("USB Audio CODEC")])
    backend.list_gate = threading.Event()
    backend.on_list_after_refresh = lambda: loop.call_soon_threadsafe(entered.set)
    driver = UsbAudioDriver(backend=backend)
    starting = asyncio.ensure_future(
        driver.start_rx(lambda _pcm: None) if stop == "stop_rx" else driver.start_tx()
    )
    await entered.wait()

    await getattr(driver, stop)()
    backend.list_gate.set()

    with pytest.raises(AudioDriverLifecycleError):
        await starting
    assert not driver.rx_running
    assert not driver.tx_running


@pytest.mark.parametrize("name", _CODEC_NAMES)
async def test_duplex_start_after_the_radio_returns_rescans_and_succeeds(name):
    backend = _ReturnedRadioBackend(stale=[], fresh=[_codec(name)])
    driver = UsbAudioDriver(backend=backend)

    await driver.start_duplex(lambda _pcm: None)

    assert driver.rx_running and driver.tx_running
    assert backend.refreshes == 1
    assert [s.running for s in backend.duplex_streams] == [True]
    await driver.stop_tx()


class _CableBackend(FakeAudioBackend):
    """The bridge's backend: the OS renumbers the cable after a device re-scan."""

    def __init__(self) -> None:
        super().__init__()
        self.opened: list[tuple[str, int]] = []
        self._scan = device_scan_generation()

    def list_devices(self):
        cable = dict(name="RigPlane Virtual Cable", input_channels=2, output_channels=2)
        if device_scan_generation() == self._scan:
            devices = [AudioDeviceInfo(id=AudioDeviceId(1), **cable)]
        else:
            # The returned radio's codec now sorts in front of the cable.
            devices = [
                AudioDeviceInfo(
                    id=AudioDeviceId(1),
                    name="USB Audio CODEC",
                    input_channels=1,
                    output_channels=1,
                ),
                AudioDeviceInfo(id=AudioDeviceId(2), **cable),
            ]
        self._devices = devices  # the fake validates opens against this list
        return devices

    def open_tx(self, device, **kwargs):
        self.opened.append(("playback", int(device)))
        return super().open_tx(device, **kwargs)

    def open_rx(self, device, **kwargs):
        self.opened.append(("capture", int(device)))
        return super().open_rx(device, **kwargs)


async def test_bridge_uses_the_cable_ids_of_the_scan_the_radio_recovery_made():
    radio_backend = _ReturnedRadioBackend(stale=[], fresh=[_codec("USB Audio CODEC")])
    radio = Ic7300SerialRadio(
        device="/dev/ttyUSB-fake",
        civ_link=_FakeSerialCivLink(model_id=0x94),
        audio_driver=UsbAudioDriver(backend=radio_backend),
        audio_codec=AudioCodec.PCM_1CH_16BIT,
        _enumerate_serial_ports_fn=lambda: [],
    )
    radio._SERIAL_WATCHDOG_INTERVAL_S = 3600.0
    await radio.connect()
    cable_backend = _CableBackend()
    bridge = AudioBridge(
        radio,
        device_name="RigPlane Virtual Cable",
        tx_enabled=False,
        backend=cable_backend,
    )
    try:
        await bridge.start()
        assert radio_backend.refreshes == 1
        assert cable_backend.opened == [("playback", 2)]  # the renumbered cable
    finally:
        await bridge.stop()
        await radio.disconnect()


async def test_overlapping_retries_keep_each_starts_stop():
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    listed_after_refresh = 0
    refresh_calls = 0
    refresh_both = asyncio.Event()
    backend = _ReturnedRadioBackend(stale=[], fresh=[_codec("USB Audio CODEC")])
    backend.refresh_gate = threading.Event()
    backend.list_gate = threading.Event()

    def on_refresh() -> None:
        nonlocal refresh_calls
        refresh_calls += 1
        if refresh_calls == 2:
            loop.call_soon_threadsafe(refresh_both.set)

    def list_devices():
        nonlocal listed_after_refresh
        if backend.refreshes >= 2:
            listed_after_refresh += 1
            if listed_after_refresh == 2:  # the second start's retry selection
                loop.call_soon_threadsafe(entered.set)
                backend.list_gate.wait()
        return FakeAudioBackend.list_devices(backend)

    backend.on_refresh = on_refresh
    backend.list_devices = list_devices  # type: ignore[method-assign]
    driver = UsbAudioDriver(backend=backend)
    rx = asyncio.ensure_future(driver.start_rx(lambda _pcm: None))
    tx = asyncio.ensure_future(driver.start_tx())
    await refresh_both.wait()
    backend.refresh_gate.set()
    await entered.wait()
    done, pending = await asyncio.wait({rx, tx}, return_when=asyncio.FIRST_COMPLETED)
    (paused,) = pending
    # One retry finished; the other is still choosing its devices.
    await (driver.stop_rx() if paused is rx else driver.stop_tx())
    backend.list_gate.set()

    with pytest.raises(AudioDriverLifecycleError):
        await paused
    assert not (driver.rx_running if paused is rx else driver.tx_running)


class _GuardSd(_RescanSd):
    def __init__(self) -> None:
        super().__init__()
        self.held: list[bool] = []

    def query_devices(self, index=None):
        self.held.append(_open_streams._lock._is_owned())
        return []

    def query_hostapis(self, index=None):
        self.held.append(_open_streams._lock._is_owned())
        return []


def test_the_shared_enumeration_helper_holds_the_native_guard():
    sd = _GuardSd()

    read_portaudio_enumeration(sd)

    assert sd.held and all(sd.held)
