"""Observation adapter coverage for the external rigctld client backend."""
# mypy: disable-error-code=untyped-decorator

from __future__ import annotations

import pytest

from fake_rigctld import FakeRigctldServer
from rigplane.backends.rigctld_client import RigctldClientRadio
from rigplane.backends.rigctld_client.observations import (
    RigctldClientObservationAdapter,
    build_external_rigctld_acquisition_profile,
)
from rigplane.core.acquisition_scheduler import AcquisitionScheduler, AcquisitionStatus
from rigplane.core.command_service import command_intent_from_request
from rigplane.core.state_pipeline_contracts import FieldPath


def _clock() -> float:
    return 50.0


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
