"""HTTP ingress contracts for the composed managed-transmit authority."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass

import pytest

from rigplane.core.state_pipeline_contracts import Observation, SourceMetadata
from rigplane.core.tx_observation import OBSERVED_PTT_PATH, ObservedPtt
from rigplane.runtime.managed_tx_authority import ManagedTxProjection
from rigplane.runtime.managed_tx_config import ManagedTxTotConfig
from rigplane.runtime.managed_tx_state import (
    ManagedTxIntent,
    ManagedTxIntentKind,
    ManagedTxOutcome,
    ManagedTxState,
    ReleasePlan,
)
from rigplane.web.server import WebConfig, WebServer


class _Writer:
    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    @property
    def status(self) -> int:
        return int(self.buffer.split(b" ", 2)[1])

    @property
    def payload(self) -> dict[str, object]:
        body = self.buffer.split(b"\r\n\r\n", 1)[1]
        return json.loads(body or b"{}")


def _reader(payload: dict[str, object]) -> tuple[asyncio.StreamReader, dict[str, str]]:
    body = json.dumps(payload).encode()
    reader = asyncio.StreamReader()
    reader.feed_data(body)
    reader.feed_eof()
    return reader, {"content-length": str(len(body))}


@dataclass
class _Submission:
    outcome: ManagedTxOutcome
    settlement_waits: int = 0

    async def wait_settlement(self) -> None:
        self.settlement_waits += 1
        raise AssertionError("HTTP admission waited for provider settlement")


class _Authority:
    def __init__(self) -> None:
        self.calls: list[object] = []
        self.submissions: list[_Submission] = []
        self.configured_tot_seconds: float | None = 180.0
        self.next_outcome = ManagedTxOutcome.ACCEPTED

    async def snapshot(self) -> ManagedTxProjection:
        self.calls.append("snapshot")
        return ManagedTxProjection(
            ManagedTxState(
                intent=ManagedTxIntent(ManagedTxIntentKind.TRANSMIT),
                release_plan=ReleasePlan.PTT_RELEASE,
                tx_started_at_monotonic=10.0,
                tot_deadline_monotonic=20.0,
            ),
            self.configured_tot_seconds,
            5.25,
            provider_generation=7,
        )

    async def submit_transmit_on(self) -> _Submission:
        self.calls.append("transmit_on")
        submission = _Submission(self.next_outcome)
        self.submissions.append(submission)
        return submission

    async def submit_force_off(self) -> _Submission:
        self.calls.append("force_off")
        submission = _Submission(self.next_outcome)
        self.submissions.append(submission)
        return submission

    async def set_tot_seconds(self, value: object) -> ManagedTxTotConfig:
        self.calls.append(("set_tot_seconds", value))
        if value == "invalid":
            raise ValueError("invalid TOT")
        self.configured_tot_seconds = None if value in (None, 0) else float(value)
        return ManagedTxTotConfig(self.configured_tot_seconds)


def _server(
    authority: _Authority | None,
    *,
    read_only: bool = False,
) -> WebServer:
    server = WebServer(
        None,
        WebConfig(host="127.0.0.1", port=0, read_only=read_only),
    )
    if authority is not None:
        server._production_managed_tx_port = type(  # noqa: SLF001
            "_Port", (), {"authority": authority}
        )()
    return server


def _observe(server: WebServer, value: ObservedPtt) -> None:
    now = time.monotonic()
    server.command_state_store.apply_current(
        Observation(
            path=OBSERVED_PTT_PATH,
            value=value,
            source=SourceMetadata(source="poll_response", provider="test"),
            timestamp_monotonic=now,
            max_age=30.0,
        )
    )


@pytest.mark.asyncio
async def test_get_projects_one_authority_snapshot_and_separate_observation() -> None:
    authority = _Authority()
    server = _server(authority)
    _observe(server, ObservedPtt.OFF)
    writer = _Writer()

    await server._handle_http(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        "GET",
        "/api/v1/managed-transmit",
    )

    assert writer.status == 200
    managed = writer.payload["managedTransmit"]
    assert isinstance(managed, dict)
    tot = managed["tot"]
    assert isinstance(tot, dict)
    expires_at = tot["expiresAt"]
    assert isinstance(expires_at, str)
    assert managed == {
        "status": "available",
        "intent": {"kind": "transmit"},
        "releaseRequired": True,
        "lastError": None,
        "lastActuation": None,
        "abortErrors": [],
        "tot": {
            "configuredSeconds": 180.0,
            "active": True,
            "remainingMs": 5250,
            "expiresAt": expires_at,
        },
    }
    assert writer.payload["txObservation"] == {"observedPtt": "off"}
    assert authority.calls == ["snapshot"]


@pytest.mark.asyncio
@pytest.mark.parametrize("observed", (ObservedPtt.OFF, ObservedPtt.ON))
async def test_transmit_on_returns_admission_without_observation_gating_or_settlement(
    observed: ObservedPtt,
) -> None:
    authority = _Authority()
    server = _server(authority)
    _observe(server, observed)
    reader, headers = _reader({"operation": "transmit_on"})
    writer = _Writer()

    await server._handle_http(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        "POST",
        "/api/v1/managed-transmit/command",
        headers=headers,
        reader=reader,
    )

    assert writer.status == 202
    assert writer.payload == {
        "ok": True,
        "operation": "transmit_on",
        "result": "accepted",
    }
    assert authority.calls == ["transmit_on"]
    assert authority.submissions[0].settlement_waits == 0


@pytest.mark.asyncio
async def test_rejected_transmit_admission_maps_to_conflict() -> None:
    authority = _Authority()
    authority.next_outcome = ManagedTxOutcome.REJECTED
    server = _server(authority)
    reader, headers = _reader({"operation": "transmit_on"})
    writer = _Writer()

    await server._handle_http(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        "POST",
        "/api/v1/managed-transmit/command",
        headers=headers,
        reader=reader,
    )

    assert writer.status == 409
    assert writer.payload == {
        "ok": False,
        "operation": "transmit_on",
        "result": "rejected",
    }
    assert authority.submissions[0].settlement_waits == 0


@pytest.mark.asyncio
async def test_force_off_is_unconditional_even_for_read_only_observed_rx() -> None:
    authority = _Authority()
    server = _server(authority, read_only=True)
    _observe(server, ObservedPtt.OFF)
    reader, headers = _reader({"operation": "force_off"})
    writer = _Writer()

    await server._handle_http(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        "POST",
        "/api/v1/managed-transmit/command",
        headers=headers,
        reader=reader,
    )

    assert writer.status == 202
    assert authority.calls == ["force_off"]
    assert authority.submissions[0].settlement_waits == 0


@pytest.mark.asyncio
async def test_read_only_refuses_transmit_on_before_authority_admission() -> None:
    authority = _Authority()
    server = _server(authority, read_only=True)
    reader, headers = _reader({"operation": "transmit_on"})
    writer = _Writer()

    await server._handle_http(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        "POST",
        "/api/v1/managed-transmit/command",
        headers=headers,
        reader=reader,
    )

    assert writer.status == 403
    assert authority.calls == []


@pytest.mark.asyncio
async def test_tot_update_round_trips_through_the_authority_snapshot() -> None:
    authority = _Authority()
    server = _server(authority)
    reader, headers = _reader({"configuredSeconds": 45})
    writer = _Writer()

    await server._handle_http(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        "PUT",
        "/api/v1/managed-transmit/tot",
        headers=headers,
        reader=reader,
    )

    assert writer.status == 200
    managed = writer.payload["managedTransmit"]
    assert isinstance(managed, dict)
    tot = managed["tot"]
    assert isinstance(tot, dict)
    assert tot["configuredSeconds"] == 45.0
    assert authority.calls == [("set_tot_seconds", 45), "snapshot"]


@pytest.mark.asyncio
async def test_missing_composition_projects_unavailable_and_rejects_writes() -> None:
    server = _server(None)
    snapshot_writer = _Writer()

    await server._handle_http(  # noqa: SLF001
        snapshot_writer,  # type: ignore[arg-type]
        "GET",
        "/api/v1/managed-transmit",
    )

    assert snapshot_writer.status == 200
    assert snapshot_writer.payload["managedTransmit"] == {
        "status": "unavailable",
        "reason": "authority_not_composed",
    }
    assert snapshot_writer.payload["txObservation"] == {"observedPtt": "unknown"}

    for path, method, payload in (
        ("/api/v1/managed-transmit/command", "POST", {"operation": "force_off"}),
        ("/api/v1/managed-transmit/tot", "PUT", {"configuredSeconds": 45}),
    ):
        reader, headers = _reader(payload)
        writer = _Writer()
        await server._handle_http(  # noqa: SLF001
            writer,  # type: ignore[arg-type]
            method,
            path,
            headers=headers,
            reader=reader,
        )
        assert writer.status == 503
        assert writer.payload["error"] == "managed_tx_unavailable"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    (
        {"operation": "ptt_on"},
        {"operation": ["force_off"]},
        {"operation": "force_off", "observedPtt": "on"},
        {},
    ),
)
async def test_command_body_accepts_only_latched_transmit_and_force_off(
    payload: dict[str, object],
) -> None:
    authority = _Authority()
    server = _server(authority)
    reader, headers = _reader(payload)
    writer = _Writer()

    await server._handle_http(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        "POST",
        "/api/v1/managed-transmit/command",
        headers=headers,
        reader=reader,
    )

    assert writer.status == 400
    assert writer.payload["error"] == "invalid_request"
    assert authority.calls == []
