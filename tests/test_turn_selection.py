"""TURN url selection by transport (K7, MOR-272).

aiortc hands aioice only the FIRST TURN url of a grant. The helper keeps
exactly one TURN url, chosen by transport from a cached UDP reachability
answer, so grant order carries no meaning. The bench row this replaces:
"home x UDP blocked, Cloudflare-order grant" failed 3/3 because the UDP
url came first.
"""

from __future__ import annotations

import asyncio
import struct

from rigplane.web.transport.ice_gathering import IceServer
from rigplane.web.transport.turn_selection import (
    UdpReachabilityCache,
    select_turn_url,
    stun_udp_probe,
    udp_probe_target,
)

_UDP = "turn:turn.example.net:3478?transport=udp"
_TCP = "turn:turn.example.net:3478?transport=tcp"
_TCP80 = "turn:turn.example.net:80?transport=tcp"
_TLS443 = "turns:turn.example.net:443?transport=tcp"
_TLS5349 = "turns:turn.example.net:5349?transport=tcp"
_STUN = "stun:stun.example.net:3478"


def _cloudflare_grant() -> list[IceServer]:
    """A Cloudflare-style grant: STUN, then TURN with UDP listed first."""
    return [
        IceServer([_STUN]),
        IceServer([_UDP, _TCP, _TLS443], username="u", credential="c"),
    ]


def _turn_urls(servers: list[IceServer]) -> list[str]:
    return [u for s in servers for u in s.urls if u.startswith("turn")]


def test_udp_ok_keeps_only_the_udp_url() -> None:
    selected = select_turn_url(_cloudflare_grant(), udp_ok=True)
    assert selected == [
        IceServer([_STUN]),
        IceServer([_UDP], username="u", credential="c"),
    ]


def test_udp_blocked_picks_tls_on_443() -> None:
    selected = select_turn_url(_cloudflare_grant(), udp_ok=False)
    assert _turn_urls(selected) == [_TLS443]
    assert selected[0] == IceServer([_STUN])  # STUN passes through
    assert selected[1].username == "u" and selected[1].credential == "c"


def test_grant_order_carries_no_meaning() -> None:
    reordered = [
        IceServer([_TLS443, _TCP, _UDP], username="u", credential="c"),
        IceServer([_STUN]),
    ]
    assert _turn_urls(select_turn_url(reordered, udp_ok=True)) == [_UDP]
    assert _turn_urls(select_turn_url(reordered, udp_ok=False)) == [_TLS443]


def test_udp_blocked_prefers_tls_then_tcp_443_then_tcp() -> None:
    grant = [IceServer([_TCP, _TCP80, _TLS5349])]
    assert _turn_urls(select_turn_url(grant, udp_ok=False)) == [_TLS5349]
    grant = [IceServer([_TCP, "turn:turn.example.net:443?transport=tcp"])]
    assert _turn_urls(select_turn_url(grant, udp_ok=False)) == [
        "turn:turn.example.net:443?transport=tcp"
    ]
    assert _turn_urls(select_turn_url([IceServer([_TCP80, _TCP])], udp_ok=False)) == [
        _TCP80
    ]


def test_only_tls_grant_keeps_tls_whatever_udp_says() -> None:
    grant = [IceServer([_TLS443], username="u", credential="c")]
    for udp_ok in (True, False):
        assert select_turn_url(grant, udp_ok=udp_ok) == grant


def test_only_udp_grant_keeps_udp_as_last_resort() -> None:
    grant = [IceServer([_UDP], username="u", credential="c")]
    assert select_turn_url(grant, udp_ok=False) == grant


def test_no_turn_in_grant_passes_stun_through() -> None:
    grant = [IceServer([_STUN]), IceServer(["stun:other.example.net"])]
    assert select_turn_url(grant, udp_ok=False) == grant
    assert select_turn_url([], udp_ok=True) == []


def test_several_udp_urls_keep_exactly_one() -> None:
    second = "turn:relay2.example.net:3478"
    grant = [
        IceServer([_UDP], username="a", credential="1"),
        IceServer([second, _TLS443], username="b", credential="2"),
    ]
    selected = select_turn_url(grant, udp_ok=True)
    assert selected == [IceServer([_UDP], username="a", credential="1")]
    # A server left without urls is dropped; inputs are not mutated.
    assert grant[1].urls == [second, _TLS443]


