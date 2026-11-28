"""MOR-4521: RX audio comes back after the radio is powered off and on.

Scenario MOR-4338 with the audio bridge up: ``PortAudioBackend`` only
re-scans the OS devices while no stream is open anywhere in the process,
but the bridge keeps its playback stream open through a radio outage, so
a returned radio stayed silent while the bridge was up.

Item 1: the bridge recovery path (teardown → restore) opens an idle
window for the re-scan before reopening its streams: the reconnect tears
the playback down, re-scans, then restores it at the renumbered device
id and RX resumes.

Item 2: a device vanishing between the format probe and the open makes
both opens fail with invalid-device/unavailable, which
``_CapturePolicy.busy_error()`` reports as ``AudioDeviceBusyError`` —
and the driver used to re-raise that without the single re-scan. The
driver now allows the one re-scan for these failures; a genuinely held
device still raises the busy error when the retry fails too. Pinned for
RX and TX with the real backend over a fake ``sounddevice`` whose opens
recover after the refresh.
"""

from __future__ import annotations

import asyncio
import types
from unittest.mock import AsyncMock

import pytest
from test_audio_backend_capture_mode_mor4030 import (
    WASAPI_DEVICE,
    FakeSd,
    _backend,
)
from test_audio_device_in_use_mor4030 import REAL_INVALID_DEVICE, FakePortAudioError

from rigplane.audio._bridge_state import BridgeState, BridgeStateChange
from rigplane.audio.backend import (
    AudioDeviceBusyError,
    AudioDeviceId,
    AudioDeviceInfo,
    FakeAudioBackend,
    _open_streams,
)
from rigplane.audio.bridge import AudioBridge
from rigplane.audio.lan_stream import AudioPacket
from rigplane.audio.usb_driver import UsbAudioDriver
from rigplane.testing.timing import await_until

_CABLE = "RigPlane Virtual Cable"
_CODEC = "USB Audio CODEC"


# ---------------------------------------------------------------------------
# Shared doubles
# ---------------------------------------------------------------------------


class _StateWaiter:
    """Wait for specific bridge states via callback (no timing assumptions)."""

    def __init__(self) -> None:
        self.events: list[BridgeStateChange] = []
        self._waiters: dict[BridgeState, asyncio.Event] = {}

    def __call__(self, change: BridgeStateChange) -> None:
        self.events.append(change)
        ev = self._waiters.get(change.current)
        if ev is not None:
            ev.set()

    async def wait_for(
        self, state: BridgeState, *, after: int = 0, timeout: float = 5.0
    ) -> None:
        if any(e.current == state for e in self.events[after:]):
            return
        ev = asyncio.Event()
        self._waiters[state] = ev
        if any(e.current == state for e in self.events[after:]):
            return
        await asyncio.wait_for(ev.wait(), timeout=timeout)


def _make_radio() -> types.SimpleNamespace:
    from rigplane.audio_bus import AudioBus

    radio: types.SimpleNamespace = types.SimpleNamespace(
        start_audio_rx_opus=AsyncMock(),
        stop_audio_rx_opus=AsyncMock(),
        start_audio_tx_pcm=AsyncMock(),
        stop_audio_tx_pcm=AsyncMock(),
        push_audio_tx_pcm=AsyncMock(),
        push_audio_tx_opus=AsyncMock(),
    )
    radio.audio_bus = AudioBus(radio)
    return radio


# ---------------------------------------------------------------------------
# Item 1: the bridge reconnect re-scans in its teardown idle window
# ---------------------------------------------------------------------------


class _RescanningCableBackend(FakeAudioBackend):
    """Bridge backend whose cable the OS renumbers when the radio returns.

    The stale scan loses the cable entirely; only ``refresh_devices`` (the
    PortAudio restart, which needs the idle window the reconnect teardown
    opens) brings the fresh scan with the cable at its new id.
    """

    def __init__(self) -> None:
        super().__init__(
            [
                AudioDeviceInfo(
                    id=AudioDeviceId(1),
                    name=_CABLE,
                    input_channels=2,
                    output_channels=2,
                )
            ]
        )
        self.refreshes = 0
        self.opened: list[tuple[str, int]] = []
        self.idle_at_refresh: list[bool] = []

    def power_cycle(self) -> None:
        """The radio's return renumbers the cable; the stale scan loses it."""
        self._devices = []

    def refresh_devices(self) -> bool:
        self.idle_at_refresh.append(
            not any(stream.running for stream in self.tx_streams)
        )
        self.refreshes += 1
        self._devices = [
            AudioDeviceInfo(
                id=AudioDeviceId(1),
                name=_CODEC,
                input_channels=1,
                output_channels=1,
            ),
            AudioDeviceInfo(
                id=AudioDeviceId(2),
                name=_CABLE,
                input_channels=2,
                output_channels=2,
            ),
        ]
        _open_streams.restart_if_idle(lambda: None)  # the real scan counter
        return True

    def open_tx(self, device, **kwargs):  # type: ignore[no-untyped-def]
        self.opened.append(("playback", int(device)))
        return super().open_tx(device, **kwargs)


