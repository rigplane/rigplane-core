"""MOR-1438 — RX/TX capture open must never block the event loop.

Bench incident (2026-08-11, reproduced twice): the first audio subscriber
triggers ``UsbAudioDriver.start_rx``, which opened the CoreAudio capture
stream directly on the event-loop thread. A macOS TCC microphone-consent
prompt that never rendered left that open blocked forever, freezing
web/WS/scope -- the ENTIRE server -- for minutes (see MOR-1420 for the TCC
mic-consent freeze this class of bug traces back to).

``UsbAudioDriver.start_rx``/``start_tx`` now drive the stream's ``start()``
off the event loop (a worker thread) and bound the wait with a configurable
capture-open timeout. On timeout: exactly one actionable WARNING is logged,
the exception (``AudioCaptureOpenTimeoutError``) propagates through the
EXISTING AudioBus/web failure path (MOR-582, ADR Sec3.4) instead of a
bespoke availability flag, and a late-arriving handle (the background open
eventually completing after the timeout fired) is closed rather than
leaked.
"""

from __future__ import annotations

import asyncio
import logging
import threading

import pytest

from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.audio.usb_driver import AudioCaptureOpenTimeoutError, UsbAudioDriver

_LOGGER_NAME = "rigplane.audio.usb_driver"
# Tiny relative to the production default -- keeps this suite fast while
# still exercising the real asyncio.wait()-based bound.
_TEST_TIMEOUT_S = 0.05


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


def _make_driver(
    backend: FakeAudioBackend | None = None,
) -> tuple[UsbAudioDriver, FakeAudioBackend]:
    backend = backend or FakeAudioBackend(_fake_devices())
    driver = UsbAudioDriver(backend=backend, capture_open_timeout=_TEST_TIMEOUT_S)
    return driver, backend


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == _LOGGER_NAME and r.levelno == logging.WARNING
    ]


@pytest.mark.timeout(10)
async def test_rx_open_timeout_keeps_event_loop_free(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A stuck RX open must not stall other coroutines on the same loop."""
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    gate = threading.Event()
    driver, backend = _make_driver()
    backend.block_rx_open = gate.wait  # blocks a WORKER thread, never the loop

    progressed = 0
    stop = asyncio.Event()

    async def _ticker() -> None:
        nonlocal progressed
        while not stop.is_set():
            progressed += 1
            await asyncio.sleep(0.005)

    ticker = asyncio.create_task(_ticker())
    try:
        with pytest.raises(AudioCaptureOpenTimeoutError):
            await driver.start_rx(lambda _frame: None)
    finally:
        stop.set()
        await ticker

    assert progressed >= 3, (
        "event loop should keep making progress while the open is stuck"
    )
    assert driver.rx_running is False

    warnings = _warnings(caplog)
    assert len(warnings) == 1, "exactly one actionable warning on timeout"
    message = warnings[0].getMessage()
    assert "MOR-1420" in message
    assert "consent" in message.lower()

    gate.set()  # release the stuck background open so the thread can exit
    await asyncio.sleep(0.05)


@pytest.mark.timeout(10)
async def test_rx_open_late_return_closes_handle_not_leaked(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A background open that finishes AFTER the timeout must be closed."""
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    gate = threading.Event()
    driver, backend = _make_driver()
    backend.block_rx_open = gate.wait

    with pytest.raises(AudioCaptureOpenTimeoutError):
        await driver.start_rx(lambda _frame: None)

    late_stream = backend.rx_streams[-1]
    assert late_stream.stopped_count == 0

    gate.set()  # let the abandoned background open finally complete
    for _ in range(50):
        await asyncio.sleep(0.01)
        if late_stream.stopped_count:
            break

    assert late_stream.started_count == 1
    assert late_stream.stopped_count == 1, "late handle must be closed, not leaked"


@pytest.mark.timeout(10)
async def test_rx_open_recovers_on_next_subscriber(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A later subscriber (e.g. consent granted) must be able to open RX again."""
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    gate = threading.Event()
    driver, backend = _make_driver()
    backend.block_rx_open = gate.wait

    with pytest.raises(AudioCaptureOpenTimeoutError):
        await driver.start_rx(lambda _frame: None)
    assert driver.rx_running is False

    gate.set()
    await asyncio.sleep(0.05)  # let the abandoned attempt's cleanup settle

    backend.block_rx_open = None  # simulate consent granted for the retry
    await driver.start_rx(lambda _frame: None)
    assert driver.rx_running is True


@pytest.mark.timeout(10)
async def test_normal_rx_open_timing_unaffected() -> None:
    """A well-behaved (non-blocking) open must not pay a meaningful penalty."""
    driver, _backend = _make_driver()
    loop = asyncio.get_event_loop()
    start = loop.time()
    await driver.start_rx(lambda _frame: None)
    elapsed = loop.time() - start

    assert driver.rx_running is True
    assert elapsed < 0.5


@pytest.mark.timeout(10)
async def test_tx_open_timeout_shares_the_same_treatment(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """TX opens use the same off-loop + bounded-timeout treatment as RX."""
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    gate = threading.Event()
    driver, backend = _make_driver()
    backend.block_tx_open = gate.wait

    with pytest.raises(AudioCaptureOpenTimeoutError):
        await driver.start_tx()

    assert driver.tx_running is False
    assert len(_warnings(caplog)) == 1

    gate.set()
    await asyncio.sleep(0.05)


@pytest.mark.timeout(10)
async def test_normal_tx_open_timing_unaffected() -> None:
    driver, _backend = _make_driver()
    loop = asyncio.get_event_loop()
    start = loop.time()
    await driver.start_tx()
    elapsed = loop.time() - start

    assert driver.tx_running is True
    assert elapsed < 0.5
