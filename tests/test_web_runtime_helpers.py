from __future__ import annotations

import json
from collections.abc import Callable
from itertools import permutations
from typing import Any

import pytest
from unittest.mock import MagicMock

from rigplane.web.runtime_helpers import (
    build_public_state_payload,
    build_public_state_payload_from_snapshot,
    classify_radio_health,
    radio_ready,
    runtime_capabilities,
)
from rigplane.web.server import WebServer
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.core.state_store import (
    FieldSnapshot,
    FreshnessClock,
    FreshnessState,
    StateSnapshot,
    StateStore,
)
from rigplane.core.tx_target import KnownTxTarget, TxTarget, UnknownTxTarget
from rigplane.core.types import ScopeFixedEdge
from rigplane.radio_state import RadioState


class _FakeWriter:
    """Minimal writer for capturing HTTP response (buffer, write, close, wait_closed)."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False
        self.wait_closed_called = False

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        self.wait_closed_called = True


class _FakeRadio:
    conn_state: str | None
    _last_civ_data_received: float | None
    _civ_ready_idle_timeout: float | None
    _has_connected_once: bool
    civ_stats: Callable[[], dict[str, int]] | None

    def __init__(
        self,
        *,
        caps: set[str] | None = None,
        connected: bool | None = True,
        radio_ready_flag: bool | None = True,
    ) -> None:
        self.capabilities = caps  # may be None or a set
        self.connected = connected
        self.radio_ready = radio_ready_flag
        self.control_connected = False
        self.model = "IC-TEST"
        self.conn_state = None
        self._last_civ_data_received = None
        self._civ_ready_idle_timeout = None
        self._has_connected_once = False
        self.civ_stats = None
        self.remote_control_unreachable = False


def _source(*, provider: str = "test") -> SourceMetadata:
    return SourceMetadata(
        source="poll_response",
        provider=provider,
        transport="fake",
        native_id="test",
    )


def _observation(
    path: FieldPath,
    value: Any,
    *,
    at: float,
    max_age: float | None = None,
    quality: tuple[str, ...] = ("confirmed",),
    provider: str = "test",
) -> Observation:
    return Observation(
        path=path,
        value=value,
        source=_source(provider=provider),
        timestamp_monotonic=at,
        max_age=max_age,
        quality=quality,
    )


def test_relative_unselected_vfo_projects_only_as_complete_public_object() -> None:
    values = {
        "freq_hz": 7_100_000,
        "mode": "LSB",
        "filter_num": 2,
        "data_mode": 0,
    }
    for order in permutations(values):
        store = StateStore()
        for index, name in enumerate(order):
            store.apply(
                _observation(
                    FieldPath.unselected("0", "freq_mode", name),
                    values[name],
                    at=10.0 + index,
                )
            )
            payload = build_public_state_payload_from_snapshot(
                store.snapshot(), radio=None, receiver_count=1
            )
            if index < len(values) - 1:
                assert "unselectedVfo" not in payload["main"]
                public_leaf = {
                    "freq_hz": "freqHz",
                    "mode": "mode",
                    "filter_num": "filterNum",
                    "data_mode": "dataMode",
                }[name]
                assert (
                    payload["fieldStatus"][f"main.unselectedVfo.{public_leaf}"][
                        "observed"
                    ]
                    is True
                )
            else:
                assert payload["main"]["unselectedVfo"] == {
                    "freqHz": 7_100_000,
                    "mode": "LSB",
                    "filterNum": 2,
                    "dataMode": 0,
                }


def test_relative_unselected_vfo_reset_removes_complete_public_object() -> None:
    store = StateStore()
    paths = [
        FieldPath.unselected("0", "freq_mode", name)
        for name in ("freq_hz", "mode", "filter_num", "data_mode")
    ]
    for path, value in zip(paths, (7_100_000, "LSB", 2, 0), strict=True):
        store.apply(_observation(path, value, at=10.0))

    before = build_public_state_payload_from_snapshot(
        store.snapshot(), radio=None, receiver_count=1
    )
    assert before["main"]["unselectedVfo"]["freqHz"] == 7_100_000

    store.discard(paths)
    after = build_public_state_payload_from_snapshot(
        store.snapshot(), radio=None, receiver_count=1
    )
    assert "unselectedVfo" not in after["main"]
    assert all(
        after["fieldStatus"][f"main.unselectedVfo.{leaf}"]["observed"] is False
        for leaf in ("freqHz", "mode", "filterNum", "dataMode")
    )


def test_web_server_publishes_single_receiver_main_from_topology_only() -> None:
    from rigplane.profiles import resolve_radio_profile

    store = StateStore()
    radio = MagicMock()
    radio.model = "IC-7300"
    radio.profile = resolve_radio_profile(model="IC-7300")
    radio.state_store = store
    radio.capabilities = set(radio.profile.capabilities)
    radio.managed_tx = None

    WebServer(radio)

    snapshot = store.snapshot()
    assert snapshot.field("global.slow_state.active").value == "MAIN"
    assert "global.tx_state.ptt" not in snapshot.as_dict()
    assert "receiver.0.vfo.active_slot" not in snapshot.as_dict()


def test_production_radio_poller_resets_vfo_identity_on_reconnect() -> None:
    from rigplane.profiles import resolve_radio_profile
    from rigplane.web.radio_poller import CommandQueue, RadioPoller

    store = StateStore()
    radio = MagicMock()
    radio.model = "IC-7300"
    radio.profile = resolve_radio_profile(model="IC-7300")
    radio.state_store = store
    radio.capabilities = set(radio.profile.capabilities)
    radio.managed_tx = None
    server = WebServer(radio)
    poller = RadioPoller(radio, CommandQueue(), state_store=store)
    server._radio_poller = poller  # noqa: SLF001
    store.apply(_observation(FieldPath.active_slot("0"), "B", at=10.0))

    def close_spawned(coro: Any) -> None:
        coro.close()

    server._spawn = close_spawned  # type: ignore[method-assign]  # noqa: SLF001
    server._on_radio_reconnect()  # noqa: SLF001

    assert "receiver.0.vfo.active_slot" not in store.snapshot().as_dict()


def _tx_target_field(
    value: Any, *, at: float, freshness: FreshnessState = FreshnessState.FRESH
) -> FieldSnapshot:
    path = FieldPath.global_("tx_state", "tx_target")
    return FieldSnapshot(path, value, freshness, at, 1.0, _source())


def test_runtime_capabilities_none_radio_returns_empty() -> None:
    assert runtime_capabilities(None) == set()


def test_runtime_capabilities_uses_explicit_caps_without_protocol_fallback() -> None:
    radio = _FakeRadio(caps=set())
    caps = runtime_capabilities(radio)
    assert caps == set()


def test_runtime_capabilities_falls_back_to_protocols_when_caps_missing() -> None:
    from rigplane.radio_protocol import AudioCapable, DualReceiverCapable, ScopeCapable

    class _ProtoRadio(ScopeCapable, AudioCapable, DualReceiverCapable):  # type: ignore[misc]
        def __init__(self) -> None:
            self.capabilities = None

        async def enable_scope(self, **kwargs: Any) -> None:  # noqa: ARG002
            ...

        async def disable_scope(self) -> None: ...

        def on_scope_data(self, callback: Any | None) -> None:  # noqa: ARG002
            ...

        @property
        def audio_bus(self) -> Any:
            return MagicMock()

        async def start_audio_rx_opus(self, callback: Any) -> None:  # noqa: ARG002
            ...

        async def stop_audio_rx_opus(self) -> None: ...

        async def push_audio_tx_opus(self, data: bytes) -> None:  # noqa: ARG002
            ...

        async def swap_main_sub(self) -> None: ...

        async def equalize_main_sub(self) -> None: ...

        async def set_main_sub_tracking(self, on: bool) -> None: ...  # noqa: ARG002

        async def get_main_sub_tracking(self) -> bool:
            return False

    radio = _ProtoRadio()
    caps = runtime_capabilities(radio)
    assert caps == {"scope", "audio", "dual_rx"}


def test_runtime_capabilities_fallback_recognises_usb_audio_only() -> None:
    """Fallback path (no `capabilities` set) must recognise USB-audio backends.

    Regression for #1356: Yaesu CAT radios that satisfy only ``UsbAudioCapable``
    (and not in-band ``AudioCapable``) must still get the ``"audio"`` tag when
    capabilities are derived purely from Protocol checks.
    """
    from rigplane.radio_protocol import UsbAudioCapable

    class _UsbOnlyRadio(UsbAudioCapable):  # type: ignore[misc]
        has_usb_audio: bool = True

    radio = _UsbOnlyRadio()
    # Sanity: no `capabilities` attribute → fallback path is used.
    assert not hasattr(radio, "capabilities")
    caps = runtime_capabilities(radio)
    assert caps == {"audio"}


def test_runtime_capabilities_filters_incompatible_tags() -> None:
    radio = _FakeRadio(caps={"scope", "audio", "dual_rx", "tx"})
    caps = runtime_capabilities(radio)
    # No Protocols implemented → scope/audio/dual_rx must be dropped, tx preserved
    assert caps == {"tx"}


def test_radio_ready_prefers_radio_ready_flag() -> None:
    radio = _FakeRadio(connected=False, radio_ready_flag=True)
    assert radio_ready(radio) is True


def test_radio_ready_falls_back_to_connected() -> None:
    radio = _FakeRadio(connected=True, radio_ready_flag=None)
    assert radio_ready(radio) is True


def test_radio_ready_handles_missing_or_non_bool_attributes() -> None:
    radio = _FakeRadio(connected="yes", radio_ready_flag="maybe")  # type: ignore[arg-type]
    assert radio_ready(radio) is False
    assert radio_ready(None) is False


def test_radio_health_classifies_ready_radio() -> None:
    radio = _FakeRadio(connected=True, radio_ready_flag=True)
    health = classify_radio_health(radio, server_reachable=True, now_monotonic=100.0)

    assert health["serverReachable"] is True
    assert health["radioLink"] == "connected"
    assert health["readiness"] == "ready"
    assert health["likelyCause"] == "unknown"


def test_radio_health_classifies_network_loss_separately_from_server_loss() -> None:
    radio = _FakeRadio(connected=False, radio_ready_flag=False)
    radio.conn_state = "reconnecting"

    health = classify_radio_health(radio, server_reachable=True, now_monotonic=100.0)

    assert health["serverReachable"] is True
    assert health["radioLink"] == "reconnecting"
    assert health["readiness"] == "recovering"
    assert health["likelyCause"] == "radio_network_lost"


def test_radio_health_classifies_remote_control_unreachable_when_host_reachable() -> (
    None
):
    """Host reachable but the radio's CI-V/remote-control server not listening
    (EPIPE / ICMP port-unreachable, no Are-You-There answer) → distinct
    actionable cause, not a generic network loss (#1217 diagnosis B).
    """
    radio = _FakeRadio(connected=False, radio_ready_flag=False)
    radio.conn_state = "reconnecting"
    radio.remote_control_unreachable = True

    health = classify_radio_health(radio, server_reachable=True, now_monotonic=100.0)

    assert health["serverReachable"] is True
    assert health["radioLink"] == "reconnecting"
    assert health["readiness"] == "recovering"
    assert health["likelyCause"] == "radio_remote_control_unreachable"


def test_radio_health_keeps_network_lost_when_host_unreachable() -> None:
    """Without the remote-control-unreachable signal a reconnecting radio is a
    plain network loss (host truly unreachable)."""
    radio = _FakeRadio(connected=False, radio_ready_flag=False)
    radio.conn_state = "reconnecting"
    radio.remote_control_unreachable = False

    health = classify_radio_health(radio, server_reachable=True, now_monotonic=100.0)

    assert health["likelyCause"] == "radio_network_lost"


def test_radio_health_keeps_unknown_when_radio_has_no_runtime_evidence() -> None:
    radio = _FakeRadio(connected=False, radio_ready_flag=False)

    health = classify_radio_health(radio, server_reachable=True, now_monotonic=100.0)

    assert health["serverReachable"] is True
    assert health["radioLink"] == "unknown"
    assert health["readiness"] == "stalled"
    assert health["likelyCause"] == "unknown"


def test_radio_health_classifies_delayed_then_stalled_radio_response() -> None:
    radio = _FakeRadio(connected=True, radio_ready_flag=False)
    radio._last_civ_data_received = 98.5
    radio._civ_ready_idle_timeout = 1.0

    delayed = classify_radio_health(radio, server_reachable=True, now_monotonic=100.0)
    assert delayed["radioLink"] == "connected"
    assert delayed["readiness"] == "delayed"
    assert delayed["likelyCause"] == "radio_not_responding"

    radio._last_civ_data_received = 94.0
    stalled = classify_radio_health(radio, server_reachable=True, now_monotonic=100.0)
    assert stalled["readiness"] == "stalled"
    assert stalled["likelyCause"] == "radio_not_responding"


def test_radio_health_promotes_repeated_probe_failures_to_powered_off_likely() -> None:
    radio = _FakeRadio(connected=True, radio_ready_flag=False)
    radio._last_civ_data_received = 80.0
    radio._civ_ready_idle_timeout = 2.0
    radio._has_connected_once = True

    def _stats() -> dict[str, int]:
        return {"timeouts": 3, "active_waiters": 0}

    radio.civ_stats = _stats

    health = classify_radio_health(radio, server_reachable=True, now_monotonic=100.0)

    assert health["readiness"] == "stalled"
    assert health["likelyCause"] == "radio_powered_off_likely"


def test_radio_health_reports_server_unreachable_without_radio_guess() -> None:
    health = classify_radio_health(None, server_reachable=False, now_monotonic=100.0)

    assert health["serverReachable"] is False
    assert health["radioLink"] == "unknown"
    assert health["readiness"] == "stalled"
    assert health["likelyCause"] == "server_unreachable"


@pytest.mark.asyncio
async def test_webserver_and_control_handler_use_same_capabilities_and_ready() -> None:
    """HTTP /api/v1/info and WS hello share the same runtime helpers."""
    from rigplane.web.handlers import ControlHandler
    from rigplane.web.websocket import WebSocketConnection

    caps = {"audio", "scope", "dual_rx", "tx"}
    radio = _FakeRadio(caps=caps, connected=True, radio_ready_flag=True)

    server = WebServer(radio)

    # HTTP: capture /api/v1/info JSON body
    writer = _FakeWriter()
    await server._serve_info(writer)  # type: ignore[arg-type]  # noqa: SLF001
    text = writer.buffer.decode("ascii", errors="replace")
    body_start = text.index("\r\n\r\n") + 4
    info = json.loads(text[body_start:])

    # WS: capture hello message emitted by ControlHandler
    ws = MagicMock(spec=WebSocketConnection)

    async def _send_text(payload: str) -> None:
        setattr(ws, "_last_payload", payload)

    ws.send_text = _send_text

    handler = ControlHandler(ws, radio, "0.0.0-test", radio.model, server=server)
    await handler._send_hello()

    hello = json.loads(getattr(ws, "_last_payload"))

    # Capabilities: tags and hello list must match runtime_capabilities(radio)
    expected_caps = sorted(runtime_capabilities(radio))
    assert sorted(info["capabilities"]["tags"]) == expected_caps
    assert sorted(hello["capabilities"]) == expected_caps

    # Readiness: both must reflect radio_ready(radio)
    expected_ready = radio_ready(radio)
    assert info["connection"]["radioReady"] is expected_ready
    assert hello["radio_ready"] is expected_ready


def test_public_state_projection_uses_snapshot_revisions_and_meter_values() -> None:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    store.apply(
        _observation(
            FieldPath.active("0", "freq_mode", "freq_hz"),
            14_074_000,
            at=clock.now(),
            max_age=10.0,
        )
    )
    store.apply(
        _observation(
            FieldPath.receiver("0", "meters", "s_meter"),
            42,
            at=clock.now(),
            max_age=0.5,
        )
    )

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=1,
    )

    assert payload["revision"] == 2
    assert payload["stateRevision"] == 2
    assert payload["freshnessRevision"] == 2
    assert payload["main"]["freqHz"] == 14_074_000
    assert payload["main"]["sMeter"] == 42


def test_tx_target_missing_is_explicit_in_both_public_producers() -> None:
    expected = {"status": "unknown", "reason": "not-observed"}
    dataclass_payload = build_public_state_payload(
        RadioState(), radio=None, revision=0, receiver_count=1
    )
    snapshot_payload = build_public_state_payload_from_snapshot(
        StateSnapshot.empty(), radio=None, receiver_count=1
    )
    status = snapshot_payload["fieldStatus"]["txTarget"]
    assert dataclass_payload["txTarget"] == expected
    assert snapshot_payload["txTarget"] == expected
    assert status == {
        "storePath": "global.tx_state.tx_target",
        "observed": False,
        "freshness": "unknown",
        "availability": "missing",
    }


@pytest.mark.parametrize(
    "target",
    [
        KnownTxTarget(receiver="SUB", slot="B", frequency_hz=14_074_000),
        UnknownTxTarget(reason="unsupported"),
        UnknownTxTarget(reason="contradiction"),
    ],
)
def test_tx_target_fresh_projection_is_lossless_and_available(
    target: TxTarget,
) -> None:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    store.apply(
        _observation(
            FieldPath.global_("tx_state", "tx_target"),
            target,
            at=clock.now(),
            max_age=1.0,
        )
    )
    payload = build_public_state_payload_from_snapshot(
        store.snapshot(), radio=None, receiver_count=2
    )
    assert payload["txTarget"] == target.to_dict()
    status = payload["fieldStatus"]["txTarget"]
    assert status["storePath"] == "global.tx_state.tx_target"
    assert status["observed"] is True
    assert status["freshness"] == "fresh"
    assert status["availability"] == "available"


def test_tx_target_stale_projection_never_leaks_known_identity() -> None:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    store.apply(
        _observation(
            FieldPath.global_("tx_state", "tx_target"),
            KnownTxTarget(receiver="SUB", slot="A", frequency_hz=7_074_000),
            at=clock.now(),
            max_age=0.5,
        )
    )
    fresh = build_public_state_payload_from_snapshot(
        store.snapshot(), radio=None, receiver_count=2
    )
    assert fresh["txTarget"]["receiver"] == "SUB"
    clock.advance(0.6)
    store.mark_stale_due()
    stale = build_public_state_payload_from_snapshot(
        store.snapshot(), radio=None, receiver_count=2
    )
    status = stale["fieldStatus"]["txTarget"]
    assert stale["txTarget"] == {"status": "unknown", "reason": "stale"}
    assert not {"receiver", "slot", "frequencyHz"} & stale["txTarget"].keys()
    assert (status["freshness"], status["availability"]) == ("stale", "stale")


def test_tx_target_malformed_impossible_snapshot_fails_closed() -> None:
    snapshot = StateSnapshot(
        1,
        1,
        1,
        10.0,
        (
            _tx_target_field(
                {
                    "status": "known",
                    "receiver": "MAIN",
                    "slot": "A",
                    "frequencyHz": 7_074_000,
                    "backend": "icom",
                },
                at=10.0,
            ),
        ),
    )
    payload = build_public_state_payload_from_snapshot(
        snapshot, radio=None, receiver_count=1
    )
    status = payload["fieldStatus"]["txTarget"]
    assert payload["txTarget"] == {"status": "unknown", "reason": "contradiction"}
    assert (status["freshness"], status["availability"]) == ("fresh", "available")


@pytest.mark.parametrize(
    "case",
    [
        (20.0, FreshnessState.STALE, {"malformed": True}, "stale"),
        (
            10.0,
            FreshnessState.FRESH,
            KnownTxTarget("SUB", None, None),
            "contradiction",
        ),
        (10.0, FreshnessState.STALE, KnownTxTarget("SUB", None, None), "stale"),
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
def test_tx_target_duplicate_path_selection_is_order_safe_and_consistent(
    case: tuple[float, FreshnessState, Any, str],
    reverse: bool,
) -> None:
    at, freshness, value, reason = case
    older = _tx_target_field(
        KnownTxTarget(receiver="MAIN", slot="B", frequency_hz=14_074_000), at=10.0
    )
    contender = _tx_target_field(value, at=at, freshness=freshness)
    fields = (contender, older) if reverse else (older, contender)
    payload = build_public_state_payload_from_snapshot(
        StateSnapshot(1, 1, 2, max(10.0, at), fields),
        radio=None,
        receiver_count=2,
    )
    assert payload["txTarget"] == {"status": "unknown", "reason": reason}
    s = payload["fieldStatus"]["txTarget"]
    availability = "available" if freshness is FreshnessState.FRESH else "stale"
    assert (s["freshness"], s["availability"]) == (freshness.value, availability)


def test_public_field_status_exposes_quality_flags() -> None:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    store.apply(
        _observation(
            FieldPath.receiver("0", "meters", "s_meter"),
            42,
            at=clock.now(),
            max_age=0.5,
            quality=("confirmed", "uncalibrated"),
        )
    )

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=1,
    )

    assert payload["fieldStatus"]["main.sMeter"]["quality"] == [
        "confirmed",
        "uncalibrated",
    ]


def test_public_state_projection_normalizes_raw_level_control_snapshots() -> None:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    for path, value in (
        (FieldPath.receiver("0", "operator_controls", "af_level"), 128),
        (FieldPath.receiver("0", "operator_controls", "rf_gain"), 64),
        (FieldPath.receiver("1", "operator_controls", "squelch"), 32),
        (FieldPath.global_("operator_controls", "power_level"), 255),
    ):
        store.apply(_observation(path, value, at=clock.now()))

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=2,
    )

    assert payload["main"]["afLevel"] == 128 / 255
    assert payload["main"]["rfGain"] == 64 / 255
    assert payload["sub"]["squelch"] == 32 / 255
    assert payload["powerLevel"] == 1.0


def test_public_state_projection_passes_through_normalized_level_controls() -> None:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    for path, value in (
        (FieldPath.receiver("0", "operator_controls", "af_level"), 0.5),
        (FieldPath.receiver("0", "operator_controls", "rf_gain"), 0.25),
        (FieldPath.receiver("1", "operator_controls", "squelch"), 0.75),
        (FieldPath.global_("operator_controls", "power_level"), 0.8),
    ):
        store.apply(_observation(path, value, at=clock.now()))

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=2,
    )

    assert payload["main"]["afLevel"] == 0.5
    assert payload["main"]["rfGain"] == 0.25
    assert payload["sub"]["squelch"] == 0.75
    assert payload["powerLevel"] == 0.8


def test_public_power_level_projection_keeps_icom_raw_byte_scale_with_profile() -> None:
    class _Profile:
        max_watts = 100

    class _Radio:
        profile = _Profile()

    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    store.apply(
        _observation(
            FieldPath.global_("operator_controls", "power_level"),
            128,
            at=clock.now(),
            provider="icom_civ",
        )
    )

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=_Radio(),  # type: ignore[arg-type]
        receiver_count=1,
    )

    assert payload["powerLevel"] == 128 / 255


def test_public_state_projection_keeps_unrelated_operator_controls_raw() -> None:
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    for path, value in (
        (FieldPath.receiver("0", "operator_controls", "mic_gain"), 180),
        (FieldPath.receiver("1", "operator_controls", "compressor_level"), 150),
        (FieldPath.global_("operator_controls", "monitor_gain"), 90),
    ):
        store.apply(_observation(path, value, at=clock.now()))

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=2,
    )

    assert payload["main"]["micGain"] == 180
    assert payload["sub"]["compressorLevel"] == 150
    assert payload["monitorGain"] == 90


def test_public_state_projection_covers_all_scope_control_leaves() -> None:
    """MOR-557: every scope_controls.global.display leaf must project.

    The v2 scope toolbar gates each control on
    ``fieldStatus["scopeControls.<leaf>"]``; before MOR-557 only ``span`` and
    ``receiver`` were mapped, so observed mode/edge/speed/hold/ref_db/dual
    stayed ``missing`` and the toolbar rendered dead.
    """
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    leaves: dict[str, Any] = {
        "receiver": 1,
        "dual": True,
        "mode": 3,
        "span": 6,
        "edge": 4,
        "hold": True,
        "ref_db": -10.5,
        "speed": 2,
    }
    for name, value in leaves.items():
        store.apply(
            _observation(
                FieldPath.scope_control("display", name),
                value,
                at=clock.now(),
            )
        )

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(),
        radio=None,
        receiver_count=2,
    )

    sc = payload["scopeControls"]
    assert sc["receiver"] == 1
    assert sc["dual"] is True
    assert sc["mode"] == 3
    assert sc["span"] == 6
    assert sc["edge"] == 4
    assert sc["hold"] is True
    assert sc["refDb"] == -10.5
    assert sc["speed"] == 2

    suffixes = ("receiver", "dual", "mode", "span", "edge", "hold", "refDb", "speed")
    for suffix in suffixes:
        status = payload["fieldStatus"][f"scopeControls.{suffix}"]
        assert status["observed"] is True, suffix
        assert status["availability"] == "available", suffix


_SCOPE_TOOLBAR_SUFFIXES = (
    "mode",
    "edge",
    "span",
    "speed",
    "hold",
    "refDb",
    "dual",
    "receiver",
)
_VFO_GROUP_PATHS = ("main.vfoA", "main.vfoB", "sub.vfoA", "sub.vfoB")
_VFO_LEAF_PATHS = (
    "main.vfoA.freqHz",
    "main.vfoB.mode",
    "sub.vfoA.freqHz",
    "sub.vfoB.mode",
)


def _frontend_availability(field_status: dict[str, dict[str, Any]], path: str) -> str:
    """Frontend MOR-429 resolver: nearest missing/stale ancestor vetoes a leaf."""
    ancestors = (path[:i] for i in range(len(path) - 1, 0, -1) if path[i] == ".")
    parent = next((field_status[p] for p in ancestors if p in field_status), None)
    own = field_status.get(path)
    if own is None:
        return parent["availability"] if parent else "available"
    if (
        own["availability"] == "available"
        and parent
        and parent["availability"] != "available"
    ):
        return parent["availability"]
    return own["availability"]


def test_default_field_status_has_no_scope_controls_group_entry() -> None:
    """MOR-557 fix 2: a bare ``scopeControls`` group entry, keyed to the
    never-written ``global.slow_state.scope_controls`` path, stays ``missing``
    forever and the frontend parent-veto disables every observed leaf. Only
    the eight per-leaf entries may be seeded."""
    payload = build_public_state_payload_from_snapshot(
        StateSnapshot.empty(), radio=None, receiver_count=2
    )
    field_status = payload["fieldStatus"]
    assert "scopeControls" not in field_status
    for suffix in _SCOPE_TOOLBAR_SUFFIXES:
        status = field_status[f"scopeControls.{suffix}"]
        assert status["availability"] == "missing", suffix
        assert status["observed"] is False, suffix


def test_default_field_status_has_no_vfo_group_entries() -> None:
    """MOR-558: slow-state ``vfo_a``/``vfo_b`` group entries are never written.

    Keep the real per-leaf VFO status entries seeded missing, but do not seed
    bare ``main/sub.vfoA/vfoB`` parents that would veto observed leaves.
    """
    payload = build_public_state_payload_from_snapshot(
        StateSnapshot.empty(), radio=None, receiver_count=2
    )
    field_status = payload["fieldStatus"]
    for path in _VFO_GROUP_PATHS:
        assert path not in field_status
    for path in _VFO_LEAF_PATHS:
        status = field_status[path]
        assert status["availability"] == "missing", path
        assert status["observed"] is False, path


def test_observed_vfo_slot_leaf_survives_frontend_parent_veto() -> None:
    """MOR-558: an observed slot leaf must not be hidden by a missing parent."""
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    store.apply(
        _observation(
            FieldPath.vfo_slot("0", "A", "freq_mode", "freq_hz"),
            14_074_000,
            at=clock.now(),
        )
    )

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(), radio=None, receiver_count=2
    )
    field_status = payload["fieldStatus"]
    assert payload["main"]["vfoA"]["freqHz"] == 14_074_000
    assert _frontend_availability(field_status, "main.vfoA.freqHz") == "available"


def test_observed_scope_control_leaves_survive_frontend_parent_veto() -> None:
    """MOR-557 fix 2: once scope-control observations land, every toolbar leaf
    resolves ``available`` through the frontend parent-availability walk —
    no never-observed ancestor entry may veto it."""
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    leaves: dict[str, Any] = {
        "receiver": 0, "dual": False, "mode": 0, "span": 0,
        "edge": 1, "hold": False, "ref_db": 0.0, "speed": 1,
    }  # fmt: skip
    for name, value in leaves.items():
        store.apply(
            _observation(
                FieldPath.scope_control("display", name), value, at=clock.now()
            )
        )
    payload = build_public_state_payload_from_snapshot(
        store.snapshot(), radio=None, receiver_count=2
    )
    field_status = payload["fieldStatus"]
    for suffix in _SCOPE_TOOLBAR_SUFFIXES:
        path = f"scopeControls.{suffix}"
        assert _frontend_availability(field_status, path) == "available", suffix


_SCOPE_SETTINGS_POPOVER_SUFFIXES = (
    "duringTx",
    "centerType",
    "vbwNarrow",
    "rbw",
    "fixedEdge",
)


def test_public_state_projection_covers_scope_settings_popover_leaves() -> None:
    """MOR-1302: the five ScopeSettingsPopover leaves must project and seed
    field status exactly like the eight MOR-557 toolbar leaves."""
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    fixed_edge = ScopeFixedEdge(
        range_index=1, edge=0, start_hz=7_000_000, end_hz=7_300_000
    )
    leaves: dict[str, Any] = {
        "during_tx": True,
        "center_type": 2,
        "vbw_narrow": True,
        "rbw": 1,
        "fixed_edge": fixed_edge,
    }
    for name, value in leaves.items():
        store.apply(
            _observation(
                FieldPath.scope_control("display", name), value, at=clock.now()
            )
        )

    payload = build_public_state_payload_from_snapshot(
        store.snapshot(), radio=None, receiver_count=2
    )

    sc = payload["scopeControls"]
    assert sc["duringTx"] is True
    assert sc["centerType"] == 2
    assert sc["vbwNarrow"] is True
    assert sc["rbw"] == 1
    assert sc["fixedEdge"] == {
        "rangeIndex": 1,
        "edge": 0,
        "startHz": 7_000_000,
        "endHz": 7_300_000,
    }

    for suffix in _SCOPE_SETTINGS_POPOVER_SUFFIXES:
        status = payload["fieldStatus"][f"scopeControls.{suffix}"]
        assert status["observed"] is True, suffix
        assert status["availability"] == "available", suffix


def test_default_field_status_seeds_scope_settings_popover_leaves_missing() -> None:
    """MOR-1302: the five popover leaves are seeded ``missing`` by default,
    same as the eight existing scope-control toolbar leaves (MOR-429)."""
    payload = build_public_state_payload_from_snapshot(
        StateSnapshot.empty(), radio=None, receiver_count=2
    )
    field_status = payload["fieldStatus"]
    for suffix in _SCOPE_SETTINGS_POPOVER_SUFFIXES:
        status = field_status[f"scopeControls.{suffix}"]
        assert status["availability"] == "missing", suffix
        assert status["observed"] is False, suffix


def test_observed_scope_settings_popover_leaves_survive_frontend_parent_veto() -> None:
    """MOR-1302: observed popover leaves resolve ``available`` through the
    frontend parent-availability walk, same as the MOR-557 toolbar leaves."""
    clock = FreshnessClock(start=10.0)
    store = StateStore(freshness_clock=clock)
    fixed_edge = ScopeFixedEdge(range_index=0, edge=0, start_hz=0, end_hz=0)
    leaves: dict[str, Any] = {
        "during_tx": False,
        "center_type": 0,
        "vbw_narrow": False,
        "rbw": 0,
        "fixed_edge": fixed_edge,
    }
    for name, value in leaves.items():
        store.apply(
            _observation(
                FieldPath.scope_control("display", name), value, at=clock.now()
            )
        )
    payload = build_public_state_payload_from_snapshot(
        store.snapshot(), radio=None, receiver_count=2
    )
    field_status = payload["fieldStatus"]
    for suffix in _SCOPE_SETTINGS_POPOVER_SUFFIXES:
        path = f"scopeControls.{suffix}"
        assert _frontend_availability(field_status, path) == "available", suffix
