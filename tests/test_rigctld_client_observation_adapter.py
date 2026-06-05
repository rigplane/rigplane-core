"""Observation adapter coverage for the external rigctld client backend."""
# mypy: disable-error-code=untyped-decorator

from __future__ import annotations

import asyncio

import pytest

from fake_rigctld import FakeRigctldServer
from rigplane._poller_types import (
    CommandQueue,
    EnableScope,
    PttOn,
    SetAfLevel,
    SetAttenuator,
    SetFreq,
    SetMode,
    SetNB,
    SetNR,
    SetPreamp,
    SetRfGain,
)
from rigplane.backends.rigctld_client import RigctldClientRadio
from rigplane.backends.rigctld_client.observations import (
    RigctldClientObservationAdapter,
    build_external_rigctld_acquisition_profile,
)
from rigplane.core.acquisition_scheduler import AcquisitionScheduler, AcquisitionStatus
from rigplane.core.capabilities import CAP_POWER_CONTROL, CAP_TX
from rigplane.core.radio_protocol import PowerControlCapable
from rigplane.core.state_acquisition_policy import FieldAvailability
from rigplane.core.command_service import (
    CommandExecutionResult,
    CommandService,
    command_intent_from_request,
)
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import FreshnessClock, StateStore
from rigplane.exceptions import CommandError


def _clock() -> float:
    return 50.0


class _NoopCommandExecutor:
    async def execute(self, intent: object) -> CommandExecutionResult:
        del intent
        return CommandExecutionResult(details={"queued": True})


@pytest.mark.asyncio
async def test_get_reads_emit_hamlib_observations() -> None:
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            adapter = RigctldClientObservationAdapter(radio, clock=_clock)

            observations = await adapter.read_freq_mode_controls()

            assert [(str(item.path), item.value) for item in observations] == [
                ("receiver.main.active.freq_mode.freq_hz", 14_074_000),
                ("receiver.main.active.freq_mode.mode", "USB"),
                ("receiver.main.active.freq_mode.filter_width", 2400),
                ("receiver.main.operator_controls.rf_gain", 128),
                ("receiver.main.operator_controls.af_level", 76),
            ]
            assert all(item.source.source == "hamlib_response" for item in observations)
            assert all(
                item.source.provider == "external_rigctld" for item in observations
            )
            assert all(item.source.transport == "rigctld" for item in observations)
            assert all(item.timestamp_monotonic == 50.0 for item in observations)
            assert observations[0].max_age == 8.0
            assert observations[-1].max_age == 120.0
        finally:
            await radio.disconnect()


@pytest.mark.asyncio
async def test_observation_poller_drains_web_commands_before_readback() -> None:
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            queue = CommandQueue()
            loop = asyncio.get_running_loop()
            futures = []
            for command in (
                SetFreq(7_050_000),
                SetMode("LSB", filter_width=1800),
                PttOn(),
                SetRfGain(200),
                SetAfLevel(51),
                SetPreamp(2),
                SetAttenuator(18),
                SetNB(True),
                SetNR(True),
            ):
                future: asyncio.Future[None] = loop.create_future()
                futures.append(future)
                queue.put_ordered(future=future, cmd=command)

            observations = []
            poller = radio.create_observation_poller(
                callback=observations.extend,
                command_queue=queue,
            )

            await poller._poll_medium()  # noqa: SLF001
            await poller._poll_slow()  # noqa: SLF001

            assert all(future.done() and not future.cancelled() for future in futures)
            assert all(future.result() is None for future in futures)
            values = {str(item.path): item.value for item in observations}
            assert values["receiver.main.active.freq_mode.freq_hz"] == 7_050_000
            assert values["receiver.main.active.freq_mode.mode"] == "LSB"
            assert values["receiver.main.active.freq_mode.filter_width"] == 1800
            assert values["global.tx_state.ptt"] is True
            assert values["receiver.main.operator_controls.rf_gain"] == 200
            assert values["receiver.main.operator_controls.af_level"] == 51
            assert values["receiver.main.operator_controls.preamp"] == 2
            assert values["receiver.main.operator_controls.att"] == 18
            assert values["receiver.main.operator_toggles.nb"] is True
            assert values["receiver.main.operator_toggles.nr"] is True
            assert all(item.source.source == "hamlib_response" for item in observations)
        finally:
            await radio.disconnect()


