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
from collections.abc import Container
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
    WindowsAdapterFacts,
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
        IceServer(urls=["turn:turn.example:3478"], username="user", credential="pass"),
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
        classify_linux_interface("eth0", {"device": "", "type": "1"}) == IfKind.PHYSICAL
    )
    assert classify_linux_interface("br0", {"brif": "vethX"}) == IfKind.BRIDGE
    assert classify_linux_interface("br0", {"bridge": ""}) == IfKind.BRIDGE
    assert classify_linux_interface("tun0", {"type": "65534"}) == IfKind.TUNNEL
    assert classify_linux_interface("tun0", {"tun_flags": "0"}) == IfKind.TUNNEL
    assert classify_linux_interface("eth0", {}) == IfKind.UNKNOWN
    assert classify_linux_interface("eth0", {"type": "1"}) == IfKind.UNKNOWN


def test_classify_linux_tunnel_beats_bridge_beats_physical() -> None:
    """Precedence: tunnel signals > bridge signals > device present."""
    assert (
        classify_linux_interface("x", {"device": "", "brif": "vethX", "tun_flags": "0"})
        == IfKind.TUNNEL
    )
    assert (
        classify_linux_interface("x", {"device": "", "brif": "vethX"}) == IfKind.BRIDGE
    )


def test_classify_macos_tunnel_physical_unknown() -> None:
    """POINTOPOINT flags win; known hardware ports are physical."""
    assert (
        classify_macos_interface("bridge0", 0x0, frozenset({"bridge0", "en0"}))
        == IfKind.BRIDGE
    )
    assert classify_macos_interface("utun9", 0x10, frozenset({"en0"})) == IfKind.TUNNEL
    assert classify_macos_interface("en0", 0x0, frozenset({"en0"})) == IfKind.PHYSICAL
    assert classify_macos_interface("utun9", 0x0, frozenset({"en0"})) == IfKind.UNKNOWN


def _wf(
    if_type: int,
    hardware: bool | None = None,
    oper_up: bool = True,
    name: str = "Ethernet",
) -> WindowsAdapterFacts:
    """Build classifier input the way ``_enumerate_windows`` does."""
    return WindowsAdapterFacts(
        if_type=if_type,
        hardware_interface=hardware,
        oper_up=oper_up,
        friendly_name=name,
    )


def test_classify_windows_physical_tunnel_unknown() -> None:
    """Ethernet/Wi-Fi are physical; tunnel/prop-virtual are tunnels."""
    assert classify_windows_interface(_wf(6)) == IfKind.PHYSICAL
    assert classify_windows_interface(_wf(71)) == IfKind.PHYSICAL
    assert classify_windows_interface(_wf(131)) == IfKind.TUNNEL
    assert classify_windows_interface(_wf(53)) == IfKind.TUNNEL
    assert classify_windows_interface(_wf(24)) == IfKind.UNKNOWN
    assert classify_windows_interface(_wf(62)) == IfKind.UNKNOWN


