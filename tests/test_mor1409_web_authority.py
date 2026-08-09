"""MOR-1409 A01: command lifecycle must not manufacture radio truth."""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from rigplane.core.command_service import command_intent_from_request
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.web.server import WebConfig, WebServer


_CASES = (
    (
        "set_freq",
        {"freq": 14_200_000, "receiver": 0},
        FieldPath.active("0", "freq_mode", "freq_hz"),
        14_074_000,
        14_200_000,
        lambda state: state["main"]["freqHz"],
    ),
    (
        "set_mode",
        {"mode": "CW", "receiver": 0},
        FieldPath.active("0", "freq_mode", "mode"),
        "USB",
        "CW",
        lambda state: state["main"]["mode"],
    ),
    (
        "set_data_mode",
        {"mode": 1, "receiver": 0},
        FieldPath.active("0", "freq_mode", "data_mode"),
        0,
        1,
        lambda state: state["main"]["dataMode"],
    ),
    (
        "set_data2_mod_input",
        {"source": 5},
        FieldPath.global_("slow_state", "data2_mod_input"),
        0,
        5,
        lambda state: state["data2ModInput"],
    ),
)


def _observation(path: FieldPath, value: object, generation: int) -> Observation:
    return Observation(
        path=path,
        value=value,
        source=SourceMetadata(source="poll_response", provider="test_provider"),
        timestamp_monotonic=time.monotonic(),
        provider_generation=generation,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "params", "path", "confirmed", "requested", "public_value"),
    _CASES,
)
async def test_success_is_lifecycle_only_until_provider_observation(
    name: str,
    params: dict[str, object],
    path: FieldPath,
    confirmed: object,
    requested: object,
    public_value,
) -> None:
    radio = SimpleNamespace(connected=True, capabilities={"data_mode"})
    server = WebServer(radio, WebConfig())
    server.command_queue.put = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    generation = server.command_state_store.provider_generation
    server.command_service.apply_observation(_observation(path, confirmed, generation))

    before_public = server.build_public_state()
    before_ws = server.build_state_update_envelope(force_full=True)["data"]
    before_snapshot = server.command_state_store.snapshot()

    result = await server.command_service.execute(
        command_intent_from_request(
            name,
            params,
            source="websocket",
            command_id=f"a01-{name}",
        )
    )

    after_command = server.command_state_store.snapshot()
    assert result.executor_result.observations == ()
    assert after_command.state_revision == before_snapshot.state_revision
    assert after_command.observation_seq == before_snapshot.observation_seq
    assert after_command.field(path).value == confirmed
    assert server.build_public_state() == before_public
    assert server.build_state_update_envelope(force_full=True)["data"] == before_ws

    changes = server.command_service.apply_observation(
        _observation(path, requested, generation)
    )

    after_provider = server.command_state_store.snapshot()
    assert changes.observed_paths == (path,)
    assert after_provider.state_revision == before_snapshot.state_revision + 1
    assert after_provider.observation_seq == before_snapshot.observation_seq + 1
    assert after_provider.field(path).value == requested
    assert public_value(server.build_public_state()) == requested
    assert public_value(server.build_state_update_envelope(force_full=True)["data"]) == requested


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("name", "params", "path", "confirmed", "_requested", "_public_value"),
    _CASES,
)
async def test_failed_enqueue_changes_no_radio_truth(
    name: str,
    params: dict[str, object],
    path: FieldPath,
    confirmed: object,
    _requested: object,
    _public_value,
) -> None:
    radio = SimpleNamespace(connected=True, capabilities={"data_mode"})
    server = WebServer(radio, WebConfig())
    generation = server.command_state_store.provider_generation
    server.command_service.apply_observation(_observation(path, confirmed, generation))

    def reject(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("enqueue rejected")

    server.command_queue.put = reject  # type: ignore[method-assign]
    before_public = server.build_public_state()
    before_ws = server.build_state_update_envelope(force_full=True)["data"]
    before_snapshot = server.command_state_store.snapshot()

    with pytest.raises(RuntimeError, match="enqueue rejected"):
        await server.command_service.execute(
            command_intent_from_request(
                name,
                params,
                source="websocket",
                command_id=f"a01-failed-{name}",
            )
        )

    after = server.command_state_store.snapshot()
    assert after.state_revision == before_snapshot.state_revision
    assert after.observation_seq == before_snapshot.observation_seq
    assert after.field(path).value == confirmed
    assert server.build_public_state() == before_public
    assert server.build_state_update_envelope(force_full=True)["data"] == before_ws
