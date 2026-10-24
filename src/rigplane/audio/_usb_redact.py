"""Bundle-wide USB serial redaction and Windows topology pruning."""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import re
import secrets
from typing import Any

_USB_SERIAL_ID = re.compile(
    r"USB\\VID_[0-9A-F]{4}&PID_[0-9A-F]{4}\\([^\\&]+)", re.IGNORECASE | re.ASCII
)


def _ascii_lower(value: str) -> str:
    return "".join(
        chr(ord(char) + 32) if "A" <= char <= "Z" else char for char in value
    )


def _token(key: bytes, value: str) -> str:
    digest = hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()
    return "R" + base64.b32encode(digest).decode("ascii")[:10]


def _prune_windows_nodes(
    rows: list[dict[str, Any]], key: bytes
) -> list[dict[str, Any]]:
    by_id = {_ascii_lower(row.get("instance_id", "")): row for row in rows}
    kept: set[str] = set()

    def keep_chain(instance_id: str, *, owner_only: bool) -> None:
        seen: set[str] = set()
        while instance_id and _ascii_lower(instance_id) not in seen:
            folded = _ascii_lower(instance_id)
            seen.add(folded)
            row = by_id.get(folded)
            if row is not None and _ascii_lower(row.get("class_name", "")) == "net":
                break
            kept.add(folded)
            if row is None or (owner_only and not folded.startswith("swd\\")):
                break
            instance_id = row.get("parent_id", "")

    for row in rows:
        folded = _ascii_lower(row.get("instance_id", ""))
        if _ascii_lower(row.get("class_name", "")) == "net":
            continue
        if folded.startswith("usb\\"):
            keep_chain(row.get("instance_id", ""), owner_only=False)
        elif folded.startswith("swd\\mmdevapi\\"):
            kept.add(folded)
            keep_chain(row.get("parent_id", ""), owner_only=True)

    def public_id(instance_id: str) -> str:
        folded = _ascii_lower(instance_id)
        if folded not in kept:
            return ""
        if folded.startswith("usb\\"):
            return instance_id
        if folded.startswith("swd\\mmdevapi\\"):
            return by_id.get(folded, {"instance_id": instance_id})["instance_id"]
        return "opaque:" + _token(key, folded)

    result = []
    for row in rows:
        if _ascii_lower(row.get("instance_id", "")) in kept:
            result.append(
                {
                    **row,
                    "instance_id": public_id(row.get("instance_id", "")),
                    "parent_id": public_id(row.get("parent_id", "")),
                }
            )
    return result


def redact_bundle(
    bundle: dict[str, Any], *, key: bytes | None = None
) -> dict[str, Any]:
    key = secrets.token_bytes(32) if key is None else key
    values: set[str] = set()
    for session in bundle["sessions"]:
        raw = session.get("raw", {})
        for port in raw.get("serial_ports", []):
            if serial := port.get("serial_number"):
                values.add(serial)
        for row in raw.get("windows", {}).get("devnodes", []):
            match = _USB_SERIAL_ID.fullmatch(row.get("instance_id", ""))
            if match:
                values.add(match[1])

    replacements = {
        canonical: _token(key, canonical)
        for canonical in {_ascii_lower(value.replace(" ", "_")) for value in values}
    }

    def replace_field(value: Any) -> Any:
        if isinstance(value, str):
            return replacements.get(_ascii_lower(value.replace(" ", "_")), value)
        return value

    def replace_id(value: str) -> str:
        match = _USB_SERIAL_ID.fullmatch(value)
        if match is None:
            return value
        return value[: match.start(1)] + replace_field(match[1])

    result = copy.deepcopy(bundle)
    for session in result["sessions"]:
        raw = session.get("raw", {})
        for port in raw.get("serial_ports", []):
            if "serial_number" in port:
                port["serial_number"] = replace_field(port["serial_number"])
        windows = raw.get("windows")
        if windows is not None:
            windows["devnodes"] = _prune_windows_nodes(windows.get("devnodes", []), key)
            for row in windows["devnodes"]:
                for name in ("instance_id", "parent_id"):
                    row[name] = replace_id(row.get(name, ""))
    return result
