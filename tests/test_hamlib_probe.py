"""Tests for safe read-only Hamlib probing and candidate ranking."""

from __future__ import annotations

import asyncio

import pytest

from fake_rigctld import FakeRigctldBehavior, FakeRigctldServer, FakeRigctldState
from rigplane.backends.hamlib_models import HamlibModelCatalog, HamlibModelMetadata
from rigplane.discovery import (
    HamlibProbeTarget,
    ProbeOptions,
    probe_hamlib_rigctld_targets,
    rank_hamlib_probe,
)

_ALLOWED_PROBE_COMMANDS = {r"\get_info", "f", "m"}
_FORBIDDEN_PROBE_COMMANDS = {
    "t",
    "T",
    "F",
    "M",
    "V",
    "L",
    "U",
    "S",
    "w",
    r"\dump_state",
}


def _catalog(*models: HamlibModelMetadata) -> HamlibModelCatalog:
    return HamlibModelCatalog(models={model.model_id: model for model in models})


def test_disabled_probe_options_do_no_io() -> None:
    def transport_factory(**_kwargs: object) -> object:
        raise AssertionError("disabled probe must not create a transport")

    result = asyncio.run(
        probe_hamlib_rigctld_targets(
            [HamlibProbeTarget(host="127.0.0.1", port=4532)],
            options=ProbeOptions(enabled=False),
            _transport_factory=transport_factory,
        )
    )

    assert result == []


async def test_successful_probe_ranks_exact_identity_high_and_uses_read_only_commands() -> (
    None
):
    catalog = _catalog(
        HamlibModelMetadata(model_id=3073, name="Icom IC-7610", status="Stable")
    )
    state = FakeRigctldState(info="Model 3073 Icom IC-7610")

    async with FakeRigctldServer(state=state) as server:
        results = await probe_hamlib_rigctld_targets(
            [HamlibProbeTarget(host=server.host, port=server.port, model_id=3073)],
            options=ProbeOptions(enabled=True, command_timeout=0.1),
            catalog=catalog,
        )

    assert len(results) == 1
    candidate = results[0].candidates[0]
    assert candidate.confidence == "high"
    assert candidate.suggested_backend == "hamlib"
    assert candidate.suggested_model == "3073 Icom IC-7610"
    assert candidate.safe_next_action == "read_only_probe_confirmed"
    assert {record.operation for record in results[0].audit} == {
        "read_info",
        "read_frequency",
        "read_mode",
    }
    assert set(server.commands_seen) <= _ALLOWED_PROBE_COMMANDS
    assert not (set(server.commands_seen) & _FORBIDDEN_PROBE_COMMANDS)


def test_ranking_returns_multiple_confirmation_candidates_when_ambiguous() -> None:
    catalog = _catalog(
        HamlibModelMetadata(model_id=3073, name="Icom IC-7610"),
        HamlibModelMetadata(model_id=3074, name="Icom IC-7600"),
    )

    candidates = rank_hamlib_probe(
        target=HamlibProbeTarget(host="private-radio.lan", port=4532),
        catalog=catalog,
        info_text="Icom",
        frequency_readable=True,
        mode_readable=True,
    )

    assert [candidate.suggested_model for candidate in candidates] == [
        "3073 Icom IC-7610",
        "3074 Icom IC-7600",
    ]
    assert {candidate.confidence for candidate in candidates} == {"medium"}
    assert {candidate.safe_next_action for candidate in candidates} == {"confirm_model"}


def test_ranking_degrades_to_low_when_catalog_is_unavailable() -> None:
    candidates = rank_hamlib_probe(
        target=HamlibProbeTarget(host="127.0.0.1", port=4532, model_id=9999),
        catalog=HamlibModelCatalog(
            models={},
            degraded_reason="hamlib model list unavailable: tool not found",
        ),
        info_text="Model 9999 Example Rig",
        frequency_readable=True,
        mode_readable=True,
    )

    assert len(candidates) == 1
    assert candidates[0].confidence == "low"
    assert candidates[0].safe_next_action == "manual_configuration_required"


