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
            return by_id.get(folded, {"instance_id": instance_id})["instance_id"]
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

    replacements: dict[str, str] = {}
    for value in sorted(values):
        canonical = value.replace(" ", "_").casefold()
        token = _token(key, canonical)
        replacements[canonical] = token
    spellings = {value.replace(" ", "_") for value in values} | replacements.keys()
    alternatives = "|".join(
        "[ _]".join(re.escape(part) for part in value.split("_"))
        for value in sorted(spellings, key=lambda value: (-len(value), value))
    )
    id_pattern = (
        re.compile(r"(?<![^\\&#:])(?:" + alternatives + r")(?![^\\&#:])", re.IGNORECASE)
        if replacements
        else None
    )
    name_pattern = (
        re.compile(
            r"(?<![A-Za-z0-9])(?:" + alternatives + r")(?![A-Za-z0-9])", re.IGNORECASE
        )
        if replacements
        else None
    )

    def replace_segments(value: str, pattern: re.Pattern[str] | None) -> str:
        if pattern is None:
            return value
        return pattern.sub(lambda match: replace_field(match[0]), value)

    def replace_field(value: Any) -> Any:
        if isinstance(value, str):
            return replacements.get(value.replace(" ", "_").casefold(), value)
        return value

    result = copy.deepcopy(bundle)
    for session in result["sessions"]:
        raw = session.get("raw", {})
        for port in raw.get("serial_ports", []):
            for name, value in port.items():
                if name == "hwid" and isinstance(value, str):
                    port[name] = re.sub(
                        r"(?<!\S)(SER=)([^ ]+)",
                        lambda match: match[1] + replace_field(match[2]),
                        value,
                        flags=re.IGNORECASE,
                    )
                else:
                    port[name] = replace_field(value)
        windows = raw.get("windows")
        if windows is not None:
            windows["devnodes"] = _prune_windows_nodes(windows["devnodes"], key)
            for row in windows["devnodes"]:
                for name in ("instance_id", "parent_id"):
                    row[name] = replace_segments(row[name], id_pattern)
                for name in ("hardware_ids", "compatible_ids"):
                    row[name] = [
                        replace_segments(value, id_pattern) for value in row[name]
                    ]
                for name in ("friendly_name", "device_desc"):
                    row[name] = replace_segments(row[name], name_pattern)
    return result
