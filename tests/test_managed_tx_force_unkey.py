"""MOR-1175: the forced unkey has to survive the trip to the wire.

``TxSafetySupervisor.force_unkey`` decides that an unowned key may be adopted;
this suite covers everything between that decision and the rig going quiet. The
runtime is where the decision is either honoured or quietly lost: the adopted
lease carries a release that may be refused, and the retry lives behind
``tick``, which nothing calls unless ``force_unkey`` arms the ticker the way
``request_on`` does (MOR-1191). No test here calls ``tick`` or ``settle_attempt``
-- a suite that drove the reducer by hand would pass over a runtime that drives
nothing at all, which is the exact defect being fenced off.

So a real supervisor, the real effect service and the real ``_Provider`` from
``test_web_recovery_durable_off`` drive every case, and the assertions are the
provider's own log. ``_Provider`` is imported rather than copied so this suite,
the watchdog suite and the recovery suite all watch the same wire.

The runtime here is deliberately never observed and never keyed: MOR-1182's
whole premise is a freshly started process meeting an already-transmitting rig,
where the supervisor reports ``IDLE``/``UNKNOWN`` because it has seen nothing.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable

from rigplane.core.tx_safety import (
    RadioTx,
    TxOutcome,
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

_OWNER = TxOwner(TxSource.WEBSOCKET, "cli-1")
_FORCED = TxReleaseReason.OPERATOR_FORCED_UNKEY
_TICK = 0.002


class _Clock:
    """Monotonic only when the test says so."""

    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class _Managed:
    """A managed runtime, the wire under it, and a way to park mid-service."""

    def __init__(self, *, tick: float = _TICK) -> None:
        self.clock, self.log = _Clock(), []
        self.serviced: list[TxTransition] = []
        self.park: asyncio.Event | None = None
        self.entered = asyncio.Event()
        self.provider = _Provider(self.log)
        self.runtime = ManagedRadioRuntime(
            "force",
            service_factory=self._factory,
            provider_lifecycle=self.provider,
            clock=self.clock,
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
                await park.wait()  # a provider call that outlives its caller
            finally:
                self.entered.clear()

        return service


async def _unobserved(*, tick: float = _TICK) -> _Managed:
    """Bring the provider up and stop: no read, so no observation at all."""
    managed = _Managed(tick=tick)
    await managed.runtime.replace_provider(ready=True)
    managed.provider._keyed = True  # the rig transmits; nothing here knows it
    managed.log.clear()
    managed.serviced.clear()
    return managed


async def _settles(predicate: Callable[[], bool], timeout: float = 2.0) -> None:
    """Let real tick intervals elapse until the driver has done its work."""
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "the ticker never got there"
        await asyncio.sleep(_TICK)


async def test_a_never_observed_runtime_still_dekeys_the_rig() -> None:
    """The OFF and its confirming read reach the provider, with no tick here.

    The state this starts from is the one the supervisor cannot tell apart from
    an idle rig, and the reason the force may not gate on ``EXTERNAL_UNOWNED``.
    """
    managed = await _unobserved()
    before = managed.runtime.tx_snapshot
    assert (before.phase, before.radio_tx) == (TxPhase.IDLE, RadioTx.UNKNOWN)

    forced = await managed.runtime.force_unkey(_OWNER, reason=_FORCED)

    assert forced.outcome is TxOutcome.ACCEPTED
    assert managed.log == ["ptt(off)", "read_ptt"]
    assert len(managed.serviced) == 1  # the effects reached a provider, not a void
    # The adopted lease self-clears on the observation it caused.
    assert managed.runtime.tx_snapshot.phase is TxPhase.IDLE
    assert managed.runtime.tx_snapshot.lease_id is None


async def test_a_refused_forced_off_retries_on_the_clock_alone() -> None:
    """Nothing calls back, so the ticker the force armed is the only way back.

    Dropping that arm leaves a runtime that reports a durable release and then
    never attempts it again: the rig stays keyed and the snapshot says a
    release is owed. Only the clock moves here -- no second call, no reconnect.
    """
    managed = await _unobserved()
    managed.provider.write_failures = 1

    forced = await managed.runtime.force_unkey(_OWNER, reason=_FORCED)
    assert forced.outcome is TxOutcome.ACCEPTED
    assert managed.log == ["ptt(off)"]
    assert managed.runtime.tx_snapshot.phase is TxPhase.FAULTED

    await asyncio.sleep(_TICK * 20)  # many ticks, but the retry is not due yet
    assert managed.log == ["ptt(off)"]

    managed.clock.now += 0.25  # retry_schedule_seconds[0]
    await _settles(lambda: managed.runtime.tx_snapshot.phase is TxPhase.IDLE)
    assert managed.log == ["ptt(off)", "ptt(off)", "read_ptt"]


async def test_the_armed_ticker_retires_once_the_forced_off_confirms() -> None:
    """The ladder ends, and the driver leaves rather than idling forever.

    The refused first write is what makes this non-vacuous: it holds the lease
    open long enough to catch the ticker actually running, so the retirement
    asserted afterwards is a retirement and not an arm that never happened.
    """
    managed = await _unobserved()
    managed.provider.write_failures = 1

    forced = await managed.runtime.force_unkey(_OWNER, reason=_FORCED)
    ticker = managed.runtime._tick_task
    assert ticker is not None
    # Adopted leases are a release obligation, never a key-down: no watchdog is
    # armed over one, and the OFF is already owed rather than pending a timeout.
    assert forced.snapshot.watchdog_deadline_monotonic is None

    managed.clock.now += 0.25
    await _settles(lambda: managed.runtime.tx_snapshot.lease_id is None)
    await _settles(lambda: managed.runtime._tick_task is None)

    assert ticker.done()
    assert not managed.runtime.tx_snapshot.watchdog_enabled


async def test_a_force_after_shutdown_is_idempotent_and_writes_nothing() -> None:
    """Shutdown owns the last word, and the answer is not a refusal.

    Which outcome is returned is load-bearing, not cosmetic: the CLI reads
    ``IDEMPOTENT`` as "nothing is owed" and ``NOT_READY`` -- what the supervisor
    answers once shutdown has torn the provider down -- as "the rig may still be
    transmitting". Without the fence a post-shutdown force reports a rig in
    trouble that shutdown already dekeyed.
    """
    managed = await _unobserved()
    await managed.runtime.shutdown(release_provider=lambda: asyncio.sleep(0))
    managed.log.clear()

    forced = await managed.runtime.force_unkey(_OWNER, reason=_FORCED)

    assert forced.outcome is TxOutcome.IDEMPOTENT
    assert managed.log == []
    assert managed.runtime.tx_snapshot.lease_id is None  # no phantom obligation


async def test_a_force_racing_an_in_flight_shutdown_never_reaches_the_wire() -> None:
    """The fence earns its keep in the window where the rig is still reachable.

    Once shutdown has finished the supervisor refuses a force by itself, having
    no provider left to write through. In the window between the fence going up
    and the teardown reaching the provider it refuses nothing: the port is still
    bound and ready. Without the guard this mints a lease and puts a WRITE_OFF
    on a rig ``shutdown`` has already taken responsibility for, then leaves the
    obligation behind on a supervisor nothing will ever service again.
    """
    managed = await _unobserved()
    stopping = asyncio.create_task(
        managed.runtime.shutdown(release_provider=lambda: asyncio.sleep(0))
    )
    await asyncio.sleep(0)  # the fence is up, the teardown has not run yet
    assert managed.runtime._shutdown_pending
    assert managed.runtime._provider_state == "bound"  # still reachable...
    assert managed.runtime.tx_snapshot.provider_ready  # ...and still willing

    forced = await managed.runtime.force_unkey(_OWNER, reason=_FORCED)

    assert managed.log == []
    assert forced.snapshot.lease_id is None
    assert forced.outcome is TxOutcome.IDEMPOTENT
    await asyncio.wait_for(stopping, timeout=2.0)
    assert managed.log == []


async def test_effects_are_serviced_with_the_lifecycle_lock_released() -> None:
    """MOR-1194 item 4: the force must not wedge the runtime it is rescuing.

    The provider this writes to is, by construction, one that has already
    misbehaved. Hold ``_lifecycle_lock`` across the service call and a provider
    that stops answering mid-OFF also blocks every attempt to replace it --
    ``_provider_state`` stuck at ``bound`` -- so the one call meant to recover a
    stuck rig becomes the call that makes recovery impossible.
    """
    managed = await _unobserved()
    managed.park = asyncio.Event()
    forcing = asyncio.create_task(managed.runtime.force_unkey(_OWNER, reason=_FORCED))
    await asyncio.wait_for(managed.entered.wait(), timeout=2.0)

    try:
        await asyncio.wait_for(
            managed.runtime.invalidate_provider(managed.runtime._provider_generation),
            timeout=1.0,
        )
        assert managed.runtime._provider_state == "unbound"
    finally:
        managed.park.set()
        await asyncio.wait_for(forcing, timeout=2.0)

    assert managed.log == []  # the write never left the parked service


async def test_the_forced_transition_names_the_operator_not_the_system() -> None:
    """Attribution survives the runtime, which is the only place it can be read.

    ``OPERATOR_FORCED_UNKEY`` is the one reason no system lane can mint, so a
    caller -- or anything watching the transitions the service is handed -- can
    tell an operator reaching for the kill switch from the
    ``CONTROL_TRANSPORT_LOST`` a fault would name.
    """
    managed = await _unobserved()

    forced = await managed.runtime.force_unkey(_OWNER, reason=_FORCED)

    assert forced.snapshot.owner == _OWNER
    assert forced.snapshot.release_reason is _FORCED
    assert forced.snapshot.terminal_release_reason is _FORCED
    assert [moved.snapshot.terminal_release_reason for moved in managed.serviced] == [
        _FORCED
    ]
