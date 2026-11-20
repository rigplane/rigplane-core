"""Pick ONE TURN url by transport for aiortc (K7, MOR-272, provisional).

aiortc hands aioice at most one TURN server: the FIRST ``turn:``/``turns:``
url of the ICE server list, every later TURN url is ignored. A grant that
lists several transports for the same relay (a Cloudflare-style grant lists
UDP first, then TCP, then TLS on 443) therefore always relays over UDP, and
on a network that blocks outbound UDP the relay never comes up.

K7: the ORDER of urls in an ICE server grant carries no meaning; consumers
select by transport. Both ends that run aiortc (the station's answerer,
through the agent's adapter, and the Pro offerer) pass the grant through
:meth:`UdpReachabilityCache.select` before handing it to aiortc:

* UDP works on this network (a STUN binding request over UDP was
  answered): the UDP TURN url;
* UDP does not work: TLS on 443 (``turns:host:443?transport=tcp``), then
  TLS on another port, then TCP (443 first), the UDP url only as a last
  resort when the grant has nothing else.

STUN urls pass through unchanged; the result carries exactly one TURN url
(or none, when the grant has none). TCP/TLS is never the default: relayed
audio over TCP stalls on every lost segment, so it is used only where UDP
cannot pass.

The UDP answer is cached per network (the default-route source address
plus the set of local interface addresses) for at most
:data:`UDP_PROBE_MAX_AGE_S`; a network change gives a new key and a fresh
probe. Probe, network key and clock are injectable for tests.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import re
import socket
import struct
import time
from collections.abc import Awaitable, Callable, Hashable, Sequence
from typing import Any, Final, Literal

from .ice_gathering import (  # noqa: TID251
    IceServer,
    _adapter_addresses,
    _default_route_source_ip,
)

__all__ = [
    "UDP_PROBE_MAX_AGE_S",
    "UDP_PROBE_TIMEOUT_S",
    "UdpReachabilityCache",
    "current_network_key",
    "select_turn_url",
    "stun_udp_probe",
    "udp_probe_target",
]

#: How long a cached UDP reachability answer stays valid on one network.
UDP_PROBE_MAX_AGE_S: Final = 300.0

#: Bound for one STUN binding request over UDP.
UDP_PROBE_TIMEOUT_S: Final = 1.0

TurnTransport = Literal["udp", "tcp", "tls"]

#: An async probe: ``(host, port, timeout_s) -> answered``.
UdpProbe = Callable[[str, int, float], Awaitable[bool]]

_URL = re.compile(
    r"^(?P<scheme>stuns?|turns?):(?P<host>[^?:]+)(?::(?P<port>\d+))?"
    r"(?:\?transport=(?P<transport>\w+))?$"
)
_DEFAULT_PORT: Final = {"stun": 3478, "stuns": 5349, "turn": 3478, "turns": 5349}

_STUN_MAGIC: Final = 0x2112A442
_STUN_BINDING_REQUEST: Final = 0x0001


def _turn_facts(url: str) -> tuple[TurnTransport, int] | None:
    """``(transport, port)`` of a TURN url; ``None`` for anything else."""
    match = _URL.match(url)
    if match is None or not match["scheme"].startswith("turn"):
        return None
    port = int(match["port"]) if match["port"] else _DEFAULT_PORT[match["scheme"]]
    if match["scheme"] == "turns":
        return "tls", port
    return ("tcp" if match["transport"] == "tcp" else "udp"), port


def _rank(transport: TurnTransport, port: int, *, udp_ok: bool) -> int:
    """Lower is better; see the module docstring for the order."""
    if transport == "udp":
        return 0 if udp_ok else 5
    if transport == "tls":
        return 1 if port == 443 else 2
    return 3 if port == 443 else 4


def select_turn_url(servers: Sequence[IceServer], *, udp_ok: bool) -> list[IceServer]:
    """Return the grant with exactly ONE TURN url, chosen by transport.

    Grant order carries no meaning (K7): the url is chosen by transport
    rank alone; only a tie between equal transports and ports falls back
    to grant order, so the choice is deterministic. STUN urls pass
    through; a server left with no urls is dropped. Inputs are not
    mutated.
    """
    best: tuple[int, int, str] | None = None
    order = 0
    for server in servers:
        for url in server.urls:
            facts = _turn_facts(url)
            if facts is not None:
                candidate = (_rank(*facts, udp_ok=udp_ok), order, url)
                best = candidate if best is None or candidate < best else best
                order += 1
    chosen = None if best is None else best[2]
    selected: list[IceServer] = []
    done = False
    for server in servers:
        urls = [u for u in server.urls if _turn_facts(u) is None]
        if not done and chosen in server.urls:
            urls.append(chosen)
            done = True
        if urls:
            selected.append(IceServer(urls, server.username, server.credential))
    return selected


def udp_probe_target(servers: Sequence[IceServer]) -> tuple[str, int] | None:
    """Where to send the UDP probe: the first ``stun:`` url, else a UDP
    TURN url (a TURN server answers STUN binding requests). ``None``
    when the grant has neither: then no UDP TURN url can be chosen and
    the probe answer does not matter."""
    fallback: tuple[str, int] | None = None
    for server in servers:
        for url in server.urls:
            match = _URL.match(url)
            if match is None:
                continue
            port = int(match["port"]) if match["port"] else 3478
            if match["scheme"] == "stun":
                return match["host"], port
            if fallback is None and _turn_facts(url) == ("udp", port):
                fallback = match["host"], port
    return fallback


class _StunAnswer(asyncio.DatagramProtocol):
    def __init__(self, transaction: bytes) -> None:
        self.transaction = transaction
        self.answered: asyncio.Future[bool] = asyncio.get_running_loop().create_future()

    def datagram_received(self, data: bytes, addr: tuple[str | Any, int]) -> None:
        # Any STUN response class (success or error) with our transaction
        # id proves the UDP round trip.
        if (
            len(data) >= 20
            and data[0] & 0xC0 == 0
            and data[8:20] == self.transaction
            and not self.answered.done()
        ):
            self.answered.set_result(True)


async def stun_udp_probe(
    host: str, port: int, timeout: float = UDP_PROBE_TIMEOUT_S
) -> bool:
    """Send one STUN binding request over UDP (IPv4); True when answered."""
    loop = asyncio.get_running_loop()
    transaction = os.urandom(12)
    request = struct.pack(">HHI", _STUN_BINDING_REQUEST, 0, _STUN_MAGIC) + transaction
    transport: asyncio.DatagramTransport | None = None
    try:
        async with asyncio.timeout(timeout):
            infos = await loop.getaddrinfo(
                host, port, family=socket.AF_INET, type=socket.SOCK_DGRAM
            )
            address = (str(infos[0][4][0]), port)
            transport, protocol = await loop.create_datagram_endpoint(
                lambda: _StunAnswer(transaction), remote_addr=address
            )
            transport.sendto(request)
            return await protocol.answered
    except (OSError, TimeoutError, IndexError):
        return False
    finally:
        if transport is not None:
            transport.close()


def current_network_key() -> Hashable:
    """The network this host is on: default-route source address plus the
    set of local interface addresses (a change means a new probe)."""
    addresses = frozenset(ip for _name, ips in _adapter_addresses() for ip in ips)
    return _default_route_source_ip(), addresses


class UdpReachabilityCache:
    """Cached answer to "does STUN over UDP get answered on this network".

    One answer per (network key, probe target), reused for at most
    ``max_age_s``. Probe, network key and clock are injectable.
    """

    def __init__(
        self,
        *,
        probe: UdpProbe = stun_udp_probe,
        network_key: Callable[[], Hashable] = current_network_key,
        clock: Callable[[], float] = time.monotonic,
        max_age_s: float = UDP_PROBE_MAX_AGE_S,
    ) -> None:
        self._probe = probe
        self._network_key = network_key
        self._clock = clock
        self._max_age_s = max_age_s
        self._answers: dict[Hashable, tuple[float, bool]] = {}

    async def udp_ok(self, target: tuple[str, int]) -> bool:
        """The cached answer for ``target`` on the current network."""
        key = (self._network_key(), target)
        now = self._clock()
        cached = self._answers.get(key)
        if cached is not None and now - cached[0] < self._max_age_s:
            return cached[1]
        answered = False
        with contextlib.suppress(Exception):
            answered = await self._probe(target[0], target[1], UDP_PROBE_TIMEOUT_S)
        self._answers[key] = (now, answered)
        return answered

    async def select(self, servers: Sequence[IceServer]) -> list[IceServer]:
        """Probe (or reuse the cached answer), then :func:`select_turn_url`."""
        target = udp_probe_target(servers)
        udp_ok = True if target is None else await self.udp_ok(target)
        return select_turn_url(servers, udp_ok=udp_ok)
