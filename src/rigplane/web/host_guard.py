"""Host allowlist and same-Origin rules for the web server (MOR-2880,
MOR-2881, MOR-3108).

Two guards against a web page running in the operator's browser:

- **Host allowlist** (DNS-rebinding defence, mirrors Pro's MOR-2877
  rule): every HTTP route and WebSocket upgrade reads the RAW ``Host``
  request header — never a value derived from the bind address — and
  admits only local names. A missing ``Host`` header is admitted too
  (pinned by ``test_ws_local_hosts_accepted[None]`` and
  ``test_http_local_hosts_accepted[None]``); everything else — an
  empty, malformed or non-local value — gets ``421``.
- **Same-Origin on WebSocket upgrades and state-changing HTTP
  requests** (MOR-2881): a present ``Origin`` must be same-origin with
  the request (scheme, host, port) and its host must pass the same
  allowlist; otherwise ``403`` before the upgrade/handler runs. A
  missing ``Origin`` is admitted (non-browser clients; the Pro
  supervisor's proxy sends none on either leg).
- **Explicit trusted public Origin** (MOR-3108 b11): an ``Origin``
  that is not same-origin may still match an origin the operator
  listed in ``WebConfig.trusted_origins`` — their own public HTTPS
  origin in front of this HTTP/internal upstream (a reverse proxy).
  The comparison is the one normalized place scheme/host case and
  default ports (80/443) canonicalize. The trusted match NEVER
  bypasses the Host allowlist — the raw ``Host`` must be present and
  pass it — nor any guard that runs after this one, and no trust ever
  comes from ``Forwarded``/``X-Forwarded-*`` headers.
"""

from __future__ import annotations

import ipaddress
import json
import re
import urllib.parse
from collections.abc import Collection

__all__ = [
    "MISDIRECTED_BODY",
    "ORIGIN_FORBIDDEN_BODY",
    "host_header_allowed",
    "origin_is_trusted",
    "origin_matches_host",
    "same_origin_allowed",
    "validate_trusted_origins",
]

# Special-use / local-only name suffixes admitted by the allowlist.
# ``.lan`` is deliberately absent: it is conventional, not reserved.
_LOCAL_NAME_SUFFIXES = (
    ".localhost",
    ".local",
    ".home.arpa",
    ".internal",
)

# A Host port tail must be 1-5 ASCII digits (mirrors Pro's final rule).
_PORT_TAIL_RE = re.compile(r"[0-9]{1,5}")

