"""MOR-4521: RX audio comes back after the radio is powered off and on.

Scenario MOR-4338 with the audio bridge up: ``PortAudioBackend`` only
re-scans the OS devices while no stream is open anywhere in the process,
but the bridge keeps its playback stream open through a radio outage, so
a returned radio stayed silent while the bridge was up.

Item 1: the radio re-arm route (``AudioBus.restart_rx``, reached from the
session re-establish after a reconnect) reaches the USB driver's
declined-refresh path, which quiesces the bridge's device streams,
re-scans in the idle window, restores the bridge, and retries the start.
The regression below drives that route with an initially live bridge
playback and a returned USB capture device: no playback error is
injected, the refresh is required to run after the teardown, and actual
returned-radio RX data must reach the restored playback.

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

import numpy as np
import pytest
from _order_sensitive_radios import LanLikeRadio
from test_audio_backend_capture_mode_mor4030 import (
    WASAPI_DEVICE,
    FakeSd,
    _backend,
)
from test_audio_device_in_use_mor4030 import REAL_INVALID_DEVICE, FakePortAudioError

from rigplane.audio._bridge_state import BridgeState, BridgeStateChange
from rigplane.audio.backend import (
    AudioDeviceBusyError,
    AudioDeviceConfig,
    AudioDeviceId,
    AudioDeviceInfo,
    FakeAudioBackend,
    PortAudioBackend,
    device_scan_generation,
)
from rigplane.audio.bridge import AudioBridge
from rigplane.audio.lan_stream import AudioPacket
from rigplane.audio.session import AudioSession, AudioSessionState
from rigplane.audio.usb_driver import UsbAudioDriver
from rigplane.audio_bus import AudioBus
from rigplane.core.types import AudioCodec
from rigplane.testing.timing import VirtualClock, await_until

_CODEC = "USB Audio CODEC"
_CABLE = "RigPlane Virtual Cable"


# ---------------------------------------------------------------------------
# Shared doubles
# ---------------------------------------------------------------------------


def _codec_device(index: int) -> dict:
    return {
        "index": index,
        "name": _CODEC,
        "hostapi": 0,
        "max_input_channels": 1,
        "max_output_channels": 1,
        "default_samplerate": 48_000,
    }


def _cable_device(index: int) -> dict:
    return {
        "index": index,
        "name": _CABLE,
        "hostapi": 0,
        "max_input_channels": 2,
        "max_output_channels": 2,
        "default_samplerate": 48_000,
    }


class _PowerCycleSd(FakeSd):
    """Fake ``sounddevice`` with a stale scan until PortAudio restarts.

    ``query_devices()`` with no index returns the FROZEN scan (what
    PortAudio listed at init); ``_initialize`` re-freezes it from the
    CURRENT hardware. Opens on devices absent from the current hardware
    fail with the invalid-device refusal, and input callbacks are captured
    so the test can inject captured frames. ``bridge`` (set by the test)
    lets ``_terminate`` record whether the refresh ran with the bridge
    streams already released.
    """

    def __init__(self, devices: list[dict]) -> None:
        super().__init__()
        self.current = [dict(d) for d in devices]
        self.frozen_scan = [dict(d) for d in devices]
        self.calls: list[str] = []
        self.input_callbacks: list = []
        self.terminated_while_idle: list[bool] = []
        self.bridge: AudioBridge | None = None
        self._refresh_live_sets()

        base_input = self.InputStream
        base_output = self.OutputStream
        sd = self

        class _LiveInput(base_input):  # type: ignore[valid-type,misc]
            def __init__(self, **kw: object) -> None:
                if kw.get("device") not in sd.live_inputs:
                    raise FakePortAudioError(REAL_INVALID_DEVICE, -9996)
                super().__init__(**kw)
                callback = kw.get("callback")
                if callback is not None:
                    sd.input_callbacks.append(callback)

        class _LiveOutput(base_output):  # type: ignore[valid-type,misc]
            def __init__(self, **kw: object) -> None:
                if kw.get("device") not in sd.live_outputs:
                    raise FakePortAudioError(REAL_INVALID_DEVICE, -9996)
                super().__init__(**kw)

        self.InputStream = _LiveInput
        self.OutputStream = _LiveOutput

    def _refresh_live_sets(self) -> None:
        self.live_inputs = {
            d["index"] for d in self.current if d["max_input_channels"] > 0
        }
        self.live_outputs = {
            d["index"] for d in self.current if d["max_output_channels"] > 0
        }

    def set_current(self, devices: list[dict]) -> None:
        """New hardware reality; the frozen scan stays stale until refresh."""
        self.current = [dict(d) for d in devices]
        self._refresh_live_sets()

    def query_devices(self, index: int | None = None):  # type: ignore[override]
        if index is None:
            return [dict(d) for d in self.frozen_scan]
        for dev in self.current:
            if dev["index"] == index:
                return dict(dev)
        raise ValueError(f"Error querying device {index}")

    def query_hostapis(self, index: int):  # type: ignore[override]
        return {"name": "MME"}

    def _terminate(self) -> None:
        bridge = self.bridge
        self.terminated_while_idle.append(
            bridge is None
            or (
                bridge._rx_stream is None
                and bridge._tx_stream is None
                and bridge._duplex_stream is None
            )
        )
        self.calls.append("terminate")

    def _initialize(self) -> None:
        self.calls.append("initialize")
        self.frozen_scan = [dict(d) for d in self.current]


class _UsbRxHarness:
    """Minimal radio: RX through a real ``UsbAudioDriver``.

    Captured PCM is wrapped in synthetic packets (the backend's usual
    ``SYNTHETIC_RX_IDENT`` shape) so the bus and bridge see real packet
    flow from the driver to the playback stream.
    """

    def __init__(self, driver: UsbAudioDriver) -> None:
        self._driver = driver
        self.audio_tx_codec = AudioCodec.PCM_1CH_16BIT
        self.pushed: list[bytes] = []
        self._seq = 0
        # Wired by the test after the shared bus is built, so the bridge's
        # session wraps the same bus the test re-arms (production shape).
        self.audio_bus: AudioBus | None = None

    async def start_audio_rx_opus(self, callback, *, jitter_depth: int = 5) -> None:  # type: ignore[no-untyped-def]
        def on_pcm(pcm: bytes) -> None:
            packet = AudioPacket(ident=0x80, send_seq=self._seq, data=pcm)
            self._seq = (self._seq + 1) & 0xFFFF
            callback(packet)

        await self._driver.start_rx(on_pcm)

    async def stop_audio_rx_opus(self) -> None:
        await self._driver.stop_rx()

    @property
    def tx_running(self) -> bool:
        return self._driver.tx_running

    async def start_tx(self) -> None:
        await self._driver.start_tx()

    async def stop_tx(self) -> None:
        await self._driver.stop_tx()

    async def push_tx(self, pcm: bytes) -> None:
        await self._driver._push_tx_pcm(pcm)
        self.pushed.append(pcm)


# ---------------------------------------------------------------------------
# Item 1: the radio re-arm runs the re-scan through the bridge idle window
# ---------------------------------------------------------------------------


@pytest.mark.timeout(30)
async def test_returned_radio_re_arms_through_the_bridge_idle_window(
    virtual_clock: VirtualClock,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("rigplane.audio.usb_driver.platform.system", lambda: "Windows")
    sd = _PowerCycleSd([_codec_device(0), _cable_device(1)])
    backend = PortAudioBackend(dependency_loader=lambda: (sd, np))
    driver = UsbAudioDriver(
        config=AudioDeviceConfig(rx_device=_CODEC, tx_device=_CODEC), backend=backend
    )
    radio = _UsbRxHarness(driver)
    bus = AudioBus(radio, monotonic=virtual_clock)
    radio.audio_bus = bus
    session = AudioSession(radio, bus=bus, monotonic=virtual_clock)
    radio.audio_session = session
    events: list[BridgeStateChange] = []
    bridge = AudioBridge(
        radio,
        device_name=_CABLE,
        backend=backend,
        on_state_changed=events.append,
    )
    sd.bridge = bridge
    await bridge.start()
    try:
        assert bridge.bridge_state is BridgeState.RUNNING
        assert session.state is AudioSessionState.RX_TX
        rx_sub, tx_lease = bridge._rx_sub, bridge._tx_lease
        generation = device_scan_generation()

        # Baseline: real frames flow from the driver to the live playback.
        baseline = b"\x01\x02" * 960 * 10
        sd.input_callbacks[0](baseline, 9600, None, None)
        await await_until(
            lambda: bridge.metrics.rx_frames >= 10,
            timeout=5.0,
            description="baseline frames to reach the live playback",
        )

        # The outage: the radio vanishes (stale scan still lists it) and
        # its capture stops; the bridge playback on the healthy cable
        # stays open — no playback error is injected anywhere.
        sd.set_current([_cable_device(1)])
        await radio.stop_audio_rx_opus()
        await radio.stop_tx()
        assert not driver.rx_running
        surviving_playback = bridge._rx_stream
        assert surviving_playback is not None and surviving_playback.running
        assert bridge._tx_stream is not None and bridge._tx_stream.running

        await virtual_clock.elapse(session._rx_liveness_timeout)
        assert session.state is AudioSessionState.RECOVERING

        # The radio returns, renumbered; the scan is still stale.
        sd.set_current([_cable_device(1), _codec_device(2)])

        # The existing radio re-arm route: the refresh it needs is
        # declined while the playback is open, so the driver quiesces the
        # bridge, re-scans in the idle window, retries, and restores.
        recovery = asyncio.create_task(session.reestablish())
        try:
            await await_until(recovery.done, description="RX+TX session recovery")
            await recovery
        finally:
            if not recovery.done():
                recovery.cancel()
                await asyncio.gather(recovery, return_exceptions=True)

        assert bus.rx_active
        assert driver.rx_running
        assert driver.tx_running
        assert session.state is AudioSessionState.RX_TX
        assert session.rx_demand == session.tx_demand == 1
        assert bridge._rx_sub is rx_sub
        assert bridge._tx_lease is tx_lease
        assert tx_lease is not None and not tx_lease.released
        assert bridge._rx_task is not None and not bridge._rx_task.done()
        assert bridge._tx_task is not None and not bridge._tx_task.done()
        # The bridge never flapped at the state level: the quiesce and
        # restore are invisible to its state machine.
        assert bridge.bridge_state is BridgeState.RUNNING
        assert not any(e.current is BridgeState.RECONNECTING for e in events)
        # Exactly one PortAudio restart, and it ran with the bridge
        # streams already released (after the teardown, not before).
        assert sd.calls == ["terminate", "initialize"]
        assert sd.terminated_while_idle == [True]
        assert device_scan_generation() == generation + 1
        # The capture reopened on the renumbered device, the playback on
        # the re-resolved cable.
        input_opens = [e for e in sd.opened if e["kind"] == "input"]
        assert [e["device"] for e in input_opens] == [0, 1, 1, 2]
        output_opens = [e for e in sd.opened if e["kind"] == "output"]
        assert [e["device"] for e in output_opens] == [0, 1, 1, 2]

        # Actual returned-radio RX data reaches the restored playback.
        returned = b"\x03\x04" * 960 * 10
        frames_before = bridge.metrics.rx_frames
        radio_input = sd.input_callbacks[-1]
        radio_input(returned[: 1920 * 8], 960 * 8, None, None)
        await await_until(
            lambda: bridge.metrics.rx_frames >= frames_before + 8,
            timeout=5.0,
            description="returned-radio frames to reach the restored playback",
        )
        restored = [e for e in output_opens if e["device"] == 1][-1]
        collected = bytearray()
        # Keep a 160 ms fill while pumping the native callback, inside the
        # adaptive player's deadband, so the entire burst stays byte-exact.
        for index in range(14):
            buf = bytearray(1920)
            restored["callback"](buf, 960, None, None)
            collected += buf
            next_frame = returned[(8 + index) * 1920 : (9 + index) * 1920]
            radio_input(next_frame or bytes(1920), 960, None, None)
            await await_until(
                lambda: bridge.metrics.rx_frames >= frames_before + 9 + index,
                description="the next frame to replenish bridge playback",
            )
        assert bridge.metrics.rx_frames >= frames_before + 10
        assert bytes(returned) in bytes(collected)

        cable_input = sd.input_callbacks[-2]
        cable_input(b"\x05\x06" * 960, 960, None, None)
        await await_until(lambda: radio.pushed, description="restored bridge TX")
        assert radio.pushed[-1] == b"\x05\x06" * 960
        radio_output = [e for e in output_opens if e["device"] == 2][-1]
        buf = bytearray(1920)
        radio_output["callback"](buf, 960, None, None)
        assert buf == b"\x05\x06" * 960
    finally:
        await bridge.stop()


@pytest.mark.parametrize("pause_at", ["resolve", "open"])
async def test_stop_drains_an_inflight_rescan_restore(
    pause_at: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _RescanningCableBackend()
    bridge = AudioBridge(
        LanLikeRadio(), device_name=_CABLE, tx_enabled=False, backend=backend
    )
    await bridge.start()
    await bridge._quiesce_for_rescan()
    entered, resume = asyncio.Event(), asyncio.Event()
    if pause_at == "resolve":
        resolve = bridge._resolve_cable_devices

        async def paused_resolve():
            devices = await resolve()
            entered.set()
            await resume.wait()
            return devices

        monkeypatch.setattr(bridge, "_resolve_cable_devices", paused_resolve)
    else:
        from rigplane.audio.usb_driver import bounded_portaudio_pool

        open_stream = bounded_portaudio_pool.open_stream_bounded

        async def paused_open(*args, **kwargs):
            await open_stream(*args, **kwargs)
            entered.set()
            await resume.wait()

        monkeypatch.setattr(bounded_portaudio_pool, "open_stream_bounded", paused_open)

    restore = asyncio.create_task(bridge._restore_after_rescan())
    stop = None
    try:
        await await_until(entered.is_set, description="restore to pause")
        stop = asyncio.create_task(bridge.stop())
        await await_until(
            lambda: bridge.bridge_state is BridgeState.IDLE,
            description="stop to retire the bridge",
        )
        resume.set()
        await await_until(
            lambda: restore.done() and stop.done(), description="restore and stop"
        )
        await restore
        await stop
        assert bridge._rx_task is bridge._tx_task is None
        assert bridge._rx_stream is bridge._tx_stream is bridge._duplex_stream is None
        assert not any(stream.running for stream in backend.tx_streams)
        assert bridge._session.rx_demand == bridge._session.tx_demand == 0
        if pause_at == "resolve":
            assert backend.opened == [("playback", 1)]
    finally:
        resume.set()
        await asyncio.gather(restore, *([stop] if stop else []), return_exceptions=True)
        await bridge.stop()
        await bridge._teardown_streams()


@pytest.mark.parametrize("cancel_count", [1, 2])
async def test_cancel_during_quiesce_restores_the_partially_quiesced_bridge(
    cancel_count: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _RescanningCableBackend()
    bridge = AudioBridge(LanLikeRadio(), device_name=_CABLE, backend=backend)
    await bridge.start()
    driver = UsbAudioDriver(backend=backend)
    entered, resume = asyncio.Event(), asyncio.Event()
    stop_streams = bridge._stop_device_streams
    rx_sub = bridge._rx_sub
    tx_lease = bridge._tx_lease

    async def paused_stop() -> None:
        entered.set()
        await resume.wait()
        await stop_streams()

    monkeypatch.setattr(bridge, "_stop_device_streams", paused_stop)
    rescan = asyncio.create_task(
        driver._rescan_with_quiesced_streams(backend, direction="rx")
    )
    try:
        await await_until(entered.is_set, description="quiesce to reach stream stop")
        assert bridge._rx_task is bridge._tx_task is None
        for _ in range(cancel_count):
            rescan.cancel()
            await asyncio.sleep(0)
            assert not rescan.done()
        resume.set()
        await await_until(
            rescan.done, description="cancelled rescan to restore holders"
        )
        with pytest.raises(asyncio.CancelledError):
            await rescan
        assert bridge.bridge_state is BridgeState.RUNNING
        assert bridge._rx_sub is rx_sub
        assert bridge._tx_lease is tx_lease
        assert tx_lease is not None and not tx_lease.released
        assert bridge._session.rx_demand == bridge._session.tx_demand == 1
        assert not bridge._quiesced_for_rescan
        assert bridge._rx_stream is not None and bridge._rx_stream.running
        assert bridge._rx_task is not None and not bridge._rx_task.done()
        assert bridge._tx_stream is not None and bridge._tx_stream.running
        assert bridge._tx_task is not None and not bridge._tx_task.done()
        bridge._session.bus._on_opus_packet(
            AudioPacket(ident=0x80, send_seq=1, data=b"\x03\x04" * 960)
        )
        await await_until(
            lambda: backend.tx_streams[-1].written_frames,
            description="RX after cancellation during quiesce",
        )
    finally:
        resume.set()
        await asyncio.gather(rescan, return_exceptions=True)
        await bridge.stop()


async def test_old_rescan_restore_does_not_reopen_a_restarted_bridge() -> None:
    backend = _RescanningCableBackend()
    bridge = AudioBridge(
        LanLikeRadio(), device_name=_CABLE, tx_enabled=False, backend=backend
    )
    await bridge.start()
    try:
        await bridge._quiesce_for_rescan()
        await bridge.stop()
        await bridge.start()
        playback, rx_task = bridge._rx_stream, bridge._rx_task
        await bridge._restore_after_rescan()
        assert bridge._rx_stream is playback
        assert bridge._rx_task is rx_task
        assert backend.opened == [("playback", 1), ("playback", 1)]
        assert bridge._session.rx_demand == 1
    finally:
        await bridge.stop()


@pytest.mark.timeout(30)
async def test_bridge_reconnect_rescans_after_a_cable_failure(
    virtual_clock: VirtualClock,
) -> None:
    """Cable-side failure: the reconnect teardown opens the idle window."""
    backend = _RescanningCableBackend()
    radio = LanLikeRadio()
    events: list[BridgeStateChange] = []
    bridge = AudioBridge(
        radio,
        device_name=_CABLE,
        tx_enabled=False,
        backend=backend,
        max_retries=5,
        retry_base_delay=0.01,
        on_state_changed=events.append,
    )
    await bridge.start()
    try:
        assert backend.opened == [("playback", 1)]

        # The outage: the radio's return renumbers the cable, and the
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

        def reconnected() -> bool:
            return any(
                e.current is BridgeState.RUNNING and e.reason == "reconnected"
                for e in events
            )

        await await_until(
            lambda: bridge._reconnect_attempt == 1,
            description="reconnect to enter its backoff",
        )
        await virtual_clock.elapse(bridge._retry_base_delay)
        await await_until(reconnected, description="pool workers to finish reconnect")
        assert reconnected()

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
        return True

    def open_tx(self, device, **kwargs):  # type: ignore[no-untyped-def]
        self.opened.append(("playback", int(device)))
        return super().open_tx(device, **kwargs)


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

    def query_devices(self, index=None):  # type: ignore[override]
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


async def test_tx_start_after_a_vanished_device_rescans_once_and_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Pin the Windows policy seam: on Darwin the single duplex CODEC would
    # take the exclusive path (sd.Stream, which this fake does not
    # provide). The WASAPI regression under test is the exclusive/shared
    # OutputStream refusals, so force the two-stream path everywhere.
    monkeypatch.setattr("rigplane.audio.usb_driver.platform.system", lambda: "Windows")
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
