"""Bundle-wide USB serial redaction and Windows topology pruning."""

from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import re
import secrets
from typing import Any

_USB_SERIAL_PREFIX = r"USB\\VID_[0-9A-F]{4}&PID_[0-9A-F]{4}\\"
_USB_SERIAL_ID = re.compile(_USB_SERIAL_PREFIX + r"([^\\&]+)", re.IGNORECASE | re.ASCII)


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
    devices: set[str] = set()
    device_serials: dict[str, str] = {}
    uid_locations: set[int | str] = set()

    def collect_devices(value: Any) -> None:
        if isinstance(value, list):
            for child in value:
                collect_devices(child)
        elif isinstance(value, dict):
            for child in value.values():
                collect_devices(child)
        elif isinstance(value, str):
            if value.startswith("/dev/"):
                devices.add(value)
            elif value.startswith(("cu.", "tty.", "serial/by-id/")):
                devices.add("/dev/" + value)
            devices.update(
                path.rstrip(".") for path in re.findall(r"/dev/[^\s'\"(),;:]+", value)
            )

    collect_devices(bundle)
    for session in bundle["sessions"]:
        raw = session.get("raw", {})
        for port in raw.get("serial_ports", []):
            if serial := port.get("serial_number"):
                values.add(serial)
                if device := port.get("device"):
                    device_serials[device] = serial
        for row in raw.get("windows", {}).get("devnodes", []):
            for field in ("instance_id", "parent_id"):
                reference = row.get(field, "")
                if match := _USB_SERIAL_ID.fullmatch(reference):
                    values.add(match[1])
        for part in ("macos", "linux"):
            for row in raw.get(part, {}).get("usb_devices", []):
                if serial := row.get("serial_number"):
                    values.add(serial)
                if isinstance(location := row.get("location_id"), int):
                    uid_locations.add(location)
                if isinstance(devpath := row.get("devpath"), str):
                    uid_locations.add(devpath)
        usb = {
            row.get("registry_id"): row.get("serial_number")
            for row in raw.get("macos", {}).get("usb_devices", [])
        }
        for row in raw.get("macos", {}).get("serial_devices", []):
            if (device := row.get("device")) and (
                serial := usb.get(row.get("usb_registry_id"))
            ):
                device_serials[device] = serial

    known_serials = {_ascii_lower(value.replace(" ", "_")) for value in values}
    replacements = {canonical: _token(key, canonical) for canonical in known_serials}

    def replace_field(value: Any) -> Any:
        if isinstance(value, str):
            return replacements.get(_ascii_lower(value.replace(" ", "_")), value)
        return value

    def replace_id(value: str) -> str:
        match = _USB_SERIAL_ID.fullmatch(value)
        if match is None:
            return value
        return value[: match.start(1)] + replace_field(match[1])

    def replace_device(value: str, serial: str | None = None) -> str:
        mac = re.fullmatch(r"(/dev/(?:cu|tty)\.(?:usbserial-|usbmodem))(.*)", value)
        for candidate in sorted({serial} if serial else values, key=len, reverse=True):
            for spelling in {candidate, candidate.replace(" ", "_")}:
                if mac:
                    # A BSD suffix hides a serial only when it equals the serial
                    # exactly, plus at most one trailing interface digit
                    # (macOS "usbserial-<serial><n>"). Anything else, e.g. the
                    # location-derived "usbmodem14201", stays as recorded.
                    exact = re.fullmatch(
                        re.escape(spelling) + r"\d?", mac[2], re.IGNORECASE | re.ASCII
                    )
                    if exact:
                        return (
                            mac[1] + replace_field(candidate) + mac[2][len(spelling) :]
                        )
                if value.startswith("/dev/serial/by-id/"):
                    pattern = re.compile(re.escape(spelling), re.IGNORECASE | re.ASCII)
                    match = re.fullmatch(
                        r"(.+_)" + pattern.pattern + r"(-if\d+.*|$)",
                        value,
                        re.IGNORECASE | re.ASCII,
                    )
                    if match:
                        return match[1] + replace_field(candidate) + match[2]
        return value

    aliases: dict[str, str] = {}
    for device in devices:
        redacted = replace_device(device, device_serials.get(device))
        aliases[device] = redacted
        aliases[device.removeprefix("/dev/")] = redacted.removeprefix("/dev/")
    diagnostic_aliases = {
        source: target for source, target in aliases.items() if source != target
    }
    diagnostic_boundary = r"(?![\w/\\-]|\.[\w./\\-])"
    diagnostic_pattern = (
        re.compile(
            r"(?<![\w./\\-])(?:"
            + "|".join(
                re.escape(source)
                for source in sorted(diagnostic_aliases, key=len, reverse=True)
            )
            + ")"
            + diagnostic_boundary
        )
        if diagnostic_aliases
        else None
    )
    diagnostic_serials = {
        spelling for serial in values for spelling in (serial, serial.replace(" ", "_"))
    }
    diagnostic_windows_pattern = (
        re.compile(
            r"(?<![\w./\\-])"
            + _USB_SERIAL_PREFIX
            + "(?:"
            + "|".join(
                re.escape(serial)
                for serial in sorted(diagnostic_serials, key=len, reverse=True)
            )
            + ")"
            + r"(?!&)"
            + diagnostic_boundary,
            re.IGNORECASE | re.ASCII,
        )
        if diagnostic_serials
        else None
    )

    def redact_topology(value: Any, part: str) -> None:
        if isinstance(value, list):
            for child in value:
                redact_topology(child, part)
        elif isinstance(value, dict):
            for field, child in value.items():
                if field == "serial_number":
                    value[field] = replace_field(child)
                elif field == "device":
                    value[field] = aliases.get(child, child)
                elif field == "reason" and isinstance(child, str):
                    if diagnostic_pattern is not None:
                        child = diagnostic_pattern.sub(
                            lambda match: diagnostic_aliases[match[0]], child
                        )
                    if diagnostic_windows_pattern is not None:
                        child = diagnostic_windows_pattern.sub(
                            lambda match: replace_id(match[0]), child
                        )
                    value[field] = child
                elif part == "macos" and isinstance(child, str):
                    if field in {"registry_path", "parent_path"}:
                        segments = []
                        for segment in child.split("/"):
                            name, sep, location = segment.partition("@")
                            segments.append(replace_field(name) + sep + location)
                        value[field] = "/".join(segments)
                    elif (
                        field == "uid"
                        and child.startswith("AppleUSBAudioEngine:")
                        and child.count(":") >= 2
                    ):
                        prefix, identity, streams = child.rsplit(":", 2)
                        try:
                            location = int(identity, 16)
                        except ValueError:
                            location = None
                        if (
                            identity not in uid_locations
                            and (location is None or location not in uid_locations)
                        ) and _ascii_lower(identity.replace(" ", "_")) in known_serials:
                            value[field] = ":".join(
                                (prefix, replace_field(identity), streams)
                            )
                else:
                    redact_topology(child, part)

    result = copy.deepcopy(bundle)
    for session in result["sessions"]:
        raw = session.get("raw", {})
        redact_topology(session.get("unavailable", []), "")
        for radio in session.get("radios", []):
            if "serial_port" in radio:
                device = radio["serial_port"]
                radio["serial_port"] = aliases.get(device, device)
        for part in ("macos", "linux"):
            redact_topology(raw.get(part), part)
        for port in raw.get("serial_ports", []):
            if "serial_number" in port:
                port["serial_number"] = replace_field(port["serial_number"])
            for field in ("device", "name"):
                if field in port:
                    port[field] = aliases.get(port[field], port[field])
        windows = raw.get("windows")
        if windows is not None:
            windows["devnodes"] = _prune_windows_nodes(windows.get("devnodes", []), key)
            for row in windows["devnodes"]:
                for name in ("instance_id", "parent_id"):
                    row[name] = replace_id(row.get(name, ""))
    return result
