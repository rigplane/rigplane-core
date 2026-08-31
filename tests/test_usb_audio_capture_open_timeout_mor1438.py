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
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Item 1: enough sequential wedged opens must fail the NEXT one fast.

    ``_rx_lock`` only serializes concurrent opens; it does not bound how
    many abandoned (still-running-in-background) opens pile up across
    SEQUENTIAL start_rx() calls. Each timed-out-and-abandoned open leaves
    its worker thread permanently blocked on ``gate.wait()``, so eight
    sequential stuck opens exhaust the whole driver-owned pool
    (``_CAPTURE_OPEN_MAX_WORKERS`` == 8). The NEXT open against a healthy
    device must recognize the pool is saturated and fail immediately with
    an honest "pool saturated" error — NOT queue behind the stuck workers
    and burn the full timeout while misreporting TCC consent as the cause.
    """
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    gate = threading.Event()
    driver, backend = _make_driver()
    backend.block_rx_open = gate.wait

    try:
        # Saturate every worker in the driver-owned pool with abandoned,
        # permanently-blocked opens (max_workers == 8, see usb_driver.py).
        for _ in range(8):
            with pytest.raises(AudioCaptureOpenTimeoutError):
                await driver.start_rx(lambda _frame: None)

        wedged = list(backend.rx_streams)

        with pytest.raises(AudioCaptureOpenTimeoutError) as exc_info:
            await driver.start_rx(lambda _frame: None)

        assert "saturated" in str(exc_info.value).lower(), (
            f"expected an honest pool-saturation message, got: {exc_info.value!r}"
        )

        # "Fails fast" is asserted as behaviour, not as a stopwatch reading.
        # The fail-fast path never reaches the pool: it closes the open
        # coroutine instead of submitting it, so the probe's handle is never
        # started -- whereas an open that QUEUED behind the wedged workers is
        # started as soon as one of them frees.
        #
        # Release the pool and wait for it to DRAIN -- every wedged open
        # completes and its abandoned handle is closed -- before reading the
        # probe. Draining is what gives the read its meaning: a queued probe
        # sits ahead of those closes in the same FIFO pool, so once all eight
        # closes have landed, a queued probe would necessarily have started.
        probe = backend.rx_streams[len(wedged)]
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
        assert probe.started_count == 0, (
            "saturated pool must fail WITHOUT handing the open to a worker; "
            "this handle was started, so the open queued behind the wedged "
            "workers instead of failing fast"
        )
    finally:
        gate.set()  # release every stuck worker so the pool can drain
        await asyncio.sleep(0.2)


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
