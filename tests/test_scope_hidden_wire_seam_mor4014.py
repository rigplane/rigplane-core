"""MOR-4014: the "scope is off on the radio" fact over the real control WebSocket.

The radio's own menu hides its scope: frames stop while CI-V keeps answering.
The scope-health monitor publishes that verdict, and the web UI says so
(``radioHealth.scopeHidden`` -> the scope panel's note). This test drives the
whole server side with only the hardware faked: the real ``WebServer`` and
its real scope-health monitor (silence -> hidden, a live frame -> recovered),
and a client connected to the real ``/api/v1/ws`` control WebSocket. The state
documents that client actually receives are asserted equal to the shared
document ``frontend/src/lib/transport/__tests__/fixtures/scope-hidden-wire.json``
(volatile clock fields pinned). Regenerate with
``RIGPLANE_UPDATE_SCOPE_HIDDEN_WIRE=1``.

The paired frontend test replays those same documents through the real
ws-client, the real stores and the runtime facade into a mounted
``SpectrumPanel`` and asserts the overlay transitions:
``frontend/src/components/spectrum/__tests__/SpectrumPanel.scope-hidden-wire.component.test.ts``.
Dropping ``scopeHidden`` anywhere between the monitor and the wire turns this
test red; dropping it from the document turns the frontend test red.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest

from rigplane.testing.timing import await_until
from rigplane.web.protocol import encode_scope_subscription
from rigplane.scope.models import Subscription, View
from rigplane.web.server import WebConfig, WebServer
from test_scope_health_watchdog_mor3690 import (
    _e2e_scope_radio,
    _establish_menu_silence,
    _producer_live_frame,
    _scope_frame_sink,
)
from test_web_server import (
    _addr,
    _close_ws,
    _ws_connect,
    _ws_recv_frame,
    _ws_send_text,
)

DOCUMENT = (
    Path(__file__).parents[1]
    / "frontend/src/lib/transport/__tests__/fixtures/scope-hidden-wire.json"
)
_UPDATE = "RIGPLANE_UPDATE_SCOPE_HIDDEN_WIRE"
# Clock-derived values: they vary run to run, so the document pins them.
_PINNED = {
    "updatedAt": "2026-01-01T00:00:00Z",
    "sinceMs": 0,
    "lastObservedMonotonic": 100.0,
    "lastFrameAgeMs": 0,
}


async def _collect(reader: asyncio.StreamReader, sink: list[dict[str, Any]]) -> None:
    """Every state document the control client receives, in arrival order."""
    while True:
        try:
            _opcode, payload = await _ws_recv_frame(reader, timeout=60.0)
        except (asyncio.IncompleteReadError, ConnectionError, TimeoutError):
            return
        message = json.loads(payload)
        if message.get("type") == "state_update":
            sink.append(message["data"])


def _pin(node: Any) -> Any:
    """``node`` with every clock-derived value replaced by its pinned one."""
    if isinstance(node, dict):
        return {
            key: _PINNED[key] if key in _PINNED else _pin(value)
            for key, value in node.items()
        }
    if isinstance(node, list):
        return [_pin(item) for item in node]
    return node


def _pin_leaf_set(document: dict[str, Any]) -> dict[str, Any]:
    """Pin the values of a leaf delta's ``set`` operations, keyed by their path."""
    pinned = _pin(document)
    pinned["set"] = [
        [path, _PINNED[path[-1]] if path[-1] in _PINNED else value]
        for path, value in pinned.get("set", [])
    ]
    return pinned  # type: ignore[no-any-return]


def _hidden(document: dict[str, Any]) -> object:
    """The ``radioHealth.scopeHidden`` a document carries (full or delta)."""
    if document["type"] == "full":
        return (document["data"].get("radioHealth") or {}).get("scopeHidden", "absent")
    for path, value in document.get("set", []):
        if path == ["radioHealth", "scopeHidden"]:
            return value
    return "absent"


