"""Tests for audio_bridge module."""

from __future__ import annotations

import asyncio
import concurrent.futures
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from rigplane.audio._bridge_metrics import BridgeMetrics
from rigplane.audio._bridge_state import BridgeState, BridgeStateChange
from rigplane.audio.backend import (
    AudioDeviceId,
    AudioDeviceInfo,
    FakeAudioBackend,
)
from rigplane.audio.lan_stream import AudioPacket
from rigplane.audio_bridge import (
    AudioBridge,
    CHANNELS,
    FRAME_BYTES,
    FRAME_MS,
    SAMPLE_RATE,
    SAMPLES_PER_FRAME,
    derive_bridge_label,
    find_loopback_device,
    list_audio_devices,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BH_DEVICE = AudioDeviceInfo(
    id=AudioDeviceId(1),
    name="BlackHole 2ch",
    input_channels=2,
    output_channels=2,
)


class _StateWaiter:
    """Test helper — waits for specific BridgeState transitions via callback."""

    def __init__(self) -> None:
        self.events: list[BridgeStateChange] = []
        self._waiters: dict[BridgeState, asyncio.Event] = {}

    def __call__(self, change: BridgeStateChange) -> None:
        self.events.append(change)
        ev = self._waiters.get(change.current)
        if ev is not None:
            ev.set()

    async def wait_for(
        self, state: BridgeState, *, after: int = 0, timeout: float = 2.0
    ) -> None:
        """Wait until the bridge enters *state*.

        Args:
            state: Target state.
            after: Only consider events at index >= *after* in the event
                list. Use ``len(waiter.events)`` before triggering an
                action to skip earlier transitions.
            timeout: Maximum wait in seconds.
        """
        # Already reached after the cutoff?
        if any(e.current == state for e in self.events[after:]):
            return
        # Need a fresh event — clear any previous one to avoid stale signal
        ev = asyncio.Event()
        self._waiters[state] = ev
        # Check again after registering (race window)
        if any(e.current == state for e in self.events[after:]):
            return
        await asyncio.wait_for(ev.wait(), timeout=timeout)


def _bridge_backend(
    devices: list[AudioDeviceInfo] | None = None,
) -> FakeAudioBackend:
    return FakeAudioBackend(
        devices
        or [
            AudioDeviceInfo(
                id=AudioDeviceId(0),
                name="Built-in Output",
                output_channels=2,
            ),
            _BH_DEVICE,
        ]
    )


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
    bus = AudioBus(radio)
    radio.audio_bus = bus
    return radio


def _bare_radio(**kwargs: object) -> types.SimpleNamespace:
    """Minimal radio stub for tests that don't call bridge.start()."""
    return types.SimpleNamespace(**kwargs)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_constants():
    assert SAMPLE_RATE == 48000
    assert CHANNELS == 1
    assert FRAME_MS == 20
    assert SAMPLES_PER_FRAME == 960
    assert FRAME_BYTES == 1920


# ---------------------------------------------------------------------------
# find_loopback_device (legacy compat)
# ---------------------------------------------------------------------------


def test_find_loopback_device_no_sounddevice():
    with patch.dict("sys.modules", {"sounddevice": None}):
        with pytest.raises(ImportError, match="sounddevice"):
            find_loopback_device("BlackHole")


def test_find_loopback_device_found():
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = [
        {"name": "Built-in Output", "index": 0},
        {"name": "BlackHole 2ch", "index": 1},
    ]
    with patch.dict("sys.modules", {"sounddevice": mock_sd}):
        dev = find_loopback_device("BlackHole")
    assert dev is not None
    assert dev["name"] == "BlackHole 2ch"


def test_find_loopback_device_not_found():
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = [
        {"name": "Built-in Output", "index": 0},
        {"name": "Built-in Input", "index": 1},
    ]
    with patch.dict("sys.modules", {"sounddevice": mock_sd}):
        dev = find_loopback_device("BlackHole")
    assert dev is None


def test_find_loopback_device_auto_detect():
    mock_sd = MagicMock()
    mock_sd.query_devices.return_value = [
        {"name": "Built-in Output", "index": 0},
        {"name": "Loopback Audio", "index": 1},
    ]
    with patch.dict("sys.modules", {"sounddevice": mock_sd}):
        dev = find_loopback_device(None)
    assert dev is not None
    assert dev["name"] == "Loopback Audio"


# ---------------------------------------------------------------------------
# list_audio_devices (legacy compat)
# ---------------------------------------------------------------------------


def test_list_audio_devices():
    mock_sd = MagicMock()
    devs = [{"name": "A", "index": 0}, {"name": "B", "index": 1}]
    mock_sd.query_devices.return_value = devs
    with patch.dict("sys.modules", {"sounddevice": mock_sd}):
        result = list_audio_devices()
    assert result == devs


def test_list_audio_devices_no_sounddevice():
    with patch.dict("sys.modules", {"sounddevice": None}):
        with pytest.raises(ImportError, match="sounddevice"):
            list_audio_devices()


# ---------------------------------------------------------------------------
# AudioBridge init
# ---------------------------------------------------------------------------


def test_bridge_init_defaults():
    radio = _bare_radio()
    bridge = AudioBridge(radio)
    assert not bridge.running
    assert bridge.bridge_state == BridgeState.IDLE
    s = bridge.stats
    assert s["running"] is False
    assert s["bridge_state"] == "idle"
    assert s["reconnect_attempt"] == 0
    assert s["rx_frames"] == 0
    assert s["tx_frames"] == 0
    assert s["rx_drops"] == 0
    assert s["uptime_seconds"] == 0.0
    assert s["rx_interval_ms"] == 0.0
    assert s["tx_interval_ms"] == 0.0
    assert s["buffer_size"] == 0


def test_bridge_init_custom():
    radio = _bare_radio()
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as custom_executor:
        bridge = AudioBridge(
            radio,
            device_name="MyDevice",
            sample_rate=8000,
            channels=2,
            frame_ms=40,
            tx_enabled=False,
            tx_executor=custom_executor,
        )
        assert bridge._device_name == "MyDevice"
        assert bridge._sample_rate == 8000
        assert bridge._channels == 2
        assert bridge._frame_ms == 40
        assert bridge._tx_enabled is False
        assert bridge._tx_executor is custom_executor


# ---------------------------------------------------------------------------
# AudioBridge start — device not found
# ---------------------------------------------------------------------------


async def test_bridge_start_no_device():
    radio = _bare_radio()
    backend = FakeAudioBackend(
        [AudioDeviceInfo(id=AudioDeviceId(0), name="Built-in", output_channels=2)]
    )
    bridge = AudioBridge(radio, device_name="BlackHole", backend=backend)
    with pytest.raises(RuntimeError, match="Virtual audio device not found"):
        await bridge.start()
    # State should revert to IDLE on start failure
    assert bridge.bridge_state == BridgeState.IDLE


# ---------------------------------------------------------------------------
# AudioBridge start + stop — happy path
# ---------------------------------------------------------------------------


async def test_bridge_start_stop_rx_only():
    radio = _make_radio()
    backend = _bridge_backend()
    bridge = AudioBridge(
        radio, device_name="BlackHole", tx_enabled=False, backend=backend
    )
    await bridge.start()

    assert bridge._running
    assert bridge.bridge_state == BridgeState.RUNNING
    assert radio.audio_bus.subscriber_count == 1
    assert len(backend.tx_streams) == 1
    assert backend.tx_streams[0].running

    await bridge.stop()
    assert not bridge._running
    assert bridge.bridge_state == BridgeState.IDLE
    assert radio.audio_bus.subscriber_count == 0
    assert backend.tx_streams[0].stopped_count == 1


async def test_bridge_start_already_running():
    radio = _make_radio()
    backend = _bridge_backend()
    bridge = AudioBridge(
        radio, device_name="BlackHole", tx_enabled=False, backend=backend
    )
    await bridge.start()
    await bridge.start()  # no-op
    assert radio.audio_bus.subscriber_count == 1
    await bridge.stop()


async def test_bridge_stop_when_not_running():
    radio = _bare_radio()
    bridge = AudioBridge(radio)
    await bridge.stop()  # no-op, no error


# ---------------------------------------------------------------------------
# RX-before-TX start order against a LAN-like state machine (MOR-556)
# ---------------------------------------------------------------------------


class _LanLikeRadio:
    """Radio stub mirroring ``LanAudioStream``'s RX/TX state machine.

    The real LAN stream supports RX-then-TX only: ``start_rx`` requires
    IDLE state, while ``start_tx`` flips the single state field to
    "transmitting". Arming radio TX before the AudioBus RX subscribe
    therefore kills RX entirely (regression from #1735, MOR-556).
    ``FakeAudioBackend``-style order-insensitive stubs cannot catch this.
    """

    def __init__(self) -> None:
        from rigplane.audio_bus import AudioBus
        from rigplane.types import AudioCodec

        self.state = "idle"
        self.calls: list[str] = []
        self.rx_callback: object | None = None
        self.audio_tx_codec = AudioCodec.PCM_1CH_16BIT
        self.audio_bus = AudioBus(self)

    async def start_rx(
        self, callback: object, *, jitter_depth: int | None = None
    ) -> None:
        self.calls.append("start_rx")
        if self.state != "idle":
            raise RuntimeError(f"Cannot start RX in state {self.state}")
        self.rx_callback = callback
        self.state = "receiving"

    async def stop_rx(self) -> None:
        if self.state == "receiving":
            self.state = "idle"
        self.rx_callback = None

    async def start_tx(self) -> None:
        self.calls.append("start_tx")
        if self.state == "transmitting":
            raise RuntimeError("Already transmitting")
        self.state = "transmitting"

    async def stop_tx(self) -> None:
        if self.state != "transmitting":
            return
        self.state = "receiving" if self.rx_callback is not None else "idle"

    async def push_tx(self, audio_data: bytes) -> None:
        if self.state != "transmitting":
            raise RuntimeError(f"Cannot push TX in state {self.state}")


async def test_bridge_subscribes_rx_before_arming_tx():
    """Regression MOR-556: bus RX subscribe must happen BEFORE radio TX arm.

    On LAN, ``start_tx`` puts the stream in TRANSMITTING and a subsequent
    ``start_rx`` raises — leaving RX dead and the packet queue undrained.
    """
    radio = _LanLikeRadio()
    backend = _bridge_backend()
    bridge = AudioBridge(radio, device_name="BlackHole", backend=backend)
    await bridge.start()
    try:
        # RX must be live on the radio — the regression left it dead.
        assert radio.audio_bus.rx_active
        assert radio.rx_callback is not None
        # TX is still armed for non-rx_only configs — after RX, never before.
        assert radio.state == "transmitting"
        assert radio.calls == ["start_rx", "start_tx"]
        assert bridge._tx_started
    finally:
        await bridge.stop()
    assert radio.audio_bus.subscriber_count == 0
    assert radio.state == "idle"


async def test_bridge_rx_only_never_arms_tx_on_lan_state_machine():
    """rx_only semantics are preserved by the MOR-556 reorder: no TX arm."""
    radio = _LanLikeRadio()
    backend = _bridge_backend()
    bridge = AudioBridge(
        radio, device_name="BlackHole", tx_enabled=False, backend=backend
    )
    await bridge.start()
    try:
        assert radio.audio_bus.rx_active
        assert radio.state == "receiving"
        assert "start_tx" not in radio.calls
    finally:
        await bridge.stop()
    assert radio.state == "idle"


# ---------------------------------------------------------------------------
# TX-before-RX start order on same-device exclusive USB duplex (MOR-559)
# ---------------------------------------------------------------------------


class _ExclusiveUsbRadio:
    """Radio stub mirroring the FTX-1 same-device USB CODEC (MOR-559).

    ``audio_duplex_mode == "exclusive"`` (MOR-534): RX and TX resolve to ONE
    physical macOS C-Media device. Per the MOR-531 live de-risk, adding the
    TX playback leg to an ALREADY-RUNNING RX capture triggers CoreAudio AUHAL
    paramErr -50 and silently kills the capture (no Python exception — live,
    the bridge then reported "started (RX+TX)" with dead RX). Adding RX to a
    running TX leg is clean. The stub reproduces the silent capture death so
    the wrong order fails the test the same way it failed live.
    """

    audio_duplex_mode = "exclusive"

    def __init__(self) -> None:
        from rigplane.audio_bus import AudioBus
        from rigplane.types import AudioCodec

        self.rx_running = False
        self.tx_running = False
        self.calls: list[str] = []
        self.rx_callback: object | None = None
        self.audio_tx_codec = AudioCodec.PCM_1CH_16BIT
        self.audio_bus = AudioBus(self)

    async def start_rx(
        self, callback: object, *, jitter_depth: int | None = None
    ) -> None:
        self.calls.append("start_rx")
        self.rx_callback = callback
        self.rx_running = True

    async def stop_rx(self) -> None:
        self.rx_running = False
        self.rx_callback = None

    async def start_tx(self) -> None:
        self.calls.append("start_tx")
        if self.rx_running:
            # ||PaMacCore (AUHAL)|| err='-50': the TX open nominally succeeds
            # but the device's running RX capture dies silently.
            self.rx_running = False
        self.tx_running = True

    async def stop_tx(self) -> None:
        self.tx_running = False

    async def push_tx(self, audio_data: bytes) -> None:
        if not self.tx_running:
            raise RuntimeError("TX not armed")


async def test_bridge_arms_tx_before_rx_on_exclusive_duplex():
    """Regression MOR-559: ``audio_duplex_mode == "exclusive"`` radios need
    the radio TX leg armed BEFORE the bus RX subscribe (pre-MOR-556 order) —
    the same-device TX open kills an already-running RX capture (-50).
    """
    radio = _ExclusiveUsbRadio()
    backend = _bridge_backend()
    bridge = AudioBridge(radio, device_name="BlackHole", backend=backend)
    await bridge.start()
    try:
        assert radio.calls == ["start_tx", "start_rx"]
        # RX capture must survive bridge start — the regression killed it.
        assert radio.rx_running
        assert radio.audio_bus.rx_active
        assert radio.tx_running
        assert bridge._tx_started
    finally:
        await bridge.stop()
    assert radio.audio_bus.subscriber_count == 0


async def test_bridge_rx_only_on_exclusive_duplex_still_starts_rx():
    """rx_only on an exclusive-duplex radio: RX starts, TX never armed."""
    radio = _ExclusiveUsbRadio()
    backend = _bridge_backend()
    bridge = AudioBridge(
        radio, device_name="BlackHole", tx_enabled=False, backend=backend
    )
    await bridge.start()
    try:
        assert radio.rx_running
        assert "start_tx" not in radio.calls
    finally:
        await bridge.stop()


# ---------------------------------------------------------------------------
# Failed initial start must release the bus subscription (MOR-560)
# ---------------------------------------------------------------------------


class _RxStartFailRadio:
    """Radio stub whose RX start fails.

    The AudioBus swallows the ``start_rx`` error (logs "audio-bus: failed to
    start RX") and leaves ``rx_active`` False — ``_subscribe_bus`` then raises
    AFTER the subscription is already registered with the bus.
    """

    def __init__(self) -> None:
        from rigplane.audio_bus import AudioBus

        self.audio_bus = AudioBus(self)

    async def start_rx(
        self, callback: object, *, jitter_depth: int | None = None
    ) -> None:
        raise RuntimeError("RX hardware unavailable")

    async def stop_rx(self) -> None:
        pass


class _OpenFailBackend(FakeAudioBackend):
    """FakeAudioBackend whose playback-stream open raises (post-subscribe)."""

    def open_tx(self, device: AudioDeviceId, **kwargs: object) -> object:
        raise OSError("simulated PortAudio open failure")


async def test_bridge_failed_rx_start_releases_bus_subscription():
    """Regression MOR-560: the ``_subscribe_bus`` rx_active=False raise must
    not leak the just-registered bus subscription.

    ``start()`` reverts to IDLE on failure and ``stop()`` early-returns on
    IDLE, so without teardown in the failure path the orphaned subscriber
    stays on the bus forever.
    """
    radio = _RxStartFailRadio()
    backend = _bridge_backend()
    bridge = AudioBridge(
        radio, device_name="BlackHole", tx_enabled=False, backend=backend
    )
    with pytest.raises(RuntimeError, match="radio RX failed to start"):
        await bridge.start()
    assert bridge.bridge_state == BridgeState.IDLE
    assert radio.audio_bus.subscriber_count == 0


async def test_bridge_failed_stream_open_releases_bus_subscription():
    """Regression MOR-560: a device-stream open failure AFTER the rx-first
    bus subscribe must tear the subscription down — otherwise the leaked
    subscriber keeps radio RX running with no consumer draining the queue.
    """
    radio = _make_radio()
    backend = _OpenFailBackend(
        [
            AudioDeviceInfo(
                id=AudioDeviceId(0),
                name="Built-in Output",
                output_channels=2,
            ),
            _BH_DEVICE,
        ]
    )
    bridge = AudioBridge(
        radio, device_name="BlackHole", tx_enabled=False, backend=backend
    )
    with pytest.raises(OSError, match="simulated PortAudio open failure"):
        await bridge.start()
    assert bridge.bridge_state == BridgeState.IDLE
    assert radio.audio_bus.subscriber_count == 0
    # Last-subscriber removal must have stopped radio RX too.
    assert radio.audio_bus.rx_active is False
    radio.stop_audio_rx_opus.assert_awaited_once()


# ---------------------------------------------------------------------------
# RX callback — packets flow from bus to backend TxStream
# ---------------------------------------------------------------------------


async def test_bridge_rx_via_bus():
    radio = _make_radio()
    backend = _bridge_backend()
    bridge = AudioBridge(
        radio, device_name="BlackHole", tx_enabled=False, backend=backend
    )
    await bridge.start()

    packet = AudioPacket(ident=0x80, send_seq=0, data=b"\x01\x02\x03")
    radio.audio_bus._on_opus_packet(packet)
    assert bridge._subscription._received == 1

    radio.audio_bus._on_opus_packet(None)
    assert bridge._subscription._received == 2

    await bridge.stop()


# ---------------------------------------------------------------------------
# TX path — captured audio flows from backend RxStream to radio
# ---------------------------------------------------------------------------


async def test_bridge_tx_path_uses_backend_rx_stream():
    radio = _make_radio()
    backend = _bridge_backend()
    bridge = AudioBridge(
        radio, device_name="BlackHole", tx_enabled=True, backend=backend
    )
    await bridge.start()

    assert len(backend.tx_streams) == 1
    assert len(backend.rx_streams) == 1
    assert backend.rx_streams[0].running

    loud_frame = (1000).to_bytes(2, "little", signed=True) * SAMPLES_PER_FRAME
    backend.rx_streams[0].inject_frame(loud_frame)

    await asyncio.sleep(0.05)
    await bridge.stop()

    # PCM session (no opus codec) → push_audio_tx_pcm is called
    assert radio.push_audio_tx_pcm.called or bridge._tx_frames > 0


async def test_bridge_tx_path_keeps_pcm_api_for_opus_radio():
    from rigplane.types import AudioCodec

    radio = _make_radio()
    radio.audio_codec = AudioCodec.OPUS_1CH
    backend = _bridge_backend()

    class _DummyDecoder:
        def __init__(self, *_args: object) -> None:
            pass

    fake_opuslib = types.SimpleNamespace(Decoder=_DummyDecoder)
    with (
        patch("rigplane.audio.bridge._require_opuslib", return_value=None),
        patch.dict("sys.modules", {"opuslib": fake_opuslib}),
    ):
        bridge = AudioBridge(
            radio, device_name="BlackHole", tx_enabled=True, backend=backend
        )
        await bridge.start()

        loud_frame = (1000).to_bytes(2, "little", signed=True) * SAMPLES_PER_FRAME
        backend.rx_streams[0].inject_frame(loud_frame)

        await asyncio.sleep(0.05)
        await bridge.stop()

    radio.start_audio_tx_pcm.assert_awaited_once()
    radio.push_audio_tx_pcm.assert_awaited()
    radio.push_audio_tx_opus.assert_not_called()


async def test_bridge_tx_queue_overflow_drops_oldest_and_counts_overrun():
    sent_frames: list[bytes] = []

    async def push_audio_tx_pcm(frame: bytes) -> None:
        sent_frames.append(frame)
        if len(sent_frames) == 2:
            bridge._running = False

    radio = _bare_radio(push_audio_tx_pcm=AsyncMock(side_effect=push_audio_tx_pcm))
    bridge = AudioBridge(radio)
    bridge._tx_queue = asyncio.Queue(maxsize=2)
    bridge._tx_stream = types.SimpleNamespace(running=True)

    stale = (1000).to_bytes(2, "little", signed=True) * SAMPLES_PER_FRAME
    queued = (2000).to_bytes(2, "little", signed=True) * SAMPLES_PER_FRAME
    live = (3000).to_bytes(2, "little", signed=True) * SAMPLES_PER_FRAME

    # Fill the queue before starting the consumer to model a stalled TX loop.
    bridge._enqueue_tx(stale)
    bridge._enqueue_tx(queued)
    bridge._enqueue_tx(live)

    assert bridge.metrics.tx_overruns == 1
    assert bridge.stats["tx_overruns"] == 1
    assert bridge._tx_queue.qsize() == 2

    bridge._running = True
    await asyncio.wait_for(asyncio.create_task(bridge._tx_loop()), timeout=1.0)

    assert sent_frames == [queued, live]
    assert bridge._tx_frames == 2


# ---------------------------------------------------------------------------
# State machine — BridgeState transitions
# ---------------------------------------------------------------------------


def test_initial_state_is_idle():
    radio = _bare_radio()
    bridge = AudioBridge(radio)
    assert bridge.bridge_state == BridgeState.IDLE


async def test_state_transitions_to_running_on_start():
    radio = _make_radio()
    backend = _bridge_backend()
    events: list[BridgeStateChange] = []
    bridge = AudioBridge(
        radio,
        device_name="BlackHole",
        tx_enabled=False,
        backend=backend,
        on_state_changed=events.append,
    )
    await bridge.start()
    assert bridge.bridge_state == BridgeState.RUNNING

    # Expect IDLE→CONNECTING→RUNNING
    assert len(events) == 2
    assert events[0].previous == BridgeState.IDLE
    assert events[0].current == BridgeState.CONNECTING
    assert events[0].reason == "start"
    assert events[1].previous == BridgeState.CONNECTING
    assert events[1].current == BridgeState.RUNNING
    assert events[1].reason == "started"

    await bridge.stop()
    # RUNNING→IDLE
    assert events[-1].current == BridgeState.IDLE
    assert events[-1].reason == "stopped"


async def test_on_state_changed_callback_fires():
    radio = _make_radio()
    backend = _bridge_backend()
    events: list[BridgeStateChange] = []
    bridge = AudioBridge(
        radio,
        device_name="BlackHole",
        tx_enabled=False,
        backend=backend,
        on_state_changed=events.append,
    )
    await bridge.start()
    await bridge.stop()

    assert len(events) >= 3  # CONNECTING, RUNNING, IDLE
    states = [e.current for e in events]
    assert BridgeState.CONNECTING in states
    assert BridgeState.RUNNING in states
    assert BridgeState.IDLE in states


# ---------------------------------------------------------------------------
# Reconnect state machine
# ---------------------------------------------------------------------------


async def test_reconnect_on_stream_write_failure():
    """When the RX TxStream write fails, bridge reconnects."""
    radio = _make_radio()
    backend = _bridge_backend()
    waiter = _StateWaiter()
    bridge = AudioBridge(
        radio,
        device_name="BlackHole",
        tx_enabled=False,
        backend=backend,
        max_retries=2,
        retry_base_delay=0.01,
        on_state_changed=waiter,
    )
    await bridge.start()
    checkpoint = len(waiter.events)  # skip initial RUNNING

    backend.tx_streams[0].fail_on_write = OSError("device removed")
    packet = AudioPacket(ident=0x80, send_seq=1, data=b"\x01\x02\x03" * 100)
    radio.audio_bus._on_opus_packet(packet)

    # Wait for reconnect to complete (event-based, no timing assumption)
    await waiter.wait_for(BridgeState.RUNNING, after=checkpoint, timeout=2.0)
    assert bridge.bridge_state == BridgeState.RUNNING
    assert any(e.reason == "reconnected" for e in waiter.events)

    await bridge.stop()


async def test_reconnect_succeeds_when_device_returns():
    """Device removed then re-added — bridge reconnects."""
    radio = _make_radio()
    backend = _bridge_backend()
    waiter = _StateWaiter()
    bridge = AudioBridge(
        radio,
        device_name="BlackHole",
        tx_enabled=False,
        backend=backend,
        max_retries=5,
        retry_base_delay=0.01,
        on_state_changed=waiter,
    )
    await bridge.start()
    checkpoint = len(waiter.events)

    backend.tx_streams[0].fail_on_write = OSError("device removed")
    backend.remove_devices()

    packet = AudioPacket(ident=0x80, send_seq=2, data=b"\xaa" * 100)
    radio.audio_bus._on_opus_packet(packet)

    # Wait for RECONNECTING state
    await waiter.wait_for(BridgeState.RECONNECTING, after=checkpoint, timeout=2.0)

    # Bring device back — reconnect loop will find it on next retry
    backend.add_device(_BH_DEVICE)

    # Wait for successful reconnect
    await waiter.wait_for(BridgeState.RUNNING, after=checkpoint, timeout=2.0)
    assert bridge.bridge_state == BridgeState.RUNNING
    assert any(e.reason == "reconnected" for e in waiter.events)

    await bridge.stop()


async def test_failed_state_after_max_retries():
    """Bridge enters FAILED when device never comes back."""
    radio = _make_radio()
    backend = _bridge_backend()
    waiter = _StateWaiter()
    bridge = AudioBridge(
        radio,
        device_name="BlackHole",
        tx_enabled=False,
        backend=backend,
        max_retries=2,
        retry_base_delay=0.01,
        on_state_changed=waiter,
    )
    await bridge.start()

    backend.tx_streams[0].fail_on_write = OSError("gone")
    backend.remove_devices()

    packet = AudioPacket(ident=0x80, send_seq=3, data=b"\xbb" * 100)
    radio.audio_bus._on_opus_packet(packet)

    # Wait for FAILED state (event-based — no timing assumption)
    await waiter.wait_for(BridgeState.FAILED, timeout=2.0)
    assert bridge.bridge_state == BridgeState.FAILED
    assert any(e.reason == "max_retries" for e in waiter.events)


async def test_stop_cancels_reconnect_task():
    """Calling stop() during reconnect cancels the reconnect loop."""
    radio = _make_radio()
    backend = _bridge_backend()
    waiter = _StateWaiter()
    bridge = AudioBridge(
        radio,
        device_name="BlackHole",
        tx_enabled=False,
        backend=backend,
        max_retries=10,
        retry_base_delay=1.0,  # long delay so reconnect is in progress
        on_state_changed=waiter,
    )
    await bridge.start()

    backend.tx_streams[0].fail_on_write = OSError("gone")
    backend.remove_devices()

    packet = AudioPacket(ident=0x80, send_seq=4, data=b"\xcc" * 100)
    radio.audio_bus._on_opus_packet(packet)

    # Wait for RECONNECTING (event-based)
    await waiter.wait_for(BridgeState.RECONNECTING, timeout=2.0)

    # Stop should cancel the long backoff reconnect
    await bridge.stop()
    assert bridge.bridge_state == BridgeState.IDLE


async def test_stats_includes_bridge_state():
    radio = _make_radio()
    backend = _bridge_backend()
    bridge = AudioBridge(
        radio, device_name="BlackHole", tx_enabled=False, backend=backend
    )
    assert bridge.stats["bridge_state"] == "idle"
    await bridge.start()
    assert bridge.stats["bridge_state"] == "running"
    await bridge.stop()
    assert bridge.stats["bridge_state"] == "idle"


# ---------------------------------------------------------------------------
# Latency stats
# ---------------------------------------------------------------------------


def test_stats_has_new_fields():
    radio = _bare_radio()
    bridge = AudioBridge(radio)
    s = bridge.stats
    assert "uptime_seconds" in s
    assert "rx_interval_ms" in s
    assert "tx_interval_ms" in s
    assert "buffer_size" in s
    assert "bridge_state" in s
    assert "reconnect_attempt" in s


def test_rx_latency_calculation():
    import time

    radio = _bare_radio()
    bridge = AudioBridge(radio)
    bridge._last_rx_time = time.monotonic() - 0.020
    bridge._rx_latency_samples.append(0.020)
    bridge._rx_latency_samples.append(0.020)

    s = bridge.stats
    assert s["rx_interval_ms"] == pytest.approx(20.0, abs=0.1)
    assert s["buffer_size"] == 2


def test_tx_latency_calculation():
    radio = _bare_radio()
    bridge = AudioBridge(radio)
    bridge._tx_latency_samples.append(0.040)
    bridge._tx_latency_samples.append(0.040)

    s = bridge.stats
    assert s["tx_interval_ms"] == pytest.approx(40.0, abs=0.1)


def test_latency_buffer_capped_at_100():
    import time

    radio = _bare_radio()
    bridge = AudioBridge(radio)
    bridge._last_rx_time = time.monotonic() - 0.020

    bridge._rx_latency_samples = [0.020] * 100
    bridge._rx_latency_samples.append(0.030)
    if len(bridge._rx_latency_samples) > 100:
        bridge._rx_latency_samples.pop(0)

    assert len(bridge._rx_latency_samples) == 100
    assert bridge.stats["buffer_size"] == 100


# ---------------------------------------------------------------------------
# derive_bridge_label
# ---------------------------------------------------------------------------


def test_derive_label_explicit():
    radio = _bare_radio(model="IC-7610")
    assert derive_bridge_label(radio, "my-label") == "my-label"


def test_derive_label_from_model():
    radio = _bare_radio(model="IC-7610")
    assert derive_bridge_label(radio, None) == "rigplane (IC-7610)"


def test_derive_label_no_model():
    radio = _bare_radio()  # no model attr — same semantics as MagicMock(spec=[])
    assert derive_bridge_label(radio, None) == "rigplane"


def test_derive_label_empty_model():
    radio = _bare_radio(model="")
    assert derive_bridge_label(radio, None) == "rigplane"


# ---------------------------------------------------------------------------
# Label parameter
# ---------------------------------------------------------------------------


def test_bridge_label_default():
    radio = _bare_radio()
    bridge = AudioBridge(radio)
    assert bridge.label == "rigplane"


def test_bridge_label_custom():
    radio = _bare_radio()
    bridge = AudioBridge(radio, label="rigplane (IC-7610)")
    assert bridge.label == "rigplane (IC-7610)"


def test_bridge_label_in_stats():
    radio = _bare_radio()
    bridge = AudioBridge(radio, label="rigplane (IC-905)")
    assert bridge.stats["label"] == "rigplane (IC-905)"


async def test_bridge_label_in_log_messages(caplog):
    import logging

    radio = _bare_radio()
    bridge = AudioBridge(radio, label="rigplane (IC-905)")

    with caplog.at_level(logging.WARNING):
        bridge._running = True
        await bridge.start()

    assert "rigplane (IC-905): already running" in caplog.text


# ---------------------------------------------------------------------------
# BridgeMetrics
# ---------------------------------------------------------------------------


def test_metrics_returns_bridge_metrics_instance():
    radio = _bare_radio()
    bridge = AudioBridge(radio)
    m = bridge.metrics
    assert isinstance(m, BridgeMetrics)
    assert m.running is False
    assert m.bridge_state == "idle"
    assert m.rx_frames == 0
    assert m.rx_jitter_ms == 0.0
    assert m.rx_level_dbfs == -96.0
    assert m.tx_level_dbfs == -96.0
    assert m.rx_underruns == 0
    assert m.tx_overruns == 0


def test_metrics_to_dict_backward_compat():
    """stats returns a dict with all BridgeMetrics fields."""
    radio = _bare_radio()
    bridge = AudioBridge(radio)
    s = bridge.stats
    assert isinstance(s, dict)
    assert "rx_jitter_ms" in s
    assert "rx_level_dbfs" in s
    assert "tx_overruns" in s
    assert "bridge_state" in s


def test_metrics_jitter_computed():
    """Jitter is the std dev of inter-frame intervals."""
    radio = _bare_radio()
    bridge = AudioBridge(radio)
    # Vary intervals: 20ms, 22ms, 18ms, 20ms
    bridge._rx_latency_samples = [0.020, 0.022, 0.018, 0.020]
    m = bridge.metrics
    assert m.rx_jitter_ms > 0
    assert m.rx_jitter_ms < 5  # should be small


async def test_on_metrics_callback():
    """on_metrics callback receives BridgeMetrics snapshots."""
    radio = _make_radio()
    backend = _bridge_backend()
    metrics_list: list[BridgeMetrics] = []
    bridge = AudioBridge(
        radio,
        device_name="BlackHole",
        tx_enabled=False,
        backend=backend,
        on_metrics=metrics_list.append,
    )
    await bridge.start()

    # Deliver 50 frames to trigger a metrics emission (every 50 frames)
    for i in range(51):
        packet = AudioPacket(ident=0x80, send_seq=i, data=b"\xaa" * 100)
        radio.audio_bus._on_opus_packet(packet)

    await asyncio.sleep(0.1)
    await bridge.stop()

    assert len(metrics_list) >= 1
    assert isinstance(metrics_list[0], BridgeMetrics)
    assert metrics_list[0].rx_frames > 0