def test_classify_windows_hardware_flag_table() -> None:
    """GTR7 host (Windows 11 x64): the hardware flag decides, IfType alone
    does not — Hyper-V/VMware virtual adapters report IfType 6 like real
    Ethernet, so non-hardware ones are bridges (skipped by default)."""
    # Intel I225 physical Ethernet: hardware, IfType 6.
    assert classify_windows_interface(_wf(6, True)) == IfKind.PHYSICAL
    assert classify_windows_interface(_wf(71, True)) == IfKind.PHYSICAL
    # vEthernet (Default Switch / WSL), VMnet1/VMnet8, vSwitch (LAN
    # Bridge), WAN Miniport: IfType 6 but not hardware.
    for iftype in (6, 71):
        assert classify_windows_interface(_wf(iftype, False)) == IfKind.BRIDGE
    # Teredo (131) and Tailscale (53) are tunnels.
    assert classify_windows_interface(_wf(131, False)) == IfKind.TUNNEL
    assert classify_windows_interface(_wf(53, False)) == IfKind.TUNNEL
    assert classify_windows_interface(_wf(131)) == IfKind.TUNNEL
    assert classify_windows_interface(_wf(53)) == IfKind.TUNNEL
    # Failed per-adapter GetIfEntry2 (no hardware signal) falls back to
    # IfType alone, as before.
    assert classify_windows_interface(_wf(6)) == IfKind.PHYSICAL
    assert classify_windows_interface(_wf(24)) == IfKind.UNKNOWN


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
    """Default policy: physical + unknown + default-route entries, in order.

    The ``169.254.1.2`` link-local address is dropped even though it
    classifies UNKNOWN: link-local addresses are never routable.
    """
    assert IceGatheringPolicy().select(_mixed_candidates()) == [
        "10.0.0.2",
        "192.168.1.3",
        "10.8.0.3",
    ]


def test_policy_select_include_vpn_keeps_tunnels() -> None:
    """include_vpn admits non-default-route tunnels; bridges stay out.

    ``include_vpn`` does not re-admit link-local addresses.
    """
    assert IceGatheringPolicy(include_vpn=True).select(_mixed_candidates()) == [
        "10.0.0.2",
        "10.8.0.2",
        "192.168.1.3",
        "10.8.0.3",
    ]


def _gtr7_candidates() -> list[AddressInfo]:
    """GTR7 host (Windows 11 x64) address table, reduced to the policy inputs.

    Ethernet ``192.168.55.132`` is the default-route source; Wi-Fi
    ``192.168.55.137`` is the second LAN path. The ``169.254.x.x`` APIPA
    addresses sit on Bluetooth PAN, the Wi-Fi Direct virtual adapters
    ("Local Area Connection* 1/2") and a Hyper-V bridge without DHCP:
    hardware-backed but RFC 3927 link-local, hence unroutable.
    """
    return [
        AddressInfo(
            ip="192.168.55.132",
            ifname="Ethernet",
            kind=IfKind.PHYSICAL,
            is_default_route=True,
        ),
        AddressInfo(ip="192.168.55.137", ifname="Wi-Fi", kind=IfKind.PHYSICAL),
        AddressInfo(
            ip="169.254.12.34",
            ifname="Bluetooth Network Connection",
            kind=IfKind.UNKNOWN,
        ),
        AddressInfo(
            ip="169.254.45.67",
            ifname="Local Area Connection* 1",
            kind=IfKind.BRIDGE,
        ),
        AddressInfo(
            ip="169.254.89.12",
            ifname="Local Area Connection* 2",
            kind=IfKind.BRIDGE,
        ),
        AddressInfo(
            ip="169.254.200.11",
            ifname="vEthernet (Hyper-V bridge, no DHCP)",
            kind=IfKind.BRIDGE,
        ),
    ]


def test_policy_select_drops_gtr7_apipa_link_locals() -> None:
    """GTR7 table: APIPA addresses are out whatever their kind, LAN stays."""
    assert IceGatheringPolicy().select(_gtr7_candidates()) == [
        "192.168.55.132",
        "192.168.55.137",
    ]
    assert IceGatheringPolicy(include_vpn=True).select(_gtr7_candidates()) == [
        "192.168.55.132",
        "192.168.55.137",
    ]


def test_policy_select_keeps_lone_apipa_default_route() -> None:
    """A host whose ONLY address is APIPA keeps it via the default route."""
    assert IceGatheringPolicy().select(
        [
            AddressInfo(
                ip="169.254.7.77",
                ifname="Ethernet",
                kind=IfKind.PHYSICAL,
                is_default_route=True,
            ),
        ]
    ) == ["169.254.7.77"]


