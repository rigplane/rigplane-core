"""Managed-transmit public projection and web invalidation delivery tests."""

import asyncio
import time
from datetime import UTC, datetime, timedelta, timezone

import pytest

from rigplane.core._bounded_queue import BoundedQueue
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.tx_observation import OBSERVED_PTT_PATH, ObservedPtt
from rigplane.audio.bus import AudioBus
from rigplane.capabilities import CAP_AUDIO
from rigplane.runtime.managed_tx_authority import (
    ManagedTxAuthority,
    ManagedTxProjection,
)
from rigplane.runtime.managed_tx_config import ManagedTxTotConfig
from rigplane.runtime.managed_tx_effect_lane import ManagedTxEffectLane
from rigplane.runtime.managed_tx_fence import TxAbortFence
from rigplane.runtime.managed_tx_state import (
    AbortError,
    AbortOperation,
    ActuationDiagnostic,
    ActuationOperation,
    ActuationResult,
    ManagedTxIntent,
    ManagedTxIntentKind,
    ManagedTxState,
    ReleasePlan,
)
from rigplane.web.handlers.control import ControlHandler
from rigplane.web.managed_tx_view import build_managed_tx_view
from rigplane.web.server import WebConfig, WebServer


_SAMPLED_AT = datetime(2026, 9, 4, 12, 34, 56, 789_000, tzinfo=UTC)


def _projection(
    state: ManagedTxState | None = None,
    *,
    configured_tot_seconds: float | None = 180.0,
    remaining_tot_seconds: float | None = None,
) -> ManagedTxProjection:
    return ManagedTxProjection(
        state or ManagedTxState(),
        configured_tot_seconds,
        remaining_tot_seconds,
        provider_generation=123,
    )


def _active_state() -> ManagedTxState:
    return ManagedTxState(
        intent=ManagedTxIntent(ManagedTxIntentKind.TRANSMIT),
        release_plan=ReleasePlan.PTT_RELEASE,
        tx_started_at_monotonic=10.0,
        tot_deadline_monotonic=55.0,
    )


def test_ptt_snapshot_serializes_the_authority_state_and_separate_observation() -> None:
    state = ManagedTxState(
        intent=ManagedTxIntent.ptt("opaque-owner-token"),
        release_plan=ReleasePlan.PTT_RELEASE,
        tx_started_at_monotonic=10.0,
        tot_deadline_monotonic=55.0,
    )

    assert build_managed_tx_view(
        _projection(state, remaining_tot_seconds=42.5009),
        ObservedPtt.UNKNOWN,
        sampled_at=_SAMPLED_AT,
    ) == {
        "schemaVersion": 1,
        "sampledAt": "2026-09-04T12:34:56.789Z",
        "managedTransmit": {
            "status": "available",
            "intent": {"kind": "ptt", "owner": "opaque-owner-token"},
            "releaseRequired": True,
            "lastError": None,
            "lastActuation": None,
            "abortErrors": [],
            "tot": {
                "configuredSeconds": 180.0,
                "active": True,
                "remainingMs": 42500,
                "expiresAt": "2026-09-04T12:35:39.289Z",
            },
        },
        "txObservation": {"observedPtt": "unknown"},
    }


def test_rx_release_debt_is_not_inferred_from_observed_ptt() -> None:
    state = ManagedTxState(release_plan=ReleasePlan.FORCE_RELEASE)

    assert build_managed_tx_view(
        _projection(state), ObservedPtt.OFF, sampled_at=_SAMPLED_AT
    )["managedTransmit"] == {
        "status": "available",
        "intent": {"kind": "rx"},
        "releaseRequired": True,
        "lastError": None,
        "lastActuation": None,
        "abortErrors": [],
        "tot": {
            "configuredSeconds": 180.0,
            "active": False,
            "remainingMs": None,
            "expiresAt": None,
        },
    }


@pytest.mark.parametrize("observed_ptt", list(ObservedPtt))
def test_observation_changes_only_the_diagnostic_block(
    observed_ptt: ObservedPtt,
) -> None:
    view = build_managed_tx_view(_projection(), observed_ptt, sampled_at=_SAMPLED_AT)

    assert view["managedTransmit"] == {
        "status": "available",
        "intent": {"kind": "rx"},
        "releaseRequired": False,
        "lastError": None,
        "lastActuation": None,
        "abortErrors": [],
        "tot": {
            "configuredSeconds": 180.0,
            "active": False,
            "remainingMs": None,
            "expiresAt": None,
        },
    }
    assert view["txObservation"] == {"observedPtt": observed_ptt.value}


