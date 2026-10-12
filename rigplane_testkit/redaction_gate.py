"""Redaction gate: flag the identity leaks it knows how to detect.

Scans the string VALUES of JSON/JSONL files — e.g. the output of
``python -m rigplane_testkit.icom.fixtures extract`` — before they are
committed.  A hex string value (even length, >= 8 chars) is decoded and
searched for each ``--ipv4`` address (4 bytes, both byte orders), each
``--mac`` address (6 bytes) and each deny string (UTF-8 bytes);
printable-ASCII runs of >= 6 bytes are reported too, EXCEPT runs fully
inside a COMPLETE CI-V scope wave frame (``FE FE xx xx 27 00 .. FD`` —
a prefix with no ``FD`` terminator is not a frame and exempts nothing),
whose amplitude bytes are legitimately printable.  A non-hex string
value is searched for the deny strings and ``--ipv4`` addresses as
text, and for dotted quads other than 127.0.0.1, 0.0.0.0 and the
RFC 5737 documentation ranges (192.0.2.0/24, 198.51.100.0/24,
203.0.113.0/24).

Not covered — check these by hand before committing: IPv4 addresses
encoded as BCD inside CI-V payloads (e.g. the IC-7610 ``1A 05`` network
items), JSON object keys, JSON numbers, and MAC addresses written as
text.

A :class:`Finding` never carries the matched value — only path, line, kind
and an index: the matching ``ipv4`` / ``mac`` / ``deny`` list entry, the
run's byte offset for ``ascii-run``, the quad's ordinal within the string
for ``dotted-quad``.

CLI::

    python -m rigplane_testkit.redaction_gate [--deny-file F] \
        [--ipv4 A.B.C.D]... [--mac aa:bb:cc:dd:ee:ff]... PATH...

prints ``path:line kind#index`` per finding (paths relative to the
current directory when inside it) and exits 1 on any finding, 0 when
clean.  The deny file holds one secret per line (user, password, host
name, computer name); its content is never printed or logged.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

_HEX_RE = re.compile(r"[0-9a-fA-F]+")
_ASCII_RUN_RE = re.compile(rb"[\x20-\x7e]{6,}")
_SCOPE_WAVE_RE = re.compile(rb"\xfe\xfe..\x27\x00", re.DOTALL)
_CIV_TERM = 0xFD
_DOTTED_QUAD_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_ALLOWED_QUADS = frozenset({"127.0.0.1", "0.0.0.0"})
_DOC_PREFIXES = ("192.0.2.", "198.51.100.", "203.0.113.")


@dataclass(frozen=True, slots=True)
class Finding:
    """One leak sighting; the matched value is never stored here."""

    path: str
    line: int
    kind: str
    index: int


def _parse_ipv4(text: str) -> bytes:
    parts = text.split(".")
    if len(parts) != 4:
        raise ValueError(f"not an IPv4 address: {text!r}")
    return bytes(int(part) for part in parts)


def _parse_mac(text: str) -> bytes:
    parts = text.split(":")
    if len(parts) != 6:
        raise ValueError(f"not a MAC address: {text!r}")
    return bytes(int(part, 16) for part in parts)


def _is_hex(value: str) -> bool:
    return (
        len(value) >= 8 and len(value) % 2 == 0 and _HEX_RE.fullmatch(value) is not None
    )


def _strings(node: object) -> Iterator[str]:
    """Yield every string VALUE in a parsed JSON document."""
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from _strings(value)
    elif isinstance(node, list):
        for item in node:
            yield from _strings(item)


def _line_of(text: str, value: str) -> int:
    """1-based line of the value's JSON encoding in ``text`` (0 if absent)."""
    index = text.find(json.dumps(value))
    return text.count("\n", 0, index) + 1 if index >= 0 else 0


def _iter_strings(path: str) -> Iterator[tuple[int, str]]:
    """Yield ``(line, string_value)`` for a JSON or JSONL file."""
    text = Path(path).read_text(encoding="utf-8")
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        for lineno, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped:
                for value in _strings(json.loads(stripped)):
                    yield lineno, value
        return
    for value in _strings(doc):
        yield _line_of(text, value), value