def test_policy_select_keeps_default_route_on_virtual_switch() -> None:
    """LAN bound to a Hyper-V external switch: the vEthernet adapter
    carrying the LAN IP classifies BRIDGE, but the default-route address
    is always kept while the other bridge addresses are skipped."""
    candidates = [
        AddressInfo(
            ip="192.168.1.20",
            ifname="vEthernet (LAN Bridge)",
            kind=IfKind.BRIDGE,
            is_default_route=True,
        ),
        AddressInfo(
            ip="192.168.1.21",
            ifname="vEthernet (LAN Bridge)",
            kind=IfKind.BRIDGE,
        ),
        AddressInfo(
            ip="10.8.0.2",
            ifname="Tailscale",
            kind=IfKind.TUNNEL,
        ),
    ]
    assert IceGatheringPolicy().select(candidates) == ["192.168.1.20"]


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
        if server._webrtc_sessions is not None:
            await server._webrtc_sessions.close_all()


async def _http_post_offer(
    port: int, payload: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """POST one JSON body to the offer endpoint of a running test server.

    Raw-socket minimal POST, like the attach-path browser tests: the
    request travels the real HTTP route and response serialisation.
    """
    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    raw = json.dumps(payload).encode()
    writer.write(
        f"POST /api/v1/transport/webrtc/offer HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
        f"Content-Length: {len(raw)}\r\nContent-Type: application/json\r\n"
        "Connection: close\r\n\r\n".encode()
        + raw
    )
    await writer.drain()
    head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10.0)
    status = int(head.split(b" ", 2)[1])
    body = json.loads(await asyncio.wait_for(reader.read(), timeout=10.0) or b"{}")
    writer.close()
    return status, body


# --------------------------------------------------------------------------
# 8b. valid ice_servers through the real offer endpoint
# --------------------------------------------------------------------------


@_webrtc
@pytest.mark.realtime(
    reason="real ICE/STUN wall-clock against live sockets on a running server"
)
async def test_offer_endpoint_forwards_ice_servers_to_answer() -> None:
    """Valid ice_servers through the REAL offer endpoint yields srflx.

    Drives ``POST /api/v1/transport/webrtc/offer`` on a running test
    server (not the session manager directly) with ``ice_servers``
    pointing at a local fake STUN responder bound to the default-route
    address. The responder must RECEIVE binding requests and the answer
    SDP must carry its srflx candidate. Removing the ``ice_servers``
    forwarding in ``_handle_webrtc_offer`` leaves this red: the answer
    stays host-only and the responder stays silent.
    """
    from rigplane.web.server import WebConfig, WebServer

    stun_host = _default_route_ipv4()
    if not stun_host:
        pytest.skip("host exposes only loopback addresses")

    loop = asyncio.get_running_loop()
    responder = _StunResponder()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: responder, local_addr=(stun_host, 0)
    )
    stun_port = transport.get_extra_info("sockname")[1]
    web = WebServer(
        None,
        WebConfig(host="127.0.0.1", port=0, discovery=False, webrtc_enabled=True),
    )
    try:
        await asyncio.wait_for(web.start(), timeout=15.0)
        sdp, offer_type = await _offer_sdp()
        status, answer = await _http_post_offer(
            web.port,
            {
                "sdp": sdp,
                "type": offer_type,
                "ice_servers": [{"urls": [f"stun:{stun_host}:{stun_port}"]}],
            },
        )
        assert status == 200, answer
        assert answer["status"] == "ok", answer
        lines = _answer_candidate_lines(answer["sdp"])
        assert any("typ srflx" in ln for ln in lines), lines
        assert responder.received, "STUN responder saw no binding requests"
    finally:
        transport.close()
        with contextlib.suppress(Exception):
            await web.stop()
        if web._webrtc_sessions is not None:  # noqa: SLF001
            await web._webrtc_sessions.close_all()  # noqa: SLF001


# --------------------------------------------------------------------------
# 8c. --webrtc-include-vpn through the real CLI → config → server path
# --------------------------------------------------------------------------


