"""Profile-less external rigctld serving: state, capabilities, WS (MOR-2901).

The rigctld client backend reports the model «External rigctld», which no
RigPlane profile names. Decision (b) of 2026-09-28: the server serves that
backend from what the backend itself reports, with no fabricated profile.

Pins:

- ``GET /api/v1/state`` and ``GET /api/v1/capabilities`` answer 200 for a
  rigctld radio with no model, with no profile-derived keys;
- the control WebSocket state envelope is delivered (registration baseline
  plus a broadcast delta) instead of closing silently;
- the provider-owned marker scopes the profile-less path to backends that
  declare it (capability Protocol, not a model-name string check);
- MOR-2012 still holds: an unidentified radio on a backend without
  provider-owned state (the Icom serial/LAN shape) refuses aloud.
"""

from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import Any

import pytest

from rigplane.backends.rigctld_client import RigctldClientRadio
from rigplane.core._bounded_queue import BoundedQueue
from rigplane.core.state_pipeline_contracts import (
    FieldPath,
    Observation,
    SourceMetadata,
)
from rigplane.web.server import WebConfig, WebServer

_SOURCE = SourceMetadata(source="test", provider="mor2901", transport="fake")


class _MemoryWriter:
    """Minimal asyncio.StreamWriter stand-in that captures written bytes."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:  # pragma: no cover - trivial
        return None

    def close(self) -> None:
        return None

    async def wait_closed(self) -> None:  # pragma: no cover - trivial
        return None

    def is_closing(self) -> bool:
        return False

    def get_extra_info(self, *_args: Any, **_kwargs: Any) -> Any:
        return ("127.0.0.1", 0)


def _status_and_json(writer: _MemoryWriter) -> tuple[int, dict]:
    text = bytes(writer.buffer).decode("ascii", errors="replace")
    status = int(text.split(" ", 2)[1])
    body = text.split("\r\n\r\n", 1)[1]
    return status, json.loads(body or "{}")


def _profileless_rigctld_server() -> WebServer:
    """A server whose rigctld radio resolves to no RigPlane profile."""
    radio = RigctldClientRadio(host="127.0.0.1", port=0)
    return WebServer(radio, WebConfig(host="127.0.0.1", port=0))


def _observe_backend_frequency(server: WebServer, freq: int) -> None:
    """Feed one observation the way the backend's poller would."""
    server.command_state_store.apply(
        Observation(
            path=FieldPath.active("0", "freq_mode", "freq_hz"),
            value=freq,
            source=_SOURCE,
            timestamp_monotonic=time.monotonic(),
            provider_generation=server.command_state_store.provider_generation,
        )
    )


@pytest.mark.asyncio
async def test_api_state_serves_200_from_backend_observations_only() -> None:
    """``/api/v1/state`` answers 200 for a profile-less rigctld radio and
    carries only what the backend itself reported (MOR-2901 decision (b))."""
    server = _profileless_rigctld_server()
    _observe_backend_frequency(server, 14_070_000)
    writer = _MemoryWriter()
    await server._handle_http(  # noqa: SLF001
        writer, "GET", "/api/v1/state", {"host": "localhost:8470"}
    )
    status, payload = _status_and_json(writer)
    assert status == 200
    assert payload["stateContractVersion"] == 1
    assert payload["main"]["freq"] == 14_070_000
    # No fabricated second receiver: the backend declares no dual_rx.
    assert "sub" not in payload


@pytest.mark.asyncio
async def test_api_capabilities_serves_200_without_profile_derived_keys() -> None:
    """``/api/v1/capabilities`` answers 200 for a profile-less rigctld radio:
    backend capabilities are served, every profile-derived key is absent
    rather than fabricated (MOR-2901 decision (b))."""
    server = _profileless_rigctld_server()
    writer = _MemoryWriter()
    await server._handle_http(  # noqa: SLF001
        writer, "GET", "/api/v1/capabilities", {"host": "localhost:8470"}
    )
    status, payload = _status_and_json(writer)
    assert status == 200
    assert payload["model"] == "External rigctld"
    assert "tx" in payload["capabilities"]
    assert payload["receivers"] == 1
    for key in (
        "modes",
        "filters",
        "freqRanges",
        "txBands",
        "vfoScheme",
        "vfoReadback",
        "attValues",
        "preValues",
        "agcModes",
        "keyboard",
    ):
        assert key not in payload, key


def test_control_ws_state_envelope_flows_without_closing() -> None:
    """The control WebSocket state path survives a profile-less rigctld
    radio: the registration baseline and a broadcast delta are both
    delivered — on main the same calls raise and the socket closes
    silently (MOR-2901)."""
    server = _profileless_rigctld_server()
    queue: BoundedQueue[dict[str, Any]] = BoundedQueue(maxsize=8)
    baseline = server.register_control_event_queue(queue)  # noqa: SLF001
    assert baseline["type"] == "full"
    _observe_backend_frequency(server, 14_070_000)
    server._last_state_broadcast = 0.0  # noqa: SLF001
    server._broadcast_state_update(force=True)  # noqa: SLF001
    event = queue.get_nowait()
    assert event["type"] == "state_update"
    assert event["data"]["type"] == "delta"


def test_provider_owned_marker_scopes_the_profile_less_path() -> None:
    """The profile-less path is scoped by a capability Protocol marker, not
    a model-name check: the rigctld client declares it, an arbitrary radio
    object does not (MOR-2901)."""
    from rigplane.core.radio_protocol import ProviderOwnedStateCapable

    assert isinstance(
        RigctldClientRadio(host="127.0.0.1", port=0), ProviderOwnedStateCapable
    )
    assert not isinstance(
        SimpleNamespace(model="Mystery Rig"), ProviderOwnedStateCapable
    )


@pytest.mark.asyncio
async def test_mor2012_unidentified_profile_driven_radio_still_refuses() -> None:
    """MOR-2012 pin: a radio whose model matches no profile and whose
    backend declares no provider-owned state (the Icom serial/LAN shape)
    keeps the loud refusal — the profile-less path must not reach it, so
    the public state build still raises and the route answers 500."""
    radio = SimpleNamespace(
        model="Mystery Rig", backend_id="icom_serial", capabilities=set()
    )
    server = WebServer(radio, WebConfig(host="127.0.0.1", port=0))
    with pytest.raises(ValueError):
        server._build_public_state_from_snapshot(  # noqa: SLF001
            server.command_state_store.snapshot()
        )
    writer = _MemoryWriter()
    await server._handle_http(  # noqa: SLF001
        writer, "GET", "/api/v1/state", {"host": "localhost:8470"}
    )
    status, payload = _status_and_json(writer)
    assert status == 500
    assert payload == {"error": "internal server error"}
