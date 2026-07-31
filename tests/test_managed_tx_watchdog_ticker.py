"""MOR-1191: the managed TX max-key-down watchdog needs a production driver.

``TxSafetySupervisor.tick`` is the only path to the watchdog and to the timed
retry of a failed OFF, and nothing under ``src`` ever called it: a rig keyed
through the managed path stayed keyed with no bound at all. No test here calls
``tick`` -- the whole defect is that nothing did -- so each one advances a fake
clock, lets the loop run, and watches the wire.

A real supervisor, the real effect service and a hand-rolled provider drive
every case: a scripted double would answer whatever it was told, and a
``MagicMock`` satisfies a ``runtime_checkable`` protocol on 3.11 but not on
3.12+ (gh-102433). ``_Provider`` is imported rather than copied so both suites
watch the same wire.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from rigplane.core.tx_safety import (
    TxOwner,
    TxPhase,
    TxReleaseReason,
    TxSafetySupervisor,
    TxSource,
    TxTransition,
)
from rigplane.runtime.managed_radio_runtime import ManagedRadioRuntime, TxService
from rigplane.runtime.managed_tx_effect_service import managed_tx_effect_service
from test_web_recovery_durable_off import _Provider

_OWNER = TxOwner(TxSource.WEBSOCKET, "ws-1")
_TICK = 0.002


class _Clock:
    """Monotonic only when the test says so."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class _Managed:
    """A managed runtime plus everything the tests need to watch it."""

    def __init__(self, *, tick: float = _TICK, shutdown: float = 3.0) -> None:
        self.clock, self.log = _Clock(), []
        self.serviced: list[TxTransition] = []
        self.park: asyncio.Event | None = None
        self.entered = asyncio.Event()
        self.provider = _Provider(self.log)
        self.runtime = ManagedRadioRuntime(
            "watchdog",
            service_factory=self._factory,
            provider_lifecycle=self.provider,
            clock=self.clock,
            shutdown_timeout_seconds=shutdown,
            tick_interval_seconds=tick,
        )

    def _factory(self, host: object) -> TxService:
        inner = managed_tx_effect_service(host)

        async def service(sup: TxSafetySupervisor, moved: TxTransition) -> None:
            self.serviced.append(moved)
            if (park := self.park) is None:
                await inner(sup, moved)
                return
            self.entered.set()
            try:
                await park.wait()  # a provider call that outlives its caller...
            except asyncio.CancelledError:
                pass  # ...and swallows the cancel meant to stop it
            finally:
                self.entered.clear()

        return service


async def _armed(
    *, key: bool = True, tick: float = _TICK, shutdown: float = 3.0
) -> _Managed:
    """Bring the provider up, seed the OFF ``request_on`` demands, then key."""
    managed = _Managed(tick=tick, shutdown=shutdown)
    await managed.runtime.replace_provider(ready=True)
    await managed.runtime.request_fresh_ptt()
    if key:
        assert (await managed.runtime.request_on(_OWNER)).snapshot.lease_id
    managed.log.clear()
    managed.serviced.clear()
    return managed


