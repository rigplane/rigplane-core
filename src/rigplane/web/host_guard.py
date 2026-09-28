"""Host allowlist and same-Origin rules for the web server (MOR-2880).

Two guards against a web page running in the operator's browser:

- **Host allowlist** (DNS-rebinding defence, mirrors Pro's MOR-2877
  rule): every HTTP route and WebSocket upgrade reads the RAW ``Host``
  request header — never a value derived from the bind address — and
  admits only local names. Everything else gets ``421``.
- **Same-Origin on WebSocket upgrades**: a present ``Origin`` must be
  same-origin with the request (scheme, host, port) and its host must
  pass the same allowlist; otherwise ``403`` before the upgrade. A
  missing ``Origin`` is admitted (non-browser clients; the Pro
  supervisor's proxy sends none on its WebSocket leg).

The Origin-check-on-HTTP-routes half is deferred to MOR-2881 until Pro
stops forwarding the browser's Origin upstream (MOR-2877).
"""

from __future__ import annotations

import ipaddress
import json
import urllib.parse
from collections.abc import Collection

__all__ = [
    "MISDIRECTED_BODY",
    "ORIGIN_FORBIDDEN_BODY",
    "host_header_allowed",
    "origin_matches_host",
    "websocket_origin_allowed",
]

# Special-use / local-only name suffixes admitted by the allowlist.
_LOCAL_NAME_SUFFIXES = (
    ".localhost",
    ".local",
    ".lan",
    ".home.arpa",
    ".internal",
)

# A Host host-part may never contain these (defence-in-depth on top of
# the port/bracket parsing below).
_FORBIDDEN_HOST_CHARS = frozenset(" \t/\\?#@[]")

#: Body every misdirected (421) refusal answers with.
MISDIRECTED_BODY = json.dumps(
    {"error": "misdirected request: host not allowed"}
).encode("ascii")

#: Body every refused (403) WebSocket upgrade answers with.
ORIGIN_FORBIDDEN_BODY = json.dumps({"error": "forbidden: origin not allowed"}).encode(
    "ascii"
)


def _normalized_extra_hosts(extra_hosts: Collection[str]) -> frozenset[str]:
    return frozenset(
        host.strip().lower() for host in extra_hosts if host and host.strip()
    )


def _split_port(raw_host: str) -> str | None:
    """Return the lowercase host part of a raw ``Host`` header value.

    The port is stripped; IPv6 literals arrive in brackets
    (``[::1]:8470``) and lose them. Returns ``None`` for empty or
    malformed values (bad port, unterminated bracket, forbidden
    characters, embedded colon).
    """
    value = raw_host.strip()
    if not value:
        return None
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            return None
        host = value[1:end]
        rest = value[end + 1 :]
        if rest and (not rest.startswith(":") or not rest[1:].isdigit()):
            return None
        if not host:
            return None
        return host.lower()
    if ":" in value:
        host, _, port = value.rpartition(":")
        if ":" in host or not port.isdigit():
            return None
    else:
        host = value
    if not host or _FORBIDDEN_HOST_CHARS.intersection(host):
        return None
    return host.lower()


def _host_name_allowed(name: str, extra_hosts: frozenset[str]) -> bool:
    """Admission rule for an already port-stripped, lowercased host."""
    if not name:
        return False
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    if "." not in name:
        # Single-label name: ``localhost``, bare hostnames.
        return True
    stripped = name[:-1] if name.endswith(".") else name
    for suffix in _LOCAL_NAME_SUFFIXES:
        if len(stripped) > len(suffix) and stripped.endswith(suffix):
            return True
    return name in extra_hosts


def host_header_allowed(
    raw_host: str | None,
    extra_hosts: Collection[str] = (),
) -> bool:
    """True when the raw ``Host`` header value passes the allowlist.

    A MISSING header (``None``) is admitted — HTTP/1.0-style and
    proxied legroom. An empty or malformed value is refused.
    """
    if raw_host is None:
        return True
    name = _split_port(raw_host)
    if name is None:
        return False
    return _host_name_allowed(name, _normalized_extra_hosts(extra_hosts))


def origin_matches_host(
    origin: str,
    request_host: str | None,
    schemes: Collection[str] = ("http", "https"),
) -> bool:
    """True when *origin* serializes to ``<scheme>://<request_host>``.

    The same-origin comparison shared with the diagnostic routes'
    :func:`rigplane.web.handlers.diagnostics.check_origin_or_loopback`
    (which admits both schemes because it cannot know the listener's);
    the WebSocket guard passes the request's actual scheme only.
    Comparison is case-insensitive (scheme and host are, per RFC 3986).
    """
    if not request_host:
        return False
    lowered = origin.lower()
    return any(lowered == f"{scheme}://{request_host}".lower() for scheme in schemes)


def websocket_origin_allowed(
    origin: str,
    raw_host: str | None,
    scheme: str,
    extra_hosts: Collection[str] = (),
) -> bool:
    """Same-origin rule for the four WebSocket upgrades.

    *origin* must parse as an absolute http(s) URL whose serialization
    equals ``scheme://<raw Host header>`` (so scheme, host and port all
    match the request), and its host must pass the same allowlist.
    """
    parts = urllib.parse.urlsplit(origin)
    if parts.scheme not in ("http", "https"):
        return False
    try:
        hostname = parts.hostname
        _ = parts.port  # raises ValueError on a malformed port
    except ValueError:
        return False
    if not hostname:
        return False
    if not origin_matches_host(origin, raw_host, (scheme,)):
        return False
    return _host_name_allowed(hostname, _normalized_extra_hosts(extra_hosts))