# A single-label host must match this after lowercasing (mirrors Pro).
_SINGLE_LABEL_RE = re.compile(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?")

# A Host host-part may never contain these (defence-in-depth on top of
# the port/bracket parsing below).
_FORBIDDEN_HOST_CHARS = frozenset(" \t/\\?#@[]")

# Default ports canonical in the trusted-origin comparison ONLY
# (MOR-3108): https://host == https://host:443, http://host ==
# http://host:80. The ordinary same-origin comparison never applies it.
_ORIGIN_DEFAULT_PORTS = {"http": 80, "https": 443}

#: Body every misdirected (421) refusal answers with.
MISDIRECTED_BODY = json.dumps(
    {"error": "misdirected request: host not allowed"}
).encode("ascii")

#: Body every refused (403) WebSocket upgrade or state-changing HTTP
#: request answers with.
ORIGIN_FORBIDDEN_BODY = json.dumps({"error": "forbidden: origin not allowed"}).encode(
    "ascii"
)


def _normalized_extra_hosts(extra_hosts: Collection[str]) -> frozenset[str]:
    return frozenset(
        host.strip().lower() for host in extra_hosts if host and host.strip()
    )


def _split_port(raw_host: str) -> str | None:
    """Return the lowercase host part of a raw ``Host`` header value.

    The port is stripped; a port tail must be 1-5 ASCII digits. IPv6
    literals may arrive bare (``::1``) or bracketed with a port
    (``[::1]:8470``) and lose their brackets. Any other value with more
    than one colon is malformed. Returns ``None`` for empty or malformed
    values (bad port, unterminated bracket, forbidden characters).
    """
    value = raw_host.strip()
    if not value:
        return None
    if value.startswith("["):
        end = value.find("]")
        if end < 0:
            return None
        host = value[1:end]
        tail = value[end + 1 :]
        if tail and not (tail.startswith(":") and _PORT_TAIL_RE.fullmatch(tail[1:])):
            return None
        if not host:
            return None
        return host.lower()
    if value.count(":") > 1:
        # Several colons: only a valid bare IPv6 literal is allowed.
        try:
            ipaddress.ip_address(value)
        except ValueError:
            return None
        return value.lower()
    if ":" in value:
        host, _, port = value.rpartition(":")
        if not _PORT_TAIL_RE.fullmatch(port):
            return None
    else:
        host = value
    if not host or _FORBIDDEN_HOST_CHARS.intersection(host):
        return None
    return host.lower()


def _host_name_allowed(name: str, extra_hosts: frozenset[str]) -> bool:
    """Admission rule for an already port-stripped, lowercased host.

    Trailing dots are stripped before every check, so ``localhost.`` is
    ``localhost``.
    """
    host = name.rstrip(".")
    if not host:
        return False
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        pass
    if "." not in host:
        # Single-label name: ``localhost``, bare hostnames.
        return _SINGLE_LABEL_RE.fullmatch(host) is not None
    for suffix in _LOCAL_NAME_SUFFIXES:
        if len(host) > len(suffix) and host.endswith(suffix):
            return True
    return host in extra_hosts


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

    The one same-origin comparison, shared by the WebSocket guard and
    the state-changing HTTP guard, both of which pass the request's
    actual scheme. Comparison is case-insensitive (scheme and host are,
    per RFC 3986).
    """
    if not request_host:
        return False
    lowered = origin.lower()
    return any(lowered == f"{scheme}://{request_host}".lower() for scheme in schemes)


def same_origin_allowed(
    origin: str,
    raw_host: str | None,
    scheme: str,
    extra_hosts: Collection[str] = (),
    trusted_origins: Collection[str] = (),
) -> bool:
    """Same-origin rule for the four WebSocket upgrades (MOR-2880) and
    every state-changing HTTP request (MOR-2881), with the explicit
    trusted-public-Origin fallback (MOR-3108 b11).

    *origin* must parse as an absolute http(s) URL whose serialization
    equals ``scheme://<raw Host header>`` (so scheme, host and port all
    match the request), and its host must pass the same allowlist —
    or, failing that, match an origin in *trusted_origins*: the
    operator's own public HTTPS origin in front of this HTTP/internal
    upstream. The trusted match never bypasses the Host allowlist (the
    raw Host must be present and pass it) nor any guard that runs after
    this one. A malformed request Origin fails closed.
    """
    if _same_origin_strict(origin, raw_host, scheme, extra_hosts):
        return True
    return _trusted_origin_admits(origin, raw_host, extra_hosts, trusted_origins)


def _same_origin_strict(
    origin: str,
    raw_host: str | None,
    scheme: str,
    extra_hosts: Collection[str],
) -> bool:
    """The MOR-2881 comparison itself (the former body of
    :func:`same_origin_allowed`).

    A malformed ``Origin`` — including one whose parse raises
    ``ValueError`` — fails closed.
    """
    try:
        parts = urllib.parse.urlsplit(origin)
    except ValueError:
        return False
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


def _parse_trusted_origin(value: object) -> tuple[str, str, int]:
    """Parse one serialized http(s) origin into its normalized key.

    Returns ``(scheme, lowercase host, effective port)`` where an
    omitted port is the scheme's default (80/443 — the only place that
    canonical equivalence is allowed). Raises ``ValueError`` for
    anything that is not a bare serialized origin: a non-string, null
    or empty value, a wildcard, whitespace, a non-http(s) scheme,
    userinfo, a path (including only a trailing slash), a query, a
    fragment, an empty or malformed host, or a malformed port.
    """
    if not isinstance(value, str) or not value:
        raise ValueError("origin must be a non-empty string")
    if "*" in value:
        raise ValueError("wildcard origins are not allowed")
    if any(ch.isspace() or ord(ch) <= 32 or ord(ch) == 127 for ch in value):
        raise ValueError("whitespace and control characters are not allowed")
    parts = urllib.parse.urlsplit(value)
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise ValueError("scheme must be http or https")
    if parts.username is not None or parts.password is not None:
        raise ValueError("userinfo is not allowed")
    if parts.path or "?" in value or "#" in value:
        raise ValueError("path, query and fragment are not allowed")
    if parts.netloc.endswith(":"):
        raise ValueError("port must not be empty")
    hostname = parts.hostname
    port = parts.port  # ValueError on a malformed or out-of-range port
    if not hostname:
        raise ValueError("host must not be empty")
    if _FORBIDDEN_HOST_CHARS.intersection(hostname) or "%" in hostname:
        raise ValueError("malformed host")
    if ":" in hostname:
        ipaddress.IPv6Address(hostname)
    else:
        labels = hostname.removesuffix(".").split(".")
        if len(hostname) > 253 or any(
            len(label) > 63 or not _SINGLE_LABEL_RE.fullmatch(label) for label in labels
        ):
            raise ValueError("malformed host")
        if len(labels) == 4 and all(label.isdigit() for label in labels):
            ipaddress.IPv4Address(hostname)
    if port is None:
        port = _ORIGIN_DEFAULT_PORTS[scheme]
    if port == 0:
        raise ValueError("port must be between 1 and 65535")
    return (scheme, hostname, port)


def validate_trusted_origins(trusted_origins: Collection[str]) -> None:
    """Reject an invalid trusted-origin configuration before serving.

    Raises ``ValueError`` describing the first invalid entry (MOR-3108:
    invalid configuration fails startup).
    """
    for index, value in enumerate(trusted_origins):
        try:
            _parse_trusted_origin(value)
        except ValueError:
            raise ValueError(
                f"invalid trusted origin entry {index + 1}: "
                "expected a serialized http(s) origin with a valid host and port"
            ) from None


def origin_is_trusted(
    origin: str,
    trusted_origins: Collection[str],
) -> bool:
    """True when a request ``Origin`` matches the trusted allowlist.

    The one normalized comparison: scheme, lowercase host and effective
    port, with 80/443 canonical for their schemes. A malformed request
    Origin fails closed, as does a malformed allowlist entry (unreachable
    past :func:`validate_trusted_origins`, refused anyway).
    """
    if not trusted_origins:
        return False
    try:
        key = _parse_trusted_origin(origin)
    except ValueError:
        return False
    for trusted in trusted_origins:
        try:
            if key == _parse_trusted_origin(trusted):
                return True
        except ValueError:
            continue
    return False


def _trusted_origin_admits(
    origin: str,
    raw_host: str | None,
    extra_hosts: Collection[str],
    trusted_origins: Collection[str],
) -> bool:
    """The MOR-3108 fallback: an explicitly trusted public Origin admits
    a request that is not same-origin — the operator's own HTTPS origin
    in front of this HTTP/internal upstream — but the raw Host must be
    present and pass the same allowlist the Host guard enforces; a
    missing, malformed or untrusted Origin is refused."""
    if not trusted_origins or raw_host is None:
        return False
    if not origin_is_trusted(origin, trusted_origins):
        return False
    return host_header_allowed(raw_host, extra_hosts)
