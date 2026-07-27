from __future__ import annotations

from collections.abc import Iterator

import pytest

from rigplane.core.tx_safety import (
    CancelProviderAttempt,
    ProviderAttempt,
    ProviderAttemptKind,
    ProviderPttObservation,
    RadioTx,
    TxOutcome,
    TxOwner,
    TxPhase,
    TxReleaseReason,
    TxSafetySupervisor,
    TxSource,
)


class Clock:
    def __init__(self) -> None:
        self.now = 10.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def ids() -> Iterator[str]:
    index = 0
    while True:
        index += 1
        yield f"id-{index}"


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def owner() -> TxOwner:
    return TxOwner(TxSource.WEBSOCKET, "web-1")


@pytest.fixture
def other() -> TxOwner:
    return TxOwner(TxSource.RIGCTLD, "cat-1")


@pytest.fixture
def supervisor(clock: Clock) -> TxSafetySupervisor:
    generated = ids()
    return TxSafetySupervisor(clock=clock, id_factory=lambda: next(generated))


def observe(
    supervisor: TxSafetySupervisor,
    clock: Clock,
    value: RadioTx,
    *,
    generation: int = 1,
    sequence: int = 1,
    at: float | None = None,
):
    return supervisor.observe_ptt(
        ProviderPttObservation(
            value,
            generation,
            sequence,
            clock.now if at is None else at,
        )
    )


def ready_off(supervisor: TxSafetySupervisor, clock: Clock) -> None:
    supervisor.replace_provider(1, ready=True)
    observe(supervisor, clock, RadioTx.OFF)


def acquire(
    supervisor: TxSafetySupervisor, clock: Clock, owner: TxOwner
) -> tuple[str, ProviderAttempt]:
    ready_off(supervisor, clock)
    result = supervisor.request_on(owner)
    assert result.snapshot.lease_id
    effect = result.effects[0]
    assert isinstance(effect, ProviderAttempt)
    return result.snapshot.lease_id, effect


def settle_on(
    supervisor: TxSafetySupervisor, attempt: ProviderAttempt
) -> ProviderAttempt:
    effect = supervisor.settle_attempt(
        attempt.id, attempt.provider_generation, succeeded=True
    ).effects[0]
    assert isinstance(effect, ProviderAttempt)
    return effect


def test_on_requires_ready_authoritative_off(
    supervisor: TxSafetySupervisor, clock: Clock, owner: TxOwner
) -> None:
    assert supervisor.request_on(owner).outcome is TxOutcome.NOT_READY
    supervisor.replace_provider(1, ready=True)
    assert supervisor.request_on(owner).outcome is TxOutcome.RADIO_NOT_OFF
    observe(supervisor, clock, RadioTx.ON)
    assert supervisor.request_on(owner).outcome is TxOutcome.RADIO_NOT_OFF


def test_acquire_emits_one_bounded_on_and_fixed_watchdog(
    supervisor: TxSafetySupervisor, clock: Clock, owner: TxOwner
) -> None:
    ready_off(supervisor, clock)
    first = supervisor.request_on(owner)
    attempt = first.effects[0]
    assert isinstance(attempt, ProviderAttempt)
    assert attempt.kind is ProviderAttemptKind.WRITE_ON
    assert attempt.timeout_seconds == 2.0
    assert first.snapshot.watchdog_deadline_monotonic == 190.0
    clock.advance(50)
    repeated = supervisor.request_on(owner)
    assert repeated.outcome is TxOutcome.IDEMPOTENT
    assert repeated.effects == ()
    assert repeated.snapshot.watchdog_deadline_monotonic == 190.0


def test_other_owner_is_busy(
    supervisor: TxSafetySupervisor, clock: Clock, owner: TxOwner, other: TxOwner
) -> None:
    acquire(supervisor, clock, owner)
    assert supervisor.request_on(other).outcome is TxOutcome.BUSY


def test_on_is_never_replayed_after_generation_change(
    supervisor: TxSafetySupervisor, clock: Clock, owner: TxOwner
) -> None:
    acquire(supervisor, clock, owner)
    changed = supervisor.replace_provider(2, ready=True)
    assert [x.kind for x in changed.effects if isinstance(x, ProviderAttempt)] == [
        ProviderAttemptKind.WRITE_OFF
    ]


def test_failed_on_creates_durable_off(
    supervisor: TxSafetySupervisor, clock: Clock, owner: TxOwner
) -> None:
    _, attempt = acquire(supervisor, clock, owner)
    result = supervisor.settle_attempt(attempt.id, 1, succeeded=False, error="lost")
    off = result.effects[0]
    assert isinstance(off, ProviderAttempt)
    assert off.kind is ProviderAttemptKind.WRITE_OFF
    assert result.snapshot.release_reason is TxReleaseReason.CONTROL_TRANSPORT_LOST