@pytest.mark.realtime(reason="real WebSocket client and server sockets on real time")
@pytest.mark.timeout(60)
async def test_scope_hidden_state_documents_are_the_shared_golden() -> None:
    """Silence -> hidden -> recovery, as the control client receives it."""
    radio = _e2e_scope_radio()
    srv = WebServer(
        radio, WebConfig(host="127.0.0.1", port=0, keepalive_interval=9999.0)
    )
    srv._scope_health_interval = 0.05  # noqa: SLF001 — real-time monitor windows
    await srv.start()
    received: list[dict[str, Any]] = []
    collector: asyncio.Task[None] | None = None
    scope_writer = control_writer = None

    async def frames() -> None:
        """The radio's scope frames, at the points the hardware delivers them:
        the server's scope sink and the radio session's own decode stamp."""
        while True:
            frame = _producer_live_frame()
            _scope_frame_sink(radio)(frame)
            srv._radio_scope_session.on_frame(frame)  # noqa: SLF001
            await asyncio.sleep(0.01)

    async def seen(role: str, wanted: bool, after: int) -> int:
        """Wait for the first document past ``after`` that says ``wanted``."""
        await await_until(
            lambda: any(_hidden(d) is wanted for d in received[after:]),
            timeout=5.0,
            description=f"the {role} verdict reaching the control client",
        )
        return next(
            index
            for index, document in enumerate(received)
            if index >= after and _hidden(document) is wanted
        )

    try:
        host, port = _addr(srv)
        scope_reader, scope_writer, _ = await _ws_connect(host, port, "/api/v1/scope")
        await _ws_send_text(
            scope_writer,
            encode_scope_subscription(
                Subscription("radio", 0, View(14_000_000, 14_350_000), 475, 30)
            ),
        )
        await _ws_recv_frame(scope_reader)
        await await_until(
            lambda: radio.enable_scope.await_count == 1,
            timeout=5.0,
            description="scope enable on the radio",
        )
        # The control client the web UI is: leaf deltas (``state_delta=2``).
        control_reader, control_writer, _ = await _ws_connect(
            host, port, "/api/v1/ws?state_delta=2"
        )
        collector = asyncio.create_task(_collect(control_reader, received))
        await await_until(
            lambda: any(d["type"] == "full" for d in received),
            timeout=5.0,
            description="the control client's full state",
        )
        assert _hidden(received[0]) == "absent"  # nothing judged yet

        pump = asyncio.create_task(frames())
        shown = await seen("shown", False, 1)
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)
        # The operator opens the radio's menu: frames stop, CI-V keeps answering.
        await _establish_menu_silence(srv, radio)
        hidden = await seen("hidden", True, shown + 1)
        pump = asyncio.create_task(frames())
        recovered = await seen("recovered", False, hidden + 1)
        pump.cancel()
        await asyncio.gather(pump, return_exceptions=True)

        delivered = {
            "ordinary": _pin(received[0]),
            "shown": _pin_leaf_set(received[shown]),
            "hidden": _pin_leaf_set(received[hidden]),
            "recovered": _pin_leaf_set(received[recovered]),
        }
    finally:
        if collector is not None:
            collector.cancel()
            await asyncio.gather(collector, return_exceptions=True)
        for writer in (scope_writer, control_writer):
            if writer is not None:
                await _close_ws(writer)
        await srv.stop()

    if os.environ.get(_UPDATE) == "1":
        DOCUMENT.write_text(json.dumps(delivered, indent=1, sort_keys=True) + "\n")
        return
    assert delivered == json.loads(DOCUMENT.read_text())


def test_golden_documents_state_the_fact_the_panel_needs() -> None:
    """The shared document says: unjudged, shown, hidden, shown again."""
    golden = json.loads(DOCUMENT.read_text())
    assert [
        _hidden(golden[role]) for role in ("ordinary", "shown", "hidden", "recovered")
    ] == [
        "absent",
        False,
        True,
        False,
    ]