@pytest.mark.asyncio
async def test_observation_poller_set_success_waits_for_rigctld_readback() -> None:
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            queue = CommandQueue()
            store = StateStore()
            service = CommandService(
                executor=_NoopCommandExecutor(),
                state_store=store,
            )
            intent = command_intent_from_request(
                "set_freq",
                {"freq": 7_050_000, "receiver": 0},
                source="websocket",
                command_id="ws-rigctld-set-freq",
            )
            await service.execute(intent)
            assert service.pending_overlays(
                source="websocket",
                session_id=None,
                command_id="ws-rigctld-set-freq",
            )

            future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            queue.put_ordered(
                SetFreq(7_050_000),
                future=future,
                command_id="ws-rigctld-set-freq",
                source="websocket",
                command_service=service,
            )
            observations = []
            poller = radio.create_observation_poller(
                callback=observations.extend,
                command_queue=queue,
            )

            await poller._drain_commands()  # noqa: SLF001

            assert future.done()
            assert future.result() is None
            with pytest.raises(KeyError):
                store.snapshot().field("receiver.0.freq_mode.freq_hz")

            await poller._poll_medium()  # noqa: SLF001
            for observation in observations:
                service.apply_observation(observation)

            assert (
                service.pending_overlays(
                    source="websocket",
                    session_id=None,
                    command_id="ws-rigctld-set-freq",
                )
                == ()
            )
            assert [
                event.state
                for event in service.lifecycle_events()
                if event.command_id == "ws-rigctld-set-freq"
            ] == ["accepted", "queued", "sent", "acknowledged", "reconciled"]
            assert (
                store.snapshot().field("receiver.main.active.freq_mode.freq_hz").value
                == 7_050_000
            )
            freq_observation = next(
                item
                for item in observations
                if str(item.path) == "receiver.main.active.freq_mode.freq_hz"
            )
            assert freq_observation.source.source == "hamlib_response"
            assert freq_observation.source.command_source == "websocket"
            assert freq_observation.source.session_id is None
            assert freq_observation.correlation_id == "ws-rigctld-set-freq"
        finally:
            await radio.disconnect()


@pytest.mark.asyncio
async def test_observation_poller_does_not_reconcile_nonmatching_readback() -> None:
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            queue = CommandQueue()
            store = StateStore()
            service = CommandService(
                executor=_NoopCommandExecutor(),
                state_store=store,
            )
            intent = command_intent_from_request(
                "set_freq",
                {"freq": 7_060_000, "receiver": 0},
                source="websocket",
                command_id="ws-rigctld-mismatch",
            )
            await service.execute(intent)
            future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            queue.put_ordered(
                SetFreq(7_050_000),
                future=future,
                command_id="ws-rigctld-mismatch",
                source="websocket",
                command_service=service,
            )
            observations = []
            poller = radio.create_observation_poller(
                callback=observations.extend,
                command_queue=queue,
            )

            await poller._poll_medium()  # noqa: SLF001
            for observation in observations:
                service.apply_observation(observation)

            assert service.pending_overlays(
                source="websocket",
                session_id=None,
                command_id="ws-rigctld-mismatch",
            )
            assert [
                event.state
                for event in service.lifecycle_events()
                if event.command_id == "ws-rigctld-mismatch"
            ] == ["accepted", "queued", "sent", "acknowledged"]
            freq_observation = next(
                item
                for item in observations
                if str(item.path) == "receiver.main.active.freq_mode.freq_hz"
            )
            assert freq_observation.value == 7_050_000
            assert freq_observation.correlation_id is None
            assert (
                store.snapshot().field("receiver.main.active.freq_mode.freq_hz").value
                == 7_050_000
            )
        finally:
            await radio.disconnect()


