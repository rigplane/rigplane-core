"""MOR-3630 finding 2: the IC-705 FM transition over the real local server.

The unit tests in ``tests/test_web_runtime_helpers.py`` seed ``"FM"`` into
the store and call the projection helper directly, so they pass even if the
production transition never clears previously observed USB values. This
test drives the real stack instead: a live ``WebServer`` over real sockets
backed by a fake radio, the mode change through the real command endpoint,
the real freshness tick, and the delivered ``GET /api/v1/state`` payload.
Only radio/Core/network dependencies are faked (stateful CI-V transport
peer); the server, poller execution path, freshness service, projection
and HTTP delivery are production code.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

import pytest

from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.types import Mode
from rigplane.web.server import WebServer
from test_web_server import _http_get
from test_web_split_writes import _request, _web_peer

_MODE = FieldPath.active("main", "freq_mode", "mode")

# (store path, public leaf, seeded radio reply value).
_CONTROLS: tuple[tuple[FieldPath, str, int], ...] = (
    (FieldPath.receiver("main", "operator_controls", "pbt_inner"), "pbtInner", 128),
    (FieldPath.receiver("main", "operator_controls", "pbt_outer"), "pbtOuter", 128),
    (
        FieldPath.receiver("main", "operator_controls", "manual_notch_width"),
        "manualNotchWidth",
        10,
    ),
    (
        FieldPath.receiver("main", "operator_controls", "filter_shape"),
        "filterShape",
        1,
    ),
)

_LEAVES = (
    "main.pbtInner",
    "main.pbtOuter",
    "main.manualNotchWidth",
    "main.filterShape",
)


def _radio_reply(store: Any, path: FieldPath, value: Any) -> None:
    """Apply one radio reply through the production observation path."""
    store.apply(
        Observation(
            path=path,
            value=value,
            source=SourceMetadata(
                source="poll_response",
                provider="web_poller",
                transport="backend",
                native_id="fm_transition_test",
            ),
            timestamp_monotonic=time.monotonic(),
        )
    )


async def _command(
    web: WebServer, executed: asyncio.Event, protocol: str, mode: str
) -> None:
    """Select ``mode`` through the real endpoint; the harness poller runs it."""
    executed.clear()
    status, result = await _request(web, protocol, "set_mode", {"mode": mode})
    assert status == (200 if protocol == "http" else None)
    assert result["ok"] is True
    await asyncio.wait_for(executed.wait(), 6.0)


async def _public_state(web: WebServer) -> dict[str, Any]:
    status, _, body = await _http_get("127.0.0.1", web.port, "/api/v1/state")
    assert status == 200
    return json.loads(body)


def _assert_controls(state: dict[str, Any], *, available: bool) -> None:
    field_status = state["fieldStatus"]
    for _, leaf, value in _CONTROLS:
        status = field_status[f"main.{leaf}"]
        assert status["observed"] is available, leaf
        assert status["availability"] == ("available" if available else "unavailable")
        assert state["main"][leaf] == (value if available else None)


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["http", "ws"])
async def test_ic705_fm_transition_clears_controls_over_real_http(
    protocol: str,
) -> None:
    """USB -> FM -> USB through the command endpoint clears and recovers.

    Starts with observed USB control values, selects FM through the real
    command endpoint, and asserts the delivered values and availability
    for all four FM-NAKed controls (they become unavailable/cleared once
    the production freshness tick discards them — the tick, not the
    command, is what clears them). Returns to USB and verifies recovery
    from radio replies through the production observation path.
    """

    async with _web_peer("IC-705", consume=2) as (
        web,
        peer,
        _entries,
        executed,
        (radio, _reader, _writer),
    ):
        store = web.command_state_store
        service = web._state_freshness_service
        scheduler = radio._acquisition_scheduler

        _radio_reply(store, _MODE, "USB")
        for path, _, value in _CONTROLS:
            _radio_reply(store, path, value)

        state = await _public_state(web)
        assert state["main"]["mode"] == "USB"
        _assert_controls(state, available=True)

        await _command(web, executed, protocol, "FM")
        assert peer.slots["A"][1] is Mode.FM
        assert any(
            command == 0x26 and data[:2] == bytes([0, Mode.FM])
            for command, _, data in peer.requests
        ), peer.requests

        # The poller's next readback reports the commanded radio's mode;
        # before the freshness tick runs, the old values are still served.
        _radio_reply(store, _MODE, "FM")
        state = await _public_state(web)
        assert state["main"]["mode"] == "FM"
        _assert_controls(state, available=True)

        service.tick()
        state = await _public_state(web)
        assert state["main"]["mode"] == "FM"
        _assert_controls(state, available=False)

        # Two further prime windows in FM: the discarded controls are
        # unobserved again, yet nothing re-queues a read of them (the
        # radio NAKs those reads in FM).
        for window in (1, 2):
            service.tick(
                now=time.monotonic()
                + window * (service.PRIME_ADAPTIVE_INTERVAL_SECONDS + 0.1)
            )
            queued = {
                path
                for request in scheduler.pending_requests()
                for path in request.paths
            }
            assert queued.isdisjoint(path for path, _, _ in _CONTROLS), queued

        await _command(web, executed, protocol, "USB")
        assert peer.slots["A"][1] is Mode.USB

        # The radio answers the re-primed reads; recovery flows through
        # the same observation path as any other radio reply.
        _radio_reply(store, _MODE, "USB")
        for path, _, value in _CONTROLS:
            _radio_reply(store, path, value)
        service.tick()
        state = await _public_state(web)
        assert state["main"]["mode"] == "USB"
        _assert_controls(state, available=True)