def test_write_success_needs_causal_observation(
    supervisor: TxSafetySupervisor, clock: Clock, owner: TxOwner
) -> None:
    _, attempt = acquire(supervisor, clock, owner)
    read = settle_on(supervisor, attempt)
    assert read.kind is ProviderAttemptKind.READ_PTT
    assert supervisor.snapshot.phase is TxPhase.KEY_PENDING
    clock.advance(0.01)
    observe(supervisor, clock, RadioTx.ON, sequence=2)
    assert supervisor.snapshot.phase is TxPhase.KEYED


def test_newer_but_pre_boundary_on_is_external_conflict(
    supervisor: TxSafetySupervisor, clock: Clock, owner: TxOwner
) -> None:
    ready_off(supervisor, clock)
    clock.advance(1)
    supervisor.request_on(owner)
    result = observe(supervisor, clock, RadioTx.ON, sequence=2, at=10.5)
    assert result.outcome is TxOutcome.APPLIED
    assert result.snapshot.external_conflict
    assert result.snapshot.phase is TxPhase.KEY_PENDING


def test_globally_older_observation_is_stale(
    supervisor: TxSafetySupervisor, clock: Clock
) -> None:
    ready_off(supervisor, clock)
    observe(supervisor, clock, RadioTx.ON, sequence=2, at=11)
    stale = observe(supervisor, clock, RadioTx.OFF, sequence=3, at=10.5)
    assert stale.outcome is TxOutcome.STALE
    assert stale.snapshot.radio_tx is RadioTx.ON


@pytest.mark.parametrize("wrong", ["owner", "lease"])
def test_wrong_owner_or_lease_cannot_release(
    supervisor: TxSafetySupervisor,
    clock: Clock,
    owner: TxOwner,
    other: TxOwner,
    wrong: str,
) -> None:
    lease, _ = acquire(supervisor, clock, owner)
    result = supervisor.request_off(
        other if wrong == "owner" else owner,
        "stale" if wrong == "lease" else lease,
    )
    assert result.outcome is TxOutcome.STALE


def test_release_cancels_inflight_then_services_off(
    supervisor: TxSafetySupervisor, clock: Clock, owner: TxOwner
) -> None:
    lease, on = acquire(supervisor, clock, owner)
    released = supervisor.request_off(owner, lease)
    cancel = released.effects[0]
    assert isinstance(cancel, CancelProviderAttempt)
    assert cancel.attempt_id == on.id
    settled = supervisor.settle_attempt(on.id, 1, succeeded=False)
    assert isinstance(settled.effects[0], ProviderAttempt)
    assert settled.effects[0].kind is ProviderAttemptKind.WRITE_OFF


def test_release_of_inflight_read_services_off_without_backoff(
    supervisor: TxSafetySupervisor, clock: Clock, owner: TxOwner
) -> None:
    lease, on = acquire(supervisor, clock, owner)
    read = settle_on(supervisor, on)
    supervisor.request_off(owner, lease)
    settled = supervisor.settle_attempt(read.id, 1, succeeded=False)
    assert settled.effects[0].kind is ProviderAttemptKind.WRITE_OFF
    assert settled.snapshot.release_last_error is None


def test_repeated_release_coalesces_and_updates_terminal_reason(
    supervisor: TxSafetySupervisor, clock: Clock, owner: TxOwner
) -> None:
    lease, _ = acquire(supervisor, clock, owner)
    supervisor.request_off(owner, lease, reason=TxReleaseReason.CLIENT_DISCONNECTED)
    repeated = supervisor.request_off(
        owner, lease, reason=TxReleaseReason.ADMIN_EMERGENCY_STOP
    )
    assert repeated.outcome is TxOutcome.IDEMPOTENT
    assert repeated.snapshot.release_reason is TxReleaseReason.CLIENT_DISCONNECTED
    assert (
        repeated.snapshot.terminal_release_reason
        is TxReleaseReason.ADMIN_EMERGENCY_STOP
    )


def test_failed_off_persists_and_retries_when_due(
    supervisor: TxSafetySupervisor, clock: Clock, owner: TxOwner
) -> None:
    lease, on = acquire(supervisor, clock, owner)
    supervisor.request_off(owner, lease)
    off = supervisor.settle_attempt(on.id, 1, succeeded=False).effects[0]
    failed = supervisor.settle_attempt(off.id, 1, succeeded=False, error="timeout")
    assert failed.snapshot.phase is TxPhase.FAULTED
    assert failed.snapshot.release_last_error == "timeout"
    assert supervisor.tick().effects == ()
    clock.advance(0.25)
    retry = supervisor.tick().effects[0]
    assert retry.kind is ProviderAttemptKind.WRITE_OFF
    assert supervisor.snapshot.release_attempt_count == 2