async def _settles(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    """Let real tick intervals elapse until the driver has done its work."""
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "the ticker never got there"
        await asyncio.sleep(_TICK)


async def _parked(*, shutdown: float = 3.0) -> _Managed:
    """Hold the ticker inside the provider call its own watchdog just made."""
    managed = await _armed(shutdown=shutdown)
    managed.park = asyncio.Event()
    managed.clock.now += 181.0  # the watchdog is due, so this tick has effects
    await asyncio.wait_for(managed.entered.wait(), timeout=2.0)
    return managed


async def _cancelled(managed: _Managed) -> asyncio.Task[None] | None:
    """Cancel the ticker the way a caller that forgets to clear the slot would."""
    ticker = managed.runtime._tick_task
    assert ticker is not None
    ticker.cancel()
    await asyncio.gather(ticker, return_exceptions=True)
    return ticker


async def _quiesce(managed: _Managed) -> None:
    """Reap the ticker whatever the test found: a parked one eats its cancel,
    and loop teardown cancels only once, so a failure would hang, not report."""
    if managed.park is not None:
        managed.park.set()
        await _settles(lambda: not managed.entered.is_set())
    if managed.runtime._tick_task is not None:
        await _cancelled(managed)


async def test_a_lease_held_past_max_key_down_is_dekeyed_on_the_wire() -> None:
    """Acceptance 1: the OFF reaches the provider, with no call from the test."""
    managed = await _armed()
    assert managed.runtime.tx_snapshot.phase is TxPhase.KEYED

    managed.clock.now += 181.0
    await _settles(lambda: managed.runtime.tx_snapshot.phase is TxPhase.IDLE)

    assert managed.log == ["ptt(off)", "read_ptt"]
    assert len(managed.serviced) == 1  # the effects reached the provider, not a void
    reason = managed.serviced[0].snapshot.release_reason
    assert reason is TxReleaseReason.BACKEND_MAX_KEY_DOWN
    # Nothing left to watch: the driver retires instead of idling forever.
    await _settles(lambda: managed.runtime._tick_task is None)


async def test_a_refused_off_retries_on_its_own_schedule() -> None:
    """Acceptance 2: the clock brings it back -- no reconnect, no second call."""
    managed = await _armed()
    managed.provider.write_failures = 1

    lease = managed.runtime.tx_snapshot.lease_id or ""
    await managed.runtime.request_off(_OWNER, lease)
    assert managed.log == ["ptt(off)"]
    assert managed.runtime.tx_snapshot.phase is TxPhase.FAULTED

    await asyncio.sleep(_TICK * 20)  # many ticks, but the retry is not due yet
    assert managed.log == ["ptt(off)"]

    managed.clock.now += 0.25  # retry_schedule_seconds[0]
    await _settles(lambda: managed.runtime.tx_snapshot.phase is TxPhase.IDLE)
    assert managed.log == ["ptt(off)", "ptt(off)", "read_ptt"]


async def test_the_ticker_stops_at_shutdown_and_never_fires_again() -> None:
    """Acceptance 3: shutdown owns the last word and leaves no live task."""
    managed = await _armed()
    ticker = managed.runtime._tick_task
    assert ticker is not None

    await managed.runtime.shutdown(release_provider=lambda: asyncio.sleep(0))

    assert managed.log == ["ptt(off)", "read_ptt"]
    assert managed.runtime._tick_task is None and ticker.done()
    managed.clock.now += 10_000.0
    await asyncio.sleep(_TICK * 20)
    assert managed.log == ["ptt(off)", "read_ptt"]


async def test_nothing_ticks_until_a_lease_exists_and_the_signal_says_so() -> None:
    """Acceptance 4 and 5: no lease, no driver, no cost, no watchdog claimed."""
    managed = await _armed(key=False)

    await asyncio.sleep(_TICK * 20)
    assert managed.runtime._tick_task is None and managed.log == []
    assert not managed.runtime.tx_snapshot.watchdog_enabled

    await managed.runtime.request_on(_OWNER)
    await _settles(lambda: managed.runtime.tx_snapshot.watchdog_enabled)
    assert managed.runtime._tick_task is not None


async def test_a_cancelled_ticker_frees_its_slot_and_claims_no_watchdog() -> None:
    """MOR-1194 item 1: the loop owns its slot, so no canceller can strand it.

    Only ``_complete_shutdown`` cancels today, and it clears the slot first --
    which makes a permanently dead watchdog depend on every future canceller
    remembering to do the same. Cancel it the careless way instead.
    """
    managed = await _armed()
    await _settles(lambda: managed.runtime.tx_snapshot.watchdog_enabled)
    await _cancelled(managed)

    assert managed.runtime._tick_task is None
    snapshot = managed.runtime.tx_snapshot
    # Item 2 of the acceptance: still keyed, and honest that nothing watches it.
    assert snapshot.lease_id is not None and not snapshot.watchdog_enabled


async def test_the_next_request_rearms_a_watchdog_that_still_fires() -> None:
    """MOR-1194 item 1: re-arming must restore the watchdog, not just a task."""
    managed = await _armed()
    await _settles(lambda: managed.runtime.tx_snapshot.watchdog_enabled)
    ticker = await _cancelled(managed)

    await managed.runtime.request_on(_OWNER)  # idempotent on the lease it holds
    assert managed.runtime._tick_task not in (None, ticker)

    managed.clock.now += 181.0
    await _settles(lambda: managed.runtime.tx_snapshot.phase is TxPhase.IDLE)
    assert managed.log == ["ptt(off)", "read_ptt"]
    await _settles(lambda: managed.runtime._tick_task is None)


async def test_shutdown_returns_even_when_the_service_swallows_its_cancel() -> None:
    """MOR-1194 item 2: the ``_shutdown_pending`` fence is the only stop left.

    A service that survives cancellation puts the ticker straight back into the
    loop, and the lease outlives the emergency release, so without the fence it
    ticks forever and the gather in ``_complete_shutdown`` never returns.
    """
    managed = await _parked(shutdown=0.05)
    try:
        await asyncio.wait_for(
            managed.runtime.shutdown(release_provider=lambda: asyncio.sleep(0)),
            timeout=5.0,
        )
        assert managed.runtime._tick_task is None
        assert not managed.runtime.tx_snapshot.watchdog_enabled
    finally:
        await _quiesce(managed)


async def test_the_ticker_waits_its_interval_between_ticks() -> None:
    """MOR-1194 item 3: four wakeups a second, not an unbounded hot loop.

    Only real time can show this. The fake clock does not move on its own, so a
    loop that never sleeps reaches all the same states -- thousands of times a
    second on a keyed rig, and no other assertion here would notice.
    """
    managed = await _armed(tick=5.0)
    supervisor, ticks = managed.runtime._tx_safety, 0
    real = supervisor.tick

    def counted() -> TxTransition:
        nonlocal ticks
        ticks += 1
        return real()

    supervisor.tick = counted
    await asyncio.sleep(0.05)

    assert managed.runtime._tick_task is not None  # alive, and still not spinning
    assert ticks <= 1, f"the interval is not awaited: {ticks} ticks in 50 ms"


async def test_effects_are_serviced_with_the_lifecycle_lock_released() -> None:
    """MOR-1194 item 4: a provider that stops answering must not stall the rest.

    Retirement needs ``_lifecycle_lock``. Hold it across the service call and
    the one provider already refusing to answer also blocks every attempt to
    replace it -- ``_provider_state`` stuck at ``bound`` instead of ``unbound``.
    """
    managed = await _parked()
    try:
        await asyncio.wait_for(
            managed.runtime.invalidate_provider(managed.runtime._provider_generation),
            timeout=1.0,
        )
        assert managed.runtime._provider_state == "unbound"
    finally:
        await _quiesce(managed)