async def test_bridge_reconnect_rescans_in_the_idle_window_and_rx_resumes():
    backend = _RescanningCableBackend()
    radio = _make_radio()
    waiter = _StateWaiter()
    bridge = AudioBridge(
        radio,
        device_name=_CABLE,
        tx_enabled=False,
        backend=backend,
        max_retries=5,
        retry_base_delay=0.01,
        on_state_changed=waiter,
    )
    await bridge.start()
    try:
        assert backend.opened == [("playback", 1)]
        checkpoint = len(waiter.events)

        # The outage: the radio returns, the OS renumbers the cable, and the
        # already-open playback stream survives it.
        backend.power_cycle()
        old_playback = backend.tx_streams[0]
        assert old_playback.running

        # The renumber surfaces as a device error on the next write, which
        # drives the bridge recovery path (teardown → restore).
        old_playback.fail_on_write = OSError("device removed")
        radio.audio_bus._on_opus_packet(
            AudioPacket(ident=0x80, send_seq=1, data=b"\x01\x02" * 960)
        )

        await waiter.wait_for(BridgeState.RUNNING, after=checkpoint, timeout=5.0)
        assert any(e.reason == "reconnected" for e in waiter.events[checkpoint:])

        # The re-scan ran once, while no bridge stream was open, and the
        # playback reopened at the renumbered cable id.
        assert backend.refreshes == 1
        assert backend.idle_at_refresh == [True]
        assert backend.opened[-1] == ("playback", 2)

        # RX resumes on the reopened playback.
        new_playback = backend.tx_streams[-1]
        assert new_playback.running
        radio.audio_bus._on_opus_packet(
            AudioPacket(ident=0x80, send_seq=2, data=b"\x03\x04" * 960)
        )
        await await_until(
            lambda: new_playback.written_frames,
            timeout=5.0,
            description="RX to resume on the reopened playback",
        )
    finally:
        await bridge.stop()


# ---------------------------------------------------------------------------
# Item 2: the driver allows the single re-scan for a vanishing device
# ---------------------------------------------------------------------------


class _VanishingSd(FakeSd):
    """Fake ``sounddevice`` whose opens fail until PortAudio restarts.

    Both the exclusive open and the shared fallback fail with the real
    run's invalid-device refusal (the vanished-device shape), so
    ``busy_error`` reports "held by another program". After the refresh
    pair runs, the opens either recover (returned device) or keep failing
    (genuinely held device).
    """

    def __init__(self, *, recover_after_refresh: bool) -> None:
        super().__init__(
            exclusive_error=FakePortAudioError(REAL_INVALID_DEVICE, -9996),
            shared_error=FakePortAudioError(REAL_INVALID_DEVICE, -9996),
            refuse_shared=True,
        )
        self._recover = recover_after_refresh
        self.refreshes = 0

    def query_devices(self, index=None):  # type: ignore[no-untyped-def]
        if index is None:
            return [
                {
                    "index": WASAPI_DEVICE,
                    "name": "Microphone (%s)" % _CODEC,
                    "hostapi": 3,
                    "max_input_channels": 1,
                    "max_output_channels": 1,
                    "default_samplerate": 48_000,
                }
            ]
        return super().query_devices(index)

    def _terminate(self) -> None:
        pass

    def _initialize(self) -> None:
        self.refreshes += 1
        if self._recover:
            self.refuse_exclusive = False
            self.refuse_shared = False


def _real_backend(sd: _VanishingSd):  # type: ignore[no-untyped-def]
    return _backend(sd, "exclusive-preferred")


async def test_rx_start_after_a_vanished_device_rescans_once_and_succeeds():
    sd = _VanishingSd(recover_after_refresh=True)
    driver = UsbAudioDriver(serial_port=None, backend=_real_backend(sd))

    await driver.start_rx(lambda _pcm: None)
    try:
        assert driver.rx_running
        assert sd.refreshes == 1
    finally:
        await driver.stop_rx()


async def test_tx_start_after_a_vanished_device_rescans_once_and_succeeds():
    sd = _VanishingSd(recover_after_refresh=True)
    driver = UsbAudioDriver(serial_port=None, backend=_real_backend(sd))

    await driver.start_tx()
    try:
        assert driver.tx_running
        assert sd.refreshes == 1
    finally:
        await driver.stop_tx()


async def test_genuine_busy_still_raises_after_the_single_rescan():
    sd = _VanishingSd(recover_after_refresh=False)
    driver = UsbAudioDriver(serial_port=None, backend=_real_backend(sd))

    with pytest.raises(AudioDeviceBusyError):
        await driver.start_rx(lambda _pcm: None)

    assert not driver.rx_running
    assert sd.refreshes == 1