def _parse_web_cli(*extra: str) -> Any:
    """Parse real ``web`` CLI args the way the operator's startup does."""
    import io
    from unittest.mock import patch

    from rigplane.cli import _build_parser

    parser = _build_parser()
    with patch("sys.stderr", new_callable=io.StringIO):
        args = parser.parse_args(["web", "--webrtc", *extra])
    args.web_rigctld = False
    return args


async def _config_from_cli_startup(args: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Run the real ``_cmd_web`` startup path, capturing its WebConfig.

    Every forwarding step (parse → ``config_kwargs`` → ``WebConfig``) is
    real code; only the final ``WebServer`` construction is captured so
    no listener binds. Removing the CLI→config assignment leaves the
    captured config at its default.
    """
    from unittest.mock import AsyncMock, patch

    from rigplane.cli import _cmd_web

    monkeypatch.setenv("ICOM_LOG_FILE", "off")
    captured: dict[str, Any] = {}

    class FakeWebServer:
        def __init__(self, _radio: Any, cfg: Any) -> None:
            captured["config"] = cfg
            self._runtime_log_path = None

        async def serve_forever(self, *, on_started: Any = None) -> None:
            on_started()
            raise asyncio.CancelledError

    with patch("rigplane.web.server.WebServer", FakeWebServer):
        assert await _cmd_web(AsyncMock(), args) == 0
    return captured["config"]


@_webrtc
@pytest.mark.parametrize("include_vpn", [False, True])
async def test_cli_webrtc_include_vpn_flag_reaches_answer(
    monkeypatch: pytest.MonkeyPatch, include_vpn: bool
) -> None:
    """The ``--webrtc-include-vpn`` flag changes the answer's candidates.

    Real CLI parsing (``_build_parser``) → real startup config
    (``_cmd_web``) → real server (``WebServer``) → real answer
    (``negotiate``): a second real local address classified as a VPN
    tunnel through typed facts (``WindowsAdapterFacts`` →
    ``classify_windows_interface`` → ``AddressInfo`` — no raw dicts) has
    no host candidate without the flag and gains one with it. Removing
    the CLI→config assignment leaves the flagged run red.
    """
    import aioice.ice

    from rigplane.web.server import WebServer
    from rigplane.web.transport import ice_gathering as ice_gathering

    lan = _default_route_ipv4()
    others = [a for a in aioice.ice.get_host_addresses(True, False) if a != lan]
    if not lan or not others:
        pytest.skip("needs a default-route address plus a second bindable IPv4 address")
    tunnel_ip = others[0]

    args = _parse_web_cli(*("--webrtc-include-vpn",) if include_vpn else ())
    assert args.webrtc_include_vpn is include_vpn
    cfg = await _config_from_cli_startup(args, monkeypatch)
    assert cfg.webrtc_include_vpn is include_vpn
    assert cfg.webrtc_enabled is True

    tunnel_kind = classify_windows_interface(
        WindowsAdapterFacts(
            if_type=IF_TYPE_TUNNEL,
            hardware_interface=False,
            oper_up=True,
            friendly_name="Test VPN",
        )
    )
    assert tunnel_kind is IfKind.TUNNEL
    monkeypatch.setattr(
        ice_gathering,
        "_enumerate_local_addresses",
        lambda: [
            AddressInfo(
                ip=lan, ifname="en0", kind=IfKind.PHYSICAL, is_default_route=True
            ),
            AddressInfo(ip=tunnel_ip, ifname="Test VPN", kind=tunnel_kind),
        ],
    )
    server = WebServer(None, cfg)
    try:
        sdp, offer_type = await _offer_sdp()
        result = await server._webrtc_session_manager().negotiate(  # noqa: SLF001
            sdp, offer_type
        )
    finally:
        if server._webrtc_sessions is not None:  # noqa: SLF001
            await server._webrtc_sessions.close_all()  # noqa: SLF001
    hosts = [ln for ln in _answer_candidate_lines(result["sdp"]) if "typ host" in ln]
    assert hosts, "an answer without host candidates proves nothing"
    if include_vpn:
        assert any(tunnel_ip in ln for ln in hosts), hosts
    else:
        assert not any(tunnel_ip in ln for ln in hosts), hosts


# --------------------------------------------------------------------------
# 9-10. gathering bound with STUN
# --------------------------------------------------------------------------


class _StunResponder(asyncio.DatagramProtocol):
    """Minimal STUN Binding responder: answers requests with MAPPED-ADDRESS."""

    def __init__(self) -> None:
        self.transport: asyncio.DatagramTransport | None = None
        self.received: list[Any] = []

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: Any) -> None:
        if len(data) < 20 or data[0:2] != b"\x00\x01":
            return
        self.received.append(addr)
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
        header = b"\x01\x01" + struct.pack("!H", len(attrs)) + cookie + txn
        if self.transport is not None:
            self.transport.sendto(header + attrs, addr)


def _default_route_ipv4() -> str:
    """Source IPv4 address of the default route (a UDP connect sends nothing).

    A STUN responder bound to loopback is unreachable from a LAN source
    address on Windows, so the responder below binds here instead.
    """
    with contextlib.suppress(Exception):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            probe.connect(("8.8.8.8", 53))
            ip = probe.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
        finally:
            probe.close()
    return ""


@_webrtc
@pytest.mark.realtime(
    reason="measures real ICE/STUN wall-clock time against live sockets"
)
async def test_multihomed_answer_fast_with_stun() -> None:
    """A responsive STUN server yields srflx candidates well within the bound.

    The responder binds the default-route IPv4 address (not loopback): a
    LAN host address cannot reach a 127.0.0.1 responder on Windows, which
    made this test wait out the srflx timeout there.
    """
    stun_host = _default_route_ipv4()
    if not stun_host:
        pytest.skip("host exposes only loopback addresses")

    from rigplane.web.transport.webrtc_session import WebRtcSessionManager

    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        _StunResponder, local_addr=(stun_host, 0)
    )
    port = transport.get_extra_info("sockname")[1]
    manager = WebRtcSessionManager(None, None, "test")
    try:
        sdp, offer_type = await _offer_sdp()
        start = time.monotonic()
        result = await manager.negotiate(
            sdp,
            offer_type,
            ice_servers=[IceServer(urls=[f"stun:{stun_host}:{port}"])],
        )
        elapsed = time.monotonic() - start
    finally:
        transport.close()
        await manager.close_all()
    assert elapsed <= 1.0, elapsed
    lines = _answer_candidate_lines(result["sdp"])
    assert any("typ srflx" in ln for ln in lines), lines


class _SelectiveStunResponder(_StunResponder):
    """STUN responder that answers only requests from allowed sources.

    Models a second local interface that cannot reach the STUN server: the
    ignored source's transaction never completes, so without the
    default-route split the gather waits out the whole cap.
    """

    def __init__(self, allowed_sources: Container[str]) -> None:
        super().__init__()
        self._allowed = frozenset(allowed_sources)

    def datagram_received(self, data: bytes, addr: Any) -> None:
        if addr[0] not in self._allowed:
            return
        super().datagram_received(data, addr)


@_webrtc
@pytest.mark.realtime(
    reason="measures real ICE/STUN wall-clock time against live sockets"
)
async def test_unreachable_second_interface_does_not_stall_gather(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only the default-route address queries STUN; the rest stay host-only.

    The responder answers the default-route address but ignores the second
    local address, modelling a multihomed host whose second interface
    cannot reach the STUN server. The answer must come back well under the
    cap with exactly 1 srflx, plus host candidates for BOTH addresses.
    """
    import aioice.ice

    from rigplane.web.transport import ice_gathering as ice_gathering
    from rigplane.web.transport.webrtc_session import WebRtcSessionManager

    lan = _default_route_ipv4()
    if not lan:
        pytest.skip("host exposes only loopback addresses")
    # A second real, bindable IPv4 address (aioice excludes 127.0.0.1 from
    # its own list, so loopback cannot play that role here).
    others = [a for a in aioice.ice.get_host_addresses(True, False) if a != lan]
    if not others:
        pytest.skip("host has only one non-loopback IPv4 address")
    second = others[0]

    monkeypatch.setattr(
        ice_gathering,
        "_enumerate_local_addresses",
        lambda: [
            AddressInfo(
                ip=lan,
                ifname="en0",
                kind=IfKind.PHYSICAL,
                is_default_route=True,
            ),
            AddressInfo(ip=second, ifname="aux", kind=IfKind.PHYSICAL),
        ],
    )

    # Bind the STUN fake to the default-route address: on Windows a LAN
    # source address cannot reach a server on 127.0.0.1.
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _SelectiveStunResponder({lan}),
        local_addr=(lan, 0),
    )
    port = transport.get_extra_info("sockname")[1]
    manager = WebRtcSessionManager(None, None, "test")
    try:
        sdp, offer_type = await _offer_sdp()
        start = time.monotonic()
        result = await manager.negotiate(
            sdp,
            offer_type,
            ice_servers=[IceServer(urls=[f"stun:{lan}:{port}"])],
        )
        elapsed = time.monotonic() - start
        # The host sockets of BOTH addresses belong to the real connection
        # (connectivity checks on the second interface reach live ICE
        # state), and the STUN setting cleared for the host-only call is
        # restored afterwards.
        session = manager._sessions[result["sessionId"]]  # noqa: SLF001
        ice_transports = session.pc._RTCPeerConnection__iceTransports  # noqa: SLF001
        assert len(ice_transports) == 1
        connection = next(iter(ice_transports)).iceGatherer._connection  # noqa: SLF001
        assert connection.stun_server is not None
        protocols = list(connection._protocols)  # noqa: SLF001
        assert protocols
        assert all(protocol.receiver is connection for protocol in protocols)
    finally:
        transport.close()
        await manager.close_all()
    assert elapsed <= 1.0, elapsed
    lines = _answer_candidate_lines(result["sdp"])
    assert sum(1 for ln in lines if "typ srflx" in ln) == 1, lines
    hosts = [ln for ln in lines if "typ host" in ln]
    assert any(lan in ln for ln in hosts), hosts
    assert any(second in ln for ln in hosts), hosts


