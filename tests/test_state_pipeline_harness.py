"""Self-tests and examples for the radio state pipeline test harness."""

from __future__ import annotations

from support.state_pipeline import (
    FakeAcquisitionScheduler,
    FakeCivPushBackend,
    FakeClock,
    FakeCommandResponseBackend,
    FakeDroppedUnsolicitedBackend,
    FakeExternalRigctldClientBackend,
    FakePendingOverlayStore,
    FakePollingOnlyBackend,
    FakeStatePipeline,
    FakeYaesuLikeBackend,
    FieldPath,
    assert_consumer_delta,
    assert_no_consumer_delta,
    assert_revision_counters,
)


MAIN_FREQ = FieldPath.receiver("main", "active", "freq_mode", "freq_hz")
MAIN_MODE = FieldPath.receiver("main", "active", "freq_mode", "mode")
MAIN_S_METER = FieldPath.receiver("main", None, "meters", "s_meter")


def test_fake_observations_and_command_responses_are_deterministic() -> None:
    clock = FakeClock(start=100.0)
    pipeline = FakeStatePipeline(clock=clock)
    backend = FakeCommandResponseBackend(clock=clock)

    freq_observation = backend.command_response(
        MAIN_FREQ,
        14_074_000,
        correlation_id="cmd-1",
    )
    first = pipeline.apply(freq_observation)

    clock.advance(0.250)
    mode_observation = backend.command_response(
        MAIN_MODE,
        "USB",
        correlation_id="cmd-2",
    )
    second = pipeline.apply(mode_observation)

    assert first is not None
    assert second is not None
    assert first.observation_seq == 1
    assert first.state_revision == 1
    assert first.timestamp_monotonic == 100.0
    assert second.observation_seq == 2
    assert second.state_revision == 2
    assert second.timestamp_monotonic == 100.250
    assert backend.command_log == ["cmd-1", "cmd-2"]
    assert_revision_counters(
        pipeline,
        state_revision=2,
        freshness_revision=2,
        observation_seq=2,
    )


def test_fake_time_drives_freshness_and_acquisition_scheduling() -> None:
    clock = FakeClock(start=20.0)
    pipeline = FakeStatePipeline(clock=clock)
    scheduler = FakeAcquisitionScheduler(clock=clock)

    pipeline.apply(
        FakeCommandResponseBackend(clock=clock).command_response(
            MAIN_FREQ,
            7_074_000,
            max_age=1.0,
        )
    )
    request = scheduler.ensure_fresh(
        [MAIN_FREQ],
        max_age=0.5,
        timeout=2.0,
        reason="web-snapshot",
    )
    duplicate = scheduler.ensure_fresh(
        [MAIN_FREQ],
        max_age=0.5,
        timeout=2.0,
        reason="rigctld-get",
    )

    assert duplicate is request
    assert scheduler.requests == [request]
    assert scheduler.due_requests() == []
    clock.advance(0.500)
    assert scheduler.due_requests() == [request]
    assert pipeline.mark_stale_due() == []

    clock.advance(0.501)
    transitions = pipeline.mark_stale_due()

    assert [(item.path, item.previous, item.current) for item in transitions] == [
        (MAIN_FREQ, "fresh", "stale")
    ]
    assert_revision_counters(
        pipeline,
        state_revision=1,
        freshness_revision=2,
        observation_seq=1,
    )


def test_dropped_unsolicited_event_and_poll_response_are_deterministic() -> None:
    clock = FakeClock(start=0.0)
    pipeline = FakeStatePipeline(clock=clock)
    backend = FakeDroppedUnsolicitedBackend(clock=clock)

    pipeline.apply(backend.unsolicited(MAIN_FREQ, 14_074_000, max_age=1.0))
    backend.drop_next_unsolicited(MAIN_FREQ)

    dropped = backend.unsolicited(MAIN_FREQ, 14_075_000, max_age=1.0)
    assert dropped is None
    assert pipeline.snapshot()[MAIN_FREQ] == 14_074_000

    clock.advance(1.100)
    assert pipeline.mark_stale_due()[0].path == MAIN_FREQ
    reconciliation = pipeline.apply(backend.poll_response(MAIN_FREQ, 14_075_000))

    assert reconciliation is not None
    assert_consumer_delta(
        reconciliation,
        path=MAIN_FREQ,
        previous=14_074_000,
        current=14_075_000,
    )
    assert pipeline.snapshot()[MAIN_FREQ] == 14_075_000
    assert_revision_counters(
        pipeline,
        state_revision=2,
        freshness_revision=3,
        observation_seq=2,
    )