def test_diagnostics_preserve_normalized_values_and_hide_internal_tokens() -> None:
    state = ManagedTxState(
        intent=ManagedTxIntent(ManagedTxIntentKind.TRANSMIT),
        release_plan=ReleasePlan.PTT_RELEASE,
        tx_started_at_monotonic=10.0,
        last_actuation=ActuationDiagnostic(
            ActuationOperation.TRANSMIT_ON,
            ActuationResult.UNCERTAIN,
            "opaque-attempt-id",
        ),
        last_error="provider error text",
        abort_errors=(
            AbortError(AbortOperation.STOP_CW, "cw error"),
            AbortError(AbortOperation.STOP_TUNE, "tune error"),
        ),
    )

    view = build_managed_tx_view(
        _projection(state, configured_tot_seconds=None),
        ObservedPtt.ON,
        sampled_at=_SAMPLED_AT,
    )

    assert view["managedTransmit"] == {
        "status": "available",
        "intent": {"kind": "transmit"},
        "releaseRequired": True,
        "lastError": "provider error text",
        "lastActuation": {
            "operation": "transmit_on",
            "result": "uncertain",
            "attemptId": "opaque-attempt-id",
        },
        "abortErrors": [
            {"operation": "stop_cw", "error": "cw error"},
            {"operation": "stop_tune", "error": "tune error"},
        ],
        "tot": {
            "configuredSeconds": None,
            "active": False,
            "remainingMs": None,
            "expiresAt": None,
        },
    }
    assert "providerGeneration" not in str(view)
    assert "effectEpoch" not in str(view)


@pytest.mark.parametrize(
    ("state", "remaining_tot_seconds"),
    [
        (_active_state(), None),
        (ManagedTxState(), 1.0),
    ],
)
def test_mismatched_tot_deadline_and_remaining_time_are_rejected(
    state: ManagedTxState, remaining_tot_seconds: float | None
) -> None:
    with pytest.raises(ValueError, match="TOT deadline and remaining time disagree"):
        build_managed_tx_view(
            _projection(state, remaining_tot_seconds=remaining_tot_seconds),
            ObservedPtt.UNKNOWN,
            sampled_at=_SAMPLED_AT,
        )


@pytest.mark.parametrize(
    ("remaining_tot_seconds", "remaining_ms", "expires_at"),
    [
        (0.0, 0, "2026-09-04T12:34:56.789Z"),
        (-0.0001, 0, "2026-09-04T12:34:56.789Z"),
        (604800.9999, 604800999, "2026-09-11T12:34:57.788Z"),
    ],
)
def test_active_tot_clamps_expired_time_and_preserves_large_remaining_time(
    remaining_tot_seconds: float, remaining_ms: int, expires_at: str
) -> None:
    tot = build_managed_tx_view(
        _projection(_active_state(), remaining_tot_seconds=remaining_tot_seconds),
        ObservedPtt.UNKNOWN,
        sampled_at=_SAMPLED_AT,
    )["managedTransmit"]["tot"]  # type: ignore[index]

    assert tot == {
        "configuredSeconds": 180.0,
        "active": True,
        "remainingMs": remaining_ms,
        "expiresAt": expires_at,
    }


def test_naive_sample_time_is_rejected() -> None:
    with pytest.raises(ValueError, match="sampled_at must be timezone-aware"):
        build_managed_tx_view(
            _projection(),
            ObservedPtt.UNKNOWN,
            sampled_at=datetime(2026, 9, 4, 12, 34, 56, 789_000),
        )


def test_non_utc_sample_time_converts_sample_and_expiry_to_utc() -> None:
    view = build_managed_tx_view(
        _projection(_active_state(), remaining_tot_seconds=1.0),
        ObservedPtt.UNKNOWN,
        sampled_at=datetime(
            2026,
            9,
            4,
            8,
            34,
            56,
            789_000,
            tzinfo=timezone(-timedelta(hours=4)),
        ),
    )

    assert view["sampledAt"] == "2026-09-04T12:34:56.789Z"
    assert view["managedTransmit"]["tot"]["expiresAt"] == "2026-09-04T12:34:57.789Z"  # type: ignore[index]


_INVALIDATION = {"type": "event", "name": "managed_transmit_changed", "data": {}}