async def test_probe_handles_unsupported_get_info_and_malformed_reads() -> None:
    behavior = FakeRigctldBehavior(
        unsupported_commands={r"\get_info"},
        malformed_responses={"f": b"PRIVATE_FREQUENCY=14074000\n", "m": b"USB\nbad\n"},
    )

    async with FakeRigctldServer(behavior=behavior) as server:
        results = await probe_hamlib_rigctld_targets(
            [HamlibProbeTarget(host=server.host, port=server.port)],
            options=ProbeOptions(enabled=True, command_timeout=0.1),
            catalog=_catalog(),
        )

    assert len(results) == 1
    assert results[0].candidates[0].confidence == "low"
    assert {record.status for record in results[0].audit} == {
        "unsupported",
        "malformed",
    }
    assert set(server.commands_seen) <= _ALLOWED_PROBE_COMMANDS


async def test_probe_timeout_audit_is_redacted() -> None:
    behavior = FakeRigctldBehavior(command_delays={"f": 0.2})

    async with FakeRigctldServer(behavior=behavior) as server:
        port = server.port
        results = await probe_hamlib_rigctld_targets(
            [HamlibProbeTarget(host=server.host, port=server.port)],
            options=ProbeOptions(enabled=True, command_timeout=0.01),
            catalog=_catalog(),
        )

    audit_text = repr(results[0].audit)
    assert "timeout" in audit_text
    assert server.host not in audit_text
    assert str(port) not in audit_text
    assert "14074000" not in audit_text
    assert r"\get_info" not in audit_text
    assert "PRIVATE" not in audit_text


async def test_probe_cancellation_closes_transport() -> None:
    behavior = FakeRigctldBehavior(command_delays={"f": 1.0})

    async with FakeRigctldServer(behavior=behavior) as server:
        task = asyncio.create_task(
            probe_hamlib_rigctld_targets(
                [HamlibProbeTarget(host=server.host, port=server.port)],
                options=ProbeOptions(enabled=True, command_timeout=2.0),
                catalog=_catalog(),
            )
        )
        await asyncio.sleep(0.05)
        task.cancel()

        with pytest.raises(asyncio.CancelledError):
            await task

    assert set(server.commands_seen) <= _ALLOWED_PROBE_COMMANDS


async def test_probe_bounds_concurrency_and_serializes_same_target() -> None:
    active_total = 0
    max_total = 0
    active_by_target: dict[tuple[str, int], int] = {}
    max_by_target: dict[tuple[str, int], int] = {}
    commands: list[str] = []

    class TrackingTransport:
        def __init__(self, *, host: str, port: int, timeout: float) -> None:
            self.host = host
            self.port = port
            self.timeout = timeout
            self.closed = False
            self.connected = False

        async def connect(self) -> None:
            nonlocal active_total, max_total
            key = (self.host, self.port)
            active_total += 1
            active_by_target[key] = active_by_target.get(key, 0) + 1
            max_total = max(max_total, active_total)
            max_by_target[key] = max(
                max_by_target.get(key, 0),
                active_by_target[key],
            )
            self.connected = True

        async def query(self, command: str, *, response_lines: int) -> list[str]:
            commands.append(command)
            await asyncio.sleep(0.01)
            if command == r"\get_info":
                return ["Model 3073 Icom IC-7610"]
            if command == "f":
                return ["14074000"]
            if command == "m":
                return ["USB", "2400"]
            raise AssertionError(f"unexpected command {command}")

        async def close(self) -> None:
            nonlocal active_total
            if self.closed or not self.connected:
                return
            key = (self.host, self.port)
            self.closed = True
            self.connected = False
            active_total -= 1
            active_by_target[key] -= 1

    catalog = _catalog(HamlibModelMetadata(model_id=3073, name="Icom IC-7610"))

    results = await probe_hamlib_rigctld_targets(
        [
            HamlibProbeTarget(host="target-a.example", port=4532, model_id=3073),
            HamlibProbeTarget(host="target-a.example", port=4532, model_id=3073),
            HamlibProbeTarget(host="target-b.example", port=4532, model_id=3073),
        ],
        options=ProbeOptions(enabled=True, max_concurrency=2, command_timeout=0.2),
        catalog=catalog,
        _transport_factory=TrackingTransport,
    )

    assert len(results) == 3
    assert max_total <= 2
    assert max_by_target[("target-a.example", 4532)] == 1
    assert set(commands) <= _ALLOWED_PROBE_COMMANDS
