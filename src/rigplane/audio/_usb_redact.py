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
    by_id = {row.get("instance_id", "").casefold(): row for row in rows}
    kept: set[str] = set()

    def keep_chain(instance_id: str, *, owner_only: bool) -> None:
        seen: set[str] = set()
        while instance_id and instance_id.casefold() not in seen:
            folded = instance_id.casefold()
            seen.add(folded)
            row = by_id.get(folded)
            if row is not None and row.get("class_name", "").casefold() == "net":
                break
            kept.add(folded)
            if row is None or (owner_only and not folded.startswith("swd\\")):
                break
            instance_id = row.get("parent_id", "")

    for row in rows:
        folded = row.get("instance_id", "").casefold()
        if row.get("class_name", "").casefold() == "net":
            continue
        if folded.startswith("usb\\"):
            keep_chain(row.get("instance_id", ""), owner_only=False)
        elif folded.startswith("swd\\mmdevapi\\"):
            kept.add(folded)
            keep_chain(row.get("parent_id", ""), owner_only=True)

    def public_id(instance_id: str) -> str:
        folded = instance_id.casefold()
        if folded not in kept:
            return ""
        if folded.startswith(("usb\\", "swd\\mmdevapi\\")):
            return by_id.get(folded, {"instance_id": instance_id})["instance_id"]
        return "opaque:" + _token(key, folded)

    result = []
    for row in rows:
        if row.get("instance_id", "").casefold() in kept:
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

    replacements: dict[str, str] = {}
    for value in sorted(values):
        canonical = value.replace(" ", "_").casefold()
        token = _token(key, canonical)
        replacements[canonical] = token
    spellings = {value.replace(" ", "_") for value in values} | replacements.keys()

    def alternatives_for(spellings: set[str]) -> str:
        return "|".join(
            "[ _]".join(re.escape(part) for part in value.split("_"))
            for value in sorted(spellings, key=lambda value: (-len(value), value))
        )

    alternatives = alternatives_for(spellings)
    id_pattern = (
        re.compile(r"(?<![^\\&#:])(?:" + alternatives + r")(?![^\\&#:])", re.IGNORECASE)
        if replacements
        else None
    )
    long_alternatives = alternatives_for(
        {value for value in spellings if len(value) >= 4}
    )
    name_pattern = (
        re.compile(
            r"(?<![^\W_])(?:" + long_alternatives + r")(?![^\W_])",
            re.IGNORECASE,
        )
        if long_alternatives
        else None
    )
    hwid_pattern = (
        re.compile(
            r"(?<!\S)(SER=)(" + alternatives + r")(?= +[A-Za-z_][A-Za-z0-9_]*=|$)",
            re.IGNORECASE,
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

    def replace_name(value: Any) -> Any:
        if not isinstance(value, str):
            return value
        whole = replace_field(value)
        return whole if whole != value else replace_segments(value, name_pattern)

    result = copy.deepcopy(bundle)
    for session in result["sessions"]:
        raw = session.get("raw", {})
        for port in raw.get("serial_ports", []):
            for name in (
                "name",
                "description",
                "serial_number",
                "manufacturer",
                "product",
                "interface",
                "location",
            ):
                if name in port:
                    port[name] = replace_name(port[name])
            if isinstance(port.get("device"), str):
                port["device"] = replace_segments(port["device"], id_pattern)
            if isinstance(port.get("hwid"), str) and hwid_pattern is not None:
                port["hwid"] = hwid_pattern.sub(
                    lambda match: match[1] + replace_field(match[2]), port["hwid"]
                )
        for collection in ("devices", "hostapis"):
            for row in raw.get("portaudio", {}).get(collection, []):
                if "name" in row:
                    row["name"] = replace_name(row["name"])
        windows = raw.get("windows")
        if windows is not None:
            windows["devnodes"] = _prune_windows_nodes(windows.get("devnodes", []), key)
            for row in windows["devnodes"]:
                for name in ("instance_id", "parent_id"):
                    row[name] = replace_segments(row.get(name, ""), id_pattern)
                for name in ("hardware_ids", "compatible_ids"):
                    row[name] = [
                        replace_segments(value, id_pattern)
                        for value in row.get(name, [])
                    ]
                for name in ("friendly_name", "device_desc"):
                    row[name] = replace_name(row.get(name, ""))
    return result
