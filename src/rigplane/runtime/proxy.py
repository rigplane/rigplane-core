"""Transparent UDP relay proxy for Icom LAN protocol.

Forwards UDP packets between a remote client (e.g. wfview via VPN)
and a local Icom radio, enabling remote access without protocol parsing.

Usage:
    icom-lan proxy --radio 192.168.55.40 --listen 0.0.0.0
"""

import asyncio
import logging
import time

__all__ = ["run_proxy"]

logger = logging.getLogger(__name__)

SESSION_TIMEOUT = 60.0  # seconds of inactivity before forgetting client


class _RelayProtocol(asyncio.DatagramProtocol):
    """Relay UDP packets between one client and the radio."""

    def __init__(self, radio_host: str, radio_port: int, label: str) -> None:
        self.radio_addr = (radio_host, radio_port)
        self.label = label
        self.transport: asyncio.DatagramTransport | None = None
        self.client_addr: tuple[str, int] | None = None
        self.last_activity: float = time.monotonic()

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        # Accept any transport with sendto/get_extra_info (e.g. tests use FakeTransport)
        self.transport = transport  # type: ignore[assignment]
        local = transport.get_extra_info("sockname")
        logger.info(
            "%s: listening on %s:%d -> radio %s:%d",
            self.label,
            local[0],
            local[1],
            self.radio_addr[0],
            self.radio_addr[1],
        )

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        if self.transport is None:
            return
        self.last_activity = time.monotonic()

        if addr[0] == self.radio_addr[0] and addr[1] == self.radio_addr[1]:
            # From radio -> forward to client
            if self.client_addr:
                self.transport.sendto(data, self.client_addr)
        else:
            # From client -> remember and forward to radio
            if self.client_addr != addr:
                if self.client_addr:
                    logger.info(
                        "%s: client changed %s:%d -> %s:%d",
                        self.label,
                        self.client_addr[0],
                        self.client_addr[1],
                        addr[0],
                        addr[1],
                    )
                else:
                    logger.info(
                        "%s: client connected from %s:%d",
                        self.label,
                        addr[0],
                        addr[1],
                    )
                self.client_addr = addr
            self.transport.sendto(data, self.radio_addr)

    def error_received(self, exc: Exception) -> None:
        logger.warning("%s: UDP error: %s", self.label, exc)

    def connection_lost(self, exc: Exception | None) -> None:
        logger.info("%s: connection lost: %s", self.label, exc)


async def _session_watchdog(relays: list[_RelayProtocol]) -> None:
    """Reset stale client sessions."""
    try:
        while True:
            await asyncio.sleep(SESSION_TIMEOUT / 2)
            now = time.monotonic()
            for relay in relays:
                if relay.client_addr and (now - relay.last_activity) > SESSION_TIMEOUT:
                    logger.info(
                        "%s: session timeout, forgetting client %s:%d",
                        relay.label,
                        relay.client_addr[0],
                        relay.client_addr[1],
                    )
                    relay.client_addr = None
    except asyncio.CancelledError:
        pass


async def run_proxy(
    radio_host: str,
    listen_host: str = "0.0.0.0",
    base_port: int = 50001,
) -> None:
    """Run transparent UDP relay on 3 ports (control, CI-V, audio).

    Args:
        radio_host: IP address of the Icom radio.
        listen_host: Address to listen on (default: all interfaces).
        base_port: First port number (default: 50001).
            Relay uses base_port, base_port+1, base_port+2.
    """
    import signal as _signal

    loop = asyncio.get_event_loop()
    labels = ["control", "civ", "audio"]
    relays: list[_RelayProtocol] = []
    transports: list[asyncio.DatagramTransport] = []

    for i, label in enumerate(labels):
        port = base_port + i
        relay = _RelayProtocol(radio_host, port, label)

        def _protocol_factory() -> _RelayProtocol:
            return relay

        transport, _ = await loop.create_datagram_endpoint(
            _protocol_factory,
            local_addr=(listen_host, port),
        )
        relays.append(relay)
        transports.append(transport)

    logger.info(
        "Proxy started: %s -> %s (ports %d-%d)",
        listen_host,
        radio_host,
        base_port,
        base_port + 2,
    )

    watchdog = asyncio.create_task(_session_watchdog(relays))

    try:
        stop = asyncio.Event()
        loop.add_signal_handler(_signal.SIGINT, stop.set)
        loop.add_signal_handler(_signal.SIGTERM, stop.set)
        await stop.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        watchdog.cancel()
        for t in transports:
            t.close()
        logger.info("Proxy stopped")