def test_meter_updates_do_not_wait_for_unrelated_state_revisions() -> None:
    clock = FakeClock()
    pipeline = FakeStatePipeline(clock=clock)
    backend = FakeCommandResponseBackend(clock=clock)

    pipeline.apply(backend.command_response(MAIN_FREQ, 14_074_000))
    meter_delta = pipeline.apply(backend.meter_sample(MAIN_S_METER, 0.42))
    unchanged_freq = pipeline.apply(backend.command_response(MAIN_FREQ, 14_074_000))

    assert meter_delta is not None
    assert_consumer_delta(
        meter_delta,
        path=MAIN_S_METER,
        previous=None,
        current=0.42,
    )
    assert_no_consumer_delta(unchanged_freq)
    assert_revision_counters(
        pipeline,
        state_revision=2,
        freshness_revision=2,
        observation_seq=3,
    )


def test_stale_field_reconciles_after_missed_unsolicited_event() -> None:
    clock = FakeClock()
    pipeline = FakeStatePipeline(clock=clock)
    backend = FakeDroppedUnsolicitedBackend(clock=clock)

    pipeline.apply(backend.unsolicited(MAIN_FREQ, 14_074_000, max_age=0.5))
    backend.drop_next_unsolicited(MAIN_FREQ)
    assert backend.unsolicited(MAIN_FREQ, 14_076_000, max_age=0.5) is None

    clock.advance(0.600)
    stale_transitions = pipeline.mark_stale_due()
    reconciliation = pipeline.apply(backend.poll_response(MAIN_FREQ, 14_076_000))

    assert [(item.path, item.current) for item in stale_transitions] == [
        (MAIN_FREQ, "stale")
    ]
    assert reconciliation is not None
    assert_consumer_delta(
        reconciliation,
        path=MAIN_FREQ,
        previous=14_074_000,
        current=14_076_000,
    )
    assert_revision_counters(
        pipeline,
        state_revision=2,
        freshness_revision=3,
        observation_seq=2,
    )


def test_backend_variants_and_pending_overlays_are_scoped() -> None:
    clock = FakeClock()
    variants = [
        (
            FakeCivPushBackend(clock=clock).unsolicited(MAIN_FREQ, 14_074_000),
            "civ_unsolicited",
        ),
        (
            FakePollingOnlyBackend(clock=clock).poll_response(MAIN_FREQ, 14_074_000),
            "state_poller",
        ),
        (
            FakeYaesuLikeBackend(clock=clock).poll_response(MAIN_FREQ, 14_074_000),
            "yaesu_poll_response",
        ),
        (
            FakeExternalRigctldClientBackend(clock=clock).poll_response(
                MAIN_FREQ, 14_074_000
            ),
            "hamlib_response",
        ),
    ]

    for observation, expected_source in variants:
        assert observation is not None
        assert observation.source == expected_source

    overlays = FakePendingOverlayStore(clock=clock)
    web = overlays.put(
        source="websocket",
        session_id="web-1",
        command_id="cmd-web",
        path=MAIN_FREQ,
        value=14_074_000,
        ttl=1.0,
    )
    rigctld = overlays.put(
        source="rigctld",
        session_id="rig-1",
        command_id="cmd-rig",
        path=MAIN_FREQ,
        value=7_074_000,
        ttl=1.0,
    )
    same_value_other_command = overlays.put(
        source="rigctld",
        session_id="rig-1",
        command_id="cmd-rig-later",
        path=MAIN_FREQ,
        value=7_074_000,
        ttl=1.0,
    )

    assert overlays.visible_value(
        source="websocket",
        session_id="web-1",
        command_id="cmd-web",
        path=MAIN_FREQ,
    ) == 14_074_000
    assert overlays.visible_value(
        source="rigctld",
        session_id="rig-1",
        command_id="cmd-rig",
        path=MAIN_FREQ,
    ) == 7_074_000
    assert overlays.confirm(
        source="rigctld",
        session_id="rig-1",
        command_id="cmd-rig",
        path=MAIN_FREQ,
        value=7_074_000,
    ) == [rigctld]
    assert overlays.visible_value(
        source="websocket",
        session_id="web-1",
        command_id="cmd-web",
        path=MAIN_FREQ,
    ) == 14_074_000
    assert overlays.visible_value(
        source="rigctld",
        session_id="rig-1",
        command_id="cmd-rig-later",
        path=MAIN_FREQ,
    ) == 7_074_000

    clock.advance(1.001)
    assert overlays.expire_due() == [web, same_value_other_command]
    assert (
        overlays.visible_value(
            source="websocket",
            session_id="web-1",
            command_id="cmd-web",
            path=MAIN_FREQ,
        )
        is None
    )
