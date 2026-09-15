"""Reference injection only; no managed admission or queue scheduling claim."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from serial_stub import SerialMockRadio
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
from rigplane.core.state_store import StateStore
from rigplane.rigctld.contract import RigctldConfig
from rigplane.rigctld.handler import RigctldHandler
from rigplane.rigctld.protocol import parse_line
from rigplane.rigctld.server import RigctldServer
from rigplane.runtime._poller_types import CommandQueue


class _NoTruthProbe:
    def __bool__(self):
        raise AssertionError("managed references must be tested with is None")


class _RecordingRadio(SerialMockRadio):
    def __init__(self, store):
        super().__init__()
        self.state_store = store
        self.frequency_calls = []
        self.write_calls = []

    async def set_freq(self, freq, receiver=0):
        self.frequency_calls.append((freq, receiver))
        self.write_calls.append(("freq", freq, receiver))
        await super().set_freq(freq, receiver=receiver)

    async def set_mode(self, mode, filter_width=None, receiver=0):
        self.write_calls.append(("mode", mode, filter_width, receiver))
        await super().set_mode(mode, filter_width=filter_width, receiver=receiver)

    async def set_vfo(self, vfo):
        self.write_calls.append(("vfo", vfo))
        await super().set_vfo(vfo)

    async def set_split(self, on):
        self.write_calls.append(("split", on))
        await super().set_split(on)

    async def _send_civ_raw(self, frame):
        self.write_calls.append(("raw", frame))
        return None


class _DefaultExecutor:
    def __init__(self):
        self.calls = []

    async def execute(self, intent):
        self.calls.append(intent)
        return CommandExecutionResult()


@pytest.fixture
async def context():
    store = StateStore()
    store.apply_current(
        Observation(
            path=FieldPath.global_("tx_state", "ptt"),
            value=False,
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=asyncio.get_running_loop().time(),
            max_age=1e9,
        )
    )
    default = _DefaultExecutor()
    refs = dict(
        managed_tx_authority=_NoTruthProbe(),
        command_queue=CommandQueue(),
        command_service=CommandService(executor=default, state_store=store),
    )
    radio = _RecordingRadio(store)
    await radio.connect()
    try:
        yield SimpleNamespace(radio=radio, store=store, default=default, refs=refs)
    finally:
        await radio.disconnect()


@pytest.mark.parametrize(
    "present",
    [(1, 0, 0), (0, 1, 0), (0, 0, 1), (1, 1, 0)],
)
def test_partial_managed_context_is_rejected_before_bootstrap(present):
    names = ("managed_tx_authority", "command_queue", "command_service")
    refs = {
        name: _NoTruthProbe() if supplied else None
        for name, supplied in zip(names, present, strict=True)
    }
    for constructor in (RigctldServer, RigctldHandler):
        with pytest.raises(ValueError, match="together"):
            constructor(object(), RigctldConfig(), **refs)


def test_managed_context_rejects_custom_handler():
    with pytest.raises(ValueError, match="_handler"):
        RigctldServer(
            object(),
            _handler=object(),
            managed_tx_authority=_NoTruthProbe(),
            command_queue=CommandQueue(),
            command_service=_NoTruthProbe(),
        )


async def test_server_start_forwards_exact_managed_references(context):
    server = RigctldServer(
        context.radio, RigctldConfig(host="127.0.0.1", port=0), **context.refs
    )
    try:
        await server.start()
        assert server._server.is_serving()
        handler = server._rig_handler
        for name, value in context.refs.items():
            assert getattr(server, "_" + name) is value
            assert getattr(handler, "_" + name) is value
        assert handler._state_store is context.store
    finally:
        await server.stop()
    assert server._server is None


async def test_handler_leaf_preserves_shared_service_default(context):
    handler = RigctldHandler(context.radio, RigctldConfig(), **context.refs)
    service = context.refs["command_service"]
    events = []
    unsubscribe = service.subscribe_lifecycle(events.append)
    try:
        response = await handler.execute(parse_line(b"F 14075000"), session_id="client")
        assert response.ok
        assert context.radio.frequency_calls == [(14_075_000, 0)]
        assert context.default.calls == []
        assert [event.state for event in events] == [
            "accepted",
            "queued",
            "sent",
            "acknowledged",
        ]
        assert len({event.command_id for event in events}) == 1
        assert all(event.source == "rigctld" for event in events)
        assert all(event.details.get("session_id") == "client" for event in events)
        intent = command_intent_from_request(
            "set_freq", {"freq_hz": 14_076_000}, source="rigctld", command_id="default"
        )
        await service.execute(intent)
        assert context.default.calls == [intent]
        assert context.radio.frequency_calls == [(14_075_000, 0)]
    finally:
        unsubscribe()


@pytest.mark.parametrize(
    ("wire", "expected_family"),
    [
        (b"F 14075000", "freq"),
        (b"M USB 2400", "mode"),
        (b"V VFOB", "vfo"),
        (b"S 0 VFOA", "split"),
        (b"w FE FE 98 E0 03 FD", "raw"),
    ],
)
async def test_managed_non_tuner_writes_bypass_legacy_rf_gates(
    context, monkeypatch, wire, expected_family
):
    context.store.apply_current(
        Observation(
            path=FieldPath.global_("tx_state", "ptt"),
            value=True,
            source=SourceMetadata(source="test", provider="tests"),
            timestamp_monotonic=asyncio.get_running_loop().time(),
            max_age=1e9,
        )
    )
    handler = RigctldHandler(context.radio, RigctldConfig(), **context.refs)
    legacy_defer = Mock(side_effect=AssertionError("managed write used defer gate"))
    legacy_rf = Mock(side_effect=AssertionError("managed write used observed RF gate"))
    monkeypatch.setattr(handler, "_defer_write_gate", legacy_defer)
    monkeypatch.setattr(handler, "_resolve_rigctld_rf_state", legacy_rf)

    response = await handler.execute(parse_line(wire), session_id="client")

    assert response.ok
    assert len(context.radio.write_calls) == 1
    assert context.radio.write_calls[0][0] == expected_family
    legacy_defer.assert_not_called()
    legacy_rf.assert_not_called()
    assert handler._command_service.lifecycle_events()[-1].state == "acknowledged"  # noqa: SLF001