@pytest.mark.asyncio
async def test_observation_poller_discards_nonmatching_readback_expectation() -> None:
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            queue = CommandQueue()
            clock = FreshnessClock(start=61.0)
            store = StateStore(freshness_clock=clock)
            service = CommandService(
                executor=_NoopCommandExecutor(),
                state_store=store,
                clock=clock.now,
            )
            command_id = "ws-rigctld-stale-mismatch"
            intent = command_intent_from_request(
                "set_freq",
                {"freq": 7_060_000, "receiver": 0},
                source="websocket",
                command_id=command_id,
            )
            await service.execute(intent)
            queue.put_ordered(
                SetFreq(7_050_000),
                command_id=command_id,
                source="websocket",
                command_service=service,
            )
            observations = []
            poller = radio.create_observation_poller(
                callback=observations.extend,
                command_queue=queue,
            )

            await poller._poll_medium()  # noqa: SLF001
            for observation in observations:
                service.apply_observation(observation)

            assert (
                service.readback_expectations(
                    source="websocket",
                    session_id=None,
                    command_id=command_id,
                )
                == ()
            )
            clock.advance(2.01)
            assert (
                service.pending_overlays(
                    source="websocket",
                    session_id=None,
                    command_id=command_id,
                )
                == ()
            )
            stale_matching_observation = next(
                item
                for item in observations
                if str(item.path) == "receiver.main.active.freq_mode.freq_hz"
            )
            service.apply_observation(
                Observation(
                    path=stale_matching_observation.path,
                    value=7_060_000,
                    source=SourceMetadata(
                        source="hamlib_response",
                        provider="external_rigctld",
                        transport="rigctld",
                        command_source="websocket",
                        session_id=None,
                    ),
                    timestamp_monotonic=clock.now(),
                    correlation_id=command_id,
                )
            )

            assert [
                event.state
                for event in service.lifecycle_events()
                if event.command_id == command_id
            ] == ["accepted", "queued", "sent", "acknowledged"]
        finally:
            await radio.disconnect()


@pytest.mark.asyncio
async def test_observation_poller_discards_expectation_when_readback_unavailable() -> (
    None
):
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            queue = CommandQueue()
            service = CommandService(
                executor=_NoopCommandExecutor(),
                state_store=StateStore(),
            )
            command_id = "ws-rigctld-no-readback"
            intent = command_intent_from_request(
                "set_preamp",
                {"level": 2, "receiver": 0},
                source="websocket",
                command_id=command_id,
            )
            await service.execute(intent)
            queue.put_ordered(
                SetPreamp(2),
                command_id=command_id,
                source="websocket",
                command_service=service,
            )
            poller = radio.create_observation_poller(
                callback=lambda _observations: None,
                command_queue=queue,
            )

            await poller._drain_commands()  # noqa: SLF001
            poller._annotate_readback_observations(())  # noqa: SLF001

            assert (
                service.readback_expectations(
                    source="websocket",
                    session_id=None,
                    command_id=command_id,
                )
                == ()
            )
        finally:
            await radio.disconnect()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("command_name", "params", "queue_command", "path_text", "expected_value"),
    (
        (
            "set_preamp",
            {"level": 2, "receiver": 0},
            SetPreamp(2),
            "receiver.main.operator_controls.preamp",
            2,
        ),
        (
            "set_attenuator",
            {"db": 18, "receiver": 0},
            SetAttenuator(18),
            "receiver.main.operator_controls.att",
            18,
        ),
        (
            "set_nb",
            {"on": True, "receiver": 0},
            SetNB(True),
            "receiver.main.operator_toggles.nb",
            True,
        ),
        (
            "set_nr",
            {"on": True, "receiver": 0},
            SetNR(True),
            "receiver.main.operator_toggles.nr",
            True,
        ),
    ),
)
async def test_observation_poller_reconciles_slow_control_set_without_waiting_for_slow_poll(
    command_name: str,
    params: dict[str, object],
    queue_command: object,
    path_text: str,
    expected_value: object,
) -> None:
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            queue = CommandQueue()
            store = StateStore()
            service = CommandService(
                executor=_NoopCommandExecutor(),
                state_store=store,
            )
            command_id = f"ws-rigctld-{command_name}"
            intent = command_intent_from_request(
                command_name,
                params,
                source="websocket",
                command_id=command_id,
            )
            await service.execute(intent)
            assert service.pending_overlays(
                source="websocket",
                session_id=None,
                command_id=command_id,
            )
            queue.put_ordered(
                queue_command,
                command_id=command_id,
                source="websocket",
                command_service=service,
            )
            observations = []
            poller = radio.create_observation_poller(
                callback=observations.extend,
                command_queue=queue,
            )

            await poller._poll_medium()  # noqa: SLF001
            for observation in observations:
                service.apply_observation(observation)

            assert (
                service.pending_overlays(
                    source="websocket",
                    session_id=None,
                    command_id=command_id,
                )
                == ()
            )
            assert [
                event.state
                for event in service.lifecycle_events()
                if event.command_id == command_id
            ] == ["accepted", "queued", "sent", "acknowledged", "reconciled"]
            readback = next(
                item for item in observations if str(item.path) == path_text
            )
            assert readback.value == expected_value
            assert readback.source.source == "hamlib_response"
            assert readback.correlation_id == command_id
        finally:
            await radio.disconnect()


