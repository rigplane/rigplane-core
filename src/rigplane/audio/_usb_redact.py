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
    r"USB\\VID_[0-9A-F]{4}&PID_[0-9A-F]{4}\\([^\\&]+)", re.IGNORECASE
)


def _token(key: bytes, value: str) -> str:
    digest = hmac.new(key, value.encode("utf-8"), hashlib.sha256).digest()
    return "R" + base64.b32encode(digest).decode("ascii")[:10]


def _prune_windows_nodes(
    rows: list[dict[str, Any]], key: bytes
) -> list[dict[str, Any]]:
    by_id = {row["instance_id"].casefold(): row for row in rows}
    kept: set[str] = set()

    def keep_chain(instance_id: str, *, owner_only: bool) -> None:
        seen: set[str] = set()
        while instance_id and instance_id.casefold() not in seen:
            folded = instance_id.casefold()
            seen.add(folded)
            row = by_id.get(folded)
            if row is not None and row["class_name"].casefold() == "net":
                break
            kept.add(folded)
            if row is None or (owner_only and not folded.startswith("swd\\")):
                break
            instance_id = row["parent_id"]

    for row in rows:
        folded = row["instance_id"].casefold()
        if row["class_name"].casefold() == "net":
            continue
        if folded.startswith("usb\\"):
            keep_chain(row["instance_id"], owner_only=False)
        elif folded.startswith("swd\\mmdevapi\\"):
            kept.add(folded)
            keep_chain(row["parent_id"], owner_only=True)

    def public_id(instance_id: str) -> str:
        folded = instance_id.casefold()
        if folded not in kept:
            return ""
        if folded.startswith(("usb\\", "swd\\mmdevapi\\")):
            return instance_id
        return "opaque:" + _token(key, folded)

    result = []
    for row in rows:
        if row["instance_id"].casefold() in kept:
            result.append(
                {
                    **row,
                    "instance_id": public_id(row["instance_id"]),
                    "parent_id": public_id(row["parent_id"]),
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
            match = _USB_SERIAL_ID.fullmatch(row["instance_id"])
            if match:
                values.add(match[1])

    representatives: dict[str, str] = {}
    replacements: dict[str, str] = {}
    for value in sorted(values):
        representative = representatives.setdefault(value.replace(" ", "_"), value)
        token = _token(key, representative)
        for twin in (value, value.replace(" ", "_"), value.replace("_", " ")):
            replacements[twin] = token
    pattern = (
        re.compile(
            r"(?<![A-Za-z0-9])(?:"
            + "|".join(
                re.escape(value)
                for value in sorted(
                    replacements, key=lambda value: (-len(value), value)
                )
            )
            + r")(?![A-Za-z0-9])"
        )
        if replacements
        else None
    )

    def replace(value: Any) -> Any:
        if isinstance(value, str) and pattern is not None:
            return pattern.sub(lambda match: replacements[match[0]], value)
        if isinstance(value, dict):
            return {name: replace(item) for name, item in value.items()}
        if isinstance(value, list):
            return [replace(item) for item in value]
        return value

    result = copy.deepcopy(bundle)
    for session in result["sessions"]:
        windows = session.get("raw", {}).get("windows")
        if windows is not None:
            windows["devnodes"] = _prune_windows_nodes(windows["devnodes"], key)
    return replace(result)
