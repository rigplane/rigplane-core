"""UDP Discovery Responder for icom-lan.

Listens for broadcast discovery requests from companion apps and responds
with server information (version, URL, radio status) via unicast.

Protocol:
    Request:  UDP broadcast, payload ``ICOM_LAN_DISCOVER\\n`` (17 bytes ASCII)
    Response: UDP unicast, payload JSON (UTF-8, single line, ≤512 bytes)
"""

from __future__ import annotations

import asyncio
import json
import logging
import socket
from dataclasses import dataclass
from typing import Callable

from .. import __version__

__all__ = ["DiscoveryResponder", "RadioInfo"]

logger = logging.getLogger(__name__)

_DISCOVERY_MAGIC = b"ICOM_LAN_DISCOVER\n"
_DEFAULT_PORT = 8470


@dataclass
class RadioInfo:
    """Snapshot of radio state for discovery responses."""

    model: str
    connected: bool


class _DiscoveryProtocol(asyncio.DatagramProtocol):
    """asyncio UDP protocol that handles discovery requests."""

    def __init__(self, responder: DiscoveryResponder) -> None:
        self._responder = responder
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if data != _DISCOVERY_MAGIC:
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
    """UDP server that responds to icom-lan discovery broadcasts.

    Args:
        web_port: HTTP/HTTPS port of the web server.
        tls: Whether the web server uses TLS.
        radio_provider: Callable returning current RadioInfo, or None.
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
    ) -> None:
        self._web_port = web_port
        self._tls = tls
        self._radio_provider = radio_provider
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
                reuse_port=True,
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

        if self._name:
            name = self._name
        elif radio_info and radio_info.model:
            name = radio_info.model
        else:
            name = "icom-lan"

        payload: dict[str, object] = {
            "service": "icom-lan",
            "version": __version__,
            "url": f"{scheme}://{local_ip}:{self._web_port}",
            "name": name,
            "radio": (
                {"model": radio_info.model, "connected": radio_info.connected}
                if radio_info
                else None
            ),
        }
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