@pytest.mark.asyncio
async def test_observation_poller_correlates_slow_control_after_overlay_ttl_edge() -> (
    None
):
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            queue = CommandQueue()
            clock = FreshnessClock(start=70.0)
            store = StateStore(freshness_clock=clock)
            service = CommandService(
                executor=_NoopCommandExecutor(),
                state_store=store,
                clock=clock.now,
            )
            command_id = "ws-rigctld-preamp-ttl-edge"
            intent = command_intent_from_request(
                "set_preamp",
                {"level": 2, "receiver": 0},
                source="websocket",
                command_id=command_id,
            )
            await service.execute(intent)
            clock.advance(2.01)
            assert (
                service.pending_overlays(
                    source="websocket",
                    session_id=None,
                    command_id=command_id,
                )
                == ()
            )
            queue.put_ordered(
                SetPreamp(2),
                command_id=command_id,
                source="websocket",
                command_service=service,
            )
            observations = []
            poller = radio.create_observation_poller(
                callback=observations.extend,
                command_queue=queue,
            )

            await poller._poll_medium()  # noqa: SLF001
            for observation in observations:
                service.apply_observation(observation)

            readback = next(
                item
                for item in observations
                if str(item.path) == "receiver.main.operator_controls.preamp"
            )
            assert readback.source.source == "hamlib_response"
            assert readback.source.command_source == "websocket"
            assert readback.source.session_id is None
            assert readback.correlation_id == command_id
            assert [
                event.state
                for event in service.lifecycle_events()
                if event.command_id == command_id
            ] == ["accepted", "queued", "sent", "acknowledged", "reconciled"]
        finally:
            await radio.disconnect()


@pytest.mark.asyncio
async def test_observation_poller_reconciles_only_matching_source_session() -> None:
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            queue = CommandQueue()
            service = CommandService(
                executor=_NoopCommandExecutor(),
                state_store=StateStore(),
            )
            websocket_intent = command_intent_from_request(
                "set_freq",
                {"freq": 7_050_000, "receiver": 0},
                source="websocket",
                command_id="shared-rigctld-set",
                session_id="ws-a",
            )
            http_intent = command_intent_from_request(
                "set_freq",
                {"freq": 7_050_000, "receiver": 0},
                source="http",
                command_id="shared-rigctld-set",
            )
            await service.execute(websocket_intent)
            await service.execute(http_intent)
            queue.put_ordered(
                SetFreq(7_050_000),
                command_id="shared-rigctld-set",
                source="http",
                command_service=service,
            )
            observations = []
            poller = radio.create_observation_poller(
                callback=observations.extend,
                command_queue=queue,
            )

            await poller._poll_medium()  # noqa: SLF001
            for observation in observations:
                service.apply_observation(observation)

            assert service.pending_overlays(source="http", session_id=None) == ()
            assert service.pending_overlays(
                source="websocket",
                session_id="ws-a",
                command_id="shared-rigctld-set",
            )
            reconciled = [
                event
                for event in service.lifecycle_events()
                if event.state == "reconciled"
            ]
            assert [
                (event.source, event.details.get("session_id")) for event in reconciled
            ] == [
                ("http", None),
            ]
        finally:
            await radio.disconnect()