def test_default_transports_and_ports() -> None:
    """``turn:`` without transport is UDP; ``turns:`` without port is 5349."""
    grant = [IceServer(["turns:t.example.net", "turn:t.example.net"])]
    assert _turn_urls(select_turn_url(grant, udp_ok=True)) == ["turn:t.example.net"]
    assert _turn_urls(select_turn_url(grant, udp_ok=False)) == ["turns:t.example.net"]


def test_probe_target_prefers_stun_then_udp_turn() -> None:
    assert udp_probe_target(_cloudflare_grant()) == ("stun.example.net", 3478)
    assert udp_probe_target([IceServer([_TLS443, _UDP])]) == (
        "turn.example.net",
        3478,
    )
    assert udp_probe_target([IceServer([_TLS443, _TCP])]) is None


class _Probe:
    def __init__(self, answer: bool | Exception) -> None:
        self.answer = answer
        self.calls: list[tuple[str, int]] = []

    async def __call__(self, host: str, port: int, timeout: float) -> bool:
        self.calls.append((host, port))
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


async def test_cache_probes_once_per_network_and_selects() -> None:
    probe = _Probe(False)
    now = [0.0]
    network = ["home"]
    cache = UdpReachabilityCache(
        probe=probe, network_key=lambda: network[0], clock=lambda: now[0]
    )
    assert _turn_urls(await cache.select(_cloudflare_grant())) == [_TLS443]
    now[0] = 100.0
    assert _turn_urls(await cache.select(_cloudflare_grant())) == [_TLS443]
    assert probe.calls == [("stun.example.net", 3478)]  # cached on this network
    network[0] = "lte"  # network change: a fresh probe
    probe.answer = True
    assert _turn_urls(await cache.select(_cloudflare_grant())) == [_UDP]
    assert len(probe.calls) == 2


async def test_cache_refreshes_after_max_age() -> None:
    probe = _Probe(True)
    now = [0.0]
    cache = UdpReachabilityCache(
        probe=probe, network_key=lambda: "home", clock=lambda: now[0], max_age_s=60.0
    )
    assert await cache.udp_ok(("stun.example.net", 3478)) is True
    now[0] = 59.0
    probe.answer = False
    assert await cache.udp_ok(("stun.example.net", 3478)) is True
    now[0] = 61.0
    assert await cache.udp_ok(("stun.example.net", 3478)) is False
    assert len(probe.calls) == 2


async def test_cache_failing_probe_reads_as_udp_blocked() -> None:
    cache = UdpReachabilityCache(
        probe=_Probe(OSError("no route")), network_key=lambda: 1
    )
    assert _turn_urls(await cache.select(_cloudflare_grant())) == [_TLS443]


async def test_cache_skips_probe_without_target() -> None:
    probe = _Probe(False)
    cache = UdpReachabilityCache(probe=probe, network_key=lambda: 1)
    grant = [IceServer([_TCP, _TLS443])]
    assert _turn_urls(await cache.select(grant)) == [_TLS443]
    assert probe.calls == []


class _StunResponder(asyncio.DatagramProtocol):
    """Answers every binding request with a success response."""

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        response = struct.pack(">HHI", 0x0101, 0, 0x2112A442) + data[8:20]
        self.transport.sendto(response, addr)  # type: ignore[attr-defined]


async def test_stun_udp_probe_against_local_responder() -> None:
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        _StunResponder, local_addr=("127.0.0.1", 0)
    )
    try:
        port = transport.get_extra_info("sockname")[1]
        assert await stun_udp_probe("127.0.0.1", port, 2.0) is True
    finally:
        transport.close()


async def test_stun_udp_probe_silent_server_is_false_within_timeout() -> None:
    loop = asyncio.get_running_loop()
    silent, _ = await loop.create_datagram_endpoint(
        asyncio.DatagramProtocol, local_addr=("127.0.0.1", 0)
    )
    try:
        port = silent.get_extra_info("sockname")[1]
        started = loop.time()
        assert await stun_udp_probe("127.0.0.1", port, 0.2) is False
        assert loop.time() - started < 2.0
    finally:
        silent.close()
