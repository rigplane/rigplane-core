"""UDP Discovery Responder for rigplane.

Listens for broadcast discovery requests from companion apps and responds
with server information (version, URL, radio status) via unicast.

Protocol:
    Request:  UDP broadcast, payload ``RIGPLANE_DISCOVER\\n`` (ASCII)
    Response: UDP unicast, payload JSON (UTF-8, single line, ≤512 bytes)
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import sys
from dataclasses import dataclass
from typing import Callable

from .. import __version__

__all__ = ["DiscoveryResponder", "FleetRadioInfo", "RadioInfo"]

logger = logging.getLogger(__name__)

_DISCOVERY_MAGIC = b"RIGPLANE_DISCOVER\n"
_LEGACY_DISCOVERY_MAGIC = b"ICOM_LAN_DISCOVER\n"
_DISCOVERY_MAGICS = {_DISCOVERY_MAGIC, _LEGACY_DISCOVERY_MAGIC}
_DEFAULT_PORT = 8470


def _reuse_port_supported() -> bool:
    return sys.platform != "win32"


@dataclass
class RadioInfo:
    """Snapshot of radio state for discovery responses."""

    model: str
    connected: bool
    control_connected: bool = False
    radio_ready: bool = False
    backend: str | None = None
    readiness: str | None = None
    message: str | None = None
    auth_required: bool = False


@dataclass
class FleetRadioInfo:
    """Snapshot of a single radio endpoint in a multi-radio fleet.

    A station supervisor runs N child radios on N web ports behind one
    discovery responder. Each fleet member is described by one of these so the
    responder can emit an additive ``radios[]`` array. ``web_port`` (and
    optional per-radio ``tls``) let the responder build a reachable per-radio
    URL using the same local IP it resolves for the response.
    """

    id: str
    model: str
    web_port: int
    connected: bool
    tls: bool | None = None
    status: str | None = None


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    """asyncio UDP protocol that handles discovery requests."""

    def __init__(self, responder: DiscoveryResponder) -> None:
        self._responder = responder
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if data not in _DISCOVERY_MAGICS:
            logger.debug("discovery: ignoring %d bytes from %s", len(data), addr[0])
            return

        try:
            response = self._responder.build_response(addr[0])
        except Exception:
            logger.warning(
                "discovery: failed to build response for %s", addr[0], exc_info=True
            )
            return
        if self.transport is not None:
            self.transport.sendto(response, addr)
            logger.debug("discovery: replied to %s", addr[0])

    def error_received(self, exc: Exception) -> None:
        logger.warning("discovery: UDP error: %s", exc)


class DiscoveryResponder:
    """UDP server that responds to rigplane discovery broadcasts.

    Args:
        web_port: HTTP/HTTPS port of the web server.
        tls: Whether the web server uses TLS.
        radio_provider: Callable returning current RadioInfo, or None.
        fleet_provider: Optional callable returning a list of FleetRadioInfo
            for a multi-radio fleet. When it yields entries, the response gains
            an additive ``radios[]`` array; the single-radio top-level fields
            stay populated (from radio_provider, or from the first fleet entry)
            for back-compatible parsers.
        name: Human-readable instance name (default: auto from radio model).
        bind_host: Address to bind UDP socket (default: 0.0.0.0).
        discovery_port: UDP port to listen on (default: 8470).
    """

    def __init__(
        self,
        web_port: int = 8080,
        tls: bool = False,
        radio_provider: Callable[[], RadioInfo | None] | None = None,
        name: str | None = None,
        bind_host: str = "0.0.0.0",
        discovery_port: int = _DEFAULT_PORT,
        fleet_provider: Callable[[], list[FleetRadioInfo]] | None = None,
    ) -> None:
        self._web_port = web_port
        self._tls = tls
        self._radio_provider = radio_provider
        self._fleet_provider = fleet_provider
        self._name = name
        self._bind_host = bind_host
        self._discovery_port = discovery_port
        self._transport: asyncio.DatagramTransport | None = None
        self._protocol: _DiscoveryProtocol | None = None

    @property
    def port(self) -> int:
        """Actual bound port (useful when testing with port 0)."""
        return self._discovery_port

    async def start(self) -> None:
        """Bind the UDP socket and start listening."""
        loop = asyncio.get_running_loop()
        try:
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: _DiscoveryProtocol(self),
                local_addr=(self._bind_host, self._discovery_port),
                reuse_port=_reuse_port_supported(),
            )
            self._transport = transport  # type: ignore[assignment]
            self._protocol = protocol  # type: ignore[assignment]
            # Update port in case OS assigned one (port 0)
            sock = transport.get_extra_info("socket")
            if sock is not None:
                self._discovery_port = sock.getsockname()[1]
            logger.info(
                "discovery responder listening on %s:%d (UDP)",
                self._bind_host,
                self._discovery_port,
            )
        except OSError as exc:
            logger.warning(
                "discovery: failed to bind %s:%d — %s (discovery disabled)",
                self._bind_host,
                self._discovery_port,
                exc,
            )

    async def stop(self) -> None:
        """Close the UDP socket."""
        if self._transport is not None:
            self._transport.close()
            self._transport = None
            self._protocol = None
            logger.info("discovery responder stopped")

    def build_response(self, remote_addr: str) -> bytes:
        """Build the JSON discovery response payload."""
        local_ip = _get_local_ip_for(remote_addr)
        scheme = "https" if self._tls else "http"

        radio_info = self._radio_provider() if self._radio_provider else None
        fleet = self._fleet_provider() if self._fleet_provider else None

        # Back-compat: keep the single-radio top-level block populated from the
        # first/selected fleet member when no dedicated radio_provider is set,
        # so legacy parsers still resolve a valid station_server.
        if radio_info is None and fleet:
            first = fleet[0]
            radio_info = RadioInfo(
                model=first.model,
                connected=first.connected,
                radio_ready=first.connected,
            )

        if self._name:
            name = self._name
        elif radio_info and radio_info.model:
            name = radio_info.model
        else:
            name = "RigPlane"

        payload: dict[str, object] = {
            "schema": "rigplane.station.discovery.v1",
            "service": "rigplane",
            "kind": "station_server",
            "version": __version__,
            "url": f"{scheme}://{local_ip}:{self._web_port}",
            "urls": {
                "base": f"{scheme}://{local_ip}:{self._web_port}",
                "health": f"{scheme}://{local_ip}:{self._web_port}/healthz",
                "readiness": f"{scheme}://{local_ip}:{self._web_port}/readyz",
                "runtime": (f"{scheme}://{local_ip}:{self._web_port}/api/v1/runtime"),
                "station": (f"{scheme}://{local_ip}:{self._web_port}/api/v1/station"),
            },
            "name": name,
            "displayName": name,
            "instanceId": None,
            "station": {
                "readiness": (
                    radio_info.readiness
                    if radio_info and radio_info.readiness
                    else (
                        "ready_with_radio"
                        if radio_info
                        and (radio_info.radio_ready or radio_info.connected)
                        else "requires_configuration_or_auth"
                    )
                ),
                "radioAvailable": bool(
                    radio_info and (radio_info.radio_ready or radio_info.connected)
                ),
                "backend": radio_info.backend if radio_info else None,
                "authRequired": bool(radio_info.auth_required) if radio_info else False,
                "message": radio_info.message if radio_info else None,
            },
            "radio": (
                {
                    "model": radio_info.model,
                    "connected": radio_info.connected,
                    "controlConnected": radio_info.control_connected,
                    "radioReady": radio_info.radio_ready,
                }
                if radio_info
                else None
            ),
        }

        if fleet:
            payload["kind"] = "station_fleet"
            payload["radios"] = [
                {
                    "id": member.id,
                    "model": member.model,
                    "url": (
                        f"{'https' if member.tls else 'http'}"
                        f"://{local_ip}:{member.web_port}"
                    ),
                    "status": (
                        member.status
                        if member.status
                        else ("connected" if member.connected else "available")
                    ),
                    "connected": member.connected,
                }
                for member in fleet
            ]

        return json.dumps(payload, separators=(",", ":")).encode("utf-8")


def _get_local_ip_for(remote_addr: str) -> str:
    """Determine which local IP would be used to reach *remote_addr*."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((remote_addr, 1))  # doesn't send anything
        host: str = s.getsockname()[0]
        return host
    except Exception:
        return "127.0.0.1"
    finally:
        s.close()