@_webrtc
@pytest.mark.realtime(
    reason="measures real ICE/STUN wall-clock time against live sockets"
)
async def test_no_default_route_falls_back_to_all_kept_querying_stun(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without a default-route address every kept address still queries STUN.

    Fallback path: the old behaviour holds (bounded wait, srflx obtained),
    so a host with no detectable default route loses nothing.
    """
    lan = _default_route_ipv4()

    from rigplane.web.transport import ice_gathering as ice_gathering
    from rigplane.web.transport.webrtc_session import WebRtcSessionManager

    infos = [
        AddressInfo(ip="127.0.0.1", ifname="lo", kind=IfKind.PHYSICAL),
    ]
    if lan:
        infos.append(AddressInfo(ip=lan, ifname="en0", kind=IfKind.PHYSICAL))
    monkeypatch.setattr(ice_gathering, "_enumerate_local_addresses", lambda: infos)

    # Bind the STUN fake to the default-route address: on Windows a LAN
    # source address cannot reach a server on 127.0.0.1.
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        _StunResponder, local_addr=(lan, 0)
    )
    port = transport.get_extra_info("sockname")[1]
    manager = WebRtcSessionManager(None, None, "test")
    try:
        sdp, offer_type = await _offer_sdp()
        start = time.monotonic()
        result = await manager.negotiate(
            sdp,
            offer_type,
            ice_servers=[IceServer(urls=[f"stun:{lan}:{port}"])],
        )
        elapsed = time.monotonic() - start
    finally:
        transport.close()
        await manager.close_all()
    assert elapsed <= GATHER_TIMEOUT_S + 1.0, elapsed
    lines = _answer_candidate_lines(result["sdp"])
    assert any("typ srflx" in ln for ln in lines), lines


@_webrtc
async def test_concurrent_negotiations_keep_their_own_policies() -> None:
    """Two concurrent negotiate() calls filter with their own policy each.

    Regression test for the patch/restore race: the old installer saved and
    restored process-wide aioice hooks around an ``await``, so two
    overlapping negotiations leaked one negotiation's patches over the
    other's. The hooks are now installed once per process and read the
    policy from a ContextVar set per negotiation; each answer must reflect
    only its own policy, and the enumerator hook must observe each policy
    in its own context (proving the context var is visible inside the task
    that awaits ``setLocalDescription``).

    The policy-isolation assertions hold on any host: the enumerator hook
    records each negotiation's policy even when an answer ends up empty.
    The answer-content contrast additionally needs a second local address
    besides the filtered one, so on a single-homed host the test verifies
    the isolation and skips the contrast.
    """
    import aioice.ice

    from rigplane.web.transport import ice_gathering as ice_gathering
    from rigplane.web.transport.webrtc_session import WebRtcSessionManager

    addrs = aioice.ice.get_host_addresses(True, True)
    ipv4 = [addr for addr in addrs if "." in addr]
    if not ipv4:
        pytest.skip("no IPv4 host address to filter on")
    target = ipv4[0]

    exclude = IceGatheringPolicy()
    include = IceGatheringPolicy(include_vpn=True)

    seen: list[object] = []
    real_enumerate = ice_gathering._enumerate_local_addresses

    def _fake_enumerate() -> list[AddressInfo]:
        # Runs inside the gather path: record which policy is active in
        # this task's context, and classify the (real, bindable) target
        # address as a tunnel so the two policies disagree on it.
        seen.append(ice_gathering._active_policy.get())
        return [AddressInfo(ip=target, ifname="tun0", kind=IfKind.TUNNEL)]

    ice_gathering._enumerate_local_addresses = _fake_enumerate
    manager_exclude = WebRtcSessionManager(None, None, "test", gather_policy=exclude)
    manager_include = WebRtcSessionManager(None, None, "test", gather_policy=include)
    try:
        sdp_a, type_a = await _offer_sdp()
        sdp_b, type_b = await _offer_sdp()
        answer_exclude, answer_include = await asyncio.gather(
            manager_exclude.negotiate(sdp_a, type_a),
            manager_include.negotiate(sdp_b, type_b),
        )
    finally:
        await manager_exclude.close_all()
        await manager_include.close_all()
        ice_gathering._enumerate_local_addresses = real_enumerate

    # The gather path observed each negotiation's own policy in its context.
    assert exclude in seen, seen
    assert include in seen, seen

    # No policy leaks out of negotiate(): the context is reset afterwards,
    # and a plain aioice gather outside any negotiate context is unfiltered.
    assert ice_gathering._active_policy.get() is None
    assert target in aioice.ice.get_host_addresses(True, True)

    if not any(addr != target for addr in addrs):
        pytest.skip(
            "single-address host: excluding the only address leaves "
            "nothing to contrast the policies against"
        )
    exclude_cands = _answer_candidate_lines(answer_exclude["sdp"])
    include_cands = _answer_candidate_lines(answer_include["sdp"])
    exclude_hosts = [ln for ln in exclude_cands if "typ host" in ln]
    include_hosts = [ln for ln in include_cands if "typ host" in ln]
    assert exclude_hosts, "answer without host candidates proves nothing"
    assert include_hosts, "answer without host candidates proves nothing"
    assert not any(target in ln for ln in exclude_hosts), exclude_hosts
    assert any(target in ln for ln in include_hosts), include_hosts


@_webrtc
async def test_installer_is_idempotent() -> None:
    """Calling the installer twice does not wrap the aioice hooks twice."""
    import aioice.ice

    from rigplane.web.transport import ice_gathering as ice_gathering

    ice_gathering.install_ice_gathering_policy()
    first_host = aioice.ice.get_host_addresses
    first_component = aioice.ice.Connection.get_component_candidates
    ice_gathering.install_ice_gathering_policy()
    ice_gathering.install_ice_gathering_policy()
    assert aioice.ice.get_host_addresses is first_host
    assert aioice.ice.Connection.get_component_candidates is first_component
    assert ice_gathering._installed is True


@_webrtc
def test_enumeration_failure_keeps_every_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raising enumerator degrades fail-open: the hook keeps aioice's
    full address list instead of dropping everything."""
    import aioice.ice

    from rigplane.web.transport import ice_gathering as ice_gathering

    ice_gathering.install_ice_gathering_policy()
    token = ice_gathering._active_policy.set(None)
    try:
        full = aioice.ice.get_host_addresses(True, True)
    finally:
        ice_gathering._active_policy.reset(token)

    def _raise() -> list[AddressInfo]:
        raise RuntimeError("simulated GetAdaptersAddresses failure")

    monkeypatch.setattr(ice_gathering, "_enumerate_local_addresses", _raise)
    with ice_gathering.apply_ice_gathering_policy(ice_gathering.IceGatheringPolicy()):
        assert aioice.ice.get_host_addresses(True, True) == full


@_webrtc
@pytest.mark.realtime(
    reason="measures real negotiation wall-clock time against live sockets"
)
async def test_negotiate_answers_within_bound_when_enumeration_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A raising enumerator must not break negotiation: the answer still
    carries host candidates and still lands within the gathering bound."""
    from rigplane.web.transport import ice_gathering as ice_gathering
    from rigplane.web.transport.webrtc_session import WebRtcSessionManager

    def _raise() -> list[AddressInfo]:
        raise RuntimeError("simulated GetAdaptersAddresses failure")

    monkeypatch.setattr(ice_gathering, "_enumerate_local_addresses", _raise)
    manager = WebRtcSessionManager(None, None, "test")
    try:
        sdp, offer_type = await _offer_sdp()
        start = time.monotonic()
        result = await manager.negotiate(sdp, offer_type)
        elapsed = time.monotonic() - start
    finally:
        await manager.close_all()
    assert elapsed <= GATHER_TIMEOUT_S + 1.0, elapsed
    lines = _answer_candidate_lines(result["sdp"])
    assert any("typ host" in ln for ln in lines), lines


@_webrtc
@pytest.mark.realtime(
    reason="measures real ICE/STUN wall-clock time against live sockets"
)
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


def test_real_enumeration_table() -> None:
    """Real enumeration on this host: at least one address, every entry
    classified, the default-route address present and kept by the policy.

    Prints the ``ifname ip kind kept`` table so it shows with ``pytest -s``.
    """
    from rigplane.web.transport import ice_gathering as ice_gathering

    infos = ice_gathering._enumerate_local_addresses()
    assert infos, "expected at least one local address"
    for info in infos:
        assert info.ip
        assert isinstance(info.kind, IfKind)
    kept = set(ice_gathering.IceGatheringPolicy().select(infos))
    print("\nifname ip kind kept")
    for info in infos:
        print(f"{info.ifname} {info.ip} {info.kind.value} {info.ip in kept}")

    default_ip = ""
    with contextlib.suppress(Exception):
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        with probe:
            probe.connect(("8.8.8.8", 53))
            default_ip = str(probe.getsockname()[0])
    if default_ip and not default_ip.startswith("127."):
        assert any(info.ip == default_ip for info in infos), default_ip
        assert default_ip in kept, default_ip
