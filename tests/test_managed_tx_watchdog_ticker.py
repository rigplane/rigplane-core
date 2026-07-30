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

    def __init__(self) -> None:
        self.clock, self.log = _Clock(), []
        self.serviced: list[TxTransition] = []
        self.provider = _Provider(self.log)
        self.runtime = ManagedRadioRuntime(
            "watchdog",
            service_factory=self._factory,
            provider_lifecycle=self.provider,
            clock=self.clock,
            tick_interval_seconds=_TICK,
        )

    def _factory(self, host: object) -> TxService:
        inner = managed_tx_effect_service(host)

        async def service(sup: TxSafetySupervisor, moved: TxTransition) -> None:
            self.serviced.append(moved)
            await inner(sup, moved)

        return service


async def _armed(*, key: bool = True) -> _Managed:
    """Bring the provider up, seed the OFF ``request_on`` demands, then key."""
    managed = _Managed()
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