def _scope_wave_spans(blob: bytes) -> list[tuple[int, int]]:
    """Byte spans of COMPLETE scope wave frames (prefix .. FD terminator).

    A ``FE FE .. 27 00`` prefix with no ``FD`` terminator after it is not
    a frame and yields no span, so the bytes after it stay scanned.
    """
    spans = []
    for match in _SCOPE_WAVE_RE.finditer(blob):
        end = blob.find(bytes([_CIV_TERM]), match.end())
        if end != -1:
            spans.append((match.start(), end + 1))
    return spans


def _scan_bytes(
    path: str,
    line: int,
    blob: bytes,
    ipv4: list[bytes],
    mac: list[bytes],
    deny: list[bytes],
) -> list[Finding]:
    findings: list[Finding] = []
    for index, needle in enumerate(ipv4):
        if needle in blob or needle[::-1] in blob:
            findings.append(Finding(path, line, "ipv4-bytes", index))
    for index, needle in enumerate(mac):
        if needle in blob:
            findings.append(Finding(path, line, "mac-bytes", index))
    for index, needle in enumerate(deny):
        if needle in blob:
            findings.append(Finding(path, line, "deny-bytes", index))
    exempt = _scope_wave_spans(blob)
    for match in _ASCII_RUN_RE.finditer(blob):
        if any(start <= match.start() and match.end() <= end for start, end in exempt):
            continue
        findings.append(Finding(path, line, "ascii-run", match.start()))
    return findings


def _scan_text(
    path: str,
    line: int,
    value: str,
    ipv4: tuple[str, ...],
    deny: tuple[str, ...],
) -> list[Finding]:
    findings: list[Finding] = []
    for index, address in enumerate(ipv4):
        if address in value:
            findings.append(Finding(path, line, "ipv4-text", index))
    for index, secret in enumerate(deny):
        if secret in value:
            findings.append(Finding(path, line, "deny-text", index))
    for ordinal, match in enumerate(_DOTTED_QUAD_RE.finditer(value)):
        quad = match.group(0)
        if quad in _ALLOWED_QUADS or quad.startswith(_DOC_PREFIXES):
            continue
        findings.append(Finding(path, line, "dotted-quad", ordinal))
    return findings


def scan(
    paths: list[str] | tuple[str, ...],
    *,
    ipv4: tuple[str, ...] = (),
    mac: tuple[str, ...] = (),
    deny: tuple[str, ...] = (),
) -> list[Finding]:
    """Scan JSON/JSONL string values for the deny lists and ASCII runs.

    Never stores matched values; see the module docstring for exactly what
    is and is not covered.
    """
    ipv4_bytes = [_parse_ipv4(address) for address in ipv4]
    mac_bytes = [_parse_mac(address) for address in mac]
    deny_bytes = [secret.encode("utf-8") for secret in deny]
    findings: list[Finding] = []
    for path in paths:
        for line, value in _iter_strings(path):
            if _is_hex(value):
                findings.extend(
                    _scan_bytes(
                        path,
                        line,
                        bytes.fromhex(value),
                        ipv4_bytes,
                        mac_bytes,
                        deny_bytes,
                    )
                )
            else:
                findings.extend(_scan_text(path, line, value, ipv4, deny))
    return findings


def _display(path: str) -> str:
    """Scanned path as printed: relative to the CWD when inside it."""
    absolute = os.path.abspath(path)
    prefix = os.getcwd().rstrip(os.sep) + os.sep
    return os.path.relpath(absolute) if absolute.startswith(prefix) else path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m rigplane_testkit.redaction_gate")
    parser.add_argument("--deny-file", help="one secret per line (never printed)")
    parser.add_argument(
        "--ipv4", action="append", default=[], help="IPv4 address to deny (repeatable)"
    )
    parser.add_argument(
        "--mac", action="append", default=[], help="MAC address to deny (repeatable)"
    )
    parser.add_argument("paths", nargs="+", help="JSON/JSONL files to scan")
    args = parser.parse_args(argv)
    deny: list[str] = []
    if args.deny_file:
        deny.extend(
            line.strip()
            for line in Path(args.deny_file).read_text(encoding="utf-8").splitlines()
            if line.strip()
        )
    findings = scan(
        args.paths, ipv4=tuple(args.ipv4), mac=tuple(args.mac), deny=tuple(deny)
    )
    for finding in findings:
        print(f"{_display(finding.path)}:{finding.line} {finding.kind}#{finding.index}")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
