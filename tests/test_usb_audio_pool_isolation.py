"""Cross-test isolation of the shared bounded PortAudio pool.

``usb_driver.bounded_portaudio_pool`` is a module singleton shared by every
``UsbAudioDriver`` and ``AudioBridge`` in the process. A test whose wedged
stream stop times out leaves the pool's ``_cleanup_streams`` retaining the
stream and ``_stops`` holding a future bound to that test's event loop, which
pytest-asyncio (function-scoped loops) closes at teardown; the abandoned
worker then can never settle the future. The next test whose fake devices
reuse the same ids makes ``open_stream_bounded`` stop the retained stream
first, and ``_submit_stop`` returns the dead pending future, so the victim
waits the full ``capture_open_timeout`` for nothing — the
``stream stop timed out`` cascade. These two tests reproduce that cascade
inside one module: the first leaves the retained stop exactly the way a
wedged-stop test does, the second performs an ordinary driver start/stop and
must pass within a short bound. The autouse pool-reset fixture in
``tests/conftest.py`` is what keeps the second test green.
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from rigplane.audio import usb_driver
from rigplane.audio.backend import AudioDeviceId, AudioDeviceInfo, FakeAudioBackend
from rigplane.audio.usb_driver import AudioCaptureOpenTimeoutError, UsbAudioDriver

# Shared between the two tests below, which must run in definition order.
# The wedged worker waits on this gate with a 30 s backstop so a test run
# that skips the second test cannot hang the process at interpreter exit.
_RELEASE_GATE = threading.Event()


def _make_driver(
    capture_open_timeout: float,
) -> tuple[UsbAudioDriver, FakeAudioBackend]:
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
    driver = UsbAudioDriver(backend=backend, capture_open_timeout=capture_open_timeout)
    return driver, backend


@pytest.mark.timeout(10)
async def test_retained_timed_out_stop_survives_in_shared_pool() -> None:
    """Polluter: a wedged stop times out and the pool retains it (contract).

    Leaves exactly the state a wedged-stop test leaves when its worker
    thread outlives the test's event loop: the stream retained in
    ``_cleanup_streams`` and a pending stop future in ``_stops``.
    """
    pool = usb_driver.bounded_portaudio_pool
    driver, backend = _make_driver(capture_open_timeout=0.05)
    await driver.start_rx(lambda _frame: None)
    stream = backend.rx_streams[-1]
    stream.block_stop = lambda: _RELEASE_GATE.wait(30)
    with pytest.raises(AudioCaptureOpenTimeoutError, match="stream stop timed out"):
        await driver.stop_rx()
    key = id(stream)
    assert key in pool._cleanup_streams
    assert key in pool._stops
    assert not pool._stops[key].done()
    assert pool.inflight == 1


@pytest.mark.timeout(10)
async def test_ordinary_driver_start_stop_after_pool_pollution() -> None:
    """Victim: an ordinary start/stop must not inherit the retained stop.

    Without the conftest pool reset, ``open_stream_bounded`` stops the
    retained stream first; its recorded stop future is bound to the previous
    test's closed loop and can never settle, so the start fails with
    ``AudioCaptureOpenTimeoutError`` only after the full
    ``capture_open_timeout`` — the cascade this regression pins.
    """
    try:
        driver, _backend = _make_driver(capture_open_timeout=0.2)
        async with asyncio.timeout(2):
            await driver.start_rx(lambda _frame: None)
            assert driver.rx_running
            await driver.stop_rx()
        assert not driver.rx_running
    finally:
        _RELEASE_GATE.set()  # release the previous test's wedged worker
