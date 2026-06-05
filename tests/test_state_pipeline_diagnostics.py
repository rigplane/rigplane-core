"""Baseline diagnostics for legacy state/revision/cache paths."""

from __future__ import annotations

from typing import Any

from rigplane.core._bounded_queue import BoundedQueue
from rigplane.core._state_cache import StateCache
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_diagnostics import StateDiagnosticsRecorder
from rigplane.radio_state import RadioState
from rigplane.runtime._civ_rx import CivRuntime
from rigplane.types import CivFrame
from rigplane.web.server import WebConfig, WebServer


class _FakeCivHost:
    def __init__(self, diagnostics: StateDiagnosticsRecorder) -> None:
        self._radio_state = RadioState()
        self._state_cache = StateCache()
        self._on_state_change = self._on_notify
        self._notifications: list[tuple[str, dict[str, Any]]] = []
        self._state_diagnostics = diagnostics
        self._last_freq_hz = None
        self._last_mode = None
        self._last_vfo = None
        self._filter_width = None

    def _on_notify(self, name: str, data: dict[str, Any]) -> None:
        self._notifications.append((name, data))


def test_state_diagnostics_are_inert_until_enabled() -> None:
    recorder = StateDiagnosticsRecorder()

    recorder.record("direct_state_write", "unit", field="main.s_meter")

    assert recorder.snapshot()["counts"] == {}
    assert recorder.events() == ()


def test_state_diagnostics_record_events_without_mutating_payloads() -> None:
    recorder = StateDiagnosticsRecorder(enabled=True)
    payload = {"field": "main.s_meter", "value": 42}

    event = recorder.record("direct_state_write", "unit", **payload)

    payload["value"] = 99
    assert event is not None
    assert event.kind == "direct_state_write"
    assert event.source == "unit"
    assert event.details == {"field": "main.s_meter", "value": 42}
    assert recorder.snapshot()["counts"] == {"direct_state_write": 1}


def test_civ_meter_write_records_diagnostic_without_notify_or_revision() -> None:
    diagnostics = StateDiagnosticsRecorder(enabled=True)
    host = _FakeCivHost(diagnostics)
    runtime = CivRuntime(host)  # type: ignore[arg-type]
    frame = CivFrame(
        to_addr=0xE0, from_addr=0x98, command=0x15, sub=0x02, data=b"\x00\x42"
    )

    runtime._update_state_cache_from_frame(frame)

    assert host._radio_state.main.s_meter == 42
    assert host._notifications == []
    snapshot = diagnostics.snapshot()
    assert snapshot["counts"] == {"direct_state_write": 1}
    assert diagnostics.events()[0].details["field_family"] == "meters"


def test_web_meter_write_delivers_state_store_revision_without_unrelated_trigger() -> (
    None
):
    server = WebServer(config=WebConfig(state_diagnostics=True))
    queue: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=8)
    server.register_control_event_queue(queue)

    server.command_state_store.apply(
        Observation(
            path=FieldPath.receiver("0", "meters", "s_meter"),
            value=42,
            source=SourceMetadata(source="test", provider="test"),
            timestamp_monotonic=1.0,
            max_age=0.5,
        )
    )
    server.state_diagnostics.record(
        "direct_state_write",
        "test",
        field="main.s_meter",
        field_family="meters",
        revision=server.build_public_state()["revision"],
    )

    payload = server.build_public_state()
    assert payload["main"]["sMeter"] == 42
    assert payload["revision"] == 1
    assert payload["stateRevision"] == 1
    assert payload["freshnessRevision"] == 1

    server._broadcast_state_update()

    queued = queue.get_nowait()
    assert queued["type"] == "state_update"
    assert queued["data"]["type"] == "full"
    assert queued["data"]["stateRevision"] == 1
    assert queued["data"]["freshnessRevision"] == 1
    assert queued["data"]["data"]["main"]["sMeter"] == 42
    assert queue.empty()
    assert server.state_diagnostics.snapshot()["counts"] == {
        "direct_state_write": 1,
        "web_delivery_trigger": 1,
    }
