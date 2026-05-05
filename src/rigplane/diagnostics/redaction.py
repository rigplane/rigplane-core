"""PII redaction utilities for diagnostic bundles.

Module-level regex patterns for safety: pre-compiled, no per-call cost.
Each redactor is a pure function: input string → scrubbed output string.

Used by built-in contributors and exported for Pro extensions.
"""

from __future__ import annotations

import ipaddress
import re

# --- Paths ----------------------------------------------------------

# /Users/<user>/...  (macOS), /home/<user>/...  (Linux), C:\Users\<user>\...  (Windows).
# Negative lookbehind `(?<![/:\w])` skips URL contexts (e.g. ``https://.../Users/...``)
# and avoids matching ``/Users`` when preceded by ``/``, ``:`` or a word character.
_PATH_UNIX = re.compile(r"(?<![/:\w])(/(?:Users|home))/[^/\s\"']+")
_PATH_WIN = re.compile(r"([A-Za-z]:\\Users\\)[^\\\s\"']+", re.IGNORECASE)


def redact_paths(text: str) -> str:
    """Replace home-directory prefixes with ``<USER>``.

    Examples
    --------
    /Users/moroz/foo  →  /Users/<USER>/foo
    /home/sergey/bar  →  /home/<USER>/bar
    C:\\Users\\Bob\\baz → C:\\Users\\<USER>\\baz
    """
    if not text:
        return ""
    text = _PATH_UNIX.sub(r"\1/<USER>", text)
    text = _PATH_WIN.sub(r"\1<USER>", text)
    return text


# --- IP addresses ---------------------------------------------------

# IPv4 octets (0-255). RFC 1918 + loopback are kept (radio LAN address).
_IPV4 = re.compile(
    r"\b(?:(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\.){3}(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)\b"
)
# Permissive IPv6 candidate matcher. We then validate via ``ipaddress`` —
# the regex only finds plausible substrings (``::``-compressed and full forms);
# stdlib determines if it's a real address and whether it's private/loopback.
#
# Boundary handling: trailing ``\b`` does NOT fire after a final ``:`` because
# ``:`` is non-word — ``\b`` requires a word/non-word transition. Use
# ``(?![\w:])`` instead so trailing-``::`` forms (``2001:db8::``,
# ``2607:f8b0:4002::``) match in full without leaking. Without this, alt 1
# greedily matched the leading ``2607:f8b0:4002`` prefix, which is not a
# valid IP and was passed through unchanged.
_IPV6 = re.compile(
    r"\b(?:[A-Fa-f0-9]{1,4}(?::[A-Fa-f0-9]{0,4}){2,7})(?![\w:])"
    r"|"
    r"\b(?:[A-Fa-f0-9]{1,4}:){1,7}:(?:[A-Fa-f0-9]{1,4})?(?![\w:])"
    r"|"
    r"\b::(?:[A-Fa-f0-9]{1,4}(?::[A-Fa-f0-9]{1,4})*)?\b"
)

_RFC1918_PREFIXES = ("10.", "192.168.", "127.")
# 172.16.x – 172.31.x
_RFC1918_172 = re.compile(r"^172\.(1[6-9]|2\d|3[01])\.")

# IPv6 ULA range (fc00::/7 covers fd00::/8 and fc00::/8). Loopback ``::1`` and
# link-local ``fe80::/10`` are detected via ``IPv6Address.is_loopback`` /
# ``is_link_local``. We deliberately do NOT use ``is_private`` because Python's
# stdlib classifies the RFC 3849 documentation range ``2001:db8::/32`` as
# private; for diagnostic redaction those are public-shaped and must redact.
_ULA_V6 = ipaddress.IPv6Network("fc00::/7")


def _is_private_ipv4(ip: str) -> bool:
    if any(ip.startswith(p) for p in _RFC1918_PREFIXES):
        return True
    return bool(_RFC1918_172.match(ip))


def redact_ips(text: str) -> str:
    """Replace public IPv4/IPv6 addresses with ``<IP>``; keep private/LAN.

    RFC 1918 private (10/8, 172.16/12, 192.168/16), loopback (127.x),
    and IPv6 link-local (fe80::/10) / ULA (fd::/8) / loopback (::1) are
    kept — they're the radio's address and the local network, useful
    for triage.
    """
    if not text:
        return ""

    def _v4(match: re.Match[str]) -> str:
        ip = match.group(0)
        return ip if _is_private_ipv4(ip) else "<IP>"

    def _v6(match: re.Match[str]) -> str:
        candidate = match.group(0)
        try:
            addr = ipaddress.ip_address(candidate)
        except ValueError:
            return candidate  # not a valid IP — leave it alone
        if not isinstance(addr, ipaddress.IPv6Address):
            return candidate
        if addr.is_loopback or addr.is_link_local or addr in _ULA_V6:
            return candidate
        return "<IP>"

    text = _IPV4.sub(_v4, text)
    text = _IPV6.sub(_v6, text)
    return text


