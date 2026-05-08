"""Tests for the UDP discovery responder."""

from __future__ import annotations

import asyncio
import json
import socket

import pytest

from rigplane import __version__
from rigplane.web.discovery import DiscoveryResponder, RadioInfo

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
        assert data["url"].startswith("https://")
        assert ":9090" in data["url"]

    async def test_response_fits_single_udp_packet(
        self, responder: DiscoveryResponder
    ) -> None:
        raw = await _query(responder.port, MAGIC)
        assert raw is not None
        assert len(raw) <= 512

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