@pytest.mark.asyncio
async def test_observation_poller_unsupported_command_fails_future() -> None:
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            queue = CommandQueue()
            future: asyncio.Future[None] = asyncio.get_running_loop().create_future()
            queue.put_ordered(EnableScope(), future=future)
            poller = radio.create_observation_poller(
                callback=lambda _observations: None,
                command_queue=queue,
            )

            await poller._drain_commands()  # noqa: SLF001

            assert future.done()
            with pytest.raises(CommandError, match="not supported"):
                future.result()
        finally:
            await radio.disconnect()


@pytest.mark.asyncio
async def test_radio_observation_poller_emits_adapter_covered_reads() -> None:
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            observations = []
            poller = radio.create_observation_poller(callback=observations.extend)

            await poller._poll_medium()  # noqa: SLF001

            assert [(str(item.path), item.value) for item in observations] == [
                ("receiver.main.active.freq_mode.freq_hz", 14_074_000),
                ("receiver.main.active.freq_mode.mode", "USB"),
                ("receiver.main.active.freq_mode.filter_width", 2400),
                ("receiver.main.operator_controls.rf_gain", 128),
                ("receiver.main.operator_controls.af_level", 76),
                ("global.tx_state.ptt", False),
                ("receiver.main.vfo.active_slot", "A"),
            ]
            assert all(item.source.source == "hamlib_response" for item in observations)
        finally:
            await radio.disconnect()


def test_command_response_observations_use_external_rigctld_provider() -> None:
    adapter = RigctldClientObservationAdapter(None, clock=_clock)
    intent = command_intent_from_request(
        "set_freq",
        {"freq": 7_050_000},
        source="rigctld",
        command_id="rig-set-1",
        session_id="client-1",
    )

    observation = adapter.command_response(intent)

    assert str(observation.path) == "receiver.main.active.freq_mode.freq_hz"
    assert observation.value == 7_050_000
    assert observation.source.source == "command_response"
    assert observation.source.provider == "external_rigctld"
    assert observation.source.transport == "rigctld"
    assert observation.source.command_source == "rigctld"
    assert observation.source.session_id == "client-1"
    assert observation.correlation_id == "rig-set-1"


def test_command_response_normalizes_receiver_zero_control_paths() -> None:
    adapter = RigctldClientObservationAdapter(None, clock=_clock)
    intent = command_intent_from_request(
        "set_preamp",
        {"level": 2, "receiver": 0},
        source="rigctld",
        command_id="rig-preamp-1",
    )

    observation = adapter.command_response(intent)

    assert str(observation.path) == "receiver.main.operator_controls.preamp"
    assert observation.value == 2
    assert observation.max_age == 120.0


def test_capability_gaps_are_explicit_for_external_rigctld_profile() -> None:
    profile = build_external_rigctld_acquisition_profile(vfo_supported=False)
    power = FieldPath.global_("tx_state", "power_on")
    active_slot = FieldPath.active_slot("main")
    scheduler = AcquisitionScheduler(profile=profile)

    power_result = scheduler.ensure_fresh(
        power,
        max_age=1.0,
        priority="user",
        reason="startup",
    )
    vfo_result = scheduler.ensure_fresh(
        active_slot,
        max_age=1.0,
        priority="user",
        reason="vfo-get",
    )

    assert power_result.status is AcquisitionStatus.UNAVAILABLE
    assert "power state" in power_result.message
    assert vfo_result.status is AcquisitionStatus.UNAVAILABLE
    assert "VFO slot" in vfo_result.message


