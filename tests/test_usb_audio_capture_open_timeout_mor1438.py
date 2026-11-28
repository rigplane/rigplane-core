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
from rigplane.audio.usb_driver import (
    AudioCaptureOpenTimeoutError,
    UsbAudioDriver,
    bounded_portaudio_pool,
)
from rigplane.testing.timing import VirtualClock, await_until, virtual_clock

from _portaudio_pool_helpers import drain_pool

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


async def _settle_shared_pool() -> None:
    """Wait until the module-singleton pool has settled every tracked operation.

    Virtual-clock replacement for :func:`drain_pool` (MOR-4075): ``drain_pool``
    parks on ``asyncio.sleep``, a virtual timer nobody advances here, so it
    would hang to the watchdog. Pool completions arrive from real worker
    threads, so this waits on the real event with ``await_until`` instead of
    advancing the clock. The ``inflight``-zero guarantee is the same one
    ``drain_pool`` documents (the uncount callback is registered first).
    """
    await await_until(
        lambda: bounded_portaudio_pool.inflight == 0,
        timeout=5.0,
        description="PortAudio pool to drain",
    )


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
@virtual_clock(inline_threads=False)
async def test_rx_open_timeout_keeps_event_loop_free(
    clock: VirtualClock, caplog: pytest.LogCaptureFixture
) -> None:
    """A stuck RX open must not stall other coroutines on the same loop.

    Virtual-clock (MOR-4075): the ticker parks on virtual timers fired by
    ``clock.elapse`` instead of wall-clock sleeps, and the capture-open bound
    fires when the clock passes it. The worker thread stays blocked on
    ``gate`` in real time throughout; ``inline_threads=False`` keeps it that
    way (a blocking gate inlined onto the loop thread would wedge the loop).
    """
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    gate = threading.Event()
    driver, backend = _make_driver()
    backend.block_rx_open = gate.wait  # blocks a WORKER thread, never the loop

    progressed = 0

    async def _ticker() -> None:
        nonlocal progressed
        while True:
            await asyncio.sleep(0.005)  # virtual timer, fired by clock.elapse
            progressed += 1

    open_task = asyncio.create_task(driver.start_rx(lambda _frame: None))
    ticker = asyncio.create_task(_ticker())
    timeout_warnings: list[logging.LogRecord] = []
    try:
        # Real event: the open reached the pool worker. ``open_rx`` runs on
        # the loop thread in the same yield-free stretch as the bounded wait,
        # so an armed timeout is guaranteed once the stream exists.
        await await_until(
            lambda: len(backend.rx_streams) >= 1,
            timeout=5.0,
            description="background open to arm",
        )
        await clock.elapse(0.03)
        assert progressed >= 3, (
            "event loop should keep making progress while the open is stuck"
        )
        await clock.elapse(_TEST_TIMEOUT_S)
        with pytest.raises(AudioCaptureOpenTimeoutError):
            await open_task
        # Snapshot while the worker is still blocked on ``gate``: releasing
        # it lets the abandoned open complete, and the late-handle close
        # logs a SECOND warning (usb_driver.abandon_open) during the settle
        # below -- reading caplog after that would see both (review round 2).
        timeout_warnings = list(_warnings(caplog))
    finally:
        if not ticker.done():
            ticker.cancel()
        if not open_task.done():
            open_task.cancel()
        gate.set()  # release the stuck background open so the thread can exit
        await asyncio.gather(open_task, ticker, return_exceptions=True)
        await _settle_shared_pool()

    assert driver.rx_running is False

    assert len(timeout_warnings) == 1, "exactly one actionable warning on timeout"
    message = timeout_warnings[0].getMessage()
    assert "MOR-1420" in message
    assert "consent" in message.lower()


@pytest.mark.timeout(10)
@virtual_clock(inline_threads=False)
async def test_rx_open_late_return_closes_handle_not_leaked(
    clock: VirtualClock, caplog: pytest.LogCaptureFixture
) -> None:
    """A background open that finishes AFTER the timeout must be closed.

    Virtual-clock (MOR-4075): the bound fires on ``clock.elapse``; the late
    handle's arrival and its off-loop close are real worker-thread events
    awaited with ``await_until``.
    """
    caplog.set_level(logging.WARNING, logger=_LOGGER_NAME)
    gate = threading.Event()
    driver, backend = _make_driver()
    backend.block_rx_open = gate.wait

    open_task = asyncio.create_task(driver.start_rx(lambda _frame: None))
    try:
        # Real event: the open reached the pool worker (same yield-free
        # stretch argument as the loop-free test above).
        await await_until(
            lambda: len(backend.rx_streams) >= 1,
            timeout=5.0,
            description="background open to arm",
        )
        await clock.elapse(_TEST_TIMEOUT_S + 0.05)
        with pytest.raises(AudioCaptureOpenTimeoutError):
            await open_task
    finally:
        if not open_task.done():
            open_task.cancel()
        await asyncio.gather(open_task, return_exceptions=True)

    late_stream = backend.rx_streams[-1]
    assert late_stream.stopped_count == 0

    gate.set()  # let the abandoned background open finally complete
    await await_until(
        lambda: late_stream.stopped_count,
        timeout=5.0,
        description="late handle to be closed",
    )

    assert late_stream.started_count == 1
    assert late_stream.stopped_count == 1, "late handle must be closed, not leaked"
    await _settle_shared_pool()


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
    await drain_pool()  # let the abandoned attempt's cleanup settle

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
    await drain_pool()