def _drain(queue: BoundedQueue[dict]) -> list[dict]:
    items: list[dict] = []
    while not queue.empty():
        items.append(queue.get_nowait())
    return items


def _web_server() -> WebServer:
    return WebServer(None, WebConfig(host="127.0.0.1", port=0, radio_model="IC-7610"))


async def test_managed_tx_invalidation_fans_out_coalesced_to_control_queues() -> None:
    server = _web_server()
    first: BoundedQueue[dict] = BoundedQueue(maxsize=100)
    second: BoundedQueue[dict] = BoundedQueue(maxsize=100)
    server._control_event_queues.update({first, second})

    server._on_managed_tx_changed()
    server._on_managed_tx_changed()

    assert _drain(first) == [_INVALIDATION]
    assert _drain(second) == [_INVALIDATION]


async def test_managed_tx_invalidation_survives_a_full_queue() -> None:
    server = _web_server()
    queue: BoundedQueue[dict] = BoundedQueue(maxsize=2)
    filler = {"type": "notification", "message": "filler"}
    queue.put_nowait(dict(filler))
    queue.put_nowait(dict(filler))
    server._control_event_queues.add(queue)

    server._on_managed_tx_changed()

    delivered = _drain(queue)
    assert _INVALIDATION in delivered
    assert len(delivered) == 2

    server._stopping = True
    server._on_managed_tx_changed()
    assert _drain(queue) == []


async def test_control_handler_forwards_managed_transmit_changed() -> None:
    handler = ControlHandler.__new__(ControlHandler)
    handler._event_queue = BoundedQueue(maxsize=10)
    handler._subscribed_streams = set()
    sent: list[dict] = []

    async def capture(event: dict) -> None:
        sent.append(event)

    handler._send_json = capture  # type: ignore[method-assign]
    loop = asyncio.get_running_loop()
    task = loop.create_task(handler._event_sender_loop())
    try:
        handler._event_queue.put_nowait(dict(_INVALIDATION))
        handler._event_queue.put_nowait(
            {"type": "event", "name": "freq_changed", "data": {"freq": 1}}
        )
        handler._event_queue.put_nowait({"type": "notification", "message": "hello"})
        for _ in range(4):
            await asyncio.sleep(0)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    assert sent == [_INVALIDATION, {"type": "notification", "message": "hello"}]


_SOURCE = SourceMetadata(source="poll_response", provider="test")


def _observe_ptt(server: WebServer, value: object) -> None:
    server.command_state_store.apply_current(
        Observation(
            path=OBSERVED_PTT_PATH,
            value=value,
            source=_SOURCE,
            timestamp_monotonic=time.monotonic(),
            max_age=60.0,
        )
    )


def _observe_public_ptt(server: WebServer, value: object) -> None:
    server.command_state_store.apply_current(
        Observation(
            path=FieldPath.global_("tx_state", "ptt"),
            value=value,
            source=_SOURCE,
            timestamp_monotonic=time.monotonic(),
            max_age=60.0,
        )
    )


def _broadcast(server: WebServer) -> None:
    server._last_state_broadcast = 0.0  # noqa: SLF001
    server._broadcast_state_update(force=True)  # noqa: SLF001


def _managed_tx_events(queue: BoundedQueue[dict]) -> list[dict]:
    return [
        event
        for event in _drain(queue)
        if event.get("name") == "managed_transmit_changed"
    ]


def _announcing_server() -> tuple[WebServer, BoundedQueue[dict]]:
    server = _web_server()
    queue: BoundedQueue[dict] = BoundedQueue(maxsize=100)
    server._control_event_queues.add(queue)  # noqa: SLF001
    return server, queue


def test_broadcast_seeds_baseline_without_emitting() -> None:
    server, queue = _announcing_server()
    _observe_ptt(server, ObservedPtt.OFF)

    _broadcast(server)

    assert _managed_tx_events(queue) == []


def test_broadcast_announces_off_to_unknown_with_public_ptt_unchanged() -> None:
    server, queue = _announcing_server()
    _observe_ptt(server, ObservedPtt.OFF)
    _observe_public_ptt(server, False)
    _broadcast(server)
    assert _managed_tx_events(queue) == []

    _observe_ptt(server, ObservedPtt.UNKNOWN)
    _observe_public_ptt(server, False)
    _broadcast(server)

    assert _managed_tx_events(queue) == [_INVALIDATION]