def test_retry_backoff_caps_but_never_drops_obligation(
    clock: Clock, owner: TxOwner
) -> None:
    generated = ids()
    supervisor = TxSafetySupervisor(
        clock=clock,
        id_factory=lambda: next(generated),
        retry_schedule_seconds=(0.1, 0.2),
    )
    lease, on = acquire(supervisor, clock, owner)
    supervisor.request_off(owner, lease)
    attempt = supervisor.settle_attempt(on.id, 1, succeeded=False).effects[0]
    for delay in (0.1, 0.2, 0.2):
        supervisor.settle_attempt(attempt.id, 1, succeeded=False)
        clock.advance(delay)
        attempt = supervisor.tick().effects[0]
    assert supervisor.snapshot.release_attempt_count == 4
    assert supervisor.snapshot.lease_id == lease


def test_off_write_success_reads_but_does_not_clear(
    supervisor: TxSafetySupervisor, clock: Clock, owner: TxOwner
) -> None:
    lease, on = acquire(supervisor, clock, owner)
    supervisor.request_off(owner, lease)
    off = supervisor.settle_attempt(on.id, 1, succeeded=False).effects[0]
    result = supervisor.settle_attempt(off.id, 1, succeeded=True)
    assert result.effects[0].kind is ProviderAttemptKind.READ_PTT
    assert result.snapshot.phase is TxPhase.RELEASE_CONFIRMING
    assert result.snapshot.lease_id == lease


@pytest.mark.parametrize(
    ("generation", "sequence", "at"),
    [(2, 2, 11.0), (1, 1, 11.0), (1, 2, 9.0)],
    ids=["old-generation", "same-sequence", "old-time"],
)
def test_off_must_cross_every_release_boundary(
    supervisor: TxSafetySupervisor,
    clock: Clock,
    owner: TxOwner,
    generation: int,
    sequence: int,
    at: float,
) -> None:
    lease, _ = acquire(supervisor, clock, owner)
    supervisor.request_off(owner, lease)
    if generation == 2:
        supervisor.replace_provider(2, ready=False)
        generation = 1
    result = observe(
        supervisor, clock, RadioTx.OFF, generation=generation, sequence=sequence, at=at
    )
    assert result.outcome is TxOutcome.STALE
    assert result.snapshot.lease_id == lease


def test_fresh_authoritative_off_is_only_release_clear(
    supervisor: TxSafetySupervisor, clock: Clock, owner: TxOwner
) -> None:
    lease, _ = acquire(supervisor, clock, owner)
    supervisor.request_off(owner, lease)
    clock.advance(0.01)
    result = observe(supervisor, clock, RadioTx.OFF, sequence=2)
    assert result.snapshot.lease_id is None
    assert result.snapshot.phase is TxPhase.IDLE


def test_stale_attempt_callback_is_noop(
    supervisor: TxSafetySupervisor, clock: Clock, owner: TxOwner
) -> None:
    _, attempt = acquire(supervisor, clock, owner)
    result = supervisor.settle_attempt("stale", 1, succeeded=True)
    assert result.outcome is TxOutcome.STALE
    assert result.snapshot.active_attempt == attempt


def test_old_generation_callbacks_and_observations_are_noops(
    supervisor: TxSafetySupervisor, clock: Clock, owner: TxOwner
) -> None:
    _, old = acquire(supervisor, clock, owner)
    supervisor.replace_provider(2, ready=False)
    assert (
        supervisor.settle_attempt(old.id, 1, succeeded=True).outcome is TxOutcome.STALE
    )
    assert (
        observe(supervisor, clock, RadioTx.OFF, generation=1, sequence=9).outcome
        is TxOutcome.STALE
    )


def test_new_generation_services_off_first(
    supervisor: TxSafetySupervisor, clock: Clock, owner: TxOwner
) -> None:
    acquire(supervisor, clock, owner)
    changed = supervisor.replace_provider(2, ready=True)
    assert changed.snapshot.radio_tx is RadioTx.UNKNOWN
    assert changed.effects[0].kind is ProviderAttemptKind.WRITE_OFF


def test_not_ready_pauses_off_and_ready_resumes(
    supervisor: TxSafetySupervisor, clock: Clock, owner: TxOwner
) -> None:
    acquire(supervisor, clock, owner)
    paused = supervisor.set_provider_ready(1, ready=False)
    assert paused.effects == ()
    resumed = supervisor.set_provider_ready(1, ready=True)
    assert resumed.effects[0].kind is ProviderAttemptKind.WRITE_OFF