@pytest.mark.timeout(10)
async def test_normal_tx_open_timing_unaffected() -> None:
    driver, _backend = _make_driver()
    loop = asyncio.get_event_loop()
    start = loop.time()
    await driver.start_tx()
    elapsed = loop.time() - start

    assert driver.tx_running is True
    assert elapsed < 0.5


# 60 s, not the file-wide 10 s: the sequential inner bounds (5 s
# entered-wait + 5 s late-close), drain_pool, and the retry's own driver
# bound can legitimately sum past 10 s in a slow-but-passing run; the
# watchdog must not kill those (review round 3). Every inner wait is
# still bounded, so a genuine hang still fails fast via those.
@pytest.mark.timeout(60)
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
    entered = threading.Event()

    def _block_in_open() -> None:
        # Set by the worker thread itself once it is INSIDE the blocked
        # device open: cancelling only after this fires is what makes this
        # a mid-open cancellation. The old fixed 0.01 s sleep let the
        # cancel land before open_rx() ran on a loaded runner, leaving
        # backend.rx_streams empty (MOR-4214 IndexError flake).
        entered.set()
        gate.wait()

    # A 10 s driver bound, not the 0.05 s default: it must clear the
    # entered-wait bound (5 s) plus cancel delivery. If the pool stalled
    # the worker past the driver timeout, start_rx would time out before
    # the cancel lands and the test would see AudioCaptureOpenTimeoutError
    # instead of CancelledError (review round 2). Nothing here asserts the
    # timeout path -- the sibling tests cover it at 0.05 s.
    driver, backend = _make_driver(capture_open_timeout=10.0)
    backend.block_rx_open = _block_in_open

    task = asyncio.create_task(driver.start_rx(lambda _frame: None))
    try:
        assert await await_until(
            entered.is_set, timeout=5.0, description="background open to block"
        )
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert driver.rx_running is False

        late_stream = backend.rx_streams[-1]
    finally:
        # Always release the worker, even when an assert above fails: it
        # blocks on a non-daemon pool thread that would otherwise wedge
        # process exit (review round 1). Settle the start task too so a
        # failure here never leaves its exception unretrieved.
        gate.set()
        if not task.done():
            task.cancel()
        try:
            await task
        except (asyncio.CancelledError, AudioCaptureOpenTimeoutError):
            pass
    # The released background open now completes; its late handle is closed.
    assert await await_until(
        lambda: late_stream.stopped_count,
        timeout=5.0,
        description="late handle to be closed",
    )

    assert late_stream.started_count == 1
    assert late_stream.stopped_count == 1, (
        "cancelled-mid-open handle must be closed, not leaked"
    )

    # Let the pool settle the late close before retrying, so the retry
    # cannot exhaust its own bound waiting on the same stop (review
    # round 1). The bus-level self-heal check: a fresh subscriber must be
    # able to open RX again, not trip AudioAlreadyStartedError forever.
    await drain_pool()
    backend.block_rx_open = None
    await driver.start_rx(lambda _frame: None)
    assert driver.rx_running is True


@pytest.mark.timeout(10)
@virtual_clock(inline_threads=False)
async def test_late_close_runs_off_the_loop_thread(clock: VirtualClock) -> None:
    """F2: closing an abandoned handle must not block the loop either.

    A real ``stream.stop()`` is the same kind of synchronous
    Pa_StopStream/Pa_CloseStream call as ``start()`` -- a wedged device
    can block its close exactly as it blocked its open. Asserts the
    close actually executes on a DIFFERENT thread than the one running
    this test's event loop (a stronger, more direct proof than timing
    heuristics).

    Virtual-clock (MOR-4075): the bound fires on ``clock.elapse``; the late
    handle's close is a real worker-thread event awaited with ``await_until``.
    """
    gate = threading.Event()
    driver, backend = _make_driver()
    backend.block_rx_open = gate.wait
    loop_thread_ident = threading.get_ident()

    open_task = asyncio.create_task(driver.start_rx(lambda _frame: None))
    try:
        # Real event: the open reached the pool worker (same yield-free
        # stretch argument as the loop-free test above).
        await await_until(
            lambda: len(backend.rx_streams) >= 1,
            timeout=5.0,
            description="background open to arm",
        )
        await clock.elapse(_TEST_TIMEOUT_S + 0.05)
        with pytest.raises(AudioCaptureOpenTimeoutError):
            await open_task
    finally:
        if not open_task.done():
            open_task.cancel()
        await asyncio.gather(open_task, return_exceptions=True)

    late_stream = backend.rx_streams[-1]
    gate.set()
    await await_until(
        lambda: late_stream.stopped_count,
        timeout=5.0,
        description="late handle to be closed",
    )

    assert late_stream.stopped_count == 1
    assert late_stream.stop_thread_ident is not None
    assert late_stream.stop_thread_ident != loop_thread_ident, (
        "late close must run off the event-loop thread"
    )
    await _settle_shared_pool()


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
        await drain_pool()