# --- Credentials ----------------------------------------------------

# password=, pwd=, passwd= (case-insensitive). Match value up to whitespace or end.
_PASSWORD_KV = re.compile(r"\b(passw(?:ord|d)?|pwd)\s*[=:]\s*\S+", re.IGNORECASE)
# Authorization: Bearer <token>
_BEARER = re.compile(r"\b(Authorization\s*:\s*Bearer)\s+\S+", re.IGNORECASE)
# AWS keys
_AWS_KEY = re.compile(
    r"\b(aws_(?:access_key_id|secret_access_key))\s*[=:]\s*\S+",
    re.IGNORECASE,
)
# Raw activation codes (per license-authority-v0)
_ACTIVATION_CODE = re.compile(r"\bcode_[A-Z0-9]{26}\b")
# PEM private key blocks
_PRIVATE_KEY_BLOCK = re.compile(
    r"-----BEGIN (?:RSA |EC |OPENSSH |PGP |DSA |ENCRYPTED )?PRIVATE KEY-----"
    r"[\s\S]*?"
    r"-----END (?:RSA |EC |OPENSSH |PGP |DSA |ENCRYPTED )?PRIVATE KEY-----",
)


def redact_credentials(text: str) -> str:
    """Redact passwords, AWS keys, Bearer tokens, activation codes, PEM keys."""
    if not text:
        return ""
    text = _PASSWORD_KV.sub(r"\1=<REDACTED>", text)
    text = _BEARER.sub(r"\1 <REDACTED>", text)
    text = _AWS_KEY.sub(r"\1=<REDACTED>", text)
    text = _ACTIVATION_CODE.sub("<ACTIVATION_CODE>", text)
    text = _PRIVATE_KEY_BLOCK.sub("<PRIVATE KEY REDACTED>", text)
    return text


# --- Tokens (high-entropy near trigger keywords) --------------------

# token=..., api_key=..., apikey=..., or "Bearer XYZ" — high-entropy [A-Za-z0-9_-]{32,}.
_TOKEN_KV = re.compile(
    r"\b(token|api[_-]?key|apikey)\s*[=:]\s*[A-Za-z0-9_\-]{32,}",
    re.IGNORECASE,
)
_BEARER_TOKEN = re.compile(r"\bBearer\s+[A-Za-z0-9_\-]{32,}")


def redact_tokens(text: str) -> str:
    """Redact high-entropy tokens near token=/api_key=/Bearer keywords."""
    if not text:
        return ""

    def _kv(match: re.Match[str]) -> str:
        return f"{match.group(1)}=<REDACTED>"

    text = _TOKEN_KV.sub(_kv, text)
    text = _BEARER_TOKEN.sub("Bearer <REDACTED>", text)
    return text


# --- Hostnames ------------------------------------------------------

# DNS-style names: 1+ labels of ASCII alnum/hyphen joined by dots, ending in
# a 2–63-letter "TLD". ``localhost`` is preserved (not PII, useful for triage).
#
# WARNING: this matcher is intentionally narrow but still over-eager — it
# matches ``radio.json``, ``audio.log``, ``script.py``, etc. Apply ONLY at
# call sites where the value is known to be a hostname (e.g. the radio's
# ``host`` connection field). Do NOT add to a generic ``_redact`` chain.
_HOSTNAME = re.compile(
    r"\b(?!localhost\b)"
    r"(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+"
    r"[a-zA-Z]{2,63}\b"
)


def redact_hostnames(text: str) -> str:
    """Replace DNS-shape hostnames with ``<HOSTNAME>``.

    Preserves ``localhost`` and bare hostnames without a TLD (e.g. ``IC-7610``).

    .. warning::

       This redactor will scrub anything matching ``label.tld`` shape, including
       filenames like ``radio.json`` and ``script.py``. Apply only to fields
       known to carry hostnames. See the ``_redact_host`` helper in
       :mod:`icom_lan.diagnostics.contributors.radio` for the safe call site.
    """
    if not text:
        return ""
    return _HOSTNAME.sub("<HOSTNAME>", text)


# --- Catalogue ------------------------------------------------------

REDACTORS: tuple[str, ...] = ("paths", "ips", "credentials", "tokens")
"""Names of the scrubbers run on a bundle, recorded in
``manifest.redactions_applied``.

``hostnames`` is intentionally NOT in this catalogue: :func:`redact_hostnames`
is opt-in per call-site (currently only the radio host field) because the
``label.tld`` shape over-matches generic identifiers like ``radio.json``."""
