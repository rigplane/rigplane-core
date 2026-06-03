"""Tests for the UDP discovery responder."""

from __future__ import annotations

import asyncio
import json
import socket

import pytest

from rigplane import __version__
from rigplane.web.discovery import DiscoveryResponder, FleetRadioInfo, RadioInfo

MAGIC = b"RIGPLANE_DISCOVER\n"
LEGACY_MAGIC = b"ICOM_LAN_DISCOVER\n"


async def _query(port: int, payload: bytes, timeout: float = 1.0) -> bytes | None:
    """Send a UDP packet to 127.0.0.1:port and wait for a response."""
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setblocking(False)
    sock.sendto(payload, ("127.0.0.1", port))
    try:
        data = await asyncio.wait_for(loop.sock_recv(sock, 4096), timeout=timeout)
        return data
    except (TimeoutError, asyncio.TimeoutError):
        return None
    finally:
        sock.close()


@pytest.fixture()
async def responder():
    """Start a DiscoveryResponder on a random loopback port."""
    r = DiscoveryResponder(
        web_port=8080,
        tls=False,
        radio_provider=lambda: RadioInfo(model="IC-7610", connected=True),
        bind_host="127.0.0.1",
        discovery_port=0,
    )
    await r.start()
    yield r
    await r.stop()


@pytest.fixture()
async def responder_no_radio():
    """Responder with no radio attached."""
    r = DiscoveryResponder(
        web_port=9090,
        tls=True,
        radio_provider=None,
        bind_host="127.0.0.1",
        discovery_port=0,
    )
    await r.start()
    yield r
    await r.stop()


class TestDiscoveryResponder:
    async def test_start_disables_reuse_port_on_windows(self, monkeypatch) -> None:
        calls: list[dict[str, object]] = []

        class FakeSocket:
            def getsockname(self) -> tuple[str, int]:
                return ("127.0.0.1", 4242)

        class FakeTransport:
            def get_extra_info(self, name: str) -> object:
                if name == "socket":
                    return FakeSocket()
                return None

            def close(self) -> None:
                pass

        class FakeLoop:
            async def create_datagram_endpoint(self, _factory, **kwargs: object):
                calls.append(kwargs)
                return FakeTransport(), object()

        monkeypatch.setattr("rigplane.web.discovery.sys.platform", "win32")
        monkeypatch.setattr(asyncio, "get_running_loop", lambda: FakeLoop())
        responder = DiscoveryResponder(web_port=8080, discovery_port=0)

        await responder.start()
        await responder.stop()

        assert calls[0]["reuse_port"] is False

    async def test_valid_request_returns_json(
        self, responder: DiscoveryResponder
    ) -> None:
        raw = await _query(responder.port, MAGIC)
        assert raw is not None
        data = json.loads(raw)
        assert data["service"] == "rigplane"
        assert data["version"] == __version__
        assert "url" in data
        assert data["url"].startswith("http://")
        assert ":8080" in data["url"]

    async def test_legacy_request_returns_json(
        self, responder: DiscoveryResponder
    ) -> None:
        raw = await _query(responder.port, LEGACY_MAGIC)
        assert raw is not None
        data = json.loads(raw)
        assert data["service"] == "rigplane"

    async def test_response_fields(self, responder: DiscoveryResponder) -> None:
        raw = await _query(responder.port, MAGIC)
        assert raw is not None
        data = json.loads(raw)
        assert data["radio"]["model"] == "IC-7610"
        assert data["radio"]["connected"] is True
        assert data["name"] == "IC-7610"
        assert data["schema"] == "rigplane.station.discovery.v1"
        assert data["kind"] == "station_server"
        assert data["displayName"] == "IC-7610"
        assert data["urls"]["base"].startswith("http://")
        assert data["urls"]["health"].endswith("/healthz")
        assert data["urls"]["readiness"].endswith("/readyz")
        assert data["urls"]["runtime"].endswith("/api/v1/runtime")
        assert data["urls"]["station"].endswith("/api/v1/station")
        assert data["station"]["readiness"] == "ready_with_radio"
        assert data["station"]["radioAvailable"] is True
        assert data["station"]["authRequired"] is False

    async def test_ignores_garbage(self, responder: DiscoveryResponder) -> None:
        result = await _query(responder.port, b"hello world", timeout=0.3)
        assert result is None

    async def test_ignores_partial_magic(self, responder: DiscoveryResponder) -> None:
        result = await _query(responder.port, b"RIGPLANE_DISCOVER", timeout=0.3)
        assert result is None

    async def test_no_radio_provider(
        self, responder_no_radio: DiscoveryResponder
    ) -> None:
        raw = await _query(responder_no_radio.port, MAGIC)
        assert raw is not None
        data = json.loads(raw)
        assert data["radio"] is None
        assert data["name"] == "RigPlane"
        assert data["displayName"] == "RigPlane"
        assert data["station"]["readiness"] == "requires_configuration_or_auth"
        assert data["station"]["radioAvailable"] is False
        assert data["url"].startswith("https://")
        assert ":9090" in data["url"]

    async def test_response_fits_udp_datagram_budget(
        self, responder: DiscoveryResponder
    ) -> None:
        raw = await _query(responder.port, MAGIC)
        assert raw is not None
        assert len(raw) <= 1200

    async def test_stop_idempotent(self, responder: DiscoveryResponder) -> None:
        await responder.stop()
        await responder.stop()  # should not raise

    async def test_broken_radio_provider_does_not_crash(self) -> None:
        """A failing radio_provider must not kill the responder."""

        def _boom() -> RadioInfo:
            raise RuntimeError("radio exploded")

        r = DiscoveryResponder(
            web_port=8080,
            radio_provider=_boom,
            bind_host="127.0.0.1",
            discovery_port=0,
        )
        await r.start()
        try:
            result = await _query(r.port, MAGIC, timeout=0.3)
            assert result is None  # no response, but no crash
        finally:
            await r.stop()