@pytest.mark.timeout(15)
@virtual_clock(inline_threads=False)
async def test_pool_saturation_fails_fast_instead_of_queuing(
    clock: VirtualClock,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
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
        for position, driver in enumerate(drivers[:-1], start=1):
            open_task = asyncio.create_task(driver.start_rx(lambda _frame: None))
            # Real event: this driver's open reached a pool worker. Each
            # driver opens a distinct device, so ``open_rx`` runs on the loop
            # thread in the same yield-free stretch as that open's bounded
            # wait -- an armed timeout is guaranteed once the stream exists.
            await await_until(
                lambda: len(backend.rx_streams) == position,
                timeout=5.0,
                description="wedged open to arm",
            )
            await clock.elapse(_TEST_TIMEOUT_S + 0.05)
            with pytest.raises(AudioCaptureOpenTimeoutError):
                await open_task

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
        # Real event: every wedged worker finished its open and the
        # abandoned-handle closes landed (no virtual timer is involved in
        # the close path, so the clock stays put).
        await await_until(
            lambda: all(s.stopped_count for s in wedged),
            timeout=10.0,
            description="wedged opens to close their late handles",
        )

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
        await await_until(
            lambda: pool.inflight == 0,
            timeout=5.0,
            description="pool to drain",
        )
        pool._executor.shutdown(wait=True)


@pytest.mark.timeout(10)
@virtual_clock(inline_threads=False)
async def test_abandoned_open_that_later_fails_is_logged(
    clock: VirtualClock, caplog: pytest.LogCaptureFixture
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

    open_task = asyncio.create_task(driver.start_rx(lambda _frame: None))
    try:
        # Real event: the open reached the pool worker (same yield-free
        # stretch argument as the loop-free test above). The bound itself
        # fires on ``clock.elapse``.
        await await_until(
            lambda: len(backend.rx_streams) >= 1,
            timeout=5.0,
            description="background open to arm",
        )
        await clock.elapse(_TEST_TIMEOUT_S + 0.05)
        with pytest.raises(AudioCaptureOpenTimeoutError):
            await open_task
    finally:
        if not open_task.done():
            open_task.cancel()
        await asyncio.gather(open_task, return_exceptions=True)
        # MUST run even if an assertion above fails: releases the
        # non-daemon worker thread blocked in _block_then_fail so it
        # cannot wedge process exit.
        gate.set()

    # Real event: the abandoned open failed on its worker and the loop-side
    # done-callback logged it.
    await await_until(
        lambda: any(
            "late device init failure" in r.getMessage() for r in _warnings(caplog)
        ),
        timeout=5.0,
        description="late open failure to be logged",
    )

    warnings = _warnings(caplog)
    matches = [r for r in warnings if "late device init failure" in r.getMessage()]
    assert matches, (
        f"expected a WARNING logging the late open's exception, got: "
        f"{[r.getMessage() for r in warnings]}"
    )
    assert matches[0].exc_info is not None, "exception traceback should be attached"
    await _settle_shared_pool()


@pytest.mark.timeout(10)
@virtual_clock(inline_threads=False)
async def test_duplex_open_routes_off_loop_and_times_out(
    clock: VirtualClock, caplog: pytest.LogCaptureFixture
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
    # Blocked until the explicit ``gate.set()`` in ``finally``: no thread
    # timeout literal here -- the realtime guard (MOR-4075) flags those and
    # the virtual clock does not advance them, so a 1 s self-release would
    # race the driver's 0.05 s virtual bound. Backstop against a regressed
    # on-loop open (which would wedge the loop with the gate unset) is the
    # file-wide 10 s pytest timeout, not a real-time wait.
    backend.block_duplex_open = gate.wait

    progressed = 0

    async def _ticker() -> None:
        nonlocal progressed
        while True:
            await asyncio.sleep(0.005)  # virtual timer, fired by clock.elapse
            progressed += 1

    open_task = asyncio.create_task(driver.start_duplex(lambda _frame: None))
    ticker = asyncio.create_task(_ticker())
    try:
        # Real event: the duplex open reached the pool worker (same
        # yield-free stretch argument as the RX loop-free test above).
        await await_until(
            lambda: len(backend.duplex_streams) >= 1,
            timeout=5.0,
            description="background duplex open to arm",
        )
        await clock.elapse(0.03)
        assert progressed >= 3, (
            "event loop should keep making progress while the duplex open is stuck"
        )
        await clock.elapse(_TEST_TIMEOUT_S)
        with pytest.raises(AudioCaptureOpenTimeoutError):
            await open_task
    finally:
        if not ticker.done():
            ticker.cancel()
        if not open_task.done():
            open_task.cancel()
        gate.set()
        await asyncio.gather(open_task, ticker, return_exceptions=True)
        await _settle_shared_pool()

    assert driver.rx_running is False
    assert driver.tx_running is False