@pytest.mark.asyncio
async def test_tx_capability_excludes_power_control_with_unsupported_power() -> None:
    """MOR-433: the rigctld-client TX capability never claims power control.

    The backend advertises ``CAP_TX`` (PTT key/un-key) but deliberately
    omits ``CAP_POWER_CONTROL`` and does not structurally satisfy
    :class:`PowerControlCapable`, because external rigctld exposes no
    power-state read/set surface. The acquisition profile mirrors this by
    declaring the ``power_on`` field ``UNSUPPORTED`` rather than emitting a
    synthesized/default observed value, so consumers see the gap explicitly
    instead of a fabricated ``power_on=True``.
    """
    power_path = FieldPath.global_("tx_state", "power_on")
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            # Capability set advertises PTT TX but never power control.
            assert CAP_TX in radio.capabilities
            assert CAP_POWER_CONTROL not in radio.capabilities
            assert "power" not in radio.capabilities
            # The backend cannot honor power-set/get, so it must not
            # structurally satisfy the PowerControlCapable contract.
            assert not isinstance(radio, PowerControlCapable)

            # The acquisition profile surfaces power as UNSUPPORTED — not a
            # default or observed value — and excludes it from polling.
            profile = build_external_rigctld_acquisition_profile(vfo_supported=True)
            power_capability = profile.capability_for(power_path)
            assert power_capability.availability is FieldAvailability.UNSUPPORTED
            assert power_capability.can_poll is False
            assert power_capability.supported_controls == ()
        finally:
            await radio.disconnect()


def test_filter_width_command_response_metadata_matches_adapter_behavior() -> None:
    profile = build_external_rigctld_acquisition_profile(vfo_supported=True)
    capability = profile.capability_for(
        FieldPath.active("main", "freq_mode", "filter_width")
    )

    assert capability.can_poll is True
    assert capability.command_response_observable is False
    assert "poll" in capability.diagnostic.lower()


@pytest.mark.asyncio
async def test_vfo_observation_available_when_rigctld_supports_it() -> None:
    async with FakeRigctldServer() as server:
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            adapter = RigctldClientObservationAdapter(radio, clock=_clock)

            observation = await adapter.read_active_vfo()

            assert observation is not None
            assert str(observation.path) == "receiver.main.vfo.active_slot"
            assert observation.value == "A"
            assert observation.source.source == "hamlib_response"
            assert observation.max_age == 8.0
        finally:
            await radio.disconnect()


@pytest.mark.asyncio
async def test_ptt_and_slow_control_reads_cover_declared_rigctld_capabilities() -> None:
    async with FakeRigctldServer() as server:
        server.state.ptt = 1
        server.state.preamp_db = 12
        server.state.att_db = 18
        server.state.nb = 1
        server.state.nr = 0
        radio = RigctldClientRadio(host=server.host, port=server.port)
        await radio.connect()
        try:
            adapter = RigctldClientObservationAdapter(radio, clock=_clock)

            observations = (
                await adapter.read_ptt(),
                *(await adapter.read_slow_controls()),
            )

            assert [(str(item.path), item.value) for item in observations] == [
                ("global.tx_state.ptt", True),
                ("receiver.main.operator_controls.rf_gain", 128),
                ("receiver.main.operator_controls.af_level", 76),
                ("receiver.main.operator_controls.preamp", 1),
                ("receiver.main.operator_controls.att", 18),
                ("receiver.main.operator_toggles.nb", True),
                ("receiver.main.operator_toggles.nr", False),
            ]
            assert observations[0].max_age == 8.0
            assert all(item.max_age == 120.0 for item in observations[1:])
        finally:
            await radio.disconnect()
