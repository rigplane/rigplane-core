"""Host allowlist and Origin guard contracts (MOR-2880, MOR-2881, MOR-3108).

Pins the guards of the web server:

- a Host-header allowlist enforced on every WebSocket upgrade and every
  HTTP route before any handler (421 on refusal), and
- a same-origin ``Origin`` rule on the four WebSocket upgrades and on
  every state-changing HTTP request (403 on refusal, no loopback
  exception). Read-only ``GET``/``HEAD`` routes admit any ``Origin``.
- an explicit trusted public Origin allowlist (MOR-3108 b11): the
  operator's own serialized HTTPS origin behind a reverse proxy with an
  HTTP/internal upstream, admitted past the same-origin rule without
  bypassing the Host allowlist or any other guard.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from rigplane.web import server as web_server
from rigplane.web.server import WebConfig, WebServer

WS_PATHS = [
    "/api/v1/ws",
    "/api/v1/scope",
    "/api/v1/audio-scope",
    "/api/v1/audio",
]
GOOD_KEY = "dGhlIHNhbXBsZSBub25jZQ=="
MISDIRECTED = {"error": "misdirected request: host not allowed"}


class _MemoryWriter:
    """Minimal asyncio.StreamWriter stand-in that captures written bytes."""

    def __init__(self) -> None:
        self.buffer = bytearray()
        self.closed = False

    def write(self, data: bytes) -> None:
        self.buffer.extend(data)

    async def drain(self) -> None:  # pragma: no cover - trivial
        return

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:  # pragma: no cover
        return

    def is_closing(self) -> bool:
        return self.closed

    def get_extra_info(self, *_args: Any, **_kwargs: Any) -> Any:
        return ("127.0.0.1", 0)


def _make_srv(**config_kwargs: Any) -> WebServer:
    srv = WebServer(radio=None, config=WebConfig(**config_kwargs))
    # /api/v1/audio-scope refuses to construct its handler without it.
    srv._audio_fft_scope = object()
    return srv


def _patch_ws_handlers(monkeypatch: pytest.MonkeyPatch) -> dict[str, MagicMock]:
    handlers: dict[str, MagicMock] = {}
    for name in ("ControlHandler", "ScopeHandler", "AudioHandler"):
        factory = MagicMock(return_value=MagicMock(run=AsyncMock()))
        monkeypatch.setattr(web_server, name, factory)
        handlers[name] = factory
    return handlers


def _response_body(writer: _MemoryWriter) -> bytes:
    return bytes(writer.buffer).split(b"\r\n\r\n", 1)[1]


# ---------------------------------------------------------------------------
# WebSocket Origin rule (all four upgrade paths)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("path", WS_PATHS)
@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://127.0.0.1:9999",  # right host, wrong port
        "null",  # browsers' opaque origin
    ],
)
@pytest.mark.parametrize("bind_host", ["0.0.0.0", "127.0.0.1"])
async def test_ws_foreign_origin_refused_before_upgrade(
    monkeypatch: pytest.MonkeyPatch, path: str, origin: str, bind_host: str
) -> None:
    handlers = _patch_ws_handlers(monkeypatch)
    srv = _make_srv(host=bind_host)
    writer = _MemoryWriter()
    await srv._handle_websocket(
        asyncio.StreamReader(),
        writer,
        path,
        {
            "sec-websocket-key": GOOD_KEY,
            "origin": origin,
            "host": "127.0.0.1:8470",
        },
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 403 ")
    assert sum(factory.call_count for factory in handlers.values()) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("path", WS_PATHS)
@pytest.mark.parametrize(
    "tls,scheme",
    [(False, "http"), (True, "https")],
)
async def test_ws_same_origin_accepted(
    monkeypatch: pytest.MonkeyPatch, path: str, tls: bool, scheme: str
) -> None:
    handlers = _patch_ws_handlers(monkeypatch)
    srv = _make_srv(tls=tls)
    writer = _MemoryWriter()
    await srv._handle_websocket(
        asyncio.StreamReader(),
        writer,
        path,
        {
            "sec-websocket-key": GOOD_KEY,
            "origin": f"{scheme}://127.0.0.1:8470",
            "host": "127.0.0.1:8470",
        },
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 101 ")
    assert sum(factory.call_count for factory in handlers.values()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("path", WS_PATHS)
async def test_ws_missing_origin_accepted(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    handlers = _patch_ws_handlers(monkeypatch)
    srv = _make_srv()
    writer = _MemoryWriter()
    await srv._handle_websocket(
        asyncio.StreamReader(),
        writer,
        path,
        {"sec-websocket-key": GOOD_KEY, "host": "127.0.0.1:8470"},
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 101 ")
    assert sum(factory.call_count for factory in handlers.values()) == 1


# ---------------------------------------------------------------------------
# Host allowlist — WebSocket upgrade
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host",
    [
        "evil.example:8099",
        "evil.example",
        "",  # present but empty
        "not a host",
        "a:b:c",  # unbracketed multi-colon
        "[::1:8470",  # unterminated bracket
        "host:",  # empty port
        "router.lan:80",  # .lan is conventional, not reserved
        "evil.lan",
        "X.LAN",  # refusal is case-insensitive
        "localhost:80:90",  # multi-colon, not a bare IPv6 literal
        "mymac:abc",  # non-numeric port tail
        "127.0.0.1:99999999",  # port tail longer than 5 digits
        "my_mac:8470",  # underscore fails the single-label pattern
    ],
)
async def test_ws_foreign_or_malformed_host_refused_with_421(
    monkeypatch: pytest.MonkeyPatch, host: str
) -> None:
    handlers = _patch_ws_handlers(monkeypatch)
    srv = _make_srv()
    writer = _MemoryWriter()
    await srv._handle_websocket(
        asyncio.StreamReader(),
        writer,
        "/api/v1/ws",
        {"sec-websocket-key": GOOD_KEY, "host": host},
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 421 ")
    assert json.loads(_response_body(writer)) == MISDIRECTED
    assert sum(factory.call_count for factory in handlers.values()) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host",
    [
        "192.168.55.77:8099",  # IPv4 literal
        "[::1]:8470",  # bracketed IPv6 with port
        "[::1]",
        "::1",  # bare IPv6 literal
        "localhost:8470",
        "LOCALHOST:8470",  # single-label match is case-insensitive
        "localhost.:8470",  # trailing dot stripped before every check
        "stand77:8470",  # bare single-label hostname
        "x.local.",  # trailing dot ignored on local suffixes
        "rack.home.arpa:8470",
        "box.internal",
        None,  # missing Host header
    ],
)
async def test_ws_local_hosts_accepted(
    monkeypatch: pytest.MonkeyPatch, host: str | None
) -> None:
    handlers = _patch_ws_handlers(monkeypatch)
    srv = _make_srv()
    writer = _MemoryWriter()
    headers = {"sec-websocket-key": GOOD_KEY}
    if host is not None:
        headers["host"] = host
    await srv._handle_websocket(asyncio.StreamReader(), writer, "/api/v1/ws", headers)
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 101 ")
    assert sum(factory.call_count for factory in handlers.values()) == 1


@pytest.mark.asyncio
async def test_ws_allowed_host_flag_admits_configured_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handlers = _patch_ws_handlers(monkeypatch)
    srv = _make_srv(allowed_hosts=("stand77.msmsoft.net",))
    writer = _MemoryWriter()
    await srv._handle_websocket(
        asyncio.StreamReader(),
        writer,
        "/api/v1/ws",
        {"sec-websocket-key": GOOD_KEY, "host": "stand77.msmsoft.net:8470"},
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 101 ")
    assert sum(factory.call_count for factory in handlers.values()) == 1


@pytest.mark.asyncio
async def test_ws_allowed_host_name_still_refused_without_flag() -> None:
    srv = _make_srv()
    writer = _MemoryWriter()
    await srv._handle_websocket(
        asyncio.StreamReader(),
        writer,
        "/api/v1/ws",
        {"sec-websocket-key": GOOD_KEY, "host": "stand77.msmsoft.net:8470"},
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 421 ")
    assert json.loads(_response_body(writer)) == MISDIRECTED


# ---------------------------------------------------------------------------
# Host allowlist — HTTP routes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host",
    [
        "evil.example",
        "evil.example:8099",
        "",
        "a:b:c",
        "stand77.msmsoft.net",
        "router.lan:80",
        "evil.lan",
        "X.LAN",
        "localhost:80:90",
        "mymac:abc",
        "127.0.0.1:99999999",
        "my_mac:8470",
    ],
)
async def test_http_foreign_or_malformed_host_refused_with_421(host: str) -> None:
    srv = _make_srv()
    writer = _MemoryWriter()
    await srv._handle_http(writer, "GET", "/api/v1/info", {"host": host})
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 421 ")
    assert json.loads(_response_body(writer)) == MISDIRECTED


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host",
    [
        "192.168.55.77:8099",
        "[::1]:8470",
        "::1",
        "localhost:8470",
        "localhost.:8470",
        "stand77:8470",
        "x.local.",
        None,  # missing Host header
    ],
)
async def test_http_local_hosts_accepted(host: str | None) -> None:
    srv = _make_srv()
    writer = _MemoryWriter()
    headers = {"host": host} if host is not None else {}
    await srv._handle_http(writer, "GET", "/api/v1/info", headers)
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 200 ")


@pytest.mark.asyncio
async def test_http_allowed_host_flag_admits_configured_name() -> None:
    srv = _make_srv(allowed_hosts=("stand77.msmsoft.net",))
    writer = _MemoryWriter()
    await srv._handle_http(
        writer, "GET", "/api/v1/info", {"host": "stand77.msmsoft.net:8470"}
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 200 ")


@pytest.mark.asyncio
async def test_http_refused_host_checked_before_route_semantics() -> None:
    """Even an unknown path gets 421 (not 404) when the Host is foreign."""
    srv = _make_srv()
    writer = _MemoryWriter()
    await srv._handle_http(
        writer, "GET", "/api/v1/nonexistent", {"host": "evil.example"}
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 421 ")


# ---------------------------------------------------------------------------
# HTTP Origin rule on state-changing requests (MOR-2881)
# ---------------------------------------------------------------------------

#: POST /api/v1/commands with no radio configured answers 503 ``no_radio``
#: from the handler — proof the request REACHED it rather than being
#: refused by a guard.
_HANDLER_REACHED_STATUS_LINE = b"HTTP/1.1 503 "


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize(
    "origin",
    [
        "https://evil.example",
        "http://127.0.0.1:9999",  # right host, wrong port
        "null",  # browsers' opaque origin
    ],
)
@pytest.mark.parametrize("bind_host", ["0.0.0.0", "127.0.0.1", "192.168.55.77"])
async def test_http_state_changing_foreign_origin_refused(
    method: str, origin: str, bind_host: str
) -> None:
    """A foreign Origin on a state-changing request → 403, before any handler.

    Inverts the #3871 deferral pin: with an allowed Host such a request
    used to be admitted; the same-origin rule now covers every
    state-changing HTTP request with no loopback exception.
    """
    srv = _make_srv(host=bind_host)
    writer = _MemoryWriter()
    await srv._handle_http(
        writer,
        method,
        "/api/v1/commands",
        {"origin": origin, "host": "127.0.0.1:8470"},
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 403 ")
    assert json.loads(_response_body(writer)) == {
        "error": "forbidden: origin not allowed"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("tls,scheme", [(False, "http"), (True, "https")])
async def test_http_state_changing_same_origin_admitted(tls: bool, scheme: str) -> None:
    """A same-origin Origin reaches the route handler (503, not 403/421)."""
    srv = _make_srv(tls=tls)
    writer = _MemoryWriter()
    await srv._handle_http(
        writer,
        "POST",
        "/api/v1/commands",
        {"origin": f"{scheme}://127.0.0.1:8470", "host": "127.0.0.1:8470"},
    )
    assert bytes(writer.buffer).startswith(_HANDLER_REACHED_STATUS_LINE)


@pytest.mark.asyncio
async def test_http_state_changing_missing_origin_admitted() -> None:
    """No Origin with Host 127.0.0.1:<port> — the Pro-proxied request
    shape — is admitted and reaches the route handler."""
    srv = _make_srv()
    writer = _MemoryWriter()
    await srv._handle_http(
        writer, "POST", "/api/v1/commands", {"host": "127.0.0.1:8470"}
    )
    assert bytes(writer.buffer).startswith(_HANDLER_REACHED_STATUS_LINE)


@pytest.mark.asyncio
async def test_http_origin_checked_before_route_semantics() -> None:
    """Even an unknown path gets 403 (not 404/405) when the Origin is
    foreign and the method is state-changing."""
    srv = _make_srv()
    writer = _MemoryWriter()
    await srv._handle_http(
        writer,
        "POST",
        "/api/v1/nonexistent",
        {"origin": "https://evil.example", "host": "127.0.0.1:8470"},
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 403 ")


@pytest.mark.asyncio
async def test_http_clearcache_foreign_origin_refused() -> None:
    """GET /clearcache changes client state (Clear-Site-Data wipes the
    browser's storage for this origin), so the rule covers it too."""
    srv = _make_srv()
    writer = _MemoryWriter()
    await srv._handle_http(
        writer,
        "GET",
        "/clearcache",
        {"origin": "https://evil.example", "host": "127.0.0.1:8470"},
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 403 ")


@pytest.mark.asyncio
async def test_http_clearcache_missing_origin_admitted() -> None:
    """GET /clearcache without an Origin (typed URL, same-origin link)
    still clears."""
    srv = _make_srv()
    writer = _MemoryWriter()
    await srv._handle_http(writer, "GET", "/clearcache", {"host": "127.0.0.1:8470"})
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 200 ")


@pytest.mark.asyncio
async def test_http_read_only_get_foreign_origin_still_admitted() -> None:
    """Scope boundary: read-only GET routes admit a foreign Origin."""
    srv = _make_srv()
    writer = _MemoryWriter()
    await srv._handle_http(
        writer,
        "GET",
        "/api/v1/info",
        {"origin": "https://evil.example", "host": "127.0.0.1:8470"},
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 200 ")


# ---------------------------------------------------------------------------
# Explicit trusted public Origin (MOR-3108 b11)
# ---------------------------------------------------------------------------

#: The operator's fictional public HTTPS origin in front of this
#: HTTP/internal upstream (the reverse-proxy shape).
_TRUSTED = ("https://station.example",)


@pytest.mark.asyncio
@pytest.mark.parametrize("path", WS_PATHS)
async def test_ws_trusted_origin_admitted_behind_https_proxy(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """Opted-in: the browser's public HTTPS Origin is admitted on all
    four WS upgrades behind the operator's HTTPS reverse proxy over
    this HTTP upstream (127.0.0.1 raw Host passes the allowlist)."""
    handlers = _patch_ws_handlers(monkeypatch)
    srv = _make_srv(trusted_origins=_TRUSTED)
    writer = _MemoryWriter()
    await srv._handle_websocket(
        asyncio.StreamReader(),
        writer,
        path,
        {
            "sec-websocket-key": GOOD_KEY,
            "origin": "https://station.example",
            "host": "127.0.0.1:8080",
        },
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 101 ")
    assert sum(factory.call_count for factory in handlers.values()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("path", WS_PATHS)
async def test_ws_public_origin_refused_without_trusted_flag(
    monkeypatch: pytest.MonkeyPatch, path: str
) -> None:
    """Default refusal: without --trusted-origin the public Origin is
    just a foreign Origin."""
    handlers = _patch_ws_handlers(monkeypatch)
    srv = _make_srv()
    writer = _MemoryWriter()
    await srv._handle_websocket(
        asyncio.StreamReader(),
        writer,
        path,
        {
            "sec-websocket-key": GOOD_KEY,
            "origin": "https://station.example",
            "host": "127.0.0.1:8080",
        },
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 403 ")
    assert sum(factory.call_count for factory in handlers.values()) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    [
        "https://other.example",  # wrong host
        "http://station.example",  # wrong scheme
        "https://station.example:8443",  # trusted host, nondefault port
    ],
)
async def test_ws_trusted_origin_mismatch_refused(
    monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    handlers = _patch_ws_handlers(monkeypatch)
    srv = _make_srv(trusted_origins=_TRUSTED)
    writer = _MemoryWriter()
    await srv._handle_websocket(
        asyncio.StreamReader(),
        writer,
        "/api/v1/ws",
        {
            "sec-websocket-key": GOOD_KEY,
            "origin": origin,
            "host": "127.0.0.1:8080",
        },
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 403 ")
    assert json.loads(_response_body(writer)) == {
        "error": "forbidden: origin not allowed"
    }
    assert sum(factory.call_count for factory in handlers.values()) == 0


@pytest.mark.asyncio
async def test_ws_trusted_origin_case_and_default_port_normalized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The trusted comparison is the one normalized place: scheme/host
    case and the default port (443) canonicalize, so an explicitly
    written default port and uppercase host still match."""
    handlers = _patch_ws_handlers(monkeypatch)
    srv = _make_srv(trusted_origins=_TRUSTED)
    writer = _MemoryWriter()
    await srv._handle_websocket(
        asyncio.StreamReader(),
        writer,
        "/api/v1/ws",
        {
            "sec-websocket-key": GOOD_KEY,
            "origin": "HTTPS://STATION.example:443",
            "host": "127.0.0.1:8080",
        },
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 101 ")
    assert sum(factory.call_count for factory in handlers.values()) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    [
        "https://station.example:99999",  # out-of-range port: ValueError, fail closed
        "https://station.example/",  # path (trailing slash)
        "https://station.example?q=1",  # query
        "https://station.example#frag",  # fragment
        "https://user@station.example",  # userinfo
        "null",  # opaque origin
    ],
)
async def test_ws_malformed_trusted_shape_request_origin_fail_closed(
    monkeypatch: pytest.MonkeyPatch, origin: str
) -> None:
    """A malformed request Origin fails closed even though the trusted
    host itself is configured — including the ValueError paths."""
    handlers = _patch_ws_handlers(monkeypatch)
    srv = _make_srv(trusted_origins=_TRUSTED)
    writer = _MemoryWriter()
    await srv._handle_websocket(
        asyncio.StreamReader(),
        writer,
        "/api/v1/ws",
        {
            "sec-websocket-key": GOOD_KEY,
            "origin": origin,
            "host": "127.0.0.1:8080",
        },
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 403 ")
    assert sum(factory.call_count for factory in handlers.values()) == 0


@pytest.mark.asyncio
async def test_ws_trusted_origin_does_not_bypass_host_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A trusted Origin with a foreign raw Host is 421: the allowlist
    never bypasses the Host guard."""
    handlers = _patch_ws_handlers(monkeypatch)
    srv = _make_srv(trusted_origins=_TRUSTED)
    writer = _MemoryWriter()
    await srv._handle_websocket(
        asyncio.StreamReader(),
        writer,
        "/api/v1/ws",
        {
            "sec-websocket-key": GOOD_KEY,
            "origin": "https://station.example",
            "host": "evil.example",
        },
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 421 ")
    assert json.loads(_response_body(writer)) == MISDIRECTED
    assert sum(factory.call_count for factory in handlers.values()) == 0


@pytest.mark.asyncio
async def test_ws_trusted_origin_requires_present_raw_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The trusted branch requires a present raw Host: a missing Host
    header (admitted by the Host guard) still refuses the upgrade."""
    handlers = _patch_ws_handlers(monkeypatch)
    srv = _make_srv(trusted_origins=_TRUSTED)
    writer = _MemoryWriter()
    await srv._handle_websocket(
        asyncio.StreamReader(),
        writer,
        "/api/v1/ws",
        {"sec-websocket-key": GOOD_KEY, "origin": "https://station.example"},
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 403 ")
    assert sum(factory.call_count for factory in handlers.values()) == 0


@pytest.mark.asyncio
async def test_ws_forwarded_headers_alone_never_admit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No trust from Forwarded/X-Forwarded-*: a foreign Origin stays
    refused no matter what the proxy-metadata headers claim."""
    handlers = _patch_ws_handlers(monkeypatch)
    srv = _make_srv(trusted_origins=_TRUSTED)
    writer = _MemoryWriter()
    await srv._handle_websocket(
        asyncio.StreamReader(),
        writer,
        "/api/v1/ws",
        {
            "sec-websocket-key": GOOD_KEY,
            "origin": "https://evil.example",
            "host": "127.0.0.1:8080",
            "x-forwarded-host": "station.example",
            "x-forwarded-proto": "https",
            "forwarded": "for=203.0.113.9;host=station.example;proto=https",
        },
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 403 ")
    assert sum(factory.call_count for factory in handlers.values()) == 0


@pytest.mark.asyncio
async def test_http_trusted_origin_state_changing_admitted() -> None:
    """Opted-in: POST with the trusted public Origin reaches the route
    handler (503 no_radio, not 403/421)."""
    srv = _make_srv(trusted_origins=_TRUSTED)
    writer = _MemoryWriter()
    await srv._handle_http(
        writer,
        "POST",
        "/api/v1/commands",
        {"origin": "https://station.example", "host": "127.0.0.1:8080"},
    )
    assert bytes(writer.buffer).startswith(_HANDLER_REACHED_STATUS_LINE)


@pytest.mark.asyncio
async def test_http_trusted_origin_clearcache_admitted() -> None:
    """GET /clearcache with the trusted public Origin still clears."""
    srv = _make_srv(trusted_origins=_TRUSTED)
    writer = _MemoryWriter()
    await srv._handle_http(
        writer,
        "GET",
        "/clearcache",
        {"origin": "https://station.example", "host": "127.0.0.1:8080"},
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 200 ")


@pytest.mark.asyncio
async def test_http_public_origin_refused_without_trusted_flag() -> None:
    """Default refusal: the same POST without the flag is 403."""
    srv = _make_srv()
    writer = _MemoryWriter()
    await srv._handle_http(
        writer,
        "POST",
        "/api/v1/commands",
        {"origin": "https://station.example", "host": "127.0.0.1:8080"},
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 403 ")
    assert json.loads(_response_body(writer)) == {
        "error": "forbidden: origin not allowed"
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "origin",
    [
        "https://other.example",
        "http://station.example",
        "https://station.example:8443",
    ],
)
async def test_http_trusted_origin_mismatch_refused(origin: str) -> None:
    srv = _make_srv(trusted_origins=_TRUSTED)
    writer = _MemoryWriter()
    await srv._handle_http(
        writer,
        "POST",
        "/api/v1/commands",
        {"origin": origin, "host": "127.0.0.1:8080"},
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 403 ")
    assert json.loads(_response_body(writer)) == {
        "error": "forbidden: origin not allowed"
    }


@pytest.mark.asyncio
async def test_http_trusted_origin_does_not_bypass_host_guard() -> None:
    """A trusted Origin with a foreign raw Host is 421 (Host guard
    first), never admitted by the allowlist."""
    srv = _make_srv(trusted_origins=_TRUSTED)
    writer = _MemoryWriter()
    await srv._handle_http(
        writer,
        "POST",
        "/api/v1/commands",
        {"origin": "https://station.example", "host": "evil.example"},
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 421 ")
    assert json.loads(_response_body(writer)) == MISDIRECTED


@pytest.mark.asyncio
async def test_http_trusted_origin_requires_present_raw_host() -> None:
    """No Host header: same-origin would refuse anyway, and the trusted
    branch requires a present raw Host → 403."""
    srv = _make_srv(trusted_origins=_TRUSTED)
    writer = _MemoryWriter()
    await srv._handle_http(
        writer,
        "POST",
        "/api/v1/commands",
        {"origin": "https://station.example"},
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 403 ")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "proxy_headers",
    [
        {"x-forwarded-host": "station.example"},
        {"x-forwarded-proto": "https"},
        {
            "x-forwarded-host": "station.example",
            "x-forwarded-proto": "https",
            "forwarded": "for=203.0.113.9;host=station.example;proto=https",
        },
    ],
)
async def test_http_forwarded_headers_alone_never_admit(
    proxy_headers: dict[str, str],
) -> None:
    """No trust from Forwarded/X-Forwarded-*: a foreign Origin on a
    state-changing request stays refused."""
    srv = _make_srv(trusted_origins=_TRUSTED)
    writer = _MemoryWriter()
    await srv._handle_http(
        writer,
        "POST",
        "/api/v1/commands",
        {
            "origin": "https://evil.example",
            "host": "127.0.0.1:8080",
            **proxy_headers,
        },
    )
    assert bytes(writer.buffer).startswith(b"HTTP/1.1 403 ")
    assert json.loads(_response_body(writer)) == {
        "error": "forbidden: origin not allowed"
    }


# ---------------------------------------------------------------------------
# WebConfig.trusted_origins validation (MOR-3108)
# ---------------------------------------------------------------------------

_INVALID_TRUSTED_ORIGINS = [
    None,  # null
    "",  # empty
    "https://*.example",  # wildcard
    "https://*",
    "https://user:pass@station.example",  # userinfo
    "https://user@station.example",
    "https://station.example/",  # path: trailing slash
    "https://station.example/ui",
    "https://station.example?q=1",  # query
    "https://station.example#frag",  # fragment
    "https://station.example?",
    "https://station.example#",
    "\x00https://station.example",
    "https://station.example\x00",
    "https://station.example\x7f",
    "https://station.example:0",
    "https://station.example%2f",
    "https://station..example",
    "https://-station.example",
    "https://999.999.999.999",
    " https://station.example",  # leading whitespace
    "https://station.example ",  # trailing whitespace
    "https://station.example\t",
    "https://station.example:99999",  # out-of-range port
    "https://station.example:abc",  # non-numeric port
    "https://station.example:",  # empty port
    "ftp://station.example",  # scheme
    "//station.example",  # no scheme
    "https://",  # empty host
    "https://:443",  # empty host
]


@pytest.mark.parametrize("bad", _INVALID_TRUSTED_ORIGINS)
def test_webconfig_rejects_invalid_trusted_origins_before_serving(
    bad: object,
) -> None:
    """Invalid configuration fails at construction, before serving."""
    with pytest.raises(ValueError):
        WebConfig(trusted_origins=(bad,))  # type: ignore[arg-type]


def test_webconfig_trusted_origins_default_empty_tuple() -> None:
    assert WebConfig().trusted_origins == ()


def test_webconfig_accepts_valid_trusted_origins_verbatim() -> None:
    """Valid origins are stored verbatim; normalization happens only in
    the comparison, not in the configuration."""
    cfg = WebConfig(
        trusted_origins=(
            "https://Station.Example",
            "http://127.0.0.1:8080",
        )
    )
    assert cfg.trusted_origins == (
        "https://Station.Example",
        "http://127.0.0.1:8080",
    )
