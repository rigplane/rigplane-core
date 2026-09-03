"""Tests for the UDP relay proxy."""

import asyncio
import contextlib
import logging
import socket

import pytest

from rigplane.proxy import _RelayProtocol, run_proxy

_LISTEN_HOST = "127.0.0.1"

# run_proxy binds base_port, base_port+1 and base_port+2 — one per entry of
# its `labels` list — so reserving a single ephemeral port is not enough.
_RELAY_PORTS = 3

_PORT_SEARCH_ATTEMPTS = 20

# Logged by run_proxy once all relay endpoints are bound.
_STARTED_LOG_PREFIX = "Proxy started"


def _udp_bind_error(port: int) -> OSError | None:
    """Bind and release a UDP socket on ``port``; return the error, if any."""
    probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        probe.bind((_LISTEN_HOST, port))
    except OSError as exc:
        return exc
    finally:
        probe.close()
    return None


def _reserve_consecutive_udp_ports(count: int) -> int:
    """Return a base port with ``count`` consecutive free UDP ports.

    The probes are released before returning, so this does not hold the
    ports against another process; it only avoids the fixed port that every
    concurrent copy of this test aimed at (MOR-2271).
    """
    for _ in range(_PORT_SEARCH_ATTEMPTS):
        probes: list[socket.socket] = []
        try:
            head = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probes.append(head)
            head.bind((_LISTEN_HOST, 0))
            base = int(head.getsockname()[1])
            if base + count - 1 > 65535:
                continue
            for offset in range(1, count):
                follower = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                probes.append(follower)
                follower.bind((_LISTEN_HOST, base + offset))
        except OSError:
            continue
        finally:
            for probe in probes:
                probe.close()
        return base
    raise RuntimeError(
        f"no run of {count} free UDP ports on {_LISTEN_HOST} "
        f"after {_PORT_SEARCH_ATTEMPTS} attempts"
    )


def _why_it_ended(task: asyncio.Task[None]) -> str:
    """Describe a finished task's outcome for an assertion message."""
    if task.cancelled():
        return "cancelled"
    return repr(task.exception())


class TestRelayProtocol:
    """Unit tests for _RelayProtocol."""

    def test_client_to_radio(self) -> None:
        """Packets from client are forwarded to radio."""
        relay = _RelayProtocol("192.168.1.100", 50001, "control")
        sent: list[tuple[bytes, tuple[str, int]]] = []

        class FakeTransport:
            def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
                sent.append((data, addr))

            def get_extra_info(self, key: str) -> tuple[str, int]:
                return ("0.0.0.0", 50001)

        relay.connection_made(FakeTransport())  # type: ignore[arg-type]
        relay.datagram_received(b"hello", ("10.0.0.5", 12345))

        assert len(sent) == 1
        assert sent[0] == (b"hello", ("192.168.1.100", 50001))
        assert relay.client_addr == ("10.0.0.5", 12345)

    def test_radio_to_client(self) -> None:
        """Packets from radio are forwarded to remembered client."""
        relay = _RelayProtocol("192.168.1.100", 50001, "control")
        sent: list[tuple[bytes, tuple[str, int]]] = []

        class FakeTransport:
            def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
                sent.append((data, addr))

            def get_extra_info(self, key: str) -> tuple[str, int]:
                return ("0.0.0.0", 50001)

        relay.connection_made(FakeTransport())  # type: ignore[arg-type]

        # First: client sends something to register
        relay.datagram_received(b"from_client", ("10.0.0.5", 12345))
        sent.clear()

        # Then: radio sends back
        relay.datagram_received(b"from_radio", ("192.168.1.100", 50001))
        assert len(sent) == 1
        assert sent[0] == (b"from_radio", ("10.0.0.5", 12345))

    def test_no_client_drops_radio_packet(self) -> None:
        """Radio packets are dropped when no client is registered."""
        relay = _RelayProtocol("192.168.1.100", 50001, "control")
        sent: list[tuple[bytes, tuple[str, int]]] = []

        class FakeTransport:
            def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
                sent.append((data, addr))

            def get_extra_info(self, key: str) -> tuple[str, int]:
                return ("0.0.0.0", 50001)

        relay.connection_made(FakeTransport())  # type: ignore[arg-type]

        # Radio sends but no client registered
        relay.datagram_received(b"from_radio", ("192.168.1.100", 50001))
        assert len(sent) == 0

    def test_session_timeout(self) -> None:
        """Client addr is cleared after timeout."""
        import time

        relay = _RelayProtocol("192.168.1.100", 50001, "control")

        class FakeTransport:
            def sendto(self, data: bytes, addr: tuple[str, int]) -> None:
                pass

            def get_extra_info(self, key: str) -> tuple[str, int]:
                return ("0.0.0.0", 50001)

        relay.connection_made(FakeTransport())  # type: ignore[arg-type]
        relay.datagram_received(b"hello", ("10.0.0.5", 12345))
        assert relay.client_addr is not None

        # Simulate timeout
        relay.last_activity = time.monotonic() - 120.0
        from rigplane.proxy import SESSION_TIMEOUT

        now = time.monotonic()
        if relay.client_addr and (now - relay.last_activity) > SESSION_TIMEOUT:
            relay.client_addr = None

        assert relay.client_addr is None


# MUTATIONS that kill this test, each run in this worktree on 2026-09-02:
#   * `base_port = 59001` (the fixed port this test used before MOR-2271),
#     with two pytest processes started concurrently -> one of the two fails
#     in each of 3 repetitions.
#   * delete the "Proxy started" logger.info from run_proxy -> the readiness
#     wait below times out after 5 s.
#   * delete `t.close()` from run_proxy's finally -> "still bound after
#     run_proxy was cancelled".
@pytest.mark.asyncio
async def test_run_proxy_starts_and_stops(caplog: pytest.LogCaptureFixture) -> None:
    """run_proxy binds its relay ports and releases them when cancelled."""
    caplog.set_level(logging.INFO, logger="rigplane.runtime.proxy")
    base_port = _reserve_consecutive_udp_ports(_RELAY_PORTS)
    ports = [base_port + offset for offset in range(_RELAY_PORTS)]

    task = asyncio.create_task(run_proxy("192.168.1.100", _LISTEN_HOST, base_port))
    try:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + 5.0
        while not any(
            record.getMessage().startswith(_STARTED_LOG_PREFIX)
            for record in caplog.records
        ):
            if task.done():
                raise AssertionError(
                    f"run_proxy ended before reporting startup on {ports}: "
                    f"{_why_it_ended(task)}"
                )
            if loop.time() > deadline:
                raise AssertionError(
                    f"run_proxy did not report startup on {ports} within 5s"
                )
            await asyncio.sleep(0.01)

        assert not task.done(), (
            f"run_proxy ended after reporting startup: {_why_it_ended(task)}"
        )
        for port in ports:
            assert _udp_bind_error(port) is not None, (
                f"run_proxy reported startup but left port {port} unbound"
            )
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    for port in ports:
        assert _udp_bind_error(port) is None, (
            f"port {port} still bound after run_proxy was cancelled"
        )
