from __future__ import annotations

import json
from typing import Any

import pytest
from unittest.mock import MagicMock

from rigplane.web.runtime_helpers import (
    classify_radio_health,
    radio_ready,
    runtime_capabilities,
)
from rigplane.web.server import WebServer


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
    await server._serve_info(writer)  # noqa: SLF001
    text = writer.buffer.decode("ascii", errors="replace")
    body_start = text.index("\r\n\r\n") + 4
    info = json.loads(text[body_start:])

    # WS: capture hello message emitted by ControlHandler
    ws = MagicMock(spec=WebSocketConnection)

    async def _send_text(payload: str) -> None:
        ws._last_payload = payload  # type: ignore[attr-defined]

    ws.send_text = _send_text  # type: ignore[assignment]

    handler = ControlHandler(ws, radio, "0.0.0-test", radio.model, server=server)
    await handler._send_hello()  # type: ignore[attr-defined]

    hello = json.loads(ws._last_payload)  # type: ignore[attr-defined]

    # Capabilities: tags and hello list must match runtime_capabilities(radio)
    expected_caps = sorted(runtime_capabilities(radio))
    assert sorted(info["capabilities"]["tags"]) == expected_caps
    assert sorted(hello["capabilities"]) == expected_caps

    # Readiness: both must reflect radio_ready(radio)
    expected_ready = radio_ready(radio)
    assert info["connection"]["radioReady"] is expected_ready
    assert hello["radio_ready"] is expected_ready
