"""MOR-1438 — RX/TX capture open must never block the event loop.

Bench incident (2026-08-11, reproduced twice): the first audio subscriber
triggers ``UsbAudioDriver.start_rx``, which opened the CoreAudio capture
stream directly on the event-loop thread. A macOS TCC microphone-consent
prompt that never rendered left that open blocked forever, freezing
web/WS/scope -- the ENTIRE server -- for minutes (see MOR-1420 for the TCC
mic-consent freeze this class of bug traces back to).

``UsbAudioDriver.start_rx``/``start_tx`` now drive the stream's ``start()``
off the event loop (a dedicated worker thread pool) and bound the wait with
a configurable capture-open timeout. On timeout: exactly one actionable
WARNING is logged, the exception (``AudioCaptureOpenTimeoutError``)
propagates through the EXISTING AudioBus/web failure path (MOR-582, ADR
Sec3.4) instead of a bespoke availability flag, and a late-arriving handle
(the background open eventually completing after it was abandoned) is
closed rather than leaked.

Independent-review follow-ups (same ticket):

- F1: cancelling the *caller* mid-open (e.g. a WS session torn down while
  the open is in flight) must abandon the background open the SAME way a
  timeout does -- not leave it to flip ``running`` True with no consumer.
- F2: closing a late/abandoned handle must ALSO run off the event loop --
  a wedged device can block its ``stop()`` exactly as it blocked its
  ``start()``.
- F3: the open/close work runs on a driver-owned thread pool, not the
  process-wide default executor shared with unrelated subsystems.
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
# Recovery-only bound (test_rx_open_recovers_on_next_subscriber, MOR-2743):
# the file's point is that the retry SUCCEEDS after a bounded timeout, so
# its bound only needs to stay bounded -- 0.05s was tight enough that a
# loaded CI runner could not schedule the worker thread in time.
_RECOVERY_TIMEOUT_S = 1.0


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
    capture_open_timeout: float = _TEST_TIMEOUT_S,
) -> tuple[UsbAudioDriver, FakeAudioBackend]:
    backend = backend or FakeAudioBackend(_fake_devices())
    driver = UsbAudioDriver(backend=backend, capture_open_timeout=capture_open_timeout)
    return driver, backend


def _warnings(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [
        r
        for r in caplog.records
        if r.name == _LOGGER_NAME and r.levelno >= logging.WARNING
    ]


@pytest.fixture(autouse=True)
def _no_coreaudio_uid_map(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the fake-backend paths off real CoreAudio (macOS test hosts).

    Since MOR-2892 the start path's device enumeration runs inside the
    same bounded (here 0.05 s) window as the open; on a macOS test host
    ``_get_uid_map()`` is a real CoreAudio mach call whose jitter would
    flake these timeout assertions. Neutralize it.
    """
    monkeypatch.setattr("rigplane.audio.usb_driver._get_uid_map", lambda: {})


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
    driver, backend = _make_driver(capture_open_timeout=_RECOVERY_TIMEOUT_S)
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TX opens use the same off-loop + bounded-timeout treatment as RX."""
    # block_tx_open steers the plain OutputStream open; pin the "full"
    # two-stream policy so a same-device fake does not take the exclusive
    # duplex arm (MOR-546) on macOS, where block_duplex_open applies instead.
    monkeypatch.setattr(
        "rigplane.audio.usb_driver.resolve_usb_duplex_mode",
        lambda _rx, _tx: "full",
    )
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


@pytest.mark.timeout(10)
async def test_rx_open_cancelled_mid_open_closes_late_handle_and_recovers() -> None:
    """F1: cancelling start_rx mid-open must not orphan a live stream.

    Before this fix, only the timeout branch abandoned the background
    open. A caller cancellation (e.g. a WS session torn down while the
    open is in flight — the actual incident behavior: an operator
    reloading the tab mid-freeze) propagated CancelledError past the
    ``except AudioCaptureOpenTimeoutError`` clause untouched: no
    done-callback attached, no ``self._rx_stream`` reset. The abandoned
    open would eventually flip ``running`` True with no consumer, every
    later subscriber would trip ``AudioAlreadyStartedError``, and the bus
    could never self-heal (``rx_active`` never got set, so
    ``_remove_subscriber`` never fires the stop that would recover) --
    RX dead until process restart.
    """
    gate = threading.Event()
    driver, backend = _make_driver()
    backend.block_rx_open = gate.wait

    task = asyncio.create_task(driver.start_rx(lambda _frame: None))
    await asyncio.sleep(0.01)  # let it reach the blocked open
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert driver.rx_running is False

    late_stream = backend.rx_streams[-1]
    gate.set()  # release the background open so it can finally complete
    for _ in range(50):
        await asyncio.sleep(0.01)
        if late_stream.stopped_count:
            break

    assert late_stream.started_count == 1
    assert late_stream.stopped_count == 1, (
        "cancelled-mid-open handle must be closed, not leaked"
    )

    # The bus-level self-heal check: a fresh subscriber must be able to
    # open RX again, not trip AudioAlreadyStartedError forever.
    backend.block_rx_open = None
    await driver.start_rx(lambda _frame: None)
    assert driver.rx_running is True


@pytest.mark.timeout(10)
async def test_late_close_runs_off_the_loop_thread() -> None:
    """F2: closing an abandoned handle must not block the loop either.

    A real ``stream.stop()`` is the same kind of synchronous
    Pa_StopStream/Pa_CloseStream call as ``start()`` -- a wedged device
    can block its close exactly as it blocked its open. Asserts the
    close actually executes on a DIFFERENT thread than the one running
    this test's event loop (a stronger, more direct proof than timing
    heuristics).
    """
    gate = threading.Event()
    driver, backend = _make_driver()
    backend.block_rx_open = gate.wait
    loop_thread_ident = threading.get_ident()

    with pytest.raises(AudioCaptureOpenTimeoutError):
        await driver.start_rx(lambda _frame: None)

    late_stream = backend.rx_streams[-1]
    gate.set()
    for _ in range(50):
        await asyncio.sleep(0.01)
        if late_stream.stopped_count:
            break

    assert late_stream.stopped_count == 1
    assert late_stream.stop_thread_ident is not None
    assert late_stream.stop_thread_ident != loop_thread_ident, (
        "late close must run off the event-loop thread"
    )


@pytest.mark.timeout(10)
async def test_frame_delivered_after_off_loop_open() -> None:
    """Loop-affinity regression guard (coverage gap closed per review).

    The central risk this whole ticket introduces is a loop-affinity bug:
    frames delivered by a stream that was opened off-loop must still
    reach the original caller's callback. A regression here would mean
    RX silently stops delivering audio even though ``start_rx()`` reports
    success.
    """
    driver, backend = _make_driver()
    received: list[bytes] = []

    await driver.start_rx(received.append)
    assert driver.rx_running is True

    backend.rx_streams[-1].inject_frame(b"\x01\x02")
    assert received == [b"\x01\x02"]


# ---------------------------------------------------------------------------
# MOR-1573 — independent-review follow-ups on top of MOR-1438
# ---------------------------------------------------------------------------


@pytest.mark.timeout(10)
async def test_timeout_warning_reports_sub_second_timeout_with_one_decimal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Item 3: a sub-second timeout must not render as the misleading "0s".

    ``_TEST_TIMEOUT_S`` (0.05s) previously formatted via ``%.0f`` as "0s",
    which reads as "no timeout configured" rather than the true 50ms bound.
    """
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    gate = threading.Event()
    driver, backend = _make_driver()
    backend.block_rx_open = gate.wait

    try:
        with pytest.raises(AudioCaptureOpenTimeoutError):
            await driver.start_rx(lambda _frame: None)

        warnings = _warnings(caplog)
        assert len(warnings) == 1
        message = warnings[0].getMessage()
        assert "0.1s" in message, (
            f"expected one-decimal timeout in message: {message!r}"
        )
        assert "0s" not in message.replace("0.1s", ""), (
            f"stale zero-second rendering leaked into message: {message!r}"
        )
    finally:
        # MUST run even if an assertion above fails: the background open is
        # blocked on a non-daemon worker thread that would otherwise wedge
        # process exit forever (the exact failure mode this ticket exists
        # to prevent in production).
        gate.set()
        await asyncio.sleep(0.05)


