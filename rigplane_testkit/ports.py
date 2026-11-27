"""Bound loopback sockets for tests that need explicit or unavailable ports."""

from __future__ import annotations

import asyncio
import socket
from collections.abc import Iterator, AsyncIterator
from typing import Any

import pytest


class UdpPorts:
    def __init__(self) -> None:
        self.pending: dict[tuple[str, int], socket.socket] = {}

    def reserve(self, count: int = 1, *, host: str = "127.0.0.1") -> tuple[int, ...]:
        for _ in range(100):
            held: list[socket.socket] = []
            try:
                head = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                held.append(head)
                head.bind((host, 0))
                base = head.getsockname()[1]
                if base + count - 1 > 65535:
                    continue
                for offset in range(1, count):
                    follower = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                    held.append(follower)
                    follower.bind((host, base + offset))
                ports = tuple(sock.getsockname()[1] for sock in held)
                self.pending.update(
                    ((host, port), sock) for port, sock in zip(ports, held)
                )
                held = []
                return ports
            except OSError:
                continue
            finally:
                for sock in held:
                    sock.close()
        raise RuntimeError(f"cannot reserve {count} consecutive UDP ports on {host}")

    def close(self) -> None:
        for sock in self.pending.values():
            sock.close()
        self.pending.clear()


@pytest.fixture
async def udp_ports(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[UdpPorts]:
    ports = UdpPorts()
    loop = asyncio.get_running_loop()
    create_endpoint = loop.create_datagram_endpoint

    async def handoff(factory: Any, **kwargs: Any) -> Any:
        address = kwargs.get("local_addr")
        if address is None or address not in ports.pending:
            return await create_endpoint(factory, **kwargs)
        sock = ports.pending[address]
        del kwargs["local_addr"]
        peer = kwargs.pop("remote_addr", None)
        if peer is not None:
            sock.connect(peer)
        sock.setblocking(False)
        result = await create_endpoint(factory, sock=sock, **kwargs)
        del ports.pending[address]
        return result

    with monkeypatch.context() as patch:
        patch.setattr(loop, "create_datagram_endpoint", handoff)
        try:
            yield ports
        finally:
            ports.close()


@pytest.fixture
def unavailable_tcp_port() -> Iterator[int]:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        yield sock.getsockname()[1]