def test_broadcast_announces_off_to_on() -> None:
    server, queue = _announcing_server()
    _observe_ptt(server, ObservedPtt.OFF)
    _broadcast(server)
    assert _managed_tx_events(queue) == []

    _observe_ptt(server, ObservedPtt.ON)
    _broadcast(server)

    assert _managed_tx_events(queue) == [_INVALIDATION]


class _GateRadio:
    """Audio-capable radio whose state store is the server's own store."""

    def __init__(self, store: object) -> None:
        self.capabilities = {CAP_AUDIO}
        self.state_store = store
        self.audio_bus = AudioBus(self)
        self.audio_codec = None
        self.audio_sample_rate = 48000

    async def start_audio_rx_opus(self, *_args: object, **_kwargs: object) -> None:
        return None

    async def stop_audio_rx_opus(self) -> None:
        return None

    async def start_audio_tx_pcm(self) -> None:
        return None

    async def stop_audio_tx_pcm(self) -> None:
        return None

    async def push_audio_tx_pcm(self, _frame: bytes) -> None:
        return None

    async def push_audio_tx_opus(self, _frame: bytes) -> None:
        return None


class _GatePort:
    def __init__(self, managed: ManagedTxAuthority) -> None:
        self.authority = managed


class _AcceptingActuator:
    async def actuate(
        self,
        token: object,
        operation: object,
        *,
        is_current: object,
    ) -> object:
        del token, operation, is_current
        from rigplane.runtime.managed_tx_state import ActuationResult

        return ActuationResult.ACCEPTED


class _MemoryConfig:
    def __init__(self) -> None:
        self._config = ManagedTxTotConfig(10.0)

    @property
    def config(self) -> ManagedTxTotConfig:
        return self._config

    def set_timeout_seconds(self, value: object) -> ManagedTxTotConfig:
        del value
        return self._config


def _gate_authority() -> ManagedTxAuthority:
    return ManagedTxAuthority(
        ManagedTxEffectLane(_AcceptingActuator()),
        _MemoryConfig(),  # type: ignore[arg-type]
        TxAbortFence(),
        provider_generation=7,
    )


def _gate_server(managed: ManagedTxAuthority | None) -> WebServer:
    server = _web_server()
    radio = _GateRadio(server.command_state_store)
    server._radio = radio
    if managed is not None:
        server._production_managed_tx_port = _GatePort(managed)  # type: ignore[assignment]
    return server


async def test_server_gate_is_closed_when_managed_and_observed_off() -> None:
    managed = _gate_authority()
    try:
        server = _gate_server(managed)
        _observe_ptt(server, ObservedPtt.OFF)
        assert await server._bridge_tx_gate_open() is False
    finally:
        await managed.close()


async def test_server_gate_opens_when_the_lease_is_keyed() -> None:
    managed = _gate_authority()
    try:
        server = _gate_server(managed)
        _observe_ptt(server, ObservedPtt.OFF)
        await managed.ptt_down("web")
        assert await server._bridge_tx_gate_open() is True
        await managed.ptt_up("web")
    finally:
        await managed.close()


async def test_server_gate_opens_when_observed_ptt_is_on() -> None:
    managed = _gate_authority()
    try:
        server = _gate_server(managed)
        _observe_ptt(server, ObservedPtt.ON)
        assert await server._bridge_tx_gate_open() is True
    finally:
        await managed.close()


async def test_server_gate_stays_open_without_managed_tx() -> None:
    server = _gate_server(None)
    _observe_ptt(server, ObservedPtt.OFF)
    assert await server._bridge_tx_gate_open() is True


async def test_managed_key_makes_the_tx_hint_true_without_observed_ptt() -> None:
    """MOR-2616: the web hint follows managed intent, not observed PTT."""

    managed = _gate_authority()
    try:
        server = _gate_server(managed)
        server._bind_managed_tx_hint(managed)
        await asyncio.sleep(0)
        assert server.tx_active_hint() is False
        await managed.ptt_down("web")
        await asyncio.sleep(0)
        assert server.tx_active_hint() is True
        await managed.ptt_up("web")
        await asyncio.sleep(0)
        assert server.tx_active_hint() is False
    finally:
        await managed.close()


def test_broadcast_emits_nothing_for_an_unchanged_projection() -> None:
    server, queue = _announcing_server()
    _observe_ptt(server, ObservedPtt.OFF)
    _broadcast(server)
    assert _managed_tx_events(queue) == []

    _observe_ptt(server, ObservedPtt.OFF)
    _broadcast(server)
    _broadcast(server)

    assert _managed_tx_events(queue) == []