@pytest.mark.timeout(15)
async def test_pool_saturation_fails_fast_instead_of_queuing(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    from rigplane.audio import usb_driver

    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    gate = threading.Event()
    pool = usb_driver._BoundedPortAudioPool()
    monkeypatch.setattr(usb_driver, "bounded_portaudio_pool", pool)
    capacity = usb_driver._CAPTURE_OPEN_MAX_WORKERS
    backend = FakeAudioBackend(
        [
            AudioDeviceInfo(
                id=AudioDeviceId(index),
                name=f"USB Audio CODEC {index}",
                input_channels=1,
                output_channels=1,
                default_samplerate=48_000,
            )
            for index in range(capacity + 1)
        ]
    )
    drivers = [
        UsbAudioDriver(
            backend=backend,
            rx_device=f"USB Audio CODEC {index}",
            tx_device=f"USB Audio CODEC {index}",
            capture_open_timeout=_TEST_TIMEOUT_S,
        )
        for index in range(capacity + 1)
    ]
    backend.block_rx_open = gate.wait

    try:
        for driver in drivers[:-1]:
            with pytest.raises(AudioCaptureOpenTimeoutError):
                await driver.start_rx(lambda _frame: None)

        wedged = list(backend.rx_streams)
        assert len(wedged) == capacity

        with pytest.raises(AudioCaptureOpenTimeoutError) as exc_info:
            await drivers[-1].start_rx(lambda _frame: None)

        assert "saturated" in str(exc_info.value).lower(), (
            f"expected an honest pool-saturation message, got: {exc_info.value!r}"
        )

        # "Fails fast" is asserted as behaviour, not as a stopwatch reading.
        # The fail-fast path never reaches the pool: no work item is
        # submitted, so the start never progresses to creating a stream
        # handle -- whereas an open that QUEUED behind the wedged workers
        # creates (and starts) one as soon as a worker frees.
        #
        # Release the pool and wait for it to DRAIN -- every wedged open
        # completes and its abandoned handle is closed -- before reading
        # the stream list. Draining is what gives the read its meaning: a
        # queued start sits ahead of those closes in the same FIFO pool,
        # so once all eight closes have landed, a queued start would
        # necessarily have created its handle.
        #
        # MOR-2892 note: the start path now submits device enumeration and
        # format-probe calls to the same driver-owned pool before the open,
        # so a saturated pool fails the next start at its FIRST submission
        # -- no new stream handle is created at all. Before MOR-2892 this
        # asserted ``probe.started_count == 0`` on the freshly created (but
        # never started) handle; the stronger invariant now is that no
        # additional handle exists.
        gate.set()
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 10.0
        while loop.time() < deadline:
            if all(s.stopped_count for s in wedged):
                break
            await asyncio.sleep(0.005)

        assert all(s.stopped_count for s in wedged), (
            "pool never drained -- the wedged opens' handles were not closed, "
            "so nothing can be concluded about the probe open"
        )
        assert backend.rx_streams[len(wedged) :] == [], (
            "saturated pool must fail WITHOUT handing anything to a worker; "
            "an extra stream handle was created, so the start queued behind "
            "the wedged workers instead of failing fast"
        )
    finally:
        gate.set()  # release every stuck worker so the pool can drain
        async with asyncio.timeout(5):
            while pool.inflight:
                await asyncio.sleep(0.005)
        pool._executor.shutdown(wait=True)


@pytest.mark.timeout(10)
async def test_abandoned_open_that_later_fails_is_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Item 2: a late-resolving abandoned open must log its exception.

    Before this fix, ``_close_late_stream`` silently swallowed a late
    background open that finished with an EXCEPTION (as opposed to a
    successful-but-late open, or a cancellation) -- zero trace of the
    failure ever reached the logs.
    """
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    gate = threading.Event()
    failure = RuntimeError("late device init failure")

    def _block_then_fail() -> None:
        gate.wait()
        raise failure

    driver, backend = _make_driver()
    backend.block_rx_open = _block_then_fail

    try:
        with pytest.raises(AudioCaptureOpenTimeoutError):
            await driver.start_rx(lambda _frame: None)
    finally:
        # MUST run even if the assertion above fails: releases the
        # non-daemon worker thread blocked in _block_then_fail so it
        # cannot wedge process exit.
        gate.set()

    for _ in range(50):
        await asyncio.sleep(0.01)
        warnings = _warnings(caplog)
        if any("late device init failure" in r.getMessage() for r in warnings):
            break

    warnings = _warnings(caplog)
    matches = [r for r in warnings if "late device init failure" in r.getMessage()]
    assert matches, (
        f"expected a WARNING logging the late open's exception, got: "
        f"{[r.getMessage() for r in warnings]}"
    )
    assert matches[0].exc_info is not None, "exception traceback should be attached"


@pytest.mark.timeout(10)
async def test_duplex_open_routes_off_loop_and_times_out(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Item 4: start_duplex must get the SAME off-loop bounded-open treatment.

    Before this fix, ``start_duplex`` awaited ``DuplexStream.start()``
    directly instead of through :meth:`UsbAudioDriver._open_stream`, so a
    stuck duplex open would freeze the event loop exactly like the
    pre-MOR-1438 RX/TX opens did.
    """
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    gate = threading.Event()
    driver, backend = _make_driver()
    # Bounded even in the not-yet-fixed case: a direct (on-loop) call would
    # otherwise block this test's entire event loop for as long as the gate
    # stays unset. Once routed off-loop, the driver's own capture-open
    # timeout (0.05s) fires long before this 1s bound is reached.
    backend.block_duplex_open = lambda: gate.wait(timeout=1.0)

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
            await driver.start_duplex(lambda _frame: None)
    finally:
        stop.set()
        await ticker
        gate.set()
        await asyncio.sleep(0.05)

    assert progressed >= 3, (
        "event loop should keep making progress while the duplex open is stuck"
    )
    assert driver.rx_running is False
    assert driver.tx_running is False