class TestDiscoveryResponderFleet:
    """MOR-303: additive radios[] array for multi-radio fleets."""

    async def _fleet_responder(
        self,
        fleet: list[FleetRadioInfo],
        *,
        radio_provider=None,
    ) -> DiscoveryResponder:
        r = DiscoveryResponder(
            web_port=8080,
            tls=False,
            radio_provider=radio_provider,
            fleet_provider=lambda: fleet,
            bind_host="127.0.0.1",
            discovery_port=0,
        )
        await r.start()
        return r

    async def test_radios_array_emitted_for_fleet(self) -> None:
        fleet = [
            FleetRadioInfo(
                id="radio-a", model="IC-7610", web_port=8081, connected=True
            ),
            FleetRadioInfo(
                id="radio-b", model="IC-705", web_port=8082, connected=False
            ),
        ]
        r = await self._fleet_responder(fleet)
        try:
            raw = await _query(r.port, MAGIC)
        finally:
            await r.stop()
        assert raw is not None
        data = json.loads(raw)

        assert data["kind"] == "station_fleet"
        radios = data["radios"]
        assert len(radios) == 2
        first, second = radios
        assert first["id"] == "radio-a"
        assert first["model"] == "IC-7610"
        assert first["url"].endswith(":8081")
        assert first["url"].startswith("http://")
        assert first["connected"] is True
        assert first["status"] == "connected"
        assert second["id"] == "radio-b"
        assert second["url"].endswith(":8082")
        assert second["connected"] is False
        assert second["status"] == "available"

    async def test_single_radio_fields_preserved_with_fleet(self) -> None:
        """Back-compat: legacy single-radio top-level fields stay populated."""
        fleet = [
            FleetRadioInfo(
                id="radio-a", model="IC-7610", web_port=8081, connected=True
            ),
            FleetRadioInfo(
                id="radio-b", model="IC-705", web_port=8082, connected=False
            ),
        ]
        # No dedicated radio_provider — top-level block must derive from first.
        r = await self._fleet_responder(fleet)
        try:
            raw = await _query(r.port, MAGIC)
        finally:
            await r.stop()
        assert raw is not None
        data = json.loads(raw)

        assert data["schema"] == "rigplane.station.discovery.v1"
        assert data["radio"]["model"] == "IC-7610"
        assert data["radio"]["connected"] is True
        assert data["name"] == "IC-7610"
        assert data["url"].startswith("http://")
        assert ":8080" in data["url"]
        assert data["station"]["radioAvailable"] is True
        assert data["station"]["readiness"] == "ready_with_radio"

    async def test_fleet_metadata_emitted_when_supplied(self) -> None:
        """MOR-387: station fleet datagrams can carry box and Fleet API data."""
        fleet = [
            FleetRadioInfo(
                id="radio-a", model="IC-7610", web_port=8081, connected=True
            ),
        ]
        r = DiscoveryResponder(
            web_port=8080,
            fleet_provider=lambda: fleet,
            fleet_api_base="http://127.0.0.1:8090",
            box_id="RP-354E-3168-2C7B",
            bind_host="127.0.0.1",
            discovery_port=0,
        )
        await r.start()
        try:
            raw = await _query(r.port, MAGIC)
        finally:
            await r.stop()
        assert raw is not None
        data = json.loads(raw)

        assert data["kind"] == "station_fleet"
        assert data["box_id"] == "RP-354E-3168-2C7B"
        assert data["urls"]["fleet"] == "http://127.0.0.1:8090"
        assert data["radio"]["model"] == "IC-7610"
        assert data["radios"][0]["id"] == "radio-a"

    async def test_radio_provider_overrides_top_level_with_fleet(self) -> None:
        """When both providers are set, top-level uses radio_provider."""
        fleet = [
            FleetRadioInfo(
                id="radio-b", model="IC-705", web_port=8082, connected=False
            ),
        ]
        r = await self._fleet_responder(
            fleet,
            radio_provider=lambda: RadioInfo(model="IC-7610", connected=True),
        )
        try:
            raw = await _query(r.port, MAGIC)
        finally:
            await r.stop()
        assert raw is not None
        data = json.loads(raw)

        assert data["radio"]["model"] == "IC-7610"
        assert data["radios"][0]["model"] == "IC-705"

    async def test_per_radio_explicit_status_and_tls(self) -> None:
        fleet = [
            FleetRadioInfo(
                id="radio-a",
                model="IC-7610",
                web_port=8443,
                connected=True,
                tls=True,
                status="in_use",
            ),
        ]
        r = await self._fleet_responder(fleet)
        try:
            raw = await _query(r.port, MAGIC)
        finally:
            await r.stop()
        assert raw is not None
        data = json.loads(raw)

        radio = data["radios"][0]
        assert radio["status"] == "in_use"
        assert radio["url"].startswith("https://")
        assert radio["url"].endswith(":8443")

    async def test_empty_fleet_keeps_single_radio_kind(self) -> None:
        """An empty fleet must not flip kind or add radios[]."""
        r = await self._fleet_responder(
            [],
            radio_provider=lambda: RadioInfo(model="IC-7610", connected=True),
        )
        try:
            raw = await _query(r.port, MAGIC)
        finally:
            await r.stop()
        assert raw is not None
        data = json.loads(raw)

        assert data["kind"] == "station_server"
        assert "radios" not in data
        assert data["radio"]["model"] == "IC-7610"