def test_release_read_failure_schedules_retry(
    supervisor: TxSafetySupervisor, clock: Clock, owner: TxOwner
) -> None:
    lease, on = acquire(supervisor, clock, owner)
    supervisor.request_off(owner, lease)
    off = supervisor.settle_attempt(on.id, 1, succeeded=False).effects[0]
    read = supervisor.settle_attempt(off.id, 1, succeeded=True).effects[0]
    failed = supervisor.settle_attempt(read.id, 1, succeeded=False)
    assert failed.snapshot.release_last_error == "read_ptt_failed"
    clock.advance(0.25)
    assert supervisor.tick().effects[0].kind is ProviderAttemptKind.WRITE_OFF


def test_acquisition_read_failure_fails_closed(
    supervisor: TxSafetySupervisor, clock: Clock, owner: TxOwner
) -> None:
    _, on = acquire(supervisor, clock, owner)
    read = settle_on(supervisor, on)
    result = supervisor.settle_attempt(read.id, 1, succeeded=False)
    assert result.effects[0].kind is ProviderAttemptKind.WRITE_OFF
    assert result.snapshot.release_reason is TxReleaseReason.CONTROL_TRANSPORT_LOST


@pytest.mark.parametrize("settle_on_first", [False, True])
def test_watchdog_expires_at_dispatch_deadline(
    supervisor: TxSafetySupervisor,
    clock: Clock,
    owner: TxOwner,
    settle_on_first: bool,
) -> None:
    _, on = acquire(supervisor, clock, owner)
    active = settle_on(supervisor, on) if settle_on_first else on
    clock.advance(180)
    result = supervisor.tick()
    assert result.snapshot.release_reason is TxReleaseReason.BACKEND_MAX_KEY_DOWN
    assert result.snapshot.watchdog_deadline_monotonic is None
    assert result.effects[0].attempt_id == active.id


def test_watchdog_can_be_disabled(clock: Clock, owner: TxOwner) -> None:
    generated = ids()
    supervisor = TxSafetySupervisor(
        clock=clock, id_factory=lambda: next(generated), watchdog_seconds=None
    )
    acquire(supervisor, clock, owner)
    clock.advance(1000)
    assert supervisor.tick().outcome is TxOutcome.NOOP
    assert not supervisor.snapshot.watchdog_enabled


def test_unowned_on_is_honest_and_not_auto_dekeyed(
    supervisor: TxSafetySupervisor, clock: Clock, owner: TxOwner
) -> None:
    supervisor.replace_provider(1, ready=True)
    result = observe(supervisor, clock, RadioTx.ON)
    assert result.snapshot.phase is TxPhase.EXTERNAL_UNOWNED
    assert result.effects == ()
    assert supervisor.request_on(owner).outcome is TxOutcome.RADIO_NOT_OFF
    observe(supervisor, clock, RadioTx.OFF, sequence=2)
    assert supervisor.request_on(owner).outcome is TxOutcome.ACCEPTED


def test_on_after_release_clear_is_new_external_activity(
    supervisor: TxSafetySupervisor, clock: Clock, owner: TxOwner
) -> None:
    lease, _ = acquire(supervisor, clock, owner)
    supervisor.request_off(owner, lease)
    clock.advance(0.01)
    observe(supervisor, clock, RadioTx.OFF, sequence=2)
    clock.advance(0.01)
    result = observe(supervisor, clock, RadioTx.ON, sequence=3)
    assert result.snapshot.phase is TxPhase.EXTERNAL_UNOWNED


def test_emergency_release(
    supervisor: TxSafetySupervisor, clock: Clock, owner: TxOwner
) -> None:
    acquire(supervisor, clock, owner)
    result = supervisor.emergency_release(reason=TxReleaseReason.ADMIN_EMERGENCY_STOP)
    assert result.outcome is TxOutcome.ACCEPTED
    assert isinstance(result.effects[0], CancelProviderAttempt)


@pytest.mark.parametrize(
    "factory",
    [
        lambda: TxOwner(TxSource.WEBSOCKET, ""),
        lambda: ProviderPttObservation(RadioTx.UNKNOWN, 1, 1, 0),
        lambda: ProviderPttObservation(RadioTx.OFF, -1, 1, 0),
        lambda: ProviderPttObservation(RadioTx.OFF, 1, 0, 0),
        lambda: TxSafetySupervisor(watchdog_seconds=0),
        lambda: TxSafetySupervisor(write_timeout_seconds=0),
        lambda: TxSafetySupervisor(retry_schedule_seconds=()),
        lambda: TxSafetySupervisor(retry_schedule_seconds=(1, 0)),
    ],
)
def test_invalid_inputs_are_rejected(factory) -> None:
    with pytest.raises(ValueError):
        factory()
