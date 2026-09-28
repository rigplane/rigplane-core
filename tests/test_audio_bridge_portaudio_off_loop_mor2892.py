"""MOR-2892 review round 1 — AudioBridge PortAudio calls stay off the loop.

The bridge is reachable from the web server (POST/DELETE ``/api/v1/bridge``
and the SIGTERM shutdown path), and until this round its device
enumeration (``sd.query_devices()``) and its stream start/stop
(``sd.InputStream``/``OutputStream``/``sd.Stream`` + ``.start()``,
Pa_StopStream/Pa_CloseStream) ran synchronously ON the event loop — the
same freeze class as the MOR-2892 stand incident. They now go through
the shared bounded PortAudio pool (``bounded_portaudio_pool``): one
executor, one worker bound, one saturation counter for the driver AND
the bridge.

Each test wedges one operation behind a blocking fake and proves the
three properties: the loop keeps answering while it is stuck, the
bridge request fails within the bound, and exactly one actionable
warning names the stuck operation.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
import types
from unittest.mock import AsyncMock

import pytest

from rigplane.audio._bridge_state import BridgeState
from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.audio.bridge import AudioBridge
from rigplane.audio.usb_driver import AudioCaptureOpenTimeoutError

# The pool lives in usb_driver, so its warnings carry that logger name —
# the shared pool's voice, whichever consumer submitted the stuck call.
_LOGGER_NAME = "rigplane.audio.usb_driver"
# Tiny relative to the production default — keeps this suite fast while
# still exercising the real asyncio.wait()-based bound.
_TEST_TIMEOUT_S = 0.05

_RP_DEVICE = AudioDeviceInfo(
    id=AudioDeviceId(1),
    name="RigPlane Virtual Cable",
    input_channels=2,
    output_channels=2,
)


def _make_radio() -> types.SimpleNamespace:
    """Radio double with the legacy PCM TX surface (mirrors test_audio_bridge).

    No neutral ``start_tx`` surface, so TX arms through the legacy
    ``start_audio_tx_pcm`` path and both bridge legs come up.
    """
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


def _make_bridge(backend: FakeAudioBackend) -> AudioBridge:
    return AudioBridge(
        _make_radio(),
        device_name="RigPlane Virtual Cable",
        backend=backend,
        portaudio_timeout=_TEST_TIMEOUT_S,
    )


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == _LOGGER_NAME and r.levelno >= logging.WARNING
    ]


class _Ticker:
    """Counts event-loop ticks while the PortAudio call under test is stuck."""

    def __init__(self) -> None:
        self.progressed = 0
        self._stop = asyncio.Event()

    async def run(self) -> None:
        while not self._stop.is_set():
            self.progressed += 1
            await asyncio.sleep(0.005)

    def start(self) -> asyncio.Task[None]:
        return asyncio.create_task(self.run())

    async def stop(self) -> None:
        self._stop.set()
        # Give the just-started task a chance to have ticked at least once
        # even on a loaded runner, so the ``progressed`` read is stable.
        await asyncio.sleep(0)


@pytest.mark.timeout(10)
async def test_enumeration_timeout_keeps_loop_free_and_fails_start(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stuck ``list_devices`` must fail the bridge start, not the server."""
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    gate = threading.Event()
    loop_ident = threading.get_ident()
    seen_idents: list[int] = []
    backend = FakeAudioBackend([_RP_DEVICE])
    bridge = _make_bridge(backend)

    def _blocked_list() -> list[AudioDeviceInfo]:
        seen_idents.append(threading.get_ident())
        gate.wait()
        return [_RP_DEVICE]

    monkeypatch.setattr(backend, "list_devices", _blocked_list)

    ticker = _Ticker()
    ticker_task = ticker.start()
    try:
        started = time.monotonic()
        with pytest.raises(AudioCaptureOpenTimeoutError):
            await bridge.start()
        elapsed = time.monotonic() - started
    finally:
        await ticker.stop()
        await ticker_task
        gate.set()  # release the stuck background listing
        await asyncio.sleep(0.05)

    assert ticker.progressed >= 3, (
        "event loop should keep making progress while enumeration is stuck"
    )
    assert elapsed < 0.5, "bridge start must fail within the bound (plus margin)"
    assert bridge.bridge_state == BridgeState.IDLE
    assert seen_idents and all(i != loop_ident for i in seen_idents), (
        "device enumeration must run on a pool worker, never the loop thread"
    )

    warnings = _warnings(caplog)
    assert len(warnings) == 1, "exactly one actionable warning on timeout"
    message = warnings[0].getMessage()
    assert "device enumeration" in message
    assert "BRIDGE" in message


