"""Failing-first tests for ICE gathering policy (host-only default, 1.5 s bound).

Pure unit tests (parse/classify/policy) are fake-table based and must run
everywhere, including Linux CI and hosts without the ``[webrtc]`` extra:
``ice_gathering`` is importable without aiortc/aioice. Only the negotiation
tests that need a real peer connection are gated on ``webrtc_available()``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import socket
import struct
import time
from typing import Any

import pytest

from rigplane.web.transport.ice_gathering import (
    GATHER_TIMEOUT_S,
    IFF_POINTOPOINT,
    IF_TYPE_ETHERNET_CSMACD,
    IF_TYPE_IEEE80211,
    IF_TYPE_PROPVIRTUAL,
    IF_TYPE_TUNNEL,
    AddressInfo,
    IceGatheringPolicy,
    IceServer,
    IfKind,
    classify_linux_interface,
    classify_macos_interface,
    classify_windows_interface,
    parse_ice_servers,
)
from rigplane.web.transport.webrtc import webrtc_available

_webrtc = pytest.mark.skipif(
    not webrtc_available(), reason="aiortc extra not installed"
)


# --------------------------------------------------------------------------
# 1. parse_ice_servers
# --------------------------------------------------------------------------


def test_parse_ice_servers_valid_and_malformed() -> None:
    """Valid lists parse; None is empty; every malformed shape raises ValueError."""
    parsed = parse_ice_servers(
        [
            {"urls": ["stun:stun.example:3478"]},
            {
                "urls": ["turn:turn.example:3478"],
                "username": "user",
                "credential": "pass",
            },
        ]
    )
    assert parsed == [
        IceServer(urls=["stun:stun.example:3478"]),
        IceServer(
            urls=["turn:turn.example:3478"], username="user", credential="pass"
        ),
    ]

    assert parse_ice_servers(None) == []
    assert parse_ice_servers([]) == []

    malformed = [
        "not-a-list",  # non-list
        42,  # non-list
        ["not-a-dict"],  # non-dict entry
        [{}],  # missing urls
        [{"urls": "stun:x"}],  # urls not a list
        [{"urls": []}],  # empty urls
        [{"urls": [42]}],  # non-string url
        [{"urls": ["stun:x"], "username": 42}],  # non-string username
        [{"urls": ["stun:x"], "credential": ["x"]}],  # non-string credential
    ]
    for bad in malformed:
        with pytest.raises(ValueError):
            parse_ice_servers(bad)


# --------------------------------------------------------------------------
# 2-4. interface classification
# --------------------------------------------------------------------------


def test_classify_linux_physical_bridge_tunnel_unknown() -> None:
    """sysfs fakes map to PHYSICAL / BRIDGE / TUNNEL / UNKNOWN."""
    assert (
        classify_linux_interface("eth0", {"device": "", "type": "1"})
        == IfKind.PHYSICAL
    )
    assert classify_linux_interface("br0", {"brif": "vethX"}) == IfKind.BRIDGE
    assert classify_linux_interface("br0", {"bridge": ""}) == IfKind.BRIDGE
    assert (
        classify_linux_interface("tun0", {"type": "65534"}) == IfKind.TUNNEL
    )
    assert classify_linux_interface("tun0", {"tun_flags": "0"}) == IfKind.TUNNEL
    assert classify_linux_interface("eth0", {}) == IfKind.UNKNOWN
    assert classify_linux_interface("eth0", {"type": "1"}) == IfKind.UNKNOWN


def test_classify_linux_tunnel_beats_bridge_beats_physical() -> None:
    """Precedence: tunnel signals > bridge signals > device present."""
    assert (
        classify_linux_interface(
            "x", {"device": "", "brif": "vethX", "tun_flags": "0"}
        )
        == IfKind.TUNNEL
    )
    assert (
        classify_linux_interface("x", {"device": "", "brif": "vethX"})
        == IfKind.BRIDGE
    )


def test_classify_macos_tunnel_physical_unknown() -> None:
    """POINTOPOINT flags win; known hardware ports are physical."""
    assert (
        classify_macos_interface("utun9", 0x10, frozenset({"en0"}))
        == IfKind.TUNNEL
    )
    assert (
        classify_macos_interface("en0", 0x0, frozenset({"en0"}))
        == IfKind.PHYSICAL
    )
    assert (
        classify_macos_interface("utun9", 0x0, frozenset({"en0"}))
        == IfKind.UNKNOWN
    )


def test_classify_windows_physical_tunnel_unknown() -> None:
    """Ethernet/Wi-Fi are physical; tunnel/prop-virtual are tunnels."""
    assert classify_windows_interface(6) == IfKind.PHYSICAL
    assert classify_windows_interface(71) == IfKind.PHYSICAL
    assert classify_windows_interface(131) == IfKind.TUNNEL
    assert classify_windows_interface(53) == IfKind.TUNNEL
    assert classify_windows_interface(24) == IfKind.UNKNOWN
    assert classify_windows_interface(62) == IfKind.UNKNOWN


def test_classifier_module_consts_match_contract() -> None:
    """Pin the iftype/flag constants the classifiers are built on."""
    assert IFF_POINTOPOINT == 0x10
    assert IF_TYPE_ETHERNET_CSMACD == 6
    assert IF_TYPE_IEEE80211 == 71
    assert IF_TYPE_TUNNEL == 131
    assert IF_TYPE_PROPVIRTUAL == 53


# --------------------------------------------------------------------------
# 5. IceGatheringPolicy.select
# --------------------------------------------------------------------------


def _mixed_candidates() -> list[AddressInfo]:
    return [
        AddressInfo(ip="10.0.0.2", ifname="eth0", kind=IfKind.PHYSICAL),
        AddressInfo(ip="192.168.1.2", ifname="br0", kind=IfKind.BRIDGE),
        AddressInfo(ip="10.8.0.2", ifname="tun0", kind=IfKind.TUNNEL),
        AddressInfo(ip="169.254.1.2", ifname="eth1", kind=IfKind.UNKNOWN),
        # Default-route entries survive regardless of kind.
        AddressInfo(
            ip="192.168.1.3",
            ifname="br0",
            kind=IfKind.BRIDGE,
            is_default_route=True,
        ),
        AddressInfo(
            ip="10.8.0.3",
            ifname="tun0",
            kind=IfKind.TUNNEL,
            is_default_route=True,
        ),
        # Duplicate IP collapses to first occurrence.
        AddressInfo(ip="10.0.0.2", ifname="eth0", kind=IfKind.PHYSICAL),
    ]


def test_policy_select_default_excludes_bridge_and_tunnel() -> None:
    """Default policy: physical + unknown + default-route entries, in order."""
    assert IceGatheringPolicy().select(_mixed_candidates()) == [
        "10.0.0.2",
        "169.254.1.2",
        "192.168.1.3",
        "10.8.0.3",
    ]


def test_policy_select_include_vpn_keeps_tunnels() -> None:
    """include_vpn admits non-default-route tunnels; bridges stay out."""
    assert IceGatheringPolicy(include_vpn=True).select(
        _mixed_candidates()
    ) == [
        "10.0.0.2",
        "10.8.0.2",
        "169.254.1.2",
        "192.168.1.3",
        "10.8.0.3",
    ]


# --------------------------------------------------------------------------
# aiortc-dependent helpers
# --------------------------------------------------------------------------


async def _offer_sdp() -> tuple[str, str]:
    """Build a hermetic offer (audio m-line, no datachannels to dispatch)."""
    from aiortc import RTCPeerConnection

    client_pc = RTCPeerConnection()
    try:
        client_pc.addTransceiver("audio", direction="sendrecv")
        offer = await client_pc.createOffer()
        await client_pc.setLocalDescription(offer)
        return client_pc.localDescription.sdp, client_pc.localDescription.type
    finally:
        await client_pc.close()


def _answer_candidate_lines(answer_sdp: str) -> list[str]:
    return [ln for ln in answer_sdp.splitlines() if "candidate:" in ln]


# --------------------------------------------------------------------------
# 6. host-only default
# --------------------------------------------------------------------------


@_webrtc
async def test_offer_without_ice_servers_answer_host_only() -> None:
    """No ice_servers → host candidates only, never srflx/relay."""
    from rigplane.web.transport.webrtc_session import WebRtcSessionManager

    sdp, offer_type = await _offer_sdp()
    manager = WebRtcSessionManager(None, None, "test")
    try:
        result = await manager.negotiate(sdp, offer_type)
    finally:
        await manager.close_all()
    lines = _answer_candidate_lines(result["sdp"])
    assert any("typ host" in ln for ln in lines), lines
    assert not any("typ srflx" in ln for ln in lines), lines
    assert not any("typ relay" in ln for ln in lines), lines


# --------------------------------------------------------------------------
# 7. explicit empty RTCConfiguration
# --------------------------------------------------------------------------


@_webrtc
async def test_offer_without_ice_servers_passes_explicit_empty_configuration() -> None:
    """No ice_servers → PC built with RTCConfiguration(iceServers=[])."""
    import aiortc

    from rigplane.web.transport.webrtc_session import WebRtcSessionManager

    real_cls = aiortc.RTCPeerConnection
    seen: dict[str, Any] = {"count": 0, "config": None}

    class _Spy(real_cls):  # type: ignore[misc]
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            seen["count"] += 1
            if args:
                seen["config"] = args[0]
            else:
                seen["config"] = kwargs.get("configuration")
            super().__init__(*args, **kwargs)

    sdp, offer_type = await _offer_sdp()
    manager = WebRtcSessionManager(None, None, "test")
    real_attr = aiortc.RTCPeerConnection
    aiortc.RTCPeerConnection = _Spy  # type: ignore[assignment]
    try:
        await manager.negotiate(sdp, offer_type)
    finally:
        aiortc.RTCPeerConnection = real_attr
        await manager.close_all()
    assert seen["count"] == 1
    assert seen["config"] is not None
    assert seen["config"].iceServers == []


# --------------------------------------------------------------------------
# 8. malformed ice_servers rejected at the HTTP entrypoint
# --------------------------------------------------------------------------


class _FakeWriter:
    """Minimal ``StreamWriter`` stand-in capturing the HTTP response bytes."""

    def __init__(self) -> None:
        self.buffer = b""
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buffer += data

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    def get_extra_info(self, *_a: Any, **_k: Any) -> tuple[str, int]:
        return ("127.0.0.1", 0)


def _parse_response(writer: _FakeWriter) -> tuple[int, dict[str, Any]]:
    head, _, body = writer.buffer.partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode()
    code = int(status_line.split(" ", 2)[1])
    payload = json.loads(body.decode()) if body else {}
    return code, payload


async def _post_raw_offer(
    server: Any, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    body = json.dumps(payload).encode()
    reader = asyncio.StreamReader()
    reader.feed_data(body)
    reader.feed_eof()
    writer = _FakeWriter()
    await server._handle_webrtc_offer(
        writer, {"content-length": str(len(body))}, reader
    )
    return _parse_response(writer)


@_webrtc
async def test_offer_with_malformed_ice_servers_rejected() -> None:
    """Malformed ice_servers → 400 invalid_ice_servers, before SDP handling."""
    from rigplane.web.server import WebConfig, WebServer

    server = WebServer(None, WebConfig(webrtc_enabled=True))
    try:
        for bad in ("not-a-list", [{"urls": []}]):
            code, body = await _post_raw_offer(
                server, {"sdp": "bogus", "type": "offer", "ice_servers": bad}
            )
            assert code == 400, (bad, code, body)
            assert body["code"] == "invalid_ice_servers", (bad, body)
    finally:
        await server._webrtc_sessions.close_all()  # type: ignore[union-attr]


# --------------------------------------------------------------------------
# 9-10. gathering bound with STUN
# --------------------------------------------------------------------------


def _has_non_loopback_ipv4() -> bool:
    """True when the host exposes an IPv4 address beyond 127/8."""
    ips: set[str] = set()
    with contextlib.suppress(Exception):
        ips.update(socket.gethostbyname_ex(socket.gethostname())[2])
    with contextlib.suppress(Exception):
        for info in socket.getaddrinfo(socket.gethostname(), None):
            addr = info[4][0]
            if "." in addr:
                ips.add(addr)
    return any(ip and not ip.startswith("127.") for ip in ips)


class _StunResponder(asyncio.DatagramProtocol):
    """Minimal STUN Binding responder: answers requests with MAPPED-ADDRESS."""

    def __init__(self) -> None:
        self.transport: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: Any) -> None:
        if len(data) < 20 or data[0:2] != b"\x00\x01":
            return
        cookie = data[4:8]
        txn = data[8:20]
        host, port = addr[0], addr[1]
        try:
            packed = socket.inet_aton(host)
        except OSError:
            return
        ip_int = struct.unpack("!I", packed)[0]
        magic = struct.unpack("!I", cookie)[0]
        xport = port ^ (magic >> 16)
        xaddr = struct.pack("!I", ip_int ^ magic)
        xor_attr = b"\x00\x01" + struct.pack("!H", xport) + xaddr
        mapped_attr = b"\x00\x01" + struct.pack("!H", port) + packed
        attrs = (
            struct.pack("!HH", 0x0020, 8)
            + xor_attr
            + struct.pack("!HH", 0x0001, 8)
            + mapped_attr
        )
        header = (
            b"\x01\x01"
            + struct.pack("!H", len(attrs))
            + cookie
            + txn
        )
        if self.transport is not None:
            self.transport.sendto(header + attrs, addr)


@_webrtc
async def test_multihomed_answer_fast_with_stun() -> None:
    """A responsive STUN server yields srflx candidates well within the bound."""
    if not _has_non_loopback_ipv4():
        pytest.skip("host exposes only loopback addresses")

    from rigplane.web.transport.webrtc_session import WebRtcSessionManager

    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        _StunResponder, local_addr=("127.0.0.1", 0)
    )
    port = transport.get_extra_info("sockname")[1]
    manager = WebRtcSessionManager(None, None, "test")
    try:
        sdp, offer_type = await _offer_sdp()
        start = time.monotonic()
        result = await manager.negotiate(
            sdp,
            offer_type,
            ice_servers=[IceServer(urls=[f"stun:127.0.0.1:{port}"])],
        )
        elapsed = time.monotonic() - start
    finally:
        transport.close()
        await manager.close_all()
    assert elapsed <= 1.0, elapsed
    lines = _answer_candidate_lines(result["sdp"])
    assert any("typ srflx" in ln for ln in lines), lines


@_webrtc
async def test_blackhole_stun_answer_within_bound() -> None:
    """An unresponsive STUN server still yields a host-only answer in time."""
    from rigplane.web.transport.webrtc_session import WebRtcSessionManager

    blackhole = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    blackhole.bind(("127.0.0.1", 0))
    blackhole.setblocking(False)
    port = blackhole.getsockname()[1]
    stop = asyncio.Event()

    async def _drain() -> None:
        while not stop.is_set():
            try:
                blackhole.recvfrom(65535)
            except BlockingIOError:
                await asyncio.sleep(0.01)

    drain_task = asyncio.ensure_future(_drain())
    manager = WebRtcSessionManager(None, None, "test")
    try:
        sdp, offer_type = await _offer_sdp()
        start = time.monotonic()
        result = await manager.negotiate(
            sdp,
            offer_type,
            ice_servers=[IceServer(urls=[f"stun:127.0.0.1:{port}"])],
        )
        elapsed = time.monotonic() - start
    finally:
        stop.set()
        await drain_task
        blackhole.close()
        await manager.close_all()
    assert elapsed <= GATHER_TIMEOUT_S + 1.0, elapsed
    lines = _answer_candidate_lines(result["sdp"])
    assert any("typ host" in ln for ln in lines), lines
