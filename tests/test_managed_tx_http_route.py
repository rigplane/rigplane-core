"""HTTP ingress contracts for the composed managed-transmit authority."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from rigplane.backends.ic705 import Ic705SerialRadio
from rigplane.backends.yaesu_cat import YaesuCatRadio
from rigplane.core import serial_open
from rigplane.core.exceptions import ConnectionError as RadioConnectionError
from rigplane.core.state_pipeline_contracts import Observation, SourceMetadata
from rigplane.core.tx_observation import (
    OBSERVED_PTT_PATH,
    ObservedPtt,
    project_observed_ptt,
)
from rigplane.runtime.managed_tx_authority import ManagedTxProjection
from rigplane.runtime.managed_tx_composition import (
    ManagedTxComposition,
    install_managed_tx_composition,
)
from rigplane.runtime.managed_tx_config import ManagedTxTotConfig
from rigplane.runtime.managed_tx_state import (
    ActuationOperation,
    ManagedTxIntent,
    ManagedTxIntentKind,
    ManagedTxOutcome,
    ManagedTxState,
    ReleasePlan,
)
from rigplane.testing.timing import virtual_clock
from rigplane.web.server import WebConfig, WebServer
from rigplane.web.web_startup import attach_managed_tx_composition
from test_managed_tx_production_composition import LegacyRadio, RecordingActuator
from test_yaesu_cat_transport import FakeStreamWriter


OWNER_TOKEN = "1" * 64


class _Writer:
    def __init__(self, peer: str = "127.0.0.1", *, api_channel: bool = False) -> None:
        self.buffer = bytearray()
        self.peer = peer
        self.api_channel = api_channel

    def get_extra_info(self, name: str, default: object = None) -> object:
        if name == "peername":
            return self.peer, 12345
        if name == "rigplane_api_channel":
            return self.api_channel
        return default

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
        self.tot_error: OSError | None = None

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

    def start_transmit_on_submission(
        self,
        *,
        ready: asyncio.Future[None],
        expires_at_monotonic: float,
    ) -> asyncio.Task[_Submission]:
        self.calls.append("transmit_on")
        submission = _Submission(self.next_outcome)
        self.submissions.append(submission)

        async def complete() -> _Submission:
            await ready
            return submission

        del expires_at_monotonic
        return asyncio.create_task(complete())

    async def submit_force_off(self) -> _Submission:
        self.calls.append("force_off")
        submission = _Submission(self.next_outcome)
        self.submissions.append(submission)
        return submission

    async def set_tot_seconds(self, value: object) -> ManagedTxTotConfig:
        self.calls.append(("set_tot_seconds", value))
        if self.tot_error is not None:
            raise self.tot_error
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
        server.command_queue.bind_connection_generation(lambda: "test-connection")

        async def enqueue_managed_positive_tx(
            *,
            ready: asyncio.Future[None],
            submission: asyncio.Task[_Submission],
            **_kwargs: object,
        ) -> _Submission:
            server.command_queue.put_ordered(
                None,
                positive_tx_ready=ready,
                positive_tx_submission=submission,
            )
            ready.set_result(None)
            return await submission

        server.enqueue_managed_positive_tx = enqueue_managed_positive_tx  # type: ignore[method-assign]
    return server


def _observe(server: WebServer, value: ObservedPtt) -> None:
    # Stamp the evidence with the store's own freshness time, not a wall-clock read.
    now = server.command_state_store.snapshot().generated_at_monotonic
    server.command_state_store.apply_current(
        Observation(
            path=OBSERVED_PTT_PATH,
            value=value,
            source=SourceMetadata(source="poll_response", provider="test"),
            timestamp_monotonic=now,
            max_age=30.0,
        )
    )


@pytest.mark.parametrize(
    ("scenario", "detach", "expected_disposition", "expected_result"),
    [
        ("never_attached", True, None, "pending"),
        ("never_attached", False, None, "pending"),
        ("remote_force_off", False, None, "pending"),
        ("remote_detach", True, None, "pending"),
        ("remote_controller_detach", True, None, "pending"),
        ("api_channel_detach", True, None, "pending"),
        ("keyed", True, None, "accepted"),
        ("release_debt", True, None, "pending"),
        ("attached_ptt_unknown", True, None, "accepted"),
        ("retired_provider", True, None, "pending"),
        ("observed_tx_without_provider", True, None, "pending"),
    ],
)
@virtual_clock
async def test_detach_force_off_uses_attachment_history(
    clock, tmp_path, scenario, detach, expected_disposition, expected_result
) -> None:
    actuator = RecordingActuator()
    radio = SimpleNamespace(
        capabilities=(),
        conn_state="reconnecting",
        last_error="serial_not_found",
        serial_open_error="serial_not_found",
        control_connected=False,
        set_ptt=LegacyRadio().set_ptt,
    )
    composition = ManagedTxComposition(
        actuator, config_path=tmp_path / "managed-tx.json"
    )
    install_managed_tx_composition(radio, composition)
    server = WebServer(
        radio, WebConfig(host="127.0.0.1", port=0, station_owner_token=OWNER_TOKEN)
    )
    attach_managed_tx_composition(server, composition)
    authority = composition.authority
    await composition.bind_state_store(server.command_state_store)
    if scenario in {"keyed", "attached_ptt_unknown", "retired_provider"}:
        await composition.transport_ready(radio)
    if scenario == "retired_provider":
        await composition.transport_unavailable(radio)
    if scenario == "keyed":
        await (await authority.submit_transmit_on()).wait_settlement()
    if scenario in {"keyed", "observed_tx_without_provider"}:
        _observe(server, ObservedPtt.ON)
    if scenario == "release_debt":
        await (await authority.submit_force_off()).wait_settlement()
    actuator.operations.clear()

    reader, headers = _reader({"operation": "force_off"})
    if detach:
        headers["x-rigplane-release-for-detach"] = "1"
    if not scenario.startswith("remote_"):
        headers["x-rigplane-station-owner"] = OWNER_TOKEN
    if scenario == "remote_controller_detach":
        headers["x-rigplane-controller"] = "0" * 64
    remote_peer = scenario in {"remote_force_off", "remote_detach"}
    writer = _Writer(
        "192.0.2.1" if remote_peer else "127.0.0.1",
        api_channel=scenario == "api_channel_detach",
    )
    if scenario.startswith("remote_"):
        server._controller.mode = "remote"
    termination = asyncio.Event()
    try:
        await server._handle_http(
            writer,  # type: ignore[arg-type]
            "POST",
            "/api/v1/managed-transmit/command",
            headers=headers,
            reader=reader,
        )
        await clock.elapse(0)
        assert writer.status == 202
        assert writer.payload.get("releaseDisposition") == expected_disposition
        assert writer.payload["result"] == expected_result
        assert (await authority.snapshot()).state.release_required is (
            expected_result == "pending"
        )
        assert (ActuationOperation.FORCE_RECEIVE in actuator.operations) is (
            scenario in {"keyed", "attached_ptt_unknown"}
        )
        if scenario.startswith(("remote_", "api_channel_")):
            async with composition.transport_open_attempt():
                pass
    finally:
        termination.set()
        await composition.shutdown(termination)


@pytest.mark.parametrize(
    "backend", [Ic705SerialRadio, YaesuCatRadio], ids=["icom", "yaesu"]
)
@pytest.mark.parametrize("retry_state", ["idle", "open-in-flight", "cancelled"])
@virtual_clock
async def test_detach_force_off_during_first_serial_open(
    clock, tmp_path, monkeypatch, backend, retry_state
) -> None:
    port_present = False
    port_opened = asyncio.Event()
    finish_open = asyncio.Event()
    real_open = serial_open._open_with_idle_lines

    async def open_port(**kwargs):
        if not port_present:
            return await real_open(**kwargs)
        wire = FakeStreamWriter()
        # The fake port is open, but the backend is still awaiting its reader/writer.
        port_opened.set()
        await finish_open.wait()
        return asyncio.StreamReader(), wire

    monkeypatch.setattr(serial_open, "_open_with_idle_lines", open_port)
    radio = backend(device=str(tmp_path / "missing-test-port"))
    composition = ManagedTxComposition(
        RecordingActuator(), config_path=tmp_path / "managed-tx.json"
    )
    install_managed_tx_composition(radio, composition)
    server = WebServer(
        radio, WebConfig(host="127.0.0.1", port=0, station_owner_token=OWNER_TOKEN)
    )
    attach_managed_tx_composition(server, composition)
    await composition.bind_state_store(server.command_state_store)
    reopen = (
        radio.soft_reconnect if isinstance(radio, Ic705SerialRadio) else radio.connect
    )
    retry = None
    try:
        with pytest.raises(RadioConnectionError):
            await radio.connect()
        radio.start_reconnect_recovery()
        assert radio.serial_open_error.startswith("serial_not_found")
        port_present = True

        if retry_state != "idle":
            retry = asyncio.create_task(reopen())
            await clock.elapse(0)
            assert port_opened.is_set()
            assert not retry.done()
            assert radio.control_connected is False
            if retry_state == "cancelled":
                retry.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await retry
                port_present = False
                with pytest.raises(RadioConnectionError):
                    await reopen()

        assert (
            project_observed_ptt(server.command_state_store.snapshot())
            is ObservedPtt.UNKNOWN
        )
        reader, headers = _reader({"operation": "force_off"})
        headers["x-rigplane-release-for-detach"] = "1"
        headers["x-rigplane-station-owner"] = OWNER_TOKEN
        writer = _Writer()
        await server._handle_http(
            writer,  # type: ignore[arg-type]
            "POST",
            "/api/v1/managed-transmit/command",
            headers=headers,
            reader=reader,
        )
        assert writer.status == 202
        projection = await composition.authority.snapshot()
        assert projection.provider_generation is None
        assert projection.state.release_required is (retry_state != "idle")
        if retry_state != "idle":
            assert writer.payload["result"] == "pending"
            assert "releaseDisposition" not in writer.payload
            if retry_state == "open-in-flight":
                finish_open.set()
                await clock.elapse(0)
                assert radio.control_connected is True
                assert (await composition.authority.snapshot()).state.release_required
        else:
            assert writer.payload["result"] == "accepted"
            assert writer.payload["releaseDisposition"] == "not_applicable"
            retry = asyncio.create_task(reopen())
            await clock.elapse(0)
            assert not port_opened.is_set()
            with pytest.raises(RadioConnectionError, match="detaching binding"):
                retry.result()
    finally:
        if retry is not None:
            retry.cancel()
            await asyncio.gather(retry, return_exceptions=True)
        await radio.disconnect()
        termination = asyncio.Event()
        termination.set()
        await composition.shutdown(termination)


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
        "code": "other",
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
async def test_tot_persistence_failure_returns_stable_json_error() -> None:
    authority = _Authority()
    authority.tot_error = OSError("disk full")
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

    assert writer.status == 500
    assert writer.payload == {
        "error": "managed_tx_config_error",
        "message": "Managed transmit TOT configuration could not be saved",
    }
    assert authority.calls == [("set_tot_seconds", 45)]


@pytest.mark.asyncio
async def test_managed_transmit_snapshot_rejects_head_instead_of_sending_json() -> None:
    authority = _Authority()
    server = _server(authority)
    writer = _Writer()

    await server._handle_http(  # noqa: SLF001
        writer,  # type: ignore[arg-type]
        "HEAD",
        "/api/v1/managed-transmit",
    )

    assert writer.status == 405
    assert writer.payload == {}
    assert authority.calls == []


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