@pytest.mark.timeout(10)
async def test_playback_start_timeout_keeps_loop_free_and_fails_start(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stuck RX-playback open must fail the bridge start, not the server."""
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    gate = threading.Event()
    backend = FakeAudioBackend([_RP_DEVICE])
    backend.block_tx_open = gate.wait  # blocks a WORKER thread, never the loop
    bridge = _make_bridge(backend)

    ticker = _Ticker()
    ticker_task = ticker.start()
    try:
        started = time.monotonic()
        with pytest.raises(AudioCaptureOpenTimeoutError):
            await bridge.start()
        elapsed = time.monotonic() - started
    finally:
        await ticker.stop()
        await ticker_task

    assert ticker.progressed >= 3, (
        "event loop should keep making progress while the open is stuck"
    )
    assert elapsed < 0.5, "bridge start must fail within the bound (plus margin)"
    assert bridge.bridge_state == BridgeState.IDLE
    assert len(backend.tx_streams) == 1, (
        "the playback leg must have been attempted before the TX leg"
    )

    warnings = _warnings(caplog)
    assert len(warnings) == 1, "exactly one actionable warning on timeout"
    assert "playback open" in warnings[0].getMessage()

    # Release LAST: the abandoned open's late-handle close logs its own
    # warning, which must land after the assertions above (mor1438 shape).
    gate.set()
    await asyncio.sleep(0.05)


@pytest.mark.timeout(10)
async def test_capture_start_timeout_keeps_loop_free_and_fails_start(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stuck TX-capture open must fail the bridge start, not the server."""
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    gate = threading.Event()
    backend = FakeAudioBackend([_RP_DEVICE])
    backend.block_rx_open = gate.wait  # blocks a WORKER thread, never the loop
    bridge = _make_bridge(backend)

    ticker = _Ticker()
    ticker_task = ticker.start()
    try:
        started = time.monotonic()
        with pytest.raises(AudioCaptureOpenTimeoutError):
            await bridge.start()
        elapsed = time.monotonic() - started
    finally:
        await ticker.stop()
        await ticker_task

    assert ticker.progressed >= 3, (
        "event loop should keep making progress while the open is stuck"
    )
    assert elapsed < 0.5, "bridge start must fail within the bound (plus margin)"
    assert bridge.bridge_state == BridgeState.IDLE

    warnings = _warnings(caplog)
    assert len(warnings) == 1, "exactly one actionable warning on timeout"
    assert "capture open" in warnings[0].getMessage()

    # Release LAST: the abandoned open's late-handle close logs its own
    # warning, which must land after the assertions above (mor1438 shape).
    gate.set()
    await asyncio.sleep(0.05)


@pytest.mark.timeout(10)
async def test_stop_timeout_keeps_loop_free_and_completes_teardown(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stuck ``stream.stop()`` must not wedge bridge teardown (or SIGTERM).

    Teardown cannot raise — it gives up on the stuck leg after the bound
    (one warning) while the healthy legs still stop cleanly.
    """
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    gate = threading.Event()
    backend = FakeAudioBackend([_RP_DEVICE])
    bridge = _make_bridge(backend)
    await bridge.start()
    assert bridge.bridge_state == BridgeState.RUNNING
    capture = backend.rx_streams[0]
    capture.block_stop = gate.wait  # blocks a WORKER thread, never the loop

    ticker = _Ticker()
    ticker_task = ticker.start()
    try:
        started = time.monotonic()
        await bridge.stop()
        elapsed = time.monotonic() - started
    finally:
        await ticker.stop()
        await ticker_task
        gate.set()  # release the stuck background stop
        await asyncio.sleep(0.05)

    assert ticker.progressed >= 3, (
        "event loop should keep making progress while the stop is stuck"
    )
    assert elapsed < 0.5, "bridge stop must give up within the bound (plus margin)"
    assert bridge.bridge_state == BridgeState.IDLE
    assert capture.stop_thread_ident != threading.get_ident(), (
        "the stream stop must run on a pool worker, never the loop thread"
    )
    assert backend.tx_streams[0].stopped_count == 1, (
        "the healthy playback leg must still stop after the stuck leg"
    )

    warnings = _warnings(caplog)
    assert len(warnings) == 1, "exactly one actionable warning on timeout"
    message = warnings[0].getMessage()
    assert "stream stop" in message
    assert "BRIDGE" in message
